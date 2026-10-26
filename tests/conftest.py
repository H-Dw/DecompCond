from pathlib import Path
import pytest
from decompcond.config import load_config


@pytest.fixture
def config(tmp_path):
    c = load_config(Path(__file__).parents[1] / "configs/defaults.yaml")
    c.data.output_dir = str(tmp_path / "run")
    return c
