from __future__ import annotations

from typing import Any, Mapping

from datetime import datetime, timedelta

from ..runtime.time import now_utc
from ..todos.contract import (
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_resume_when,
    normalize_todo_status,
    normalize_todo_task_class,
    normalize_todo_watch_only,
)
from .time import parse_scheduler_timestamp
from .state_transition_rules import project_monitor_todo_schedule


parse_monitor_timestamp = parse_scheduler_timestamp


def monitor_cadence_delta(value: Any) -> timedelta | None:
    projected = project_monitor_todo_schedule(
        generated_at="1970-01-01T00:00:00Z",
        cadence=value,
    )
    if projected.cadence_seconds is None:
        return None
    return timedelta(seconds=projected.cadence_seconds)


def monitor_next_due_at(
    *,
    generated_at: str,
    cadence: Any = None,
    explicit_next_due_at: Any = None,
) -> str | None:
    return project_monitor_todo_schedule(
        generated_at=generated_at,
        cadence=cadence,
        explicit_next_due_at=explicit_next_due_at,
    ).next_due_at


def monitor_todo_task_class(item: Mapping[str, Any], *, task_text: str | None = None) -> str:
    text = str(item.get("text") or "") if task_text is None else task_text
    return normalize_todo_task_class(
        item.get("task_class"),
        text=text,
        action_kind=item.get("action_kind"),
    )


def monitor_todo_is_actionable_open(item: dict[str, Any]) -> bool:
    if item.get("done") is True:
        return False
    status = normalize_todo_status(item.get("status")) or TODO_STATUS_OPEN
    if status != TODO_STATUS_OPEN:
        return False
    if normalize_todo_resume_when(item.get("resume_when")):
        return item.get("resume_ready") is True
    return True


def monitor_todo_next_due_at(item: dict[str, Any]) -> datetime | None:
    return parse_monitor_timestamp(item.get("next_due_at"))


def monitor_todo_has_schedule(item: dict[str, Any]) -> bool:
    return monitor_todo_next_due_at(item) is not None


def monitor_todo_expires_at(item: dict[str, Any]) -> datetime | None:
    return parse_monitor_timestamp(item.get("expires_at"))


def monitor_todo_is_expired(item: dict[str, Any], *, now: datetime | None = None) -> bool:
    expires_at = monitor_todo_expires_at(item)
    if expires_at is None:
        return False
    current_time = now or now_utc()
    return expires_at <= current_time


def monitor_todo_is_due(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
    task_text: str | None = None,
) -> bool:
    if not monitor_todo_is_actionable_open(item):
        return False
    if monitor_todo_task_class(item, task_text=task_text) != TODO_TASK_CLASS_MONITOR:
        return False
    if monitor_todo_is_expired(item, now=now):
        return False
    next_due_at = monitor_todo_next_due_at(item)
    if next_due_at is None:
        return False
    current_time = now or now_utc()
    return next_due_at <= current_time


def monitor_todo_missing_schedule(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
    task_text: str | None = None,
) -> bool:
    if not monitor_todo_is_actionable_open(item):
        return False
    if monitor_todo_task_class(item, task_text=task_text) != TODO_TASK_CLASS_MONITOR:
        return False
    if monitor_todo_is_expired(item, now=now):
        return False
    if normalize_todo_watch_only(item.get("watch_only")) is True:
        return False
    return not monitor_todo_has_schedule(item)
