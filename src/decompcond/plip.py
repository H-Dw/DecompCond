"""PLIP-only profiling of an existing pose; no generative model imports or calls.
Derived from inspected conditional_eval/profiling.py functions.
The SDF graph is authoritative; PDB serialization must not change it.
"""

import hashlib
import logging
import json
from pathlib import Path
import numpy as np
from rdkit import Chem


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def graph(m):
    return {
        "elements": [a.GetSymbol() for a in m.GetAtoms()],
        "formal_charges": [a.GetFormalCharge() for a in m.GetAtoms()],
        "bonds": [
            [b.GetBeginAtomIdx(), b.GetEndAtomIdx(), str(b.GetBondType())]
            for b in m.GetBonds()
        ],
    }


def roundtrip_heavy_view(mol):
    """Normalize serialization-only H retention on a detached validation copy.

    RDKit may keep an explicit H defining C=N stereo when reading OpenBabel SDF.
    This audit already ignores stereochemistry; remove stereo on the copy before
    removing H so equivalent implicit/explicit H representations compare equally.
    The actual PLIP complex, donor hydrogens and all heavy coordinates are untouched.
    """
    view = Chem.Mol(mol)
    Chem.RemoveStereochemistry(view)
    view = Chem.RemoveHs(view)
    if any(a.GetAtomicNum() == 1 for a in view.GetAtoms()):
        raise ValueError("PLIP audit contains unsupported retained hydrogen")
    return view


def complex_pdb(mol, protein_file, destination, policy):
    """Keep all heavy coordinates; write explicit hydrogen/bond/charge information."""
    m = Chem.Mol(mol)
    original = np.array(m.GetConformer().GetPositions())
    h = Chem.AddHs(m, addCoords=True)
    assert np.array_equal(
        original, np.array(h.GetConformer().GetPositions())[: len(original)]
    )
    text = Path(protein_file).read_text()
    lines = [
        l
        for l in text.splitlines()
        if l.startswith(("ATOM  ", "HETATM", "TER   ", "CONECT"))
    ]
    atom_lines = [l for l in lines if l.startswith(("ATOM  ", "HETATM"))]
    offset = max(int(l[6:11]) for l in atom_lines)
    serial_to_native = {}
    for i, a in enumerate(h.GetAtoms()):
        info = Chem.AtomPDBResidueInfo()
        info.SetName((a.GetSymbol() + str(i + 1)).rjust(4))
        info.SetResidueName(policy.ligand_resname)
        info.SetResidueNumber(policy.ligand_resnum)
        info.SetChainId(policy.ligand_chain)
        info.SetIsHeteroAtom(True)
        info.SetOccupancy(1.0)
        a.SetMonomerInfo(info)
        if i < m.GetNumAtoms():
            serial_to_native[offset + i + 1] = i
    added = []
    for l in Chem.MolToPDBBlock(h).splitlines():
        if l.startswith(("ATOM  ", "HETATM")):
            serial = int(l[6:11]) + offset
            added.append(l[:6] + f"{serial:5d}" + l[11:])
        elif l.startswith("CONECT"):
            values = [
                int(l[i : i + 5]) + offset
                for i in range(6, len(l), 5)
                if l[i : i + 5].strip()
            ]
            added.append("CONECT" + "".join(f"{v:5d}" for v in values))
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text("\n".join(lines + added + ["END"]) + "\n")
    return serial_to_native, h


def plip_profile(mol, protein_file, out, policy):
    mol = Chem.Mol(mol)
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    from plip.basic import config
    from plip.structure.preparation import PDBComplex, LigandFinder
    from plip.exchange.report import StructureReport, BindingSiteReport
    from openbabel import openbabel as ob, pybel
    from scipy.optimize import linear_sum_assignment

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    config.NOHYDRO = policy.nohydro
    config.NOFIX = policy.nofix
    config.NOFIXFILE = True
    config.OUTPUTFOLDER = str(out)
    config.COMPRESS = False
    config.MAXTHREADS = policy.maxthreads
    for key, value in policy.thresholds.items():
        if not hasattr(config, key):
            raise ValueError("Unknown PLIP threshold " + key)
        setattr(config, key, value)
    logging.getLogger("plip").setLevel(logging.ERROR)
    serial_map, hydrogenated = complex_pdb(
        mol, protein_file, out / "complex.pdb", policy
    )
    c = PDBComplex()
    c.output_path = str(out) + "/"
    c.load_pdb(str(out / "complex.pdb"))
    # PDB does not unambiguously preserve bond orders. OpenBabel can infer extra
    # bonds for a distorted generated pose even with CONECT records. Restore the
    # authoritative SDF graph (and deterministic explicit H) without moving any
    # atom, then let standard PLIP chemistry/interaction rules analyze that graph.
    # This repairs serialization, never the generated molecule or its geometry.
    start_serial = min(serial_map)
    serials = {start_serial + i: i for i in range(hydrogenated.GetNumAtoms())}
    inverse = {v: k for k, v in c.Mapper.proteinmap.items()}
    global_ids = {i: inverse[s] for s, i in serials.items()}
    for serial, i in serials.items():
        atom = c.protcomplex.OBMol.GetAtom(global_ids[i])
        if atom.GetResidue().GetSerialNum(atom) != serial:
            raise ValueError("PLIP mapper/PDB serial disagreement")
    full_ids = set(global_ids.values())
    om = c.protcomplex.OBMol
    previous_bonds = [
        [bond.GetBeginAtomIdx(), bond.GetEndAtomIdx(), bond.GetBondOrder()]
        for bond in ob.OBMolBondIter(om)
        if bond.GetBeginAtomIdx() in full_ids or bond.GetEndAtomIdx() in full_ids
    ]
    for a, b, order in previous_bonds:
        om.DeleteBond(om.GetBond(a, b))
    kekule = Chem.Mol(hydrogenated)
    Chem.Kekulize(kekule, clearAromaticFlags=False)
    for i, a in enumerate(hydrogenated.GetAtoms()):
        atom = om.GetAtom(global_ids[i])
        atom.SetAtomicNum(a.GetAtomicNum())
        atom.SetFormalCharge(a.GetFormalCharge())
        atom.SetImplicitHCount(0)
        atom.SetSpinMultiplicity(0)
        atom.SetAromatic(a.GetIsAromatic())
    for bond in kekule.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        om.AddBond(global_ids[a], global_ids[b], int(bond.GetBondTypeAsDouble()))
        om.GetBond(global_ids[a], global_ids[b]).SetAromatic(
            hydrogenated.GetBondBetweenAtoms(a, b).GetIsAromatic()
        )
    om.SetAromaticPerceived(True)
    # Topology edits invalidate OpenBabel's chain-perception flag. Preserve the
    # explicit PDB residue labels instead of allowing it to recreate them.
    om.SetChainsPerceived(True)
    for i, expected in enumerate(hydrogenated.GetConformer().GetPositions()):
        atom = om.GetAtom(global_ids[i])
        observed = np.array([atom.GetX(), atom.GetY(), atom.GetZ()])
        if np.linalg.norm(expected - observed) > policy.coordinate_tolerance:
            raise ValueError("Graph transfer moved a coordinate")
    finder = LigandFinder(c.protcomplex, c.altconf, c.modres, c.covalent, c.Mapper)
    c.ligands = finder.ligands
    c.excluded = finder.excluded
    c.atoms = {a.idx: a for a in c.protcomplex}
    ligs = [
        l
        for l in c.ligands
        if l.hetid == policy.ligand_resname
        and l.chain == policy.ligand_chain
        and int(l.position) == policy.ligand_resnum
    ]
    if len(ligs) != 1:
        raise ValueError("PLIP did not identify exactly LIG:Z:1")
    # Validate PDB/OpenBabel graph perception before trusting chemical roles.
    # PLIP's extracted ligand intentionally omits H without replenishing their
    # implicit counts. Validate the input graph with its explicit H, rather than
    # misclassifying that internal heavy-only view as a radical molecule.
    full = ob.OBMol()
    bits = ob.OBBitVec(c.protcomplex.OBMol.NumAtoms())
    for atom in ob.OBMolAtomIter(c.protcomplex.OBMol):
        residue = atom.GetResidue()
        if (
            residue
            and residue.GetChain() == policy.ligand_chain
            and residue.GetName().strip() == policy.ligand_resname
        ):
            bits.SetBitOn(atom.GetIdx())
    c.protcomplex.OBMol.CopySubstructure(full, bits, None, 0)
    block = pybel.Molecule(full).write("sdf")
    perceived = Chem.MolFromMolBlock(block, removeHs=True, sanitize=True)
    if perceived is None:
        raise ValueError("PLIP ligand graph cannot be decoded")
    perceived = roundtrip_heavy_view(perceived)
    expected_smiles = Chem.MolToSmiles(mol, isomericSmiles=False)
    actual_smiles = Chem.MolToSmiles(perceived, isomericSmiles=False)
    if expected_smiles != actual_smiles:
        raise ValueError(
            f"PDB round-trip changed ligand graph: {expected_smiles} != {actual_smiles}"
        )
    a = np.array(mol.GetConformer().GetPositions())
    b = np.array(perceived.GetConformer().GetPositions())
    if len(a) != len(b):
        raise ValueError("PLIP heavy-atom count changed")
    cost = ((a[:, None] - b[None, :]) ** 2).sum(-1)
    za = np.array([x.GetAtomicNum() for x in mol.GetAtoms()])
    zb = np.array([x.GetAtomicNum() for x in perceived.GetAtoms()])
    cost += policy.element_mismatch_penalty * (za[:, None] != zb[None, :])
    ii, jj = linear_sum_assignment(cost)
    if cost[ii, jj].max() > policy.mapping_tolerance**2:
        raise ValueError("PLIP atom-coordinate mapping failed")
    for i, j in zip(ii, jj):
        if (
            mol.GetAtomWithIdx(int(i)).GetFormalCharge()
            != perceived.GetAtomWithIdx(int(j)).GetFormalCharge()
        ):
            raise ValueError("PLIP formal charge changed")
    c.analyze()
    site = c.interaction_sets[
        f"{policy.ligand_resname}:{policy.ligand_chain}:{policy.ligand_resnum}"
    ]
    report = BindingSiteReport(site)
    StructureReport(c).write_xml()
    rows = []
    for kind, name in [
        ("hydrophobic", "hydrophobic"),
        ("hbond", "hbond"),
        ("saltbridge", "saltbridge"),
        ("pistacking", "pistacking"),
        ("pication", "pication"),
        ("halogen", "halogen"),
        ("waterbridge", "waterbridge"),
        ("metal", "metal"),
    ]:
        for values in getattr(report, name + "_info"):
            d = dict(zip(getattr(report, name + "_features"), values))
            role = "hydrophobe" if kind == "hydrophobic" else kind
            if kind == "hydrophobic":
                ligidx = [int(d["LIGCARBONIDX"])]
                protidx = [int(d["PROTCARBONIDX"])]
            elif kind in ("hbond", "waterbridge"):
                pd = bool(d["PROTISDON"])
                role = "ligand_acceptor" if pd else "ligand_donor"
                dk = "DONORIDX" if kind == "hbond" else "DONOR_IDX"
                ak = "ACCEPTORIDX" if kind == "hbond" else "ACCEPTOR_IDX"
                ligidx = [int(d[ak if pd else dk])]
                protidx = [int(d[dk if pd else ak])]
            elif kind in ("saltbridge", "pistacking", "pication"):
                ligidx = [int(x) for x in d["LIG_IDX_LIST"].split(",")]
                protidx = [int(x) for x in d["PROT_IDX_LIST"].split(",")]
                if kind == "saltbridge":
                    role = "ligand_negative" if d["PROTISPOS"] else "ligand_positive"
                if kind == "pication":
                    role = "ligand_aromatic" if d["PROTCHARGED"] else "ligand_positive"
            elif kind == "halogen":
                ligidx = [int(d["DON_IDX"])]
                protidx = [int(d["ACC_IDX"])]
                role = "ligand_halogen_donor"
            else:
                ligidx = [
                    int(x)
                    for x in [d["METAL_IDX"], d["TARGET_IDX"]]
                    if int(x) in serial_map
                ]
                protidx = [
                    int(x)
                    for x in [d["METAL_IDX"], d["TARGET_IDX"]]
                    if int(x) not in serial_map
                ]
            if any(x not in serial_map for x in ligidx):
                raise ValueError("PLIP ligand serial absent from original atom map")
            core = [serial_map[x] for x in ligidx]
            fp = [kind, str(d["RESCHAIN"]), str(d["RESNR"]), str(d["RESTYPE"]), role]
            rows.append(
                {
                    "type": kind,
                    "role": role,
                    "ligand_atoms": core,
                    "ligand_pdb_serials": ligidx,
                    "protein_pdb_serials": protidx,
                    "fingerprint": fp,
                    "details": d,
                }
            )
    core = sorted({i for r in rows for i in r["ligand_atoms"]})
    thresholds = {
        k: v
        for k, v in vars(config).items()
        if k.isupper()
        and any(x in k for x in ("DIST", "ANGLE", "OFFSET"))
        and isinstance(v, (float, int, str, bool))
    }
    result = {
        "plip_version": config.__version__,
        "openbabel": ob.OBReleaseVersion(),
        "nohydro": policy.nohydro,
        "nofix": policy.nofix,
        "protein_sha256": digest(protein_file),
        "source_graph": graph(mol),
        "roundtrip_graph_verified": True,
        "ligand_graph_source": "original SDF, transferred after PDB serialization; no geometric relaxation",
        "heavy_coords_roundtrip_max_A": float(np.sqrt(cost[ii, jj]).max()),
        "pdb_serial_to_native": {str(k): v for k, v in serial_map.items()},
        "core_atoms": core,
        "core_coverage": len(core) / mol.GetNumHeavyAtoms(),
        "interactions": rows,
        "fingerprints": sorted({tuple(r["fingerprint"]) for r in rows}),
        "thresholds": thresholds,
    }
    write(out / "interactions.json", result)
    return result


def prepare_receptor(source, destination, policy):
    from openbabel import openbabel as ob

    conversion = ob.OBConversion()
    conversion.SetInAndOutFormats("pdb", "pdb")
    mol = ob.OBMol()
    assert conversion.ReadFile(mol, str(source))
    before = [
        (a.GetAtomicNum(), a.GetX(), a.GetY(), a.GetZ())
        for a in ob.OBMolAtomIter(mol)
        if a.GetAtomicNum() > 1
    ]
    mol.AddHydrogens()
    assert conversion.WriteFile(mol, str(destination))
    check = ob.OBMol()
    assert conversion.ReadFile(check, str(destination))
    after = [
        (a.GetAtomicNum(), a.GetX(), a.GetY(), a.GetZ())
        for a in ob.OBMolAtomIter(check)
        if a.GetAtomicNum() > 1
    ]
    assert len(before) == len(after)
    delta = float(np.max(np.abs(np.asarray(before) - np.asarray(after))))
    assert delta <= policy.coordinate_tolerance
    return {
        "method": "OpenBabel AddHydrogens; no pH conversion or minimization requested",
        "heavy_count": len(before),
        "heavy_max_component_delta_A": delta,
        "source_sha256": digest(source),
        "prepared_sha256": digest(destination),
    }
