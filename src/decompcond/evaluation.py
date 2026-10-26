"""Evaluate raw predictions and immutable XYZ, never the projected labels."""

from __future__ import annotations
import copy
from pathlib import Path
import numpy as np
import yaml
from .io import TechnicalFailure, file_digest, write_json


def xyz_reconstruction(z, xyz, factor):
    from rdkit import Chem
    from rdkit.Chem import rdDetermineBonds
    from openbabel import openbabel as ob

    rw = Chem.RWMol()
    for number in z:
        rw.AddAtom(Chem.Atom(int(number)))
    conf = Chem.Conformer(len(z))
    for i, point in enumerate(xyz):
        conf.SetAtomPosition(i, point)
    rw.AddConformer(conf)
    naked = rw.GetMol()
    rdDetermineBonds.DetermineConnectivity(naked, useVdw=True, covFactor=factor)
    rd_adj = Chem.GetAdjacencyMatrix(naked).astype(bool)
    om = ob.OBMol()
    om.BeginModify()
    for number, point in zip(z, xyz):
        atom = om.NewAtom()
        atom.SetAtomicNum(int(number))
        atom.SetVector(*map(float, point))
    om.EndModify()
    om.ConnectTheDots()
    om.PerceiveBondOrders()
    rebuilt = Chem.RWMol()
    for number in z:
        rebuilt.AddAtom(Chem.Atom(int(number)))
    ob_adj = np.zeros_like(rd_adj)
    for edge in ob.OBMolBondIter(om):
        i, j = edge.GetBeginAtomIdx() - 1, edge.GetEndAtomIdx() - 1
        ob_adj[i, j] = ob_adj[j, i] = True
        rebuilt.AddBond(
            i,
            j,
            {1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE}[
                edge.GetBondOrder()
            ],
        )
    rebuilt.AddConformer(conf)
    mol = rebuilt.GetMol()
    try:
        Chem.SanitizeMol(mol)
        return mol, rd_adj, ob_adj, None
    except (ValueError, RuntimeError) as exc:
        return None, rd_adj, ob_adj, type(exc).__name__


def strain_energy(mol, config):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    heavy = mol.GetNumAtoms()
    h = Chem.AddHs(Chem.Mol(mol), addCoords=True)
    if not AllChem.MMFFHasAllMoleculeParams(h):
        return dict(status="unsupported_parameters", strain_kcal_mol=None)
    props = AllChem.MMFFGetMoleculeProperties(h, mmffVariant=config.force_field)
    ff = AllChem.MMFFGetMoleculeForceField(h, props)
    for i in range(heavy):
        ff.AddFixedPoint(i)
    ff.Initialize()
    h_code = ff.Minimize(
        maxIts=config.minimization_steps,
        forceTol=config.force_tolerance,
        energyTol=config.energy_tolerance,
    )
    if h_code != 0:
        return dict(status="hydrogen_optimization_not_converged", strain_kcal_mol=None)
    e_pose = float(ff.CalcEnergy())
    free = Chem.Mol(h)
    opt = AllChem.MMFFGetMoleculeForceField(free, props)
    code = opt.Minimize(
        maxIts=config.minimization_steps,
        forceTol=config.force_tolerance,
        energyTol=config.energy_tolerance,
    )
    if code != 0:
        return dict(
            status="optimization_not_converged",
            pose_energy=e_pose,
            strain_kcal_mol=None,
        )
    e_min = float(opt.CalcEnergy())
    if not np.isfinite([e_pose, e_min]).all():
        raise TechnicalFailure("nonfinite force-field energy")
    return dict(
        status="ok",
        pose_energy=e_pose,
        minimum_energy=e_min,
        strain_kcal_mol=e_pose - e_min,
        heavy_rmsd_A=float(
            np.sqrt(
                np.mean(
                    np.sum(
                        (
                            free.GetConformer().GetPositions()[:heavy]
                            - mol.GetConformer().GetPositions()
                        )
                        ** 2,
                        axis=1,
                    )
                )
            )
        ),
    )


def interaction_recovery(reference, actual, slots):
    def key(row, mapping):
        ids = (
            tuple(sorted(mapping.get(i + 1, i) for i in row["ligand_atoms"]))
            if mapping
            else tuple(sorted(row["ligand_atoms"]))
        )
        return tuple(row["fingerprint"]) + (ids,)

    expected = {key(r, slots) for r in reference}
    observed = {key(r, None) for r in actual}
    return dict(
        expected=len(expected),
        matched=len(expected & observed),
        observed=len(observed),
        recall=len(expected & observed) / len(expected) if expected else None,
    )


class Evaluator:
    def __init__(self, config, backend):
        from posebusters import PoseBusters
        import importlib
        from rdkit import rdBase
        from openbabel import openbabel
        from plip.basic import config as plip_config

        self.config = config
        self.backend = backend
        self.pb_config = yaml.safe_load(Path(config.evaluation.pb_config).read_text())
        self.pb = PoseBusters(config=copy.deepcopy(self.pb_config))
        self.teacher_scores = {}
        self.contract = dict(
            rdkit=rdBase.rdkitVersion,
            numpy=np.__version__,
            openbabel=openbabel.OBReleaseVersion(),
            plip=plip_config.__version__,
            sources={},
        )
        for name in ("posebusters", "plip"):
            package_root = Path(importlib.import_module(name).__file__).parent
            self.contract["sources"][name] = {
                str(p.relative_to(package_root)): file_digest(p)
                for p in sorted(package_root.rglob("*.py"))
            }

    def teacher_score(self, prepared):
        from rdkit import Chem

        key = prepared["teacher"]["id"]
        if key not in self.teacher_scores:
            mol = next(
                iter(Chem.SDMolSupplier(prepared["files"]["teacher"], removeHs=True))
            )
            self.teacher_scores[key] = self.backend.score_molecule(mol, prepared)
            write_json(
                Path(prepared["files"]["teacher"]).parent / "score_reference.json",
                dict(
                    stored_pic50=prepared["teacher"]["stored_pic50"],
                    normalized_pic50=self.teacher_scores[key],
                    policy="canonical atom ordering, aromatic perception, then Kekulize; unchanged XYZ",
                ),
            )
        return self.teacher_scores[key]

    def evaluate(self, result, prepared, library, folder):
        from rdkit import Chem

        folder = Path(folder)
        c = self.config.evaluation
        if result["status"] == "generation_failure":
            return dict(
                status="generation_failure",
                reason=result["reason"],
                structure_pass=False,
                atom_recovery=0.0,
                bond_recovery=0.0,
                recovery=0.0,
                effective_recovery=0.0,
                delta_pic50=None,
                condition_metrics={},
                pb_checks={},
                strain=None,
                plip=None,
                trace=result.get("trace", []),
                recovery_status="zero_by_failed_attempt_convention_not_measured",
            )
        if file_digest(result["raw_path"]) != result["raw_sha256"]:
            raise TechnicalFailure("raw output checksum mismatch")
        world = self.backend.load_raw(result["raw_path"])
        mol = self.backend.decode(world)
        if mol is None:
            return dict(
                status="chemical_invalid",
                structure_pass=False,
                atom_recovery=0.0,
                bond_recovery=0.0,
                recovery=0.0,
                effective_recovery=0.0,
                delta_pic50=None,
                condition_metrics={},
                pb_checks={},
                strain=None,
                plip=None,
                trace=result.get("trace", []),
                recovery_status="zero_by_failed_attempt_convention_not_measured",
            )
        original_xyz = np.array(mol.GetConformer().GetPositions(), copy=True)
        raw_xyz = world["coords"][0].numpy()
        if not np.allclose(original_xyz, raw_xyz, rtol=0, atol=0):
            raise TechnicalFailure("decoder changed raw coordinates")
        slots = {int(k): v for k, v in result["slots"].items()}
        xyz = original_xyz
        z = [a.GetAtomicNum() for a in mol.GetAtoms()]
        # All bonds in the generated graph remain model predictions; independent
        # reconstruction receives only raw XYZ and raw predicted elements.
        rebuilt, rd_adj, ob_adj, rebuild_error = xyz_reconstruction(
            z, xyz, c.connectivity_factor
        )
        raw_adj = Chem.GetAdjacencyMatrix(mol).astype(bool)
        local_ok = np.all((raw_adj == rd_adj) & (raw_adj == ob_adj), axis=1)
        atom_hits = {
            i: bool(z[slots[int(i)]] == a["atomic_number"] and local_ok[slots[int(i)]])
            for i, a in library["atoms"].items()
        }
        edge_hits = {}
        bond_evidence = {}
        for key, edge in library["bonds"].items():
            i, j = [slots[k] for k in edge["ids"]]
            raw_edge = mol.GetBondBetweenAtoms(i, j)
            inferred = (
                rebuilt.GetBondBetweenAtoms(i, j) if rebuilt is not None else None
            )
            edge_hits[key] = bool(
                raw_edge
                and inferred
                and str(raw_edge.GetBondType()) == edge["semantic"]
                and str(inferred.GetBondType()) == edge["semantic"]
                and rd_adj[i, j]
                and all(atom_hits[str(k)] for k in edge["ids"])
            )
            bond_evidence[key] = dict(
                expected=edge["semantic"],
                raw=str(raw_edge.GetBondType()) if raw_edge else None,
                xyz_inferred=str(inferred.GetBondType()) if inferred else None,
                distance_A=float(np.linalg.norm(xyz[i] - xyz[j])),
                rdkit_connected=bool(rd_adj[i, j]),
                openbabel_connected=bool(ob_adj[i, j]),
                recovered=edge_hits[key],
            )
        rz = sum(atom_hits.values()) / len(atom_hits)
        rb = sum(edge_hits.values()) / len(edge_hits) if edge_hits else None
        # No selected bonds: no fabricated bond successes; use atom recovery.
        recovery = c.atom_weight * rz + c.bond_weight * rb if rb is not None else rz
        frame = self.pb.bust(
            mol, mol_cond=prepared["files"]["evaluation_protein"], full_report=False
        )
        if frame.empty:
            raise TechnicalFailure("PoseBusters returned no report")
        checks = {}
        for key, value in frame.iloc[0].items():
            if value is None or (
                isinstance(value, (float, np.floating)) and not np.isfinite(value)
            ):
                raise TechnicalFailure("PoseBusters missing metric: " + key)
            checks[key] = bool(value)
        structural = bool(
            all(checks.values())
            and len(Chem.GetMolFrags(mol)) == 1
            and all(a.GetNumRadicalElectrons() == 0 for a in mol.GetAtoms())
        )
        score = self.backend.score_molecule(mol, prepared)
        energy = strain_energy(mol, c)
        from .plip import plip_profile

        profile = plip_profile(
            mol, prepared["files"]["evaluation_protein"], folder / "plip", c.plip
        )
        plip = interaction_recovery(
            library["reference_interactions"], profile["interactions"], slots
        )
        displacement = np.array(
            [xyz[slots[int(i)]] - a["xyz"] for i, a in library["atoms"].items()]
        )
        norms = np.linalg.norm(displacement, axis=1)
        per_group = {}
        for gid, group in library["groups"].items():
            ah = [atom_hits[str(i)] for i in group["atom_ids"]]
            bh = [edge_hits[i] for i in group["bond_ids"]]
            per_group[gid] = dict(
                atom_hits=sum(ah),
                atom_count=len(ah),
                bond_hits=sum(bh),
                bond_count=len(bh),
            )
        with Chem.SDWriter(str(folder / "actual.sdf")) as writer:
            writer.write(mol)
        if not np.array_equal(original_xyz, mol.GetConformer().GetPositions()):
            raise TechnicalFailure("evaluation moved original atoms")
        return dict(
            status="evaluated",
            structure_pass=structural,
            atom_recovery=rz,
            bond_recovery=rb,
            recovery=recovery,
            effective_recovery=recovery if structural else 0.0,
            exact_recovery=all(atom_hits.values()) and all(edge_hits.values()),
            pic50=score,
            delta_pic50=score - self.teacher_score(prepared),
            pb_checks=checks,
            strain=energy,
            plip=plip,
            atom_hits=atom_hits,
            bond_hits=edge_hits,
            bond_evidence=bond_evidence,
            raw_element_match_rate=sum(
                z[slots[int(i)]] == a["atomic_number"]
                for i, a in library["atoms"].items()
            )
            / len(library["atoms"]),
            condition_metrics=per_group,
            reconstruction_error=rebuild_error,
            selected_rmsd_A=float(np.sqrt(np.mean(norms**2))),
            selected_max_displacement_A=float(norms.max()),
            coordinate_gate=False,
            raw_sha256=result["raw_sha256"],
            trace=result.get("trace", []),
        )
