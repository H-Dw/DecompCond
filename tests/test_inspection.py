"""A misleading completion flag must not trigger another expensive experiment."""

import json
import runpy
from pathlib import Path

inspect_run = runpy.run_path(str(Path(__file__).parents[1] / "scripts/inspect_run.py"))[
    "inspect_run"
]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def completed_fixture(root, final_status="complete"):
    write(
        root / "run.json",
        {
            "config": {
                "data": {"teachers": [{"id": "teacher", "target_id": "target"}]},
                "search": {
                    "rounds": 1,
                    "paths_per_round": 1,
                    "candidates_per_path": 1,
                    "baseline_candidates": 1,
                    "confirmation_candidates": 1,
                },
            }
        },
    )
    write(root / "status.json", {"status": "complete"})
    write(root / "teacher/final.json", {"status": final_status})
    specifications = [("baseline", ["base"]), ("round_001", ["plan"])]
    if final_status == "complete":
        specifications.append(("confirmation", ["base", "plan"]))
    for name, paths in specifications:
        folder = root / "teacher" / name
        for path in paths:
            write(folder / path / "1/generated.json", {"status": "generated"})
            write(
                folder / path / "1/evaluation.json",
                {"status": "chemical_invalid", "structure_pass": False},
            )
        write(
            folder / "complete.json",
            {
                "records": [
                    {"path": {"id": path}, "summary": {"attempts": 1}} for path in paths
                ]
            },
        )


def test_requires_confirmation_accounting(tmp_path):
    completed_fixture(tmp_path)
    assert inspect_run(tmp_path)["full_experiment_verified"]
    (tmp_path / "teacher/confirmation/base/1/evaluation.json").unlink()
    report = inspect_run(tmp_path)
    assert not report["full_experiment_verified"]
    assert report["generated"] == 4 and report["evaluated"] == 3


def test_no_qualified_path_is_explicit_terminal_outcome(tmp_path):
    completed_fixture(tmp_path, "no_structure_qualified_candidate")
    report = inspect_run(tmp_path)
    assert report["full_experiment_verified"]
    assert report["maximum_formal_attempts"] == 4 and report["evaluated"] == 2


def test_technical_missing_result_is_not_a_completed_evaluation(tmp_path):
    completed_fixture(tmp_path)
    write(
        tmp_path / "teacher/round_001/plan/1/evaluation.json",
        {"status": "technical_failure"},
    )
    assert not inspect_run(tmp_path)["full_experiment_verified"]


def test_initializing_run_has_unknown_budget(tmp_path):
    report = inspect_run(tmp_path)
    assert not report["full_experiment_verified"]
    assert report["maximum_formal_attempts"] is None
