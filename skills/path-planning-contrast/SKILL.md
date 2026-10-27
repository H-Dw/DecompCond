---
name: path-planning-contrast
description: Plan DecompCond condition schedules from frozen sampling semantics, joint recovery and geometry outcomes, and duration-matched discovery contrasts.
---

Plan one complete batch for the current teacher using every supplied previous-round strategy and its discovery history. The model is frozen FLOWR.ROOT; MULTI describes sampling conventions. Your only controls are active condition IDs and complete update-index windows.

## Respect the executable sampling contract

- Read `sampling_contract` on every request. Use its grid, budgets and allowed windows, expressed as zero-based half-open update indices. Do not change weights, known times, condition facts, coordinates, seeds or evaluation rules.
- With `last_prediction`, the final active set enters the last clean prediction before the final update. The subsequent reference projection cannot repair that returned prediction; no extra terminal forward exists. With `extra_prediction`, consult the supplied readout time and final active mask instead of transferring a terminal-window explanation across protocols.
- Elements and XYZ activate, release and re-enter together; selected bonds use their explicit mask. Release stops overwriting while preserving state and unprojected self-conditioning. It does not reset noise or erase conditioning.

## Read success and failure together

- Optimize the declared all-attempt effective recovery score; mean affinity change on structure-qualified candidates remains the tie-break. Use `success_decomposition` to distinguish geometry rejection from incomplete recovery among qualified candidates. Neither raw exact recovery nor structure-pass count alone is success: inspect their measured intersection. A missing legacy intersection cannot be reconstructed by taking the smaller marginal count.
- Compare module atom and bond hit counts with their denominators; zero selected bonds is not perfect bond recovery. Audit omitted or persistently weak modules, and test a focused recovery mechanism when feasible. Explain deliberate omission and its trade-off rather than silently abandoning a difficult module.
- Read geometry failures, coordinate displacement, re-entry displacement, strain and sampled PLIP together with recovery. Restoration jumps are diagnostics, not new acceptance thresholds. Report affinity losses and PLIP sample sizes; missing measurements are not zeros. Injected labels never establish recovery.

## Choose informative contrasts

- Read `search_progress` before extending a successful ancestor. A primary-score plateau can coexist with affinity tie-break gains. It is evidence to reconsider the mechanism or branch, not permission to stop early or repeat an evaluated schedule.
- Use `matched_history_contrasts` to identify earlier schedules with the same per-condition active duration. Inspect the actual changed windows and paired seed outcomes. The supplied nearest match is a descriptive comparison, not causal proof or a prescribed parent; empty duration, coactivation and re-entry may still differ.
- When a batch has concentrated on small terminal changes without a primary gain, consider a different successful ancestor, an earlier release/re-entry window, or a different coactivation mechanism. Within the configured batch, preserve useful exploitation while allocating informative contrasts when feasible. No fixed role counts, mandatory windows, diversity threshold or atom-specific template are imposed.
- Compare competing terminal mechanisms from evidence: retaining or re-entering a module may improve raw recovery, while earlier release may permit geometric adjustment. Match marginal active durations when testing order, or explicitly acknowledge that duration changed too. Neither a fully active nor an empty tail guarantees structural validity.
- Use paired discovery comparisons with their sample sizes and losses as well as gains. Adaptive search statistics do not establish generalization. Confirmation results are excluded; histories and baselines are isolated by teacher.

Return the required paths JSON only. In each existing observation/change/hypothesis field, cite actual evidence, identify changed windows or combinations, and state a falsifiable expectation for recovery and geometry. Do not claim an unexecuted improvement. The validator enforces grid coverage, condition IDs and expanded-schedule uniqueness.
