"""Small real ROOT and Agent validation, separate from formal research attempts."""

import argparse
import gc
from pathlib import Path
import yaml
from pydantic import Field
from decompcond.config import Strict, load_config
from decompcond.io import read_json, write_json, file_digest, TechnicalFailure
from decompcond.conditions import prepare_teacher
from decompcond.paths import constant_path
from decompcond.feedback import build_feedback
from decompcond.sampling_contract import planning_sampling_contract


class ValidationPlan(Strict):
    base_config: str
    output_dir: str
    parent_run: str
    baseline_seeds: list[int] = Field(min_length=1)
    agent_seeds: list[int] = Field(min_length=1)
    agent_variant: str
    variants: dict[str, list[str]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args()
    plan = ValidationPlan.model_validate(yaml.safe_load(args.plan.read_text()))
    if plan.agent_variant not in plan.variants:
        raise ValueError("agent variant absent")
    if set(plan.baseline_seeds) & set(plan.agent_seeds):
        raise ValueError("technical baseline and agent seeds must be distinct")
    for seeds in [plan.baseline_seeds, plan.agent_seeds]:
        if len(seeds) != len(set(seeds)) or min(seeds) < 0:
            raise ValueError("invalid technical seeds")
    root = Path(plan.output_dir)
    base_path = args.plan.parent / plan.base_config
    common = load_config(base_path, [f"data.output_dir={root / 'fixtures'}"])
    prepared = [prepare_teacher(common, t) for t in common.data.teachers]
    for item in prepared:
        parent = read_json(Path(plan.parent_run) / item["teacher"]["id"] / "prepared/prepared.json")
        if item["library_hash"] != parent["library_hash"]:
            raise TechnicalFailure("technical validation changed the condition library")
    write_json(root / "validation-plan.json", dict(
        plan=plan.model_dump(), source_plan_sha256=file_digest(args.plan),
        kind="technical_only_not_formal_search", formal_confirmation_observed=False,
    ))
    from decompcond.backends.root import RootBackend
    from decompcond.evaluation import Evaluator
    from decompcond.agent import DeepSeekPlanner
    from decompcond.engine import Experiment, exclusive_run
    import torch

    all_records = {}
    with exclusive_run(root):
        for variant, overrides in plan.variants.items():
            config = load_config(base_path, overrides + [f"data.output_dir={root / variant}"])
            backend = RootBackend(config)
            evaluator = Evaluator(config, backend)
            planner = DeepSeekPlanner(config)
            engine = Experiment(config, backend, evaluator, planner)
            engine.bind()
            variant_records = {}
            try:
                for item in prepared:
                    teacher = item["teacher"]["id"]
                    library = read_json(item["files"]["library"])
                    baseline = constant_path(library["groups"], config.sampling.steps, config.sampling.segment_multiple)
                    records = engine.stage(item, library, [baseline], "baseline", plan.baseline_seeds)
                    variant_records[teacher] = dict(baseline=records[0])
                    if variant == plan.agent_variant:
                        folder = Path(config.data.output_dir) / teacher / "planner_validation"
                        feedback = build_feedback(dict(baseline=records[0], rounds=[]), library, 1, enhanced=True)
                        feedback["validation_notice"] = (
                            "Small technical interface test. Only supplied new-protocol baseline attempts "
                            "are evidence; no formal confirmation or historical protocol metrics are supplied."
                        )
                        paths_file = folder / "validated_paths.json"
                        if paths_file.exists():
                            paths = read_json(paths_file)
                        else:
                            paths = planner.propose(library, feedback, {baseline["schedule_hash"]: records[0]}, folder)
                            write_json(paths_file, paths)
                        results = engine.stage(item, library, paths, "agent_probe", plan.agent_seeds)
                        variant_records[teacher]["agent_paths"] = results
                    contract = planning_sampling_contract(config)
                    for file in (Path(config.data.output_dir) / teacher).glob("*/*/*/generated.json"):
                        result = read_json(file)
                        if result["sampling_contract_sha256"] != contract["sha256"]:
                            raise TechnicalFailure("candidate sampler differs from Agent contract")
                        if result["status"] == "generated" and (
                            result["nfe"] != contract["readout"]["forward_count_per_candidate"]
                            or abs(result["readout_global_time"] - contract["readout"]["global_time"]) > 1e-5
                        ):
                            raise TechnicalFailure("actual readout/call count differs from contract")
                    print(f"validated {variant} {teacher}", flush=True)
                all_records[variant] = variant_records
                write_json(root / "summary.json", all_records)
            finally:
                evaluator.close()
                del engine, evaluator, backend, planner
                gc.collect()
                torch.cuda.empty_cache()
        expected = len(prepared) * (
            len(plan.variants) * len(plan.baseline_seeds)
            + common.search.paths_per_round * len(plan.agent_seeds)
        )
        generated = list(root.glob("*/*/*/*/*/generated.json"))
        evaluated = list(root.glob("*/*/*/*/*/evaluation.json"))
        if len(generated) != len(evaluated) or len(evaluated) != expected:
            raise TechnicalFailure("technical attempt accounting mismatch")
        write_json(root / "complete.json", dict(
            status="passed", generated=len(generated), evaluated=len(evaluated),
            kind="technical_only", formal_attempts=0,
        ))


if __name__ == "__main__":
    main()
