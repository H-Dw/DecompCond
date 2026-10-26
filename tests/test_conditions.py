import numpy as np
from rdkit import Chem
from decompcond.conditions import compile_conditions, slot_map


def fixture():
    m = Chem.MolFromSmiles("CC(=O)NCC")
    c = Chem.Conformer(m.GetNumAtoms())
    for i in range(m.GetNumAtoms()):
        c.SetAtomPosition(i, (float(i), 0.0, 0.0))
    m.AddConformer(c)
    return m


def test_exact_selection_and_reference_coordinates():
    m = fixture()
    before = np.array(m.GetConformer().GetPositions())
    lib = compile_conditions(m, [2, 3, 4], "selected_connectivity", [])
    assert set(lib["atoms"]) == {"3", "4", "5"}
    assert list(lib["bonds"]) == ["B:4:5"]  # carbonyl carbon not selected
    assert len(lib["groups"]) == 2
    assert lib["atoms"]["3"]["xyz"] == before[2].tolist()
    assert np.array_equal(before, m.GetConformer().GetPositions())


def test_brics_projection_preserves_selected_fact_union():
    m = fixture()
    a = compile_conditions(m, [1, 2, 3, 4], "selected_connectivity", [])
    b = compile_conditions(m, [1, 2, 3, 4], "brics_projection", [])
    assert a["atoms"] == b["atoms"] and a["bonds"] == b["bonds"]
    assert slot_map(a, 4) == {2: 0, 3: 1, 4: 2, 5: 3}
