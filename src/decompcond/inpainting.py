"""Copy-on-write joint projection. This module never overwrites model predictions."""

from __future__ import annotations
import torch


def encode_payload(
    library, slots, active_rows, n, vocab, device, center, scale, bond_classes
):
    batch = len(active_rows)
    am = torch.zeros((batch, n), dtype=torch.bool, device=device)
    bm = torch.zeros((batch, n, n), dtype=torch.bool, device=device)
    xyz = torch.zeros((batch, n, 3), device=device)
    z = torch.zeros((batch, n, vocab.size), device=device)
    b = torch.zeros((batch, n, n, bond_classes), device=device)
    for row, active in enumerate(active_rows):
        atom_ids = {i for g in active for i in library["groups"][g]["atom_ids"]}
        edge_ids = {i for g in active for i in library["groups"][g]["bond_ids"]}
        for atom_id in atom_ids:
            atom = library["atoms"][str(atom_id)]
            slot = slots[atom_id]
            am[row, slot] = True
            xyz[row, slot] = (
                torch.tensor(atom["xyz"], device=device) - center[row]
            ) / scale
            z[row, slot, vocab.indices_from_tokens([atom["element"]])[0]] = 1
        for key in edge_ids:
            edge = library["bonds"][key]
            i, j = [slots[x] for x in edge["ids"]]
            bm[row, i, j] = bm[row, j, i] = True
            b[row, i, j, edge["encoded"]] = b[row, j, i, edge["encoded"]] = 1
    return dict(atom_mask=am, bond_mask=bm, coords=xyz, atomics=z, bonds=b)


def project(state, payload):
    result = dict(state)
    for field, mask in [
        ("coords", payload["atom_mask"]),
        ("atomics", payload["atom_mask"]),
        ("bonds", payload["bond_mask"]),
    ]:
        result[field] = torch.where(
            mask.unsqueeze(-1), payload[field].to(state[field].dtype), state[field]
        )
    result["fragment_mask"] = payload["atom_mask"].clone()
    return result


def network_times(times, mask, active, known_time):
    continuous = times[0].reshape(-1, 1, 1).expand(-1, mask.shape[1], 1)
    discrete = times[1].reshape(-1, 1, 1).expand_as(continuous)
    if active is None:
        return [continuous, discrete]
    active = active.bool() & mask.bool()
    return [
        torch.where(active.unsqueeze(-1), torch.full_like(t, known_time), t)
        for t in [continuous, discrete]
    ]


def finite_state(state):
    return all(
        torch.isfinite(v).all().item()
        for v in state.values()
        if torch.is_tensor(v) and v.is_floating_point()
    )


def row(state, index):
    return {k: v[index : index + 1] for k, v in state.items() if torch.is_tensor(v)}


def combine(states):
    return {
        key: torch.cat([s[key] for s in states], dim=0)
        for key in states[0]
        if torch.is_tensor(states[0][key])
    }
