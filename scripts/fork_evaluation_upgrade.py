"""Explicitly version an authorized evaluation change at completed-round boundaries.

Completed stages retain their exact original records. Incomplete stages retain
their frozen schedules and every generated attempt, but are evaluated uniformly
under the new policy. Prior partial reports remain in the immutable parent and
the child's legacy_partial directory. No candidate is sampled by this utility.
"""

import argparse
import copy
from pathlib import Path
import shutil

from decompcond.config import load_config
from decompcond.engine import algorithm_identity, exclusive_run
from decompcond.io import digest, file_digest, read_json, write_json

POLICY_FIELDS = {
    "protocol_version",
    "workers",
    "worker_threads",
    "fast_volume_overlap",
    "score_only_structure_pass",
    "plip_sampling",
    "plip_samples_per_path",
    "plip_sampling_salt",
}


def validate_change(old, new):
    a, b = copy.deepcopy(old), copy.deepcopy(new)
    a["data"].pop("output_dir")
    b["data"].pop("output_dir")
    for config in (a, b):
        for key in POLICY_FIELDS:
            config["evaluation"].pop(key, None)
    if a != b:
        raise ValueError(
            "upgrade may change only evaluation execution/diagnostic policy and output location"
        )


def fork_upgrade(parent, parent_source, config, audit_path):
    parent, parent_source, audit_path = map(
        lambda p: Path(p).resolve(), (parent, parent_source, audit_path)
    )
    destination = Path(config.data.output_dir).resolve()
    if (
        destination == parent
        or parent in destination.parents
        or destination in parent.parents
        or destination.exists()
    ):
        raise ValueError("new output must be an absent, disjoint directory")
    with exclusive_run(parent):
        old = read_json(parent / "run.json")
        validate_change(old["config"], config.model_dump())
        actual_source = {
            str(p.relative_to(parent_source / "src/decompcond")): file_digest(p)
            for p in sorted((parent_source / "src/decompcond").rglob("*.py"))
        }
        if actual_source != old["algorithm_sha256"]:
            raise ValueError("parent source changed")
        if file_digest(config.backend.checkpoint) != config.backend.checkpoint_sha256:
            raise ValueError("checkpoint changed")
        if file_digest(config.evaluation.pb_config) != old["posebusters_config_sha256"]:
            raise ValueError("PoseBusters preset changed")
        audit = read_json(audit_path)
        if (
            audit.get("status") != "passed"
            or audit["parent_run"] != str(parent)
            or audit["parent_manifest_sha256"] != file_digest(parent / "run.json")
            or audit["algorithm_sha256"] != algorithm_identity()
            or audit["config_hash"] != config.fingerprint()
        ):
            raise ValueError("missing/stale metric equivalence audit")
        evaluations = {
            str(p): file_digest(p) for p in parent.glob("*/*/*/*/evaluation.json")
        }
        if evaluations != {r["file"]: r["sha256"] for r in audit["results"]}:
            raise ValueError("audit must cover every evaluation present at stop")
        backend = read_json(parent / "backend_contract.json")
        if any(file_digest(p) != sha for p, sha in backend["source_sha256"].items()):
            raise ValueError("ROOT source changed")
        from decompcond.evaluation import Evaluator

        evaluator = Evaluator(config, None)
        old_contract = read_json(parent / "evaluation_contract.json")
        for key, value in old_contract.items():
            if key != "policy" and evaluator.contract.get(key) != value:
                raise ValueError("evaluator dependency changed: " + key)
        before = {
            str(p.relative_to(parent)): file_digest(p)
            for p in parent.rglob("*")
            if p.is_file() and p.name != ".run.lock"
        }
        shutil.copytree(parent, destination, ignore=shutil.ignore_patterns(".run.lock"))
        completed, reopened = [], []
        for teacher in config.data.teachers:
            consecutive = 0
            for r in range(1, config.search.rounds + 1):
                path = parent / teacher.id / f"round_{r:03d}"
                if (path / "complete.json").exists():
                    if r != consecutive + 1:
                        raise ValueError("completed rounds are not consecutive")
                    consecutive = r
            completed.append(dict(teacher=teacher.id, last_complete_round=consecutive))
        for file in destination.glob("*/prepared/prepared.json"):
            value = read_json(file)
            value["config_hash"] = config.fingerprint()
            value["files"] = {
                key: str(destination / Path(p).resolve().relative_to(parent))
                for key, p in value["files"].items()
            }
            write_json(file, value)
        for file in destination.glob("*/*/stage.json"):
            value = read_json(file)
            value["config_hash"] = config.fingerprint()
            write_json(file, value)
            done = file.parent / "complete.json"
            if done.exists():
                record = read_json(done)
                record["identity_hash"] = digest(value)
                for item in record["records"]:
                    reports = list(
                        (file.parent / item["path"]["id"]).glob("*/evaluation.json")
                    )
                    if len(reports) != item["summary"]["attempts"]:
                        raise ValueError("completed stage accounting mismatch")
                write_json(done, record)
            else:
                reopened.append(str(file.parent.relative_to(destination)))
                for attempt in file.parent.glob("*/*/generated.json"):
                    for name in (
                        "evaluation.json",
                        "cpu_evaluation.json",
                        "evaluation_error.json",
                        "actual.sdf",
                        "plip",
                    ):
                        artifact = attempt.parent / name
                        if artifact.exists():
                            target = (
                                destination
                                / "legacy_partial"
                                / artifact.relative_to(destination)
                            )
                            target.parent.mkdir(parents=True, exist_ok=True)
                            artifact.rename(target)
                for name in ("evaluation_plan.json", "progress.json"):
                    artifact = file.parent / name
                    if artifact.exists():
                        target = (
                            destination
                            / "legacy_partial"
                            / artifact.relative_to(destination)
                        )
                        target.parent.mkdir(parents=True, exist_ok=True)
                        artifact.rename(target)
        for file in destination.glob("*/round_*/plan.json"):
            value = read_json(file)
            value["binding"]["config_hash"] = config.fingerprint()
            write_json(file, value)
        for section in ("resolved_args", "hparams"):
            if backend[section]["save_dir"] != str(parent / "backend"):
                raise ValueError("unexpected ROOT save directory")
            backend[section]["save_dir"] = str(destination / "backend")
        write_json(destination / "backend_contract.json", backend)
        write_json(destination / "evaluation_contract.json", evaluator.contract)
        manifest = dict(
            config_hash=config.fingerprint(),
            config=config.model_dump(),
            backend_contract_hash=digest(backend),
            evaluation_contract_hash=digest(evaluator.contract),
            algorithm_sha256=algorithm_identity(),
            posebusters_config_sha256=file_digest(config.evaluation.pb_config),
        )
        write_json(destination / "run.json", manifest)
        proof = dict(
            kind="authorized_evaluation_policy_upgrade",
            parent_run=str(parent),
            parent_manifest_sha256=file_digest(parent / "run.json"),
            config_hash=config.fingerprint(),
            parent_config_hash=old["config_hash"],
            audit_file=str(audit_path),
            audit_sha256=file_digest(audit_path),
            boundaries=completed,
            reopened_stages=reopened,
            reused_generated_attempts=len(list(parent.glob("*/*/*/*/generated.json"))),
            parent_evaluated_attempts=len(evaluations),
            new_generation_during_migration=0,
            history_policy="completed records unchanged; incomplete stage reevaluated on same raw outputs",
            parent_artifact_sha256=before,
        )
        write_json(destination / "continuation.json", proof)
        after = {
            str(p.relative_to(parent)): file_digest(p)
            for p in parent.rglob("*")
            if p.is_file() and p.name != ".run.lock"
        }
        if before != after:
            raise ValueError("parent changed during migration")
        write_json(
            destination / "status.json",
            dict(status="upgrade_ready", parent_run=str(parent)),
        )
        return {k: v for k, v in proof.items() if k != "parent_artifact_sha256"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-run", required=True)
    parser.add_argument("--parent-source", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--audit", required=True)
    args = parser.parse_args()
    import json

    print(
        json.dumps(
            fork_upgrade(
                args.parent_run,
                args.parent_source,
                load_config(args.config),
                args.audit,
            )
        )
    )


if __name__ == "__main__":
    main()
