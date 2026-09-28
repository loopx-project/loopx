"""Kiro CLI reaches LoopX's typed control plane through the shared MCP server.

Three contracts matter and each is exercised for real rather than mocked:

* identity: the server acts only for the agent the running session's
  ``KIRO_SESSION_ID`` is bound to, and fails closed otherwise;
* settlement: a completion through the Kiro server runs the same typed
  guard -> lifecycle -> writeback -> spend path Claude uses, under the Kiro
  runtime profile;
* install: the kiro-cli surface registers exactly one ``loopx`` entry in
  ``KIRO_HOME/settings/mcp.json`` and never takes over a user's own server.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx import slash_command_install
from loopx.goal_mode_mcp import GoalModeMCPControlPlane
from loopx.kiro_cli_goal_mode import (
    KIRO_CLI_MCP_CONFIG_SUBPATH,
    KIRO_CLI_SESSION_ID_ENV,
    mcp_server_script,
)
from loopx.kiro_cli_goal_mode.mcp_server import CONFIG, goal_context
from loopx.slash_command_install import install_slash_commands
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo

GOAL_ID = "kiro-mcp-fixture"
AGENT_ID = "kiro-worker"
OTHER_AGENT_ID = "codex-main"
SESSION_ID = "0f5d0a52-5b8e-4c55-9a8e-4a3f2b1c9d10"


def _write_project(
    tmp_path: Path,
    *,
    bindings: list[dict[str, str]] | None = None,
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "---\n"
        f"goal_id: {GOAL_ID}\n"
        "status: active\n"
        "updated_at: 2026-08-21T00:00:00+00:00\n"
        "---\n\n## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            # A different agent first: resolution must never
                            # fall back to registry order.
                            "registered_agents": [OTHER_AGENT_ID, AGENT_ID],
                            "thread_agent_bindings": (
                                bindings
                                if bindings is not None
                                else [
                                    {
                                        "thread_id": SESSION_ID,
                                        "host_surface": "kiro-cli",
                                        "agent_id": AGENT_ID,
                                    }
                                ]
                            ),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, registry, state_file


def test_session_binding_selects_the_bound_agent_not_registry_order(
    tmp_path: Path,
) -> None:
    project, registry, _ = _write_project(tmp_path)
    context = goal_context(project, {KIRO_CLI_SESSION_ID_ENV: SESSION_ID})
    assert context is not None
    assert context["goal_id"] == GOAL_ID
    assert context["agent_id"] == AGENT_ID
    assert context["registry"] == str(registry)


@pytest.mark.parametrize(
    ("environ", "bindings"),
    (
        # The host exported no session id.
        ({}, None),
        # A session nobody bound.
        ({KIRO_CLI_SESSION_ID_ENV: "another-session"}, None),
        # The same id bound under another host surface is not a Kiro binding.
        (
            {KIRO_CLI_SESSION_ID_ENV: SESSION_ID},
            [{"thread_id": SESSION_ID, "host_surface": "codex-app", "agent_id": AGENT_ID}],
        ),
        # One session bound to two lanes is ambiguous, never a pick.
        (
            {KIRO_CLI_SESSION_ID_ENV: SESSION_ID},
            [
                {"thread_id": SESSION_ID, "host_surface": "kiro-cli", "agent_id": AGENT_ID},
                {"thread_id": SESSION_ID, "host_surface": "kiro-cli", "agent_id": OTHER_AGENT_ID},
            ],
        ),
        # A binding to an agent the Goal does not register.
        (
            {KIRO_CLI_SESSION_ID_ENV: SESSION_ID},
            [{"thread_id": SESSION_ID, "host_surface": "kiro-cli", "agent_id": "ghost"}],
        ),
    ),
)
def test_unbound_or_ambiguous_sessions_fail_closed(
    tmp_path: Path,
    environ: dict[str, str],
    bindings: list[dict[str, str]] | None,
) -> None:
    project, _, _ = _write_project(tmp_path, bindings=bindings)
    assert goal_context(project, environ) is None
    control = GoalModeMCPControlPlane(CONFIG, lambda: goal_context(project, environ))
    refused = json.loads(control.should_run())
    assert refused["ok"] is False
    assert "Kiro CLI session" in refused["next_action"]


def _run_cli(registry: Path, *args: str) -> tuple[int, dict[str, object]]:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode, json.loads(result.stdout)


def test_completion_through_the_kiro_server_settles_once_under_its_profile(
    tmp_path: Path,
) -> None:
    project, registry, state_file = _write_project(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver the bounded Kiro fixture change.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
        continuation_policy="same_agent_non_delivery",
    )
    todo_id = str(added["todo_id"])
    environ = {KIRO_CLI_SESSION_ID_ENV: SESSION_ID}
    control = GoalModeMCPControlPlane(CONFIG, lambda: goal_context(project, environ))
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]

    # The gate runs under the Kiro profile for the bound agent.
    gate = json.loads(control.should_run())
    assert gate["goal_id"] == GOAL_ID
    assert gate["agent_identity"]["agent_id"] == AGENT_ID
    assert any(
        f"--agent-id {AGENT_ID} --runtime-profile kiro_cli" in action
        for action in gate["interaction_contract"]["cli_channel"]["next_cli_actions"]
    ), gate["interaction_contract"]["cli_channel"]

    # The server refuses to act for an agent the session is not bound to.
    foreign = json.loads(control.claim_task(todo_id, OTHER_AGENT_ID))
    assert foreign["ok"] is False
    assert foreign["bound_agent_id"] == AGENT_ID

    result = json.loads(
        control.complete_task(
            todo_id,
            AGENT_ID,
            "focused Kiro fixture validation passed",
            next_agent_todo="Run the Kiro successor fixture check.",
        )
    )
    assert result["ok"] is True, json.dumps(result, indent=2, sort_keys=True)
    settlement = result["settlement"]
    assert settlement["durable_writeback"]["ok"] is True
    assert settlement["quota_spend"]["appended"] is True

    status_rc, status = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        CONFIG.runtime_profile,
    )
    assert status_rc == 0, status
    assert status["quota"]["spent_slots"] == 1  # type: ignore[index]
    todos = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    by_id = {str(item["todo_id"]): item for item in todos["agent_todos"]["items"]}
    assert by_id[todo_id]["status"] == "done"


def test_server_entrypoint_starts_as_a_standalone_script() -> None:
    """Kiro launches the registered script with the provisioned interpreter,
    not as an installed module, so it must import cleanly by path."""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; sys.argv=['x']; "
            f"ns = runpy.run_path({str(mcp_server_script())!r}, run_name='probe'); "
            "print(ns['CONFIG'].runtime_profile)",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd="/",
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "kiro_cli"


def _mcp_row(payload: dict[str, object]) -> dict[str, object]:
    rows = [
        row
        for row in payload["installed"]  # type: ignore[union-attr]
        if row["mechanism"] == "kiro_cli_mcp_server"  # type: ignore[index]
    ]
    assert len(rows) == 1
    return rows[0]  # type: ignore[return-value]


def test_install_registers_one_loopx_entry_and_uninstall_retires_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {"command": "/opt/loopx/bin/python", "args": [str(mcp_server_script())]}
    monkeypatch.setattr(
        slash_command_install,
        "_kiro_cli_mcp_command",
        lambda: (entry["command"], entry["args"][0]),
    )
    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    config.parent.mkdir(parents=True)
    other = {"command": "codegraph", "args": ["serve", "--mcp"]}
    config.write_text(json.dumps({"mcpServers": {"codegraph": other}}), encoding="utf-8")

    preview = install_slash_commands(
        execute=False, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _mcp_row(preview)["status"] == "would_write"
    assert json.loads(config.read_text(encoding="utf-8")) == {"mcpServers": {"codegraph": other}}

    payload = install_slash_commands(
        execute=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    row = _mcp_row(payload)
    assert row["status"] == "written"
    assert row["path"] == str(config)
    assert payload["summary"]["kiro_cli_mcp_path"] == str(config)  # type: ignore[index]
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "mcpServers": {"codegraph": other, "loopx": entry}
    }

    again = install_slash_commands(execute=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home))
    assert _mcp_row(again)["status"] == "unchanged"

    removed = install_slash_commands(
        execute=True, uninstall=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _mcp_row(removed)["status"] == "retired"
    assert json.loads(config.read_text(encoding="utf-8")) == {"mcpServers": {"codegraph": other}}


def test_install_never_takes_over_a_user_owned_loopx_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slash_command_install,
        "_kiro_cli_mcp_command",
        lambda: ("/opt/loopx/bin/python", str(mcp_server_script())),
    )
    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    config.parent.mkdir(parents=True)
    mine = {"mcpServers": {"loopx": {"command": "my-own-loopx", "args": []}}}
    config.write_text(json.dumps(mine), encoding="utf-8")

    for uninstall in (False, True):
        payload = install_slash_commands(
            execute=True,
            uninstall=uninstall,
            surfaces=["kiro-cli"],
            kiro_home=str(kiro_home),
        )
        assert _mcp_row(payload)["status"] == "skipped_user_owned_mcp_entry"
        assert json.loads(config.read_text(encoding="utf-8")) == mine


def test_install_reports_a_malformed_config_instead_of_rewriting_it(
    tmp_path: Path,
) -> None:
    kiro_home = tmp_path / "kiro-home"
    config = kiro_home / KIRO_CLI_MCP_CONFIG_SUBPATH
    config.parent.mkdir(parents=True)
    config.write_text('{"mcpServers": ["not", "a", "map"]}', encoding="utf-8")
    payload = install_slash_commands(
        execute=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _mcp_row(payload)["status"] == "blocked_invalid_kiro_cli_mcp_json"
    assert config.read_text(encoding="utf-8") == '{"mcpServers": ["not", "a", "map"]}'
