"""Transport/materialization for the shared TS handoff read model.

Common Todo metadata codecs stay with their existing owner. Python's historical
str(tuple) digest is wire identity encoding; moving it would change legacy IDs.
No fallback decision kernel runs when the typed owner is unavailable.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .contract import (
    normalize_todo_action_kind, normalize_todo_claimed_by, normalize_todo_decision_scope,
    normalize_todo_excluded_agents, normalize_todo_id, normalize_todo_id_list,
    normalize_todo_required_decision_scopes, normalize_todo_resume_when,
)

TODO_HANDOFF_NOTE_SCHEMA_VERSION = "handoff_note_v0"
TODO_CONTINUATION_HINT_MAX_CHARS = 280
_TEXT_FIELDS = ("continuation_hint", "suggested_next_action", "note", "reason", "summary",
                "intent", "blocked_on", "evidence", "title", "text", "task_class", "goal_id",
                "source", "latest_event_kind")


def _refs(value: Any) -> dict[str, Any]:
    values = [str(raw or "") for raw in value] if isinstance(value, (list, tuple, set)) else []
    return {**({"truthy": True} if value else {}), **({"values": values} if values else {})}


def handoff_context_source(item: dict[str, Any], *, goal_id: str | None = None,
                           source: str | None = None) -> dict[str, Any]:
    """Encode language-native scalar facts; selection/compaction belongs to TS."""
    nested: dict[str, Any] = {}
    nested_meta: dict[str, Any] = {}
    for name in ("handoff", "handoff_note"):
        value = item.get(name)
        row = value if isinstance(value, dict) else {}
        if value:
            nested[name] = {"truthy": True, "fields":
                {key: str(val or "") for key, val in row.items()} if isinstance(value, dict) else None}
        nested_meta[name] = {
            "from_agent": normalize_todo_claimed_by(row.get("from_agent") or item.get("agent_id")),
            "to_agent": normalize_todo_claimed_by(row.get("to_agent")),
            "evidence_refs": _refs(row.get("evidence_refs")),
        }
    todo_id = normalize_todo_id(item.get("todo_id"))
    legacy_id = None if todo_id else "handoff_" + hashlib.sha1(str((
        item.get("goal_id"), item.get("role"), item.get("index"), item.get("text") or item.get("title"),
    )).encode("utf-8")).hexdigest()[:12]
    result = {**nested, "nested_metadata": nested_meta,
        "texts": {key: str(item[key]) for key in _TEXT_FIELDS if item.get(key)},
        "metadata": {"todo_id": todo_id,
            "claimed_by": normalize_todo_claimed_by(item.get("claimed_by")),
            "excluded_agents": normalize_todo_excluded_agents(item.get("excluded_agents")),
            "successor_todo_ids": normalize_todo_id_list(item.get("successor_todo_ids")),
            "unblocks_todo_id": normalize_todo_id(item.get("unblocks_todo_id")),
            "superseded_by": normalize_todo_id(item.get("superseded_by")),
            "action_kind": normalize_todo_action_kind(item.get("action_kind")),
            "resume_when": normalize_todo_resume_when(item.get("resume_when")),
            "required_decision_scopes": normalize_todo_required_decision_scopes(item.get("required_decision_scopes")),
            "decision_scope": normalize_todo_decision_scope(item.get("decision_scope"))},
        "evidence_refs": _refs(item.get("evidence_refs")),
        "has_evidence": bool(item.get("evidence")), "has_note": bool(item.get("note")),
        "goal_id": str(goal_id or ""), "source": str(source or ""), "legacy_id": legacy_id}

    # Omitted empty scalar/list facts have the codec's explicit empty default.
    # Nested text keys are kept, including "", because presence changes priority.
    for name, row in nested_meta.items():
        nested_meta[name] = {key: value for key, value in row.items() if value}
    result["metadata"] = {key: value for key, value in result["metadata"].items() if value}
    result["nested_metadata"] = {key: value for key, value in nested_meta.items() if value}
    return {key: value for key, value in result.items() if value}


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
    return project_handoff_context([handoff_context_source(item)])[0]["continuation_hint"]


def build_todo_handoff_note(item: dict[str, Any], *, goal_id: str | None = None,
                            source: str | None = None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return project_handoff_context([handoff_context_source(item, goal_id=goal_id, source=source)])[0]["note"]


def attach_todo_handoff_note(item: dict[str, Any], *, goal_id: str | None = None,
                             source: str | None = None) -> dict[str, Any]:
    note = build_todo_handoff_note(item, goal_id=goal_id, source=source)
    if note:
        item["handoff_note"] = note
    return item


def attach_todo_handoff_notes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = project_handoff_context([handoff_context_source(item) for item in items])
    for item, result in zip(items, results, strict=True):
        if result["note"]:
            item["handoff_note"] = result["note"]
    return items
