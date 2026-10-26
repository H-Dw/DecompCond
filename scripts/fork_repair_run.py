"""Fork an audited technical repair without regenerating saved attempts.

The parent remains immutable. Only output location and explicitly audited source
modules may differ. This is not a generic way to bypass experiment contracts.
"""

import argparse
import copy
import shutil
from pathlib import Path

from decompcond.config import load_config
from decompcond.engine import algorithm_identity, exclusive_run
from decompcond.io import digest, file_digest, read_json, write_json


def fork_repair(parent, config, audit_file, allowed_modules):
    parent, audit_file = Path(parent).resolve(), Path(audit_file).resolve()
    destination = Path(config.data.output_dir).resolve()
    if (
        destination == parent
        or parent in destination.parents
        or destination in parent.parents
    ):
        raise ValueError("repair output and parent must be disjoint directories")
    if destination.exists():
        raise ValueError("repair destination already exists")
    with exclusive_run(parent):
        original = read_json(parent / "run.json")
        if read_json(parent / "status.json")["status"] != "needs_attention":
            raise ValueError("parent is not a stopped technical failure")
        expected_config = copy.deepcopy(original["config"])
        expected_config["data"]["output_dir"] = config.data.output_dir
        if expected_config != config.model_dump():
            raise ValueError("technical repair may change only output_dir in config")
        current_algorithm = algorithm_identity()
        changed = {
            k
            for k in set(current_algorithm) | set(original["algorithm_sha256"])
            if current_algorithm.get(k) != original["algorithm_sha256"].get(k)
        }
        if not changed or changed != set(allowed_modules):
            raise ValueError("source changes do not match the explicit repair scope")
        audit = read_json(audit_file)
        if (
            audit.get("status") != "passed"
            or not audit.get("parent_unchanged")
            or Path(audit["parent_run"]).resolve() != parent
            or audit["parent_run_sha256"] != file_digest(parent / "run.json")
            or audit["algorithm_sha256"] != current_algorithm
        ):
            raise ValueError("missing or incompatible repair audit")
        parent_hashes = {
            str(p.relative_to(parent)): file_digest(p)
            for p in parent.rglob("*")
            if p.is_file() and p.name != ".run.lock"
        }
        audited_hashes = {
            k: v for k, v in audit["parent_artifact_sha256"].items() if k != ".run.lock"
        }
        if parent_hashes != audited_hashes:
            raise ValueError("parent changed after the audit")
        evaluations = {
            str(p.relative_to(parent)): file_digest(p)
            for p in parent.glob("*/*/*/*/evaluation.json")
        }
        checked = {r["file"]: r["sha256"] for r in audit["verified_evaluations"]}
        if evaluations != checked:
            raise ValueError("audit does not cover every existing evaluation")
        teacher_ids = {t.id for t in config.data.teachers}
        if teacher_ids != {t["id"] for t in audit["verified_teachers"]}:
            raise ValueError("teacher audit is incomplete")
        for teacher in audit["verified_teachers"]:
            prepared = read_json(parent / teacher["id"] / "prepared/prepared.json")
            if prepared["library_hash"] != teacher["library_hash"]:
                raise ValueError("condition library audit mismatch")
        failed_file = Path(audit["failed_result"])
        if failed_file.is_absolute() or ".." in failed_file.parts:
            raise ValueError("invalid failed result path")
        repair_folder = audit_file.parent / "failed-candidate"
        repaired = read_json(repair_folder / "evaluation.json")
        if (
            file_digest(repair_folder / "evaluation.json")
            != audit["repaired_evaluation_sha256"]
            or repaired["source_result_hash"] != digest(read_json(parent / failed_file))
            or repaired["status"]
            not in ("evaluated", "chemical_invalid", "generation_failure")
        ):
            raise ValueError("repaired result lacks matching source evidence")
        if (parent / failed_file.parent / "evaluation.json").exists():
            raise ValueError("repair must not overwrite an existing evaluation")
        shutil.copytree(parent, destination, ignore=shutil.ignore_patterns(".run.lock"))
        # Raw references keep pointing to immutable parent bytes. Prepared inputs
        # move to the copy: teacher-score caching must not write into the parent.
        for file in destination.glob("*/prepared/prepared.json"):
            value = read_json(file)
            value["config_hash"] = config.fingerprint()
            value["files"] = {
                key: str(destination / Path(path).resolve().relative_to(parent))
                for key, path in value.get("files", {}).items()
            }
            write_json(file, value)
        for file in destination.glob("*/*/stage.json"):
            value = read_json(file)
            value["config_hash"] = config.fingerprint()
            write_json(file, value)
            complete = file.parent / "complete.json"
            if complete.exists():
                record = read_json(complete)
                record["identity_hash"] = digest(value)
                write_json(complete, record)
        for file in destination.glob("*/round_*/plan.json"):
            value = read_json(file)
            value["binding"]["config_hash"] = config.fingerprint()
            write_json(file, value)
        manifest = copy.deepcopy(original)
        manifest.update(
            config_hash=config.fingerprint(),
            config=config.model_dump(),
            algorithm_sha256=current_algorithm,
        )
        # ROOT records its output directory in resolved_args and loaded hparams.
        # Relocate that operational field; every scientific backend field stays.
        backend_contract = read_json(parent / "backend_contract.json")
        for section in ("resolved_args", "hparams"):
            if backend_contract[section]["save_dir"] != str(parent / "backend"):
                raise ValueError("unexpected parent backend output directory")
            backend_contract[section]["save_dir"] = str(destination / "backend")
        manifest["backend_contract_hash"] = digest(backend_contract)
        write_json(destination / "backend_contract.json", backend_contract)
        write_json(destination / "run.json", manifest)
        shutil.copytree(
            repair_folder, destination / failed_file.parent, dirs_exist_ok=True
        )
        proof = {
            "kind": "audited_technical_repair",
            "parent_run": str(parent),
            "parent_run_sha256": file_digest(parent / "run.json"),
            "audit_file": str(audit_file),
            "audit_sha256": file_digest(audit_file),
            "changed_modules": sorted(changed),
            "parent_config_hash": original["config_hash"],
            "config_hash": config.fingerprint(),
            "reused_generated_attempts": len(
                list(parent.glob("*/*/*/*/generated.json"))
            ),
            "unchanged_existing_evaluations": len(evaluations),
            "repaired_evaluations": 1,
            "new_generation_during_repair": 0,
            "relocated_backend_fields": ["resolved_args.save_dir", "hparams.save_dir"],
            "absolute_artifact_references": "raw outputs retain parent references and source hashes; prepared inputs are copied",
        }
        write_json(destination / "repair_provenance.json", proof)
        after = {
            str(p.relative_to(parent)): file_digest(p)
            for p in parent.rglob("*")
            if p.is_file() and p.name != ".run.lock"
        }
        if after != parent_hashes:
            raise ValueError("parent was changed during repair fork")
        write_json(
            destination / "status.json",
            {"status": "repair_ready", "parent_run": str(parent)},
        )
        return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-run", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--allow-changed-module", action="append", required=True)
    args = parser.parse_args()
    import json

    print(
        json.dumps(
            fork_repair(
                args.parent_run,
                load_config(args.config),
                args.audit,
                args.allow_changed_module,
            )
        )
    )


if __name__ == "__main__":
    main()
