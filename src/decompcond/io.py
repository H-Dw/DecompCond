"""Atomic records and content identities. No credentials belong in these records."""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def canonical(obj: Any) -> str:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def file_digest(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class TechnicalFailure(RuntimeError):
    """Infrastructure/evaluator failure: never score as a bad generated molecule."""


class InvalidProposal(ValueError):
    """Invalid complete schedule, including a previously evaluated schedule."""
