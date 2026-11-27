"""Idle-pump dispatch loops for Steward portfolio and managed-session watches."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import logging
import re
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
_DEFAULT_POLL_INTERVAL_SECONDS = 60.0
_MAX_FAILURE_BACKOFF_SECONDS = 3600.0
_MAX_SUMMARY_CHARS = 160
_POSIX_PATH_RE = re.compile(r"(?<![\w.:/])(?:/[^\s/'\":;,)\]}]+){2,}/?")
_TILDE_PATH_RE = re.compile(r"(?<![\w.:])~/[^\s'\";,)\]}]+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]*")


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


def _ensure_failure_cache(
    controller: StewardControllerProtocol,
) -> dict[str, tuple[int, str]]:
    cache = getattr(controller, "_steward_monitor_failure_state", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(controller, "_steward_monitor_failure_state", cache)
    return cache


def _failure_key(lane: str, owner_id: str, item_id: str = "") -> str:
    return f"failure.{lane}.{owner_id}.{item_id}" if item_id else f"failure.{lane}.{owner_id}"


_SESSION_FAILURE_LANES = ("watch.session", "calendar.session", "report.session")


def _sanitize_exception_summary(exc: BaseException) -> tuple[str, str]:
    exc_name = type(exc).__name__
    raw = " ".join(str(exc).split())
    redacted = _POSIX_PATH_RE.sub("<path>", raw)
    redacted = _TILDE_PATH_RE.sub("<path>", redacted)
    redacted = _WINDOWS_PATH_RE.sub("<path>", redacted).strip()
    if len(redacted) > _MAX_SUMMARY_CHARS:
        redacted = redacted[: _MAX_SUMMARY_CHARS - 3].rstrip() + "..."
    return exc_name, redacted or exc_name


def _failure_active(cache: dict[str, float], failure_key: str, now_mono: float) -> bool:
    return bool(failure_key) and cache.get(failure_key, 0.0) > now_mono


def _any_session_failure_active(
    cache: dict[str, float], session_id: str, now_mono: float
) -> bool:
    return any(
        _failure_active(cache, _failure_key(lane, session_id), now_mono)
        for lane in _SESSION_FAILURE_LANES
    )


def _clear_failure(
    cache: dict[str, float],
    failures: dict[str, tuple[int, str]],
    failure_key: str,
) -> None:
    if not failure_key:
        return
    cache.pop(failure_key, None)
    failures.pop(failure_key, None)


def _record_failure(
    cache: dict[str, float],
    failures: dict[str, tuple[int, str]],
    failure_key: str,
    base_message: str,
    exc: BaseException,
    *,
    now_mono: float,
) -> float:
    exc_name, summary = _sanitize_exception_summary(exc)
    signature = f"{exc_name}:{summary}"
    count, prev_sig = failures.get(failure_key, (0, "")) if failure_key else (0, "")
    next_count = count + 1
    delay: float = float(
        min(
            _MAX_FAILURE_BACKOFF_SECONDS,
            _DEFAULT_POLL_INTERVAL_SECONDS * (1 << min(6, next_count - 1)),
        )
    )
    if failure_key:
        failures[failure_key] = (next_count, signature)
        cache[failure_key] = max(cache.get(failure_key, 0.0), now_mono + delay)
    if signature != prev_sig:
        _LOG.warning("%s (%s: %s)", base_message, exc_name, summary)
    else:
        _LOG.debug(
            "%s (suppressed repeat #%d: %s: %s)",
            base_message,
            next_count,
            exc_name,
            summary,
        )
    return delay


def clear_steward_monitor_check_hints(
    controller: StewardControllerProtocol, owner_key: str
) -> None:
    """Reset ephemeral pump throttle and failure-backoff hints for one owner."""
    exact_keys = {
        owner_key,
        f"session.{owner_key}",
        *(_failure_key(lane, owner_key) for lane in ("binding", "report", *_SESSION_FAILURE_LANES)),
    }
    prefixes = (f"failure.monitor.{owner_key}.", f"failure.advancement.{owner_key}.")
    for attr in ("_steward_monitor_next_check", "_steward_monitor_failure_state"):
        cache = getattr(controller, attr, None)
        if not isinstance(cache, dict):
            continue
        for key in list(cache.keys()):
            if key in exact_keys or any(key.startswith(prefix) for prefix in prefixes):
                cache.pop(key, None)


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
    failures: dict[str, tuple[int, str]],
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
    cache[binding_id] = now_mono + _DEFAULT_POLL_INTERVAL_SECONDS
    fg_checked[binding_id] = fg_can_dispatch
    # The existing pump also observes the optional report frontier without adding timers.
    if fg_can_dispatch:
        report_config = _report_config_for_goal(controller, str(actor["goal_id"]))
        report_fail_key = _failure_key("report", binding_id)
        if (
            report_config.get("enabled") is True
            and report_config.get("profile_preset") == "portfolio-progress"
            and not _failure_active(cache, report_fail_key, now_mono)
        ):
            from .steward_reports import StewardReports

            try:
                accepted, created = StewardReports(controller).frontier(
                    _coerce_dict(binding)
                )
                _clear_failure(cache, failures, report_fail_key)
                if created:
                    changed.append(_coerce_dict(accepted))
                    if bg_idle:
                        cache.pop(binding_id, None)
                    return
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                _record_failure(
                    cache,
                    failures,
                    report_fail_key,
                    "An original steward report is unavailable; its window and other work are retained",
                    exc,
                    now_mono=now_mono,
                )
    todos = service.todos(actor)
    if bg_can_dispatch:
        due = service.due_monitors(actor, todos=todos)
        if due:
            dispatched = False
            for todo in due:
                monitor_fail_key = _failure_key("monitor", binding_id, str(todo["todo_id"]))
                if _failure_active(cache, monitor_fail_key, now_mono):
                    continue
                try:
                    accepted, created = StewardTurnHost(controller).submit(
                        binding_id,
                        turn_instance_id=monitor_turn_id(todo),
                        todo_id=todo["todo_id"],
                        work_kind="monitor",
                        monitor_deadline=todo["next_due_at"],
                    )
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                    _record_failure(
                        cache,
                        failures,
                        monitor_fail_key,
                        "A native steward monitor is unavailable; original work retained",
                        exc,
                        now_mono=now_mono,
                    )
                    continue
                _clear_failure(cache, failures, monitor_fail_key)
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
            adv_fail_key = _failure_key("advancement", binding_id, str(adv["todo_id"]))
            if _failure_active(cache, adv_fail_key, now_mono):
                continue
            try:
                accepted, created = StewardTurnHost(controller).submit(
                    binding_id,
                    turn_instance_id=turn_id_token,
                    todo_id=adv["todo_id"],
                    work_kind="native_turn",
                )
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                _record_failure(
                    cache,
                    failures,
                    adv_fail_key,
                    "A native steward monitor is unavailable; original work retained",
                    exc,
                    now_mono=now_mono,
                )
                continue
            _clear_failure(cache, failures, adv_fail_key)
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
    failures: dict[str, tuple[int, str]],
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
    now_mono = time.monotonic()
    key = "session." + session_id
    if cache.get(key, 0) > now_mono:
        return
    cache[key] = now_mono + _DEFAULT_POLL_INTERVAL_SECONDS
    service = ManagedSessionTurn(controller)
    request = service._request(session_id, str(managed_agent_id), "preview")
    service.admit(request)
    report_config = _report_config_for_goal(controller, str(session["goal_id"]))
    route_ref = "managed-session:" + session_id
    calendar_fail_key = _failure_key("calendar.session", session_id)
    if (
        report_config.get("enabled") is True
        and report_config.get("profile_preset") == "session-task"
        and report_config.get("route_ref") == route_ref
        and not _failure_active(cache, calendar_fail_key, now_mono)
    ):
        from .session_calendar_tasks import SessionCalendarTasks
        from .steward_reports import ManagedSessionReports

        try:
            calendar_tasks: ManagedSessionReports = SessionCalendarTasks(controller)
            accepted, created = calendar_tasks.frontier(session_id)
            _clear_failure(cache, failures, calendar_fail_key)
            if created:
                changed.append(_coerce_dict(accepted))
                return
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            _record_failure(
                cache,
                failures,
                calendar_fail_key,
                "A managed calendar task is unavailable; its original window and work are retained",
                exc,
                now_mono=now_mono,
            )
    report_fail_key = _failure_key("report.session", session_id)
    if (
        report_config.get("enabled") is True
        and report_config.get("profile_preset") == "weekly-progress"
        and report_config.get("route_ref") == route_ref
        and not _failure_active(cache, report_fail_key, now_mono)
    ):
        from .steward_reports import ManagedSessionReports

        try:
            accepted, created = ManagedSessionReports(controller).frontier(session_id)
            _clear_failure(cache, failures, report_fail_key)
            if created:
                changed.append(_coerce_dict(accepted))
                return
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            _record_failure(
                cache,
                failures,
                report_fail_key,
                "A managed Session report is unavailable; its original window and other work are retained",
                exc,
                now_mono=now_mono,
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
        if (
            todo.get("status") == "open"
            and todo.get("next_due_at")
            and not _any_session_failure_active(cache, session_id, now_mono)
        ):
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
    failures = _ensure_failure_cache(controller)
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
        raw_binding_id = binding.get("binding_id")
        binding_fail_key = (
            _failure_key("binding", raw_binding_id)
            if isinstance(raw_binding_id, str) and raw_binding_id
            else ""
        )
        now_mono = time.monotonic()
        if _failure_active(cache, binding_fail_key, now_mono):
            continue
        try:
            _process_steward_binding(
                controller,
                service,
                binding,
                cache=cache,
                failures=failures,
                fg_checked=fg_checked,
                changed=changed,
            )
            _clear_failure(cache, failures, binding_fail_key)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            # One unreadable conversation or unavailable provider must not starve other monitors.
            _record_failure(
                cache,
                failures,
                binding_fail_key,
                "A native steward monitor is unavailable; original work retained",
                exc,
                now_mono=now_mono,
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
    failures = _ensure_failure_cache(controller)
    for session in controller.store.list_sessions():
        if cancelled():
            break
        raw_session_id = session.get("session_id") if isinstance(session, Mapping) else None
        watch_fail_key = (
            _failure_key("watch.session", raw_session_id)
            if isinstance(raw_session_id, str) and raw_session_id
            else ""
        )
        now_mono = time.monotonic()
        if _failure_active(cache, watch_fail_key, now_mono):
            continue
        try:
            _process_managed_session(
                controller,
                monitor,
                session,
                cache=cache,
                failures=failures,
                changed=changed,
            )
            _clear_failure(cache, failures, watch_fail_key)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            _record_failure(
                cache,
                failures,
                watch_fail_key,
                "A managed Session watch is unavailable; its original task and deadline are retained",
                exc,
                now_mono=now_mono,
            )
    return changed
