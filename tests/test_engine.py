"""Synthetic state-machine tests. These are not molecular generation results."""

from pathlib import Path
import pytest
from decompcond.engine import Experiment
from decompcond.io import read_json, write_json, TechnicalFailure
from decompcond.paths import compile_path


class Backend:
    contract = {"test_double": True}

    def __init__(self):
        self.generated = []

    def generate_batch(self, prepared, library, jobs):
        self.generated.extend((j["path"]["id"], j["seed"]) for j in jobs)
        return [
            dict(status="generated", path=j["path"]["id"], seed=j["seed"]) for j in jobs
        ]


class Eval:
    def __init__(self):
        self.fail = False
        self.evaluated = 0

    def evaluate(self, result, prepared, library, folder):
        if self.fail and result["path"] != "baseline":
            raise TechnicalFailure("temporary checker unavailable")
        self.evaluated += 1
        # First round wins: verify selection across all rounds, not just the last.
        r = int(result["path"].split("_")[0][1:]) if result["path"] != "baseline" else 0
        value = 1 / r if r else 0.1
        return dict(
            status="evaluated",
            structure_pass=True,
            exact_recovery=value == 1,
            atom_recovery=value,
            bond_recovery=value,
            recovery=value,
            effective_recovery=value,
            delta_pic50=0.0,
            condition_metrics={},
            pb_checks={},
            strain=None,
            plip=None,
        )


class Planner:
    def __init__(self):
        self.inputs = []

    def propose(self, library, feedback, previous, folder):
        self.inputs.append(feedback)
        r = feedback["round"]
        results = []
        for j in range(5):
            mask = (r - 1) * 5 + j + 1
            segments = [
                dict(
                    start=k * 10,
                    end=(k + 1) * 10,
                    active=["C1"] if (mask >> k) & 1 else ["C2"],
                )
                for k in range(10)
            ]
            p = dict(
                id=f"r{r}_p{j}",
                segments=segments,
                parents=[],
                evidence=[feedback["previous_strategies"][0]["evidence_id"]],
                observation="Synthetic state machine test",
                change="New mask",
                hypothesis="Test only",
            )
            results.append(compile_path(p, library["groups"], 100, 10))
        return results


def prepare(config, tmp_path):
    library = {
        "groups": {
            "C1": {"atom_ids": [1], "bond_ids": []},
            "C2": {"atom_ids": [2], "bond_ids": []},
        },
        "hash": "fixture",
    }
    path = tmp_path / "conditions.json"
    write_json(path, library)
    return dict(teacher={"id": "fixture"}, files={"library": str(path)})


def test_ten_round_barriers_history_budget_holdout_and_resume(config, tmp_path):
    backend = Backend()
    evaluator = Eval()
    planner = Planner()
    prepared = prepare(config, tmp_path)
    engine = Experiment(config, backend, evaluator, planner)
    final = engine.run([prepared])["fixture"]
    assert final["completed_rounds"] == 10
    assert len(backend.generated) == 1060
    assert len(planner.inputs) == 10
    for r, feedback in enumerate(planner.inputs, 1):
        assert feedback["round"] == r and feedback["previous_round"] == r - 1
        assert len(feedback["previous_strategies"]) == (1 if r == 1 else 5)
        assert "confirmation" not in str(feedback["history_trend"])
    assert final["winner"].startswith("r1_")
    assert all(seed < 2000 for _, seed in backend.generated[:-40])
    assert all(seed >= 2000 for _, seed in backend.generated[-40:])
    engine.run([prepared])
    assert len(backend.generated) == 1060 and len(planner.inputs) == 10


def test_evaluator_failure_stops_round_then_reuses_saved_generation(config, tmp_path):
    config.search.rounds = 2
    backend = Backend()
    evaluator = Eval()
    evaluator.fail = True
    planner = Planner()
    prepared = prepare(config, tmp_path)
    engine = Experiment(config, backend, evaluator, planner)
    with pytest.raises(TechnicalFailure):
        engine.run([prepared])
    assert len(planner.inputs) == 1 and len(backend.generated) == 120
    assert (
        read_json(Path(config.data.output_dir) / "fixture/history.json")["rounds"] == []
    )
    evaluator.fail = False
    engine.run([prepared])
    assert len(backend.generated) == 260 and len(planner.inputs) == 2


def test_changed_config_cannot_resume(config, tmp_path):
    prepared = prepare(config, tmp_path)
    engine = Experiment(config, Backend(), Eval(), Planner())
    engine.run([prepared], baseline_only=True)
    config.sampling.known_time = 0.9
    with pytest.raises(TechnicalFailure):
        engine.run([prepared], baseline_only=True)


def test_changed_backend_cannot_overwrite_saved_contract(config, tmp_path):
    prepared = prepare(config, tmp_path)
    backend = Backend()
    engine = Experiment(config, backend, Eval(), Planner())
    engine.run([prepared], baseline_only=True)
    path = Path(config.data.output_dir) / "backend_contract.json"
    before = path.read_bytes()
    backend.contract = {"test_double": True, "changed": True}
    with pytest.raises(TechnicalFailure):
        engine.run([prepared], baseline_only=True)
    assert path.read_bytes() == before
