"""Real CPU workers and affinity replay on saved attempts; no new generation."""

import argparse
import copy
import json
import shutil
from pathlib import Path
import time


def main():
    from decompcond.config import load_config
    from decompcond.backends.root import RootBackend
    from decompcond.evaluation import Evaluator
    from decompcond.evaluation_policy import diagnostic_plan
    from decompcond.io import read_json, write_json

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-stage", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--paths", type=int, required=True)
    parser.add_argument("--seeds-per-path", type=int, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    source, output = Path(args.source_stage), Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    prepared = read_json(source.parent / "prepared/prepared.json")
    prepared = copy.deepcopy(prepared)
    shutil.copyfile(prepared["files"]["teacher"], output / "teacher.sdf")
    prepared["files"]["teacher"] = str(output / "teacher.sdf")
    library = read_json(prepared["files"]["library"])
    config.data.output_dir = str(output / "runtime")
    backend = RootBackend(config)

    def forbidden(*args):
        raise AssertionError("validation must not generate")

    backend.generate_batch = forbidden
    evaluator = Evaluator(config, backend)
    paths = sorted(p for p in source.iterdir() if p.is_dir())[: args.paths]
    all_seeds = sorted(int(p.parent.name) for p in paths[0].glob("*/generated.json"))
    policy = diagnostic_plan(config, prepared["teacher"]["id"], source.name, all_seeds)
    jobs, expected = [], {}
    for path in paths:
        for seed in all_seeds[: args.seeds_per_path]:
            original = path / str(seed)
            destination = output / "attempts" / path.name / str(seed)
            destination.mkdir(parents=True)
            job = dict(seed=seed, path={"id": path.name}, folder=str(destination))
            jobs.append((job, read_json(original / "generated.json")))
            expected[str(destination)] = read_json(original / "evaluation.json")
    started = time.perf_counter()
    records = []
    try:
        for job, result, report in evaluator.evaluate_many(
            jobs, prepared, library, policy
        ):
            old = expected[job["folder"]]
            for key in ("status", "structure_pass", "effective_recovery"):
                if report[key] != old[key]:
                    raise AssertionError(key)
            if (
                report["structure_pass"]
                and abs(report["delta_pic50"] - old["delta_pic50"]) > 1e-6
            ):
                raise AssertionError("affinity changed")
            if report.get("plip") is not None and report["plip"] != old["plip"]:
                raise AssertionError("PLIP changed")
            if not report["structure_pass"] and report.get("pic50") is not None:
                raise AssertionError("ineligible affinity was computed")
            write_json(Path(job["folder"]) / "evaluation.json", report)
            records.append(report)
        cache_before = {
            str(p): p.stat().st_mtime_ns for p in output.rglob("cpu_evaluation.json")
        }
        resumed = list(evaluator.evaluate_many(jobs, prepared, library, policy))
        cache_after = {
            str(p): p.stat().st_mtime_ns for p in output.rglob("cpu_evaluation.json")
        }
        if cache_before != cache_after or len(resumed) != len(records):
            raise AssertionError("CPU results were recomputed on resume")
    finally:
        evaluator.close()
    summary = dict(
        status="passed",
        source_stage=str(source),
        attempts=len(records),
        new_generated=0,
        elapsed_seconds=time.perf_counter() - started,
        worker_pids=sorted({r["worker_pid"] for r in records if "worker_pid" in r}),
        plip_measured=sum(r.get("plip") is not None for r in records),
        cpu_cache_reused=True,
        affinity_and_plip_equal=True,
        protocol="technical replay only; not formal candidates",
    )
    write_json(output / "validation.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
