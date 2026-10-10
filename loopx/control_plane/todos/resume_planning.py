"""Compatibility codecs for the typed, read-only Todo resume planning owner."""

from __future__ import annotations

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


def _planning_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = compact_todo_projection_item(
        item, text=str(item.get("text") or "").strip(), extra_fields=("content_revision",)
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
        "available_capabilities": (
            normalize_required_capabilities(available_capabilities)
            if available_capabilities is not None else None
        ),
    }


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
