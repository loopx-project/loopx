"""Status attention projections inside the `status` bounded context."""

from __future__ import annotations

from typing import Any

from ..todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
)
from ..todos.todo_summary import (
    normalize_todo_text,
    open_todo_items,
    todo_item_is_actionable_open,
)
from ..work_items.attention_queue import (
    build_attention_queue_projection as _build_attention_queue_projection,
)
from ..work_items.autonomous_candidates import (
    MAX_AUTONOMOUS_TODO_CANDIDATES,
    autonomous_backlog_candidates as _autonomous_backlog_candidates,
    autonomous_monitor_candidates as _autonomous_monitor_candidates,
)
from .monitor_display_projection import MONITOR_SIGNAL_WAITING_ON


MAX_AUTONOMOUS_BACKLOG_CANDIDATES = MAX_AUTONOMOUS_TODO_CANDIDATES


def autonomous_backlog_candidates(
    items: list[dict[str, Any]],
    *,
    limit: int = MAX_AUTONOMOUS_BACKLOG_CANDIDATES,
) -> dict[str, Any] | None:
    return _autonomous_backlog_candidates(
        items,
        open_todo_items=open_todo_items,
        todo_item_is_actionable_open=todo_item_is_actionable_open,
        normalize_todo_text=normalize_todo_text,
        advancement_task_class=TODO_TASK_CLASS_ADVANCEMENT,
        limit=limit,
    )


def autonomous_monitor_candidates(
    items: list[dict[str, Any]],
    *,
    limit: int = MAX_AUTONOMOUS_BACKLOG_CANDIDATES,
) -> dict[str, Any] | None:
    return _autonomous_monitor_candidates(
        items,
        open_todo_items=open_todo_items,
        todo_item_is_actionable_open=todo_item_is_actionable_open,
        normalize_todo_text=normalize_todo_text,
        monitor_task_class=TODO_TASK_CLASS_MONITOR,
        monitor_signal_waiting_on=MONITOR_SIGNAL_WAITING_ON,
        limit=limit,
    )


def build_attention_queue_projection(
    *,
    items: list[dict[str, Any]],
    goal_id_filter: str | None,
    autonomous_backlog_candidates: dict[str, Any] | None,
    autonomous_monitor_candidates: dict[str, Any] | None,
) -> dict[str, Any]:
    return _build_attention_queue_projection(
        items=items,
        goal_id_filter=goal_id_filter,
        autonomous_backlog_candidates=autonomous_backlog_candidates,
        autonomous_monitor_candidates=autonomous_monitor_candidates,
        monitor_signal_waiting_on=MONITOR_SIGNAL_WAITING_ON,
    )
