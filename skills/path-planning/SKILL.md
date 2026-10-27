---
name: path-planning
description: Plan DecompCond inference schedules from complete discovery feedback, separating recovery bottlenecks, condition exposure and temporal order.
---

Use the supplied metric evidence to plan a complementary batch of complete schedules. The generator, condition library, evaluation policy and ranking are frozen; this skill changes planning only.

- Read every previous strategy and compare its failures and J with the historical best. Retrieve older subcondition counts and exposure from `evidence` before citing them; do not invent missing numerical evidence. Distinguish recovery conditional on chemically evaluable molecules from all-attempt effective recovery.
- Rank ideas around J; use qualified-candidate mean delta pIC50 only as the declared tie-break. Report affinity loss candidly. PLIP is sampled diagnostic evidence: inspect selected and measured n and avoid treating unmeasured attempts as zeros.
- Use paired same-seed differences to judge consistency across attempts. A small positive discovery difference or a low standard error after many adaptive proposals does not prove improvement on new seeds. Parent confirmation outcomes are not present in the runtime feedback.
- Keep useful local refinements, but do not spend every path on nearly identical edits to the same winner. Include a test of a distinct temporal pattern or a different successful ancestor when possible. Use actual schedules and exposure, not path names, to assess novelty.
- Include an exposure-matched temporal comparison when the budget and allowed grid permit: move or exchange windows while preserving each condition's active-step total. Label changes to coactivation, empty time or reentry counts; equal exposure alone does not isolate order perfectly. Do not call a longer injection an order effect.
- Use weak bond-bearing modules and weak isolated atoms as different hypotheses. Improve a bond bottleneck through timing/coactivation rather than assuming more terminal all-condition exposure helps. A restoration jump diagnoses tension but is not itself a new acceptance threshold.
- Explain each path's observed problem, changed windows and falsifiable expectation in the existing observation/change/hypothesis fields. Allocate different experimental roles across the configured batch without adding JSON fields. When a suggested comparison is infeasible, explain the limitation and choose another informative complete path.

The interface enforces the supplied grid, batch size and duplicate rules. This guide introduces no fixed molecular IDs, seed values, evaluation thresholds or mandatory schedule templates.
