"""Discovery-only comparisons for planning; never changes path ranking."""

import math
import statistics
from itertools import combinations, groupby


def paired_returns(candidate, reference):
    a = candidate["summary"].get("seed_scores", {})
    b = reference["summary"].get("seed_scores", {})
    if not a or set(a) != set(b):
        return dict(status="unavailable_seed_pairing", n=0)
    differences = [a[k] - b[k] for k in sorted(a)]
    n = len(differences)
    return dict(
        status="paired_same_seeds",
        n=n,
        mean_delta_J=statistics.mean(differences),
        standard_error=statistics.stdev(differences) / math.sqrt(n) if n > 1 else None,
        wins=sum(d > 0 for d in differences),
        ties=sum(d == 0 for d in differences),
        losses=sum(d < 0 for d in differences),
        interpretation="Descriptive discovery comparison after adaptive selection; not independent confirmation or a significance test",
    )


def schedule_relation(candidate, reference, groups):
    a, b = candidate["path"]["expanded"], reference["path"]["expanded"]
    if len(a) != len(b) or not a or not groups:
        raise ValueError("schedule comparison requires the same nonempty grid")
    changes = [
        (tuple(sorted(set(x) - set(y))), tuple(sorted(set(y) - set(x))))
        for x, y in zip(a, b)
    ]
    windows, cursor = [], 0
    for (added, removed), block in groupby(changes):
        end = cursor + sum(1 for _ in block)
        if added or removed:
            windows.append(
                dict(start=cursor, end=end, added=list(added), removed=list(removed))
            )
        cursor = end
    ea, eb = candidate["summary"]["exposure"], reference["summary"]["exposure"]
    dose = {g: ea["active_steps"][g] - eb["active_steps"][g] for g in groups}
    return dict(
        normalized_activity_distance=sum(len(x) + len(y) for x, y in changes)
        / (len(a) * len(groups)),
        changed_windows=windows,
        active_steps_delta=dose,
        same_per_condition_exposure=all(v == 0 for v in dose.values()),
        empty_steps_delta=ea["empty_steps"] - eb["empty_steps"],
        reentries_delta={g: ea["reentries"][g] - eb["reentries"][g] for g in groups},
        interpretation="Equal per-condition exposure controls marginal duration only; coactivation and reentry can still differ",
    )


def enhance_feedback(feedback, all_records, previous, library):
    groups = list(library["groups"])
    by_id = {r["path"]["id"]: r for r in all_records}
    for record in all_records:
        s = record["summary"]
        evidence = feedback["evidence"][f"metrics:{record['path']['id']}"]
        # Keep counts for old subcondition evidence; do not turn missing or
        # no-selected-bond cases into successful recovery rates.
        for key in (
            "exposure",
            "condition_metrics",
            "strain",
            "strain_statuses",
            "plip_recall",
            "plip_sampling",
            "reentry_displacement_A",
            "evaluation_protocols",
        ):
            if key in s:
                evidence[key] = s[key]
        evidence["coordinate_displacement"] = {
            key: s["distributions"][key]
            for key in ("selected_rmsd_A", "selected_max_displacement_A")
        }
    references = {"baseline": all_records[0]}
    if feedback["history_best"]:
        references["history_best"] = feedback["history_best"]
    comparisons = {}
    for record in previous:
        candidates = dict(references)
        candidates.update(
            {
                pid: by_id[pid]
                for pid in record["path"].get("parents", [])
                if pid in by_id
            }
        )
        comparisons[record["path"]["id"]] = {
            name: dict(
                reference_id=ref["path"]["id"],
                evidence_id=f"metrics:{ref['path']['id']}",
                paired=paired_returns(record, ref),
                schedule=schedule_relation(record, ref, groups),
            )
            for name, ref in candidates.items()
        }
    pairs = []
    for a, b in combinations(previous, 2):
        relation = schedule_relation(a, b, groups)
        pairs.append(
            dict(
                path_ids=[a["path"]["id"], b["path"]["id"]],
                distance=relation["normalized_activity_distance"],
                same_per_condition_exposure=relation["same_per_condition_exposure"],
            )
        )
    feedback["planning_comparisons"] = comparisons
    feedback["previous_batch_diversity"] = dict(
        pairs=pairs,
        mean_activity_distance=statistics.mean(p["distance"] for p in pairs)
        if pairs
        else None,
        same_exposure_pairs=sum(p["same_per_condition_exposure"] for p in pairs),
    )
    feedback["feedback_version"] = "paired-history-v1"
    # Segments fully specify a schedule. Remove redundant per-update copies
    # and seed vectors only from the prompt view, never from saved history.
    for item in feedback["previous_strategies"]:
        item["path"] = {k: v for k, v in item["path"].items() if k != "expanded"}
        item["metrics"] = {
            k: v for k, v in item["metrics"].items() if k != "seed_scores"
        }
    if feedback["history_best"]:
        view = dict(feedback["history_best"])
        view["path"] = {k: v for k, v in view["path"].items() if k != "expanded"}
        view["summary"] = {
            k: v for k, v in view["summary"].items() if k != "seed_scores"
        }
        feedback["history_best"] = view
