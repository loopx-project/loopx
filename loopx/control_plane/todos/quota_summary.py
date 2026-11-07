from __future__ import annotations

from typing import Any, Callable
from copy import deepcopy


from ..goals.goal_vision_policy import COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD
from ..runtime.time import now_utc
from .quota_selection import project_quota_planning
from .frontier_deadline import todo_summary_frontier_deadline
from .summary_item import (
    todo_planning_source_items,
    todo_summary_source_items,
)

MONITOR_DUE_ITEM_LIMIT = 1
QUOTA_PAYLOAD_ITEM_TEXT_LIMIT = 180
QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT = 2
QUOTA_PAYLOAD_USER_ACTION_ITEM_LIMIT = 3
QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT = 2
QUOTA_PAYLOAD_COMPACTION_SCHEMA_VERSION = "quota_todo_summary_payload_compaction_v0"
AGENT_LANE_STATUS_TODO_COMPACTION_SCHEMA_VERSION = (
    "agent_lane_status_todo_summary_compaction_v1"
)
AGENT_LANE_STATUS_TODO_REFERENCE_SCHEMA_VERSION = (
    "agent_lane_status_todo_reference_v0"
)
QUOTA_PAYLOAD_ITEM_FIELDS = (
    "goal_acceptance_guard",
    "schema_version",
    "index",
    "text",
    "title",
    "todo_id",
    "status",
    "priority",
    "task_class",
    "action_kind",
    "task_domain",
    "decision_scope",
    "required_decision_scopes",
    "task_repository",
    "continuation_policy",
    "required_capabilities",
    "required_write_scopes",
    "missing_capabilities",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "excluded_agents",
    "global_gate",
    "unblocks_todo_id",
    "resume_when",
    "resume_monitor_generation",
    "resume_condition",
    "resume_ready",
    "blocked_retry",
    "blocking_monitor_todo_id",
    "no_followup",
    "successor_todo_ids",
    "completion_continuation",
    "completion_recovery",
    "replan_obligation_id",
    "target_key",
    "cadence",
    "next_due_at",
    "expires_at",
    "watch_only",
    "last_checked_at",
    "result_hash",
    "consecutive_no_change",
    "material_change",
    "material_change_generation",
    "material_change_scope",
    "material_change_scope_generations",
    "max_no_change_before_replan",
    "route_continuation_replan_required",
    "route_continuation_reason",
    "route_id",
    "route_key",
    "completed_at",
    "updated_at",
    "gate_state",
    "reason",
)
QUOTA_PAYLOAD_LANE_LIMITS = {
    "monitor_due_items": MONITOR_DUE_ITEM_LIMIT,
    "watch_only_monitor_due_items": MONITOR_DUE_ITEM_LIMIT,
    "non_watch_only_monitor_due_items": MONITOR_DUE_ITEM_LIMIT,
    "monitor_capability_blocked_due_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "monitor_schedule_gap_items": MONITOR_DUE_ITEM_LIMIT,
    "first_open_items": 3,
    "first_executable_items": 3,
    "active_next_action_items": 3,
    "active_next_action_executable_items": 3,
    "monitor_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "backlog_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "executable_backlog_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "unclaimed_priority_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "claimed_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "claimed_advancement_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "claimed_monitor_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "current_agent_claimed_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "current_agent_claimed_advancement_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "current_agent_claimed_monitor_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "current_agent_blocker_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "recent_completed_advancement_items": COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD,
    "claimed_by_others_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "other_agent_scoped_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "other_agent_bound_user_action_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "user_action_items": QUOTA_PAYLOAD_USER_ACTION_ITEM_LIMIT,
    "resume_blocked_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "handoff_gates": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "current_agent_handoff_gates": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "current_agent_cleared_without_successor_handoff_gates": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
}
AGENT_LANE_STATUS_TODO_ITEM_FIELDS = (
    "schema_version",
    "index",
    "todo_id",
    "text",
    "title",
    "status",
    "priority",
    "task_class",
    "action_kind",
    "task_domain",
    "claimed_by",
    "bound_agent",
    "blocks_agent",
    "global_gate",
    "unblocks_todo_id",
    "required_capabilities",
    "required_write_scopes",
    "missing_capabilities",
    "resume_ready",
    "next_due_at",
)
AGENT_LANE_STATUS_TODO_LANES = {
    "agent": (
        "items",
        "current_agent_blocker_items",
    ),
    "user": (
        "items",
        "gate_open_items",
        "user_action_items",
        "current_agent_blocker_items",
    ),
}
AGENT_LANE_STATUS_TODO_LANE_LIMITS = {
    "gate_open_items": 3,
    "user_action_items": 3,
}


def summarize_user_todos_for_quota(
    value: Any, *, agent_identity: dict[str, Any] | None = None,
    filter_user_gate_blocks_agent: bool = False, available_capabilities: Any = None,
    resolve_capacity: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return project_quota_planning(
        value, all_open_items=[], source_open_count=value.get("open_count"),
        agent_identity=agent_identity, filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities, resolve_capacity=resolve_capacity,
        summary_kind="user",
    )


def _truncate_quota_payload_text(value: Any, *, limit: int) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_quota_payload_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    compact: dict[str, Any] = {}
    for key in QUOTA_PAYLOAD_ITEM_FIELDS:
        value = item.get(key)
        if value is None:
            continue
        if key in {"text", "title"}:
            value = _truncate_quota_payload_text(
                value,
                limit=QUOTA_PAYLOAD_ITEM_TEXT_LIMIT,
            )
        compact[key] = value
    if "text" not in compact and item.get("text") is not None:
        compact["text"] = _truncate_quota_payload_text(
            item.get("text"),
            limit=QUOTA_PAYLOAD_ITEM_TEXT_LIMIT,
        )
    return compact


def _compact_quota_payload_item_list(
    items: Any,
    *,
    limit: int,
) -> list[Any]:
    if not isinstance(items, list):
        return []
    return [_compact_quota_payload_item(item) for item in items[:limit]]


def _compact_quota_payload_claim_scope(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    compact: dict[str, Any] = {}
    for key, child in value.items():
        if isinstance(child, list):
            compact[key] = _compact_quota_payload_item_list(
                child,
                limit=QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
            )
        else:
            compact[key] = child
    return compact


def _compact_quota_payload_nested_warning(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    compact: dict[str, Any] = {}
    for key, child in value.items():
        if isinstance(child, list) and key.endswith("items"):
            compact[key] = _compact_quota_payload_item_list(
                child,
                limit=QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
            )
        else:
            compact[key] = child
    return compact


def compact_quota_todo_summary_for_payload(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep quota hot-path todo summaries bounded without changing decision input."""
    frontier_deadline = todo_summary_frontier_deadline(
        summary,
        current_time=now_utc(),
    )
    compact: dict[str, Any] = {}
    compacted_lanes: dict[str, dict[str, int]] = {}
    for key, value in summary.items():
        if key == "advancement_frontier_revision_index":
            # Decision input is complete by construction, but it is not an
            # agent-facing diagnostic surface.
            continue
        if key in {"source_completeness", "closure_intent"}:
            continue
        if isinstance(value, list):
            limit = QUOTA_PAYLOAD_LANE_LIMITS.get(key, QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT)
            compact[key] = _compact_quota_payload_item_list(value, limit=limit)
            if len(value) > limit:
                compacted_lanes[key] = {
                    "shown": limit,
                    "total": len(value),
                }
        elif key == "claim_scope":
            compact[key] = _compact_quota_payload_claim_scope(value)
        elif isinstance(value, dict):
            compact[key] = _compact_quota_payload_nested_warning(value)
        else:
            compact[key] = value
    if frontier_deadline:
        compact["frontier_deadline"] = frontier_deadline
    compact["payload_compaction"] = {
        "schema_version": QUOTA_PAYLOAD_COMPACTION_SCHEMA_VERSION,
        "item_text_limit": QUOTA_PAYLOAD_ITEM_TEXT_LIMIT,
        "visibility_lane_item_limit": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
        "diagnostic_lane_item_limit": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
        "compacted_lanes": compacted_lanes,
        "full_detail_cold_path": "status, todo list, or active state",
    }
    return compact


def _compact_agent_lane_status_todo_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    compact: dict[str, Any] = {}
    for key in AGENT_LANE_STATUS_TODO_ITEM_FIELDS:
        value = item.get(key)
        if value is None:
            continue
        if key in {"text", "title"}:
            value = _truncate_quota_payload_text(
                value,
                limit=QUOTA_PAYLOAD_ITEM_TEXT_LIMIT,
            )
        compact[key] = value
    return compact


def _compact_agent_lane_status_todo_summary(
    summary: dict[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    retained_lanes = set(AGENT_LANE_STATUS_TODO_LANES[role])
    compact: dict[str, Any] = {}
    omitted_lanes: list[str] = []
    compacted_lanes: dict[str, dict[str, int]] = {}
    for key, value in summary.items():
        if key == "payload_compaction":
            continue
        if isinstance(value, list):
            if key not in retained_lanes:
                if value:
                    omitted_lanes.append(key)
                continue
            limit = AGENT_LANE_STATUS_TODO_LANE_LIMITS.get(key, 1)
            compact[key] = [
                _compact_agent_lane_status_todo_item(item)
                for item in value[:limit]
            ]
            if len(value) > limit:
                compacted_lanes[key] = {
                    "shown": limit,
                    "total": len(value),
                }
            continue
        if isinstance(value, dict):
            if key in {"monitor_writeback", "work_counts", "deferred_work_counts"}:
                compact[key] = _compact_quota_payload_nested_warning(value)
            continue
        compact[key] = value
    compact["payload_compaction"] = {
        "schema_version": AGENT_LANE_STATUS_TODO_COMPACTION_SCHEMA_VERSION,
        "item_text_limit": QUOTA_PAYLOAD_ITEM_TEXT_LIMIT,
        "retained_lanes": sorted(retained_lanes),
        "omitted_nonempty_lane_count": len(omitted_lanes),
        "compacted_lanes": compacted_lanes,
        "full_detail_cold_path": (
            "status without --agent-id, todo list, or active state"
        ),
    }
    return compact


def _agent_lane_status_project_asset_todo_reference(
    summary: dict[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    reference: dict[str, Any] = {}
    for key in (
        "schema_version",
        "source_section",
        "open",
        "done",
        "total",
        "deferred_count",
        "claimed_open_count",
        "unclaimed_open_count",
        "projection_view",
        "detail_pointer",
    ):
        value = summary.get(key)
        if value is not None:
            reference[key] = value
    reference["payload_reference"] = {
        "schema_version": AGENT_LANE_STATUS_TODO_REFERENCE_SCHEMA_VERSION,
        "canonical_path": f"attention_queue.items[].{role}_todos",
        "full_detail_cold_path": (
            "status without --agent-id, todo list, or active state"
        ),
    }
    return reference


def compact_agent_lane_todos_for_status_display(payload: dict[str, object]) -> None:
    queue = payload.get("attention_queue")
    if not isinstance(queue, dict):
        return
    items = queue.get("items")
    if not isinstance(items, list):
        return
    compacted = 0
    references = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, role in (("user_todos", "user"), ("agent_todos", "agent")):
            summary = item.get(key)
            if not isinstance(summary, dict):
                continue
            item[key] = _compact_agent_lane_status_todo_summary(
                summary,
                role=role,
            )
            compacted += 1
        project_asset = item.get("project_asset")
        if not isinstance(project_asset, dict):
            continue
        for key, role in (("user_todos", "user"), ("agent_todos", "agent")):
            summary = project_asset.get(key)
            if not isinstance(summary, dict):
                continue
            project_asset[key] = _agent_lane_status_project_asset_todo_reference(
                summary,
                role=role,
            )
            references += 1
    if compacted:
        payload["agent_lane_todo_summary_compaction"] = {
            "schema_version": AGENT_LANE_STATUS_TODO_COMPACTION_SCHEMA_VERSION,
            "compacted_summary_count": compacted,
            "project_asset_reference_count": references,
            "reason": (
                "status --agent-id keeps one compact todo summary per role and "
                "replaces nested project-asset duplicates with references"
            ),
        }


def summarize_project_asset_todos_for_quota(
    value: Any, *, agent_identity: dict[str, Any] | None = None,
    filter_user_gate_blocks_agent: bool = False, available_capabilities: Any = None,
    resolve_capacity: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return project_quota_planning(
        value, all_open_items=[], source_open_count=value.get("open_count"),
        agent_identity=agent_identity, filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities, resolve_capacity=resolve_capacity,
        summary_kind="project_asset",
    )


def is_canonical_attention_todo_summary(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("schema_version") == "todo_summary_v0":
        return True
    source_section = str(value.get("source_section") or "").strip().lower()
    if source_section.startswith("raw "):
        return False
    return source_section in {"agent todo", "user todo"}


def select_quota_todo_summary(
    canonical_value: Any,
    project_asset_value: Any,
    *,
    agent_identity: dict[str, Any] | None = None,
    filter_user_gate_blocks_agent: bool = False,
    available_capabilities: Any = None,
) -> dict[str, Any] | None:
    sources: tuple[tuple[Callable[..., dict[str, Any] | None], Any], ...] = (
        (summarize_user_todos_for_quota, canonical_value),
        (summarize_project_asset_todos_for_quota, project_asset_value),
    )
    if not is_canonical_attention_todo_summary(canonical_value):
        sources = tuple(reversed(sources))
    # Source precedence is decided before projecting Agent lanes/capacity.
    # The fallback is needed only when the preferred source has no summary.
    for summarize, value in sources:
        summary = summarize(
            value,
            agent_identity=agent_identity,
            filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
            available_capabilities=available_capabilities,
            resolve_capacity=True,
        )
        if summary:
            return summary
    return summary


def select_quota_todo_summaries(
    user_source: Any, agent_source: Any, *, agent_identity: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Compose the refresh caller's two native summaries in one transaction.

    Supplied and expired source references retain the standalone adapters.
    The native owner still validates every source and independent request.
    """
    from .summary_item import _CanonicalTodoSummary

    if isinstance(user_source, _CanonicalTodoSummary) and isinstance(agent_source, _CanonicalTodoSummary):
        requests = [project_quota_planning(source, all_open_items=[],
            source_open_count=source.get("open_count"), agent_identity=agent_identity,
            filter_user_gate_blocks_agent=index == 0, available_capabilities=None,
            summary_kind="user", request_only=True)
            for index, source in enumerate((user_source, agent_source))]
        if all("source_reference" in request["summary_request"] for request in requests):
            from .goal_todo_projection import project_todo_read
            from .quota_selection import _decode_quota_summary_wire

            result = project_todo_read({"kind": "quota_projection", "requests": requests})
            wires = result.get("quota_wires")
            if not isinstance(wires, list) or len(wires) != 2:
                raise RuntimeError("invalid complete quota summary response")
            return (_decode_quota_summary_wire(wires[0], user_source),
                    _decode_quota_summary_wire(wires[1], agent_source))
    return (select_quota_todo_summary(user_source, None, agent_identity=agent_identity,
                filter_user_gate_blocks_agent=True),
            select_quota_todo_summary(agent_source, None, agent_identity=agent_identity))


def select_quota_todo_contexts(
    user_canonical: Any, user_asset: Any, agent_canonical: Any, agent_asset: Any,
    *, agent_identity: dict[str, Any] | None, available_capabilities: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """The quota caller explicitly consumes both summaries and Agent displays.

    One native read transaction materializes each complete source. A supplied
    compatibility source retains its full codec and cannot acquire authority.
    """
    from .quota_selection import _decode_quota_summary_wire
    from .goal_todo_projection import project_todo_read

    values: list[dict[str, Any]] = []
    roles: list[str] = []
    requests: list[dict[str, Any]] = []
    for role, canonical, asset in (("user", user_canonical, user_asset), ("agent", agent_canonical, agent_asset)):
        selected = _select_todo_source(canonical, asset)
        if selected is None:
            continue
        kind, source = selected
        values.append(source)
        roles.append(role)
        requests.append(project_quota_planning(source, all_open_items=[], source_open_count=source.get("open_count"),
            agent_identity=agent_identity, filter_user_gate_blocks_agent=role == "user",
            available_capabilities=available_capabilities, resolve_capacity=True,
            summary_kind=kind, consume_display_views=role == "agent", request_only=True))
        requests[-1]["summary_request"]["consume_source_summary"] = role == "user"
        requests[-1]["summary_request"]["consume_vision_wait_basis"] = role == "agent"
    summaries: dict[str, dict[str, Any]] = {}
    views: dict[str, list[dict[str, Any]]] = {"summary": [], "planning": [], "terminal": []}
    user_rows: list[dict[str, Any]] = []
    if requests:
        result = project_todo_read({"kind": "quota_projection", "requests": requests})
        wires = result.get("quota_wires")
        if not isinstance(wires, list) or len(wires) != len(requests):
            raise RuntimeError("invalid complete quota projection response")
        for role, source, wire in zip(roles, values, wires, strict=True):
            summaries[role] = _decode_quota_summary_wire(wire, source)
            if role == "user":
                user_rows = wire.get("source_summary")
                if not isinstance(user_rows, list) or any(not isinstance(row, dict) for row in user_rows):
                    raise RuntimeError("invalid User quota source display")
            if role == "agent":
                raw = wire.get("display_views") if isinstance(wire, dict) else None
                if not isinstance(raw, dict) or set(raw) != set(views) or any(
                    not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
                    for rows in raw.values()
                ):
                    raise RuntimeError("invalid explicit Agent quota display views")
                views = raw
                from .summary_item import _QuotaProjectedSummary
                basis = wire.get("vision_wait_basis")
                if not isinstance(basis, dict):
                    raise RuntimeError("invalid prepared quota vision wait basis")
                summaries[role] = _QuotaProjectedSummary(summaries[role], basis, views["terminal"],
                    (agent_identity or {}).get("agent_id"))
    return summaries.get("user"), summaries.get("agent"), views, user_rows


def _select_todo_source(
    canonical_value: Any,
    project_asset_value: Any,
) -> tuple[str, dict[str, Any]] | None:
    """Keep the projection kind selected by precedence, including aliased values."""
    sources: tuple[tuple[str, Any], ...] = (("user", canonical_value), ("project_asset", project_asset_value))
    if not is_canonical_attention_todo_summary(canonical_value):
        sources = tuple(reversed(sources))
    return next(((kind, source) for kind, source in sources if isinstance(source, dict)), None)


def select_quota_todo_source_items(
    canonical_value: Any,
    project_asset_value: Any,
) -> list[dict[str, Any]]:
    """Return the authoritative pre-hot-path todo source for semantic checks."""

    selected = _select_todo_source(canonical_value, project_asset_value)
    return todo_summary_source_items(selected[1]) if selected is not None else []


def select_quota_todo_display_views(
    canonical_value: Any,
    project_asset_value: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Select the source once for the quota caller's three consumed displays."""
    from .summary_item import todo_source_display_views

    selected = _select_todo_source(canonical_value, project_asset_value)
    return (todo_source_display_views(selected[1]) if selected is not None
            else {"summary": [], "planning": [], "terminal": []})


def select_planning_inventory_source_items(
    canonical_value: Any,
    project_asset_value: Any,
    *,
    include_terminal: bool = False,
) -> list[dict[str, Any]]:
    """Select the canonical non-terminal rows shared by planning read models."""

    selected = _select_todo_source(canonical_value, project_asset_value)
    return (todo_planning_source_items(selected[1], include_terminal=include_terminal)
            if selected is not None else [])


def _task_orchestration_authority_items(
    value: Any,
    *,
    role: str,
) -> list[dict[str, Any]] | None:
    if not isinstance(value, dict):
        return None
    authority = value.get("task_orchestration_authority")
    if (
        not isinstance(authority, dict)
        or authority.get("schema_version") != "task_orchestration_authority_v0"
        or authority.get("role") != role
    ):
        return None
    key = "candidate_items" if role == "agent" else "user_blocker_items"
    items = authority.get(key)
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


def select_task_orchestration_authority_items(
    canonical_value: Any,
    project_asset_value: Any,
    *,
    role: str,
    fallback_source_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    canonical_items = _task_orchestration_authority_items(
        canonical_value,
        role=role,
    )
    project_asset_items = _task_orchestration_authority_items(
        project_asset_value,
        role=role,
    )
    if is_canonical_attention_todo_summary(canonical_value):
        selected = (
            canonical_items if canonical_items is not None else project_asset_items
        )
    else:
        selected = (
            project_asset_items if project_asset_items is not None else canonical_items
        )
    if selected is not None:
        return selected
    if fallback_source_items is not None:
        # Preserve the independent mutable display returned by the old call.
        return deepcopy(fallback_source_items)
    return select_quota_todo_source_items(canonical_value, project_asset_value)
