"""A due check with a negative result must yield until its next cadence."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.testing.canary_harness import run_json_cli
from tests.control_plane.test_monitor_followthrough_contract import AGENT_ID, GOAL_ID, _write_fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("scoped_gate", [False, True])
@pytest.mark.parametrize("bound_turn", [False, True])
def test_poll(tmp_path, monkeypatch, provider, scoped_gate, bound_turn):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = _write_fixture(tmp_path, provider=provider)

    def call(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)

    if scoped_gate:
        waiting = call("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
            "--text", "Process the supplied input", "--task-class", "advancement_task",
            "--status", "blocked", "--claimed-by", AGENT_ID)
        call("todo", "add", "--goal-id", GOAL_ID, "--role", "user",
            "--text", "Supply the input", "--task-class", "user_gate",
            "--agent-id", AGENT_ID, "--blocks-agent", AGENT_ID,
            "--unblocks-todo-id", waiting["todo_id"])
    monitor = call("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--text", "Check inputs/ready.json once when due; wait while missing.",
        "--task-class", "continuous_monitor", "--claimed-by", AGENT_ID,
        "--target-key", "input-ready", "--cadence", "1m",
        "--next-due-at", "2000-01-01T00:00:00Z", "--watch-only")
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--runtime-profile", "generic_cli")
    guard = call("quota", "should-run", *scope,
        *(("--turn-instance-id", "missing-input") if bound_turn else ()))
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]
    if not bound_turn:
        (binding,) = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
        bind_args = shlex.split(binding)[1:]
        assert "--use-current-task-lease" not in bind_args
        bind_args[bind_args.index("--turn-instance-id") + 1] = "missing-input"
        guard = call(*bind_args)
        assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == monitor["todo_id"]
    channel = guard["interaction_contract"]["cli_channel"]
    assert "condition unmet" in channel["next_cli_actions"][0]
    assert "unchanged observation" in channel["next_cli_actions"][0]
    commands = [shlex.split(command) for command in channel["next_cli_actions"]]
    poll_commands = [argv for argv in commands if "monitor-poll" in argv]
    assert len(poll_commands) == 1, channel["next_cli_actions"]
    assert "only if unable to observe" in channel["spend_policy"]
    assert channel["spend_after_validation"] is False

    # This is a successful negative observation, not an unavailable tool/source.
    assert not (tmp_path / "project/inputs/ready.json").exists()
    argv = poll_commands[0][1:]
    argv[argv.index("--result-hash") + 1] = "input-absent"
    poll = call(*argv)
    assert poll["ok"] is True
    writeback = poll["todo_writeback"]
    checked = datetime.fromisoformat(writeback["last_checked_at"])
    due = datetime.fromisoformat(writeback["next_due_at"].replace("Z", "+00:00"))
    assert due - checked == timedelta(minutes=1)
    assert writeback["material_change"] is False
    assert writeback["consecutive_no_change"] == 1
    assert poll["turn_continuation"]["current_turn_settled"] is True
    replay = call(*argv)
    assert replay["replayed"] is True
    persisted = call("todo", "list", "--goal-id", GOAL_ID, "--todo-id", monitor["todo_id"])["todo"]
    assert persisted["next_due_at"] == writeback["next_due_at"]

    next_turn = call("quota", "should-run", *scope, "--turn-instance-id", "after-missing-input")
    assert next_turn["should_run"] is False, next_turn
    assert next_turn["scheduler_hint"]["action"] != "run_now"
    assert next_turn["agent_todo_summary"]["monitor_due_count"] == 0
    rows = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    assert sum(row["classification"] == "quota_monitor_poll" and row.get("todo_id") == monitor["todo_id"] for row in rows) == 1
    assert not any(row["classification"] in {"quota_slot_spent", "state_refreshed"} for row in rows)


@pytest.mark.parametrize("mode", ["full", "compact", "brief", "thin", "envelope"])
def test_common_prompt_retains_settlement_and_defers_monitor_details(mode, tmp_path):
    from loopx.heartbeat_prompt import build_heartbeat_prompt

    options = ({"thin": True, "turn_envelope": True, "decision_output_root": tmp_path}
        if mode == "envelope" else {mode: True})
    prompt = build_heartbeat_prompt(goal_id=GOAL_ID, agent_id=AGENT_ID,
        runtime_profile="generic_cli", turn_instance_id="prompt-turn", **options)
    body = prompt["task_body"]
    assert "Exact monitor settlement=no refresh/spend" in body
    assert "settlement_plan" in body
    assert "including an absent watched file" not in body


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_powershell_uses_exact_cli_and_monitor_receipt(tmp_path, monkeypatch, provider):
    from loopx.control_plane.quota import effect_program
    from loopx import install_contract
    from loopx.cli import main

    shell = shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = _write_fixture(tmp_path, provider=provider, handoff_mode="hard_lease")
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--runtime-profile", "generic_cli")
    monitor = run_json_cli("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--text", "Check for a missing input", "--task-class", "continuous_monitor",
        "--claimed-by", AGENT_ID, "--target-key", "input-ready", "--cadence", "1m",
        "--next-due-at", "2000-01-01T00:00:00Z", "--watch-only",
        registry_path=registry, runtime_root=runtime)
    claim = run_json_cli("todo", "claim", "--goal-id", GOAL_ID, "--todo-id", monitor["todo_id"],
        "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID, "--claim-operation-id", "pwsh-monitor",
        registry_path=registry, runtime_root=runtime)
    assert claim["execution_managed"] is True
    lease = run_json_cli("task-lease", "inspect", "--goal-id", GOAL_ID, "--todo-id", monitor["todo_id"],
        registry_path=registry, runtime_root=runtime)
    assert lease["active"] and lease["lease"]["owner"] == AGENT_ID
    launcher = tmp_path / "fixture agent's LoopX.ps1"
    launch_python = install_contract.powershell_literal(sys.executable)
    # Bind the checkout directly and keep the exact kwargs emitted by the guard.
    launcher.write_text(
        f"& {launch_python} -m loopx.entrypoint @args\nexit $LASTEXITCODE\n", encoding="utf-8",
    )
    monkeypatch.setattr(effect_program, "_is_windows_host", lambda: True)
    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: True)
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "off")
    from io import StringIO
    from contextlib import redirect_stdout
    text = StringIO()
    with redirect_stdout(text):
        assert main(["--format", "json", "--cli-bin", str(launcher), "--registry", str(registry),
                     "--runtime-root", str(runtime), "quota", "should-run", *scope,
                     "--turn-instance-id", "missing-input-pwsh"]) == 0
    guard = json.loads(text.getvalue())
    assert (guard.get("selected_todo") or {}).get("todo_id") == monitor["todo_id"], guard
    commands = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    polls = [command for command in commands if " quota monitor-poll " in command]
    assert len(polls) == 1, (guard.get("reason"), guard.get("work_lane_contract"), commands)
    [poll] = polls
    assert poll.startswith("& " + install_contract.powershell_literal(launcher))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    env.pop("LOOPX_MONITOR_RESULT_HASH", None)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", poll],
        capture_output=True, text=True, env=env, timeout=25)
    assert result.returncode != 0 and "LOOPX_MONITOR_RESULT_HASH" in result.stderr
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
        "$env:LOOPX_MONITOR_RESULT_HASH = 'input-absent'\n" + poll],
        capture_output=True, text=True, env=env, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    current = run_json_cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", monitor["todo_id"],
        registry_path=registry, runtime_root=runtime)["todo"]
    assert current["next_due_at"] != "2000-01-01T00:00:00Z"
    assert current["consecutive_no_change"] == "1"
    records = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text().splitlines()]
    observations = [row for row in records if row["classification"] == "quota_monitor_poll"]
    assert [row["todo_id"] for row in observations] == [monitor["todo_id"]]
    assert observations[0]["turn_instance_id"] == "missing-input-pwsh"
    assert not any(row["classification"] == "quota_slot_spent" for row in records)
