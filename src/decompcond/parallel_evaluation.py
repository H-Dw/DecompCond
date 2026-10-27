"""Spawned CPU workers; the parent alone owns ROOT and affinity inference."""

import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
from .io import read_json, write_json, digest, file_digest, TechnicalFailure

_worker = None


def initialize_worker(config_data):
    global _worker
    from .config import Config

    config = Config.model_validate(config_data)
    threads = config.evaluation.worker_threads
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = str(threads)
    if hasattr(os, "sched_getaffinity"):
        available = sorted(os.sched_getaffinity(0))
        rank = (mp.current_process()._identity or (1,))[0] - 1
        start = rank * threads % len(available)
        os.sched_setaffinity(
            0, {available[(start + i) % len(available)] for i in range(threads)}
        )
    from .evaluation import Evaluator

    _worker = Evaluator(config, None)


def cpu_job(payload):
    from rdkit import Chem

    mol = Chem.Mol(payload["molecule"])
    result = _worker.evaluate_molecule(
        mol,
        payload["result"],
        payload["prepared"],
        payload["library"],
        payload["folder"],
        payload["diagnostics"],
    )
    result["worker_pid"] = os.getpid()
    return result


def cached_cpu_job(payload):
    folder = Path(payload["folder"])
    cache = folder / "cpu_evaluation.json"
    binding = payload["binding"]
    if cache.exists():
        old = read_json(cache)
        if old["binding"] != binding:
            raise TechnicalFailure("CPU evaluation cache contract changed")
        return old["report"]
    for retry in range(payload["retries"] + 1):
        try:
            report = cpu_job(payload)
            write_json(cache, dict(binding=binding, report=report))
            return report
        except Exception as exc:
            write_json(
                folder / "evaluation_error.json",
                dict(
                    type=type(exc).__name__,
                    message=str(exc),
                    retry=retry,
                    phase="cpu",
                    status="technical_failure",
                ),
            )
            if retry == payload["retries"]:
                raise TechnicalFailure(
                    "CPU evaluation retry budget exhausted: " + str(folder)
                ) from exc


def evaluate_many(evaluator, jobs, prepared, library, plan):
    from rdkit import Chem
    from .evaluation import annotate_failure

    c = evaluator.config
    if evaluator._pool is None:
        evaluator._pool = ProcessPoolExecutor(
            max_workers=c.evaluation.workers,
            mp_context=mp.get_context("spawn"),
            initializer=initialize_worker,
            initargs=(c.model_dump(),),
        )
    futures = {}
    for job, result in jobs:
        diagnostics = dict(
            protocol=plan["protocol"],
            rule=plan["rule"],
            plip_selected=job["seed"] in plan["selected_seeds"],
            seed=job["seed"],
            planned_plip_attempts=plan["planned_plip_attempts"],
            planned_attempts=plan["planned_attempts"],
        )
        mol = None
        for retry in range(c.search.evaluation_retries + 1):
            try:
                mol = evaluator.prepare_input(result)
                break
            except Exception as exc:
                write_json(
                    Path(job["folder"]) / "evaluation_error.json",
                    dict(
                        type=type(exc).__name__,
                        message=str(exc),
                        retry=retry,
                        phase="decode",
                        status="technical_failure",
                    ),
                )
                if retry == c.search.evaluation_retries:
                    raise TechnicalFailure("decode retry budget exhausted") from exc
        if isinstance(mol, dict):
            yield job, result, annotate_failure(mol, diagnostics, c)
            continue
        flags = (
            Chem.PropertyPickleOptions.AllProps
            | Chem.PropertyPickleOptions.CoordsAsDouble
        )
        binary = mol.ToBinary(flags)
        binding = dict(
            source_result_hash=digest(result),
            evaluation=digest(c.evaluation.model_dump()),
            pb=file_digest(c.evaluation.pb_config),
            diagnostics=diagnostics,
            condition_hash=library["hash"],
        )
        payload = dict(
            molecule=binary,
            result=result,
            prepared=prepared,
            library=library,
            folder=job["folder"],
            diagnostics=diagnostics,
            binding=binding,
            retries=c.search.evaluation_retries,
        )
        future = evaluator._pool.submit(cached_cpu_job, payload)
        futures[future] = (job, result, mol)
    for future in as_completed(futures):
        job, result, mol = futures[future]
        report = future.result()
        for retry in range(c.search.evaluation_retries + 1):
            try:
                report = evaluator.finish_affinity(report, mol, prepared)
                break
            except Exception as exc:
                write_json(
                    Path(job["folder"]) / "evaluation_error.json",
                    dict(
                        type=type(exc).__name__,
                        message=str(exc),
                        retry=retry,
                        phase="affinity",
                        status="technical_failure",
                    ),
                )
                if retry == c.search.evaluation_retries:
                    raise TechnicalFailure("affinity retry budget exhausted") from exc
        yield job, result, report
