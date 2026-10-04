"""Adapt the same-read public Todo projection to the typed attention owner."""
from collections.abc import Mapping
from typing import Any
from pathlib import Path

from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.quota.blocked_transition_notice import collect_blocked_transition_notices
from ...control_plane.runtime.public_safety import public_safe_compact_text, validate_public_safe_value


def notice_request_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    """Filter complete fields before the TS notice owner applies its bounds."""
    fields = {key: item[key] for key in (
        "todo_id", "goal_id", "role", "status", "task_class", "updated_at",
        "done", "superseded_by",
    ) if key in item}
    for key, value in (("text", item.get("text")),
                       ("note", item.get("note") or item.get("reason")),
                       ("evidence", item.get("evidence"))):
        fields[key] = public_safe_compact_text(
            value, normalize_text=lambda value, **_: " ".join(value.strip().split()),
        )
        if value and fields[key] is None:
            fields["content_redacted"] = True
    return fields


def project_goal_attention(
    status: Mapping[str, Any], goal_id: str, quota_packet: Mapping[str, Any],
    *, fallback_assessed: bool = True,
) -> dict[str, Any]:
    queue = status.get("attention_queue") or {}
    matches = [row for row in queue.get("items", [])
               if isinstance(row, Mapping) and row.get("goal_id") == goal_id]
    if status.get("ok") is not True or len(matches) != 1:
        return {"status": "unavailable", "items": [],
                "reason": "attention_source_missing_or_ambiguous"}
    blockers, _ = collect_blocked_transition_notices(
        status, goal_id, quota_packet, fallback_assessed=fallback_assessed,
    )
    raw = (matches[0].get("user_todos") or {}).get("items", [])
    prepared = {
        "goal_id": goal_id, "blockers": blockers,
        "todos": [notice_request_fields(item) for item in raw if isinstance(item, Mapping)],
    }
    try:
        validate_public_safe_value(prepared)
    except ValueError:
        return {"status": "unavailable", "items": [], "reason": "content_redacted"}
    result = dict(effect_runtime_result("presentation.goal_attention.project", prepared))
    return {"status": "read", **result}


def read_goal_attention(
    registry_path: Path, runtime_root: Path, goal_id: str, quota_packet: Mapping[str, Any],
) -> dict[str, Any]:
    """A current canonical Todo read is independent of stale/absent run history."""
    from ...todos import list_goal_todos

    try:
        listed = list_goal_todos(registry_path=registry_path,
            runtime_root_arg=str(runtime_root), goal_id=goal_id)
        if listed.get("ok") is not True:
            raise ValueError("Todo source unavailable")
        records = listed.get("todos", [])
        current = {"ok": True, "attention_queue": {"items": [{
            "goal_id": goal_id,
            "user_todos": {"items": [r for r in records if r.get("role") == "user"]},
            "agent_todos": {"items": [r for r in records if r.get("role") == "agent"]},
        }]}}
        result = project_goal_attention(current, goal_id, quota_packet,
            fallback_assessed=bool(quota_packet.get("blocked_priority_fallback")))
        return {**result, "source": listed.get("source"),
                "authority_revision": (listed.get("authority_read") or {}).get("provider_revision")}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {"status": "unavailable", "items": [], "reason": "todo_source_unavailable"}
