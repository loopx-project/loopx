from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx import goal_mode_mcp
from loopx import cli as loopx_cli
from loopx.claude_goal_mode.hooks.goal_state import goal_context as claude_context
from loopx.goal_mode_mcp import (
    GoalModeMCPConfig,
    GoalModeMCPControlPlane,
    create_fastmcp_server,
)
from loopx.control_plane.effect_program import SettlementIdentity
from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostRuntimeRejected,
)
from loopx.control_plane.goals.source_session_registry_state import guard_path
from loopx.control_plane.projects.registry_codec import (
    source_session_registry_transaction,
)
from loopx.file_lock import exclusive_cross_runtime_file_lock
from loopx.kunluncode_goal_mode import cli
from loopx.kunluncode_goal_mode.app_server import (
    NATIVE_GOAL_MODES,
    KunlunAppServerError,
    native_goal_success,
    stable_text_digest,
)
from loopx.kunluncode_goal_mode.context import (
    goal_context as kunluncode_context,
    write_binding,
)
from loopx.kunluncode_goal_mode.control_plane import LoopXControlPlane
from loopx.kunluncode_goal_mode.guards import (
    NATIVE_CONTROLLER_AGENT_ENV,
    NATIVE_CONTROLLER_ENV,
    NATIVE_CONTROLLER_GOAL_ENV,
    guard_native_controller_writeback,
    native_controller_cli_write_block,
)
from loopx.kunluncode_goal_mode.runtime import (
    RUNTIME_STATE_SCHEMA_VERSION,
    KunlunNativeGoalRuntimeError,
    build_native_goal_objective,
    read_runtime_state,
    run_native_goal,
    runtime_state_path,
    write_runtime_state,
)


def _registry(project: Path) -> Path:
    path = project / ".loopx" / "registry.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": "shared-goal",
                        "repo": str(project),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["cc", "kunlun"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def mock_goal_cli(monkeypatch):
    """Keep CLI test doubles out of the real Effect runtime subprocess path."""

    def install(runner):
        monkeypatch.setattr(goal_mode_mcp, "subprocess", SimpleNamespace(run=runner))

    return install


def test_goal_cli_mock_preserves_real_node_probe(mock_goal_cli):
    from loopx.control_plane import effect_runtime

    def fake_cli(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "not-json", "")

    mock_goal_cli(fake_cli)
    status, executable, version = effect_runtime._probe_node()
    assert status == "ready"
    assert executable is not None
    assert version is not None


def test_claude_and_kunluncode_bind_distinct_agents(tmp_path: Path) -> None:
    _registry(tmp_path)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    claude_dir.joinpath("loop.md").write_text(
        '<!-- loopx:armed {"goal_id":"shared-goal","agent_id":"cc"} -->\n',
        encoding="utf-8",
    )
    write_binding(tmp_path, goal_id="shared-goal", agent_id="kunlun")

    claude = claude_context(tmp_path)
    kunlun = kunluncode_context(tmp_path)

    assert claude and claude["agent_id"] == "cc"
    assert kunlun and kunlun["agent_id"] == "kunlun"
    assert claude["goal_id"] == kunlun["goal_id"] == "shared-goal"


def test_kunluncode_binding_fails_closed_for_unregistered_agent(tmp_path: Path) -> None:
    _registry(tmp_path)
    write_binding(tmp_path, goal_id="shared-goal", agent_id="not-registered")

    assert kunluncode_context(tmp_path) is None


def test_mcp_uses_kunluncode_profile_and_rejects_agent_impersonation(
    mock_goal_cli,
) -> None:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-kunluncode",
            runtime_profile="kunluncode",
            legacy_host_surface="kunluncode",
        ),
        lambda: {
            "goal_id": "shared-goal",
            "registry": "/project/.loopx/registry.json",
            "agent_id": "kunlun",
        },
    )
    commands: list[list[str]] = []
    control.command_prefix = lambda: ["loopx"]

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "--turn-instance-id" not in command:
            payload = '{"ok": true}'
        else:
            args = command[command.index("json") + 1 :]
            identity = SettlementIdentity(
                goal_id="shared-goal",
                agent_id="kunlun",
                todo_id="todo_333333333333",
                turn_instance_id=args[args.index("--turn-instance-id") + 1],
            )
            if "should-run" in command:
                payload = json.dumps(
                    {
                        "ok": True,
                        "should_run": True,
                        "selected_todo": {"todo_id": "todo_333333333333"},
                    }
                )
            elif "complete" in command:
                payload = json.dumps(
                    {
                        "ok": True,
                        "completed": True,
                        "status": "done",
                        "completion_continuation": (
                            "no_followup"
                            if "--no-follow-up" in command
                            else "active_goal"
                        ),
                        "settlement_identity": identity.as_dict(),
                        "settlement_result": {"failure": None},
                    }
                )
            else:
                payload = json.dumps(
                    {
                        "ok": True,
                        "appended": True,
                        "settlement_identity": identity.as_dict(),
                        "settlement_result": {"failure": None},
                    }
                )
        return subprocess.CompletedProcess(command, 0, payload, "")

    mock_goal_cli(capture)

    assert json.loads(control.claim_task("todo-1", "cc"))["ok"] is False
    assert commands == []

    assert json.loads(control.should_run())["ok"] is True
    assert commands[-1][-2:] == ["--runtime-profile", "kunluncode"]
    assert commands[-1][3:5] == ["--format", "json"]

    assert json.loads(control.claim_task("todo-1", "kunlun"))["ok"] is True
    assert commands[-1][-4:] == ["--claimed-by", "kunlun", "--agent-id", "kunlun"]

    command_count = len(commands)
    output = control.complete_task(
        "todo_333333333333",
        "kunlun",
        "focused check passed",
        task_lease_idempotency_key="lease-fixture",
        task_lease_expected_version=7,
        no_follow_up=True,
    )
    completed = json.loads(output)
    assert completed["ok"] is True
    settlement_commands = commands[command_count:]
    assert len(settlement_commands) == 5
    first_completion = settlement_commands[1]
    terminal_closeout = settlement_commands[-1]
    assert "--no-follow-up" not in first_completion
    assert "--no-follow-up" in terminal_closeout
    assert "--task-lease-idempotency-key" in first_completion
    assert "--task-lease-idempotency-key" in terminal_closeout
    assert completed["settlement"]["terminal_closeout"][
        "completion_continuation"
    ] == "no_followup"
    for lease_command in (first_completion, terminal_closeout):
        version_at = lease_command.index("--task-lease-expected-version")
        assert lease_command[version_at : version_at + 2] == [
            "--task-lease-expected-version",
            "7",
        ]


def test_fastmcp_complete_task_forwards_complete_task_lease_fence(
    mock_goal_cli,
) -> None:
    server, control = create_fastmcp_server(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": "shared-goal",
            "registry": "/project/.loopx/registry.json",
            "agent_id": "claude",
        },
    )
    commands: list[list[str]] = []
    control.command_prefix = lambda: ["loopx"]

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "--turn-instance-id" not in command:
            payload = '{"ok": true}'
        else:
            args = command[command.index("json") + 1 :]
            identity = SettlementIdentity(
                goal_id="shared-goal",
                agent_id="claude",
                todo_id="todo_777777777777",
                turn_instance_id=args[args.index("--turn-instance-id") + 1],
            )
            if "should-run" in command:
                payload = json.dumps(
                    {
                        "ok": True,
                        "should_run": True,
                        "selected_todo": {"todo_id": "todo_777777777777"},
                    }
                )
            elif "complete" in command:
                payload = json.dumps(
                    {
                        "ok": True,
                        "completed": True,
                        "status": "done",
                        "completion_continuation": "active_goal",
                        "settlement_identity": identity.as_dict(),
                        "settlement_result": {"failure": None},
                    }
                )
            else:
                payload = json.dumps(
                    {
                        "ok": True,
                        "appended": True,
                        "settlement_identity": identity.as_dict(),
                        "settlement_result": {"failure": None},
                    }
                )
        return subprocess.CompletedProcess(command, 0, payload, "")

    mock_goal_cli(capture)

    result = asyncio.run(
        server.call_tool(
            "complete_task",
            {
                "todo_id": "todo_777777777777",
                "agent_id": "claude",
                "evidence": "focused check passed",
                "task_lease_idempotency_key": "lease-fixture",
                "task_lease_expected_version": 7,
            },
        )
    )
    assert json.loads(result[0][0].text)["ok"] is True

    complete_commands = [
        command for command in commands if "complete" in command
    ]
    assert complete_commands, commands
    for complete_command in complete_commands:
        assert complete_command[
            complete_command.index("--task-lease-idempotency-key") :
        ][:2] == ["--task-lease-idempotency-key", "lease-fixture"]
        assert complete_command[
            complete_command.index("--task-lease-expected-version") :
        ][:2] == ["--task-lease-expected-version", "7"]


def test_mcp_spends_only_after_typed_completed_state(
    mock_goal_cli,
) -> None:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": "shared-goal",
            "registry": "/project/.loopx/registry.json",
            "agent_id": "claude",
        },
    )
    commands: list[list[str]] = []
    control.command_prefix = lambda: ["loopx"]

    def incomplete(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        payload = (
            json.dumps(
                {
                    "ok": True,
                    "should_run": True,
                    "selected_todo": {"todo_id": "todo_111111111111"},
                }
            )
            if "should-run" in command
            else '{"ok": true}'
        )
        return subprocess.CompletedProcess(command, 0, payload, "")

    mock_goal_cli(incomplete)

    output = control.complete_task("todo_111111111111", "claude", "not yet complete")

    assert json.loads(output)["ok"] is True
    assert len(commands) == 2
    assert not any("spend-slot" in command for command in commands)


def test_complete_task_spends_bound_to_selected_todo_and_refreshes_state(
    mock_goal_cli,
) -> None:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": "shared-goal",
            "registry": "/project/.loopx/registry.json",
            "agent_id": "claude",
        },
    )
    commands: list[list[str]] = []
    control.command_prefix = lambda: ["loopx"]

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        args = command[command.index("json") + 1 :]
        identity = SettlementIdentity(
            goal_id="shared-goal",
            agent_id="claude",
            todo_id="todo_222222222222",
            turn_instance_id=args[args.index("--turn-instance-id") + 1],
        )
        if "should-run" in command:
            payload = json.dumps(
                {
                    "ok": True,
                    "should_run": True,
                    "selected_todo": {
                        "todo_id": "todo_222222222222",
                        "source": "agent_lane_next_action",
                    },
                }
            )
        elif "complete" in command:
            payload = json.dumps(
                {
                    "ok": True,
                    "completed": True,
                    "status": "done",
                    "settlement_identity": identity.as_dict(),
                    "settlement_result": {"failure": None},
                }
            )
        else:
            payload = json.dumps(
                {
                    "ok": True,
                    "appended": True,
                    "settlement_identity": identity.as_dict(),
                    "settlement_result": {"failure": None},
                }
            )
        return subprocess.CompletedProcess(command, 0, payload, "")

    mock_goal_cli(capture)

    output = control.complete_task(
        "todo_222222222222", "claude", "focused check passed"
    )

    result = json.loads(output)
    assert result["ok"] is True
    assert result["settlement"]["ok"] is True
    should_run, complete, refresh, spend = commands[-4:]
    assert should_run[should_run.index("json") + 1 :] == [
        "quota",
        "should-run",
        "--goal-id",
        "shared-goal",
        "--agent-id",
        "claude",
        "--todo-id",
        "todo_222222222222",
        "--turn-instance-id",
        result["settlement_identity"]["turn_instance_id"],
        "--runtime-profile",
        "claude_code",
    ]
    assert complete[complete.index("--todo-id") + 1] == "todo_222222222222"
    assert complete[complete.index("--turn-instance-id") + 1] == (
        result["settlement_identity"]["turn_instance_id"]
    )
    assert refresh[refresh.index("json") + 1] == "refresh-state"
    assert refresh[refresh.index("--goal-id") + 1] == "shared-goal"
    assert refresh[refresh.index("--todo-id") + 1] == "todo_222222222222"
    assert refresh[refresh.index("--turn-instance-id") + 1] == (
        result["settlement_identity"]["turn_instance_id"]
    )
    assert refresh[refresh.index("--completion-todo-id") + 1] == (
        "todo_222222222222"
    )
    assert refresh[refresh.index("--completion-turn-key") + 1] == (
        result["settlement_identity"]["effect_id"]
    )
    assert spend[spend.index("json") + 1 : spend.index("json") + 3] == [
        "quota",
        "spend-slot",
    ]
    assert spend[spend.index("--todo-id") + 1] == "todo_222222222222"
    assert spend[spend.index("--source") + 1] == "heartbeat"
    assert spend[spend.index("--turn-instance-id") + 1] == (
        result["settlement_identity"]["turn_instance_id"]
    )


def test_complete_task_classifies_terminal_no_selection_and_fails_closed(
    mock_goal_cli,
) -> None:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": "shared-goal",
            "registry": "/project/.loopx/registry.json",
            "agent_id": "claude",
        },
    )
    commands: list[list[str]] = []
    control.command_prefix = lambda: ["loopx"]

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        payload = json.dumps(
            {
                "ok": True,
                "should_run": False,
                "effective_action": "terminal_no_followup",
                "selected_todo": None,
            }
        )
        return subprocess.CompletedProcess(command, 0, payload, "")

    mock_goal_cli(capture)

    output = control.complete_task(
        "todo_222222222222", "claude", "focused check passed"
    )

    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["settlement"]["guard_state"] == "terminal_no_selection"
    assert len(commands) == 1


def test_complete_task_fails_closed_on_unparseable_snapshot(
    mock_goal_cli,
) -> None:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": "shared-goal",
            "registry": "/project/.loopx/registry.json",
            "agent_id": "claude",
        },
    )
    commands: list[list[str]] = []
    control.command_prefix = lambda: ["loopx"]

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "not-json", "")

    mock_goal_cli(capture)

    output = control.complete_task(
        "todo_222222222222", "claude", "focused check passed"
    )

    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["settlement"]["guard_state"] == "invalid"
    assert len(commands) == 1


def test_installer_registers_only_the_owned_global_mcp_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli, "_compatible_python", lambda _python: True)
    monkeypatch.setattr(cli, "_configured_mcp_servers", lambda _binary: [])

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli, "_run", capture)

    selected = cli.install_mcp(
        python="/managed/.venv/bin/python",
        dry_run=False,
        replace=False,
    )

    assert selected == "/managed/.venv/bin/python"
    assert calls == [
        [
            "/usr/bin/kunluncode",
            "mcp",
            "add",
            "loopx-kunluncode",
            "--command",
            "/managed/.venv/bin/python",
            "--args",
            str(cli.MCP_SCRIPT),
        ]
    ]


def test_provisioner_installs_matching_distribution_outside_a_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    adapter_venv = tmp_path / "adapter" / ".venv"
    adapter_python = cli._venv_python(adapter_venv)
    monkeypatch.setattr(cli, "DEFAULT_MCP_VENV", adapter_venv)
    monkeypatch.setattr(cli, "_source_root", lambda: None)
    monkeypatch.setattr(cli.importlib.metadata, "version", lambda _name: "0.4.8")
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        cli,
        "_compatible_python",
        lambda value: Path(value) == adapter_python,
    )

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli, "_run", capture)

    selected = cli.provision_mcp_python()

    assert selected == str(adapter_python)
    install = next(command for command in calls if "install" in command)
    assert "loopx==0.4.8" in install
    assert "-e" not in install
    assert cli.MCP_REQUIREMENT in install


def test_installer_refuses_to_replace_a_foreign_named_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli, "_compatible_python", lambda _python: True)
    monkeypatch.setattr(
        cli,
        "_configured_mcp_servers",
        lambda _binary: [
            {
                "name": "loopx-kunluncode",
                "command": "/user/custom-server",
                "args": [],
            }
        ],
    )

    with pytest.raises(RuntimeError, match="user-owned"):
        cli.install_mcp(
            python="/managed/.venv/bin/python",
            dry_run=False,
            replace=False,
        )


def test_installer_restores_previous_entry_when_replacement_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    previous = {
        "name": "loopx-kunluncode",
        "command": "/user/custom-server",
        "args": ["--serve"],
    }
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli, "_compatible_python", lambda _python: True)
    monkeypatch.setattr(
        cli, "_configured_mcp_servers", lambda _binary: [previous]
    )

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["mcp", "add"] and "/managed/.venv/bin/python" in command:
            return subprocess.CompletedProcess(command, 1, "", "new entry rejected")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli, "_run", capture)

    with pytest.raises(RuntimeError, match="previous entry was restored"):
        cli.install_mcp(
            python="/managed/.venv/bin/python",
            dry_run=False,
            replace=True,
        )

    assert calls == [
        ["/usr/bin/kunluncode", "mcp", "remove", "loopx-kunluncode"],
        [
            "/usr/bin/kunluncode",
            "mcp",
            "add",
            "loopx-kunluncode",
            "--command",
            "/managed/.venv/bin/python",
            "--args",
            str(cli.MCP_SCRIPT),
        ],
        [
            "/usr/bin/kunluncode",
            "mcp",
            "add",
            "loopx-kunluncode",
            "--command",
            "/user/custom-server",
            "--args",
            "--serve",
        ],
    ]


def test_installer_refuses_unrestorable_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli, "_compatible_python", lambda _python: True)
    monkeypatch.setattr(
        cli,
        "_configured_mcp_servers",
        lambda _binary: [{"name": "loopx-kunluncode", "url": "https://example.test"}],
    )

    with pytest.raises(RuntimeError, match="cannot be safely replaced"):
        cli.install_mcp(
            python="/managed/.venv/bin/python",
            dry_run=False,
            replace=True,
        )


def test_installer_normalizes_relative_python_to_an_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    interpreter = tmp_path / ".venv" / "bin" / "python"
    calls: list[list[str]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli, "_compatible_python", lambda _python: True)
    monkeypatch.setattr(cli, "_configured_mcp_servers", lambda _binary: [])

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli, "_run", capture)

    selected = cli.install_mcp(
        python=".venv/bin/python",
        dry_run=False,
        replace=False,
    )

    assert selected == str(interpreter)
    assert str(interpreter) in calls[0]


def test_connect_preserves_existing_registered_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(cli, "_loopx_prefix", lambda: ["loopx"])

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"ok": true}', "")

    monkeypatch.setattr(cli, "_run", capture)

    result = cli.connect_project(
        project=tmp_path,
        goal_id="shared-goal",
        agent_id="kunlun-next",
        objective=None,
        python=None,
        skip_mcp=True,
        replace=False,
        dry_run=False,
    )

    configure = commands[0]
    registered = [
        configure[index + 1]
        for index, argument in enumerate(configure)
        if argument == "--registered-agent"
    ]
    assert registered == ["cc", "kunlun", "kunlun-next"]
    assert result["agent_id"] == "kunlun-next"
    assert json.loads(registry.read_text(encoding="utf-8"))["agent_backends"] == [
        "kunluncode"
    ]


def test_add_todo_assigns_without_impersonating_the_bound_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        cli,
        "goal_context",
        lambda _project: {
            "goal_id": "shared-goal",
            "agent_id": "kunlun",
            "registry": str(tmp_path / ".loopx" / "registry.json"),
        },
    )
    monkeypatch.setattr(cli, "_loopx_prefix", lambda: ["loopx"])

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        return subprocess.CompletedProcess(
            command,
            0,
            '{"ok": true, "todo_id": "todo-1"}',
            "",
        )

    monkeypatch.setattr(cli, "_run", capture)

    result = cli.add_todo(tmp_path, "Run the native protocol smoke")

    assert result["todo_id"] == "todo-1"
    assert captured[-2:] == ["--claimed-by", "kunlun"]
    assert "--agent-id" not in captured


def test_status_passes_one_typed_runtime_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        cli,
        "goal_context",
        lambda _project: {
            "goal_id": "shared-goal",
            "agent_id": "kunlun",
            "registry": str(tmp_path / ".loopx" / "registry.json"),
        },
    )
    monkeypatch.setattr(cli, "_loopx_prefix", lambda: ["loopx"])
    monkeypatch.setattr(cli, "read_runtime_state", lambda _project: None)

    def capture(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"ok": true}', "")

    monkeypatch.setattr(cli, "_run", capture)

    payload = cli.status(tmp_path)

    assert payload["ok"] is True
    assert commands[0].count("--runtime-profile") == 1
    profile_index = commands[0].index("--runtime-profile")
    assert commands[0][profile_index + 1] == "kunluncode"


def test_worker_prompt_closes_each_bounded_lifecycle_segment() -> None:
    prompt = cli.worker_prompt("kunlun")

    assert "should_run first" in prompt
    assert "claim exactly the selected todo" in prompt
    assert "complete_task" in prompt
    assert "no_follow_up=true" in prompt
    assert "never reuse the completed todo_id" in prompt
    assert "Re-check should_run once and stop" in prompt


def test_worker_prepends_the_adapter_environment_to_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "goal_context",
        lambda _project: {"goal_id": "g", "agent_id": "kunlun"},
    )
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/bin/kunluncode")

    def capture(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", capture)

    assert (
        cli.run_worker(
            tmp_path,
            permission_mode="auto",
            max_duration_secs=60,
            json_output=True,
            dry_run=False,
            mode="headless",
        )
        == 0
    )

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["PATH"].split(":", 1)[0] == str(
        Path(cli.sys.executable).absolute().parent
    )


def test_native_control_plane_scopes_recovery_and_writeback_to_todo(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def capture(
        command: list[str], **_kwargs
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            '{"ok": true, "appended": true, "ledger": []}',
            "",
        )

    control = LoopXControlPlane(
        tmp_path,
        {
            "registry": str(tmp_path / ".loopx" / "registry.json"),
            "goal_id": "shared-goal",
            "agent_id": "kunlun",
        },
        command_prefix=["loopx"],
        runner=capture,
    )

    control.evidence_since("2026-08-18T00:00:00Z", todo_id="todo-native-1")
    control.record_verified_delivery(
        mode="goal-pro", todo_id="todo-native-1"
    )
    control.spend(todo_id="todo-native-1")

    assert len(commands) == 3
    for command in commands:
        todo_index = command.index("--todo-id")
        assert command[todo_index + 1] == "todo-native-1"


class _FakeControlPlane:
    def __init__(self, todo: dict[str, object]) -> None:
        self.todo = todo
        self.calls: list[str] = []
        self.ledger: list[dict[str, object]] = []
        self.evidence_error = False

    def should_run(self) -> dict[str, object]:
        self.calls.append("should_run")
        return {"ok": True, "should_run": True, "selected_todo": self.todo}

    def claim(self, todo_id: str) -> dict[str, object]:
        self.calls.append(f"claim:{todo_id}")
        return {"ok": True, "todo_id": todo_id, "changed": True}

    def evidence_since(self, _since: str, *, todo_id: str) -> dict[str, object]:
        assert todo_id == str(self.todo["todo_id"])
        self.calls.append("evidence_since")
        if self.evidence_error:
            raise KunlunNativeGoalRuntimeError("evidence ledger unavailable")
        return {"ok": True, "ledger": list(self.ledger)}

    def record_verified_delivery(
        self, *, mode: str, todo_id: str
    ) -> dict[str, object]:
        assert todo_id == str(self.todo["todo_id"])
        self.calls.append(f"record:{mode}")
        return {
            "ok": True,
            "appended": True,
            "classification": f"kunluncode_native_{mode}_verified",
            "generated_at": "2026-08-05T00:00:01Z",
        }

    def complete(self, todo_id: str, *, evidence: str) -> dict[str, object]:
        assert "status=complete" in evidence
        self.calls.append(f"complete:{todo_id}")
        return {
            "ok": True,
            "completed": True,
            "todo_id": todo_id,
            "status": "done",
            "status_changed": True,
            "changed": True,
        }

    def spend(self, *, todo_id: str) -> dict[str, object]:
        assert todo_id == str(self.todo["todo_id"])
        self.calls.append("spend")
        return {
            "ok": True,
            "appended": True,
            "classification": "quota_slot_spent",
            "generated_at": "2026-08-05T00:00:02Z",
            "slots": 1,
        }


class _FakeAppServer:
    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        event_handler,
        terminal_goal: dict[str, object] | None = None,
        resumed_goal: dict[str, object] | None = None,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.event_handler = event_handler
        self.terminal_goal = terminal_goal
        self.resumed_goal = resumed_goal
        self.calls: list[str] = []
        self.objective = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def initialize(self) -> dict[str, object]:
        self.calls.append("initialize")
        return {"protocolVersion": "2026-07-27"}

    def start_thread(self, _project: Path) -> str:
        self.calls.append("start_thread")
        return "thread-1"

    def resume_thread(self, thread_id: str) -> dict[str, object]:
        self.calls.append(f"resume:{thread_id}")
        return {"thread": {"id": thread_id}}

    def set_goal(
        self,
        _thread_id: str,
        *,
        objective: str,
        mode: str,
        token_budget: int | None = None,
    ) -> dict[str, object]:
        del token_budget
        self.calls.append(f"set_goal:{mode}")
        self.objective = objective
        return {
            "goalId": "native-goal-1",
            "revision": 0,
            "mode": NATIVE_GOAL_MODES[mode],
            "verificationKind": "artifact",
            "status": "active",
            "objective": objective,
            "events": [],
        }

    def get_goal(self, _thread_id: str) -> dict[str, object]:
        self.calls.append("get_goal")
        if self.resumed_goal is None:
            raise AssertionError("unexpected get_goal")
        self.objective = str(self.resumed_goal.get("objective") or "")
        return dict(self.resumed_goal)

    def start_turn(self, _thread_id: str, _prompt: str) -> dict[str, object]:
        self.calls.append("start_turn")
        self.event_handler({"method": "turn/started", "params": {}})
        return {"turn": {"id": "turn-1", "status": "running"}}

    def wait_for_goal_terminal(
        self,
        _thread_id: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        assert timeout_seconds > 0
        self.calls.append("wait_terminal")
        self.event_handler({"method": "turn/completed", "params": {}})
        if self.terminal_goal is not None:
            goal = dict(self.terminal_goal)
            goal.setdefault("objective", self.objective)
            return goal
        return {
            "goalId": "native-goal-1",
            "revision": 2,
            "mode": "strict",
            "verificationKind": "artifact",
            "status": "complete",
            "objective": self.objective,
            "events": [
                {"kind": "verification_passed"},
                {"kind": "complete"},
            ],
        }


def _native_context(tmp_path: Path) -> dict[str, str]:
    return {
        "goal_id": "shared-goal",
        "agent_id": "kunlun",
        "registry": str(tmp_path / ".loopx" / "registry.json"),
    }


def _write_source_native_registry(tmp_path: Path, instance_id: str) -> Path:
    registry = tmp_path / ".loopx" / "registry.json"
    payload = {
        "schema_version": "0.2",
        "registry_role": "project-local",
        "profile_id": "source_session_v1",
        "common_runtime_root": str(tmp_path / "runtime"),
        "projects": [],
        "goals": [
            {
                "id": "shared-goal",
                "goal_instance_id": instance_id,
                "status": "active",
                "execution_authority": False,
            }
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
        "retired_goal_instances": [],
    }
    create = None if registry.exists() else lambda: payload
    with source_session_registry_transaction(
        registry,
        operation="kunlun_host_goal_instance_test",
        create=create,
    ) as transaction:
        current = transaction.payload_copy()
        current["goals"] = payload["goals"]
        transaction.commit(current)
    return registry


def _replace_source_native_goal(tmp_path: Path, instance_id: str) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    with exclusive_cross_runtime_file_lock(
        guard_path(registry, "shared-goal"),
        operation="kunlun_host_goal_instance_test_recreate",
    ):
        _write_source_native_registry(tmp_path, instance_id)


def _native_todo(*, claimed: bool = False) -> dict[str, object]:
    return {
        "todo_id": "todo-native-1",
        "text": "Implement and verify the native Goal Pro adapter.",
        "claimed_by": "kunlun" if claimed else "",
        "required_write_scopes": ["loopx/kunluncode_goal_mode/**"],
    }


def _native_state(
    todo: dict[str, object],
    *,
    thread_id: str = "thread-1",
    verified: bool = False,
) -> dict[str, object]:
    objective = build_native_goal_objective(todo, agent_id="kunlun")
    return {
        "schema_version": RUNTIME_STATE_SCHEMA_VERSION,
        "created_at": "2026-08-05T00:00:00Z",
        "updated_at": "2026-08-05T00:00:00Z",
        "binding": {"goal_id": "shared-goal", "agent_id": "kunlun"},
        "todo_id": "todo-native-1",
        "native": {
            "mode": "goal-pro",
            "objective_sha256": stable_text_digest(objective),
            "thread_id": thread_id,
            "goal_id": "native-goal-1",
            "revision": 2 if verified else 0,
            "status": "complete" if verified else "active",
            "verification_kind": "artifact",
            "verification_passed": verified,
            "verified": verified,
            "verified_at": "2026-08-05T00:00:00Z" if verified else None,
            "resumed": False,
            "event_counts": {},
        },
        "writeback": {
            "delivery_recorded": False,
            "todo_completed": False,
            "quota_spent": False,
        },
    }


def test_runtime_journal_rejects_illegal_writeback_order(tmp_path: Path) -> None:
    state = _native_state(_native_todo(), verified=True)
    state["writeback"]["quota_spent"] = True

    with pytest.raises(KunlunNativeGoalRuntimeError, match="before todo completion"):
        write_runtime_state(tmp_path, state)


def test_runtime_journal_rejects_unsupported_schema_before_write(
    tmp_path: Path,
) -> None:
    state = _native_state(_native_todo())
    state["schema_version"] = "future-schema"

    with pytest.raises(KunlunNativeGoalRuntimeError, match="unsupported schema"):
        write_runtime_state(tmp_path, state)

    assert not (tmp_path / ".loopx" / "kunluncode-runtime.json").exists()


def test_runtime_journal_rejects_goal_pro_without_verifier_proof(
    tmp_path: Path,
) -> None:
    state = _native_state(_native_todo(), verified=True)
    state["native"]["verification_passed"] = False

    with pytest.raises(KunlunNativeGoalRuntimeError, match="without verifier proof"):
        write_runtime_state(tmp_path, state)


def test_runtime_journal_rejects_incomplete_typed_state(tmp_path: Path) -> None:
    journal = tmp_path / ".loopx" / "kunluncode-runtime.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        json.dumps({"schema_version": RUNTIME_STATE_SCHEMA_VERSION}),
        encoding="utf-8",
    )

    with pytest.raises(KunlunNativeGoalRuntimeError, match="binding object"):
        read_runtime_state(tmp_path)


def test_strict_native_goal_requires_verification_passed_event() -> None:
    accepted = {
        "status": "complete",
        "mode": "strict",
        "events": [{"kind": "verification_passed"}, {"kind": "complete"}],
    }
    rejected = {
        "status": "complete",
        "mode": "strict",
        "events": [{"kind": "complete"}],
    }

    assert native_goal_success(accepted, mode="goal-pro") is True
    assert native_goal_success(rejected, mode="goal-pro") is False
    assert (
        native_goal_success(
            {"status": "complete", "mode": "arrangement", "events": []},
            mode="goal",
        )
        is True
    )


def test_native_controller_blocks_model_visible_mcp_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, control = create_fastmcp_server(
        GoalModeMCPConfig(
            server_name="loopx-kunluncode-test",
            runtime_profile="kunluncode",
            legacy_host_surface="kunluncode",
        ),
        lambda: {
            "goal_id": "shared-goal",
            "registry": "/project/.loopx/registry.json",
            "agent_id": "kunlun",
        },
    )
    monkeypatch.setenv(NATIVE_CONTROLLER_ENV, "1")

    guard_native_controller_writeback(control)

    claim_content, _ = asyncio.run(
        server.call_tool(
            "claim_task",
            {"todo_id": "todo-1", "agent_id": "kunlun"},
        )
    )
    complete_content, _ = asyncio.run(
        server.call_tool(
            "complete_task",
            {
                "todo_id": "todo-1",
                "agent_id": "kunlun",
                "evidence": "premature evidence",
                "task_lease_idempotency_key": "lease-fixture",
                "task_lease_expected_version": 7,
            },
        )
    )

    assert json.loads(claim_content[0].text)["ok"] is False
    assert json.loads(complete_content[0].text)["ok"] is False


def test_native_controller_blocks_direct_cli_write_to_the_bound_goal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(NATIVE_CONTROLLER_ENV, "1")
    monkeypatch.setenv(NATIVE_CONTROLLER_GOAL_ENV, "shared-goal")
    monkeypatch.setenv(NATIVE_CONTROLLER_AGENT_ENV, "kunlun")

    exit_code = loopx_cli.main(
        [
            "--format",
            "json",
            "todo",
            "complete",
            "--goal-id",
            "shared-goal",
            "--todo-id",
            "todo-1",
            "--claimed-by",
            "kunlun",
            "--agent-id",
            "kunlun",
            "--evidence",
            "premature evidence",
            "--no-follow-up",
        ]
    )

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 2
    assert payload["error_code"] == "kunluncode_outer_controller_write_owned"
    assert payload["operation"] == "todo complete"
    assert payload["goal_id"] == "shared-goal"


def test_native_controller_cli_guard_preserves_reads_and_other_goals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NATIVE_CONTROLLER_ENV, "1")
    monkeypatch.setenv(NATIVE_CONTROLLER_GOAL_ENV, "shared-goal")
    monkeypatch.setenv(NATIVE_CONTROLLER_AGENT_ENV, "kunlun")

    assert (
        native_controller_cli_write_block(
            SimpleNamespace(
                command="quota",
                quota_command="should-run",
                goal_id="shared-goal",
            )
        )
        is None
    )
    assert (
        native_controller_cli_write_block(
            SimpleNamespace(
                command="todo",
                todo_command="complete",
                goal_id="another-goal",
            )
        )
        is None
    )
    retirement = native_controller_cli_write_block(
        SimpleNamespace(
            command="retire-global-goal",
            goal_id=["shared-goal"],
        )
    )
    assert retirement is not None
    assert retirement["operation"] == "retire-global-goal"


def test_native_goal_pro_commits_only_after_verified_terminal_state(
    tmp_path: Path,
) -> None:
    control = _FakeControlPlane(_native_todo())
    clients: list[_FakeAppServer] = []

    def factory(command: list[str], **kwargs) -> _FakeAppServer:
        client = _FakeAppServer(command, **kwargs)
        clients.append(client)
        return client

    result = run_native_goal(
        tmp_path,
        _native_context(tmp_path),
        mode="goal-pro",
        permission_mode="auto",
        max_duration_secs=60,
        controller_timeout_secs=120,
        kunluncode_bin="/usr/bin/kunluncode",
        control_plane=control,
        client_factory=factory,
    )

    assert result["status"] == "committed"
    assert control.calls == [
        "should_run",
        "claim:todo-native-1",
        "evidence_since",
        "record:goal-pro",
        "complete:todo-native-1",
        "spend",
    ]
    assert clients[0].calls == [
        "initialize",
        "start_thread",
        "set_goal:goal-pro",
        "start_turn",
        "wait_terminal",
    ]
    assert clients[0].env[NATIVE_CONTROLLER_ENV] == "1"
    assert clients[0].env[NATIVE_CONTROLLER_GOAL_ENV] == "shared-goal"
    assert clients[0].env[NATIVE_CONTROLLER_AGENT_ENV] == "kunlun"
    state = read_runtime_state(tmp_path)
    assert state is not None
    assert state["native"]["verified"] is True
    assert state["writeback"]["quota_spent"] is True
    journal = (tmp_path / ".loopx" / "kunluncode-runtime.json").read_text()
    assert "Implement and verify the native Goal Pro adapter" not in journal


def test_source_native_goal_rejects_alias_only_runtime_state(
    tmp_path: Path,
) -> None:
    _write_source_native_registry(
        tmp_path,
        "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    state = _native_state(_native_todo(claimed=True))
    write_runtime_state(tmp_path, state)
    before = runtime_state_path(tmp_path).read_bytes()
    control = _FakeControlPlane(_native_todo(claimed=True))

    with pytest.raises(FirstPartyHostRuntimeRejected) as exc_info:
        run_native_goal(
            tmp_path,
            _native_context(tmp_path),
            mode="goal-pro",
            permission_mode="auto",
            max_duration_secs=60,
            controller_timeout_secs=120,
            kunluncode_bin="/usr/bin/kunluncode",
            control_plane=control,
            client_factory=lambda *_args, **_kwargs: pytest.fail(
                "legacy runtime state must not launch KunlunCode"
            ),
        )

    assert exc_info.value.code == "legacy_host_state"
    assert control.calls == []
    assert runtime_state_path(tmp_path).read_bytes() == before


def test_source_native_goal_rejects_result_after_recreation(
    tmp_path: Path,
) -> None:
    instance_a = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    instance_b = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    _write_source_native_registry(tmp_path, instance_a)
    control = _FakeControlPlane(_native_todo())

    class RecreatingAppServer(_FakeAppServer):
        def wait_for_goal_terminal(
            self,
            thread_id: str,
            *,
            timeout_seconds: float,
        ) -> dict[str, object]:
            result = super().wait_for_goal_terminal(
                thread_id,
                timeout_seconds=timeout_seconds,
            )
            _replace_source_native_goal(tmp_path, instance_b)
            return result

    with pytest.raises(FirstPartyHostRuntimeRejected) as exc_info:
        run_native_goal(
            tmp_path,
            _native_context(tmp_path),
            mode="goal-pro",
            permission_mode="auto",
            max_duration_secs=60,
            controller_timeout_secs=120,
            kunluncode_bin="/usr/bin/kunluncode",
            control_plane=control,
            client_factory=lambda command, **kwargs: RecreatingAppServer(
                command,
                **kwargs,
            ),
        )

    assert exc_info.value.code == "stale_goal_instance"
    assert control.calls == ["should_run", "claim:todo-native-1"]
    state = read_runtime_state(tmp_path)
    assert state is not None
    assert state["binding"]["goal_instance_id"] == instance_a
    assert state["native"]["verified"] is False
    assert state["writeback"] == {
        "delivery_recorded": False,
        "todo_completed": False,
        "quota_spent": False,
    }


def test_native_goal_reconciles_crash_after_writeback_without_repeating_it(
    tmp_path: Path,
) -> None:
    todo = _native_todo(claimed=True)
    control = _FakeControlPlane(todo)
    state = _native_state(todo, verified=True)
    write_runtime_state(tmp_path, state)
    control.ledger = [
        {
            "event_kind": "refresh_state",
            "classification": "kunluncode_native_goal_pro_verified",
            "todo_id": "todo-native-1",
        },
        {"event_kind": "todo_complete", "todo_id": "todo-native-1"},
        {"event_kind": "quota_spend", "todo_id": "todo-native-1"},
    ]

    def forbidden_factory(*_args, **_kwargs):
        raise AssertionError("verified recovery must not relaunch KunlunCode")

    result = run_native_goal(
        tmp_path,
        _native_context(tmp_path),
        mode="goal-pro",
        permission_mode="auto",
        max_duration_secs=60,
        controller_timeout_secs=120,
        kunluncode_bin="/usr/bin/kunluncode",
        control_plane=control,
        client_factory=forbidden_factory,
    )

    assert result["recovered"] is True
    assert control.calls == ["evidence_since"]
    recovered = read_runtime_state(tmp_path)
    assert recovered is not None
    assert recovered["writeback"]["delivery_recorded"] is True
    assert recovered["writeback"]["todo_completed"] is True
    assert recovered["writeback"]["quota_spent"] is True


def test_verified_recovery_ignores_writeback_evidence_for_another_todo(
    tmp_path: Path,
) -> None:
    todo = _native_todo(claimed=True)
    control = _FakeControlPlane(todo)
    write_runtime_state(tmp_path, _native_state(todo, verified=True))
    control.ledger = [
        {
            "event_kind": "refresh_state",
            "classification": "kunluncode_native_goal_pro_verified",
            "todo_id": "other-todo",
        },
        {"event_kind": "todo_complete", "todo_id": "other-todo"},
        {"event_kind": "quota_spend", "todo_id": "other-todo"},
    ]

    result = run_native_goal(
        tmp_path,
        _native_context(tmp_path),
        mode="goal-pro",
        permission_mode="auto",
        max_duration_secs=60,
        controller_timeout_secs=120,
        kunluncode_bin="/usr/bin/kunluncode",
        control_plane=control,
        client_factory=lambda *_args, **_kwargs: pytest.fail(
            "verified recovery must not relaunch KunlunCode"
        ),
    )

    assert result["recovered"] is True
    assert control.calls == [
        "evidence_since",
        "record:goal-pro",
        "complete:todo-native-1",
        "spend",
    ]


def test_verified_recovery_does_not_repeat_writeback_without_ledger_evidence(
    tmp_path: Path,
) -> None:
    todo = _native_todo(claimed=True)
    control = _FakeControlPlane(todo)
    control.evidence_error = True
    write_runtime_state(tmp_path, _native_state(todo, verified=True))

    with pytest.raises(KunlunNativeGoalRuntimeError, match="ledger unavailable"):
        run_native_goal(
            tmp_path,
            _native_context(tmp_path),
            mode="goal-pro",
            permission_mode="auto",
            max_duration_secs=60,
            controller_timeout_secs=120,
            kunluncode_bin="/usr/bin/kunluncode",
            control_plane=control,
            client_factory=lambda *_args, **_kwargs: pytest.fail(
                "verified recovery must not relaunch KunlunCode"
            ),
        )

    assert control.calls == ["evidence_since"]


def test_native_goal_resumes_the_same_thread_and_goal_after_interruption(
    tmp_path: Path,
) -> None:
    todo = _native_todo(claimed=True)
    control = _FakeControlPlane(todo)
    objective = build_native_goal_objective(todo, agent_id="kunlun")
    state = _native_state(todo, thread_id="thread-existing")
    write_runtime_state(tmp_path, state)
    clients: list[_FakeAppServer] = []

    def factory(command: list[str], **kwargs) -> _FakeAppServer:
        client = _FakeAppServer(
            command,
            **kwargs,
            resumed_goal={
                "goalId": "native-goal-1",
                "revision": 0,
                "mode": "strict",
                "verificationKind": "artifact",
                "status": "active",
                "objective": objective,
                "events": [],
            },
        )
        clients.append(client)
        return client

    result = run_native_goal(
        tmp_path,
        _native_context(tmp_path),
        mode="goal-pro",
        permission_mode="auto",
        max_duration_secs=60,
        controller_timeout_secs=120,
        kunluncode_bin="/usr/bin/kunluncode",
        control_plane=control,
        client_factory=factory,
    )

    assert result["status"] == "committed"
    assert result["recovered"] is True
    assert clients[0].calls[:3] == [
        "initialize",
        "resume:thread-existing",
        "get_goal",
    ]
    assert "start_thread" not in clients[0].calls
    assert "set_goal:goal-pro" not in clients[0].calls


def test_native_goal_does_not_duplicate_work_when_recorded_thread_cannot_resume(
    tmp_path: Path,
) -> None:
    todo = _native_todo(claimed=True)
    control = _FakeControlPlane(todo)
    write_runtime_state(tmp_path, _native_state(todo, thread_id="thread-existing"))
    clients: list[_FakeAppServer] = []

    class UnavailableThreadAppServer(_FakeAppServer):
        def resume_thread(self, thread_id: str) -> dict[str, object]:
            self.calls.append(f"resume:{thread_id}")
            raise KunlunAppServerError("recorded thread is unavailable")

    def factory(command: list[str], **kwargs) -> _FakeAppServer:
        client = UnavailableThreadAppServer(command, **kwargs)
        clients.append(client)
        return client

    with pytest.raises(KunlunAppServerError, match="recorded thread is unavailable"):
        run_native_goal(
            tmp_path,
            _native_context(tmp_path),
            mode="goal-pro",
            permission_mode="auto",
            max_duration_secs=60,
            controller_timeout_secs=120,
            kunluncode_bin="/usr/bin/kunluncode",
            control_plane=control,
            client_factory=factory,
        )

    assert clients[0].calls == ["initialize", "resume:thread-existing"]
    assert not any(call.startswith("record:") for call in control.calls)
    assert not any(call.startswith("complete:") for call in control.calls)
    assert "spend" not in control.calls


def test_native_goal_pro_rejects_complete_without_verifier_pass(
    tmp_path: Path,
) -> None:
    control = _FakeControlPlane(_native_todo(claimed=True))
    clients: list[_FakeAppServer] = []

    def factory(command: list[str], **kwargs) -> _FakeAppServer:
        client = _FakeAppServer(
            command,
            **kwargs,
            terminal_goal={
                "goalId": "native-goal-1",
                "revision": 1,
                "mode": "strict",
                "verificationKind": "artifact",
                "status": "complete",
                "events": [{"kind": "complete"}],
            },
        )
        clients.append(client)
        return client

    with pytest.raises(KunlunNativeGoalRuntimeError, match="accepted terminal"):
        run_native_goal(
            tmp_path,
            _native_context(tmp_path),
            mode="goal-pro",
            permission_mode="auto",
            max_duration_secs=60,
            controller_timeout_secs=120,
            kunluncode_bin="/usr/bin/kunluncode",
            control_plane=control,
            client_factory=factory,
        )

    assert "record:goal-pro" not in control.calls
    assert "complete:todo-native-1" not in control.calls
    assert "spend" not in control.calls
    state = read_runtime_state(tmp_path)
    assert state is not None
    assert state["native"]["status"] == "complete"
    assert state["native"]["verified"] is False


def test_mcp_pin_has_one_source_of_truth() -> None:
    """Refs #4447: the `mcp` pin is defined once and derived everywhere else.

    The pin is deliberately exact, not a range: the server imports
    `mcp.server.fastmcp`, which the MCP SDK 2.x line no longer ships, and the
    pin was set by a security fix. Before this change the version string was
    repeated in the requirement, in the compatibility probe and in the
    user-facing error, so a bump could leave a stale probe behind.
    """
    probe_source = inspect.getsource(cli._compatible_python)

    assert cli.MCP_SDK_VERSION
    assert cli.MCP_REQUIREMENT == f"mcp=={cli.MCP_SDK_VERSION}"
    assert cli.MCP_REQUIREMENT.startswith("mcp==")
    assert cli.MCP_SDK_VERSION not in probe_source
    assert "MCP_SDK_VERSION" in probe_source
