from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..todos.contract import normalize_todo_id
from ..todos.handoff_note import compact_todo_continuation_hint
from ..todos.summary_item import todo_text_content_revision
from ..work_items.primary_action import protocol_action_text

SELECTED_TODO_COMPACT_FIELDS = (
    "todo_id",
    "index",
    "role",
    "priority",
    "status",
    "task_class",
    "action_kind",
    "task_domain",
    "capability_binding_ref",
    "task_repository",
    "continuation_policy",
    "required_capabilities",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "excluded_agents",
    "unblocks_todo_id",
    "resume_when",
    "resume_ready",
    "resume_condition",
    "target_key",
    "next_due_at",
    "expires_at",
    "content_revision",
)
SELECTED_TODO_AGENT_FIELDS = (
    "agent_id",
    "selected_by",
    "confidence",
    "claim_required_before_work",
    "selection_binding",
    "selection_reason",
    "delivery_boundary",
)
WORK_LANE_SELECTED_TODO_ITEM_FIELDS = (
    "monitor_due_items",
    "monitor_schedule_gap_items",
    "resume_blocked_by_monitor_items",
)


def selected_todo_projection(
    *,
    agent_lane_next_action: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
    agent_scope_frontier: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if (
        isinstance(agent_scope_frontier, dict)
        and agent_scope_frontier.get("action") == "successor_replan_required"
    ):
        candidates = agent_scope_frontier.get("deferred_resume_candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            selected = _compact_selected_todo(
                candidates[0],
                source="agent_scope_frontier.deferred_resume_candidates",
            )
            if selected:
                return selected

    if isinstance(agent_lane_next_action, dict):
        source = str(agent_lane_next_action.get("source") or "agent_lane_next_action")
        selected = _compact_selected_todo(agent_lane_next_action, source=source)
        if selected:
            return selected

    if not isinstance(work_lane_contract, dict):
        return None
    selected_todo_id = normalize_todo_id(work_lane_contract.get("selected_todo_id"))
    if not selected_todo_id:
        return None
    for key in WORK_LANE_SELECTED_TODO_ITEM_FIELDS:
        items = work_lane_contract.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_todo_id = normalize_todo_id(item.get("todo_id") or item.get("id"))
            if item_todo_id != selected_todo_id:
                continue
            selected = _compact_selected_todo(item, source=f"work_lane_contract.{key}")
            if selected:
                return selected
    return {
        "schema_version": "quota_selected_todo_v0",
        "source": "work_lane_contract.selected_todo_id",
        "todo_id": selected_todo_id,
    }


def _compact_selected_todo(
    item: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any] | None:
    todo_id = normalize_todo_id(item.get("todo_id") or item.get("id"))
    text = protocol_action_text(item.get("text"), limit=320)
    if not todo_id and not text:
        return None
    selected: dict[str, Any] = {
        "schema_version": "quota_selected_todo_v0",
        "source": source,
    }
    for key in SELECTED_TODO_COMPACT_FIELDS:
        value = item.get(key)
        if value is not None:
            selected[key] = value
    if todo_id:
        selected["todo_id"] = todo_id
    if text:
        selected["text"] = text
        selected["_context_text_display_only"] = True
    snapshot = item.get("_context_text_sha256")
    if isinstance(snapshot, str):
        selected["_context_text_sha256"] = snapshot
    for key in SELECTED_TODO_AGENT_FIELDS:
        if item.get(key) is not None:
            selected[key] = item.get(key)
    content_revision = item.get("content_revision")
    if not content_revision:
        # Deferred-resume candidates can reach this boundary without the
        # summary metadata. Hash their source text before the display copy is
        # compacted so the exact read can validate the selected content.
        content_revision = todo_text_content_revision(item.get("text"))
    if content_revision:
        selected["content_revision"] = content_revision
    if (
        item.get("task_class") == "advancement_task"
        and item.get("status") == "open"
    ):
        continuation_hint = compact_todo_continuation_hint(item)
        if continuation_hint:
            selected["continuation_hint"] = continuation_hint
    return selected


def lookup_raw_todo(raw_summary: object, todo_id: Any) -> dict[str, Any] | None:
    """Return the matching uncompacted Todo dict from a raw agent_todos summary."""
    normalized_target = normalize_todo_id(todo_id)
    if not isinstance(raw_summary, Mapping) or not normalized_target:
        return None
    for value in raw_summary.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if (
                isinstance(item, dict)
                and normalize_todo_id(item.get("todo_id")) == normalized_target
            ):
                return item
    authority = raw_summary.get("task_orchestration_authority")
    if isinstance(authority, Mapping):
        candidates = authority.get("candidate_items")
        if isinstance(candidates, list):
            for item in candidates:
                if (
                    isinstance(item, dict)
                    and normalize_todo_id(item.get("todo_id")) == normalized_target
                ):
                    return item
    return None


def raw_agent_todo_summaries_from_status(
    status_payload: Mapping[str, Any] | None,
    *,
    goal_id: str | None = None,
) -> tuple[object, ...]:
    """Extract canonical and project-asset agent_todos mappings for a Goal."""
    if not isinstance(status_payload, Mapping):
        return ()
    queue = status_payload.get("attention_queue")
    items = queue.get("items") if isinstance(queue, Mapping) else None
    if not isinstance(items, list):
        return ()
    target_goal = str(goal_id or "").strip()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if target_goal and str(item.get("goal_id") or "").strip() != target_goal:
            continue
        project_asset = item.get("project_asset")
        return (
            item.get("agent_todos"),
            project_asset.get("agent_todos") if isinstance(project_asset, Mapping) else None,
        )
    return ()


def resolve_selected_todo_write_contract(
    selected_todo: Mapping[str, Any] | None,
    *raw_summaries: object,
) -> dict[str, Any] | None:
    """Return a selected Todo copy enriched with raw write-contract fields."""
    if not isinstance(selected_todo, Mapping):
        return None
    resolved = dict(selected_todo)
    if "required_write_scopes" in resolved:
        return resolved
    todo_id = resolved.get("todo_id")
    for raw_summary in raw_summaries:
        raw_match = lookup_raw_todo(raw_summary, todo_id)
        if not raw_match:
            continue
        for field in (
            "required_write_scopes",
            "task_repository",
            "required_capabilities",
            "continuation_policy",
        ):
            if field not in resolved and field in raw_match:
                resolved[field] = raw_match[field]
        if "required_write_scopes" in resolved:
            break
    return resolved
