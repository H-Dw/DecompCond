"""Only audited, separately versioned technical repair may reuse frozen work."""

import copy
import runpy
from pathlib import Path

import pytest

from decompcond.engine import algorithm_identity
from decompcond.io import digest, file_digest, read_json, write_json

fork_repair = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/fork_repair_run.py")
)["fork_repair"]


def fixture(config, tmp_path):
    parent = Path(config.data.output_dir)
    original_config = config.model_dump()
    old_algorithm = algorithm_identity()
    old_algorithm["plip.py"] = "previous_version"
    write_json(
        parent / "run.json",
        {
            "config": original_config,
            "config_hash": config.fingerprint(),
            "algorithm_sha256": old_algorithm,
        },
    )
    write_json(parent / "status.json", {"status": "needs_attention"})
    (parent / ".run.lock").touch()
    for teacher in config.data.teachers:
        teacher_file = parent / teacher.id / "prepared/teacher.txt"
        teacher_file.parent.mkdir(parents=True, exist_ok=True)
        teacher_file.write_text("immutable reference", encoding="utf-8")
        write_json(
            parent / teacher.id / "prepared/prepared.json",
            {
                "config_hash": config.fingerprint(),
                "library_hash": "same_conditions",
                "files": {"teacher": str(teacher_file)},
            },
        )
    teacher = config.data.teachers[0].id
    stage = parent / teacher / "round_001"
    write_json(
        stage / "stage.json", {"config_hash": config.fingerprint(), "seeds": [1, 2]}
    )
    write_json(
        stage / "plan.json",
        {
            "binding": {
                "config_hash": config.fingerprint(),
                "history_hash": "unchanged",
            },
            "paths": [{"id": "path"}],
        },
    )
    for seed in [1, 2]:
        write_json(
            stage / "path" / str(seed) / "generated.json",
            {"seed": seed, "status": "generated"},
        )
    result = read_json(stage / "path/1/generated.json")
    report = {
        "status": "evaluated",
        "source_result_hash": digest(result),
        "recovery": 0.5,
    }
    write_json(stage / "path/1/evaluation.json", report)
    audit_folder = tmp_path / "audit"
    repaired = {
        "status": "evaluated",
        "source_result_hash": digest(read_json(stage / "path/2/generated.json")),
        "recovery": 0.6,
    }
    write_json(audit_folder / "failed-candidate/evaluation.json", repaired)
    evaluation_path = stage / "path/1/evaluation.json"
    proof = {
        "status": "passed",
        "parent_unchanged": True,
        "parent_run": str(parent),
        "parent_run_sha256": file_digest(parent / "run.json"),
        "algorithm_sha256": algorithm_identity(),
        "parent_artifact_sha256": {
            str(p.relative_to(parent)): file_digest(p)
            for p in parent.rglob("*")
            if p.is_file() and p.name != ".run.lock"
        },
        "verified_evaluations": [
            {
                "file": str(evaluation_path.relative_to(parent)),
                "sha256": file_digest(evaluation_path),
            }
        ],
        "verified_teachers": [
            {"id": t.id, "library_hash": "same_conditions"}
            for t in config.data.teachers
        ],
        "failed_result": str((stage / "path/2/generated.json").relative_to(parent)),
        "repaired_evaluation_sha256": file_digest(
            audit_folder / "failed-candidate/evaluation.json"
        ),
    }
    write_json(audit_folder / "audit.json", proof)
    new = copy.deepcopy(config)
    new.data.output_dir = str(tmp_path / "repaired")
    return parent, new, audit_folder / "audit.json", stage.relative_to(parent)


def test_fork_preserves_parent_and_saved_attempts(config, tmp_path):
    parent, new, audit, stage = fixture(config, tmp_path)
    before = read_json(audit)["parent_artifact_sha256"]
    report = fork_repair(parent, new, audit, ["plip.py"])
    destination = Path(new.data.output_dir)
    assert report["reused_generated_attempts"] == 2
    assert report["unchanged_existing_evaluations"] == 1
    assert report["new_generation_during_repair"] == 0
    teacher = config.data.teachers[0].id
    copied_prepared = read_json(destination / teacher / "prepared/prepared.json")
    assert Path(copied_prepared["files"]["teacher"]).is_relative_to(destination)
    assert (
        Path(copied_prepared["files"]["teacher"]).read_text() == "immutable reference"
    )
    assert read_json(destination / stage / "path/1/evaluation.json") == read_json(
        parent / stage / "path/1/evaluation.json"
    )
    assert (
        read_json(destination / stage / "plan.json")["binding"]["history_hash"]
        == "unchanged"
    )
    assert (
        read_json(destination / stage / "plan.json")["binding"]["config_hash"]
        == new.fingerprint()
    )
    assert before == {
        str(p.relative_to(parent)): file_digest(p)
        for p in parent.rglob("*")
        if p.is_file() and p.name != ".run.lock"
    }
    assert not (parent / stage / "path/2/evaluation.json").exists()
    with pytest.raises(ValueError, match="already exists"):
        fork_repair(parent, new, audit, ["plip.py"])


def test_repair_rejects_scientific_config_change(config, tmp_path):
    parent, new, audit, _ = fixture(config, tmp_path)
    new.sampling.known_time = 0.9
    with pytest.raises(ValueError, match="only output_dir"):
        fork_repair(parent, new, audit, ["plip.py"])


def test_repair_rejects_stale_or_incomplete_audit(config, tmp_path):
    parent, new, audit, _ = fixture(config, tmp_path)
    data = read_json(audit)
    data["verified_evaluations"] = []
    write_json(audit, data)
    with pytest.raises(ValueError, match="every existing evaluation"):
        fork_repair(parent, new, audit, ["plip.py"])
