"""Onboarding and heartbeat packets must print commands the host can run."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import loopx.project_prompt as project_prompt
from loopx import install_contract
from loopx.agent_onboarding import build_agent_onboarding_packet
from loopx.control_plane.heartbeat.task_body import (
    bind_exact_turn_settlement_task_body,
)
from loopx.project_prompt import (
    build_codex_cli_bootstrap_message,
    build_new_project_prompt,
    render_cli_preflight,
    render_quota_guard_command,
    render_turn_bootstrap,
)

PWSH = shutil.which("pwsh")
SH = shutil.which("sh")
GOAL_ID = "windows-onboarding-host"
POSIX_ONLY_TOKENS = (
    "python3 ",
    "| bash",
    "export PATH=",
    "command -v ",
    ">/dev/null",
)


def pretend_windows_host(
    monkeypatch: pytest.MonkeyPatch,
    interpreter: str = r"C:\Python311\python.exe",
) -> None:
    """Exercise every Windows branch without switching ``os.name``."""

    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: True)
    monkeypatch.setattr(project_prompt, "_is_windows_host", lambda: True)
    monkeypatch.setattr(sys, "executable", interpreter)


def assert_powershell_ready(command: str) -> None:
    assert command.strip(), "empty command"
    assert "\\\n" not in command, command
    for token in POSIX_ONLY_TOKENS:
        assert token not in command, (token, command)


def project_with_apostrophe(tmp_path: Path) -> Path:
    project = tmp_path / "project's"
    project.mkdir()
    return project


def test_windows_codex_bootstrap_message_prints_powershell_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pretend_windows_host(monkeypatch)
    project = project_with_apostrophe(tmp_path)
    payload = build_codex_cli_bootstrap_message(
        project=project,
        goal_id=GOAL_ID,
        agent_id="host-agent",
        cli_bin="loopx",
    )

    assert_powershell_ready(str(payload["install_repair_command"]))
    connect = str(payload["connect_command"])
    assert_powershell_ready(connect)
    assert len(connect.splitlines()) == 2
    assert connect.splitlines()[0].startswith("cd ")
    assert connect.splitlines()[1].startswith("loopx bootstrap --project .")
    assert "project''s" in connect
    assert "'\\''" not in connect


def test_windows_agent_onboard_prints_powershell_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pretend_windows_host(monkeypatch)
    project = project_with_apostrophe(tmp_path)
    payload = build_agent_onboarding_packet(
        project=project,
        agent_type="codex-cli",
        goal_id=GOAL_ID,
        agent_id="host-agent",
    )
    commands = payload["commands"]
    assert isinstance(commands, dict)

    for key in ("doctor_or_install", "bootstrap_command_pack", "agent_onboard_recheck"):
        assert_powershell_ready(str(commands[key]))
    assert "Get-Command" in str(commands["doctor_or_install"])
    assert "project''s" in str(commands["bootstrap_command_pack"])
    assert "project''s" in str(commands["agent_onboard_recheck"])


def test_windows_new_project_prompt_prints_powershell_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pretend_windows_host(monkeypatch)
    project = project_with_apostrophe(tmp_path)
    payload = build_new_project_prompt(
        project=project,
        goal_doc=project / "GOAL.md",
        goal_id=GOAL_ID,
        objective="Verify host-runnable onboarding",
        domain="example",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        next_probe=None,
        spawn_allowed=True,
        allowed_domains=["example.com"],
        write_scope=["docs/**"],
    )

    assert_powershell_ready(str(payload["cli_preflight"]))
    connect = str(payload["connect_command"])
    assert_powershell_ready(connect)
    assert len(connect.splitlines()) == 2
    assert "project''s" in connect
    assert "--spawn-allowed" in connect
    assert "--allowed-domain example.com" in connect
    assert "--write-scope 'docs/**'" in connect


def test_windows_heartbeat_preflight_does_not_upgrade_every_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pretend_windows_host(monkeypatch)
    command = render_cli_preflight()

    assert_powershell_ready(command)
    assert "Get-Command" in command
    assert command.count("-m pip install --upgrade loopx") == 1
    assert "doctor | Out-Null" in command


def test_posix_preflight_keeps_the_documented_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: False)
    monkeypatch.setattr(project_prompt, "_is_windows_host", lambda: False)

    command = render_cli_preflight()

    assert command.startswith('export PATH="$HOME/.local/bin:$PATH"')
    assert "install-local.sh" in command
    assert "doctor >/dev/null" in command


def run_powershell(pwsh: str, script: Path, *, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=cwd,
    )


@pytest.mark.skipif(
    os.name != "nt" or PWSH is None,
    reason="requires native Windows and PowerShell 7",
)
def test_generated_windows_onboarding_commands_run_in_powershell(tmp_path: Path) -> None:
    pwsh = PWSH
    assert pwsh is not None
    project = tmp_path / "project's"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = (
        f"{Path(sys.executable).parent}{os.pathsep}{environment.get('PATH', '')}"
    )
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)

    payload = build_new_project_prompt(
        project=project,
        goal_doc=project / "GOAL.md",
        goal_id=GOAL_ID,
        objective="Verify host-runnable onboarding",
        domain="example",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        next_probe=None,
        spawn_allowed=False,
        allowed_domains=None,
        write_scope=None,
    )

    connect_script = tmp_path / "connect.ps1"
    connect_script.write_text(str(payload["connect_command"]), encoding="utf-8")
    connect = run_powershell(pwsh, connect_script, env=environment, cwd=project)
    assert connect.returncode == 0, connect.stdout + connect.stderr
    assert (project / ".loopx" / "registry.json").is_file()
    assert (project / ".loopx" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md").is_file()

    preflight_script = tmp_path / "preflight.ps1"
    preflight_script.write_text(str(payload["cli_preflight"]), encoding="utf-8")
    preflight = run_powershell(pwsh, preflight_script, env=environment, cwd=project)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr


def test_windows_executable_path_uses_powershell_call_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pretend_windows_host(monkeypatch)
    cli = r"C:\Program Files\LoopX\loopx.exe"
    project = tmp_path / "project"
    project.mkdir()

    message = build_codex_cli_bootstrap_message(
        project=project,
        goal_id=GOAL_ID,
        agent_id="host-agent",
        cli_bin=cli,
    )
    assert message["connect_command"].splitlines()[1].startswith(
        f"& '{cli}' bootstrap"
    )
    packet = build_agent_onboarding_packet(
        project=project,
        agent_type="codex-cli",
        goal_id=GOAL_ID,
        agent_id="host-agent",
        cli_bin=cli,
    )
    commands = packet["commands"]
    assert isinstance(commands, dict)
    assert commands["agent_onboard_recheck"].startswith(f"& '{cli}' agent-onboard")
    assert f"& '{cli}'" in str(commands["bootstrap_command_pack"])
    assert f"& '{cli}'" in str(commands["doctor_or_install"])


def test_windows_heartbeat_turn_identity_uses_powershell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pretend_windows_host(monkeypatch)

    assert render_turn_bootstrap() == '$LOOPX_TURN = "<current_time_iso>"'
    guard = render_quota_guard_command("goal", heartbeat_turn_receipt=True)
    assert "--turn-instance-id $LOOPX_TURN" in guard
    assert "${LOOPX_TURN" not in guard
    body = f"```sh\n{render_turn_bootstrap()}\n{guard}\n```"
    bound = bind_exact_turn_settlement_task_body(
        body,
        turn_instance_id="2026-10-23T00:00:00Z",
    )
    assert '$LOOPX_TURN = "2026-10-23T00:00:00Z"' in bound
    assert "${LOOPX_TURN" not in bound


def test_posix_heartbeat_turn_identity_keeps_shell_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: False)
    monkeypatch.setattr(project_prompt, "_is_windows_host", lambda: False)

    assert render_turn_bootstrap() == "LOOPX_TURN=<current_time_iso>"
    guard = render_quota_guard_command("goal", heartbeat_turn_receipt=True)
    assert '--turn-instance-id "${LOOPX_TURN:?}"' in guard


@pytest.mark.skipif(
    os.name == "nt" or SH is None,
    reason="requires a POSIX shell to parse the printed command",
)
def test_posix_connect_commands_are_valid_shell(tmp_path: Path) -> None:
    sh = SH
    assert sh is not None
    project = tmp_path / "project's"
    project.mkdir()
    payload = build_new_project_prompt(
        project=project,
        goal_doc=project / "GOAL.md",
        goal_id=GOAL_ID,
        objective="Verify host-runnable onboarding",
        domain="example",
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected-read-only",
        next_probe=None,
        spawn_allowed=False,
        allowed_domains=None,
        write_scope=None,
    )
    for command in (payload["connect_command"], payload["cli_preflight"]):
        text = str(command)
        assert "\\\n" not in text
        completed = subprocess.run(
            [sh, "-n"],
            input=text,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
