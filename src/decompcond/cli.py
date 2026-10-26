"""Explicit CLI stages; validation/preparation never instantiate ROOT."""

from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
from .config import load_config
from .io import TechnicalFailure


def main(argv=None):
    parser = argparse.ArgumentParser(prog="decompcond")
    parser.add_argument(
        "command",
        choices=["validate", "doctor", "prepare", "baseline", "experiment", "report"],
    )
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--set", action="append", default=[], dest="overrides", metavar="KEY=VALUE"
    )
    args = parser.parse_args(argv)
    config = load_config(args.config, args.overrides)
    if args.command == "validate":
        total = len(config.data.teachers) * (
            config.search.baseline_candidates
            + config.search.rounds
            * config.search.paths_per_round
            * config.search.candidates_per_path
            + 2 * config.search.confirmation_candidates
        )
        print(
            json.dumps(
                dict(
                    valid=True,
                    config_hash=config.fingerprint(),
                    maximum_formal_attempts=total,
                )
            )
        )
        return
    if args.command == "doctor":
        from .credentials import read_credential

        info = dict(
            paths={
                p: Path(p).exists()
                for p in [
                    config.data.archive_dir,
                    config.backend.checkpoint,
                    config.backend.source_dir,
                    config.evaluation.pb_config,
                ]
            },
            packages={
                name: bool(importlib.util.find_spec(name))
                for name in [
                    "rdkit",
                    "torch",
                    "openbabel",
                    "plip",
                    "posebusters",
                    "httpx",
                ]
            },
        )
        for mode in ("env", "interactive_shell"):
            try:
                info["credential_" + mode] = bool(
                    read_credential(
                        config.agent.credentials.model_copy(update={"mode": mode})
                    )
                )
            except TechnicalFailure:
                info["credential_" + mode] = False
        print(json.dumps(info))
        return
    if args.command == "report":
        from .report import render_report

        print(render_report(config))
        return
    from .conditions import prepare_teacher

    prepared = [prepare_teacher(config, t) for t in config.data.teachers]
    if args.command == "prepare":
        print(json.dumps({"prepared": [p["teacher"]["id"] for p in prepared]}))
        return
    from .backends.root import RootBackend
    from .evaluation import Evaluator
    from .agent import DeepSeekPlanner
    from .engine import Experiment

    backend = RootBackend(config)
    engine = Experiment(
        config, backend, Evaluator(config, backend), DeepSeekPlanner(config)
    )
    try:
        engine.run(prepared, baseline_only=args.command == "baseline")
    finally:
        from .report import render_report

        render_report(config)


if __name__ == "__main__":
    main()
