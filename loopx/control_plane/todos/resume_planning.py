"""Compatibility codecs for the typed, read-only Todo resume planning owner."""

from __future__ import annotations

import json
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from .contract import (
    normalize_required_capabilities,
    normalize_todo_claimed_by, normalize_todo_id,
    normalize_todo_resume_when,
    normalize_todo_status, normalize_todo_task_class, normalize_todo_excluded_agents,
)
from .compact_projection import compact_todo_projection_item
from .todo_semantics import todo_projection_sort_key

_SOURCE_KEYS = (
    "items", "backlog_items", "first_open_items", "deferred_items",
    "deferred_resume_candidates", "resume_blocked_items", "monitor_open_items",
    "current_agent_claimed_monitor_items", "claimed_monitor_open_items",
)

_REFERENCE_REQUEST_SCHEMA = "todo_resume_planning_request_v1"
_PAYLOAD_REFERENCE_KEY = "_loopx_resume_record_ref"
# These are the only payload facts read by resume_planning.ts and the shared
# resume-condition evaluator.  Full compact payloads remain in Python and are
# restored after the typed projection returns.
_REFERENCE_PAYLOAD_FIELDS = (
    "index", "text", "todo_id", "role", "status", "archive_state",
    "source_section", "task_class", "claimed_by", "task_repository",
    "next_due_at", "expires_at", "resume_when", "resume_monitor_generation",
    "material_change_generation", "resume_condition", "resume_ready",
    "blocked_retry",
)


def _planning_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = compact_todo_projection_item(
        item, text=str(item.get("text") or "").strip(), extra_fields=("content_revision", "blocked_retry")
    )
    condition = item.get("resume_condition")
    condition = condition if isinstance(condition, dict) else {}
    priority, index = todo_projection_sort_key(payload)
    ready = item.get("resume_ready")
    return {
        "payload": payload, "id": normalize_todo_id(item.get("todo_id")),
        "status": normalize_todo_status(item.get("status")),
        "claim": normalize_todo_claimed_by(item.get("claimed_by")),
        "excluded": normalize_todo_excluded_agents(item.get("excluded_agents")),
        "resume": normalize_todo_resume_when(item.get("resume_when")),
        "done": item.get("done") is True,
        "ready": ready if isinstance(ready, bool) else None,
        "ready_truthy": bool(ready), "priority": priority, "index": index,
        "target_id": normalize_todo_id(condition.get("target_todo_id") or condition.get("target")),
        "target_status": normalize_todo_status(condition.get("target_status")),
        "target_class": normalize_todo_task_class(condition.get("target_task_class"), text=""),
    }


def _planning_sources(value: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    encoded: dict[int, dict[str, Any]] = {}

    def encode(item: Any) -> dict[str, Any]:
        # Ignored rows still make an explicit lane nonempty; dropping them
        # here could activate the legacy fallback to a different source.
        identity = id(item) if isinstance(item, dict) else -1
        if identity not in encoded:
            encoded[identity] = _planning_item(item if isinstance(item, dict) else {})
        return encoded[identity]

    sources = {}
    for key in _SOURCE_KEYS:
        raw = value.get(key)
        sources[key] = [encode(item) for item in raw] if isinstance(raw, list) else []
    return sources


def build_todo_resume_planning_request(
    value: Any, *, agent_id: str | None = None, item_limit: int = 5,
    available_capabilities: Any = None,
) -> dict[str, Any]:
    """Encode legacy input facts for the typed planning owner."""
    value = value if isinstance(value, dict) else {}
    return {
        "schema_version": "todo_resume_planning_request_v0",
        "sources": _planning_sources(value),
        "agent_id": normalize_todo_claimed_by(agent_id), "item_limit": item_limit,
        "has_deferred_count": "deferred_count" in value,
        "has_visible_deferred_count": bool(value.get("deferred_count")),
        "deferred_count": value.get("deferred_count"),
        "deferred_work_counts": value.get("deferred_work_counts"),
        "available_capabilities": (
            normalize_required_capabilities(available_capabilities)
            if available_capabilities is not None else None
        ),
    }


def build_referenced_todo_resume_planning_request(
    value: Any, *, agent_id: str | None = None, item_limit: int = 5,
    available_capabilities: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Encode one semantic snapshot without repeating full payloads per lane.

    The ordinary v0 builder remains the compatibility contract for callers that
    inspect or rewrite its source rows.  Quota planning uses this referenced v1
    wire and rehydrates the typed result from the returned payload table.
    """
    request = build_todo_resume_planning_request(
        value,
        agent_id=agent_id,
        item_limit=item_limit,
        available_capabilities=available_capabilities,
    )
    records: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    references: dict[str, int] = {}
    sources: dict[str, list[int]] = {}
    for source_name, source_rows in request["sources"].items():
        source_refs: list[int] = []
        for row in source_rows:
            # Equal normalized records are semantically interchangeable.  Use
            # their exact JSON form instead of Todo id alone so conflicting
            # historical facts remain separate and are still diagnosed.
            identity = json.dumps(
                row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            reference = references.get(identity)
            if reference is None:
                reference = len(records)
                references[identity] = reference
                payload = dict(row.get("payload") or {})
                payloads.append(payload)
                wire_payload = {
                    key: payload[key]
                    for key in _REFERENCE_PAYLOAD_FIELDS
                    if key in payload
                }
                wire_payload[_PAYLOAD_REFERENCE_KEY] = reference
                records.append({**row, "payload": wire_payload})
            source_refs.append(reference)
        sources[source_name] = source_refs
    return (
        {
            **request,
            "schema_version": _REFERENCE_REQUEST_SCHEMA,
            "sources": sources,
            "records": records,
        },
        payloads,
    )


def rehydrate_todo_resume_planning_result(
    value: Any, payloads: list[dict[str, Any]],
) -> Any:
    """Replace private wire references with their complete compact payloads."""
    if isinstance(value, list):
        return [rehydrate_todo_resume_planning_result(item, payloads) for item in value]
    if not isinstance(value, dict):
        return value
    projected = {
        key: rehydrate_todo_resume_planning_result(item, payloads)
        for key, item in value.items()
        if key != _PAYLOAD_REFERENCE_KEY
    }
    reference = value.get(_PAYLOAD_REFERENCE_KEY)
    if reference is None:
        return projected
    if type(reference) is not int or not 0 <= reference < len(payloads):
        raise RuntimeError("TypeScript Todo resume planning record reference is invalid")
    return {**payloads[reference], **projected}


def project_todo_resume_planning(
    value: Any, *, agent_id: str | None = None, item_limit: int = 5,
    available_capabilities: Any = None,
) -> dict[str, Any]:
    """Project one snapshot; no business write or additional authority is granted."""
    try:
        result = effect_runtime_result("todo.resume_planning.project",
            build_todo_resume_planning_request(value, agent_id=agent_id,
                item_limit=item_limit, available_capabilities=available_capabilities))
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, dict) or result.get("schema_version") != "todo_resume_planning_v0":
        raise RuntimeError("TypeScript Todo resume planning shape mismatch")
    return result


def read_todo_deferred_work_counts(value: Any) -> dict[str, Any]:
    """Decode source counts; only historical nonempty inputs need a typed projection."""
    if not isinstance(value, dict) or not value:
        return {"ready": 0, "blocked": 0, "unknown": None, "complete": False,
                "ready_by_agent": {}}
    counts = value.get("deferred_work_counts")
    if counts is None:
        counts = project_todo_resume_planning(value)["deferred_lanes"]["deferred_work_counts"]

    def count(raw: Any) -> bool:
        # Match the co-deployed TypeScript Number.isSafeInteger wire decoder.
        return type(raw) is int and 0 <= raw <= 2**53 - 1

    if (not isinstance(counts, dict)
        or "unknown" not in counts
        or not all(count(counts.get(key)) for key in ("ready", "blocked"))
        or counts.get("unknown") is not None and not count(counts.get("unknown"))
        or type(counts.get("complete")) is not bool
        or counts["complete"] and counts.get("unknown") != 0):
        raise ValueError("invalid deferred work counts")
    claims = counts.get("ready_by_agent")
    if (not isinstance(claims, dict)
        or any(not isinstance(agent, str) or not agent or not count(total)
               for agent, total in claims.items())
        or sum(claims.values()) > counts["ready"]):
        raise ValueError("invalid deferred ready claimant counts")
    total = value.get("deferred_count")
    if (count(total) and counts.get("unknown") is not None
        and counts["ready"] + counts["blocked"] + counts["unknown"] != total):
        raise ValueError("deferred work counts disagree with the source total")
    return {**counts, "ready_by_agent": dict(claims)}
