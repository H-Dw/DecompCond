"""Replay saved raw outputs on CPU and compare all ranking/gating metrics."""

import argparse
import json
import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
import time


def check_one(job):
    from rdkit import Chem
    import numpy as np
    from decompcond.parallel_evaluation import _worker
    from decompcond.io import read_json, file_digest, digest

    p, output = Path(job["file"]), Path(job["output"])
    original = read_json(p)
    generated = read_json(p.parent / "generated.json")
    if digest(generated) != original["source_result_hash"]:
        raise ValueError("saved result provenance mismatch")
    proof = dict(file=str(p), sha256=file_digest(p), status=original["status"])
    if original["status"] != "evaluated":
        return proof
    if file_digest(generated["raw_path"]) != generated["raw_sha256"]:
        raise ValueError("raw bytes changed")
    mol = Chem.Mol(job["molecule"])
    with np.load(generated["raw_path"]) as raw:
        xyz = raw["coords"][0]
    if not np.array_equal(mol.GetConformer().GetPositions(), xyz):
        raise ValueError("raw decode/pickle changed XYZ")
    prepared = read_json(p.parents[3] / "prepared/prepared.json")
    library = read_json(prepared["files"]["library"])
    output.mkdir(parents=True, exist_ok=True)
    new = _worker.evaluate_molecule(
        mol,
        generated,
        prepared,
        library,
        output,
        dict(plip_selected=False, rule="audit_only"),
    )
    fields = [
        "status",
        "structure_pass",
        "atom_recovery",
        "bond_recovery",
        "recovery",
        "effective_recovery",
        "exact_recovery",
        "pb_checks",
        "atom_hits",
        "bond_hits",
        "bond_evidence",
        "raw_element_match_rate",
        "condition_metrics",
        "reconstruction_error",
        "selected_rmsd_A",
        "selected_max_displacement_A",
        "strain",
    ]
    mismatch = [k for k in fields if digest(new[k]) != digest(original[k])]
    proof.update(
        mismatches=mismatch, timing=new["timing_seconds"], worker_pid=os.getpid()
    )
    return proof


def main():
    from decompcond.config import load_config
    from decompcond.engine import algorithm_identity
    from decompcond.parallel_evaluation import initialize_worker
    from decompcond.io import write_json, file_digest

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--parent-run", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--reuse-audit")
    args = parser.parse_args()
    config = load_config(args.config)
    parent, output = Path(args.parent_run).resolve(), Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    files = sorted(parent.glob("*/*/*/*/evaluation.json"))
    if args.limit:
        # Uniformly spread the smoke replay over all saved files.
        files = [
            files[i * len(files) // min(args.limit, len(files))]
            for i in range(min(args.limit, len(files)))
        ]
    started = time.perf_counter()
    results = []
    if args.reuse_audit:
        from decompcond.io import read_json

        previous = read_json(args.reuse_audit)
        if (
            previous["status"] != "passed"
            or previous["algorithm_sha256"] != algorithm_identity()
            or previous["config_hash"] != config.fingerprint()
            or previous["parent_manifest_sha256"] != file_digest(parent / "run.json")
        ):
            raise ValueError("reused audit has an incompatible source/configuration")
        checked = {r["file"]: r for r in previous["results"]}
        pending = []
        for p in files:
            if str(p) in checked and checked[str(p)]["sha256"] == file_digest(p):
                results.append(checked[str(p)])
            else:
                pending.append(p)
        files = pending
    reused = len(results)
    # Decode with the same frozen ROOT builder, never via an SDF round-trip.
    # SDF can infer stereo that was absent in the raw model molecule and change
    # hydrogen placement in MMFF. No generation or affinity forward is called.
    from decompcond.backends.root import RootBackend
    from decompcond.io import read_json
    from rdkit import Chem

    decode_config = config.model_copy(deep=True)
    decode_config.data.output_dir = str(output / "decoder")
    backend = RootBackend(decode_config) if files else None
    payloads = []
    for p in files:
        saved = read_json(p)
        generated = read_json(p.parent / "generated.json")
        mol = None
        if generated["status"] != "generation_failure":
            mol = backend.decode(backend.load_raw(generated["raw_path"]))
        if (mol is not None) != (saved["status"] == "evaluated"):
            raise ValueError("raw decode status changed: " + str(p))
        flags = (
            Chem.PropertyPickleOptions.AllProps
            | Chem.PropertyPickleOptions.CoordsAsDouble
        )
        payloads.append(
            dict(
                file=str(p),
                output=str(output / "replay" / p.relative_to(parent).parent),
                molecule=mol.ToBinary(flags) if mol is not None else None,
            )
        )
    del backend
    with ProcessPoolExecutor(
        max_workers=config.evaluation.workers,
        mp_context=mp.get_context("spawn"),
        initializer=initialize_worker,
        initargs=(config.model_dump(),),
    ) as pool:
        jobs = [pool.submit(check_one, payload) for payload in payloads]
        for future in as_completed(jobs):
            results.append(future.result())
            write_json(
                output / "progress.json",
                dict(completed=len(results), planned=len(files) + reused),
            )
    failed = [r for r in results if r.get("mismatches")]
    report = dict(
        status="failed" if failed else "passed",
        parent_run=str(parent),
        parent_manifest_sha256=file_digest(parent / "run.json"),
        algorithm_sha256=algorithm_identity(),
        config_hash=config.fingerprint(),
        elapsed_seconds=time.perf_counter() - started,
        checked=len(results),
        reused_verified_results=reused,
        mismatches=failed,
        results=results,
        scope="all core acceptance/recovery/strain metrics on saved raw XYZ; no ROOT generation or affinity replay",
    )
    write_json(output / "audit.json", report)
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ["results", "algorithm_sha256", "mismatches"]
            }
        )
    )
    if failed:
        print(json.dumps(failed[:3]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
