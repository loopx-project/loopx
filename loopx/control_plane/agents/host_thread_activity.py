"""Read-only activity of host threads bound to Goals.

A ``bind-agent-thread`` binding proves which host thread owns an agent lane; it
does not prove that the thread is working. Host observers read a host's own
local records and report one typed state per bound thread. Observers never
write to the host, and a record they cannot recognize is reported as
``unknown``, never as an open turn.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

HOST_THREAD_ACTIVITY_SCHEMA_VERSION = "loopx_host_thread_activity_v0"
HOST_DELIVERY_WINDOW_SCHEMA_VERSION = "loopx_host_delivery_window_v0"
MAX_OBSERVED_THREADS_PER_GOAL = 32
DEFAULT_DELIVERY_WINDOW_TOLERANCE = 2


class HostThreadState(str, Enum):
    # The host recorded a turn start without a matching end. A host that exits
    # mid-turn leaves this state behind, so consumers must weigh last_event_at.
    TURN_OPEN = "turn_open"
    IDLE = "idle"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


class HostThreadObservationCompleteness(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class HostThreadUnknownReason(str, Enum):
    UNSUPPORTED_HOST = "unsupported_host"
    STORE_UNAVAILABLE = "store_unavailable"
    THREAD_NOT_FOUND = "thread_not_found"
    RECORD_UNRECOGNIZED = "record_unrecognized"
    NO_TURN_MARKER = "no_turn_marker"


@dataclass(frozen=True)
class HostThreadActivity:
    state: HostThreadState
    reason: HostThreadUnknownReason | None = None
    turn_started_at: str | None = None
    last_turn_ended_at: str | None = None
    last_event_at: str | None = None

    def __post_init__(self) -> None:
        if (self.state is HostThreadState.UNKNOWN) != (self.reason is not None):
            raise ValueError("an unknown host thread state requires exactly one reason")

    @classmethod
    def unknown(cls, reason: HostThreadUnknownReason) -> HostThreadActivity:
        return cls(state=HostThreadState.UNKNOWN, reason=reason)

    def to_payload(self) -> dict[str, str]:
        payload = {
            "state": self.state.value,
            "reason": self.reason.value if self.reason else None,
            "turn_started_at": self.turn_started_at,
            "last_turn_ended_at": self.last_turn_ended_at,
            "last_event_at": self.last_event_at,
        }
        return {key: value for key, value in payload.items() if value is not None}


# Maps opaque host thread ids to their activity; ids it cannot resolve may be omitted.
HostThreadObserver = Callable[[Iterable[str]], Mapping[str, HostThreadActivity]]


def _goal_bindings(goal: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    coordination = goal.get("coordination")
    raw_bindings = coordination.get("thread_agent_bindings") if isinstance(coordination, Mapping) else None
    bindings: list[tuple[str, str, str]] = []
    for raw in raw_bindings if isinstance(raw_bindings, list) else []:
        if not isinstance(raw, Mapping):
            continue
        agent_id, host_surface, thread_id = (
            str(raw.get(key) or "").strip() for key in ("agent_id", "host_surface", "thread_id")
        )
        if agent_id and host_surface and thread_id:
            bindings.append((agent_id, host_surface, thread_id))
    return bindings


def attach_host_thread_activity(
    status_payload: dict[str, Any],
    *,
    observers: Mapping[str, HostThreadObserver],
    now: datetime | None = None,
) -> None:
    """Add ``host_thread_activity`` to each ``run_history`` Goal with bound threads.

    Thread ids stay in ``coordination``; the added rows carry only the agent,
    host surface and observed activity.
    """

    run_history = status_payload.get("run_history")
    goals = run_history.get("goals") if isinstance(run_history, Mapping) else None
    if not isinstance(goals, list):
        return
    bindings_by_goal = [
        (goal, _goal_bindings(goal)) for goal in goals if isinstance(goal, dict)
    ]
    requested: dict[str, set[str]] = {}
    for _goal, bindings in bindings_by_goal:
        for _agent_id, host_surface, thread_id in bindings[:MAX_OBSERVED_THREADS_PER_GOAL]:
            if host_surface in observers:
                requested.setdefault(host_surface, set()).add(thread_id)
    observed = {
        host_surface: observers[host_surface](sorted(thread_ids))
        for host_surface, thread_ids in requested.items()
    }
    observed_at = (now or datetime.now(timezone.utc)).isoformat()
    for goal, bindings in bindings_by_goal:
        if not bindings:
            continue
        threads = []
        for agent_id, host_surface, thread_id in bindings[:MAX_OBSERVED_THREADS_PER_GOAL]:
            if host_surface not in observers:
                activity = HostThreadActivity.unknown(HostThreadUnknownReason.UNSUPPORTED_HOST)
            else:
                activity = observed[host_surface].get(thread_id) or HostThreadActivity.unknown(
                    HostThreadUnknownReason.THREAD_NOT_FOUND
                )
            threads.append({"agent_id": agent_id, "host_surface": host_surface, **activity.to_payload()})
        goal["host_thread_activity"] = {
            "schema_version": HOST_THREAD_ACTIVITY_SCHEMA_VERSION,
            "observed_at": observed_at,
            "completeness": (
                HostThreadObservationCompleteness.COMPLETE
                if len(bindings) <= MAX_OBSERVED_THREADS_PER_GOAL
                else HostThreadObservationCompleteness.INCOMPLETE
            ).value,
            "threads": threads,
        }


class HostDeliveryWindowState(str, Enum):
    # The lane produced activity inside the cadence it is expected to keep.
    FRESH = "fresh"
    # No activity was observed inside the expected window. This names the
    # symptom only; it does not by itself name a cause.
    STALE = "stale"
    # No observation of this lane was available at all.
    MISSING = "missing"
    # The window could not be computed, so it is not reported as fresh.
    UNKNOWN = "unknown"


class HostDeliveryWindowUnknownReason(str, Enum):
    NO_EXPECTATION = "no_expectation"
    NO_OBSERVATION = "no_observation"
    UNPARSEABLE_OBSERVATION = "unparseable_observation"


_REASONED_DELIVERY_WINDOW_STATES = frozenset(
    {HostDeliveryWindowState.MISSING, HostDeliveryWindowState.UNKNOWN}
)


@dataclass(frozen=True)
class HostDeliveryExpectation:
    """The cadence one bound lane is expected to produce activity at.

    ``source`` names where the cadence came from, so a reader can tell an agreed
    loopX cadence from a host-reported one without guessing.
    """

    expected_interval_minutes: int | None
    source: str

    def __post_init__(self) -> None:
        if (
            self.expected_interval_minutes is not None
            and self.expected_interval_minutes <= 0
        ):
            raise ValueError("expected_interval_minutes must be positive or null")
        if not self.source.strip():
            raise ValueError("source is required for a delivery expectation")


@dataclass(frozen=True)
class HostDeliveryScope:
    """One bound lane a delivery window can be reported for.

    ``thread_id`` is the canonical binding the lane was observed through, so a
    provider can tell this lane's own automation from another lane's. It is
    carried only to resolve the expectation: the window payload never names it.
    """

    goal_id: str
    agent_id: str
    host_surface: str
    thread_id: str | None = None


@dataclass(frozen=True)
class HostDeliveryWindow:
    state: HostDeliveryWindowState
    reason: HostDeliveryWindowUnknownReason | None = None
    last_observed_at: str | None = None
    age_seconds: int | None = None
    age_hours: float | None = None
    expected_interval_minutes: int | None = None
    window_minutes: int | None = None
    tolerance: int | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        reasoned = self.state in _REASONED_DELIVERY_WINDOW_STATES
        if reasoned != (self.reason is not None):
            raise ValueError(
                "a missing or unknown delivery window requires exactly one reason"
            )

    @classmethod
    def unknown(
        cls,
        reason: HostDeliveryWindowUnknownReason,
        *,
        state: HostDeliveryWindowState = HostDeliveryWindowState.UNKNOWN,
        source: str | None = None,
    ) -> HostDeliveryWindow:
        return cls(state=state, reason=reason, source=source)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": HOST_DELIVERY_WINDOW_SCHEMA_VERSION,
            "state": self.state.value,
            "reason": self.reason.value if self.reason else None,
            "last_observed_at": self.last_observed_at,
            "age_seconds": self.age_seconds,
            "age_hours": self.age_hours,
            "expected_interval_minutes": self.expected_interval_minutes,
            "window_minutes": self.window_minutes,
            "tolerance": self.tolerance,
            "source": self.source,
        }
        return {key: value for key, value in payload.items() if value is not None}


def _parse_observation_time(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_host_delivery_window(
    activity: HostThreadActivity | None,
    expectation: HostDeliveryExpectation | None,
    *,
    now: datetime | None = None,
    tolerance: int = DEFAULT_DELIVERY_WINDOW_TOLERANCE,
) -> HostDeliveryWindow:
    """Compare one observed lane against the cadence it is expected to keep.

    Absence of evidence is never reported as healthy: an unobserved lane is
    ``missing`` and an incomputable window is ``unknown``. Neither is ``fresh``.
    """

    if tolerance < 1:
        raise ValueError("tolerance must be at least one interval")
    if expectation is None or not expectation.expected_interval_minutes:
        return HostDeliveryWindow.unknown(HostDeliveryWindowUnknownReason.NO_EXPECTATION)
    if activity is None or activity.state is HostThreadState.UNKNOWN:
        return HostDeliveryWindow.unknown(
            HostDeliveryWindowUnknownReason.NO_OBSERVATION,
            state=HostDeliveryWindowState.MISSING,
            source=expectation.source,
        )
    observed = _parse_observation_time(activity.last_event_at)
    if observed is None:
        return HostDeliveryWindow.unknown(
            HostDeliveryWindowUnknownReason.UNPARSEABLE_OBSERVATION,
            source=expectation.source,
        )
    window_minutes = expectation.expected_interval_minutes * tolerance
    reference = now or datetime.now(timezone.utc)
    age_seconds = max(0, int((reference - observed).total_seconds()))
    return HostDeliveryWindow(
        state=(
            HostDeliveryWindowState.FRESH
            if age_seconds <= window_minutes * 60
            else HostDeliveryWindowState.STALE
        ),
        last_observed_at=activity.last_event_at,
        age_seconds=age_seconds,
        age_hours=round(age_seconds / 3600, 2),
        expected_interval_minutes=expectation.expected_interval_minutes,
        window_minutes=window_minutes,
        tolerance=tolerance,
        source=expectation.source,
    )


# Maps bound lanes to the cadence each is expected to keep; lanes it cannot
# resolve are omitted rather than guessed.
HostDeliveryExpectationProvider = Callable[
    [Iterable[HostDeliveryScope]], Mapping[HostDeliveryScope, HostDeliveryExpectation]
]


def _bound_thread_ids(goal: Mapping[str, Any]) -> dict[tuple[str, str], str | None]:
    """Map each ``(agent, surface)`` lane of a Goal to its canonical thread.

    A lane the Goal binds to more than one thread has no single identity, so it
    maps to ``None`` and its window stays ``unknown``.
    """

    resolved: dict[tuple[str, str], str | None] = {}
    for agent_id, host_surface, thread_id in _goal_bindings(goal):
        key = (agent_id, host_surface)
        resolved[key] = None if key in resolved else thread_id
    return resolved


def _scope_from_row(
    goal_id: str, row: Mapping[str, Any], bound: Mapping[tuple[str, str], str | None]
) -> HostDeliveryScope | None:
    agent_id = str(row.get("agent_id") or "").strip()
    host_surface = str(row.get("host_surface") or "").strip()
    if not goal_id or not agent_id or not host_surface:
        return None
    return HostDeliveryScope(
        goal_id=goal_id,
        agent_id=agent_id,
        host_surface=host_surface,
        thread_id=bound.get((agent_id, host_surface)),
    )


def attach_host_delivery_windows(
    status_payload: dict[str, Any],
    *,
    expectations: HostDeliveryExpectationProvider,
    tolerance: int = DEFAULT_DELIVERY_WINDOW_TOLERANCE,
    now: datetime | None = None,
) -> None:
    """Add ``delivery_window`` to each observed lane of ``host_thread_activity``.

    This reads the projection that ``attach_host_thread_activity`` already
    produced, so no host record is read twice. Each lane is resolved through the
    canonical binding the Goal records, so a cadence installed for another
    thread of the same Goal is not read as this lane's. A lane the expectation
    provider cannot resolve is reported as ``unknown``, never as healthy.
    """

    run_history = status_payload.get("run_history")
    goals = run_history.get("goals") if isinstance(run_history, Mapping) else None
    if not isinstance(goals, list):
        return
    rows_by_goal: list[
        tuple[str, list[dict[str, Any]], dict[tuple[str, str], str | None]]
    ] = []
    scopes: list[HostDeliveryScope] = []
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        activity = goal.get("host_thread_activity")
        threads = activity.get("threads") if isinstance(activity, Mapping) else None
        if not isinstance(threads, list):
            continue
        rows = [row for row in threads if isinstance(row, dict)]
        if not rows:
            continue
        goal_id = str(goal.get("id") or "").strip()
        bound = _bound_thread_ids(goal)
        rows_by_goal.append((goal_id, rows, bound))
        for row in rows:
            scope = _scope_from_row(goal_id, row, bound)
            if scope is not None:
                scopes.append(scope)
    resolved = expectations(scopes) if scopes else {}
    for goal_id, rows, bound in rows_by_goal:
        for row in rows:
            scope = _scope_from_row(goal_id, row, bound)
            row["delivery_window"] = build_host_delivery_window(
                _activity_from_row(row),
                resolved.get(scope) if scope is not None else None,
                now=now,
                tolerance=tolerance,
            ).to_payload()


def _activity_from_row(row: Mapping[str, Any]) -> HostThreadActivity:
    state_value = str(row.get("state") or "")
    try:
        state = HostThreadState(state_value)
    except ValueError:
        return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
    reason_value = str(row.get("reason") or "")
    reason: HostThreadUnknownReason | None = None
    if reason_value:
        try:
            reason = HostThreadUnknownReason(reason_value)
        except ValueError:
            reason = HostThreadUnknownReason.RECORD_UNRECOGNIZED
    return HostThreadActivity(
        state=state,
        reason=reason,
        turn_started_at=row.get("turn_started_at"),
        last_turn_ended_at=row.get("last_turn_ended_at"),
        last_event_at=row.get("last_event_at"),
    )
