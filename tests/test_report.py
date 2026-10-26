from decompcond.io import write_json, read_json
from decompcond.report import render_candidate_index


def test_candidate_ranking_keeps_failures_and_separates_confirmation(tmp_path):
    for stage, seed, score, qualified in [
        ("baseline", 1, 1.0, True),
        ("round_001", 2, 2.0, True),
        ("round_001", 3, 0.0, False),
        ("confirmation", 4, 9.0, True),
    ]:
        write_json(
            tmp_path / "teacher" / stage / "strategy" / str(seed) / "evaluation.json",
            dict(
                status="evaluated" if qualified else "chemical_invalid",
                structure_pass=qualified,
                recovery=1.0 if qualified else 0.0,
                pic50=score,
            ),
        )
    render_candidate_index(tmp_path)
    result = read_json(tmp_path / "candidates.json")
    assert len(result["all_attempts"]) == 4
    assert [r["seed"] for r in result["discovery_ranking"]] == [2, 1]
    assert [r["seed"] for r in result["confirmation_ranking"]] == [4]
    assert all(len(r["report_sha256"]) == 64 for r in result["all_attempts"])
