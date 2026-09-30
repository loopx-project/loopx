"""Private local store for provider-first Todo validation declarations."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ...registry import atomic_write_json, read_json
from ...file_lock import exclusive_cross_runtime_file_lock
from .active_state_editing import fsync_state_directory
from .completion_validation_projection import (
    completion_validation_declaration,
    completion_validation_declaration_sha256,
)
from ..content_digest import BARE_SHA256_PATTERN


DECLARATION_SCHEMA_VERSION = "loopx_todo_completion_validation_declaration_v0"
_PUBLIC_ID = re.compile(r"[A-Za-z0-9_.:-]+")


def _require_public_id(value: str, label: str) -> str:
    if not _PUBLIC_ID.fullmatch(value):
        raise ValueError(f"{label} must be a public-safe token")
    return value


def completion_validation_declaration_path(
    *, runtime_root: Path, goal_id: str, todo_id: str
) -> Path:
    return (
        runtime_root.expanduser().resolve(strict=False)
        / "goals"
        / _require_public_id(goal_id, "goal_id")
        / "todo-validation-declarations"
        / f"{_require_public_id(todo_id, 'todo_id')}.json"
    )


def persist_completion_validation_declaration(
    *,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    declaration: Mapping[str, Any],
) -> str:
    normalized = completion_validation_declaration(dict(declaration))
    if normalized is None:
        raise ValueError("completion validation declaration is empty")
    digest = prepare_completion_validation_declaration(
        runtime_root=runtime_root, goal_id=goal_id, declaration=normalized
    )
    path = completion_validation_declaration_path(
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
    )
    atomic_write_json(
        path,
        {
            "schema_version": DECLARATION_SCHEMA_VERSION,
            "goal_id": goal_id,
            "todo_id": todo_id,
            "declaration_sha256": digest,
            "declaration": normalized,
        },
        preserve_mode=True,
    )
    os.chmod(path, 0o600)
    return digest


def prepare_completion_validation_declaration(
    *, runtime_root: Path, goal_id: str, declaration: Mapping[str, Any],
) -> str:
    """Persist immutable private content before a canonical digest can reference it.

    A prepared blob grants no Todo authority. Only a canonical record selecting
    its exact digest can consume it. Rejected creates may leave unreferenced blobs.
    """
    normalized = completion_validation_declaration(dict(declaration))
    if normalized is None:
        raise ValueError("completion validation declaration is empty")
    digest = completion_validation_declaration_sha256(normalized)
    path = completion_validation_declaration_path(
        runtime_root=runtime_root, goal_id=goal_id, todo_id="blobs",
    ).parent / "blobs" / f"{digest}.json"
    payload = {"schema_version": "loopx_todo_validation_blob_v0", "goal_id": goal_id,
               "declaration_sha256": digest, "declaration": normalized}
    with exclusive_cross_runtime_file_lock(path, operation="prepare_validation_declaration"):
        if path.exists():
            if read_json(path) != payload:
                raise ValueError("prepared validation declaration digest mismatch")
        else:
            atomic_write_json(path, payload)
        # Also re-establish directory durability on an idempotent retry.
        fsync_state_directory(path)
    return digest


def _read_prepared_declaration(path: Path, goal_id: str, digest: str) -> dict[str, Any] | None:
    if not BARE_SHA256_PATTERN.fullmatch(digest):
        raise ValueError("canonical validation digest must be SHA-256")
    try:
        value = read_json(path.parent / "blobs" / f"{digest}.json")
    except FileNotFoundError:
        return None
    if not isinstance(value, Mapping) or not isinstance(value.get("declaration"), dict):
        raise ValueError("prepared validation declaration is malformed")
    declaration = completion_validation_declaration(value["declaration"])
    if (value.get("schema_version") != "loopx_todo_validation_blob_v0"
            or value.get("goal_id") != goal_id or value.get("declaration_sha256") != digest
            or declaration is None or completion_validation_declaration_sha256(declaration) != digest):
        raise ValueError("prepared validation declaration digest or identity mismatch")
    return declaration


def read_completion_validation_declaration(
    *, runtime_root: Path, goal_id: str, todo_id: str,
    expected_digest: str | None = None,
) -> dict[str, Any] | None:
    path = completion_validation_declaration_path(
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
    )
    if expected_digest is not None:
        prepared = _read_prepared_declaration(path, goal_id, expected_digest)
        if prepared is not None:
            return prepared
    try:
        value = read_json(path)
    except FileNotFoundError:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("completion validation declaration store is not an object")
    declaration = value.get("declaration")
    if (
        value.get("schema_version") != DECLARATION_SCHEMA_VERSION
        or value.get("goal_id") != goal_id
        or value.get("todo_id") != todo_id
        or not isinstance(declaration, Mapping)
    ):
        raise ValueError("completion validation declaration store identity mismatch")
    normalized = completion_validation_declaration(dict(declaration))
    if normalized is None:
        raise ValueError("completion validation declaration store is empty")
    digest = completion_validation_declaration_sha256(normalized)
    if value.get("declaration_sha256") != digest:
        raise ValueError("completion validation declaration store digest mismatch")
    if expected_digest is not None and digest != expected_digest:
        raise ValueError("private completion validation declaration does not match canonical Todo digest")
    return normalized


def load_completion_validation_declarations(
    *,
    runtime_root: Path,
    goal_id: str,
    todos: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for todo in todos:
        if todo.get("completion_validation_required") is not True:
            continue
        todo_id = str(todo.get("todo_id") or "")
        declaration = read_completion_validation_declaration(
            runtime_root=runtime_root,
            goal_id=goal_id,
            todo_id=todo_id,
            expected_digest=str(todo.get("completion_validation_sha256") or ""),
        )
        if declaration is not None:
            loaded[todo_id] = declaration
    return loaded


__all__ = [
    "DECLARATION_SCHEMA_VERSION",
    "completion_validation_declaration_path",
    "load_completion_validation_declarations",
    "persist_completion_validation_declaration",
    "prepare_completion_validation_declaration",
    "read_completion_validation_declaration",
]
