"""A due Monitor is told that its observation cannot complete a User gate."""
from __future__ import annotations

import json

import pytest
from canonical_authority_fixture import isolate_sqlite_runtime

from loopx.control_plane.testing.canary_harness import run_json_cli, run_json_cli_result
from loopx.control_plane.work_items.interaction_contract import MONITOR_USER_GATE_NOTICE
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID,
    GOAL_ID,
    _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_monitor_observation_never_projects_gate_completion(tmp_path, tmp_path_factory, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path_factory.mktemp("g") / "a b", provider=provider,
        handoff_mode="hard_lease")

    def cli(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)

    def rejected(*args):
        rc, result = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
        assert rc == 1 and result["ok"] is False, result
        return result

    def actions(guard):
        return guard["interaction_contract"]["cli_channel"]["next_cli_actions"]

    actor = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    scope = (*actor, "--runtime-profile", "generic_cli")
    monitor_args = ("todo", "add", "--goal-id", GOAL_ID, "--role", "agent", "--task-class", "continuous_monitor",
        "--claimed-by", AGENT_ID, "--target-key", "prices-input", "--cadence", "1m",
        "--expires-at", "2099-01-01T00:00:00Z", "--next-due-at", "2000-01-01T00:00:00Z",
        "--text", "Observe inputs/prices.json once when due.")
    monitor = cli(*monitor_args)["todo_id"]
    claim = cli("todo", "claim", *actor, "--todo-id", monitor, "--claimed-by", AGENT_ID)
    # Without an open User gate the projection is unchanged.
    plain = cli("quota", "should-run", *scope, "--turn-instance-id", "no-gate")
    assert plain["selected_todo"]["todo_id"] == monitor
    assert MONITOR_USER_GATE_NOTICE not in actions(plain)

    work = cli("todo", "add", "--goal-id", GOAL_ID, "--role", "agent", "--task-class", "advancement_task",
        "--claimed-by", AGENT_ID, "--status", "blocked",
        "--text", "Compute value from the supplied prices.")["todo_id"]
    gate = cli("todo", "add", *actor, "--role", "user", "--task-class", "user_gate",
        "--unblocks-todo-id", work, "--text", "Provide inputs/prices.json.")["todo_id"]
    # An unrelated owner decision for the same agent must never receive an approval recipe either.
    release = cli("todo", "add", *actor, "--role", "user", "--task-class", "user_gate",
        "--decision-scope", "production:goal:release", "--text", "Approve the release.")["todo_id"]

    guard = cli("quota", "should-run", *scope, "--turn-instance-id", "no-gate")
    assert guard["selected_todo"]["todo_id"] == monitor
    projected = actions(guard)
    assert "condition unmet" in projected[0]
    assert projected[1] == MONITOR_USER_GATE_NOTICE
    commands = [action for action in projected if action.startswith("loopx ")]
    assert len(commands) == 1 and " quota monitor-poll " in commands[0], projected
    assert not any("todo complete" in action or "--decision-outcome" in action for action in projected)
    assert gate not in " ".join(projected) and release not in " ".join(projected)

    price_file = state.parents[3] / "inputs" / "prices.json"
    price_file.parent.mkdir()
    price_file.write_text('{"A":199}', encoding="utf-8")
    poll = cli("quota", "monitor-poll", *scope, "--turn-instance-id", "no-gate", "--todo-id", monitor,
        "--target-key", "prices-input", "--result-hash", "prices-present", "--material-change",
        "--use-current-task-lease", "--execute")
    assert poll["material_change"] is True
    assert poll["turn_continuation"]["current_turn_settled"] is True
    # The two attempts the notice rules out stay rejected by their existing owners.
    denied = rejected("todo", "complete", *actor, "--role", "user", "--todo-id", gate,
        "--turn-instance-id", "no-gate", "--decision-outcome", "approve", "--evidence", "Actual input observed")
    assert "settlement binding does not match the original quota guard" in denied["error"]
    denied = rejected("todo", "update", *actor, "--todo-id", gate, "--evidence", "Actual input observed")
    assert denied["error_code"] == "handoff_mode_requires_lease"
    rows = {row["todo_id"]: row for row in cli("todo", "list", "--goal-id", GOAL_ID)["todos"]}
    assert rows[gate]["status"] == "open" and rows[release]["status"] == "open"
    assert rows[work]["status"] == "blocked"

    # A later unchanged check still carries the notice and still offers no completion.
    cli("todo", "update", *actor, "--todo-id", monitor, "--claim-operation-id", claim["claim_operation_id"],
        "--next-due-at", "2000-01-01T00:00:00Z")
    unchanged = cli("quota", "should-run", *scope, "--turn-instance-id", "unchanged-monitor")
    assert unchanged["selected_todo"]["todo_id"] == monitor
    assert MONITOR_USER_GATE_NOTICE in actions(unchanged)
    assert not any("todo complete" in action for action in actions(unchanged))

    # The owner's own decision remains the only route, and resumes the linked work for a fresh Turn.
    completed = cli("todo", "complete", *actor, "--role", "user", "--todo-id", gate,
        "--decision-outcome", "approve", "--evidence", "Owner confirmed the supplied prices")
    assert completed["unblock_resume"]["state"] == "resumed"
    rows = {row["todo_id"]: row for row in cli("todo", "list", "--goal-id", GOAL_ID)["todos"]}
    assert rows[work]["status"] == "open" and rows[release]["status"] == "open"
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    events = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert sum(row["classification"] == "quota_monitor_poll" for row in events) == 1
    assert not any(row["classification"] == "quota_slot_spent" for row in events)
