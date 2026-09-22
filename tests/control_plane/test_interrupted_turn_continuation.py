"""Interrupted work must remain runnable rather than acquire a fictitious wait."""
from __future__ import annotations

from pathlib import Path

from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _run_generated_cli,
    _spend_run_count, _write_fixture,
)


def test_original_turn_can_resume_and_settle_without_mutating_todo(tmp_path: Path) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    prior_turn = "interrupted-original"
    recovery_turn = "interrupted-recovery"

    def guard(turn: str):
        rc, result = _run_cli(
            registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--turn-instance-id", turn, "--todo-id", TODO_ID,
            "--scan-path", str(project),
        )
        assert rc == 0, result
        return result

    admitted = guard(prior_turn)
    assert admitted["heartbeat_receipt"]["closeout_required"] is True
    interrupted = guard(recovery_turn)
    assert interrupted["effective_action"] == "unsettled_host_turn_recovery"
    assert interrupted["unsettled_host_turn_recovery"]["repair"] == "resume_prior_turn"
    actions = interrupted["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert not any("--resume-when" in action for action in actions)
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_before = state_path.read_bytes()
    rc, resumed = _run_generated_cli(actions[1], registry_path=registry)
    assert rc == 0, resumed
    assert state_path.read_bytes() == state_before
    assert resumed["effective_action"] != "unsettled_host_turn_recovery", resumed
    assert resumed["selected_todo"]["todo_id"] == TODO_ID
    assert _spend_run_count(runtime) == 0

    rc, writeback = _run_cli(
        registry, runtime, "refresh-state", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", prior_turn,
        "--classification", "validated_progress",
        "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation",
        "--no-global-sync", "--suppress-external-sinks",
    )
    assert rc == 0, writeback
    for _ in range(2):
        rc, spent = _run_cli(
            registry, runtime, "quota", "spend-slot", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
            "--turn-instance-id", prior_turn, "--source", "heartbeat",
            "--slots", "1", "--execute", "--scan-path", str(project),
        )
        assert rc == 0, spent
    assert _spend_run_count(runtime) == 1
    rc, next_guard = _run_generated_cli(actions[-1], registry_path=registry)
    assert rc == 0, next_guard
    assert next_guard["effective_action"] != "unsettled_host_turn_recovery", next_guard
    assert next_guard["selected_todo"]["todo_id"] == TODO_ID
