"""One real planner batch against archived discovery feedback; no generation."""

import argparse
import copy
import json
from pathlib import Path
from decompcond.agent import DeepSeekPlanner
from decompcond.config import load_config
from decompcond.feedback import build_feedback
from decompcond.paths import exposure
from decompcond.planning_evidence import schedule_relation
from decompcond.io import read_json, write_json


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--teacher-dir", required=True)
    p.add_argument("--previous-rounds", type=int, required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    config = load_config(a.config)
    td, output = Path(a.teacher_dir), Path(a.output)
    output.mkdir(parents=True, exist_ok=False)
    history = copy.deepcopy(read_json(td / "history.json"))
    history["rounds"] = history["rounds"][: a.previous_rounds]
    prepared = read_json(td / "prepared/prepared.json")
    library = read_json(prepared["files"]["library"])
    records = [history["baseline"]] + [
        r for rr in history["rounds"] for r in rr["records"]
    ]
    for r in records:
        r["summary"]["seed_scores"] = {
            p.parent.name: read_json(p)["effective_recovery"]
            for p in (td / r["stage"] / r["path"]["id"]).glob("*/evaluation.json")
        }
    previous = {r["path"]["schedule_hash"]: r for r in records}
    feedback = build_feedback(
        history,
        library,
        len(history["rounds"]) + 1,
        enhanced=config.agent.enhanced_feedback,
        contrast=config.agent.contrast_feedback,
    )
    paths = DeepSeekPlanner(config).propose(library, feedback, previous, output)
    new_records = [
        dict(path=p, summary={"exposure": exposure(p, list(library["groups"]))})
        for p in paths
    ]
    matches = []
    for i, r in enumerate(new_records):
        for ref in records + new_records[:i]:
            relation = schedule_relation(r, ref, library["groups"])
            if relation["same_per_condition_exposure"]:
                matches.append(
                    dict(
                        path=r["path"]["id"],
                        reference=ref["path"]["id"],
                        relation=relation,
                    )
                )
    report = dict(
        status="passed",
        no_generation=True,
        validated_paths=len(paths),
        exact_exposure_matches=matches,
        guidance_sha256=config.agent.guidance_sha256,
        feedback_version=feedback.get("feedback_version"),
        paths=paths,
    )
    write_json(output / "validation.json", report)
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("paths", "exact_exposure_matches")
            }
            | {"exposure_matched_pairs": len(matches)}
        )
    )
