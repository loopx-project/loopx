"""The opt-in Kiro CLI `loopx` agent: its preToolUse hook is an enforced gate.

The host contract these tests rely on was checked against Kiro CLI 2.24.1:
only exit status 2 blocks a tool call (stderr reaches the model), any other
failure lets it run, and a hook that outlives `timeout_ms` is abandoned and
the tool runs. So the tests pin exit statuses, not just verdicts, and drive
the installed script as a real subprocess against a real registry and CLI.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.goal_mode_tool_policy import (
    ToolCall,
    ToolKind,
    Verdict,
    decide_tool_call,
)
from loopx.host_loop_activation import build_host_loop_activation_packet
from loopx.kiro_cli_goal_mode import (
    KIRO_CLI_GATED_AGENT_LAUNCH,
    KIRO_CLI_HOOK_PROBE_TIMEOUT_SECONDS,
    KIRO_CLI_HOOK_TIMEOUT_MS,
)
from loopx.kiro_cli_goal_mode import pretooluse_hook
from loopx.kiro_cli_goal_mode.gated_agent import (
    GATED_AGENT_MARKER,
    gated_agent_path,
    hook_script,
)
from loopx.slash_command_install import install_slash_commands

sys.path.insert(0, str(Path(__file__).resolve().parent / "control_plane"))
import test_quota_settlement_cli as settlement_fixture  # noqa: E402

SESSION_ID = "6a1f9d7e-2b3c-4d5e-8f90-a1b2c3d4e5f6"


# --- host-neutral rule ------------------------------------------------------


def _never_probed() -> bool | None:
    raise AssertionError("read-only tools must not consult the gate")


def test_read_only_is_allowed_without_consulting_the_gate() -> None:
    decision = decide_tool_call(
        ToolCall(ToolKind.READ_ONLY), goal_id="g", write_scope=["/p"], should_run=_never_probed
    )
    assert decision.verdict is Verdict.ALLOW


@pytest.mark.parametrize("gate", (False, None))
@pytest.mark.parametrize(
    "call",
    (
        ToolCall(ToolKind.FILE_WRITE, write_path="/p/a.txt"),
        ToolCall(ToolKind.SHELL, command="make test"),
        ToolCall(ToolKind.OTHER),
    ),
)
def test_closed_or_unknown_gate_denies_every_state_changing_call(
    call: ToolCall, gate: bool | None
) -> None:
    decision = decide_tool_call(call, goal_id="g", write_scope=["/p"], should_run=lambda: gate)
    assert decision.verdict is Verdict.DENY
    assert ("should_run=false" if gate is False else "failing closed") in decision.reason


@pytest.mark.parametrize(
    ("call", "expected"),
    (
        (ToolCall(ToolKind.FILE_WRITE, write_path="/p/a.txt"), Verdict.ALLOW),
        # Relative paths resolve against the host's cwd, not the hook's.
        (ToolCall(ToolKind.FILE_WRITE, write_path="src/a.txt"), Verdict.ALLOW),
        (ToolCall(ToolKind.FILE_WRITE, write_path="../outside.txt"), Verdict.DENY),
        (ToolCall(ToolKind.FILE_WRITE, write_path="/etc/passwd"), Verdict.DENY),
        (ToolCall(ToolKind.SHELL, command="make test"), Verdict.ALLOW),
        (ToolCall(ToolKind.SHELL, command="rm -rf /"), Verdict.DENY),
        (ToolCall(ToolKind.OTHER), Verdict.DEFER),
    ),
)
def test_open_gate_scopes_writes_screens_shell_and_defers_the_rest(
    call: ToolCall, expected: Verdict
) -> None:
    decision = decide_tool_call(
        call, goal_id="g", write_scope=["/p"], should_run=lambda: True, cwd="/p"
    )
    assert decision.verdict is expected


# --- Kiro mapping and exit-code contract -------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "kind"),
    (
        ("read", {"operations": []}, ToolKind.READ_ONLY),
        ("fs_read", {}, ToolKind.READ_ONLY),
        ("grep", {}, ToolKind.READ_ONLY),
        # Ending the host's own goal loop stays possible while closed.
        ("goal", {}, ToolKind.READ_ONLY),
        ("@loopx/should_run", {}, ToolKind.READ_ONLY),
        ("write", {"path": "a.txt"}, ToolKind.FILE_WRITE),
        ("fs_write", {"path": "a.txt"}, ToolKind.FILE_WRITE),
        ("shell", {"command": "ls"}, ToolKind.SHELL),
        ("execute_bash", {"command": "ls"}, ToolKind.SHELL),
        # Unlisted tools are gated, including LoopX's own writing tools.
        ("@loopx/complete_task", {}, ToolKind.OTHER),
        ("code", {}, ToolKind.OTHER),
        ("subagent", {}, ToolKind.OTHER),
        ("aws", {}, ToolKind.OTHER),
        ("@someserver/anything", {}, ToolKind.OTHER),
    ),
)
def test_kiro_tool_names_map_to_typed_kinds(
    tool_name: str, tool_input: dict[str, object], kind: ToolKind
) -> None:
    call = pretooluse_hook.tool_call(tool_name, tool_input)
    assert call.kind is kind
    if kind is ToolKind.FILE_WRITE:
        assert call.write_path == "a.txt"


def _event(tool_name: str, **tool_input: object) -> str:
    return json.dumps(
        {
            "hook_event_name": "preToolUse",
            "cwd": "/p",
            "session_id": SESSION_ID,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


def test_malformed_event_fails_closed_with_exit_2() -> None:
    for stdin in ("", "not json", "[1, 2]"):
        status, message = pretooluse_hook.run(stdin)
        assert status == 2
        assert "failing closed" in message


def test_gate_fault_blocks_state_changes_but_not_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(pretooluse_hook, "decide", broken)
    assert pretooluse_hook.run(_event("read", operations=[])) == (0, "")
    status, message = pretooluse_hook.run(_event("shell", command="ls"))
    assert status == 2
    assert "RuntimeError" in message


# --- the installed script, end to end --------------------------------------


def _bound_project(root: Path, *, gate_open: bool) -> Path:
    project, _runtime, registry = settlement_fixture._write_fixture(root)
    if gate_open:
        settlement_fixture._configure_read_only_todo(project)
    payload = json.loads(registry.read_text(encoding="utf-8"))
    coordination = payload["goals"][0]["coordination"]
    coordination["thread_agent_bindings"] = [
        {
            "thread_id": SESSION_ID,
            "host_surface": "kiro-cli",
            "agent_id": settlement_fixture.AGENT_ID,
        }
    ]
    if not gate_open:
        payload["goals"][0]["quota"]["allowed_slots"] = 0
    registry.write_text(json.dumps(payload), encoding="utf-8")
    return project


def _run_hook(project: Path, tool_name: str, session_id: str = SESSION_ID, **tool_input: object) -> subprocess.CompletedProcess[str]:
    event = {
        "hook_event_name": "preToolUse",
        "cwd": str(project),
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    return subprocess.run(
        [sys.executable, str(hook_script())],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        cwd="/",
        timeout=KIRO_CLI_HOOK_TIMEOUT_MS / 1000,
    )


def test_installed_hook_enforces_a_real_open_gate(tmp_path: Path) -> None:
    project = _bound_project(tmp_path, gate_open=True)
    cases = (
        ("write", {"path": "notes/a.txt"}, 0),
        ("write", {"path": "/etc/loopx-probe"}, 2),
        ("shell", {"command": "make test"}, 0),
        ("shell", {"command": "rm -rf /"}, 2),
        ("read", {"operations": []}, 0),
    )
    for tool_name, tool_input, expected in cases:
        result = _run_hook(project, tool_name, **tool_input)
        assert result.returncode == expected, (tool_name, tool_input, result.stderr)
        if expected == 2:
            assert result.stderr.startswith("LoopX gate:"), result.stderr


def test_installed_hook_enforces_a_real_closed_gate(tmp_path: Path) -> None:
    project = _bound_project(tmp_path, gate_open=False)
    result = _run_hook(project, "shell", command="make test")
    assert result.returncode == 2, result.stderr
    assert "should_run=false" in result.stderr
    # Reads, the host goal tool and the LoopX read path stay usable.
    for tool_name in ("read", "goal", "@loopx/should_run"):
        assert _run_hook(project, tool_name).returncode == 0


def test_unbound_session_is_not_gated(tmp_path: Path) -> None:
    """Before `/loopx` binds the session (for example while start-goal itself
    runs), the gate must stay out of the way instead of blocking setup."""
    project = _bound_project(tmp_path, gate_open=False)
    result = _run_hook(project, "shell", session_id="unbound-session", command="loopx start-goal")
    assert result.returncode == 0, result.stderr


# --- installer ---------------------------------------------------------------


def _row(payload: dict[str, object], mechanism: str) -> dict[str, object]:
    rows = [r for r in payload["installed"] if r["mechanism"] == mechanism]  # type: ignore[union-attr,index]
    assert len(rows) == 1, rows
    return rows[0]  # type: ignore[return-value]


def test_gated_agent_install_refresh_and_uninstall(tmp_path: Path) -> None:
    kiro_home = tmp_path / "kiro-home"
    agent = gated_agent_path(kiro_home)

    preview = install_slash_commands(
        execute=False, with_gated_agent=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _row(preview, "kiro_cli_gated_agent")["status"] == "would_write"
    assert not agent.exists()

    payload = install_slash_commands(
        execute=True, with_gated_agent=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    row = _row(payload, "kiro_cli_gated_agent")
    assert row["status"] == "written"
    assert row["invoke_as"] == [KIRO_CLI_GATED_AGENT_LAUNCH]
    config = json.loads(agent.read_text(encoding="utf-8"))
    assert config["name"] == "loopx"
    assert GATED_AGENT_MARKER in config["description"]
    assert config["includeMcpJson"] is True
    (entry,) = config["hooks"]["preToolUse"]
    assert entry["matcher"] == "*"
    assert str(hook_script()) in entry["command"]
    # The host abandons a hook past timeout_ms and runs the tool, so the probe
    # deadline must leave room inside it.
    assert entry["timeout_ms"] == KIRO_CLI_HOOK_TIMEOUT_MS
    assert KIRO_CLI_HOOK_PROBE_TIMEOUT_SECONDS * 1000 < KIRO_CLI_HOOK_TIMEOUT_MS

    again = install_slash_commands(
        execute=True, with_gated_agent=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _row(again, "kiro_cli_gated_agent")["status"] == "unchanged"

    # Uninstalling the surface retires the agent even without the flag: an
    # agent left pointing at a removed hook would run every tool ungated.
    removed = install_slash_commands(
        execute=True, uninstall=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    assert _row(removed, "kiro_cli_gated_agent")["status"] == "retired"
    assert not agent.exists()


def test_default_install_does_not_create_the_agent(tmp_path: Path) -> None:
    kiro_home = tmp_path / "kiro-home"
    payload = install_slash_commands(execute=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home))
    assert not any(r["mechanism"] == "kiro_cli_gated_agent" for r in payload["installed"])
    assert not gated_agent_path(kiro_home).exists()


def test_user_owned_loopx_agent_is_never_replaced_or_removed(tmp_path: Path) -> None:
    kiro_home = tmp_path / "kiro-home"
    agent = gated_agent_path(kiro_home)
    agent.parent.mkdir(parents=True)
    mine = '{"name": "loopx", "description": "my own agent"}\n'
    agent.write_text(mine, encoding="utf-8")
    for uninstall in (False, True):
        payload = install_slash_commands(
            execute=True,
            uninstall=uninstall,
            with_gated_agent=True,
            surfaces=["kiro-cli"],
            kiro_home=str(kiro_home),
        )
        assert _row(payload, "kiro_cli_gated_agent")["status"] == "skipped_user_owned_agent"
        assert agent.read_text(encoding="utf-8") == mine


def test_flag_without_the_kiro_surface_is_reported_not_ignored(tmp_path: Path) -> None:
    payload = install_slash_commands(
        execute=True,
        with_gated_agent=True,
        surfaces=["claude-code"],
        claude_home=str(tmp_path / "claude"),
        kiro_home=str(tmp_path / "kiro-home"),
    )
    row = _row(payload, "kiro_cli_gated_agent")
    assert row["status"] == "blocked_gated_agent_requires_kiro_cli_surface"
    assert not gated_agent_path(tmp_path / "kiro-home").exists()


@pytest.mark.skipif(shutil.which("kiro-cli") is None, reason="Kiro CLI not installed")
def test_generated_agent_passes_the_hosts_own_validator(tmp_path: Path) -> None:
    kiro_home = tmp_path / "kiro-home"
    install_slash_commands(
        execute=True, with_gated_agent=True, surfaces=["kiro-cli"], kiro_home=str(kiro_home)
    )
    result = subprocess.run(
        ["kiro-cli", "agent", "validate", "--path", str(gated_agent_path(kiro_home))],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_activation_packet_states_both_the_advisory_and_the_enforced_path() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="kiro-cli",
        goal_id="surface-goal",
        agent_id="probe-agent",
        registered_agents=["probe-agent"],
    )
    mutation = packet["host_mutation"]
    assert mutation["quota_gate_enforcement"] == "advisory_only"
    gate = mutation["opt_in_enforced_gate"]
    assert gate["launch_command"] == KIRO_CLI_GATED_AGENT_LAUNCH
    assert "--with-gated-agent" in gate["setup_command"]
    assert gate["failure_mode"] == "fail_closed"
    steps = " ".join(packet["activation_steps"])
    assert KIRO_CLI_GATED_AGENT_LAUNCH in steps
