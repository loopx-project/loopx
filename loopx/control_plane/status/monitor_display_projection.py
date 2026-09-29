"""Monitor-display status projections inside the `status` bounded context."""

from __future__ import annotations

from typing import Any

from ..scheduler.monitor_display import (
    attention_item_is_monitor_quiet_display_candidate as _attention_item_is_monitor_quiet_display_candidate,
    normalize_monitor_quiet_attention_display as _normalize_monitor_quiet_attention_display,
    quiet_monitor_display_action as _quiet_monitor_display_action,
    todo_summary_lane_items as _todo_summary_lane_items,
    todo_summary_open_count as _todo_summary_open_count,
)
from ..todos.todo_summary import (
    MAX_STATUS_TODOS_PER_ROLE,
    open_todo_items,
    todo_item_is_actionable_open,
)
from ..work_items.project_asset import (
    build_project_asset as _build_project_asset,
)


MONITOR_DISPLAY_SCHEMA_VERSION = "monitor_quiet_display_v0"
MONITOR_DISPLAY_STOP_CONDITION = (
    "stop until a material monitor transition, regression, or concrete blocker appears"
)
MONITOR_DISPLAY_FALLBACK_ACTION = (
    "No immediate agent work; keep the monitor quiet until a material monitor "
    "transition, regression, or concrete blocker appears."
)
MONITOR_SIGNAL_WAITING_ON = "monitor_signal"


def build_project_asset(
    *,
    status: str,
    waiting_on: str,
    recommended_action: str,
    operator_question: str | None,
    agent_command: str | None,
    missing_gates: list[str] | None,
    next_handoff_condition: str | None,
) -> dict[str, Any]:
    return _build_project_asset(
        status=status,
        waiting_on=waiting_on,
        recommended_action=recommended_action,
        operator_question=operator_question,
        agent_command=agent_command,
        missing_gates=missing_gates,
        next_handoff_condition=next_handoff_condition,
        monitor_signal_waiting_on=MONITOR_SIGNAL_WAITING_ON,
        monitor_display_stop_condition=MONITOR_DISPLAY_STOP_CONDITION,
    )


def todo_summary_open_count(summary: dict[str, Any] | None) -> int:
    return _todo_summary_open_count(
        summary,
        open_todo_items=open_todo_items,
        todo_item_is_actionable_open=todo_item_is_actionable_open,
        fallback_limit=MAX_STATUS_TODOS_PER_ROLE,
    )


def todo_summary_lane_items(summary: dict[str, Any] | None, lane: str) -> list[dict[str, Any]]:
    return _todo_summary_lane_items(summary, lane)


def attention_item_is_monitor_quiet_display_candidate(item: dict[str, Any]) -> bool:
    return _attention_item_is_monitor_quiet_display_candidate(
        item,
        open_todo_items=open_todo_items,
        todo_item_is_actionable_open=todo_item_is_actionable_open,
        fallback_limit=MAX_STATUS_TODOS_PER_ROLE,
    )


def quiet_monitor_display_action(raw_action: str | None) -> str:
    return _quiet_monitor_display_action(
        raw_action,
        fallback_action=MONITOR_DISPLAY_FALLBACK_ACTION,
    )


def normalize_monitor_quiet_attention_display(item: dict[str, Any]) -> None:
    _normalize_monitor_quiet_attention_display(
        item,
        is_monitor_quiet_display_candidate=attention_item_is_monitor_quiet_display_candidate,
        display_fallback_action=MONITOR_DISPLAY_FALLBACK_ACTION,
        monitor_signal_waiting_on=MONITOR_SIGNAL_WAITING_ON,
        monitor_display_schema_version=MONITOR_DISPLAY_SCHEMA_VERSION,
        monitor_display_stop_condition=MONITOR_DISPLAY_STOP_CONDITION,
    )
