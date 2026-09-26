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
MAX_OBSERVED_THREADS_PER_GOAL = 32


class HostThreadState(str, Enum):
    # The host recorded a turn start without a matching end. A host that exits
    # mid-turn leaves this state behind, so consumers must weigh last_event_at.
    TURN_OPEN = "turn_open"
    IDLE = "idle"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


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
    return bindings[:MAX_OBSERVED_THREADS_PER_GOAL]


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
        for _agent_id, host_surface, thread_id in bindings:
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
        for agent_id, host_surface, thread_id in bindings:
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
            "threads": threads,
        }
