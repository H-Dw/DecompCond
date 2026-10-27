"""All-attempt summaries, evidence references, and lexicographic path selection."""

from __future__ import annotations
from collections import Counter
import statistics
import math
from .io import TechnicalFailure
from .paths import exposure


def distribution(values):
    values = [float(x) for x in values if x is not None]
    if not values:
        return dict(n=0, mean=None, std=None, min=None, max=None)
    return dict(
        n=len(values),
        mean=statistics.mean(values),
        std=statistics.stdev(values) if len(values) > 1 else 0.0,
        min=min(values),
        max=max(values),
    )


def reentry_steps(path):
    previous, seen, steps = set(), set(), set()
    for index, active in enumerate(path["expanded"]):
        current = set(active)
        if (current - previous) & seen:
            steps.add(index)
        seen.update(current)
        previous = current
    return steps


def summarize(path, attempts, library):
    if not attempts or any(
        a.get("status") not in ("evaluated", "chemical_invalid", "generation_failure")
        for a in attempts
    ):
        raise TechnicalFailure(
            "cannot summarize an incomplete or technically failed path"
        )
    if any(
        not math.isfinite(a["effective_recovery"])
        or not 0 <= a["effective_recovery"] <= 1
        for a in attempts
    ):
        raise TechnicalFailure(
            "invalid recovery score; do not conceal a missing metric"
        )
    valid = [a for a in attempts if a["structure_pass"]]
    if any(
        a.get("delta_pic50") is None or not math.isfinite(a["delta_pic50"])
        for a in valid
    ):
        raise TechnicalFailure("qualified attempt is missing required affinity")
    reentries = reentry_steps(path)
    result = dict(
        path_id=path["id"],
        schedule_hash=path["schedule_hash"],
        attempts=len(attempts),
        outcomes=dict(Counter(a["status"] for a in attempts)),
        recovery_statuses=dict(
            Counter(a.get("recovery_status", "measured") for a in attempts)
        ),
        structure_pass_count=len(valid),
        exact_recovery_count=sum(bool(a.get("exact_recovery")) for a in attempts),
        J=statistics.mean(a["effective_recovery"] for a in attempts),
        valid_delta_pic50=distribution([a["delta_pic50"] for a in valid]),
        distributions={
            key: distribution([a.get(key) for a in attempts])
            for key in [
                "atom_recovery",
                "bond_recovery",
                "recovery",
                "selected_rmsd_A",
                "selected_max_displacement_A",
            ]
        },
        exposure=exposure(path, list(library["groups"])),
        condition_metrics={},
        pb_failures=dict(
            Counter(
                key
                for a in attempts
                for key, value in a.get("pb_checks", {}).items()
                if not value
            )
        ),
        strain=distribution(
            [a["strain"].get("strain_kcal_mol") for a in attempts if a.get("strain")]
        ),
        strain_statuses=dict(
            Counter(a["strain"]["status"] for a in attempts if a.get("strain"))
        ),
        plip_recall=distribution(
            [a["plip"]["recall"] for a in attempts if a.get("plip")]
        ),
        plip_sampling=dict(
            selected_attempts=sum(
                a.get("diagnostics", {}).get("plip_selected", bool(a.get("plip")))
                for a in attempts
                if a.get("diagnostics") is not None
            ),
            measured_attempts=sum(a.get("plip") is not None for a in attempts),
            statuses=dict(
                Counter(a.get("plip_status", "legacy_full_policy") for a in attempts)
            ),
            rules=sorted(
                {
                    (a.get("diagnostics") or {}).get("rule", "legacy_full")
                    for a in attempts
                }
            ),
            interpretation="Recall distribution is conditional on sampled, chemically evaluable attempts; missing values are not zero",
        ),
        evaluation_protocols=dict(
            Counter(a.get("evaluation_protocol", "full-v1") for a in attempts)
        ),
        affinity_statuses=dict(
            Counter(a.get("pic50_status", "legacy") for a in attempts)
        ),
        reentry_displacement_A=distribution(
            [
                t["pre_projection_max_A"]
                for a in attempts
                for t in a.get("trace", [])
                if t["step"] in reentries
            ]
        ),
    )
    for group in library["groups"]:
        rows = [
            a["condition_metrics"][group]
            for a in attempts
            if group in a.get("condition_metrics", {})
        ]
        result["condition_metrics"][group] = {
            key: sum(r[key] for r in rows)
            for key in ["atom_hits", "atom_count", "bond_hits", "bond_count"]
        }
        result["condition_metrics"][group]["reported_attempts"] = len(rows)
        result["condition_metrics"][group]["unavailable_attempts"] = len(
            attempts
        ) - len(rows)
    return result


def best_record(records):
    eligible = [
        r
        for r in records
        if r["summary"]["structure_pass_count"] > 0
        and r["summary"]["valid_delta_pic50"]["mean"] is not None
    ]
    return (
        max(
            eligible,
            key=lambda r: (
                r["summary"]["J"],
                r["summary"]["valid_delta_pic50"]["mean"],
            ),
        )
        if eligible
        else None
    )


def build_feedback(history, library, round_number):
    base = history["baseline"]
    rounds = history["rounds"]
    previous = rounds[-1]["records"] if rounds else [base]
    all_records = [base] + [record for r in rounds for record in r["records"]]
    winner = best_record([r for rr in rounds for r in rr["records"]])
    # Complete previous-round metrics remain above; older evidence is compact.
    evidence = {
        f"metrics:{r['path']['id']}": {
            k: r["summary"][k]
            for k in (
                "attempts",
                "outcomes",
                "structure_pass_count",
                "exact_recovery_count",
                "J",
                "valid_delta_pic50",
                "pb_failures",
            )
        }
        for r in all_records
    }
    trend = [
        dict(
            round=r["round"],
            strategies=[
                dict(
                    id=x["path"]["id"],
                    J=x["summary"]["J"],
                    valid_delta_pic50=x["summary"]["valid_delta_pic50"],
                )
                for x in r["records"]
            ],
        )
        for r in rounds
    ]
    references = {"baseline": base}
    if winner:
        references["history_best"] = winner
    older = rounds[-2]["records"] if len(rounds) > 1 else [base]
    previous_best = best_record(older)
    if previous_best:
        references["preceding_round_best"] = previous_best
    comparisons = {
        r["path"]["id"]: {
            name: dict(
                reference_id=ref["path"]["id"],
                delta_J=r["summary"]["J"] - ref["summary"]["J"],
                delta_structure_pass_rate=r["summary"]["structure_pass_count"]
                / r["summary"]["attempts"]
                - ref["summary"]["structure_pass_count"] / ref["summary"]["attempts"],
            )
            for name, ref in references.items()
        }
        for r in previous
    }
    return dict(
        round=round_number,
        previous_round=rounds[-1]["round"] if rounds else 0,
        previous_strategies=[
            dict(
                path=r["path"],
                metrics=r["summary"],
                evidence_id=f"metrics:{r['path']['id']}",
            )
            for r in previous
        ],
        baseline=base["summary"],
        history_best=winner,
        history_trend=trend,
        relative_changes=comparisons,
        evaluated_paths=[
            dict(
                id=r["path"]["id"],
                segments=r["path"]["segments"],
                schedule_hash=r["path"]["schedule_hash"],
            )
            for r in all_records
        ],
        evidence_ids=["condition_library"] + list(evidence),
        evidence=evidence,
        scope="Discovery only; confirmation outcomes are never included",
        diagnostic_guidance=(
            "PLIP may use a preregistered seed-stratified sample. Compare measured n and selection rules, "
            "not maxima; unselected/chemically invalid values are missing, not zero. "
            "Imported full-v1 rounds used full PLIP. Structure-failed affinity may be skipped by policy. "
            "Acceptance and J remain unchanged; do not infer improvement from a sampling-policy change."
        ),
    )
