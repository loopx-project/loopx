from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loopx.control_plane.quota.host_poll_receipts import (
    HOST_POLL_RECEIPT_DIRNAME,
    HOST_POLL_RECEIPT_SCHEMA_VERSION,
    receipt_path_for_goal,
    read_host_poll_receipt,
    read_host_poll_receipt_observation,
    record_host_poll_receipt,
    stale_host_poll_risk,
)


def terminal_decision() -> dict[str, Any]:
    return {
        "should_run": False,
        "effective_action": "terminal_no_followup",
        "goal_frontier_projection": {
            "terminal_state": {"kind": "no_followup"},
        },
    }


def run_now_decision() -> dict[str, Any]:
    return {"should_run": True, "effective_action": "normal_run"}


def test_receipt_path_sits_beside_the_goal_state_file() -> None:
    goal = {"id": "goal/one", "state_file": "state/goal-one.md", "repo": "/repo"}
    path = receipt_path_for_goal(goal, Path("/repo/state/goal-one.md"))
    assert path is not None
    assert path.parent.name == HOST_POLL_RECEIPT_DIRNAME
    assert path.parent.parent == Path("/repo/state")
    assert "goal_one" in path.name


def test_receipt_path_fails_closed_without_state_path() -> None:
    assert receipt_path_for_goal({"id": "goal-1"}, None) is None
    assert receipt_path_for_goal({"state_file": "x.md"}, Path("/tmp/x.md")) is None


def test_record_and_read_receipt_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / "goals" / "goal-1.md"
    goal = {"id": "goal-1", "state_file": "goals/goal-1.md", "repo": str(tmp_path)}
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    receipt = record_host_poll_receipt(
        goal,
        state_path,
        agent_id="agent-1",
        decision=run_now_decision(),
        now=now,
    )
    assert receipt is not None
    assert receipt["schema_version"] == HOST_POLL_RECEIPT_SCHEMA_VERSION
    assert receipt["expected_continuation"] is True
    assert receipt["poll_count"] == 1
    path = receipt_path_for_goal(goal, state_path)
    assert path is not None and path.exists()
    reread = read_host_poll_receipt(path)
    assert reread == receipt


def test_receipt_increments_poll_count_and_marks_terminal_stops(tmp_path: Path) -> None:
    state_path = tmp_path / "goals" / "goal-1.md"
    goal = {"id": "goal-1", "state_file": "goals/goal-1.md", "repo": str(tmp_path)}
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    first = record_host_poll_receipt(
        goal, state_path, agent_id=None, decision=run_now_decision(), now=now
    )
    second = record_host_poll_receipt(
        goal,
        state_path,
        agent_id=None,
        decision=terminal_decision(),
        now=now + timedelta(minutes=1),
    )
    assert first is not None and second is not None
    assert second["poll_count"] == 2
    assert second["expected_continuation"] is False
    assert second["last_decision_action"] == "terminal_no_followup"


def test_read_receipt_rejects_foreign_schemas(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"schema_version": "other"}), encoding="utf-8")
    assert read_host_poll_receipt(path) is None
    path.write_text("{broken", encoding="utf-8")
    assert read_host_poll_receipt(path) is None
    assert read_host_poll_receipt(tmp_path / "missing.json") is None


def test_read_receipt_observation_distinguishes_missing_and_unreadable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "receipt.json"
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "missing",
        "receipt": None,
    }

    path.write_text("{broken", encoding="utf-8")
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "unreadable",
        "receipt": None,
    }

    path.write_text(
        json.dumps(
            {
                "schema_version": HOST_POLL_RECEIPT_SCHEMA_VERSION,
                "goal_id": "goal-1",
                "last_poll_at": "not-a-timestamp",
            }
        ),
        encoding="utf-8",
    )
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "unreadable",
        "receipt": None,
    }


def test_stale_host_poll_risk_only_for_quiet_expecting_loops() -> None:
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    stale = {
        "schema_version": HOST_POLL_RECEIPT_SCHEMA_VERSION,
        "goal_id": "goal-1",
        "agent_id": "agent-1",
        "poll_count": 7,
        "last_poll_at": (now - timedelta(hours=6)).isoformat(),
        "expected_continuation": True,
    }
    risk = stale_host_poll_risk(stale, now=now)
    assert risk is not None
    assert risk["kind"] == "stale_host_poll"
    assert risk["age_minutes"] == 360.0
    fresh = {**stale, "last_poll_at": (now - timedelta(minutes=5)).isoformat()}
    assert stale_host_poll_risk(fresh, now=now) is None
    stopped = {**stale, "expected_continuation": False}
    assert stale_host_poll_risk(stopped, now=now) is None
    assert stale_host_poll_risk({"expected_continuation": True, "last_poll_at": "not-a-date"}, now=now) is None
