from pathlib import Path
import pytest
from decompcond.config import load_config


def test_default_budget_and_grid():
    c = load_config(Path(__file__).parents[1] / "configs/defaults.yaml")
    assert (
        c.search.rounds,
        c.search.paths_per_round,
        c.search.candidates_per_path,
    ) == (10, 5, 20)
    assert (c.sampling.steps, c.sampling.segment_multiple) == (100, 10)
    assert (
        len(c.data.teachers)
        * (
            c.search.baseline_candidates
            + c.search.rounds * c.search.paths_per_round * c.search.candidates_per_path
            + 2 * c.search.confirmation_candidates
        )
        == 2120
    )


@pytest.mark.parametrize(
    "change",
    [
        "sampling.confirmation_seed_start=1000",
        "sampling.segment_multiple=30",
        "search.candidates_per_path=21",
        "unknown=1",
    ],
)
def test_incoherent_config_rejected(change):
    with pytest.raises(ValueError):
        load_config(Path(__file__).parents[1] / "configs/defaults.yaml", [change])


def test_multi_style_config_changes_sampling_not_model_or_evaluation():
    root = Path(__file__).parents[1] / "configs"
    original = load_config(root / "research-cycle01.yaml")
    aligned = load_config(root / "multi-style.yaml")
    assert aligned.sampling.known_time == aligned.sampling.integration_end_time == 0.999
    assert aligned.sampling.readout == "last_prediction"
    assert aligned.sampling.final_epsilon is None
    assert aligned.sampling.initial_self_condition_coords == "zeros"
    assert original.sampling.readout == "extra_prediction"
    assert original.sampling.integration_end_time == 1.0
    assert original.backend == aligned.backend
    assert original.evaluation == aligned.evaluation
    assert original.search == aligned.search
    assert original.data.output_dir != aligned.data.output_dir
    assert original.fingerprint() != aligned.fingerprint()


@pytest.mark.parametrize("changes", [
    ["sampling.integration_end_time=0"],
    ["sampling.integration_end_time=1.01"],
    ["sampling.readout=extra_prediction", "sampling.final_epsilon=null"],
    ["sampling.integration_end_time=0.1", "sampling.final_epsilon=0.2"],
    ["sampling.readout=last_prediction"],
    ["sampling.readout=projected_state"],
])
def test_ambiguous_or_invalid_readout_rejected(changes):
    with pytest.raises(ValueError):
        load_config(Path(__file__).parents[1] / "configs/defaults.yaml", changes)
