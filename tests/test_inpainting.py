import pytest

torch = pytest.importorskip("torch")
from decompcond.inpainting import project, network_times


def test_projection_only_touches_explicit_fields_and_preserves_raw():
    s = dict(
        coords=torch.randn(2, 3, 3),
        atomics=torch.rand(2, 3, 4),
        bonds=torch.rand(2, 3, 3, 5),
        charges=torch.rand(2, 3, 3),
    )
    original = {k: v.clone() for k, v in s.items()}
    am = torch.tensor([[True, False, False], [False, False, False]])
    bm = torch.zeros(2, 3, 3, dtype=torch.bool)
    bm[0, 0, 1] = bm[0, 1, 0] = True
    payload = dict(
        atom_mask=am,
        bond_mask=bm,
        coords=torch.zeros_like(s["coords"]),
        atomics=torch.zeros_like(s["atomics"]),
        bonds=torch.zeros_like(s["bonds"]),
    )
    result = project(s, payload)
    assert torch.equal(result["coords"][~am], s["coords"][~am])
    assert torch.equal(result["bonds"][~bm], s["bonds"][~bm])
    assert torch.equal(result["charges"], s["charges"])
    assert all(torch.equal(s[k], original[k]) for k in s)
    # Release: no projection after the model moves a formerly fixed atom.
    result["coords"][0, 0] = 3
    payload["atom_mask"] = torch.zeros_like(am)
    payload["bond_mask"] = torch.zeros_like(bm)
    assert torch.equal(project(result, payload)["coords"], result["coords"])


def test_known_times_do_not_mutate_global_times_or_squeeze_batch():
    t = [torch.tensor([0.4]) for _ in range(3)]
    local = network_times(t, torch.ones(1, 2), torch.tensor([[True, False]]), 1.0)
    assert local[0].shape == (1, 2, 1)
    assert local[0][0, 0, 0] == 1 and local[0][0, 1, 0] == t[0][0]
    assert t[0][0] == torch.tensor(0.4)
