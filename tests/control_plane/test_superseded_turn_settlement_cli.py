"""Retire an expired task without consuming its existing future monitor."""
from __future__ import annotations

from pathlib import Path

import pytest
import test_declared_terminal_settlement_cli as declared
import test_quota_settlement_cli as cli

from loopx.control_plane.quota.settlement import read_heartbeat_settlement


def _future_monitor(run, lease):
    due = "2099-01-01T00:00:00Z"
    code, added = run(
        "todo", "add", "--goal-id", cli.GOAL_ID, "--role", "agent",
        "--text", "[P1] Observe the existing future evidence window.",
        "--task-class", "continuous_monitor", "--action-kind", "monitor",
        "--target-key", "existing-future-window", "--cadence", "1h",
        "--next-due-at", due, "--expires-at", "2099-01-02T00:00:00Z",
        "--claimed-by", cli.AGENT_ID, "--operation-id", "existing-future-monitor",
    )
    assert code == 0, added
    monitor_id = added["todo_id"]
    code, linked = run(
        "todo", "update", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID,
        "--agent-id", cli.AGENT_ID, "--successor-todo-id", monitor_id, *lease,
        "--update-operation-id", "link-existing-future-monitor",
        "--update-expected-provider-revision", added["provider_revision"],
    )
    assert code == 0, linked
    return monitor_id, due


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("unscoped_first", [False, True])
def test_supersede_existing_future_monitor_settles_only_original_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    unscoped_first: bool,
) -> None:
    project, _, runtime, _, run, lease, marker, _, original = declared._fixture(
        tmp_path, monkeypatch, provider,
    )
    monitor_id, due = _future_monitor(run, lease)
    binding = ("--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
               "--turn-instance-id", cli.TURN_ID)
    guard_args = ("quota", "should-run", "--codex-app", "--goal-id", cli.GOAL_ID,
                  "--scan-path", str(project), *binding,
                  "--available-capability", "shell", "--available-capability", "filesystem_write")
    code, guard = run(*guard_args)
    assert code == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    retirement = ("todo", "supersede", "--goal-id", cli.GOAL_ID,
                  "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
                  "--reason", "The original window expired; retain its existing future monitor.", *lease)
    if unscoped_first:
        code, unscoped = run(*retirement)
        assert code == 0, unscoped
        assert unscoped["superseded"] is True
        readback = read_heartbeat_settlement(
            runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
            todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID,
        )
        assert readback is not None and readback.replay_phase.value == "open"
    code, retired = run(*retirement, "--turn-instance-id", cli.TURN_ID)
    assert code == 0, retired
    assert retired["superseded"] is True and retired["completed"] is False
    assert retired["settlement_identity"] == identity
    assert retired["changed"] is (not unscoped_first)
    assert not marker.exists()  # Superseding is not deliverable acceptance.

    refresh_args = (
        "refresh-state", "--goal-id", cli.GOAL_ID, *binding,
        "--classification", "original_window_superseded", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--vision-state", "active",
        "--vision-summary", "Retired an expired task while preserving its future observation.",
        "--vision-acceptance", "The future monitor remains open with its original due time.",
        "--no-global-sync", "--suppress-external-sinks",
    )
    code, refreshed = run(*refresh_args)
    assert code == 0, refreshed
    spend_args = ("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *binding,
                  "--slots", "1", "--source", "heartbeat", "--execute", "--scan-path", str(project))
    code, spent = run(*spend_args)
    assert code == 0, spent
    assert spent["settlement_progress"]["state"] == "settled"
    for args in (guard_args, (*retirement, "--turn-instance-id", cli.TURN_ID), spend_args):
        code, replay = run(*args)
        assert code == 0, replay
        if args is guard_args:
            assert replay["effective_action"] == "heartbeat_settled_skip"
            assert replay["should_run"] is False
            assert replay.get("selected_todo") is None
            assert replay["heartbeat_receipt"]["settlement_identity"] == identity
        else:
            assert replay["idempotent_replay"] is True
    assert cli._spend_run_count(runtime) == 1
    code, listed = run("todo", "list", "--goal-id", cli.GOAL_ID)
    assert code == 0, listed
    assert len(listed["todos"]) == 2
    source = next(t for t in listed["todos"] if t["todo_id"] == cli.TODO_ID)
    monitor = next(t for t in listed["todos"] if t["todo_id"] == monitor_id)
    assert source["status"] == "done" and source["successor_todo_ids"] == [monitor_id]
    assert source["completion_continuation"] == "active_goal"
    assert source["completion_validation_sha256"] == original["completion_validation_sha256"]
    assert monitor["status"] == "open" and monitor["next_due_at"] == due
    readback = read_heartbeat_settlement(
        runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID,
    )
    assert readback is not None and readback.replay_phase.value == "settled"
    assert readback.completion_event is None
    assert readback.terminal_closeout.failure is not None


def test_supersede_foreign_turn_rejects_before_canonical_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _, runtime, _, run, lease, marker, _, _ = declared._fixture(
        tmp_path, monkeypatch, "file",
    )
    code, guard = run(
        "quota", "should-run", "--codex-app", "--goal-id", cli.GOAL_ID,
        "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
        "--turn-instance-id", cli.TURN_ID, "--scan-path", str(project),
        "--available-capability", "shell", "--available-capability", "filesystem_write",
    )
    assert code == 0, guard
    code, rejected = run(
        "todo", "supersede", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID,
        "--agent-id", cli.AGENT_ID, "--turn-instance-id", "foreign-turn",
        "--reason", "Retire the expired original window.", *lease,
    )
    assert code == 1, rejected
    assert "matching quota should-run heartbeat receipt is missing" in rejected["error"]
    code, listed = run("todo", "list", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID)
    assert code == 0 and listed["todo"]["status"] == "open", listed
    assert not marker.exists() and cli._spend_run_count(runtime) == 0
