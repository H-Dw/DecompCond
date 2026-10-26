"""Replay saved outputs before an explicitly versioned PLIP validation repair."""

import argparse
import copy
import shutil
from pathlib import Path

from rdkit import Chem

from decompcond.conditions import compile_conditions
from decompcond.config import load_config
from decompcond.io import digest, file_digest, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--parent-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--failed-result", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    parent_files = {
        str(p.relative_to(args.parent_run)): file_digest(p)
        for p in args.parent_run.rglob("*")
        if p.is_file()
    }
    from decompcond.backends.root import RootBackend
    from decompcond.engine import algorithm_identity
    from decompcond.evaluation import Evaluator
    from decompcond.plip import plip_profile

    backend = RootBackend(config)
    evaluator = Evaluator(config, backend)
    assert backend.contract == read_json(args.parent_run / "backend_contract.json")
    assert evaluator.contract == read_json(args.parent_run / "evaluation_contract.json")
    verified, teachers = [], []
    prepared_by_id = {}
    for teacher in config.data.teachers:
        prepared = read_json(args.parent_run / teacher.id / "prepared/prepared.json")
        library = read_json(prepared["files"]["library"])
        mol = next(
            iter(Chem.SDMolSupplier(prepared["files"]["teacher"], removeHs=True))
        )
        profile = plip_profile(
            mol,
            prepared["files"]["evaluation_protein"],
            args.output_dir / teacher.id / "teacher",
            config.evaluation.plip,
        )
        rebuilt = compile_conditions(
            mol, profile["core_atoms"], config.partition, profile["interactions"]
        )
        assert digest(rebuilt) == digest(library), "teacher conditions changed"
        teachers.append({"id": teacher.id, "library_hash": library["hash"]})
        # The evaluator caches teacher scores beside the teacher file: isolate it.
        isolated = copy.deepcopy(prepared)
        dest = args.output_dir / teacher.id / "teacher.sdf"
        shutil.copy2(prepared["files"]["teacher"], dest)
        isolated["files"]["teacher"] = str(dest)
        prepared_by_id[teacher.id] = (isolated, library)
        for path in sorted(
            (args.parent_run / teacher.id).glob("*/*/*/evaluation.json")
        ):
            result = read_json(path.parent / "generated.json")
            old = read_json(path)
            assert old["source_result_hash"] == digest(result)
            if result.get("raw_path"):
                assert file_digest(result["raw_path"]) == result["raw_sha256"]
            if old["status"] == "evaluated":
                mol = backend.decode(backend.load_raw(result["raw_path"]))
                assert mol is not None
                out = args.output_dir / path.parent.relative_to(args.parent_run)
                profile = plip_profile(
                    mol,
                    prepared["files"]["evaluation_protein"],
                    out / "plip",
                    config.evaluation.plip,
                )
                assert digest(profile) == digest(
                    read_json(path.parent / "plip/interactions.json")
                ), str(path)
            elif old["status"] == "chemical_invalid":
                assert backend.decode(backend.load_raw(result["raw_path"])) is None
            else:
                assert old["status"] == "generation_failure"
            verified.append(
                {
                    "file": str(path.relative_to(args.parent_run)),
                    "sha256": file_digest(path),
                    "status": old["status"],
                }
            )
            if len(verified) % 10 == 0:
                print(f"Verified {len(verified)} saved evaluations", flush=True)
    failed = read_json(args.failed_result)
    teacher_id = args.failed_result.relative_to(args.parent_run).parts[0]
    prepared, library = prepared_by_id[teacher_id]
    repaired = evaluator.evaluate(
        failed, prepared, library, args.output_dir / "failed-candidate"
    )
    repaired["source_result_hash"] = digest(failed)
    write_json(args.output_dir / "failed-candidate/evaluation.json", repaired)
    after = {
        str(p.relative_to(args.parent_run)): file_digest(p)
        for p in args.parent_run.rglob("*")
        if p.is_file()
    }
    assert after == parent_files, "audit altered the parent run"
    report = {
        "status": "passed",
        "parent_run": str(args.parent_run),
        "parent_run_sha256": file_digest(args.parent_run / "run.json"),
        "algorithm_sha256": algorithm_identity(),
        "verified_teachers": teachers,
        "verified_evaluations": verified,
        "parent_unchanged": True,
        "parent_artifact_sha256": parent_files,
        "failed_result": str(args.failed_result.relative_to(args.parent_run)),
        "repaired_evaluation_sha256": file_digest(
            args.output_dir / "failed-candidate/evaluation.json"
        ),
        "repaired_evaluation_status": repaired["status"],
    }
    write_json(args.output_dir / "audit.json", report)
    print(
        f"PASS: {len(verified)} prior evaluations preserved; failed evaluation now {repaired['status']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
