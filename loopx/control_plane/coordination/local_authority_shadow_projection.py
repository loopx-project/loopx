"""Source projection encoding and transport for local authority capture and parity.

TS owns complete Todo capture assembly; Python retains exact-byte encoding
and source-file identity adaptation. Neither path changes source state. The same complete record contracts and canonical
bytes define the source digest, the outbox partition digest, and the candidate
readback comparison, so no two code paths can disagree about what "the same
coordination state" means.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from collections.abc import Iterable, Mapping
from typing import Any, NoReturn

from .coordination_state_contract import TODO_CANONICAL_READ_RECORD_FIELDS
from .coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA,
    COORDINATION_STATE_CONTRACT,
    COORDINATION_SOURCE_TRANSFER_REQUEST_SCHEMA as TRANSFER_SCHEMA,
    COORDINATION_SOURCE_TRANSFER_RESULT_SCHEMA as TRANSFER_RESULT_SCHEMA,
)


LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA_V0 = LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA
TODO_PARTITION = "todos"
LEASE_PARTITION = "leases"
PARTITIONS: tuple[str, ...] = (TODO_PARTITION, LEASE_PARTITION)

TODO_FIELDS: tuple[str, ...] = TODO_CANONICAL_READ_RECORD_FIELDS


class ProjectionValueError(ValueError):
    """A value cannot be part of a canonical shadow projection."""


def _reject_floats(value: object, path: str) -> None:
    # Python `1.0` and JavaScript `1` would canonicalize differently, so a
    # float anywhere in a compared projection would manufacture a false
    # divergence between the Python source digest and the TypeScript head.
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
        raise ProjectionValueError(f"integer is outside the cross-runtime safe range ({path})")
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        raise ProjectionValueError(f"float values are not allowed in shadow projections ({path})")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProjectionValueError(f"non-string key in shadow projection ({path})")
            _reject_floats(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_floats(item, f"{path}[{index}]")
        return
    raise ProjectionValueError(
        f"unsupported value type {type(value).__name__} in shadow projection ({path})"
    )


def canonical_bytes(value: object) -> bytes:
    """Sorted-key, minimal-separator UTF-8 JSON; floats and NaN are rejected."""

    _reject_floats(value, "$")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_value(value: object) -> Any:
    """Round-trip a value through canonical JSON so key order is normalized."""

    return json.loads(canonical_bytes(value))


def sha256_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def text_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


MAX_TRANSFER_BYTES: int = COORDINATION_STATE_CONTRACT["source_transfer_limits"]["max_bytes"]


def source_effect_runtime_result(method: str, request: dict[str, Any], **kwargs: Any) -> Any:
    """Keep RPC envelopes small without truncating a source or its result."""
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    def reject(message: str) -> NoReturn:
        raise EffectRuntimeRejected(message, diagnostic_code="coordination_source_transfer_invalid")

    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_TRANSFER_BYTES:
        reject("coordination source transfer exceeds the 16 MiB artifact limit")
    request_digest = hashlib.sha256(encoded).hexdigest()
    # A timed-out TS handler can still hold its output open on Windows. Preserve
    # the ambiguous-operation error even when the OS cannot yet unlink that file.
    with tempfile.TemporaryDirectory(prefix="loopx-coordination-", ignore_cleanup_errors=True) as temporary:
        directory = Path(temporary).resolve()
        source = directory / "request.json"
        with source.open("xb") as writer:
            writer.write(encoded)
        result = effect_runtime_result(method, {
            "schema_version": TRANSFER_SCHEMA,
            "method": method,
            "directory": str(directory),
            "request_sha256": request_digest,
            "request_bytes": len(encoded),
        }, **kwargs)
        if (
            not isinstance(result, dict)
            or set(result) != {"schema_version", "method", "request_sha256", "result_sha256", "result_bytes"}
            or result.get("schema_version") != TRANSFER_RESULT_SCHEMA
            or result.get("method") != method
            or result.get("request_sha256") != request_digest
            or type(result.get("result_bytes")) is not int
            or not 0 < result["result_bytes"] <= MAX_TRANSFER_BYTES
        ):
            reject("coordination source transfer result does not match its request")
        target = directory / "result.json"
        before = target.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != result["result_bytes"]:
            reject("coordination source transfer result is not the witnessed regular file")
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                reject("coordination source transfer result changed before readback")
            data = handle.read(MAX_TRANSFER_BYTES + 1)
        if len(data) != result["result_bytes"] or hashlib.sha256(data).hexdigest() != result["result_sha256"]:
            reject("coordination source transfer result digest mismatch")
        return json.loads(data)


def project_coordination_source(request: dict[str, Any]) -> dict[str, Any]:
    """One bounded call for a complete capture, never one call per record."""
    from ..effect_runtime import EffectRuntimeRejected

    _reject_floats(request, "$")
    try:
        result = source_effect_runtime_result("coordination.source.project", {
            "schema_version": "coordination_source_projection_request_v0", **request,
        })
    except EffectRuntimeRejected as error:
        raise ProjectionValueError(str(error)) from error
    if (not isinstance(result, dict)
        or result.get("schema_version") != "coordination_source_projection_result_v0"):
        raise ProjectionValueError("invalid coordination source projection result")
    projection = result.get("projection")
    if not isinstance(projection, dict):
        raise ProjectionValueError("invalid coordination source projection result")
    return projection


def compact_lease(raw: object, *, goal_id: str, file_stem: str) -> dict[str, Any]:
    """Retain the complete versioned lease record after binding its identity."""

    if not isinstance(raw, Mapping):
        raise ProjectionValueError("task lease must contain an object")
    if raw.get("goal_id") != goal_id or raw.get("todo_id") != file_stem:
        raise ProjectionValueError("task lease identity does not match its shadow source")
    return dict(canonical_value(dict(raw)))


def todo_partition_projection(
    *,
    handoff_mode: str,
    todos: Iterable[object],
) -> dict[str, Any]:
    """The state guarded by the goal's active-state file lock."""

    return project_coordination_source({
        "kind": "todo_partition", "handoff_mode": handoff_mode, "todos": list(todos),
    })


def _stable_todos(value: object) -> object:
    """Remove only query-clock observations from Todo authority identity."""

    if not isinstance(value, list):
        return value
    stable_todos: list[object] = []
    for item in value:
        if not isinstance(item, Mapping):
            stable_todos.append(item)
            continue
        todo = dict(item)
        condition = todo.get("resume_condition")
        if isinstance(condition, Mapping):
            stable_condition = dict(condition)
            stable_condition.pop("evaluated_at", None)
            todo["resume_condition"] = stable_condition
        stable_todos.append(todo)
    return stable_todos


def partition_comparison_view(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Stable partition identity shared by capture and TS continuity checks.

    The complete prepared projection remains byte-verified separately. Only
    the read-time resume evaluation clock is absent from this semantic digest.
    """

    view = dict(projection)
    if "todos" in view:
        view["todos"] = _stable_todos(view.get("todos"))
    return view


def partition_digest(projection: Mapping[str, Any]) -> str:
    return sha256_digest(partition_comparison_view(projection))


def head_comparison_view(head: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a candidate head that parity compares against the source."""

    return {
        "handoff_mode": head.get("handoff_mode"),
        "todos": _stable_todos(head.get("todos")),
        "leases": head.get("leases"),
    }


def head_digest(head: Mapping[str, Any]) -> str:
    return sha256_digest(head_comparison_view(head))


__all__ = [
    "LEASE_PARTITION",
    "LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA_V0",
    "PARTITIONS",
    "TODO_FIELDS",
    "TODO_PARTITION",
    "ProjectionValueError",
    "canonical_bytes",
    "canonical_value",
    "compact_lease",
    "head_comparison_view",
    "head_digest",
    "partition_comparison_view",
    "partition_digest",
    "sha256_digest",
    "text_digest",
    "todo_partition_projection",
]
