import copy
import json
import pytest
from decompcond.agent import DeepSeekPlanner
from decompcond.feedback import summarize, build_feedback
from decompcond.io import file_digest, read_json, TechnicalFailure
from decompcond.paths import compile_path
from decompcond.planning_evidence import paired_returns, schedule_relation


def record(name, scores, reverse=False):
    groups = {"C1": {}, "C2": {}}
    p = compile_path(
        dict(
            id=name,
            segments=[
                dict(start=0, end=50, active=["C2" if reverse else "C1"]),
                dict(start=50, end=100, active=["C1" if reverse else "C2"]),
            ],
            parents=[],
            evidence=["condition_library"],
            observation="test",
            change="test",
            hypothesis="test",
        ),
        groups,
        100,
        10,
    )
    attempts = [
        dict(
            status="evaluated" if x else "chemical_invalid",
            structure_pass=bool(x),
            effective_recovery=x,
            delta_pic50=-1 if x else None,
            condition_metrics={},
            pb_checks={},
            exact_recovery=x == 1,
        )
        for x in scores
    ]
    return dict(
        path=p,
        summary=summarize(
            p, attempts, {"groups": groups}, seeds=list(range(len(scores)))
        ),
        stage="round_001",
    )


def test_pairing_uses_seed_keys_and_keeps_failures():
    a, b = record("a", [1, 0]), record("b", [0, 1])
    b["summary"]["seed_scores"] = {"1": 1, "0": 0}
    result = paired_returns(a, b)
    assert result["n"] == 2 and result["mean_delta_J"] == 0
    assert result["standard_error"] == pytest.approx(1)
    assert result["wins"] == result["losses"] == 1
    b["summary"]["seed_scores"] = {"2": 1, "3": 0}
    assert paired_returns(a, b)["status"] == "unavailable_seed_pairing"


def test_equal_exposure_does_not_mean_identical_schedule():
    a, b = record("a", [1, 0]), record("b", [0, 1], reverse=True)
    result = schedule_relation(a, b, ["C1", "C2"])
    assert result["same_per_condition_exposure"]
    assert result["normalized_activity_distance"] == 1
    assert result["changed_windows"][0] == dict(
        start=0, end=50, added=["C1"], removed=["C2"]
    )


def test_history_evidence_available_but_confirmation_excluded():
    base, a, b = (
        record("baseline", [1, 0]),
        record("a", [1, 1]),
        record("b", [0, 1], True),
    )
    history = dict(
        baseline=base,
        rounds=[dict(round=1, records=[a, b])],
        confirmation={"SECRET_HOLDOUT": "do not expose"},
    )
    snapshot = copy.deepcopy(history)
    f = build_feedback(history, {"groups": {"C1": {}, "C2": {}}}, 2, enhanced=True)
    assert len(f["previous_strategies"]) == 2
    assert "exposure" in f["evidence"]["metrics:baseline"]
    assert "condition_metrics" in f["evidence"]["metrics:a"]
    assert f["planning_comparisons"]["a"]["baseline"]["paired"]["n"] == 2
    assert "SECRET_HOLDOUT" not in json.dumps(f)
    assert "expanded" not in f["previous_strategies"][0]["path"]
    assert "seed_scores" not in f["previous_strategies"][0]["metrics"]
    assert f["previous_strategies"][0]["path"]["segments"] == a["path"]["segments"]
    assert history == snapshot


def test_guidance_hash_and_actual_input(config, tmp_path):
    guide = tmp_path / "SKILL.md"
    guide.write_text("Test temporal contrasts", encoding="utf-8")
    config.agent.guidance_file = str(guide)
    config.agent.guidance_sha256 = file_digest(guide)
    config.search.paths_per_round = 1
    planner = DeepSeekPlanner(config)
    p = record("test", [1, 0])["path"]
    proposal = {k: v for k, v in p.items() if k not in ("schedule_hash", "expanded")}
    seen = []

    def request(messages):
        seen.extend(copy.deepcopy(messages))
        return json.dumps({"paths": [proposal]}), {}

    planner.request = request
    folder = tmp_path / "round"
    folder.mkdir()
    planner.propose(
        {"groups": {"C1": {}, "C2": {}}, "atoms": {}, "bonds": {}},
        {"evidence_ids": ["condition_library"]},
        {},
        folder,
    )
    assert read_json(folder / "agent_messages.json") == seen
    payload = json.loads(seen[1]["content"])
    assert payload["planning_guidance"]["sha256"] == config.agent.guidance_sha256
    assert payload["planning_guidance"]["content"] == guide.read_text(encoding="utf-8")
    guide.write_text("Changed guidance", encoding="utf-8")
    with pytest.raises(TechnicalFailure, match="changed"):
        planner.propose({"groups": {}, "atoms": {}, "bonds": {}}, {}, {}, folder)
    with pytest.raises(TechnicalFailure, match="checksum"):
        DeepSeekPlanner(config)


def test_contrast_feedback_matches_duration_and_tracks_primary_plateau():
    base, a, b = record("baseline", [.5, .5]), record("a", [1, 1]), record("b", [1, 1], True)
    history = dict(baseline=base, rounds=[dict(round=1, records=[a]), dict(round=2, records=[b])],
                   confirmation={"UNSEEN_CONFIRMATION": "excluded"})
    before = copy.deepcopy(history)
    result = build_feedback(history, {"groups": {"C1": {}, "C2": {}}}, 3, enhanced=True, contrast=True)
    assert result["search_progress"]["last_strict_primary_improvement_round"] == 1
    assert result["search_progress"]["rounds_since_strict_primary_improvement"] == 1
    match = result["matched_history_contrasts"]["b"]
    assert match["reference_id"] == "a" and match["eligible_references"] == 2
    assert match["schedule"]["same_per_condition_exposure"]
    assert match["paired"]["n"] == 2 and match["paired"]["mean_delta_J"] == 0
    assert result["success_decomposition"]["b"]["qualified_exact_recovery_count"] == 2
    assert "UNSEEN_CONFIRMATION" not in json.dumps(result) and history == before


def test_contrast_feedback_does_not_invent_legacy_joint_counts_or_duration_match():
    base, a = record("baseline", [1, 0]), record("a", [1, 0])
    base["summary"].pop("qualified_exact_recovery_count")
    base["summary"].pop("qualified_recovery")
    a["summary"]["exposure"]["active_steps"]["C1"] += 10
    result = build_feedback(dict(baseline=base, rounds=[dict(round=1, records=[a])]),
                            {"groups": {"C1": {}, "C2": {}}}, 2, enhanced=True, contrast=True)
    assert result["success_decomposition"]["baseline"]["qualified_exact_recovery_count"] is None
    assert result["success_decomposition"]["baseline"]["availability"] == "unavailable_in_legacy_summary"
    assert result["matched_history_contrasts"]["a"]["status"] == "no_equal_exposure_reference"
    with pytest.raises(TechnicalFailure, match="requires enhanced"):
        build_feedback(dict(baseline=base, rounds=[]), {"groups": {"C1": {}, "C2": {}}}, 1, contrast=True)
