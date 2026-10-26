"""Exact-node provenance and PLIP-selected chemistry/XYZ compilation."""

from __future__ import annotations
import tarfile
from pathlib import Path
import numpy as np
from .io import read_json, write_json, file_digest, digest, TechnicalFailure


def components(vertices, edges):
    adjacency = {i: set() for i in vertices}
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    remaining, result = set(vertices), []
    while remaining:
        pending, group = [min(remaining)], set()
        while pending:
            i = pending.pop()
            if i not in group:
                group.add(i)
                pending.extend(adjacency[i] - group)
        remaining -= group
        result.append(sorted(group))
    return sorted(result)


def compile_conditions(mol, selected, partition, interactions):
    from rdkit import Chem, rdBase
    from rdkit.Chem import BRICS

    selected = sorted(set(selected))
    if not selected or not set(selected) <= set(range(mol.GetNumAtoms())):
        raise ValueError("PLIP selection is empty or outside the teacher")
    if any(mol.GetAtomWithIdx(i).GetAtomicNum() == 1 for i in selected):
        raise ValueError("PLIP conditions must refer to heavy atoms")
    xyz = np.asarray(mol.GetConformer().GetPositions())
    if not np.isfinite(xyz).all():
        raise ValueError("nonfinite reference coordinates")
    kekule = Chem.Mol(mol)
    Chem.Kekulize(kekule, clearAromaticFlags=True)
    atoms = {
        str(i + 1): dict(
            id=i + 1,
            source_index=i,
            atomic_number=mol.GetAtomWithIdx(i).GetAtomicNum(),
            element=mol.GetAtomWithIdx(i).GetSymbol(),
            xyz=xyz[i].tolist(),
        )
        for i in selected
    }
    bonds = {}
    for edge in mol.GetBonds():
        i, j = sorted((edge.GetBeginAtomIdx(), edge.GetEndAtomIdx()))
        if i in selected and j in selected:
            key = f"B:{i + 1}:{j + 1}"
            bonds[key] = dict(
                ids=[i + 1, j + 1],
                semantic=str(edge.GetBondType()),
                encoded=int(kekule.GetBondBetweenAtoms(i, j).GetBondTypeAsDouble()),
            )
    if partition == "selected_connectivity":
        blocks = components(
            [i + 1 for i in selected], [b["ids"] for b in bonds.values()]
        )
    elif partition == "brics_projection":
        cuts = [
            mol.GetBondBetweenAtoms(*ends).GetIdx()
            for ends, _ in BRICS.FindBRICSBonds(mol, randomizeOrder=False)
        ]
        split = Chem.FragmentOnBonds(mol, cuts, addDummies=False) if cuts else mol
        blocks = sorted(
            [sorted(i + 1 for i in group) for group in Chem.GetMolFrags(split)]
        )
    else:
        raise ValueError("unsupported partition")
    groups, owned = {}, set()
    for block in blocks:
        atom_ids = sorted(set(block) & {i + 1 for i in selected})
        edge_ids = sorted(k for k, b in bonds.items() if set(b["ids"]) <= set(block))
        if atom_ids or edge_ids:
            gid = f"C{len(groups) + 1}"
            groups[gid] = dict(atom_ids=atom_ids, bond_ids=edge_ids)
            owned.update(edge_ids)
    cross = sorted(set(bonds) - owned)
    if cross:
        groups["C_interface"] = dict(atom_ids=[], bond_ids=cross)
    all_atoms = [a for g in groups.values() for a in g["atom_ids"]]
    all_bonds = [b for g in groups.values() for b in g["bond_ids"]]
    if sorted(all_atoms) != [i + 1 for i in selected] or sorted(all_bonds) != sorted(
        bonds
    ):
        raise ValueError("incomplete or duplicate condition ownership")
    library = dict(
        schema_version=1,
        atoms=atoms,
        bonds=bonds,
        groups=groups,
        reference_atom_count=mol.GetNumAtoms(),
        partition=partition,
        coordinate_frame="receptor_world_angstrom",
        rdkit_version=rdBase.rdkitVersion,
        reference_interactions=interactions,
        policy=dict(
            one_hop_expansion=False,
            unspecified_is_no_bond=False,
            element_xyz_coupled=True,
        ),
    )
    library["hash"] = digest(library)
    return library


def prepare_teacher(config, teacher):
    from rdkit import Chem

    out = Path(config.data.output_dir) / teacher.id / "prepared"
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "prepared.json"
    if manifest.exists():
        data = read_json(manifest)
        if data["config_hash"] != config.fingerprint():
            raise TechnicalFailure(
                "prepared config changed; use a new output directory"
            )
        for key, path in data["files"].items():
            if file_digest(path) != data["hashes"][key]:
                raise TechnicalFailure("prepared file changed: " + key)
        return data
    archive = Path(config.data.archive_dir) / teacher.archive
    if file_digest(archive) != teacher.archive_sha256:
        raise TechnicalFailure("teacher archive checksum mismatch")
    wanted = {
        teacher.sdf_member: "all.sdf",
        teacher.protein_member: "protein.pdb",
        teacher.ligand_member: "reference_ligand.sdf",
    }
    found = set()
    with tarfile.open(archive, "r|gz") as stream:
        for member in stream:
            name = member.name.removeprefix("./")
            if name not in wanted:
                continue
            if name in found or not member.isfile():
                raise TechnicalFailure("duplicate or non-file archive member")
            (out / wanted[name]).write_bytes(stream.extractfile(member).read())
            found.add(name)
    if found != set(wanted):
        raise TechnicalFailure("missing configured archive members")
    supplier = Chem.SDMolSupplier(str(out / "all.sdf"), sanitize=False, removeHs=False)
    matches = [
        (index, m)
        for index, m in enumerate(supplier)
        if m is not None
        and m.HasProp("node_id")
        and m.GetProp("node_id") == teacher.node_id
    ]
    if len(matches) != 1:
        raise TechnicalFailure("node_id must match exactly one SDF record")
    index, mol = matches[0]
    source = supplier.GetItemText(index)
    (out / "teacher.sdf").write_text(source, encoding="utf-8")
    Chem.SanitizeMol(mol)
    if any(a.GetAtomMapNum() not in (0, a.GetIdx() + 1) for a in mol.GetAtoms()):
        raise TechnicalFailure(
            "existing atom maps conflict with source-index identity policy"
        )
    if (
        mol.GetNumAtoms() != mol.GetNumHeavyAtoms()
        or mol.GetNumHeavyAtoms() != teacher.heavy_atoms
        or mol.GetNumConformers() != 1
    ):
        raise TechnicalFailure("teacher heavy-atom layout or conformer count changed")
    if (
        abs(float(mol.GetProp(teacher.score_property)) - teacher.stored_pic50)
        > teacher.score_tolerance
    ):
        raise TechnicalFailure("teacher score provenance mismatch")
    from .plip import plip_profile, prepare_receptor

    protein = out / "protein_H.pdb"
    prep = prepare_receptor(out / "protein.pdb", protein, config.evaluation.plip)
    write_json(out / "protein_preparation.json", prep)
    # Always recompute on this host; an old report is not silently trusted.
    profile = plip_profile(mol, protein, out / "plip", config.evaluation.plip)
    library = compile_conditions(
        mol, profile["core_atoms"], config.partition, profile["interactions"]
    )
    write_json(out / "conditions.json", library)
    files = {
        "teacher": str(out / "teacher.sdf"),
        "protein": str(out / "protein.pdb"),
        "evaluation_protein": str(protein),
        "reference_ligand": str(out / "reference_ligand.sdf"),
        "library": str(out / "conditions.json"),
    }
    data = dict(
        teacher=teacher.model_dump(),
        config_hash=config.fingerprint(),
        files=files,
        hashes={k: file_digest(p) for k, p in files.items()},
        archive_sha256=teacher.archive_sha256,
        source_record_index_audit_only=index,
        library_hash=library["hash"],
        plip_versions={
            "plip": profile["plip_version"],
            "openbabel": profile["openbabel"],
        },
    )
    write_json(manifest, data)
    return data


def slot_map(library, n):
    required = sorted(int(i) for i in library["atoms"])
    if n < len(required):
        raise ValueError("size cannot accommodate all selected atoms")
    if n == library["reference_atom_count"]:
        return {i: i - 1 for i in required}
    # Size changes happen before initialization; unselected slots have no teacher identity.
    return {i: k for k, i in enumerate(required)}
