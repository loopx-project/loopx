"""The Python follow-up route applies the same receipt fence as the native route.

A Turn id binds the follow-up to that Turn's heartbeat receipt. The native
transaction rejects a receipt that is missing *or* superseded; the compatibility
route used to check only for absence, so an ACK for a Turn whose receipt a later
`quota should-run` had superseded still rewrote scheduler state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.control_plane.quota_plan_fixtures import (
    SCOPED_AGENT_ID,
    write_cli_fixture,
)
from loopx.control_plane.scheduler.state import (
    APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY as APP_KEY,
    load_scheduler_state,
    scheduler_state_path,
)
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.rollout_event_log import (
    append_rollout_event,
    build_rollout_event,
    rollout_event_log_path,
)

GOAL = "needs-operator"
TURN_1 = "stale-receipt-turn-1"
TURN_2 = "stale-receipt-turn-2"


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    registry, runtime, project = write_cli_fixture(
        tmp_path / "fixture", scoped_agents=True, provider="file"
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CODEX_THREAD_ID", "scheduler-fixture")
    return registry, runtime, project


def _should_run(
    registry: Path, runtime: Path, project: Path, turn: str
) -> dict:
    code, result = run_json_cli_result(
        "quota", "should-run", "--goal-id", GOAL,
        "--agent-id", SCOPED_AGENT_ID, "--codex-app",
        "--turn-instance-id", turn,
        registry_path=registry, runtime_root=runtime, cwd=project,
    )
    assert code == 0, result
    return result


def _ack(
    registry: Path, runtime: Path, project: Path, turn: str
) -> tuple[int, dict]:
    return run_json_cli_result(
        "quota", "scheduler-ack-current", "--goal-id", GOAL,
        "--agent-id", SCOPED_AGENT_ID, "--codex-app",
        "--turn-instance-id", turn, "--execute",
        registry_path=registry, runtime_root=runtime, cwd=project,
    )


def _supercede(runtime: Path, turn: str) -> None:
    """Append a later receipt for ``turn`` on the same goal and agent."""

    append_rollout_event(
        rollout_event_log_path(runtime, GOAL),
        build_rollout_event(
            goal_id=GOAL,
            event_kind="quota_should_run",
            agent_id=SCOPED_AGENT_ID,
            run_id=turn,
            status="normal_run",
            details={"ok": True, "should_run": True},
        ),
    )


def test_superseded_receipt_cannot_commit_a_scheduler_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, runtime, project = _fixture(tmp_path, monkeypatch)
    _should_run(registry, runtime, project, TURN_1)
    _supercede(runtime, TURN_2)

    code, result = _ack(registry, runtime, project, TURN_1)

    assert code == 1, result
    assert result["ok"] is False
    assert result["status"] == "heartbeat_receipt_stale"
    assert result["state"] == "blocked_receipt"
    assert result["scheduler_state_mutated"] is False
    assert not scheduler_state_path(
        runtime, goal_id=GOAL, agent_id=SCOPED_AGENT_ID, state_key=APP_KEY
    ).exists()


def test_fresh_receipt_still_commits_the_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, runtime, project = _fixture(tmp_path, monkeypatch)
    _should_run(registry, runtime, project, TURN_1)

    code, result = _ack(registry, runtime, project, TURN_1)

    assert code == 0, result
    assert result["ok"] is True
    assert result["scheduler_state_mutated"] is True
    state = load_scheduler_state(
        runtime, goal_id=GOAL, agent_id=SCOPED_AGENT_ID, state_key=APP_KEY
    )
    assert state is not None
    assert state["last_applied_rrule"]
