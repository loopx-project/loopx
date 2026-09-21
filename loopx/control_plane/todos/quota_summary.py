from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..agents.agent_scope import agent_scope_item_claimed_by
from ..goals.goal_vision_policy import COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD
from ..runtime.time import now_utc
from .contract import (
    TODO_TASK_CLASS_BLOCKER,
)
from .quota_selection import project_quota_planning
from .frontier_deadline import todo_summary_frontier_deadline
from .handoff_gate import build_todo_handoff_gate_lanes
from .todo_semantics import (
    todo_item_is_watch_only_monitor,
    todo_item_task_class,
    todo_presentation_sort_key,
    todo_summary_monitor_schedule_gap_items,
    todo_summary_monitor_writeback_contract,
)
from .route_continuation import build_todo_route_continuation_lanes
from .succession_warning import build_todo_succession_warning_lanes
from .summary_item import (
    compact_todo_summary_item,
    todo_planning_source_items,
    todo_summary_source_items,
)
from .user_gate import is_user_gate_todo_item

MONITOR_DUE_ITEM_LIMIT = 1
TODO_BACKLOG_ITEM_LIMIT = 8
TODO_VISIBILITY_LANE_LIMIT = 16
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


@dataclass(frozen=True)
class _QuotaTodoLanes:
    all_open_items: list[dict[str, Any]]
    blocking_open_items: list[dict[str, Any]]
    user_action_open_items: list[dict[str, Any]]
    other_agent_bound_user_action_items: list[dict[str, Any]]
    user_action_agent_scope_filter: dict[str, Any] | None
    other_agent_scoped_items: list[dict[str, Any]]
    agent_scope_filter: dict[str, Any] | None
    open_items: list[dict[str, Any]]
    claim_scope: dict[str, Any] | None
    executable_items: list[dict[str, Any]]
    monitor_items: list[dict[str, Any]]
    monitor_due_items: list[dict[str, Any]]
    watch_only_monitor_items: list[dict[str, Any]]
    watch_only_monitor_due_items: list[dict[str, Any]]
    non_watch_only_monitor_due_items: list[dict[str, Any]]
    monitor_capability_blocked_due_items: list[dict[str, Any]]
    claimed_open_items: list[dict[str, Any]]
    display_open_items: list[dict[str, Any]]
    active_next_action_items: list[dict[str, Any]]
    active_next_action_executable_items: list[dict[str, Any]]
    open_count: Any


def _strict_non_negative_int(value: Any) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _terminal_closure_proof_is_valid(
    value: dict[str, Any],
    *,
    counts: dict[str, int | None],
    source_proof: dict[str, Any],
) -> bool:
    proof = value.get("terminal_closure_proof")
    items = value.get("items")
    total_count = counts["total_count"]
    displayed_items_cover_source = bool(
        isinstance(total_count, int)
        and (
            (total_count == 0 and items == [])
            or (
                total_count > 0
                and isinstance(items, list)
                and 0 < len(items) <= total_count
            )
        )
    )
    return bool(
        value.get("schema_version") == "todo_summary_v0"
        and isinstance(items, list)
        and displayed_items_cover_source
        and all(
            isinstance(item, dict)
            and (
                (item.get("status") == "done" and item.get("done") is True)
                or (
                    todo_item_is_watch_only_monitor(item)
                )
            )
            and item.get("route_continuation_replan_required") is not True
            for item in items
        )
        and all(
            isinstance(item, dict)
            and todo_item_is_watch_only_monitor(item)
            for item in (value.get("monitor_open_items") or [])
        )
        and value.get("deferred_items") == []
        and value.get("deferred_resume_candidates") == []
        and _strict_non_negative_int(
            value.get("convergence_open_count", value.get("open_count"))
        ) == 0
        and _strict_non_negative_int(value.get("completed_without_successor_count", 0)) == 0
        and _strict_non_negative_int(value.get("route_continuation_replan_count", 0)) == 0
        and isinstance(proof, dict)
        and proof.get("schema_version") == "todo_terminal_closure_proof_v0"
        and proof.get("role") == source_proof.get("role")
        and proof.get("source_section") == value.get("source_section")
        and proof.get("item_count") == total_count
        and (
            proof.get("all_todos_done") is True
            or proof.get("all_convergent_todos_done") is True
        )
        and _strict_non_negative_int(proof.get("monitor_open_count"))
        == _strict_non_negative_int(proof.get("watch_only_monitor_count", 0))
        and _strict_non_negative_int(proof.get("successor_gap_count")) == 0
        and _strict_non_negative_int(proof.get("route_replan_count")) == 0
        and _strict_non_negative_int(proof.get("no_followup_count")) is not None
        and proof.get("derived") is True
    )


def validate_todo_source_contract(
    value: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    proof = value.get("source_proof")
    counts = {
        key: _strict_non_negative_int(value.get(key))
        for key in ("total_count", "open_count", "done_count", "deferred_count")
    }
    total_count = counts["total_count"]
    open_count = counts["open_count"]
    done_count = counts["done_count"]
    deferred_count = counts["deferred_count"]
    valid_counts = (
        total_count is not None
        and open_count is not None
        and done_count is not None
        and deferred_count is not None
        and total_count == open_count + done_count + deferred_count
    )
    valid_proof = bool(
        isinstance(proof, dict)
        and proof.get("schema_version") == "todo_source_proof_v0"
        and proof.get("role") in {"user", "agent"}
        and proof.get("derived") is True
        and bool(str(value.get("source_section") or "").strip())
        and type(proof.get("item_count")) is int
        and proof.get("item_count") == total_count
    )
    valid_terminal_closure = bool(
        valid_counts
        and valid_proof
        and isinstance(proof, dict)
        and _terminal_closure_proof_is_valid(
            value,
            counts=counts,
            source_proof=proof,
        )
    )
    completeness = {
        "schema_version": "todo_source_completeness_v0",
        "status": "valid" if valid_terminal_closure else "invalid",
        "source": "structured_todo_projection",
        "role": proof.get("role") if isinstance(proof, dict) else None,
        "terminal_closure": "valid" if valid_terminal_closure else "invalid",
    }

    intent = value.get("closure_intent")
    terminal_proof = value.get("terminal_closure_proof")
    intent_count = intent.get("count") if isinstance(intent, dict) else None
    valid_intent = bool(
        valid_terminal_closure
        and isinstance(intent, dict)
        and intent.get("schema_version") == "todo_closure_intent_v0"
        and intent.get("kind") == "no_followup"
        and intent.get("derived") is True
        and type(intent_count) is int
        and done_count is not None
        and 0 < intent_count <= done_count
        and isinstance(terminal_proof, dict)
        and intent_count == terminal_proof.get("no_followup_count")
    )
    closure_intent = (
        {**intent, "source": "todo_no_followup"}
        if valid_intent and isinstance(intent, dict)
        else None
    )
    return completeness, closure_intent


def summarize_user_todos_for_quota(
    value: Any,
    *,
    agent_identity: dict[str, Any] | None = None,
    filter_user_gate_blocks_agent: bool = False,
    available_capabilities: Any = None,
    resolve_capacity: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source_completeness, closure_intent = validate_todo_source_contract(value)
    all_open_items = sorted(
        todo_summary_source_items(value),
        key=todo_presentation_sort_key,
    )
    planning = project_quota_planning(
        value,
        all_open_items=all_open_items,
        source_open_count=value.get("open_count", len(all_open_items)),
        agent_identity=agent_identity,
        filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities,
        resolve_capacity=resolve_capacity,
    )
    lanes = _QuotaTodoLanes(**planning["lanes"])
    resume_planning = planning["resume_planning"]
    value = {**value, **(resume_planning["capacity_fields"] or {})}
    monitor_schedule_gap_items = todo_summary_monitor_schedule_gap_items(
        {
            "monitor_open_items": lanes.monitor_items,
            "monitor_writeback": value.get("monitor_writeback"),
        }
    )
    gate_items = [
        item
        for item in lanes.open_items
        if is_user_gate_todo_item(item)
    ]
    blocker_items = [
        item
        for item in lanes.all_open_items
        if todo_item_task_class(item) == TODO_TASK_CLASS_BLOCKER
        and str(item.get("status") or "").strip().lower() == "blocked"
        and str(item.get("reason") or "").strip()
    ]
    agent_id = str((agent_identity or {}).get("agent_id") or "").strip()
    recent_completed_advancement_items = [
        compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
        for item in (value.get("recent_completed_advancement_items") or [])
        if isinstance(item, dict)
        if not agent_id or agent_scope_item_claimed_by(item) == agent_id
    ]
    current_agent_blocker_items = [
        item
        for item in blocker_items
        if agent_id and agent_scope_item_claimed_by(item) == agent_id
    ]
    summary = {
        "schema_version": value.get("schema_version"),
        "source_section": value.get("source_section"),
        "total_count": value.get("total_count"),
        "open_count": lanes.open_count,
        "work_counts": planning["work_counts"],
        "done_count": value.get("done_count"),
        "deferred_count": value.get("deferred_count"),
        "source_completeness": source_completeness,
        "first_open_items": lanes.display_open_items[:3],
        "first_executable_items": lanes.executable_items[:3],
        "gate_open_items": gate_items[:3],
        "monitor_open_items": lanes.monitor_items,
        "monitor_due_count": len(lanes.monitor_due_items),
        "monitor_due_items": lanes.monitor_due_items[:MONITOR_DUE_ITEM_LIMIT],
        "watch_only_monitor_count": len(lanes.watch_only_monitor_items),
        "watch_only_monitor_due_count": len(lanes.watch_only_monitor_due_items),
        "watch_only_monitor_due_items": lanes.watch_only_monitor_due_items[
            :MONITOR_DUE_ITEM_LIMIT
        ],
        "non_watch_only_monitor_due_items": lanes.non_watch_only_monitor_due_items[
            :MONITOR_DUE_ITEM_LIMIT
        ],
        "monitor_capability_blocked_due_count": len(
            lanes.monitor_capability_blocked_due_items
        ),
        "monitor_capability_blocked_due_items": (
            lanes.monitor_capability_blocked_due_items
        ),
        "monitor_schedule_gap_count": len(monitor_schedule_gap_items),
        "monitor_schedule_gap_items": monitor_schedule_gap_items[:MONITOR_DUE_ITEM_LIMIT],
        "active_next_action_items": lanes.active_next_action_items,
        "active_next_action_executable_items": lanes.active_next_action_executable_items,
        "backlog_items": lanes.display_open_items[:TODO_BACKLOG_ITEM_LIMIT],
        "executable_backlog_items": lanes.executable_items[:TODO_BACKLOG_ITEM_LIMIT],
    }
    if isinstance(value.get("goal_acceptance_contract"), dict):
        summary["goal_acceptance_contract"] = value["goal_acceptance_contract"]
    if isinstance(value.get("advancement_frontier_revision_index"), dict):
        summary["advancement_frontier_revision_index"] = value[
            "advancement_frontier_revision_index"
        ]
    if lanes.watch_only_monitor_items:
        summary["convergence_open_count"] = value.get("convergence_open_count")
    if recent_completed_advancement_items:
        summary["recent_completed_advancement_items"] = recent_completed_advancement_items
    if isinstance(value.get("vision_wait_states"), list):
        summary["vision_wait_states"] = [
            proof for proof in value["vision_wait_states"]
            if isinstance(proof, dict) and proof.get("agent_id") == agent_id
        ]
    if blocker_items:
        summary["blocker_open_count"] = len(blocker_items)
    if current_agent_blocker_items:
        summary["current_agent_blocker_count"] = len(current_agent_blocker_items)
        summary["current_agent_blocker_items"] = current_agent_blocker_items[
            :QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT
        ]
    if closure_intent:
        summary["closure_intent"] = closure_intent
    monitor_writeback = todo_summary_monitor_writeback_contract(value)
    if monitor_writeback:
        summary["monitor_writeback"] = monitor_writeback
    summary.update(planning["claim_visibility"])
    summary.update(resume_planning["deferred_lanes"])
    summary.update(resume_planning["resume_blocked_lanes"])
    summary.update(
        build_todo_handoff_gate_lanes(
            value,
            agent_identity=agent_identity,
            item_limit=TODO_BACKLOG_ITEM_LIMIT,
        )
    )
    summary.update(
        build_todo_route_continuation_lanes(
            value,
            agent_identity=agent_identity,
            item_limit=TODO_BACKLOG_ITEM_LIMIT,
        )
    )
    summary.update(
        build_todo_succession_warning_lanes(
            value,
            item_limit=TODO_BACKLOG_ITEM_LIMIT,
        )
    )
    source_claimed_open_count = None if filter_user_gate_blocks_agent else value.get("claimed_open_count")
    if lanes.claimed_open_items or source_claimed_open_count:
        summary["claimed_open_count"] = source_claimed_open_count or len(lanes.claimed_open_items)
        summary["unclaimed_open_count"] = (
            max(0, int(lanes.open_count or 0) - len(lanes.claimed_open_items))
            if filter_user_gate_blocks_agent
            else value.get(
                "unclaimed_open_count",
                max(0, int(lanes.open_count or 0) - len(lanes.claimed_open_items)),
            )
        )
    if lanes.claim_scope:
        summary["claim_scope"] = lanes.claim_scope
    if filter_user_gate_blocks_agent:
        summary["all_open_count"] = value.get("open_count", len(all_open_items))
    if lanes.agent_scope_filter:
        summary["agent_scope_filter"] = lanes.agent_scope_filter
        summary["other_agent_scoped_open_count"] = len(lanes.other_agent_scoped_items)
        summary["other_agent_scoped_items"] = [
            compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
            for item in lanes.other_agent_scoped_items[:TODO_VISIBILITY_LANE_LIMIT]
        ]
    if filter_user_gate_blocks_agent and lanes.user_action_open_items:
        summary["user_action_open_count"] = len(lanes.user_action_open_items)
        summary["user_action_items"] = [
            compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
            for item in lanes.user_action_open_items[:TODO_VISIBILITY_LANE_LIMIT]
        ]
    if lanes.user_action_agent_scope_filter:
        summary["user_action_agent_scope_filter"] = lanes.user_action_agent_scope_filter
        summary["other_agent_bound_user_action_open_count"] = len(
            lanes.other_agent_bound_user_action_items
        )
        summary["other_agent_bound_user_action_items"] = [
            compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
            for item in lanes.other_agent_bound_user_action_items[
                :TODO_VISIBILITY_LANE_LIMIT
            ]
        ]
    return summary


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
            if key in {"monitor_writeback", "work_counts"}:
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
    value: Any,
    *,
    agent_identity: dict[str, Any] | None = None,
    filter_user_gate_blocks_agent: bool = False,
    available_capabilities: Any = None,
    resolve_capacity: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if (
        isinstance(value.get("items"), list)
        or isinstance(value.get("first_open_items"), list)
    ) and (
        "total_count" in value or "open_count" in value or "done_count" in value
    ):
        return summarize_user_todos_for_quota(
            value,
            agent_identity=agent_identity,
            filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
            available_capabilities=available_capabilities,
            resolve_capacity=resolve_capacity,
        )

    all_open_items = sorted(
        todo_summary_source_items(value),
        key=todo_presentation_sort_key,
    )
    if not all_open_items:
        next_text = str(value.get("next") or "").strip()
        next_index = value.get("next_index", 1)
        all_open_items = [{"index": next_index, "text": next_text}] if next_text else []
        next_claimed_by = str(value.get("next_claimed_by") or "").strip()
        if all_open_items and next_claimed_by:
            all_open_items[0]["claimed_by"] = next_claimed_by
    planning = project_quota_planning(
        value,
        all_open_items=all_open_items,
        source_open_count=value.get("open", value.get("open_count", len(all_open_items))),
        agent_identity=agent_identity,
        filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities,
        resolve_capacity=resolve_capacity,
    )
    lanes = _QuotaTodoLanes(**planning["lanes"])
    resume_planning = planning["resume_planning"]
    value = {**value, **(resume_planning["capacity_fields"] or {})}
    summary = {
        "schema_version": value.get("schema_version"),
        "source_section": value.get("source_section") or "project_asset",
        "total_count": value.get("total", value.get("total_count")),
        "open_count": lanes.open_count,
        "work_counts": planning["work_counts"],
        "done_count": value.get("done", value.get("done_count")),
        "first_open_items": lanes.display_open_items[:3],
        "first_executable_items": lanes.executable_items[:3],
        "monitor_open_items": lanes.monitor_items,
        "monitor_due_count": len(lanes.monitor_due_items),
        "monitor_due_items": lanes.monitor_due_items[:MONITOR_DUE_ITEM_LIMIT],
        "watch_only_monitor_count": len(lanes.watch_only_monitor_items),
        "watch_only_monitor_due_count": len(lanes.watch_only_monitor_due_items),
        "watch_only_monitor_due_items": lanes.watch_only_monitor_due_items[
            :MONITOR_DUE_ITEM_LIMIT
        ],
        "non_watch_only_monitor_due_items": lanes.non_watch_only_monitor_due_items[
            :MONITOR_DUE_ITEM_LIMIT
        ],
        "monitor_capability_blocked_due_count": len(
            lanes.monitor_capability_blocked_due_items
        ),
        "monitor_capability_blocked_due_items": (
            lanes.monitor_capability_blocked_due_items
        ),
        "active_next_action_items": lanes.active_next_action_items,
        "active_next_action_executable_items": lanes.active_next_action_executable_items,
        "backlog_items": lanes.display_open_items[:TODO_BACKLOG_ITEM_LIMIT],
        "executable_backlog_items": lanes.executable_items[:TODO_BACKLOG_ITEM_LIMIT],
    }
    monitor_writeback = todo_summary_monitor_writeback_contract(value)
    if monitor_writeback:
        summary["monitor_writeback"] = monitor_writeback
    summary.update(planning["claim_visibility"])
    summary.update(resume_planning["deferred_lanes"])
    summary.update(
        build_todo_handoff_gate_lanes(
            value,
            agent_identity=agent_identity,
            item_limit=TODO_BACKLOG_ITEM_LIMIT,
        )
    )
    summary.update(
        build_todo_route_continuation_lanes(
            value,
            agent_identity=agent_identity,
            item_limit=TODO_BACKLOG_ITEM_LIMIT,
        )
    )
    source_claimed_open_count = None if filter_user_gate_blocks_agent else value.get("claimed_open_count")
    if lanes.claimed_open_items or source_claimed_open_count:
        summary["claimed_open_count"] = source_claimed_open_count or len(lanes.claimed_open_items)
        summary["unclaimed_open_count"] = (
            max(0, int(lanes.open_count or 0) - len(lanes.claimed_open_items))
            if filter_user_gate_blocks_agent
            else value.get(
                "unclaimed_open_count",
                max(0, int(lanes.open_count or 0) - len(lanes.claimed_open_items)),
            )
        )
    if lanes.claim_scope:
        summary["claim_scope"] = lanes.claim_scope
    if lanes.agent_scope_filter:
        summary["agent_scope_filter"] = lanes.agent_scope_filter
        summary["all_open_count"] = value.get("open", value.get("open_count", len(all_open_items)))
        summary["other_agent_scoped_open_count"] = len(lanes.other_agent_scoped_items)
        summary["other_agent_scoped_items"] = [
            compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
            for item in lanes.other_agent_scoped_items[:TODO_VISIBILITY_LANE_LIMIT]
        ]
    if filter_user_gate_blocks_agent and lanes.user_action_open_items:
        summary["user_action_open_count"] = len(lanes.user_action_open_items)
        summary["user_action_items"] = [
            compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
            for item in lanes.user_action_open_items[:TODO_VISIBILITY_LANE_LIMIT]
        ]
    if lanes.user_action_agent_scope_filter:
        summary["user_action_agent_scope_filter"] = lanes.user_action_agent_scope_filter
        summary["other_agent_bound_user_action_open_count"] = len(
            lanes.other_agent_bound_user_action_items
        )
        summary["other_agent_bound_user_action_items"] = [
            compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
            for item in lanes.other_agent_bound_user_action_items[
                :TODO_VISIBILITY_LANE_LIMIT
            ]
        ]
    return summary


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
    canonical_summary = summarize_user_todos_for_quota(
        canonical_value,
        agent_identity=agent_identity,
        filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities,
        resolve_capacity=True,
    )
    project_asset_summary = summarize_project_asset_todos_for_quota(
        project_asset_value,
        agent_identity=agent_identity,
        filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities,
        resolve_capacity=True,
    )
    if is_canonical_attention_todo_summary(canonical_value):
        return canonical_summary or project_asset_summary
    return project_asset_summary or canonical_summary


def select_quota_todo_source_items(
    canonical_value: Any,
    project_asset_value: Any,
) -> list[dict[str, Any]]:
    """Return the authoritative pre-hot-path todo source for semantic checks."""

    canonical_items = (
        todo_summary_source_items(canonical_value)
        if isinstance(canonical_value, dict)
        else None
    )
    project_asset_items = (
        todo_summary_source_items(project_asset_value)
        if isinstance(project_asset_value, dict)
        else None
    )
    if is_canonical_attention_todo_summary(canonical_value):
        return canonical_items if canonical_items is not None else project_asset_items or []
    return project_asset_items if project_asset_items is not None else canonical_items or []


def _planning_inventory_source_items(value: Any, *, include_terminal: bool = False) -> list[dict[str, Any]] | None:
    """Return canonical Todo rows before presentation-lane expansion.

    Planning consumers share domain-state rows instead of rebuilding a larger,
    order-sensitive union from presentation lanes.
    """

    if not isinstance(value, dict):
        return None
    return todo_planning_source_items(value, include_terminal=include_terminal)


def select_planning_inventory_source_items(
    canonical_value: Any,
    project_asset_value: Any,
    *,
    include_terminal: bool = False,
) -> list[dict[str, Any]]:
    """Select the canonical non-terminal rows shared by planning read models."""

    canonical_items = _planning_inventory_source_items(canonical_value, include_terminal=include_terminal)
    project_asset_items = _planning_inventory_source_items(project_asset_value, include_terminal=include_terminal)
    if is_canonical_attention_todo_summary(canonical_value):
        return canonical_items if canonical_items is not None else project_asset_items or []
    return project_asset_items if project_asset_items is not None else canonical_items or []


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
    return select_quota_todo_source_items(canonical_value, project_asset_value)
