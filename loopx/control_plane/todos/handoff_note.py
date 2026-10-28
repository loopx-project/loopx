"""Transport/materialization for the shared TS handoff read model.

Common Todo metadata codecs stay with their existing owner. Python's historical
str(tuple) digest is wire identity encoding; moving it would change legacy IDs.
No fallback decision kernel runs when the typed owner is unavailable.
"""
from __future__ import annotations

import json
import math
from typing import Any

TODO_HANDOFF_NOTE_SCHEMA_VERSION = "handoff_note_v0"
TODO_CONTINUATION_HINT_MAX_CHARS = 280


def todo_read_source(item: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Carry raw values plus Python-only string/repr facts, without field rules."""
    facts: list[Any] = []

    def encode(value: Any, path: list[Any]) -> Any:
        if isinstance(value, dict):
            result = {str(key): encode(val, [*path, str(key)]) for key, val in value.items()}
        elif isinstance(value, (list, tuple, set)):
            result = [encode(val, [*path, index]) for index, val in enumerate(value)]
        elif value is None or isinstance(value, (str, bool, int, float)):
            result = str(value) if isinstance(value, float) and not math.isfinite(value) else value
        else:
            result = str(value or "")
        if isinstance(value, (tuple, set, float)) or not isinstance(value, (dict, list, str, bool, int, type(None))):
            facts.append([path, str(value or ""), repr(value)])
        return result

    record = encode(item, [])
    return {"record": record, **({"python_values": facts} if facts else {}), **overrides}


def validate_handoff_context(value: Any, count: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != count or any(
        not isinstance(row, dict) or not isinstance(row.get("continuation_hint"), (str, type(None)))
        or not (row.get("note") is None or isinstance(row.get("note"), dict)
                and row["note"].get("schema_version") == TODO_HANDOFF_NOTE_SCHEMA_VERSION)
        for row in value
    ):
        raise ValueError("invalid typed Todo handoff context")
    return value


def project_handoff_context(sources: list[dict[str, Any]], *,
                            followups: list[dict[str, Any] | None] | None = None) -> list[dict[str, Any]]:
    from ..effect_runtime import MAX_REQUEST_BYTES, EffectRuntimeRejected, effect_runtime_result

    if followups is not None and len(followups) != len(sources):
        raise ValueError("handoff followup cardinality mismatch")
    results: list[dict[str, Any]] = []
    # This lens is row-independent: byte-bounded transport batches preserve
    # every source and ordering. It never chunks dependency/closure decisions.
    start, size = 0, 0
    for index in range(len(sources) + 1):
        row_size = 0 if index == len(sources) else len(json.dumps(sources[index], separators=(",", ":")).encode()) + 2
        if followups is not None and index < len(sources):
            row_size += len(json.dumps(followups[index], separators=(",", ":")).encode()) + 2
        if index > start and (index == len(sources) or size + row_size > MAX_REQUEST_BYTES - 4096):
            try:
                result = effect_runtime_result("todo.context.page", {"handoff_sources": sources[start:index],
                    **({"handoff_followups": followups[start:index]} if followups is not None else {})})
            except EffectRuntimeRejected as error:
                raise ValueError(str(error)) from error
            results.extend(validate_handoff_context(result.get("handoff_context") if isinstance(result, dict) else None, index - start))
            start, size = index, 0
        if row_size > MAX_REQUEST_BYTES - 4096:
            # Historical source text can exceed one ordinary frame. Reuse the
            # existing private snapshot transport; do not truncate before the
            # typed credential exclusion or add a Python fallback decision.
            try:
                result = effect_runtime_result("todo.context.page", {"handoff_sources": sources[index:index + 1],
                    **({"handoff_followups": followups[index:index + 1]} if followups is not None else {})},
                    large_local_snapshot=True)
            except EffectRuntimeRejected as error:
                raise ValueError(str(error)) from error
            results.extend(validate_handoff_context(result.get("handoff_context") if isinstance(result, dict) else None, 1))
            start, size = index + 1, 0
            continue
        size += row_size
    return results


def compact_todo_continuation_hint(item: dict[str, Any]) -> str | None:
    return project_handoff_context([todo_read_source(item)])[0]["continuation_hint"]


def build_todo_handoff_note(item: dict[str, Any], *, goal_id: str | None = None,
                            source: str | None = None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return project_handoff_context([todo_read_source(item, goal_id=goal_id, source=source)])[0]["note"]


def attach_todo_handoff_note(item: dict[str, Any], *, goal_id: str | None = None,
                             source: str | None = None) -> dict[str, Any]:
    note = build_todo_handoff_note(item, goal_id=goal_id, source=source)
    if note:
        item["handoff_note"] = note
    return item


def attach_todo_handoff_notes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = project_handoff_context([todo_read_source(item) for item in items])
    for item, result in zip(items, results, strict=True):
        if result["note"]:
            item["handoff_note"] = result["note"]
    return items
