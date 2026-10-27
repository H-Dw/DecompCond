---
name: path-planning-multi
description: Plan time-varying DecompCond conditions using an explicit frozen ROOT sampling and readout contract with MULTI-style defaults.
---

Use every supplied previous-round strategy and the current teacher's discovery history to propose one complete batch of schedules. The model is frozen FLOWR.ROOT. MULTI describes sampling conventions, not different model weights. This guide changes planning only.

## Read the sampling contract before proposing windows

- Read `sampling_contract` in every request. Its values are generated from the executable configuration and override illustrative defaults in this guide. Never choose or edit the known-atom time, global time grid, readout, self-conditioning initialization, weights, condition facts, budgets or evaluation rules. Your only controls are active condition IDs and complete update-index windows.
- Express windows using zero-based half-open update indices, not fractional flow times. Use `planning_windows` to relate each allowed block to global prediction times. In a 100-update MULTI-style run, `[90,100)` includes the predictions for updates 90 through 99; it does not create a prediction at index 100.
- For `last_prediction`, the returned output is the clean prediction made before the last native update. That last update still executes, but its subsequent reference projection cannot repair the returned prediction. There is no extra terminal forward pass. The final active set is supplied before that final prediction, so terminal activation does affect it; do not confuse "no terminal overwrite" with "no terminal conditioning".
- For `extra_prediction`, the last projected state and its active mask enter an additional prediction. Consult the supplied readout time rather than assuming this branch is active. Do not transfer a successful terminal-window explanation across readout modes without new evidence.
- Elements and XYZ activate, release and re-enter together; selected existing bonds use their explicit mask. Release stops overwriting but preserves the current state and the unprojected self-conditioning cache. Do not assume release resets noise or erases past conditioning.

## Plan complementary experiments from evidence

- Inspect every previous strategy, the static baseline and historical best. Recover older subcondition counts and exposure from evidence before citing them. Distinguish recovery conditional on chemically evaluable molecules from effective recovery averaged over every planned attempt.
- Optimize the declared all-attempt effective recovery score. Use the mean affinity change of structure-qualified candidates only as the tie-break. Report affinity loss candidly. PLIP is a sampled diagnostic: include measured sample counts and do not turn missing values into zeros.
- Examine at least two competing terminal mechanisms when supported by the evidence: retaining/re-entering a weak module may aid its last raw prediction, while releasing it earlier may let the surrounding geometry adjust. Neither terminal clamping nor an empty tail guarantees a valid structure. State expected changes in element/bond recovery, geometry and restoration displacement as testable hypotheses.
- Do not force every proposed path to end with all conditions or with no conditions. Diversify the configured batch using terminal-window variants, an earlier-window comparison, a distinct successful ancestor or a different coactivation pattern. These are possible experimental roles, not hard-coded templates or extra paths.
- Include an exposure-matched timing comparison when the grid and budget permit. Changing terminal activation usually changes exposure too; compensate elsewhere if isolating timing, and report any remaining differences in coactivation, empty duration and re-entry. Equal exposure alone does not perfectly isolate order.
- Use paired discovery seeds to examine consistency. Adaptive selection and a small standard error do not establish improvement on fresh seeds. Old sampler results are method-comparison evidence, not measurements from this run. Independent confirmation must stay outside planning feedback.
- Diagnose isolated atoms separately from bonded modules. A restoration jump suggests tension but is not a new acceptance threshold. Read raw recovery and three-dimensional audits; never count injected labels as successful recovery.
- Each path uses the existing observation/change/hypothesis fields to state the observed issue, changed windows, evidence references and a falsifiable expectation. Output only the required paths JSON. The validator enforces full coverage, configured granularity, permitted IDs and duplicate rules.

Each teacher has its own baseline, history and best path. Do not assume a timing policy discovered for one teacher transfers to the other. No fixed atom IDs, seeds, thresholds or mandatory schedules are introduced here.
