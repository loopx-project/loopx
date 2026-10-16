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
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOST_POLL_RECEIPT_SCHEMA_VERSION = "loopx_host_poll_receipt_v0"
HOST_POLL_RECEIPT_DIRNAME = ".loopx-host-poll-receipts"
HOST_POLL_STALE_MINUTES = 240


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
) -> dict[str, Any] | None:
    """Write or refresh the host poll receipt for one goal.

    `expected_continuation` is derived from the decision: only validated
    terminal no-follow-up marks the loop as expected to stop. Returns the
    written receipt or None when the goal state path is unknown.
    """

    path = receipt_path_for_goal(goal, state_path)
    if path is None:
        return None
    current = read_host_poll_receipt(path)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return receipt


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
    except (OSError, json.JSONDecodeError):
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
