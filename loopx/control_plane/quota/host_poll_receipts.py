"""Host poll receipts for visible goal loops.

Long-running host loops (OpenCode bridge, OpenCode 2 worker, and other
generic-CLI drivers) probe `quota should-run` on every settled turn or timer
wake. When a driver passes `--record-host-poll`, the CLI writes a compact
receipt next to the goal state file so the control plane can distinguish a
live polling loop from a driver that died mid-wait.

The receipt is a small JSON file owned by LoopX, written atomically, and
stored beside the goal state file so it inherits the same ignore rules as
the rest of the project-local control-plane state.
"""

from __future__ import annotations
from .effective_action import EffectiveAction

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock, write_json_atomic
from ..todos.contract import normalize_todo_claimed_by

HOST_POLL_RECEIPT_SCHEMA_VERSION = "loopx_host_poll_receipt_v0"
HOST_POLL_RECEIPT_DIRNAME = ".loopx-host-poll-receipts"
HOST_POLL_STALE_MINUTES = 240
_UNCHANGED_WORKER_OBSERVATION = object()


def _receipt_dir_for_goal(goal: dict[str, Any], state_path: Path | None) -> Path | None:
    if state_path is None:
        return None
    return state_path.parent / HOST_POLL_RECEIPT_DIRNAME


def receipt_path_for_goal(goal: dict[str, Any], state_path: Path | None) -> Path | None:
    directory = _receipt_dir_for_goal(goal, state_path)
    if directory is None:
        return None
    goal_id = str(goal.get("id") or "").strip()
    if not goal_id:
        return None
    safe = goal_id.replace("/", "_").replace("\\", "_")
    return directory / f"{safe}.json"


def record_host_poll_receipt(
    goal: dict[str, Any],
    state_path: Path | None,
    *,
    agent_id: str | None,
    decision: dict[str, Any],
    poll_count: int | None = None,
    now: datetime | None = None,
    worker_observation: object = _UNCHANGED_WORKER_OBSERVATION,
) -> dict[str, Any] | None:
    """Write or refresh the host poll receipt for one goal.

    `expected_continuation` describes the driver. A validated terminal decision
    or its explicit paused/unknown report can end that driver's polling; the
    latter never certifies Goal completion. Ordinary polls retain the worker's
    latest observation until that worker explicitly clears it.
    """

    path = receipt_path_for_goal(goal, state_path)
    if path is None:
        return None
    if agent_id:
        agent_id = normalize_todo_claimed_by(agent_id)
        if not agent_id:
            raise ValueError("host poll receipt requires a public-safe Agent")
    with exclusive_file_lock(path, agent_id=agent_id, operation="host_poll_receipt"):
        return _record_host_poll_receipt(
            goal, path, agent_id=agent_id, decision=decision,
            poll_count=poll_count, now=now, worker_observation=worker_observation,
        )


def _record_host_poll_receipt(
    goal: dict[str, Any], path: Path, *, agent_id: str | None,
    decision: dict[str, Any], poll_count: int | None, now: datetime | None,
    worker_observation: object,
) -> dict[str, Any]:
    observation = read_host_poll_receipt_observation(path, expected_goal_id=str(goal.get("id") or ""))
    if (observation["read_status"] == "unreadable"
            and not isinstance(worker_observation, dict)):
        # A missing receipt may be created by a liveness poll. An unreadable
        # one may still contain unresolved observations. A clear is not proof
        # of recovery either. Only an explicit worker update can replace it.
        # The CLI returns recorded=false alongside its independent decision.
        raise ValueError("host poll receipt is unreadable; repair its source before updating observations")
    current = observation["receipt"]
    previous_count = current.get("poll_count") if isinstance(current, dict) else None
    count = (
        poll_count
        if poll_count is not None
        else int(previous_count or 0) + 1
    )
    frontier = decision.get("goal_frontier_projection")
    terminal_state = frontier.get("terminal_state") if isinstance(frontier, dict) else None
    terminal = bool(
        decision.get("should_run") is False
        and decision.get("effective_action") == EffectiveAction.TERMINAL_NO_FOLLOWUP.value
        and isinstance(terminal_state, dict)
        and terminal_state.get("kind") == "no_followup"
    )
    receipt = {
        "schema_version": HOST_POLL_RECEIPT_SCHEMA_VERSION,
        "goal_id": str(goal.get("id") or ""),
        "agent_id": str(agent_id or ""),
        "poll_count": count,
        "last_poll_at": (now or datetime.now(timezone.utc)).isoformat(),
        "expected_continuation": not terminal,
        "last_decision_action": str(decision.get("effective_action") or ""),
    }
    stored_observations = (current or {}).get("worker_observations", {})
    # An ordinary liveness poll cannot erase a damaged worker observation.
    # Keep it visibly unreadable until the source is repaired. Nor should a
    # malformed optional observation prevent the quota decision from returning.
    if not isinstance(stored_observations, dict):
        receipt["worker_observations"] = stored_observations
        write_json_atomic(path, receipt, mode=0o600, fsync_directory=True)
        return receipt
    observations = dict(stored_observations)
    if worker_observation is None:
        observations.pop(str(agent_id or ""), None)
    elif worker_observation is not _UNCHANGED_WORKER_OBSERVATION:
        if not isinstance(worker_observation, dict) or not agent_id:
            raise ValueError("worker observation requires a scoped Agent")
        observations[agent_id] = {**worker_observation, "agent_id": agent_id,
                                  "observed_at": receipt["last_poll_at"]}
    if observations:
        receipt["worker_observations"] = observations
        own_observation = observations.get(str(agent_id or ""))
        if isinstance(own_observation, dict) and own_observation.get("status") in (
            "replan_paused", "replan_outcome_unknown",
        ):
            receipt["expected_continuation"] = False
    write_json_atomic(path, receipt, mode=0o600, fsync_directory=True)
    return receipt


def read_host_worker_observations(
    goal: dict[str, Any], state_path: Path | None, *,
    registry_goal_digest: str | None, runtime_root: Path,
    source_goal_observation_digest: str | None | Callable[[], str | None] = None,
) -> dict[str, Any] | None:
    """Read driver observations without making them Goal execution authority.

    The caller supplies its actual registry Goal and runtime. A stale scope,
    corrupt receipt or missing fields cannot reuse another Goal's last state.
    Paths and registry witnesses are only association checks, never UI fields.
    """
    observation = read_host_poll_receipt_observation(
        receipt_path_for_goal(goal, state_path), expected_goal_id=str(goal.get("id") or ""),
    )
    source = {"goal_id": str(goal.get("id") or "")}
    if observation["read_status"] == "missing":
        return None
    if observation["read_status"] != "read":
        return {**source, "read_status": "unreadable", "rows": []}
    receipt = observation["receipt"]
    if "worker_observations" not in receipt:
        return None
    workers = receipt["worker_observations"]
    if not isinstance(workers, dict):
        return {**source, "read_status": "unreadable", "rows": []}
    if not workers:
        return None
    # A normal Goal with no worker observation must not acquire source reads.
    if callable(source_goal_observation_digest):
        source_goal_observation_digest = source_goal_observation_digest() if any(
            isinstance(row, dict) and "source_goal_observation_digest" in row for row in workers.values()
        ) else None
    rows = []
    stale = False
    for agent, row in workers.items():
        if (not isinstance(agent, str) or not agent or not isinstance(row, dict)
                or row.get("agent_id") != agent
                or not isinstance(row.get("status"), str)
                or row["status"] not in {"replan_paused", "replan_outcome_unknown"}
                or not isinstance(row.get("obligation_id"), str)
                or not row["obligation_id"]
                or (row.get("turn_instance_id") is not None
                    and not isinstance(row["turn_instance_id"], str))
                or not isinstance(row.get("observed_at"), str)):
            return {**source, "read_status": "unreadable", "rows": []}
        try:
            timestamp = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("observation requires a timezone")
        except ValueError:
            return {**source, "read_status": "unreadable", "rows": []}
        # Keep the old selected-view contract for persisted legacy rows. New
        # rows must satisfy their stronger source binding, with no fallback
        # that could hide a changed source by matching mirror contents alone.
        digest = source_goal_observation_digest if "source_goal_observation_digest" in row else registry_goal_digest
        recorded_digest = row.get("source_goal_observation_digest") if "source_goal_observation_digest" in row else row.get("registry_goal_digest")
        if (not digest or recorded_digest != digest
                or row.get("runtime_root") != str(runtime_root.resolve())):
            stale = True
            continue
        rows.append({key: row.get(key) for key in (
            "agent_id", "status", "obligation_id", "turn_instance_id", "observed_at",
        )})
    return {**source, "read_status": "stale" if stale else "read", "rows": rows}


def read_host_poll_receipt(path: Path | None) -> dict[str, Any] | None:
    """Read one valid receipt, preserving the historical optional return API."""

    observation = read_host_poll_receipt_observation(path)
    receipt = observation.get("receipt")
    return receipt if isinstance(receipt, dict) else None


def read_host_poll_receipt_observation(
    path: Path | None,
    *,
    expected_goal_id: str | None = None,
) -> dict[str, Any]:
    """Classify an expected receipt location without hiding corrupt data.

    A missing per-goal receipt is normal because host polling is opt-in. Files
    that exist but cannot satisfy the receipt contract are unreadable so global
    projections cannot certify the source as fresh and complete.
    """

    if path is None:
        return {"read_status": "missing", "receipt": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"read_status": "missing", "receipt": None}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"read_status": "unreadable", "receipt": None}
    if not isinstance(payload, dict):
        return {"read_status": "unreadable", "receipt": None}
    if payload.get("schema_version") != HOST_POLL_RECEIPT_SCHEMA_VERSION:
        return {"read_status": "unreadable", "receipt": None}
    goal_id = payload.get("goal_id")
    if not isinstance(goal_id, str) or not goal_id.strip():
        return {"read_status": "unreadable", "receipt": None}
    if expected_goal_id is not None and goal_id != expected_goal_id:
        return {"read_status": "unreadable", "receipt": None}
    if not isinstance(payload.get("agent_id"), str):
        return {"read_status": "unreadable", "receipt": None}
    poll_count = payload.get("poll_count")
    if isinstance(poll_count, bool) or not isinstance(poll_count, int) or poll_count < 0:
        return {"read_status": "unreadable", "receipt": None}
    if not isinstance(payload.get("expected_continuation"), bool):
        return {"read_status": "unreadable", "receipt": None}
    if not isinstance(payload.get("last_decision_action"), str):
        return {"read_status": "unreadable", "receipt": None}
    raw_last_poll = payload.get("last_poll_at")
    if not isinstance(raw_last_poll, str):
        return {"read_status": "unreadable", "receipt": None}
    try:
        datetime.fromisoformat(raw_last_poll.replace("Z", "+00:00"))
    except ValueError:
        return {"read_status": "unreadable", "receipt": None}
    return {"read_status": "read", "receipt": payload}


def stale_host_poll_risk(
    receipt: dict[str, Any],
    *,
    now: datetime | None = None,
    stale_minutes: int = HOST_POLL_STALE_MINUTES,
) -> dict[str, Any] | None:
    """Derive a compact stale-loop risk row from one receipt."""

    if not isinstance(receipt, dict):
        return None
    if receipt.get("expected_continuation") is not True:
        return None
    raw_last_poll = receipt.get("last_poll_at")
    if not isinstance(raw_last_poll, str):
        return None
    try:
        last_poll = datetime.fromisoformat(raw_last_poll)
    except ValueError:
        return None
    current = now or datetime.now(timezone.utc)
    if last_poll.tzinfo is None:
        last_poll = last_poll.replace(tzinfo=timezone.utc)
    age_minutes = (current - last_poll).total_seconds() / 60
    if age_minutes < stale_minutes:
        return None
    return {
        "schema_version": "loopx_stale_host_poll_risk_v0",
        "kind": "stale_host_poll",
        "goal_id": str(receipt.get("goal_id") or ""),
        "agent_id": str(receipt.get("agent_id") or ""),
        "last_poll_at": raw_last_poll,
        "age_minutes": round(age_minutes, 1),
        "stale_minutes": stale_minutes,
        "poll_count": receipt.get("poll_count"),
        "reason": (
            "The host loop polled quota while expecting continuation but has "
            "gone quiet for longer than the staleness window. The driver may "
            "have died mid-wait, or the loop may have been paused or stopped "
            "intentionally; check the session and LoopX gates before resuming."
        ),
    }
