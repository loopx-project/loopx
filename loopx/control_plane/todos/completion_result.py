"""Local, content-addressed output bytes for accepted Todo completions.

The canonical Todo owns the binding. This provider owns bytes only and never
turns a file's existence into evidence of completion or acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any
from ..content_digest import BARE_SHA256_PATTERN

MAX_RESULT_BYTES = 128_000
_CONTENT_TYPES = {".json": "application/json", ".md": "text/markdown", ".txt": "text/plain"}
_DIGEST = BARE_SHA256_PATTERN


def _object_path(runtime_root: Path, goal_id: str, digest: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", goal_id) or not _DIGEST.fullmatch(digest):
        raise ValueError("invalid completion result identity")
    return runtime_root / "goals" / goal_id / "result-objects" / digest


def _read_regular(path: Path) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                            getattr(os, "O_NONBLOCK", 0)), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("completion result must be a regular file")
        data = stream.read(MAX_RESULT_BYTES + 1)
    if not data or len(data) > MAX_RESULT_BYTES:
        raise ValueError("completion result must contain 1..128000 bytes")
    data.decode("utf-8")
    return data


def _install_object(target: Path, data: bytes) -> None:
    """Install exact content-addressed bytes with one atomic replace.

    A digest path can only ever hold the bytes it names, so a pre-existing
    regular file that does not match is the artifact of an interrupted store
    (or a corrupt entry) rather than a competing object. It is replaced by the
    complete staged bytes instead of failing the retry with a partial file on
    disk. ``os.replace`` installs the staged name without following a link, so
    an existing symlink is replaced rather than written through.
    """
    try:
        if _read_regular(target) == data:
            return
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        pass
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def store_completion_result(*, source: Path, runtime_root: Path, goal_id: str,
                            persist: bool = True) -> dict[str, Any]:
    """Stage exact local bytes before the canonical completion transaction."""
    content_type = _CONTENT_TYPES.get(source.suffix.lower())
    if content_type is None:
        raise ValueError("completion result supports .json, .md or .txt")
    data = _read_regular(source)
    if content_type == "application/json":
        json.loads(data)
    digest = hashlib.sha256(data).hexdigest()
    if persist:
        target = _object_path(runtime_root, goal_id, digest)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _install_object(target, data)
    return {"provider": "local_runtime_v0", "sha256": digest,
            "size_bytes": len(data), "content_type": content_type}


def read_completion_result(*, registry_path: Path, runtime_root: Path,
                           goal_id: str, todo_id: str) -> dict[str, Any]:
    """Exact owner-local read; absence, stale acceptance and byte drift fail closed."""
    from ..goals.acceptance import inspect_goal_acceptance
    from ...control_plane.todos.list_readback import list_goal_todos

    todos = list_goal_todos(registry_path=registry_path, goal_id=goal_id,
                            todo_id=todo_id, runtime_root_arg=str(runtime_root))
    todo = todos.get("todo")
    if not isinstance(todo, dict) or todo.get("status") != "done" or todo.get("done") is not True:
        raise ValueError("completion result requires a current completed Todo")
    binding = todo.get("completion_result")
    if not isinstance(binding, dict) or binding.get("schema_version") != "loopx_completion_result_v0":
        raise ValueError("Todo has no accepted completion result")
    basis = inspect_goal_acceptance(registry_path=registry_path, goal_id=goal_id,
                                    runtime_root=str(runtime_root))
    if todos.get("authority_read", {}).get("provider_revision") != basis.get("provider_revision"):
        raise ValueError("completion result canonical snapshot changed; retry readback")
    contract = basis.get("goal_acceptance_contract")
    if (not isinstance(contract, dict) or contract.get("enabled") is not True or
            contract.get("digest") != binding.get("acceptance_contract_digest") or
            contract.get("revision") != binding.get("acceptance_contract_revision")):
        raise ValueError("completion result acceptance basis is stale")
    if binding.get("todo_id") != todo_id or binding.get("producer_agent_id") != todo.get("last_actor_agent_id"):
        raise ValueError("completion result producer or Todo identity changed")
    digest = binding.get("sha256")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError("completion result digest is invalid")
    data = _read_regular(_object_path(runtime_root, goal_id, digest))
    if hashlib.sha256(data).hexdigest() != digest or len(data) != binding.get("size_bytes"):
        raise ValueError("completion result bytes no longer match canonical binding")
    return {"ok": True, "goal_id": goal_id, "todo_id": todo_id,
            "result": binding, "text": data.decode("utf-8"), "audience": "local_operator"}
