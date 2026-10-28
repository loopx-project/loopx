"""Compatibility transport for the single TypeScript Todo display owner."""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from .handoff_note import todo_read_source

TODO_SUMMARY_SOURCE_KEYS = (
    "active_next_action_items",
    "active_next_action_executable_items",
    "first_open_items",
    "backlog_items",
    "unclaimed_priority_open_items",
    "claimed_open_items",
    "claimed_advancement_open_items",
    "claimed_monitor_open_items",
    "monitor_open_items",
    "current_agent_claimed_open_items",
    "current_agent_claimed_advancement_items",
    "current_agent_claimed_monitor_items",
    "blocker_items",
    "resume_blocked_items",
    "monitor_blocked_resume_candidates",
    "current_agent_monitor_blocked_resume_candidates",
    "unclaimed_monitor_blocked_resume_candidates",
    "items",
)

TODO_PLANNING_SOURCE_KEYS = (
    "items",
    "deferred_items",
    "blocker_items",
    "monitor_open_items",
)


def todo_text_content_revision(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    return "sha256:" + sha256(normalized.encode("utf-8")).hexdigest()


def _project(request: dict[str, Any]) -> list[dict[str, Any]]:
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        result = effect_runtime_result("todo.context.page", {"summary_display": request},
                                       large_local_snapshot=True)
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    rows = result.get("items") if isinstance(result, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid typed Todo summary display")
    return rows


def compact_todo_summary_items(
    items: list[dict[str, Any]], *, texts: list[str | None] | None = None, strip_text: bool = False,
) -> list[dict[str, Any]]:
    from ..effect_runtime import MAX_REQUEST_BYTES

    if texts is not None and len(texts) != len(items):
        raise ValueError("summary text cardinality mismatch")
    sources = [todo_read_source(item, **({"text": texts[i]} if texts is not None else {}))
               for i, item in enumerate(items)]
    result: list[dict[str, Any]] = []
    start, size = 0, 0
    for i in range(len(sources) + 1):
        row_size = 0 if i == len(sources) else len(json.dumps(sources[i], separators=(",", ":")).encode()) + 2
        if i > start and (i == len(sources) or size + row_size > MAX_REQUEST_BYTES - 4096):
            rows = _project({"items": sources[start:i], "strip_text": strip_text})
            if len(rows) != i - start:
                raise ValueError("summary display cardinality mismatch")
            result.extend(rows)
            start, size = i, 0
        size += row_size
    return result


def compact_todo_summary_item(item: dict[str, Any], *, text: str | None = None) -> dict[str, Any]:
    return compact_todo_summary_items([item], texts=[text])[0]


def todo_summary_source_items(value: dict[str, Any]) -> list[dict[str, Any]]:
    from .handoff_gate import todo_summary_handoff_gates

    if not any(value.get(key) for key in TODO_SUMMARY_SOURCE_KEYS):
        return []
    return _project({"source": todo_read_source(value), "handoff_gates": todo_summary_handoff_gates(value)})


def todo_planning_source_items(value: dict[str, Any], *, include_terminal: bool = False) -> list[dict[str, Any]]:
    if not any(value.get(key) for key in TODO_PLANNING_SOURCE_KEYS):
        return []
    return _project({"source": todo_read_source(value), "include_terminal": include_terminal})
