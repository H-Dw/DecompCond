"""Audit a completed experiment and export compact, reproducible research evidence."""

import argparse
import json
from pathlib import Path
from collections import Counter
from itertools import combinations
from datetime import datetime, timezone
import statistics
from inspect_run import inspect_run
from decompcond.io import read_json, write_json, digest, file_digest
from decompcond.planning_evidence import schedule_relation


def analyze(root):
    root = Path(root).resolve()
    inspection = inspect_run(root)
    if not inspection["full_experiment_verified"]:
        raise ValueError("complete two-stage accounting required before research")
    evidence = dict(
        parent_run=str(root),
        audited_at_utc=datetime.now(timezone.utc).isoformat(),
        inspection=inspection,
        files={},
        teachers={},
    )

    def read(path):
        evidence["files"][str(path.relative_to(root))] = file_digest(path)
        return read_json(path)

    manifest = read(root / "run.json")
    for teacher in manifest["config"]["data"]["teachers"]:
        td = root / teacher["id"]
        history, final = read(td / "history.json"), read(td / "final.json")
        prepared = read(td / "prepared/prepared.json")
        library = read_json(prepared["files"]["library"])
        records = (
            [history["baseline"]]
            + [v for rr in history["rounds"] for v in rr["records"]]
            + (final.get("confirmation") or [])
        )
        total_pb, groups, compact, transitions = Counter(), {}, [], []
        for record in records:
            stage, pid = record["stage"], record["path"]["id"]
            folder = td / stage / pid
            evaluations = sorted(folder.glob("*/evaluation.json"))
            rows = []
            for path in evaluations:
                row, generated = read(path), read(path.parent / "generated.json")
                if row["source_result_hash"] != digest(generated):
                    raise ValueError("candidate provenance changed")
                if (
                    generated.get("raw_path")
                    and file_digest(generated["raw_path"]) != generated["raw_sha256"]
                ):
                    raise ValueError("raw candidate changed")
                rows.append(row)
            s = record["summary"]
            if (
                len(rows) != s["attempts"]
                or abs(statistics.mean(r["effective_recovery"] for r in rows) - s["J"])
                > 1e-12
            ):
                raise ValueError("summary accounting mismatch")
            entry = dict(
                stage=stage,
                path_id=pid,
                segments=record["path"]["segments"],
                parents=record["path"]["parents"],
                metrics=s,
                qualified_exact=sum(
                    r["structure_pass"] and r.get("exact_recovery", False) for r in rows
                ),
            )
            compact.append(entry)
            if stage.startswith("round_"):
                total_pb.update(s["pb_failures"])
                for gid, counts in s["condition_metrics"].items():
                    groups.setdefault(gid, Counter()).update(counts)
                transitions.append(
                    dict(
                        path_id=pid,
                        J=s["J"],
                        exposure=s["exposure"],
                        reentry_displacement_A=s["reentry_displacement_A"],
                    )
                )
        diversity = []
        for rr in history["rounds"]:
            pairs = [
                schedule_relation(a, b, library["groups"])
                for a, b in combinations(rr["records"], 2)
            ]
            diversity.append(
                dict(
                    round=rr["round"],
                    mean_activity_distance=statistics.mean(
                        p["normalized_activity_distance"] for p in pairs
                    )
                    if pairs
                    else None,
                    same_exposure_pairs=sum(
                        p["same_per_condition_exposure"] for p in pairs
                    ),
                )
            )
        score_file = td / "prepared/score_reference.json"
        evidence["teachers"][teacher["id"]] = dict(
            records=compact,
            rounds_diversity=diversity,
            search_pb_failures=dict(total_pb),
            search_condition_counts={k: dict(v) for k, v in groups.items()},
            transitions=transitions,
            winner=final.get("winner"),
            teacher_reference=read(score_file) if score_file.exists() else None,
            library_hash=library["hash"],
        )
    evidence["source_manifest_sha256"] = file_digest(root / "run.json")
    evidence["warning"] = (
        "Adaptive discovery comparisons are descriptive; old confirmation is development evidence once used to design a successor. PLIP denominators differ across policies."
    )
    return evidence


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    result = analyze(a.run_dir)
    write_json(a.output, result)
    print(
        json.dumps(
            dict(
                status="passed",
                generated=result["inspection"]["generated"],
                evaluated=result["inspection"]["evaluated"],
                bound_files=len(result["files"]),
                teachers=list(result["teachers"]),
            )
        )
    )
