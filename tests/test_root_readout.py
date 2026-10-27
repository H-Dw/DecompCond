"""Exercise the actual ROOT adapter loop without loading model weights or CUDA."""

from types import SimpleNamespace
import numpy as np
import pytest

torch = pytest.importorskip("torch")
from decompcond.backends.root import RootBackend
from decompcond.inpainting import network_times


def adapter(config, mutate_prediction=False, fail_final_step=False):
    backend = RootBackend.__new__(RootBackend)
    backend.config, backend.device = config, torch.device("cpu")
    backend.vocab = SimpleNamespace(size=2, indices_from_tokens=lambda tokens: [0] * len(tokens))
    calls, steps = [], []

    class Model:
        sc_charges = False
        sc_recycle_steps = 1
        coord_scale = 1.0
        gen = SimpleNamespace(get_pocket_encoding=lambda *args, **kwargs: (None, None))

        def __call__(self, state, pocket, times, cond_batch, **kwargs):
            calls.append(dict(
                time=float(times[0][0]),
                local_times=network_times(times, state["mask"], state["fragment_mask"], config.sampling.known_time),
                input_coords=state["coords"].clone(),
                cache_coords=cond_batch["coords"].clone(),
            ))
            return {k: (v.clone() + 2 if k == "coords" else v.clone())
                    for k, v in state.items() if k != "fragment_mask"}

        def _get_predictions(self, output):
            return output, {k: output[k] for k in ("coords", "atomics", "bonds")}

        def _update_times(self, times, delta):
            return [t + delta for t in times]

    backend.model = Model()

    def step(state, prediction, prior, times, delta):
        steps.append((float(times[0][0]), float(delta)))
        result = {k: v.clone() for k, v in state.items()}
        result["coords"] += 0.25
        if mutate_prediction:
            prediction["coords"].fill_(777)
        if fail_final_step and len(steps) == config.sampling.steps:
            result["coords"].fill_(float("nan"))
        return result

    backend.model.integrator = SimpleNamespace(step=step)
    state = dict(coords=torch.full((1, 3, 3), 3.0), atomics=torch.ones(1, 3, 2),
                 bonds=torch.ones(1, 3, 3, 3), charges=torch.zeros(1, 3, 2), mask=torch.ones(1, 3))
    backend.seed_state = lambda seed, n: ({k: v.clone() for k, v in state.items()}, (torch.get_rng_state(), None))
    pocket = dict(coords=torch.zeros(1, 1, 3), atom_names=torch.zeros(1, 1),
                  charges=torch.zeros(1, 1, 2), bonds=torch.zeros(1, 1, 1, 2),
                  res_names=torch.zeros(1, 1), mask=torch.ones(1, 1),
                  complex=[SimpleNamespace(com=[0.0, 0.0, 0.0])])
    backend.pocket = lambda prepared, size: pocket
    backend.world = lambda raw, pocket: raw
    library = dict(reference_atom_count=3, hash="test-library",
                   atoms={"1": dict(element="C", xyz=[1.5, 0.0, 0.0])}, bonds={},
                   groups={"selected": dict(atom_ids=[1], bond_ids=[])})
    return backend, library, calls, steps


def run(config, tmp_path, **kwargs):
    backend, library, calls, steps = adapter(config, **kwargs)
    path = dict(expanded=[["selected"]] * config.sampling.steps, schedule_hash="fixed")
    result = backend.generate_batch({}, library, [dict(path=path, seed=7, folder=tmp_path)])[0]
    return result, calls, steps


def test_multi_style_returns_last_raw_prediction_with_exact_forward_budget(config, tmp_path):
    config.sampling.integration_end_time = 0.999
    config.sampling.known_time = 0.999
    config.sampling.readout = "last_prediction"
    config.sampling.final_epsilon = None
    config.sampling.initial_self_condition_coords = "zeros"
    result, calls, steps = run(config, tmp_path, mutate_prediction=True)
    assert len(calls) == len(steps) == result["nfe"] == result["updates"] == 100
    assert result["readout_global_time"] == pytest.approx(0.98901, abs=2e-6)
    assert sum(delta for _, delta in steps) == pytest.approx(0.999)
    assert torch.count_nonzero(calls[0]["cache_coords"]) == 0
    assert float(calls[-1]["local_times"][0][0, 0, 0]) == pytest.approx(0.999)
    assert float(calls[-1]["local_times"][0][0, 1, 0]) == pytest.approx(calls[-1]["time"])
    assert steps[-1][0] == pytest.approx(calls[-1]["time"])
    with np.load(result["raw_path"]) as raw:
        # Neither reference projection (1.5,0,0), final state, nor solver-mutated tensors.
        np.testing.assert_allclose(raw["coords"][0, 0], [3.5, 2, 2])


def test_legacy_extra_forward_is_preserved(config, tmp_path):
    result, calls, steps = run(config, tmp_path)
    assert len(calls) == result["nfe"] == 101
    assert len(steps) == 100
    assert calls[-1]["time"] == pytest.approx(0.9999, abs=2e-6)
    assert torch.all(calls[0]["cache_coords"] == 3)
    assert result["readout"] == "extra_prediction"


def test_last_prediction_does_not_hide_failed_final_update(config, tmp_path):
    config.sampling.readout = "last_prediction"
    config.sampling.final_epsilon = None
    result, calls, steps = run(config, tmp_path, fail_final_step=True)
    assert result["status"] == "generation_failure"
    assert result["reason"] == "nonfinite_integrated_state"
    assert result["step"] == 99
    assert len(calls) == len(steps) == 100
    assert not (tmp_path / "raw.npz").exists()
