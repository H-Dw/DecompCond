"""Frozen FLOWR.ROOT adapter with explicit masks, times and raw final readout."""

from __future__ import annotations
import copy
import inspect
import random
import sys
import types
from pathlib import Path
import numpy as np
import torch
import torch.nn.functional as F
from ..io import TechnicalFailure, file_digest, write_json
from ..conditions import slot_map
from ..sampling_contract import planning_sampling_contract
from ..inpainting import (
    encode_payload,
    project,
    network_times,
    finite_state,
    row,
    combine,
)


class RootBackend:
    def __init__(self, config):
        self.config = config
        c = config.backend
        if file_digest(c.checkpoint) != c.checkpoint_sha256:
            raise TechnicalFailure("checkpoint checksum mismatch")
        sys.path.insert(0, str(Path(c.source_dir).resolve()))
        from flowr.gen.generate_from_pdb import get_args
        from flowr.scriptutil import load_model
        from flowr.gen.utils import load_util
        from flowr.data.datamodules import GeometricDM

        if (
            not Path(inspect.getfile(load_model))
            .resolve()
            .is_relative_to(Path(c.source_dir).resolve())
        ):
            raise TechnicalFailure("imported ROOT is outside configured source_dir")

        saved = sys.argv
        try:
            sys.argv = [
                "decompcond",
                "--arch",
                c.model_args["arch"],
                "--pocket_type",
                c.model_args["pocket_type"],
            ]
            args = get_args()
        finally:
            sys.argv = saved
        for key, value in c.model_args.items():
            if not hasattr(args, key) and key not in {
                "virtual_atom_p",
                "sc_recycle_steps",
            }:
                raise TechnicalFailure("unsupported ROOT argument: " + key)
            setattr(args, key, value)
        args.ckpt_path = c.checkpoint
        args.save_dir = str(Path(config.data.output_dir) / "backend")
        args.data_path = config.data.archive_dir
        args.integration_steps = config.sampling.steps
        args.sample_n_molecules_per_target = config.sampling.batch_size
        torch.set_num_threads(c.threads)
        torch.set_float32_matmul_precision(c.matmul_precision)
        self.device = torch.device(c.device)
        self.model, self.hp, self.vocab, self.qv, self.hv, self.av, _, _ = load_model(
            args
        )
        self.model = self.model.to(self.device).eval()
        self.model.sc_recycle_steps = c.model_args["sc_recycle_steps"]
        if (
            self.model.sc_recycle_steps < 1
            or args.corrector_iters != 0
            or args.virtual_atom_p != 0
        ):
            raise TechnicalFailure(
                "adapter requires positive SC recycles, zero corrector iterations and no virtual atoms"
            )
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)
        if (
            not self.model.self_condition
            or self.model.self_condition_mode != "stacking"
            or self.model.add_feats
            or self.model.flow_interactions
        ):
            raise TechnicalFailure(
                "unsupported checkpoint feature contract; adapter does not silently drop channels"
            )
        checkpoint = torch.load(c.checkpoint, map_location="cpu", weights_only=False)
        mismatch = [
            k
            for k, v in self.model.state_dict().items()
            if k.startswith("gen.")
            and (
                k not in checkpoint["state_dict"]
                or not torch.equal(v.cpu(), checkpoint["state_dict"][k])
            )
        ]
        if mismatch:
            raise TechnicalFailure("loaded generator differs from frozen checkpoint")
        del checkpoint
        self.model._inpaint_self_condition = False
        self.model.graph_inpainting = False
        self.model.inpainting_mode = False
        known_time = config.sampling.known_time

        def get_times(instance, times, lig_mask, inpaint_mask):
            return network_times(times, lig_mask, inpaint_mask, known_time)

        # Only the non-parametric time adapter changes. No learned layer is added.
        self.model._get_times = types.MethodType(get_times, self.model)
        self.transform, self.interpolant = load_util(
            args, self.hp, self.vocab, self.qv, self.hv, self.av
        )
        self.dm = GeometricDM(
            None,
            None,
            None,
            config.sampling.batch_size,
            config.sampling.batch_size,
            num_workers=args.num_workers,
        )
        self.args = args
        self.pockets = {}
        source_files = sorted((Path(c.source_dir) / "flowr").rglob("*.py"))
        self.contract = dict(
            checkpoint_sha256=c.checkpoint_sha256,
            source_sha256={str(p): file_digest(p) for p in source_files},
            model_class=str(type(self.model)),
            hparams=dict(self.hp),
            resolved_args=vars(args),
            atom_vocabulary=self.vocab.tokens_from_indices(
                list(range(self.vocab.size))
            ),
            torch_version=torch.__version__,
            device=str(self.device),
            device_name=torch.cuda.get_device_name(self.device)
            if self.device.type == "cuda"
            else "cpu",
            weights_frozen=True,
            raw_final_readout=True,
            learned_parameters_added=False,
            known_network_time=known_time,
            integration_end_time=config.sampling.integration_end_time,
            readout=config.sampling.readout,
            initial_self_condition_coords=config.sampling.initial_self_condition_coords,
            rng_policy="per-attempt forked RNG; same seed across paths",
            update_count=config.sampling.steps,
            final_forward_count=int(config.sampling.readout == "extra_prediction"),
            planning_sampling_contract=planning_sampling_contract(config),
        )

    def pocket(self, prepared, batch_size):
        from flowr.gen.utils import load_data_from_pdb
        from flowr.util.pocket import PocketComplexBatch

        key = (prepared["teacher"]["id"], batch_size)
        if key not in self.pockets:
            args = copy.copy(self.args)
            args.pdb_file = prepared["files"]["protein"]
            args.ligand_file = prepared["files"]["reference_ligand"]
            system = load_data_from_pdb(
                args,
                remove_hs=self.hp["remove_hs"],
                remove_aromaticity=self.hp["remove_aromaticity"],
            )
            system = self.transform(system)
            data = self.dm._complex_batch_to_dict(
                PocketComplexBatch([system] * batch_size), i=1
            )
            p = self.model.builder.extract_pocket_from_complex(data)
            p["complex"] = data["complex"]
            p["interactions"] = data["interactions"]
            p = {
                k: v.to(self.device) if torch.is_tensor(v) else v for k, v in p.items()
            }
            self.pockets[key] = p
        return self.pockets[key]

    def seed_state(self, seed, n):
        from flowr.util.molrepr import GeometricMolBatch

        devices = [self.device.index or 0] if self.device.type == "cuda" else []
        with torch.random.fork_rng(devices=devices):
            torch.manual_seed(seed)
            if self.device.type == "cuda":
                torch.cuda.manual_seed(seed)
            previous_np = np.random.get_state()
            previous_py = random.getstate()
            try:
                np.random.seed(seed)
                random.seed(seed)
                mol = self.interpolant.prior_sampler.sample_molecule(
                    n, symmetrize=self.interpolant.symmetrize
                )
                state = self.dm._batch_to_dict(GeometricMolBatch.from_list([mol]))
            finally:
                np.random.set_state(previous_np)
                random.setstate(previous_py)
            rng = (
                torch.get_rng_state(),
                torch.cuda.get_rng_state(self.device) if devices else None,
            )
        state = {
            k: v.to(self.device)
            for k, v in state.items()
            if torch.is_tensor(v) and k not in ("fragment_mask", "fragment_mode")
        }
        return state, rng

    def _step(self, state, predicted, prior, times, delta, rng):
        devices = [self.device.index or 0] if self.device.type == "cuda" else []
        with torch.random.fork_rng(devices=devices):
            torch.set_rng_state(rng[0])
            if devices:
                torch.cuda.set_rng_state(rng[1], self.device)
            updated = self.model.integrator.step(state, predicted, prior, times, delta)
            next_rng = (
                torch.get_rng_state(),
                torch.cuda.get_rng_state(self.device) if devices else None,
            )
        return updated, next_rng

    def world(self, state, pocket):
        out = {k: v.detach().cpu() for k, v in state.items() if torch.is_tensor(v)}
        out["coords"] = self.model.builder.undo_zero_com_batch(
            out["coords"] * self.model.coord_scale,
            out["mask"],
            [s.com for s in pocket["complex"]],
        )
        return out

    @torch.no_grad()
    def generate_batch(self, prepared, library, jobs):
        c = self.config
        sampling_contract_hash = planning_sampling_contract(c)["sha256"]
        m = self.model
        bs = len(jobs)
        n = (
            library["reference_atom_count"]
            if c.sampling.node_count_policy == "teacher"
            else c.sampling.node_count
        )
        slots = slot_map(library, n)
        p = self.pocket(prepared, bs)
        initial = [self.seed_state(job["seed"], n) for job in jobs]
        prior = combine([a for a, _ in initial])
        rng = [b for _, b in initial]
        current = {k: v.clone() for k, v in prior.items()}
        cache = dict(
            coords=(
                prior["coords"].clone()
                if c.sampling.initial_self_condition_coords == "prior"
                else torch.zeros_like(prior["coords"])
            ),
            atomics=torch.zeros_like(prior["atomics"]),
            bonds=torch.zeros_like(prior["bonds"]),
        )
        if m.sc_charges:
            cache["charges"] = torch.zeros_like(prior["charges"])
        centers = torch.stack(
            [torch.as_tensor(s.com, device=self.device) for s in p["complex"]]
        ).reshape(bs, 3)
        times = [torch.zeros(bs, device=self.device) for _ in range(3)]
        points = torch.linspace(
            0, c.sampling.integration_end_time, c.sampling.steps + 1, device=self.device
        )
        pe, pi = m.gen.get_pocket_encoding(
            p["coords"],
            p["atom_names"],
            pocket_atom_charges=p["charges"].argmax(-1),
            pocket_bond_types=p["bonds"].argmax(-1),
            pocket_res_types=p["res_names"],
            pocket_atom_mask=p["mask"],
        )
        traces = [[] for _ in jobs]
        failures = [None for _ in jobs]
        previous_active = [set() for _ in jobs]
        forward_count = 0
        for step in range(c.sampling.steps):
            active = [job["path"]["expanded"][step] for job in jobs]
            payload = encode_payload(
                library,
                slots,
                active,
                n,
                self.vocab,
                self.device,
                centers,
                m.coord_scale,
                prior["bonds"].shape[-1],
            )
            injected = project(current, payload)
            predicted, next_cache = m._get_predictions(
                m(
                    injected,
                    p,
                    times,
                    cond_batch=cache,
                    pocket_equis=pe,
                    pocket_invs=pi,
                    training=False,
                )
            )
            forward_count += 1
            for _ in range(m.sc_recycle_steps - 1):
                predicted, next_cache = m._get_predictions(
                    m(
                        injected,
                        p,
                        times,
                        cond_batch=next_cache,
                        pocket_equis=pe,
                        pocket_invs=pi,
                        training=False,
                    )
                )
                forward_count += 1
            # Snapshot the unprojected prediction before the solver can reuse tensors.
            # In last_prediction mode this is the returned clean prediction, not S_end.
            if step == c.sampling.steps - 1 and c.sampling.readout == "last_prediction":
                raw = {k: v.detach().clone() for k, v in predicted.items() if torch.is_tensor(v)}
                readout_time = float(times[0][0])
            proposed = []
            for index in range(bs):
                if not finite_state(row(predicted, index)) or not finite_state(
                    row(next_cache, index)
                ):
                    failures[index] = failures[index] or dict(
                        reason="nonfinite_prediction", step=step
                    )
                if failures[index] is None:
                    state, rng[index] = self._step(
                        row(injected, index),
                        row(predicted, index),
                        row(prior, index),
                        [t[index : index + 1] for t in times],
                        points[step + 1] - points[step],
                        rng[index],
                    )
                    if not finite_state(state):
                        failures[index] = dict(
                            reason="nonfinite_integrated_state", step=step
                        )
                if failures[index] is not None:
                    # Keep a finite placeholder in the batch; this attempt is
                    # terminally failed and will never be scored as generated.
                    state = row(injected, index)
                    for key, value in next_cache.items():
                        if torch.is_tensor(value):
                            value[index : index + 1] = cache[key][index : index + 1]
                proposed.append(state)
            proposal = combine(proposed)
            for index in range(bs):
                changed = set(active[index]) != previous_active[index]
                if (
                    changed
                    or step % c.sampling.trace_every == 0
                    or step == c.sampling.steps - 1
                ):
                    mask = payload["atom_mask"][index]
                    displacement = (
                        current["coords"][index] - payload["coords"][index]
                    ).norm(dim=-1) * m.coord_scale
                    restoration = (
                        proposal["coords"][index] - payload["coords"][index]
                    ).norm(dim=-1) * m.coord_scale
                    traces[index].append(
                        dict(
                            step=step,
                            active=active[index],
                            switched=changed,
                            pre_projection_max_A=float(displacement[mask].max())
                            if mask.any()
                            else 0.0,
                            post_step_restoration_max_A=float(restoration[mask].max())
                            if mask.any()
                            else 0.0,
                        )
                    )
                previous_active[index] = set(active[index])
            current = project(proposal, payload)
            cache = {k: v.detach() for k, v in next_cache.items() if torch.is_tensor(v)}
            times = m._update_times(times, points[step + 1] - points[step])
        if c.sampling.readout == "extra_prediction":
            times = m._update_times(times, -c.sampling.final_epsilon)
            raw, _ = m._get_predictions(
                m(
                    current,
                    p,
                    times,
                    cond_batch=cache,
                    pocket_equis=pe,
                    pocket_invs=pi,
                    training=False,
                )
            )
            forward_count += 1
            readout_time = float(times[0][0])
        world = self.world(raw, p)
        results = []
        for index, job in enumerate(jobs):
            folder = Path(job["folder"])
            folder.mkdir(parents=True, exist_ok=True)
            tensors = row(world, index)
            if failures[index] is not None or not finite_state(tensors):
                failure = failures[index] or dict(
                    reason="nonfinite_final_output", step=c.sampling.steps
                )
                write_json(folder / "trace.json", traces[index])
                results.append(
                    dict(
                        status="generation_failure",
                        **failure,
                        seed=job["seed"],
                        trace=traces[index],
                        condition_hash=library["hash"],
                        schedule_hash=job["path"]["schedule_hash"],
                        sampling_contract_sha256=sampling_contract_hash,
                    )
                )
                continue
            path = folder / "raw.npz"
            np.savez_compressed(path, **{k: v.numpy() for k, v in tensors.items()})
            write_json(folder / "trace.json", traces[index])
            results.append(
                dict(
                    status="generated",
                    raw_path=str(path),
                    raw_sha256=file_digest(path),
                    slots={str(k): v for k, v in slots.items()},
                    trace=traces[index],
                    seed=job["seed"],
                    updates=c.sampling.steps,
                    nfe=forward_count,
                    readout=c.sampling.readout,
                    readout_global_time=readout_time,
                    integration_end_time=c.sampling.integration_end_time,
                    known_network_time=c.sampling.known_time,
                    initial_self_condition_coords=c.sampling.initial_self_condition_coords,
                    sampling_contract_sha256=sampling_contract_hash,
                    condition_hash=library["hash"],
                    schedule_hash=job["path"]["schedule_hash"],
                )
            )
        return results

    def decode(self, world):
        return self.model._generate_mols(world, valence_repair=False)[0]

    @torch.no_grad()
    def score_molecule(self, mol, prepared):
        """Normalize aromatic representation on a copy; never move heavy atoms."""
        from rdkit import Chem

        mol = Chem.Mol(mol)
        for a in mol.GetAtoms():
            a.SetAtomMapNum(0)
        Chem.SanitizeMol(mol)
        # Same normalization for reference and generated molecules.
        ranks = list(Chem.CanonicalRankAtoms(mol, breakTies=True))
        order = sorted(range(mol.GetNumAtoms()), key=ranks.__getitem__)
        mol = Chem.RenumberAtoms(mol, order)
        Chem.Kekulize(mol, clearAromaticFlags=True)
        n = mol.GetNumAtoms()
        p = self.pocket(prepared, 1)
        center = torch.as_tensor(p["complex"][0].com, device=self.device).reshape(
            1, 1, 3
        )
        z = torch.tensor(
            self.vocab.indices_from_tokens([a.GetSymbol() for a in mol.GetAtoms()]),
            device=self.device,
        )
        q = torch.tensor(
            self.qv.indices_from_tokens([a.GetFormalCharge() for a in mol.GetAtoms()]),
            device=self.device,
        )
        b = torch.zeros((n, n), dtype=torch.long, device=self.device)
        for edge in mol.GetBonds():
            i, j = edge.GetBeginAtomIdx(), edge.GetEndAtomIdx()
            b[i, j] = b[j, i] = int(edge.GetBondTypeAsDouble())
        state = dict(
            coords=(
                torch.tensor(
                    mol.GetConformer().GetPositions(),
                    dtype=torch.float32,
                    device=self.device,
                )[None]
                - center
            )
            / self.model.coord_scale,
            atomics=F.one_hot(z, self.vocab.size).float()[None],
            charges=F.one_hot(q, self.qv.size).float()[None],
            bonds=F.one_hot(b, self.model.gen.ligand_dec.n_bond_types).float()[None],
            mask=torch.ones((1, n), device=self.device, dtype=torch.long),
        )
        times = [
            torch.full((1,), self.config.backend.affinity_time, device=self.device)
            for _ in range(3)
        ]
        value = float(
            self.model._predict_affinity(state, state, p, times)["affinity"][
                "pic50"
            ].reshape(-1)[0]
        )
        if not np.isfinite(value):
            raise TechnicalFailure("nonfinite affinity prediction")
        return value

    def load_raw(self, path):
        with np.load(path) as arrays:
            return {k: torch.from_numpy(arrays[k]) for k in arrays.files}
