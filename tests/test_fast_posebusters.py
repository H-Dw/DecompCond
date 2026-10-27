import numpy as np
import pytest

Chem = pytest.importorskip("rdkit.Chem")
pytest.importorskip("posebusters")
from decompcond.fast_posebusters import delete_atoms_batch, make_posebusters
from posebusters.tools.molecules import delete_atoms
from posebusters.modules.volume_overlap import check_volume_overlap


@pytest.mark.parametrize(
    "smiles,indices",
    [
        ("C[C@H](F)CC=CCO", [0, 3, 6]),
        ("c1ccccc1CO", [0, 1, 6]),
        ("CCCC", [0, 1, 2, 3]),
        ("CCCC", []),
        ("CC.O", [2]),
    ],
)
def test_batch_deletion_preserves_graph_properties_and_coordinates(smiles, indices):
    mol = Chem.MolFromSmiles(smiles)
    conf = Chem.Conformer(mol.GetNumAtoms())
    for i in range(mol.GetNumAtoms()):
        conf.SetAtomPosition(i, (i * 0.7, i * 0.3, i * 0.2))
        mol.GetAtomWithIdx(i).SetIntProp("original", i)
    mol.AddConformer(conf)
    a, b = delete_atoms(mol, indices), delete_atoms_batch(mol, indices)
    assert Chem.MolToSmiles(a) == Chem.MolToSmiles(b)
    assert np.array_equal(
        a.GetConformer().GetPositions(), b.GetConformer().GetPositions()
    )
    assert [x.GetIntProp("original") for x in a.GetAtoms()] == [
        x.GetIntProp("original") for x in b.GetAtoms()
    ]


def test_all_volume_categories_and_cache_match_original():
    ligand = Chem.MolFromSmiles("CCO")
    conf = Chem.Conformer(3)
    for i in range(3):
        conf.SetAtomPosition(i, (i * 1.4, 0, 0))
    ligand.AddConformer(conf)
    receptor = Chem.MolFromSmiles("CC.O.[Na+].CC")
    conf = Chem.Conformer(receptor.GetNumAtoms())
    labels = ["ALA", "ALA", "HOH", "NA", "COF", "COF"]
    for i, atom in enumerate(receptor.GetAtoms()):
        info = Chem.AtomPDBResidueInfo()
        info.SetResidueName(labels[i])
        info.SetResidueNumber(i + 1)
        info.SetIsHeteroAtom(labels[i] != "ALA")
        atom.SetMonomerInfo(info)
        conf.SetAtomPosition(i, (i * 1.3, 1.0, 0))
    receptor.AddConformer(conf)
    fast = make_posebusters({"modules": []})
    all_types = {
        "protein",
        "hydrogens",
        "waters",
        "organic_cofactors",
        "inorganic_cofactors",
    }
    for category in ["protein", "waters", "organic_cofactors", "inorganic_cofactors"]:
        params = dict(
            ignore_types=all_types - {category}, vdw_scale=0.8, clash_cutoff=0.075
        )
        for repeat in range(2):
            # The same cached receptor must be checked against a new pose.
            offset = 0.0 if repeat == 0 else 100.0
            for i in range(3):
                ligand.GetConformer().SetAtomPosition(i, (i * 1.4 + offset, 0, 0))
            old = check_volume_overlap(ligand, receptor, **params)["results"]
            new = fast.volume(ligand, receptor, **params)["results"]
            assert old["no_volume_clash"] == new["no_volume_clash"]
            assert old["volume_overlap"] == pytest.approx(
                new["volume_overlap"], nan_ok=True
            )
