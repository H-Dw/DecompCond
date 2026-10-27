import copy
import runpy
from pathlib import Path
import pytest

module = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/fork_evaluation_upgrade.py")
)
validate_change = module["validate_change"]


def test_upgrade_allows_only_explicit_policy_and_location(config):
    old = config.model_dump()
    new = copy.deepcopy(old)
    new["data"]["output_dir"] = "new-version"
    new["evaluation"].update(
        workers=4,
        fast_volume_overlap=True,
        score_only_structure_pass=True,
        plip_sampling="seed_strata_v1",
    )
    validate_change(old, new)
    for key, value in [
        ("connectivity_factor", 1.5),
        ("atom_weight", 0.6),
        ("pb_config", "different.yaml"),
    ]:
        altered = copy.deepcopy(new)
        altered["evaluation"][key] = value
        with pytest.raises(ValueError):
            validate_change(old, altered)
    changed_model = copy.deepcopy(new)
    changed_model["backend"]["affinity_time"] = 0.5
    with pytest.raises(ValueError):
        validate_change(old, changed_model)


def test_legacy_manifest_without_policy_fields_is_supported(config):
    new = config.model_dump()
    old = copy.deepcopy(new)
    for key in module["POLICY_FIELDS"]:
        old["evaluation"].pop(key)
    validate_change(old, new)
