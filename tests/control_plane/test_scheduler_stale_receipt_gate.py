"""The Python follow-up route applies the same receipt fence as the native route.

A Turn id binds the follow-up to that Turn's heartbeat receipt. The native
transaction rejects a receipt that is missing *or* superseded; the compatibility
route used to check only for absence, so an ACK for a Turn whose receipt a later
`quota should-run` had superseded still rewrote scheduler state.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from examples.control_plane.quota_plan_fixtures import (
    SCOPED_AGENT_ID,
    write_cli_fixture,
)
from loopx.control_plane.scheduler.state import (
    APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY as APP_KEY,
    build_scheduler_state,
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
    registry: Path, runtime: Path, project: Path, turn: str, *, execute: bool = True
) -> tuple[int, dict]:
    return run_json_cli_result(
        "quota", "scheduler-ack-current", "--goal-id", GOAL,
        "--agent-id", SCOPED_AGENT_ID, "--codex-app",
        "--turn-instance-id", turn, *(["--execute"] if execute else []),
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


def _legacy_segment(value: str) -> str:
    """Mirror the legacy (unbounded) scheduler path segment normalization."""

    safe = re.sub(r"[^0-9A-Za-z_.-]+", "-", value.strip())
    return re.sub(r"^[-._]+|[-._]+$", "", safe) or "default"


def _legacy_state_path(
    runtime: Path, *, goal_id: str, agent_id: str, surface: str, state_key: str
) -> Path:
    return (
        runtime
        / "goals"
        / _legacy_segment(goal_id)
        / "scheduler-state"
        / _legacy_segment(agent_id)
        / _legacy_segment(surface)
        / f"{hashlib.sha256(state_key.encode('utf-8')).hexdigest()[:16]}.json"
    )


@pytest.mark.parametrize("execute", [True, False])
def test_stale_receipt_rejection_does_not_migrate_legacy_scheduler_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, execute: bool
) -> None:
    """The rejected follow-up rebuilds a hint, but the hint read is read-only.

    Migration belongs to the native commit that holds the scheduler lock after
    every receipt fence passes; a blocked preview or execute must leave the
    legacy layout byte-identical and create no canonical state file.
    """

    registry, runtime, project = _fixture(tmp_path, monkeypatch)
    _should_run(registry, runtime, project, TURN_1)
    legacy_path = _legacy_state_path(
        runtime,
        goal_id=GOAL,
        agent_id=SCOPED_AGENT_ID,
        surface="codex_app",
        state_key=APP_KEY,
    )
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_state = build_scheduler_state(
        goal_id=GOAL,
        agent_id=SCOPED_AGENT_ID,
        state_key=APP_KEY,
        reset_token="legacy-reset",
        identity_signature="legacy-identity",
        progression_index=1,
        progression_minutes=[30, 60],
        last_applied_rrule="FREQ=MINUTELY;INTERVAL=30",
        updated_at="2026-01-01T12:00:00Z",
    )
    legacy_bytes = json.dumps(legacy_state, sort_keys=True).encode("utf-8")
    legacy_path.write_bytes(legacy_bytes)
    canonical_path = scheduler_state_path(
        runtime, goal_id=GOAL, agent_id=SCOPED_AGENT_ID, state_key=APP_KEY
    )
    assert legacy_path != canonical_path
    assert not canonical_path.exists()

    _supercede(runtime, TURN_2)
    code, result = _ack(registry, runtime, project, TURN_1, execute=execute)

    assert code == 1, result
    assert result["status"] == "heartbeat_receipt_stale"
    assert result["state"] == "blocked_receipt"
    assert result["scheduler_state_mutated"] is False
    assert legacy_path.read_bytes() == legacy_bytes
    assert not canonical_path.exists()
