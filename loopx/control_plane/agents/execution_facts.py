"""Execution facts behind the worker lifecycle projection: read, never lock.

`executing` has to be backed by something that runs, not by a fresh Todo
timestamp. This module gathers, per agent, the facts LoopX already keeps about
running work, so the agent management projection can derive `executing` and
`unknown` from them instead of from activity age:

- the Turn lane holder record, one per Goal and agent, read through
  `turn_lane_liveness`; a delegated member executes inside its own lane too;
- the delegation worker: a delegation journal row whose operation lock and
  whose execution slot for the delegated Todo are both held by a live process;
- the task leases on the Goal's open Todos, read from the canonical head after
  cutover and from the local lease files before it.

It writes nothing, takes no lock and keeps no state of its own: every input is
owned elsewhere and this is a bounded read model over them. A fact that cannot
be read is reported as such (`unreadable`, `unavailable`), never as "not
running".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from ...file_lock import LOCK_HOLDER_LIVE, lock_holder_liveness
from ..collaboration.inbox import _hash as manager_context_hash
from ..collaboration.inbox import _read as read_manager_context_record
from ..collaboration.inbox import _root as manager_context_root
from ..coordination.local_authority import read_canonical_todos_if_promoted
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
from ..work_items.task_lease import lease_expires_at, normalize_goal_id, task_lease_path
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
_JOURNAL_ADDRESS = re.compile(r"[a-f0-9]{64}")


def _lane_rank(state: str) -> int:
    return _LANE_PRECEDENCE.index(state) if state in _LANE_PRECEDENCE else len(_LANE_PRECEDENCE)


def _lease_rank(lease: Mapping[str, Any]) -> int:
    """Fresher evidence first: an unexpired lease outranks an expired one."""

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
    if holder:
        fact["lane_holder"] = holder
    return fact


def _delegation_worker_agents(
    runtime_root: Path, *, goal_id: str, requesters: Iterable[str]
) -> set[str]:
    """Delegated members whose worker is executing their Todo right now.

    A journal row lives under its Goal and requester, so only this Goal's
    requesters are read. The row's operation lock is also taken briefly by
    readers and by result adoption, so a live operation holder alone is not a
    worker; the execution slot for the delegated Todo is taken only by the
    worker while it runs, which makes a live slot holder the execution fact.
    """

    store = manager_context_root(runtime_root)
    active: set[str] = set()
    for requester in dict.fromkeys(requesters):
        journal = store / "executions" / manager_context_hash([goal_id, requester])
        try:
            rows = sorted(journal.glob("*.json")) if journal.is_dir() else []
        except OSError:
            continue
        for path in rows:
            if not _JOURNAL_ADDRESS.fullmatch(path.stem) or path.is_symlink() or not path.is_file():
                continue
            if lock_holder_liveness(path)[0] != LOCK_HOLDER_LIVE:
                continue
            try:
                binding = read_manager_context_record(path)["identity"]["binding"]
                member, todo_id = binding["agent_id"], binding["todo_id"]
                slot = store / "execution-slots" / manager_context_hash([goal_id, todo_id])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            agent_id = normalize_todo_claimed_by(member)
            if agent_id and lock_holder_liveness(slot)[0] == LOCK_HOLDER_LIVE:
                active.add(agent_id)
    return active


def _open_todo_leases(
    runtime_root: Path, *, goal_id: str, todo_ids: set[str]
) -> list[Mapping[str, Any]] | None:
    """Leases on the Goal's open Todos; ``None`` when the lease authority is unreadable.

    After cutover the canonical head is the only lease source; its failure is
    reported, never replaced by the local files it superseded. The failure
    classes are the ones the ownership observation already isolates.
    """

    try:
        canonical = read_canonical_todos_if_promoted(
            runtime_root=runtime_root, goal_id=goal_id, include_leases=True
        )
    except (OSError, RuntimeError, ValueError):
        return None
    if canonical is not None:
        leases = [lease for lease in canonical.get("leases") or [] if isinstance(lease, Mapping)]
    else:
        leases = []
        for todo_id in sorted(todo_ids):
            try:
                lease = read_lease(
                    task_lease_path(runtime_root=runtime_root, goal_id=goal_id, todo_id=todo_id)
                )
            except (TaskLeaseError, OSError):
                # One corrupt peer lease must not hide the healthy ones.
                continue
            if lease:
                leases.append(lease)
    return [lease for lease in leases if lease.get("todo_id") in todo_ids]


def _lease_fact(lease: Mapping[str, Any], *, at: datetime) -> dict[str, Any]:
    status = str(lease.get("status") or "").strip().lower() or "unknown"
    fact: dict[str, Any] = {"status": status}
    if status == LEASE_STATUS_ACTIVE:
        expires_at = lease_expires_at(dict(lease))
        # An active lease whose expiry cannot be read is not proven current.
        fact["expired"] = not (expires_at is not None and expires_at > at)
    return fact


def _merge_lane(row: dict[str, Any], fact: Mapping[str, Any]) -> None:
    if _lane_rank(fact["lane"]) < _lane_rank(row["lane"]):
        row.pop("lane_holder", None)
        row.update(fact)


def _merge_lease(row: dict[str, Any], fact: Mapping[str, Any]) -> None:
    if "lease" not in row or _lease_rank(fact) < _lease_rank(row["lease"]):
        row["lease"] = dict(fact)


def collect_agent_execution_facts(
    *, runtime_root: Path | str | None, status_payload: Mapping[str, Any]
) -> dict[str, dict[str, Any]] | None:
    """Return ``agent_id -> {lane, lane_holder?, delegation_worker_active, lease?}``.

    Agents and Goals are the ones the management projection rows: each Goal's
    registered agents plus its open-Todo claimants. ``None`` means no runtime
    root could be read, which the projection reports as facts not collected,
    never as "nobody is running".
    """

    if runtime_root is None:
        return None
    root = Path(runtime_root)
    if not root.is_dir():
        return None
    facts: dict[str, dict[str, Any]] = {}
    at = now_utc()
    for raw_goal_id, agents in projected_agent_goals(dict(status_payload)).items():
        try:
            goal_id = normalize_goal_id(raw_goal_id)
        except TaskLeaseError:
            continue
        workers = _delegation_worker_agents(
            root,
            goal_id=goal_id,
            requesters=(spelling for work in agents.values() for spelling in work["spellings"]),
        )
        todo_ids = {todo_id for work in agents.values() for todo_id in work["open_todo_ids"]}
        leases = _open_todo_leases(root, goal_id=goal_id, todo_ids=todo_ids) if todo_ids else []
        for agent_id, work in agents.items():
            row = facts.setdefault(
                agent_id, {"lane": TURN_LANE_ABSENT, "delegation_worker_active": False}
            )
            for spelling in work["spellings"]:
                _merge_lane(row, _lane_fact(root, goal_id=goal_id, agent_id=spelling))
            if agent_id in workers:
                row["delegation_worker_active"] = True
            if leases is None and work["open_todo_ids"]:
                _merge_lease(row, {"status": LEASE_STATUS_UNAVAILABLE})
        for lease in leases or []:
            owner = normalize_todo_claimed_by(lease.get("owner"))
            if owner in agents:
                _merge_lease(facts[owner], _lease_fact(lease, at=at))
    return facts
