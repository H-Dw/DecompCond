"""Strict configuration: scientific defaults live in YAML, not runtime functions."""

from __future__ import annotations
import copy
import os
import re
from pathlib import Path
from typing import Any, Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .io import digest


class Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )


class Teacher(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    target_id: str
    node_id: str
    archive: str
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sdf_member: str
    protein_member: str
    ligand_member: str
    heavy_atoms: int = Field(gt=0)
    stored_pic50: float
    score_property: str
    score_tolerance: float = Field(ge=0)


class DataConfig(Strict):
    archive_dir: str
    output_dir: str
    teachers: list[Teacher] = Field(min_length=1)


class Sampling(Strict):
    steps: int = Field(gt=0)
    segment_multiple: int = Field(gt=0)
    known_time: float = Field(ge=0, le=1)
    integration_end_time: float = Field(gt=0, le=1)
    readout: Literal["extra_prediction", "last_prediction"]
    initial_self_condition_coords: Literal["prior", "zeros"]
    final_epsilon: float | None = Field(gt=0, lt=1)
    seed_count: int = Field(gt=0)
    discovery_seed_start: int = Field(ge=0)
    confirmation_seed_start: int = Field(ge=0)
    batch_size: int = Field(gt=0)
    trace_every: int = Field(gt=0)
    node_count_policy: Literal["teacher", "explicit"]
    node_count: int | None

    @model_validator(mode="after")
    def coherent(self):
        if self.readout == "extra_prediction" and (
            self.final_epsilon is None
            or self.final_epsilon >= self.integration_end_time
        ):
            raise ValueError("extra prediction requires epsilon below the integration endpoint")
        if self.readout == "last_prediction" and self.final_epsilon is not None:
            raise ValueError("last prediction requires final_epsilon=null; no extra forward is performed")
        if self.steps % self.segment_multiple:
            raise ValueError("steps must be divisible by segment_multiple")
        a = set(
            range(
                self.discovery_seed_start, self.discovery_seed_start + self.seed_count
            )
        )
        b = set(
            range(
                self.confirmation_seed_start,
                self.confirmation_seed_start + self.seed_count,
            )
        )
        if a & b:
            raise ValueError("discovery and confirmation seeds must be disjoint")
        if self.node_count_policy == "explicit" and (
            self.node_count is None or self.node_count <= 0
        ):
            raise ValueError("explicit size requires a positive node_count")
        return self


class Search(Strict):
    rounds: int = Field(gt=0)
    paths_per_round: int = Field(gt=0)
    candidates_per_path: int = Field(gt=0)
    baseline_candidates: int = Field(gt=0)
    confirmation_candidates: int = Field(gt=0)
    proposal_repairs: int = Field(ge=0)
    evaluation_retries: int = Field(ge=0)
    early_stop: Literal[False]


class Credential(Strict):
    mode: Literal["env", "interactive_shell"]
    env_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    shell_executable: str
    shell_kind: Literal["posix", "powershell"]
    shell_timeout: float = Field(gt=0)


class AgentConfig(Strict):
    base_url: str
    model: str
    credentials: Credential
    timeout: float = Field(gt=0)
    api_retries: int = Field(ge=0)
    retry_delay: float = Field(ge=0)
    max_tokens: int = Field(gt=0)
    temperature: float | None
    thinking: dict[str, Any]
    system_prompt: str
    enhanced_feedback: bool
    contrast_feedback: bool = False
    guidance_file: str | None
    guidance_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def guidance_binding(self):
        if self.contrast_feedback and not self.enhanced_feedback:
            raise ValueError("contrast feedback requires enhanced discovery feedback")
        if bool(self.guidance_file) != bool(self.guidance_sha256):
            raise ValueError("guidance requires both a file and its SHA-256")
        return self


class BackendConfig(Strict):
    source_dir: str
    checkpoint: str
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device: str
    threads: int = Field(gt=0)
    matmul_precision: Literal["highest", "high", "medium"]
    model_args: dict[str, Any]
    affinity_time: float = Field(ge=0, lt=1)


class PlipConfig(Strict):
    ligand_resname: str
    ligand_chain: str
    ligand_resnum: int
    nohydro: bool
    nofix: bool
    maxthreads: int = Field(gt=0)
    coordinate_tolerance: float = Field(gt=0)
    mapping_tolerance: float = Field(gt=0)
    element_mismatch_penalty: float = Field(gt=0)
    thresholds: dict[str, float]


class Evaluation(Strict):
    protocol_version: str
    workers: int = Field(gt=0)
    worker_threads: int = Field(gt=0)
    fast_volume_overlap: bool
    score_only_structure_pass: bool
    plip_sampling: Literal["full", "seed_strata_v1"]
    plip_samples_per_path: int = Field(gt=0)
    plip_sampling_salt: str
    atom_weight: float = Field(gt=0)
    bond_weight: float = Field(gt=0)
    connectivity_factor: float = Field(gt=0)
    force_field: Literal["MMFF94", "MMFF94s"]
    minimization_steps: int = Field(gt=0)
    force_tolerance: float = Field(gt=0)
    energy_tolerance: float = Field(gt=0)
    pb_config: str
    plip: PlipConfig

    @model_validator(mode="after")
    def weights(self):
        if abs(self.atom_weight + self.bond_weight - 1) > 1e-12:
            raise ValueError("recovery weights must sum to one")
        return self


class Remote(Strict):
    host: str
    port: int = Field(gt=0, le=65535)
    user: str
    password_env: str
    python: str
    workdir: str
    environment: dict[str, str]


class Config(Strict):
    schema_version: Literal[1]
    data: DataConfig
    sampling: Sampling
    search: Search
    partition: Literal["selected_connectivity", "brics_projection"]
    agent: AgentConfig
    backend: BackendConfig
    evaluation: Evaluation
    remote: Remote

    @model_validator(mode="after")
    def coherent(self):
        counts = [
            self.search.candidates_per_path,
            self.search.baseline_candidates,
            self.search.confirmation_candidates,
        ]
        if max(counts) > self.sampling.seed_count:
            raise ValueError("seed_count must cover all candidate budgets")
        ids = [t.id for t in self.data.teachers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate teacher IDs")
        return self

    def fingerprint(self) -> str:
        return digest(self.model_dump())


def merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        out[key] = (
            merge(out[key], value)
            if isinstance(value, dict) and isinstance(out.get(key), dict)
            else copy.deepcopy(value)
        )
    return out


def load_config(path: str | Path, overrides: list[str] | None = None) -> Config:
    def read(p: Path, seen: set[Path]):
        p = p.resolve()
        if p in seen:
            raise ValueError("cyclic config inheritance")
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        parent = raw.pop("extends", None)
        return merge(read(p.parent / parent, seen | {p}), raw) if parent else raw

    raw = read(Path(path), set())
    for item in overrides or []:
        key, value = item.split("=", 1)
        node = raw
        for part in key.split(".")[:-1]:
            node = node[part]
        node[key.split(".")[-1]] = yaml.safe_load(value)

    def env(value):
        if isinstance(value, str):

            def expand(m):
                name = m.group(1)
                if not os.environ.get(name):
                    raise ValueError(f"missing configured environment variable {name}")
                return os.environ[name]

            return re.sub(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", expand, value)
        if isinstance(value, list):
            return [env(v) for v in value]
        if isinstance(value, dict):
            return {k: env(v) for k, v in value.items()}
        return value

    return Config.model_validate(env(raw))
