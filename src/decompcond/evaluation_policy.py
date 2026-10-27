"""Outcome-independent, paired seed strata for optional PLIP diagnostics."""

from .io import digest


def diagnostic_plan(config, teacher_id, stage_id, seeds):
    seeds = sorted(seeds)
    c = config.evaluation
    full = c.plip_sampling == "full" or not stage_id.startswith("round_")
    count = len(seeds) if full else min(c.plip_samples_per_path, len(seeds))
    strata = [
        seeds[i * len(seeds) // count : (i + 1) * len(seeds) // count]
        for i in range(count)
    ]
    selected = []
    for index, group in enumerate(strata):
        key = digest(
            dict(
                salt=c.plip_sampling_salt,
                teacher=teacher_id,
                stratum=index,
                seeds=group,
            )
        )
        selected.append(group[int(key, 16) % len(group)])
    return dict(
        protocol=c.protocol_version,
        rule="full" if full else c.plip_sampling,
        selected_seeds=selected,
        planned_attempts=len(seeds),
        planned_plip_attempts=count,
        strata=strata,
        pairing="same seed selection for every path and round of a teacher",
        selection_uses_outcomes=False,
    )
