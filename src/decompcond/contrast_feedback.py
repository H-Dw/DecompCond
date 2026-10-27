"""Discovery-only success decomposition and duration-matched timing contrasts."""

from .planning_evidence import paired_returns, schedule_relation


def enrich_contrast_feedback(feedback, history, library):
    rounds = history["rounds"]
    records = [history["baseline"]] + [r for rr in rounds for r in rr["records"]]
    best = history["baseline"]["summary"]["J"]
    last_improved = 0
    progress = []
    for rr in rounds:
        value = max(r["summary"]["J"] for r in rr["records"])
        if value > best:
            best, last_improved = value, rr["round"]
        progress.append(dict(round=rr["round"], batch_best_primary=value, best_primary_so_far=best))
    feedback["search_progress"] = dict(
        completed_rounds=len(rounds),
        best_primary_score=best,
        last_strict_primary_improvement_round=last_improved,
        rounds_since_strict_primary_improvement=len(rounds) - last_improved,
        rounds=progress,
        interpretation="Descriptive primary-score history including baseline; affinity tie-break gains may occur without a strict primary gain. No early stopping is implied.",
    )
    decompositions = {}
    for record in records:
        s = record["summary"]
        evidence_id = f"metrics:{record['path']['id']}"
        # Older summaries cannot identify the intersection from marginal counts.
        # Keep it missing rather than treating min(pass, exact) as the answer.
        joint = s.get("qualified_exact_recovery_count")
        conditional = s.get("qualified_recovery")
        feedback["evidence"][evidence_id].update(
            qualified_exact_recovery_count=joint, qualified_recovery=conditional,
        )
        decompositions[record["path"]["id"]] = dict(
            attempts=s["attempts"], structure_pass_count=s["structure_pass_count"],
            structure_pass_fraction=s["structure_pass_count"] / s["attempts"],
            mean_recovery_given_structure_pass=conditional["mean"] if conditional else None,
            qualified_exact_recovery_count=joint,
            availability="measured" if joint is not None and conditional is not None else "unavailable_in_legacy_summary",
        )
    feedback["success_decomposition"] = decompositions
    feedback["success_semantics"] = (
        "exact_recovery_count includes structurally rejected candidates; qualified_exact_recovery_count is the measured intersection. "
        "The primary score equals structure_pass_fraction times mean_recovery_given_structure_pass when any candidate passes; "
        "when none pass the primary score is zero and the conditional mean is undefined. "
        "These are diagnostics, not a new ranking rule or acceptance threshold."
    )
    previous = rounds[-1]["records"] if rounds else [history["baseline"]]
    matches = {}
    for record in previous:
        pid = record["path"]["id"]
        eligible = []
        dose = record["summary"]["exposure"]["active_steps"]
        for ref in records:
            if ref["path"]["id"] == pid or ref["summary"]["exposure"]["active_steps"] != dose:
                continue
            relation = schedule_relation(record, ref, library["groups"])
            eligible.append((relation["normalized_activity_distance"], ref["path"]["id"], ref, relation))
        if not eligible:
            matches[pid] = dict(status="no_equal_exposure_reference", eligible_references=0)
            continue
        _, ref_id, ref, relation = min(eligible, key=lambda row: row[:2])
        matches[pid] = dict(
            status="nearest_equal_exposure_discovery_reference",
            eligible_references=len(eligible), reference_id=ref_id,
            evidence_id=f"metrics:{ref_id}", paired=paired_returns(record, ref), schedule=relation,
        )
    feedback["matched_history_contrasts"] = matches
    feedback["feedback_version"] = "contrast-history-v2"
