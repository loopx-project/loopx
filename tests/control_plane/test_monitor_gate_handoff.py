"""An observed prerequisite can resolve its gate without rebinding a Monitor Turn."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

import pytest
from canonical_authority_fixture import isolate_sqlite_runtime

from loopx.control_plane.testing.canary_harness import run_json_cli, run_json_cli_result
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID,
    GOAL_ID,
    _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_gate_handoff(tmp_path, tmp_path_factory, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path_factory.mktemp("g") / "a b", provider=provider,
        handoff_mode="hard_lease")

    def cli(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)

    def rejected(*args):
        rc, result = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
        assert rc == 1 and result["ok"] is False, result
        return result

    actor = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    work = cli("todo", "add", "--goal-id", GOAL_ID, "--role", "agent", "--task-class", "advancement_task",
        "--claimed-by", AGENT_ID, "--status", "blocked",
        "--text", "Compute value from the supplied prices.")["todo_id"]
    gate = cli("todo", "add", *actor, "--role", "user", "--task-class", "user_gate",
        "--unblocks-todo-id", work,
        "--text", "Provide inputs/prices.json; authorized observation may confirm receipt.")["todo_id"]
    monitor = cli("todo", "add", "--goal-id", GOAL_ID, "--role", "agent", "--task-class", "continuous_monitor",
        "--claimed-by", AGENT_ID, "--target-key", "prices-input", "--cadence", "1m",
        "--expires-at", "2099-01-01T00:00:00Z",
        "--next-due-at", "2000-01-01T00:00:00Z", "--text",
        "Observe inputs/prices.json; resolve its authorized file prerequisite, then yield.")["todo_id"]
    claim = cli("todo", "claim", *actor, "--todo-id", monitor, "--claimed-by", AGENT_ID)
    proof = ("--claim-operation-id", claim["claim_operation_id"])
    scope = (*actor, "--runtime-profile", "generic_cli")
    guard = cli("quota", "should-run", *scope, "--turn-instance-id", "changed-monitor")
    assert guard["selected_todo"]["todo_id"] == monitor
    price_file = state.parents[3] / "inputs" / "prices.json"
    price_file.parent.mkdir()
    price_file.write_text('{"A":199}', encoding="utf-8")
    assert json.loads(price_file.read_text()) == {"A": 199}
    poll = cli("quota", "monitor-poll", *scope, "--turn-instance-id", "changed-monitor",
        "--todo-id", monitor, "--target-key", "prices-input", "--result-hash", "prices-present",
        "--material-change", "--use-current-task-lease", "--execute")
    assert poll["material_change"] is True
    assert poll["turn_continuation"]["current_turn_settled"] is True
    assert cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", gate)["todo"]["status"] == "open"
    denied = rejected("todo", "complete", *actor, "--role", "user", "--todo-id", gate,
        "--turn-instance-id", "changed-monitor", "--decision-outcome", "approve",
        "--evidence", "Actual input observed")
    assert "settlement binding does not match the original quota guard" in denied["error"]
    denied = rejected("todo", "update", *actor, "--todo-id", gate, "--evidence", "Actual input observed")
    assert denied["error_code"] == "handoff_mode_requires_lease"

    # Public rescheduling replaces a wall-clock sleep; a later unchanged check
    # must still offer the unresolved prerequisite's completion route.
    cli("todo", "update", *actor, "--todo-id", monitor, *proof,
        "--next-due-at", "2000-01-01T00:00:00Z")
    next_guard = cli("quota", "should-run", *scope, "--turn-instance-id", "unchanged-monitor")
    assert next_guard["selected_todo"]["todo_id"] == monitor
    actions = next_guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    recipe = next(action for action in actions if "todo complete" in action and "--role user" in action)
    assert "even when the observation is unchanged" in " ".join(actions)
    command = "loopx " + recipe.split("loopx ", 1)[1]
    assert "--turn-instance-id" not in command and "--claim-operation-id" not in command
    command = command.replace("<verified-user-gate-id>", gate).replace(
        "<verified prerequisite evidence>", "inputs/prices.json observed and validated")
    def execute_rendered(command, result_hash=""):
        # Run the projected text in the host shell it was rendered for.
        python = sys.executable.replace("\\", "/")
        if os.name == "nt":
            script = "\n".join((f"function loopx {{ & '{python}' -m loopx.cli @args }}",
                "$LOOPX_TURN = 'unchanged-monitor'", f"$env:LOOPX_MONITOR_RESULT_HASH = '{result_hash}'", command))
            argv = ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script]
        else:
            script = "\n".join((f'loopx() {{ {shlex.quote(python)} -m loopx.cli "$@"; }}',
                "export LOOPX_TURN=unchanged-monitor",
                f"export LOOPX_MONITOR_RESULT_HASH={shlex.quote(result_hash)}", command))
            argv = ["bash", "--noprofile", "--norc", "-c", script]
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", check=False,
            env={**os.environ, "LOOPX_CONTEXT_DELIVERY": "off"})
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    completed = execute_rendered(command)
    assert completed["completion_identity_source"] == "unscoped_completion"
    assert completed["terminal_decision"]["lease_fence"] == "auto_acquire"
    assert completed["unblock_resume"]["state"] == "resumed"
    poll_command = next(action for action in actions if action.startswith("loopx ") and "quota monitor-poll" in action)
    assert actions.index(recipe) < actions.index(poll_command)
    observed = execute_rendered(poll_command.replace(" quota monitor-poll ", " --format json quota monitor-poll "),
        "prices-present")
    assert observed["material_change"] is False
    replay = cli("quota", "should-run", *scope, "--turn-instance-id", "unchanged-monitor")
    assert replay["should_run"] is False
    assert not any("todo complete" in action for action in
        replay["interaction_contract"]["cli_channel"]["next_cli_actions"])
    resumed = cli("quota", "should-run", *scope, "--turn-instance-id", "delivery-turn")
    assert resumed["selected_todo"]["todo_id"] == work
    assert resumed["execution_obligation"]["must_attempt_work"] is True
    assert resumed["execution_preparation"]["state"] == "claim_required"
    cli("todo", "claim", *actor, "--todo-id", work, "--claimed-by", AGENT_ID)
    ready = cli("quota", "should-run", *scope, "--turn-instance-id", "delivery-turn")
    assert ready["selected_todo"]["todo_id"] == work
    assert ready["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
    assert not any("verified-user-gate-id" in action for action in
        ready["interaction_contract"]["cli_channel"]["next_cli_actions"])
    rows = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    assert sum(row["classification"] == "quota_monitor_poll" for row in rows) == 2
    assert not any(row["classification"] == "quota_slot_spent" for row in rows)
