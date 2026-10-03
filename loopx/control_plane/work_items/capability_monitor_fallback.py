from __future__ import annotations

from typing import Any

from ..agents.capability_gate import build_capability_gate
from ..todos.contract import TODO_TASK_CLASS_ADVANCEMENT, TODO_TASK_CLASS_MONITOR
from ..todos.todo_semantics import todo_item_task_class
from ..todos.summary_item import compact_todo_summary_item
from .work_lane import WORK_LANE_CONTRACT_SCHEMA_VERSION as WORK_LANE_CONTRACT_SCHEMA_VERSION


CAPABILITY_MONITOR_FALLBACK_SCHEMA_VERSION = "capability_skip_monitor_fallback_v0"
DEFAULT_MONITOR_ITEM_LIMIT = 1


def _compact_monitor_items(
    items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    return [
        compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
        for item in items[:limit]
        if isinstance(item, dict)
    ]


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def build_capability_skip_monitor_fallback_contract(
    capability_gate: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    *,
    monitor_item_limit: int = DEFAULT_MONITOR_ITEM_LIMIT,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Expose monitor work when unavailable advancement candidates would hide it.

    Capability skip means every visible advancement candidate is unavailable to
    the current agent. Current-agent monitor work still has a scheduler contract:
    repair missing monitor schedule metadata, attempt due monitor work, or keep a
    quiet monitor wait. Without this fallback, quota collapses to generic
    quiet_wait and stateful host loops may drift to the slowest backoff.
    """

    if not isinstance(capability_gate, dict) or capability_gate.get("action") != "skip":
        return None, None
    if not isinstance(agent_todo_summary, dict):
        return None, None
    monitor_items = (
        agent_todo_summary.get("monitor_open_items")
        if isinstance(agent_todo_summary.get("monitor_open_items"), list)
        else []
    )

    if not monitor_items:
        return None, None

    blocked_candidates = (
        capability_gate.get("blocked_candidates")
        if isinstance(capability_gate.get("blocked_candidates"), list)
        else []
    )
    blocked_advancement_count = sum(
        1
        for item in blocked_candidates
        if isinstance(item, dict)
        and todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
    )
    blocked_due_monitor_count = sum(
        1
        for item in blocked_candidates
        if isinstance(item, dict)
        and todo_item_task_class(item) == TODO_TASK_CLASS_MONITOR
    )
    capability_unavailable_reason = (
        "advancement_unavailable_by_capability"
        if blocked_advancement_count
        else "due_monitor_unavailable_by_capability"
    )
    if blocked_advancement_count and blocked_due_monitor_count:
        fallback_reason = (
            "advancement and due-monitor candidates require unavailable capabilities, "
            "so remaining current-agent monitor work must stay visible to the scheduler"
        )
    elif blocked_due_monitor_count:
        fallback_reason = (
            "due-monitor candidates require unavailable capabilities, so their "
            "diagnostic monitor state must stay visible to the scheduler"
        )
    else:
        fallback_reason = (
            "all advancement candidates require unavailable capabilities, so "
            "current-agent monitor work must remain visible to the scheduler"
        )
    base_fallback = {
        "schema_version": CAPABILITY_MONITOR_FALLBACK_SCHEMA_VERSION,
        "source": "quota.capability_gate",
        "capability_gate_action": "skip",
        "blocked_advancement_count": blocked_advancement_count,
        "blocked_due_monitor_count": blocked_due_monitor_count,
        "reason": fallback_reason,
    }

    due_items = (
        agent_todo_summary.get("monitor_due_items")
        if isinstance(agent_todo_summary.get("monitor_due_items"), list)
        else []
    )
    monitor_due_count = _safe_count(agent_todo_summary.get("monitor_due_count"))
    if monitor_due_count > 0 and due_items:
        selected = due_items[0]
        return (
            {
                "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                "lane": "continuous_monitor",
                "monitor_kind": "todo_monitor_due",
                "next_lane": "continuous_monitor",
                "obligation": "attempt_due_monitor",
                "must_attempt_work": True,
                "reason_codes": [
                    capability_unavailable_reason,
                    "monitor_due",
                ],
                "monitor_policy": "attempt_due_monitor_once_then_writeback_or_no_spend_if_unchanged",
                "monitor_due_count": monitor_due_count,
                "monitor_due_items": due_items[:monitor_item_limit],
                "selected_todo_id": selected.get("todo_id"),
                "selected_next_due_at": selected.get("next_due_at"),
                "action": (
                    "attempt the selected due continuous_monitor todo because all "
                    "advancement candidates are unavailable to this agent"
                ),
            },
            {**base_fallback, "mode": "due_monitor_attempt"},
        )

    schedule_gap_items = (
        agent_todo_summary.get("monitor_schedule_gap_items")
        if isinstance(agent_todo_summary.get("monitor_schedule_gap_items"), list)
        else []
    )
    monitor_schedule_gap_count = _safe_count(
        agent_todo_summary.get("monitor_schedule_gap_count")
    )
    if monitor_schedule_gap_count > 0 and schedule_gap_items:
        selected = schedule_gap_items[0]
        return (
            {
                "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                "lane": "advancement_task",
                "monitor_kind": "todo_monitor_schedule_gap",
                "next_lane": "continuous_monitor",
                "obligation": "repair_monitor_schedule_metadata",
                "must_attempt_work": True,
                "reason_codes": [
                    capability_unavailable_reason,
                    "monitor_schedule_metadata_gap",
                ],
                "monitor_policy": "repair_schedule_metadata_before_quiet_wait",
                "monitor_schedule_gap_count": monitor_schedule_gap_count,
                "monitor_schedule_gap_items": schedule_gap_items[:monitor_item_limit],
                "selected_todo_id": selected.get("todo_id"),
                "action": (
                    "repair the selected continuous_monitor todo by adding cadence/"
                    "next_due_at, superseding it, or recording an explicit no-schedule "
                    "policy before quiet scheduler backoff"
                ),
            },
            {**base_fallback, "mode": "monitor_schedule_metadata_repair"},
        )

    return (
        {
            "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
            "lane": "continuous_monitor",
            "monitor_kind": "todo_monitor",
            "next_lane": "continuous_monitor",
            "obligation": "quiet_until_material_monitor_transition",
            "must_attempt_work": False,
            "reason_codes": [
                capability_unavailable_reason,
                "monitor_todo_present",
            ],
            "monitor_policy": "write_once_per_material_transition_else_no_spend",
            "monitor_open_count": len(monitor_items),
            "monitor_items": _compact_monitor_items(
                monitor_items,
                limit=monitor_item_limit,
            ),
            "material_transition": (
                "a monitor todo may write back only material state transitions, regressions, or concrete blockers"
            ),
            "action": "wait quietly for material monitor evidence",
        },
        {**base_fallback, "mode": "monitor_quiet_wait"},
    )


def build_capability_gate_with_monitor_fallback(
    agent_todo_summary: dict[str, Any] | None,
    *,
    available_capabilities: list[str],
    agent_identity: dict[str, Any] | None = None,
    monitor_item_limit: int = DEFAULT_MONITOR_ITEM_LIMIT,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    capability_gate = build_capability_gate(
        agent_todo_summary,
        available_capabilities=available_capabilities,
        agent_identity=agent_identity,
    )
    contract, fallback = build_capability_skip_monitor_fallback_contract(
        capability_gate,
        agent_todo_summary,
        monitor_item_limit=monitor_item_limit,
    )
    return capability_gate, contract, fallback
