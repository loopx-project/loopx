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
from loopx.control_plane.goals.goal_ref_validation import exact_goal_ref
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
# A different Goal that shares this display name and Agent, so it writes into
# the same rollout-event log.
OTHER_INSTANCE = "ginst_" + "b" * 32
PROVIDERS = ["file", "sqlite"]


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, provider: str = "file"
) -> tuple[Path, Path, Path]:
    registry, runtime, project = write_cli_fixture(
        tmp_path / "fixture", scoped_agents=True, provider=provider
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


def _fail(
    registry: Path,
    runtime: Path,
    project: Path,
    turn: str,
    *,
    failed_rrule: str,
    execute: bool = True,
) -> tuple[int, dict]:
    return run_json_cli_result(
        "quota", "scheduler-fail-current", "--goal-id", GOAL,
        "--agent-id", SCOPED_AGENT_ID, "--codex-app",
        "--turn-instance-id", turn,
        "--failed-rrule", failed_rrule,
        *(["--execute"] if execute else []),
        registry_path=registry, runtime_root=runtime, cwd=project,
    )


def _supercede(
    runtime: Path, turn: str, goal_ref: dict[str, str] | None = None
) -> None:
    """Append a later receipt for ``turn`` on the same goal and agent."""

    append_rollout_event(
        rollout_event_log_path(runtime, GOAL),
        build_rollout_event(
            goal_id=GOAL,
            event_kind="quota_should_run",
            goal_ref=goal_ref,
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


def _diagnostic_row(runtime: Path) -> None:
    """Append one identity-less successful quota row: a host-protocol audit."""

    append_rollout_event(
        rollout_event_log_path(runtime, GOAL),
        build_rollout_event(
            goal_id=GOAL,
            event_kind="quota_should_run",
            agent_id=SCOPED_AGENT_ID,
            status="normal_run",
            details={"ok": True, "should_run": True},
        ),
    )


def test_identity_less_diagnostic_row_cannot_expire_the_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostic row after the receipt is audit, not a successor.

    The host protocol requires a nonempty Turn identity for a heartbeat
    receipt; an identity-less quota row must neither grant host writeback nor
    expire the admitted Turn's receipt, on either backend.
    """

    registry, runtime, project = _fixture(tmp_path, monkeypatch)
    _should_run(registry, runtime, project, TURN_1)
    _diagnostic_row(runtime)

    code, result = _ack(registry, runtime, project, TURN_1)

    assert code == 0, result
    assert result["ok"] is True
    assert result["scheduler_state_mutated"] is True
    state = load_scheduler_state(
        runtime, goal_id=GOAL, agent_id=SCOPED_AGENT_ID, state_key=APP_KEY
    )
    assert state is not None
    assert state["last_applied_rrule"]


def test_a_real_successor_receipt_still_expires_the_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strategy: the relaxation above must not weaken real successors."""

    registry, runtime, project = _fixture(tmp_path, monkeypatch)
    _should_run(registry, runtime, project, TURN_1)
    _diagnostic_row(runtime)
    _supercede(runtime, TURN_2)

    code, result = _ack(registry, runtime, project, TURN_1)

    assert code == 1, result
    assert result["status"] == "heartbeat_receipt_stale"
    assert result["scheduler_state_mutated"] is False
    assert not scheduler_state_path(
        runtime, goal_id=GOAL, agent_id=SCOPED_AGENT_ID, state_key=APP_KEY
    ).exists()


def _writeback(
    operation: str,
    registry: Path,
    runtime: Path,
    project: Path,
    turn: str,
    hint: dict,
) -> tuple[int, dict]:
    """Run the ACK or the host-failure writeback this Turn is bound to."""

    if operation == "ack":
        return _ack(registry, runtime, project, turn)
    return _fail(
        registry,
        runtime,
        project,
        turn,
        failed_rrule=hint["scheduler_hint"]["app_automation"]["recommended_rrule"],
    )


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("operation", ["ack", "failure"])
def test_same_scope_successor_receipt_still_expires_the_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, operation: str
) -> None:
    """Control: a real successor in the same scope still blocks both writebacks.

    Nothing in the instance-scoping relaxation may let an older Turn report
    over a newer one that genuinely follows it.
    """

    registry, runtime, project = _fixture(tmp_path, monkeypatch, provider=provider)
    hint = _should_run(registry, runtime, project, TURN_1)
    _supercede(runtime, TURN_2)

    code, result = _writeback(operation, registry, runtime, project, TURN_1, hint)

    assert code == 1, result
    assert result["status"] == "heartbeat_receipt_stale"
    assert result["state"] == "blocked_receipt"
    assert result["scheduler_state_mutated"] is False
    assert not scheduler_state_path(
        runtime, goal_id=GOAL, agent_id=SCOPED_AGENT_ID, state_key=APP_KEY
    ).exists()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("operation", ["ack", "failure"])
def test_another_goal_instance_receipt_cannot_expire_the_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, operation: str
) -> None:
    """A receipt from another Goal instance is not this Turn's successor.

    Both Goals share the display name and Agent, so they append to one log.
    Only receipts of the admitted Turn's own scope may expire its receipt.
    """

    registry, runtime, project = _fixture(tmp_path, monkeypatch, provider=provider)
    hint = _should_run(registry, runtime, project, TURN_1)
    _supercede(runtime, TURN_2, exact_goal_ref(GOAL, OTHER_INSTANCE))

    code, result = _writeback(operation, registry, runtime, project, TURN_1, hint)

    assert code == 0, result
    assert result["ok"] is True
    assert result["scheduler_state_mutated"] is True
    state = load_scheduler_state(
        runtime, goal_id=GOAL, agent_id=SCOPED_AGENT_ID, state_key=APP_KEY
    )
    assert state is not None
