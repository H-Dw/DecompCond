"""Read-only progress and completion audit; never generates or repairs candidates."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def inspect_run(folder, pid=None):
    root = Path(folder)
    errors, activity = [], []

    def read(path, default=None):
        if not path.exists():
            return default
        activity.append(path.stat().st_mtime)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append({"file": str(path), "error_type": type(exc).__name__})
            return default

    manifest = read(root / "run.json", {})
    config = manifest.get("config", {})
    search = config.get("search", {})
    status = read(root / "status.json", {})
    process = {"pid": pid, "running": None}
    if pid is not None and Path("/proc").is_dir():
        try:
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
            process["running"] = b"decompcond.cli" in command
        except (FileNotFoundError, ProcessLookupError):
            process["running"] = False
        except PermissionError:
            process["inspection_error"] = "PermissionError"

    teachers = []
    for teacher in config.get("data", {}).get("teachers", []):
        directory = root / teacher["id"]
        final = read(directory / "final.json", {})
        stages, outcomes, qualified = [], Counter(), 0
        specifications = [("baseline", 1, search["baseline_candidates"])]
        specifications += [
            (f"round_{r:03d}", search["paths_per_round"], search["candidates_per_path"])
            for r in range(1, search["rounds"] + 1)
        ]
        if final.get("status") != "no_structure_qualified_candidate":
            specifications.append(
                ("confirmation", 2, search["confirmation_candidates"])
            )
        for name, path_count, candidates in specifications:
            stage = directory / name
            generated_files = list(stage.glob("*/*/generated.json"))
            evaluated_files = list(stage.glob("*/*/evaluation.json"))
            complete = read(stage / "complete.json", {})
            plan = read(stage / "plan.json", {})
            unresolved = [
                str(p.relative_to(root))
                for p in stage.glob("*/*/evaluation_error.json")
                if not (p.parent / "evaluation.json").exists()
            ]
            for file in generated_files:
                activity.append(file.stat().st_mtime)
            for file in evaluated_files:
                row = read(file, {})
                outcomes[row.get("status", "unreadable")] += 1
                qualified += int(row.get("structure_pass", False))
                if row.get("status") not in (
                    "evaluated",
                    "chemical_invalid",
                    "generation_failure",
                ):
                    errors.append(
                        {"file": str(file), "error_type": "NonterminalEvaluation"}
                    )
            expected = path_count * candidates
            records = complete.get("records", [])
            checked_complete = bool(complete) and (
                len(generated_files) == len(evaluated_files) == expected
                and len(records) == path_count
                and all(
                    r.get("summary", {}).get("attempts") == candidates for r in records
                )
                and len({r.get("path", {}).get("id") for r in records}) == path_count
                and all(
                    sum(
                        p.parent.parent.name == r.get("path", {}).get("id")
                        for p in evaluated_files
                    )
                    == candidates
                    for r in records
                )
                and {p.parent for p in generated_files}
                == {p.parent for p in evaluated_files}
                and not unresolved
            )
            if complete and not checked_complete:
                errors.append(
                    {
                        "file": str(stage / "complete.json"),
                        "error_type": "AccountingMismatch",
                    }
                )
            stages.append(
                {
                    "stage": name,
                    "planned_attempts": expected,
                    "generated": len(generated_files),
                    "evaluated": len(evaluated_files),
                    "plan_frozen": bool(plan),
                    "complete": checked_complete,
                    "unresolved_evaluation_errors": unresolved,
                    "progress": read(stage / "progress.json", {}),
                }
            )
        complete_rounds = sum(
            s["complete"] for s in stages if s["stage"].startswith("round_")
        )
        terminal = final.get("status") in (
            "complete",
            "no_structure_qualified_candidate",
        )
        teachers.append(
            {
                "id": teacher["id"],
                "target_id": teacher["target_id"],
                "completed_rounds": complete_rounds,
                "required_rounds": search["rounds"],
                "generated": sum(s["generated"] for s in stages),
                "evaluated": sum(s["evaluated"] for s in stages),
                "structure_pass_count": qualified,
                "outcomes": dict(outcomes),
                "final_status": final.get("status"),
                "completion_verified": terminal and all(s["complete"] for s in stages),
                "current_stage": next(
                    (s["stage"] for s in stages if not s["complete"]), None
                ),
                "stages": stages,
            }
        )
    existing = root
    while not existing.exists():
        existing = existing.parent
    disk = shutil.disk_usage(existing)
    return {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "run_dir": str(root),
        "config_hash": manifest.get("config_hash"),
        "process": process,
        "recorded_status": status.get("status", "initializing"),
        "recorded_error_type": status.get("error_type"),
        "generated": sum(t["generated"] for t in teachers),
        "evaluated": sum(t["evaluated"] for t in teachers),
        "maximum_formal_attempts": len(teachers)
        * (
            search.get("baseline_candidates", 0)
            + search.get("rounds", 0)
            * search.get("paths_per_round", 0)
            * search.get("candidates_per_path", 0)
            + 2 * search.get("confirmation_candidates", 0)
        )
        if manifest
        else None,
        "full_experiment_verified": bool(teachers)
        and status.get("status") == "complete"
        and all(t["completion_verified"] for t in teachers)
        and not errors,
        "last_artifact_update_utc": datetime.fromtimestamp(
            max(activity), UTC
        ).isoformat()
        if activity
        else None,
        "free_disk_bytes": disk.free,
        "teachers": teachers,
        "inspection_errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect_run(args.run_dir, args.pid)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + f".{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(args.output)
    print(text, end="")


if __name__ == "__main__":
    main()
