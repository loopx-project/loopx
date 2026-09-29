"""Execution facts behind the worker lifecycle projection: read, never lock.

`executing` has to be backed by something that runs, not by a fresh Todo
timestamp. This module gathers the facts LoopX already keeps about running
work, keyed by agent id, so the agent management projection can derive
`executing` and `unknown` from them instead of from activity age:

- the Turn lane holder record, one per Goal and agent, read through
  `turn_lane_liveness`; a delegated member executes inside its own lane too;
- the delegation worker's operation lock, whose holder is the detached worker
  process executing a delegated Turn;
- the task lease on a claimed Todo, active, expired or released.

It writes nothing, takes no lock and keeps no state of its own: every input is
owned elsewhere and this is a bounded read model over them. Absence of a fact
is reported as absence, never as "not running".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ...file_lock import LOCK_HOLDER_LIVE, lock_holder_liveness
from ..collaboration.inbox import _read as read_manager_context_record
from ..collaboration.inbox import _root as manager_context_root
from ..coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from ..runtime.time import now_utc
from ..todos.contract import normalize_todo_claimed_by
from ..turn_driver.lane_fence import (
    TURN_LANE_ABSENT,
    TURN_LANE_DEAD,
    TURN_LANE_FOREIGN_HOST,
    TURN_LANE_LIVE,
    TURN_LANE_RELEASED,
    TURN_LANE_UNREADABLE,
    turn_lane_liveness,
    turn_lane_target,
)
from ..work_items.local_lease_record import TaskLeaseError, read_lease
from ..work_items.task_lease import lease_expires_at, task_lease_dir
from .management_projection import projected_agent_goals

# One agent can hold one lane per Goal; the row reports the strongest fact.
# Unknowns outrank a plain "not running": a reader must fail closed on them.
_LANE_PRECEDENCE = (
    TURN_LANE_LIVE,
    TURN_LANE_FOREIGN_HOST,
    TURN_LANE_UNREADABLE,
    TURN_LANE_DEAD,
    TURN_LANE_RELEASED,
    TURN_LANE_ABSENT,
)
LEASE_STATUS_ACTIVE = "active"
LEASE_STATUS_UNAVAILABLE = "unavailable"
_LANE_HOLDER_FIELDS = ("host", "pid", "acquired_at")


def _lane_rank(state: str) -> int:
    return _LANE_PRECEDENCE.index(state) if state in _LANE_PRECEDENCE else len(_LANE_PRECEDENCE)


def _lease_rank(lease: Mapping[str, Any]) -> int:
    """Fresher evidence first: an unexpired claim outranks an expired one."""

    status = lease.get("status")
    if status == LEASE_STATUS_ACTIVE:
        return 0 if lease.get("expired") is False else 1
    if status == LEASE_STATUS_UNAVAILABLE:
        return 3
    return 2


def _lane_fact(runtime_root: Path, *, goal_id: str, agent_id: str) -> dict[str, Any]:
    target = turn_lane_target(
        runtime_root=runtime_root,
        goal_id=goal_id,
        plan={"turn_envelope": {"agent_id": agent_id}},
    )
    liveness = turn_lane_liveness(target)
    fact: dict[str, Any] = {"lane": liveness["state"]}
    holder = {
        key: liveness["holder"][key]
        for key in _LANE_HOLDER_FIELDS
        if liveness["holder"].get(key) not in (None, "")
    }
    if holder and liveness["state"] != TURN_LANE_ABSENT:
        fact["lane_holder"] = holder
    return fact


def _goal_leases(runtime_root: Path, goal_id: str) -> list[dict[str, Any]] | None:
    """Leases at the canonical head after cutover, else the local lease files.

    ``None`` means the lease authority could not be read, which is reported as
    ``unavailable`` rather than as "no lease".
    """

    try:
        canonical = read_canonical_todos_if_promoted(
            runtime_root=runtime_root, goal_id=goal_id, include_leases=True
        )
    except LocalCoordinationAuthorityUnavailable:
        return None
    if canonical is not None:
        return [dict(lease) for lease in canonical.get("leases") or []]
    lease_dir = task_lease_dir(runtime_root=runtime_root, goal_id=goal_id)
    if not lease_dir.is_dir():
        return []
    leases: list[dict[str, Any]] = []
    for path in sorted(lease_dir.glob("todo_*.json")):
        try:
            lease = read_lease(path)
        except TaskLeaseError:
            # One corrupt peer lease must not hide the healthy ones.
            continue
        if lease:
            leases.append(lease)
    return leases


def _lease_fact(lease: Mapping[str, Any], *, at: Any) -> dict[str, Any]:
    status = str(lease.get("status") or "").strip().lower() or "unknown"
    fact: dict[str, Any] = {"status": status}
    if status == LEASE_STATUS_ACTIVE:
        expires_at = lease_expires_at(dict(lease))
        fact["expired"] = not (expires_at and expires_at > at)
    return fact


def _delegation_worker_agents(runtime_root: Path) -> set[str]:
    """Agents whose delegation worker process still holds its operation lock."""

    executions = manager_context_root(runtime_root) / "executions"
    if not executions.is_dir():
        return set()
    active: set[str] = set()
    for path in sorted(executions.glob("*/*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        state, _record = lock_holder_liveness(path)
        if state != LOCK_HOLDER_LIVE:
            continue
        try:
            row = read_manager_context_record(path)
            raw_agent = row["identity"]["binding"]["agent_id"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        agent_id = normalize_todo_claimed_by(raw_agent) or str(raw_agent or "").strip()
        if agent_id:
            active.add(agent_id)
    return active


def _merge_lane(row: dict[str, Any], fact: Mapping[str, Any]) -> None:
    if "lane" not in row or _lane_rank(fact["lane"]) < _lane_rank(row["lane"]):
        row.pop("lane_holder", None)
        row.update(fact)


def _merge_lease(row: dict[str, Any], fact: Mapping[str, Any]) -> None:
    if "lease" not in row or _lease_rank(fact) < _lease_rank(row["lease"]):
        row["lease"] = dict(fact)


def collect_agent_execution_facts(
    *, runtime_root: Path | str | None, status_payload: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Return ``agent_id -> {lane, lane_holder?, delegation_worker_active, lease?}``.

    Agents and Goals are the ones the management projection would row: the
    Goal's registered agents plus every Todo claimant. Without a runtime root
    there are no facts to read and the result is empty, which the projection
    treats as "facts not collected", not as "nobody is running".
    """

    if runtime_root is None:
        return {}
    root = Path(runtime_root)
    if not root.is_dir():
        return {}
    facts: dict[str, dict[str, Any]] = {}
    at = now_utc()
    for goal_id, agents in projected_agent_goals(status_payload).items():
        leases = _goal_leases(root, goal_id) if agents else []
        for agent_id, spellings in agents.items():
            row = facts.setdefault(agent_id, {"delegation_worker_active": False})
            for spelling in spellings:
                _merge_lane(row, _lane_fact(root, goal_id=goal_id, agent_id=spelling))
            if leases is None:
                _merge_lease(row, {"status": LEASE_STATUS_UNAVAILABLE})
        for lease in leases or []:
            owner = normalize_todo_claimed_by(lease.get("owner"))
            if owner in facts:
                _merge_lease(facts[owner], _lease_fact(lease, at=at))
    for agent_id in _delegation_worker_agents(root):
        facts.setdefault(agent_id, {"lane": TURN_LANE_ABSENT})["delegation_worker_active"] = True
    return facts


def execution_fact_rows(
    facts: Mapping[str, Mapping[str, Any]] | None, agent_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Narrow a facts map to the named agents, for agent-lane compaction."""

    if not isinstance(facts, Mapping):
        return {}
    wanted = set(agent_ids)
    return {
        agent_id: dict(row)
        for agent_id, row in facts.items()
        if agent_id in wanted and isinstance(row, Mapping)
    }
