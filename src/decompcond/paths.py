"""Complete schedule validation, canonical identities, and exposure statistics."""

from __future__ import annotations
from typing import Any
from pydantic import Field, StrictInt
from .config import Strict
from .io import InvalidProposal, digest


class Segment(Strict):
    start: StrictInt
    end: StrictInt
    active: list[str]


class PathProposal(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    segments: list[Segment] = Field(min_length=1)
    parents: list[str]
    evidence: list[str] = Field(min_length=1)
    observation: str = Field(min_length=1)
    change: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)


def compile_path(
    path: dict | PathProposal, groups: dict, steps: int, segment_multiple: int
) -> dict:
    p = path if isinstance(path, PathProposal) else PathProposal.model_validate(path)
    cursor, expanded = 0, []
    for s in p.segments:
        if s.start != cursor or s.end <= s.start or s.end > steps:
            raise InvalidProposal(
                "segments must be contiguous, nonempty, and cover the update range"
            )
        if s.start % segment_multiple or s.end % segment_multiple:
            raise InvalidProposal("segment boundary is not on the configured grid")
        if len(s.active) != len(set(s.active)) or not set(s.active) <= set(groups):
            raise InvalidProposal("unknown or duplicate condition IDs")
        expanded.extend([sorted(s.active)] * (s.end - s.start))
        cursor = s.end
    if cursor != steps:
        raise InvalidProposal("path does not cover every update")
    return {**p.model_dump(), "expanded": expanded, "schedule_hash": digest(expanded)}


def constant_path(groups: dict, steps: int, segment_multiple: int) -> dict:
    return compile_path(
        dict(
            id="baseline",
            segments=[dict(start=0, end=steps, active=sorted(groups))],
            parents=[],
            evidence=["condition_library"],
            observation="All selected facts",
            change="Static control",
            hypothesis="Measure frozen ROOT inpainting capability",
        ),
        groups,
        steps,
        segment_multiple,
    )


def validate_batch(
    proposals: list[dict],
    library: dict,
    steps: int,
    grid: int,
    count: int,
    previous: dict[str, dict],
    evidence_ids: set[str],
) -> list[dict]:
    if len(proposals) != count:
        raise InvalidProposal(f"expected exactly {count} paths")
    result, identities, names = [], set(previous), set()
    previous_ids = {x["path"]["id"] for x in previous.values()}
    for proposal in proposals:
        p = compile_path(proposal, library["groups"], steps, grid)
        if p["id"] in names or p["id"] in previous_ids:
            raise InvalidProposal("path IDs must be unique across rounds")
        if p["schedule_hash"] in identities:
            known = previous.get(p["schedule_hash"])
            raise InvalidProposal(
                "already evaluated or duplicate schedule"
                + (f"; cached result: {known['summary']}" if known else "")
            )
        if not set(p["evidence"]) <= evidence_ids:
            raise InvalidProposal("rationale references unknown evidence")
        if not set(p["parents"]) <= previous_ids:
            raise InvalidProposal("unknown parent path")
        identities.add(p["schedule_hash"])
        names.add(p["id"])
        result.append(p)
    return result


def exposure(path: dict, group_ids: list[str]) -> dict[str, Any]:
    rows = path["expanded"]
    counts = {g: sum(g in row for row in rows) for g in group_ids}
    entries, releases = {}, {}
    for g in group_ids:
        flags = [g in row for row in rows]
        entries[g] = sum(
            v and (i == 0 or not flags[i - 1]) for i, v in enumerate(flags)
        )
        releases[g] = sum(not v and flags[i - 1] for i, v in enumerate(flags) if i)
    return {
        "active_steps": counts,
        "empty_steps": sum(not r for r in rows),
        "entries": entries,
        "reentries": {g: max(0, n - 1) for g, n in entries.items()},
        "releases": releases,
    }
