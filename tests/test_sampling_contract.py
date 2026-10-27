import copy
import json
from pathlib import Path
import pytest

from decompcond.agent import DeepSeekPlanner
from decompcond.config import load_config
from decompcond.io import digest, read_json
from decompcond.sampling_contract import planning_sampling_contract


def test_multi_contract_exposes_actual_terminal_semantics():
    config = load_config(Path(__file__).parents[1] / "configs/multi-style.yaml")
    contract = planning_sampling_contract(config)
    assert contract["known_atom_network_time"] == 0.999
    assert contract["last_loop_prediction"]["global_time"] == pytest.approx(0.98901)
    assert contract["readout"]["global_time"] == pytest.approx(0.98901)
    assert contract["readout"]["extra_forward_count"] == 0
    assert contract["readout"]["forward_count_per_candidate"] == 100
    assert not contract["readout"]["final_post_update_projection_can_affect_output"]
    assert contract["self_condition"]["initial_coords"] == "zeros"
    assert contract["planning_windows"][-1] == pytest.approx(dict(
        start=90, end=100, first_prediction_global_time=0.8991,
        last_prediction_global_time=0.98901,
    ))
    assert contract["sha256"] == digest({k: v for k, v in contract.items() if k != "sha256"})


def test_legacy_contract_and_changed_grid_are_computed_from_config(config):
    old = planning_sampling_contract(config)
    assert old["readout"]["global_time"] == pytest.approx(0.9999)
    assert old["readout"]["forward_count_per_candidate"] == 101
    assert old["readout"]["final_post_update_projection_can_affect_output"]
    config.sampling.steps = 40
    config.sampling.segment_multiple = 5
    config.sampling.integration_end_time = 0.8
    config.sampling.readout = "last_prediction"
    config.sampling.final_epsilon = None
    config.backend.model_args["sc_recycle_steps"] = 2
    changed = planning_sampling_contract(config)
    assert changed["sha256"] != old["sha256"]
    assert len(changed["planning_windows"]) == 8
    assert changed["readout"]["global_time"] == pytest.approx(0.78)
    assert changed["readout"]["forward_count_per_candidate"] == 80


def test_actual_agent_request_contains_contract_and_new_guidance(tmp_path):
    config = load_config(Path(__file__).parents[1] / "configs/multi-style.yaml")
    config.search.paths_per_round = 1
    planner = DeepSeekPlanner(config)
    seen = []
    proposal = dict(
        id="terminal_release",
        segments=[dict(start=0, end=90, active=["selected"]), dict(start=90, end=100, active=[])],
        parents=[], evidence=["condition_library"],
        observation="Need terminal readout comparison", change="Release final block",
        hypothesis="Test raw geometry and recovery without additional terminal prediction",
    )

    def request(messages):
        seen.extend(copy.deepcopy(messages))
        return json.dumps({"paths": [proposal]}), {}

    planner.request = request
    feedback = {"evidence_ids": ["condition_library"]}
    planner.propose(
        {"groups": {"selected": {}}, "atoms": {}, "bonds": {}, "hash": "teacher-specific-hash"},
        feedback, {}, tmp_path,
    )
    saved = read_json(tmp_path / "agent_input.json")
    assert json.loads(seen[1]["content"]) == saved
    assert saved["sampling_contract"] == planning_sampling_contract(config)
    assert saved["condition_library_hash"] == "teacher-specific-hash"
    assert "no extra terminal forward pass" in saved["planning_guidance"]["content"]
    assert saved["planning_guidance"]["sha256"] == config.agent.guidance_sha256
    assert feedback == {"evidence_ids": ["condition_library"]}
