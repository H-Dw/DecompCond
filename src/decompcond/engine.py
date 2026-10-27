"""Barriered feedback rounds with atomic per-attempt persistence and resume."""

from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import os
from .io import read_json, write_json, digest, file_digest, TechnicalFailure


def algorithm_identity():
    """Bind resumes to the implementation and metric preset, not only YAML."""
    root = Path(__file__).parent
    return {
        str(p.relative_to(root)): file_digest(p) for p in sorted(root.rglob("*.py"))
    }


from .paths import constant_path, validate_batch
from .feedback import summarize, build_feedback, best_record
from .evaluation_policy import diagnostic_plan


@contextmanager
def exclusive_run(folder):
    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    stream = (path / ".run.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise TechnicalFailure("another process owns this experiment") from None
    try:
        yield
    finally:
        stream.close()


class Experiment:
    def __init__(self, config, backend, evaluator, planner):
        self.config = config
        self.backend = backend
        self.evaluator = evaluator
        self.planner = planner
        self.root = Path(config.data.output_dir)

    def bind(self):
        payload = dict(
            config_hash=self.config.fingerprint(),
            config=self.config.model_dump(),
            backend_contract_hash=digest(self.backend.contract),
            evaluation_contract_hash=digest(getattr(self.evaluator, "contract", None)),
            algorithm_sha256=algorithm_identity(),
            posebusters_config_sha256=file_digest(self.config.evaluation.pb_config),
        )
        manifest = self.root / "run.json"
        if manifest.exists() and read_json(manifest) != payload:
            raise TechnicalFailure(
                "configuration/backend contract changed; use a new run directory"
            )
        write_json(manifest, payload)
        write_json(self.root / "backend_contract.json", self.backend.contract)
        write_json(
            self.root / "evaluation_contract.json",
            getattr(self.evaluator, "contract", None),
        )

    def stage(self, prepared, library, paths, stage_id, seeds):
        folder = self.root / prepared["teacher"]["id"] / stage_id
        identity = dict(
            config_hash=self.config.fingerprint(),
            library_hash=library["hash"],
            schedules=[p["schedule_hash"] for p in paths],
            seeds=seeds,
        )
        manifest = folder / "stage.json"
        if manifest.exists() and read_json(manifest) != identity:
            raise TechnicalFailure("stage identity changed on resume")
        write_json(manifest, identity)
        jobs = []
        # Interleave paths, so each device microbatch includes different schedules.
        for seed in seeds:
            for p in paths:
                sub = folder / p["id"] / str(seed)
                jobs.append(dict(path=p, seed=seed, folder=str(sub)))
        complete_file = folder / "complete.json"
        if complete_file.exists():
            complete = read_json(complete_file)
            if complete["identity_hash"] != digest(identity):
                raise TechnicalFailure("completed stage identity mismatch")
            for job in jobs:
                generated = read_json(Path(job["folder"]) / "generated.json")
                report = read_json(Path(job["folder"]) / "evaluation.json")
                if report.get("source_result_hash") != digest(generated):
                    raise TechnicalFailure("completed evaluation source changed")
                if (
                    generated.get("raw_path")
                    and file_digest(generated["raw_path"]) != generated["raw_sha256"]
                ):
                    raise TechnicalFailure("completed raw artifact changed")
            records = complete["records"]
            if len(records) != len(paths) or any(
                record["path"] != path or record["summary"]["attempts"] != len(seeds)
                for record, path in zip(records, paths)
            ):
                raise TechnicalFailure("completed stage accounting mismatch")
            # Imported completed stages retain original feedback and policy.
            return records
        policy = diagnostic_plan(
            self.config, prepared["teacher"]["id"], stage_id, seeds
        )
        policy_file = folder / "evaluation_plan.json"
        if policy_file.exists() and read_json(policy_file) != policy:
            raise TechnicalFailure("diagnostic sampling changed on resume")
        write_json(policy_file, policy)
        pending = [
            j for j in jobs if not (Path(j["folder"]) / "generated.json").exists()
        ]
        size = self.config.sampling.batch_size
        for start in range(0, len(pending), size):
            batch = pending[start : start + size]
            generated = self.backend.generate_batch(prepared, library, batch)
            if len(generated) != len(batch):
                raise TechnicalFailure("backend lost candidate outputs")
            for job, result in zip(batch, generated):
                write_json(Path(job["folder"]) / "generated.json", result)
            write_json(
                folder / "progress.json",
                dict(
                    phase="generation",
                    attempts=len(jobs),
                    generated=sum(
                        (Path(j["folder"]) / "generated.json").exists() for j in jobs
                    ),
                ),
            )
        # A separate barrier ensures no Agent sees a partial strategy table.
        if hasattr(self.evaluator, "evaluate_many"):
            pending_evaluations = []
            completed = 0
            for job in jobs:
                output = Path(job["folder"])
                result = read_json(output / "generated.json")
                if (
                    result.get("raw_path")
                    and file_digest(result["raw_path"]) != result["raw_sha256"]
                ):
                    raise TechnicalFailure("saved raw output changed on resume")
                if (output / "evaluation.json").exists():
                    if read_json(output / "evaluation.json").get(
                        "source_result_hash"
                    ) != digest(result):
                        raise TechnicalFailure("saved evaluation source changed")
                    completed += 1
                else:
                    pending_evaluations.append((job, result))
            for job, result, evaluated in self.evaluator.evaluate_many(
                pending_evaluations, prepared, library, policy
            ):
                evaluated["source_result_hash"] = digest(result)
                write_json(Path(job["folder"]) / "evaluation.json", evaluated)
                completed += 1
                write_json(
                    folder / "progress.json",
                    dict(phase="evaluation", attempts=len(jobs), evaluated=completed),
                )
            if completed != len(jobs):
                raise TechnicalFailure(
                    "evaluation barrier did not account for every attempt"
                )
        for index, job in enumerate(jobs):
            output = Path(job["folder"])
            report = output / "evaluation.json"
            result = read_json(output / "generated.json")
            if (
                result.get("raw_path")
                and file_digest(result["raw_path"]) != result["raw_sha256"]
            ):
                raise TechnicalFailure("saved raw output changed on resume")
            if report.exists():
                saved = read_json(report)
                if saved.get("source_result_hash") != digest(result):
                    raise TechnicalFailure(
                        "saved evaluation belongs to a different generation result"
                    )
                continue
            for retry in range(self.config.search.evaluation_retries + 1):
                try:
                    evaluated = self.evaluator.evaluate(
                        result, prepared, library, output
                    )
                    evaluated["source_result_hash"] = digest(result)
                    write_json(report, evaluated)
                    break
                except Exception as exc:
                    write_json(
                        output / "evaluation_error.json",
                        dict(
                            type=type(exc).__name__,
                            message=str(exc),
                            retry=retry,
                            status="technical_failure",
                        ),
                    )
                    if retry == self.config.search.evaluation_retries:
                        raise TechnicalFailure(
                            f"evaluation unresolved: {stage_id}/{job['path']['id']}/{job['seed']}; see evaluation_error.json"
                        ) from None
            write_json(
                folder / "progress.json",
                dict(phase="evaluation", attempts=len(jobs), evaluated=index + 1),
            )
        records = []
        for p in paths:
            reports = [
                read_json(folder / p["id"] / str(seed) / "evaluation.json")
                for seed in seeds
            ]
            summary = summarize(p, reports, library, seeds=seeds)
            write_json(folder / p["id"] / "summary.json", summary)
            records.append(dict(path=p, summary=summary, stage=stage_id))
        write_json(
            folder / "complete.json",
            dict(identity_hash=digest(identity), records=records),
        )
        return records

    def run_teacher(self, prepared, baseline_only=False):
        c = self.config
        library = read_json(prepared["files"]["library"])
        teacher_dir = self.root / prepared["teacher"]["id"]
        disc = list(
            range(
                c.sampling.discovery_seed_start,
                c.sampling.discovery_seed_start + c.sampling.seed_count,
            )
        )
        hold = list(
            range(
                c.sampling.confirmation_seed_start,
                c.sampling.confirmation_seed_start + c.sampling.seed_count,
            )
        )
        baseline_path = constant_path(
            library["groups"], c.sampling.steps, c.sampling.segment_multiple
        )
        baseline = self.stage(
            prepared,
            library,
            [baseline_path],
            "baseline",
            disc[: c.search.baseline_candidates],
        )[0]
        history = dict(baseline=baseline, rounds=[])
        write_json(teacher_dir / "history.json", history)
        if baseline_only:
            return history
        previous = {baseline_path["schedule_hash"]: baseline}
        for r in range(1, c.search.rounds + 1):
            round_dir = teacher_dir / f"round_{r:03d}"
            frozen = round_dir / "plan.json"
            feedback = build_feedback(
                history, library, r, enhanced=c.agent.enhanced_feedback,
                contrast=c.agent.contrast_feedback,
            )
            write_json(round_dir / "feedback.json", feedback)
            expected = dict(
                config_hash=c.fingerprint(),
                library_hash=library["hash"],
                history_hash=digest(history),
            )
            if frozen.exists():
                record = read_json(frozen)
                if record["binding"] != expected:
                    raise TechnicalFailure("round history changed on resume")
                paths = record["paths"]
                proposals = [
                    {
                        k: v
                        for k, v in p.items()
                        if k not in ("expanded", "schedule_hash")
                    }
                    for p in paths
                ]
                checked = validate_batch(
                    proposals,
                    library,
                    c.sampling.steps,
                    c.sampling.segment_multiple,
                    c.search.paths_per_round,
                    previous,
                    set(feedback["evidence_ids"]),
                )
                if checked != paths:
                    raise TechnicalFailure("frozen path content changed")
            else:
                paths = self.planner.propose(library, feedback, previous, round_dir)
                # Validate independently of the planner implementation.
                proposals = [
                    {
                        k: v
                        for k, v in p.items()
                        if k not in ("expanded", "schedule_hash")
                    }
                    for p in paths
                ]
                paths = validate_batch(
                    proposals,
                    library,
                    c.sampling.steps,
                    c.sampling.segment_multiple,
                    c.search.paths_per_round,
                    previous,
                    set(feedback["evidence_ids"]),
                )
                write_json(frozen, dict(binding=expected, paths=paths))
            records = self.stage(
                prepared,
                library,
                paths,
                f"round_{r:03d}",
                disc[: c.search.candidates_per_path],
            )
            history["rounds"].append(dict(round=r, records=records))
            previous.update(
                {record["path"]["schedule_hash"]: record for record in records}
            )
            write_json(teacher_dir / "history.json", history)
        winner = best_record(
            [record for r in history["rounds"] for record in r["records"]]
        )
        if winner is None:
            final = dict(
                status="no_structure_qualified_candidate",
                completed_rounds=len(history["rounds"]),
                confirmation=None,
            )
        else:
            write_json(teacher_dir / "selected_path.json", winner["path"])
            confirmation = self.stage(
                prepared,
                library,
                [winner["path"], baseline_path],
                "confirmation",
                hold[: c.search.confirmation_candidates],
            )
            final = dict(
                status="complete",
                completed_rounds=len(history["rounds"]),
                winner=winner["path"]["id"],
                confirmation=confirmation,
            )
        write_json(teacher_dir / "final.json", final)
        return final

    def run(self, prepared_teachers, baseline_only=False):
        with exclusive_run(self.root):
            self.bind()
            try:
                results = {
                    p["teacher"]["id"]: self.run_teacher(p, baseline_only)
                    for p in prepared_teachers
                }
                write_json(
                    self.root / "status.json",
                    dict(
                        status="baseline_complete" if baseline_only else "complete",
                        teachers=list(results),
                    ),
                )
                return results
            except Exception as exc:
                write_json(
                    self.root / "status.json",
                    dict(
                        status="needs_attention",
                        error_type=type(exc).__name__,
                        message=str(exc),
                    ),
                )
                raise
            finally:
                if hasattr(self.evaluator, "close"):
                    self.evaluator.close()
