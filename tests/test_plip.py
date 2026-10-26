"""Serialization validation must distinguish representation from graph changes."""

import numpy as np
from rdkit import Chem

from decompcond.plip import roundtrip_heavy_view


def test_stereo_defining_hydrogen_does_not_create_false_graph_change():
    explicit = Chem.MolFromSmiles("[H]/N=C(\\N)C(O)Cc1cc(C)c(F)c(F)c1CO")
    expected = Chem.MolFromSmiles("Cc1cc(CC(O)C(=N)N)c(CO)c(F)c1F")
    assert explicit.GetNumAtoms() == expected.GetNumAtoms() + 1
    conf = Chem.Conformer(explicit.GetNumAtoms())
    for i in range(explicit.GetNumAtoms()):
        conf.SetAtomPosition(i, (i, i * 0.1, i * 0.2))
    explicit.AddConformer(conf)
    before = Chem.MolToMolBlock(explicit)
    view = roundtrip_heavy_view(explicit)
    assert Chem.MolToSmiles(view, isomericSmiles=False) == Chem.MolToSmiles(
        expected, isomericSmiles=False
    )
    assert np.array_equal(
        view.GetConformer().GetPositions(), explicit.GetConformer().GetPositions()[1:]
    )
    assert Chem.MolToMolBlock(explicit) == before


def test_normalization_keeps_bond_and_charge_differences():
    molecules = [Chem.MolFromSmiles(s) for s in ("NC(=N)C", "NC(N)C", "NC(=[NH2+])C")]
    normalized = {
        Chem.MolToSmiles(roundtrip_heavy_view(m), isomericSmiles=False)
        for m in molecules
    }
    assert len(normalized) == 3
