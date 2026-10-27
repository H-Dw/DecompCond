"""Equivalent volume checks with immutable receptor caches and batched edits."""

from functools import partial
from pathlib import Path
import numpy as np


def delete_atoms_batch(mol, indices):
    from rdkit import Chem

    indices = sorted(indices, reverse=True)
    if not indices:
        return mol
    edited = Chem.RWMol(mol)
    edited.BeginBatchEdit()
    for index in indices:
        edited.RemoveAtom(int(index))
    edited.CommitBatchEdit()
    return Chem.Mol(edited)


def make_posebusters(config):
    """Keep library and thresholds intact; override only volume execution."""
    from posebusters import PoseBusters
    from posebusters.modules.volume_overlap import check_volume_overlap
    from posebusters.tools.protein import get_atom_type_mask
    from rdkit.Chem.rdShapeHelpers import ShapeTverskyIndex
    from .io import file_digest
    import inspect

    defaults = inspect.signature(check_volume_overlap)

    class CachedPoseBusters(PoseBusters):
        def __init__(self):
            super().__init__(config=config)
            self.receptors = {}
            self.categories = {}
            self.receptor_key = None

        def bust(self, mol_pred, mol_true=None, mol_cond=None, full_report=False):
            # Receptor bytes, loader parameters and category parameters identify
            # a cache entry. A worker never shares mutable RDKit/PLIP objects.
            from posebusters.posebusters import safe_load_mol
            from .io import digest

            if isinstance(mol_cond, (str, Path)):
                params = self.config.get("loading", {}).get("mol_cond", {})
                self.receptor_key = (file_digest(mol_cond), digest(params))
                if self.receptor_key not in self.receptors:
                    self.receptors[self.receptor_key] = safe_load_mol(
                        path=mol_cond, **params
                    )
                mol_cond = self.receptors[self.receptor_key]
            else:
                # Unbound objects are not cached across calls.
                self.receptor_key = None
                self.categories.clear()
            return super().bust(mol_pred, mol_true, mol_cond, full_report)

        def _initialize_modules(self):
            super()._initialize_modules()
            for i, module in enumerate(self.config["modules"]):
                if module["function"] == "volume_overlap":
                    self.module_func[i] = partial(
                        self.volume, **module.get("parameters", {})
                    )

        def volume(self, mol_pred, mol_cond, **parameters):
            bound = defaults.bind(mol_pred, mol_cond, **parameters)
            bound.apply_defaults()
            p = bound.arguments
            ignored = frozenset(p["ignore_types"])
            key = (
                self.receptor_key if self.receptor_key is not None else id(mol_cond),
                ignored,
            )
            if key not in self.categories:
                mask = np.asarray(get_atom_type_mask(mol_cond, ignored), dtype=bool)
                # Empty classes need no expensive construction/deletion at all.
                subset = (
                    delete_atoms_batch(mol_cond, np.flatnonzero(~mask))
                    if mask.any()
                    else None
                )
                self.categories[key] = subset
            subset = self.categories[key]
            if subset is None:
                return {"results": {"volume_overlap": np.nan, "no_volume_clash": True}}
            distance = np.linalg.norm(
                mol_pred.GetConformer().GetPositions()[:, None, :]
                - subset.GetConformer().GetPositions()[None, :, :],
                axis=-1,
            )
            keep = distance.min(axis=0) <= p["search_distance"] * p["vdw_scale"]
            if not keep.any():
                return {"results": {"volume_overlap": np.nan, "no_volume_clash": True}}
            local = delete_atoms_batch(subset, np.flatnonzero(~keep))
            overlap = ShapeTverskyIndex(
                mol_pred,
                local,
                alpha=1,
                beta=0,
                vdwScale=p["vdw_scale"],
                ignoreHs="hydrogens" in ignored,
            )
            return {
                "results": {
                    "volume_overlap": overlap,
                    "no_volume_clash": overlap <= p["clash_cutoff"],
                }
            }

    return CachedPoseBusters()
