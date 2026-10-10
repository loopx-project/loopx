"""Install/repair guidance LoopX prints must run on the host that prints it.

The bootstrap/connect hint is the first repair instruction a new operator sees.
Native Windows has no ``python3`` launcher and no Bash, while ``loopx doctor``
already names the interpreter and snapshot installer this host owns. These
tests pin that platform rule together with the PowerShell invocation semantics
the printed Windows commands need, so the guidance is executable and not only
worded.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from loopx import install_contract
from loopx.bootstrap import bootstrap_project

POSIX_ONLY_TOKENS = ("python3 ", "| bash", "export PATH=")


WINDOWS_INTERPRETERS = (
    r"C:\Python311\python.exe",
    r"C:\Program Files\Python311\python.exe",
    r"C:\O'Brien\python.exe",
)

PWSH = shutil.which("pwsh")


def powershell_literal(value: str) -> str:
    """PowerShell single-quoted literal, written independently of the owner."""

    return "'" + value.replace("'", "''") + "'"


def pretend_windows_host(
    monkeypatch: pytest.MonkeyPatch, interpreter: str | None = None
) -> None:
    """Exercise the Windows branch on any host without switching ``os.name``."""

    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: True)
    if interpreter is not None:
        monkeypatch.setattr(sys, "executable", interpreter)


def powershell_parse_errors(pwsh: str, script: Path) -> str:
    probe = (
        "$errors = $null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"{powershell_literal(str(script))}, [ref]$null, [ref]$errors) > $null;"
        "$errors | ForEach-Object { $_.Message }"
    )
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-Command", probe],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def bootstrap_hint(tmp_path: Path) -> tuple[str, str]:
    registry = tmp_path / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    payload = bootstrap_project(
        project=tmp_path,
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id="install-hint-goal",
        objective="Print install guidance.",
        domain="test",
        role="controller",
        parent_goal_id=None,
        state_file=None,
        goal_doc=None,
        adapter_kind="generic_project_goal_v0",
        adapter_status="connected",
        next_probe=None,
        spawn_allowed=False,
        max_children=0,
        allowed_domains=[],
        write_scope=[],
        force=False,
        dry_run=True,
        sync_global=False,
    )
    return str(payload["install_repair_command"]), str(
        payload["archive_fallback_install_command"]
    )


@pytest.mark.parametrize("os_name", ["posix", "nt"])
def test_install_hint_commands_follow_the_host_platform(
    monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    monkeypatch.setattr(install_contract, "_is_windows_host", lambda: os_name == "nt")
    repair = install_contract.install_repair_command()
    fallback = install_contract.archive_fallback_install_command()

    if os_name == "nt":
        for command in (repair, fallback):
            for token in POSIX_ONLY_TOKENS:
                assert token not in command, command
        assert repair.splitlines()[0] == (
            f"& {powershell_literal(sys.executable)} -m pip install --upgrade loopx"
        )
        assert "loopx workflow-skills --install" in repair
        assert "loopx doctor" in repair
        assert "install-windows.ps1" in fallback
        assert "curl" not in fallback
    else:
        assert repair == install_contract.DEFAULT_INSTALL_REPAIR_COMMAND
        assert fallback == install_contract.ARCHIVE_FALLBACK_INSTALL_COMMAND


@pytest.mark.parametrize("interpreter", WINDOWS_INTERPRETERS)
def test_windows_repair_command_invokes_the_interpreter_with_the_call_operator(
    monkeypatch: pytest.MonkeyPatch, interpreter: str
) -> None:
    pretend_windows_host(monkeypatch, interpreter)

    repair = install_contract.install_repair_command()

    assert repair.splitlines()[0] == (
        f"& {powershell_literal(interpreter)} -m pip install --upgrade loopx"
    )


@pytest.mark.skipif(PWSH is None, reason="requires pwsh to parse the printed commands")
@pytest.mark.parametrize("interpreter", WINDOWS_INTERPRETERS)
def test_windows_commands_parse_as_powershell(
    monkeypatch: pytest.MonkeyPatch, interpreter: str, tmp_path: Path
) -> None:
    pwsh = PWSH
    assert pwsh is not None
    pretend_windows_host(monkeypatch, interpreter)
    commands = {
        "repair": install_contract.install_repair_command(),
        "fallback": install_contract.archive_fallback_install_command(),
    }

    for name, command in commands.items():
        script = tmp_path / f"{name}-hint.ps1"
        script.write_text(command, encoding="utf-8")

        errors = powershell_parse_errors(pwsh, script)

        assert errors == "", (name, errors)


@pytest.mark.skipif(PWSH is None, reason="requires pwsh to run the printed command")
def test_windows_repair_command_runs_a_real_interpreter_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pwsh = PWSH
    assert pwsh is not None
    pretend_windows_host(monkeypatch)
    interpreter_line = install_contract.install_repair_command().splitlines()[0]
    witness = interpreter_line.replace(
        "-m pip install --upgrade loopx", "-c \"print('loopx-witness-ok')\""
    )
    assert witness != interpreter_line

    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-Command", witness],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr
    assert "loopx-witness-ok" in completed.stdout


def test_bootstrap_payload_prints_commands_this_host_can_run(tmp_path: Path) -> None:
    repair, fallback = bootstrap_hint(tmp_path)
    assert "loopx doctor" in repair
    assert "loopx doctor" in fallback
    if os.name == "nt":
        for command in (repair, fallback):
            for token in POSIX_ONLY_TOKENS:
                assert token not in command, command
        assert "install-windows.ps1" in fallback
    else:
        assert repair == install_contract.DEFAULT_INSTALL_REPAIR_COMMAND
        assert fallback == install_contract.ARCHIVE_FALLBACK_INSTALL_COMMAND
