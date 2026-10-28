"""Machine consumers retain quota semantics when native presentation is enabled."""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.claude_goal_mode.scripts import goalmode_cmd
from loopx.claude_goal_mode.statusline import goal_status
from loopx.claude_goal_mode.hooks import goal_policy
from loopx.control_plane.host_adapter_settlement import HostTodoSettlementRequest, _prepare
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_context_machine_configuration import configure
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _initialize_fixture_authority, _spend_run_count, _write_fixture,
)


@pytest.fixture(params=["off", "on"])
def machine(request, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "machine-guard-fixture")
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime)
    assert configure(registry, runtime, tmp_path, request.param)[0] == 0
    monkeypatch.chdir(project)
    prefix = [sys.executable, "-m", "loopx.cli", "--runtime-root", str(runtime)]
    context = {"goal_id": GOAL_ID, "agent_id": AGENT_ID, "registry": str(registry)}
    config_path = runtime / "machine/configuration.json"
    configuration = config_path.read_bytes()
    yield context, prefix, runtime
    assert config_path.read_bytes() == configuration
    assert "LOOPX_CONTEXT_DELIVERY" not in os.environ


def assert_decision(payload, runtime):
    assert payload["ok"] is True, payload
    assert payload["should_run"] is True, payload
    assert payload["goal_id"] == GOAL_ID
    assert payload["selected_todo"]["todo_id"] == TODO_ID
    assert _spend_run_count(runtime) == 0
    assert not (runtime / "context-delivery").exists()


def test_claude_status_detail_reads_full_decision(machine, monkeypatch):
    context, prefix, runtime = machine
    monkeypatch.setattr(goalmode_cmd, "gh_prefix", lambda: prefix)
    _, payload = goalmode_cmd.goal_detail(context)
    assert_decision(payload, runtime)


def test_claude_statusline_reads_full_decision(machine, monkeypatch, capsys):
    context, prefix, runtime = machine
    monkeypatch.setattr(goal_status, "_gh_prefix", lambda: prefix)
    monkeypatch.setattr(goal_status, "active_context", lambda _: context)
    monkeypatch.setattr(goal_status, "_render", lambda _, payload: json.dumps(payload))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    goal_status.main()
    assert_decision(json.loads(capsys.readouterr().out), runtime)


def test_claude_tool_gate_reads_raw_decision(machine, monkeypatch):
    context, prefix, runtime = machine
    monkeypatch.setattr(goal_policy, "_gh_prefix", lambda: prefix)
    assert goal_policy.should_run(context["registry"], GOAL_ID, AGENT_ID) is True
    assert _spend_run_count(runtime) == 0
    assert not (runtime / "context-delivery").exists()


@pytest.mark.parametrize("completion", [False, True])
def test_mcp_and_typed_completion_guards_use_raw_transport(machine, completion):
    context, prefix, runtime = machine
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(server_name="fixture", runtime_profile="codex_app_ssh_goal",
                          legacy_host_surface="codex_app_ssh"),
        lambda: context,
    )
    control.command_prefix = lambda: prefix
    if completion:
        request = HostTodoSettlementRequest(
            goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
            runtime_profile="codex_app_ssh_goal", legacy_host_surface="codex_app_ssh",
            scheduler_owner="agent_cli_loop", execution_mode="interactive",
            completion_args=(),
        )
        plan = _prepare(request)
        step = plan["provider_effect"]["steps"][0]
        args, legacy = step["args"], step["legacy_args"]
    else:
        args, legacy = control.should_run_args(GOAL_ID, AGENT_ID)
    payload = json.loads(control.run_cli(args, legacy_args=legacy))
    assert_decision(payload, runtime)
    if completion:
        assert payload["heartbeat_receipt"]["settlement_identity"] == plan["identity"]
    # Both current and legacy host declarations use the same machine contract.
    legacy_payload = json.loads(control.run_cli(legacy))
    assert_decision(legacy_payload, runtime)


def test_rejected_guard_never_completes_or_spends_with_machine_on(machine):
    context, prefix, runtime = machine
    registry = Path(context["registry"])
    document = json.loads(registry.read_text())
    document["goals"][0]["adapter"].pop("status")
    registry.write_text(json.dumps(document))
    state = registry.parent.parent / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    before = state.read_bytes()
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(server_name="fixture", runtime_profile="codex_app_ssh_goal",
                          legacy_host_surface="codex_app_ssh"),
        lambda: context,
    )
    control.command_prefix = lambda: prefix
    result = json.loads(control.complete_task(TODO_ID, AGENT_ID, "must not complete"))
    assert result["ok"] is False and result["settlement_blocked_completion"] is True
    assert result["settlement"]["failed_stage"] == "guard"
    assert state.read_bytes() == before
    assert _spend_run_count(runtime) == 0
    assert not (runtime / "context-delivery").exists()


def test_mcp_legacy_retry_keeps_raw_transport_without_new_parser_flags(monkeypatch):
    import loopx.goal_mode_mcp as mcp

    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]))
        assert "--context-delivery" not in argv
        assert kwargs["env"]["LOOPX_CONTEXT_DELIVERY"] == "off"
        if "--runtime-profile" in argv:
            return SimpleNamespace(returncode=2, stdout="", stderr="unrecognized arguments: --runtime-profile")
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"should_run":false}', stderr="")

    monkeypatch.setattr(mcp.subprocess, "run", run)
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(server_name="fixture", runtime_profile="claude_code", legacy_host_surface="claude_code"),
        lambda: {"goal_id": GOAL_ID, "agent_id": AGENT_ID},
    )
    args, legacy = control.should_run_args(GOAL_ID, AGENT_ID)
    result = json.loads(control.run_cli(args, legacy_args=legacy))
    assert result == {"ok": True, "should_run": False}
    assert len(calls) == 2 and os.environ["LOOPX_CONTEXT_DELIVERY"] == "on"
