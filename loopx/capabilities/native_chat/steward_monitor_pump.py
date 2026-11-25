"""Idle-pump dispatch loops for Steward portfolio and managed-session watches."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import logging
import time
from typing import Protocol

from ...agent_registry import load_goal_from_registry
from ...control_plane.scheduler.monitor_todo import monitor_todo_is_due
from .steward_monitor_turns import (
    FOLLOWUP_TARGET_PREFIX,
    StewardControllerProtocol,
    StewardMonitorProtocol,
    StewardTodoRecord,
    _coerce_dict,
    monitor_turn_id,
    resolve_advancement_turn_id,
)

_LOG = logging.getLogger(__name__)


class _CancelCheck(Protocol):
    def __call__(self) -> bool: ...


class _BindingsReaderProtocol(Protocol):
    def read(self) -> Mapping[str, object]: ...


def _ensure_float_cache(
    controller: StewardControllerProtocol, attr: str
) -> dict[str, float]:
    cache = getattr(controller, attr, None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(controller, attr, cache)
    return cache


def _ensure_bool_cache(
    controller: StewardControllerProtocol, attr: str
) -> dict[str, bool]:
    cache = getattr(controller, attr, None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(controller, attr, cache)
    return cache


def _report_config_for_goal(
    controller: StewardControllerProtocol, goal_id: str
) -> Mapping[str, object]:
    goal = load_goal_from_registry(controller.registry_path, goal_id)
    control_plane = goal.get("control_plane") if isinstance(goal, Mapping) else None
    report_config = (
        control_plane.get("periodic_report")
        if isinstance(control_plane, Mapping)
        else None
    )
    return report_config if isinstance(report_config, Mapping) else {}


def _remaining_due_seconds(next_due_at: str) -> float:
    due_dt = datetime.fromisoformat(next_due_at.replace("Z", "+00:00"))
    return (due_dt - datetime.now(timezone.utc)).total_seconds()


def _schedule_soonest_monitor_backoff(
    cache: dict[str, float],
    binding_id: str,
    todos: Sequence[StewardTodoRecord],
) -> None:
    all_monitors = [
        row
        for row in todos
        if row.get("task_class") == "continuous_monitor"
        and row.get("status") == "open"
        and isinstance(row.get("target_key"), str)
        and not row["target_key"].startswith("managed-session-watch.")
        and not row["target_key"].startswith(FOLLOWUP_TARGET_PREFIX)
        and row.get("next_due_at")
    ]
    if not all_monitors:
        return
    soonest = min(
        all_monitors,
        key=lambda row: datetime.fromisoformat(
            row["next_due_at"].replace("Z", "+00:00")
        ),
    )
    remaining = _remaining_due_seconds(soonest["next_due_at"])
    if remaining > 0:
        cache[binding_id] = time.monotonic() + min(60, remaining)


def _process_steward_binding(
    controller: StewardControllerProtocol,
    service: StewardMonitorProtocol,
    binding: Mapping[str, object],
    *,
    cache: dict[str, float],
    fg_checked: dict[str, bool],
    changed: list[dict[str, object]],
) -> None:
    from .steward_turn import StewardTurnHost

    actor_raw = binding.get("managed_actor")
    if binding.get("enabled") is not True or not isinstance(actor_raw, Mapping):
        return
    actor = _coerce_dict(actor_raw)
    session_id = str(actor["session_id"])
    parent_session = controller.store.load_session(session_id)
    if not parent_session or parent_session.get("status") == "closed":
        return
    active_fg_id = parent_session.get("active_turn_id")
    queued_fg = controller.store.queued_turns(session_id)
    human_idle = not active_fg_id and not queued_fg
    active_fg_turn = (
        controller.store.load_turn(session_id, str(active_fg_id))
        if active_fg_id
        else None
    )
    fg_native_active = bool(
        (isinstance(active_fg_turn, Mapping) and active_fg_turn.get("origin") == "native_loopx")
        or any(isinstance(row, Mapping) and row.get("origin") == "native_loopx" for row in queued_fg)
    )
    binding_id = str(binding["binding_id"])
    bg_session_id = StewardTurnHost.background_session_id(binding_id)
    bg_session = controller.store.load_session(bg_session_id)
    recover_fn = getattr(controller, "recover_exited_turn", None)
    if (
        bg_session is not None
        and bg_session.get("active_turn_id")
        and callable(recover_fn)
    ):
        recover_fn(bg_session_id)
        bg_session = controller.store.load_session(bg_session_id)
    if bg_session is not None and bg_session.get("status") == "closed":
        bg_idle = False
        bg_active = False
    else:
        bg_active = bool(
            bg_session is not None
            and (
                bg_session.get("active_turn_id")
                or controller.store.queued_turns(bg_session_id)
            )
        )
        bg_idle = not bg_active
    # Background monitor polls and foreground native turns share the same
    # steward Goal's runs/ ledger; serialize them against each other while
    # keeping human conversations parallel with steward.background.
    fg_can_dispatch = human_idle and not bg_active
    bg_can_dispatch = bg_idle and not fg_native_active
    if not fg_can_dispatch and not bg_can_dispatch:
        if human_idle and binding_id in cache:
            fg_checked[binding_id] = False
        return
    now_mono = time.monotonic()
    bypass_fg_throttle = (
        fg_can_dispatch and binding_id in cache and fg_checked.get(binding_id) is False
    )
    if not bypass_fg_throttle and cache.get(binding_id, 0) > now_mono:
        return
    # An ephemeral wake hint only, never authority. Configuration clears it immediately;
    # external edits are observed within a minute. Every dispatch rechecks native state.
    cache[binding_id] = now_mono + 60
    fg_checked[binding_id] = fg_can_dispatch
    # The existing pump also observes the optional report frontier without adding timers.
    if fg_can_dispatch:
        report_config = _report_config_for_goal(controller, str(actor["goal_id"]))
        if (
            report_config.get("enabled") is True
            and report_config.get("profile_preset") == "portfolio-progress"
        ):
            from .steward_reports import StewardReports

            try:
                accepted, created = StewardReports(controller).frontier(
                    _coerce_dict(binding)
                )
                if created:
                    changed.append(_coerce_dict(accepted))
                    if bg_idle:
                        cache.pop(binding_id, None)
                    return
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                _LOG.warning(
                    "An original steward report is unavailable; its window and other work are retained"
                )
    todos = service.todos(actor)
    if bg_can_dispatch:
        due = service.due_monitors(actor, todos=todos)
        if due:
            dispatched = False
            for todo in due:
                try:
                    accepted, created = StewardTurnHost(controller).submit(
                        binding_id,
                        turn_instance_id=monitor_turn_id(todo),
                        todo_id=todo["todo_id"],
                        work_kind="monitor",
                        monitor_deadline=todo["next_due_at"],
                    )
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    _LOG.warning(
                        "A native steward monitor is unavailable; original work retained"
                    )
                    continue
                if created:
                    changed.append(_coerce_dict(accepted))
                    dispatched = True
                    break
                if isinstance(accepted, dict) and accepted.get("status") in {
                    "queued",
                    "running",
                }:
                    dispatched = True
                    break
            if dispatched:
                if fg_can_dispatch:
                    fg_checked[binding_id] = False
                return
    advancement_dispatched = False
    if fg_can_dispatch:
        for adv in service.runnable_advancement_todos(actor, todos=todos):
            turn_id_token = resolve_advancement_turn_id(
                controller.store, actor, adv
            )
            if turn_id_token is None:
                continue
            accepted, created = StewardTurnHost(controller).submit(
                binding_id,
                turn_instance_id=turn_id_token,
                todo_id=adv["todo_id"],
                work_kind="native_turn",
            )
            if created:
                changed.append(_coerce_dict(accepted))
            advancement_dispatched = True
            break
    if not advancement_dispatched:
        _schedule_soonest_monitor_backoff(cache, binding_id, todos)


def _process_managed_session(
    controller: StewardControllerProtocol,
    monitor: StewardMonitorProtocol,
    session: Mapping[str, object],
    *,
    cache: dict[str, float],
    changed: list[dict[str, object]],
) -> None:
    from .steward_managed_mode import ManagedSessionTurn

    session_id = str(session["session_id"])
    managed_agent_id = session.get("managed_agent_id")
    if (
        not managed_agent_id
        or session.get("active_turn_id")
        or session.get("status") == "closed"
        or controller.store.queued_turns(session_id)
    ):
        return
    key = "session." + session_id
    if cache.get(key, 0) > time.monotonic():
        return
    cache[key] = time.monotonic() + 60
    service = ManagedSessionTurn(controller)
    request = service._request(session_id, str(managed_agent_id), "preview")
    service.admit(request)
    report_config = _report_config_for_goal(controller, str(session["goal_id"]))
    route_ref = "managed-session:" + session_id
    if (
        report_config.get("enabled") is True
        and report_config.get("profile_preset") == "session-task"
        and report_config.get("route_ref") == route_ref
    ):
        from .session_calendar_tasks import SessionCalendarTasks
        from .steward_reports import ManagedSessionReports

        try:
            calendar_tasks: ManagedSessionReports = SessionCalendarTasks(controller)
            accepted, created = calendar_tasks.frontier(session_id)
            if created:
                changed.append(_coerce_dict(accepted))
                return
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            _LOG.warning(
                "A managed calendar task is unavailable; its original window and work are retained"
            )
    if (
        report_config.get("enabled") is True
        and report_config.get("profile_preset") == "weekly-progress"
        and report_config.get("route_ref") == route_ref
    ):
        from .steward_reports import ManagedSessionReports

        try:
            accepted, created = ManagedSessionReports(controller).frontier(session_id)
            if created:
                changed.append(_coerce_dict(accepted))
                return
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            _LOG.warning(
                "A managed Session report is unavailable; its original window and other work are retained"
            )
    rows = [
        row
        for row in monitor.todos(request["actor"])
        if row.get("target_key") == "managed-session-watch." + session_id
    ]
    if len(rows) != 1:
        if rows:
            raise ValueError("the original managed Session watch is ambiguous")
        return
    todo = rows[0]
    if not monitor_todo_is_due(todo):
        if todo.get("status") == "open" and todo.get("next_due_at"):
            remaining = _remaining_due_seconds(todo["next_due_at"])
            cache[key] = time.monotonic() + min(60, max(0, remaining))
        return
    accepted, created = service.submit(
        session_id,
        agent_id=str(managed_agent_id),
        operation_id=monitor_turn_id(todo),
        todo_id=todo["todo_id"],
        monitor_deadline=todo["next_due_at"],
    )
    if created:
        changed.append(_coerce_dict(accepted))


def pump_steward_monitors(
    controller: StewardControllerProtocol,
    *,
    cancelled: _CancelCheck = lambda: False,
) -> list[dict[str, object]]:
    """One cheap frontier check in the existing host; never consume messages."""
    from .steward_monitor import StewardMonitor

    bindings: _BindingsReaderProtocol | None = getattr(
        getattr(controller, "project_contexts", None), "conversation_bindings", None
    )
    if controller.closed.is_set():
        return []
    service = StewardMonitor(controller)
    changed: list[dict[str, object]] = []
    cache = _ensure_float_cache(controller, "_steward_monitor_next_check")
    fg_checked = _ensure_bool_cache(controller, "_steward_monitor_fg_idle_checked")
    raw_bindings = bindings.read().get("bindings") if bindings is not None else []
    binding_rows = (
        raw_bindings
        if isinstance(raw_bindings, Sequence)
        and not isinstance(raw_bindings, (str, bytes))
        else []
    )
    for binding in binding_rows:
        if cancelled():
            break
        if not isinstance(binding, Mapping):
            continue
        try:
            _process_steward_binding(
                controller,
                service,
                binding,
                cache=cache,
                fg_checked=fg_checked,
                changed=changed,
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            # One unreadable conversation or unavailable provider must not starve other monitors.
            _LOG.warning(
                "A native steward monitor is unavailable; original work retained"
            )
    return changed + pump_managed_session_monitors(controller, cancelled=cancelled)


def pump_managed_session_monitors(
    controller: StewardControllerProtocol,
    *,
    cancelled: _CancelCheck = lambda: False,
) -> list[dict[str, object]]:
    """Continue explicitly authored native watches through the same idle pump."""
    from .steward_monitor import StewardMonitor

    changed: list[dict[str, object]] = []
    monitor = StewardMonitor(controller)
    cache = _ensure_float_cache(controller, "_steward_monitor_next_check")
    for session in controller.store.list_sessions():
        if cancelled():
            break
        try:
            _process_managed_session(
                controller, monitor, session, cache=cache, changed=changed
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            _LOG.warning(
                "A managed Session watch is unavailable; its original task and deadline are retained"
            )
    return changed
