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
