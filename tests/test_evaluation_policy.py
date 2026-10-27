from decompcond.evaluation_policy import diagnostic_plan
from decompcond.evaluation import Evaluator


def test_seed_strata_are_predeclared_paired_and_confirmation_is_full(config):
    config.evaluation.plip_sampling = "seed_strata_v1"
    seeds = list(range(1000, 1020))
    first = diagnostic_plan(config, "teacher", "round_001", seeds)
    second = diagnostic_plan(config, "teacher", "round_010", list(reversed(seeds)))
    assert first == second
    assert len(first["selected_seeds"]) == 5
    assert all(
        sum(seed in group for seed in first["selected_seeds"]) == 1
        for group in first["strata"]
    )
    assert (
        diagnostic_plan(config, "teacher", "confirmation", seeds)["selected_seeds"]
        == seeds
    )
    assert (
        diagnostic_plan(config, "teacher", "baseline", seeds)["selected_seeds"] == seeds
    )
    assert (
        diagnostic_plan(config, "teacher", "round_001", seeds[:2])["selected_seeds"]
        == seeds[:2]
    )


def test_ineligible_affinity_never_calls_root_or_fabricates_zero(config):
    config.evaluation.score_only_structure_pass = True
    evaluator = Evaluator.__new__(Evaluator)
    evaluator.config = config

    class ForbiddenBackend:
        def score_molecule(self, *args):
            raise AssertionError("ROOT must not be called")

    evaluator.backend = ForbiddenBackend()
    report = evaluator.finish_affinity(
        dict(status="evaluated", structure_pass=False), None, None
    )
    assert report["pic50"] is None and report["delta_pic50"] is None
    assert report["pic50_status"] == "not_computed_by_policy_structure_failed"
