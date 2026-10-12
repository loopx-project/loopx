"""Pip-owned upgrade and rollback guidance must run on the host that prints it.

``loopx doctor`` (install-freshness ``upgrade_command`` and the fix hint) and
``loopx update plan`` (backup ``rollback_command``) name the interpreter that
owns a pip installation. Native Windows prints PowerShell, where a bare quoted
path is a string literal, so the printed interpreter token needs the ``&`` call
operator. These tests pin that platform rule and, where PowerShell is
available, run the printed interpreter line for real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from loopx import python_install_owner
from loopx.doctor import build_install_freshness, collect_doctor
from loopx.python_install_owner import (
    PythonInstallOwner,
    python_distribution_upgrade_command,
)
from loopx.self_update import build_update_plan

PLAIN_WINDOWS_INTERPRETER = r"C:\Python311\python.exe"
SPACED_WINDOWS_INTERPRETER = r"C:\Program Files\Python311\python.exe"
APOSTROPHE_WINDOWS_INTERPRETER = r"C:\O'Brien\python.exe"
SPACED_POSIX_INTERPRETER = "/opt/Loop X/bin/python"

PWSH = shutil.which("pwsh")


def powershell_literal(value: str) -> str:
    """PowerShell single-quoted literal, written independently of the owner."""

    return "'" + value.replace("'", "''") + "'"


def pretend_windows_host(
    monkeypatch: pytest.MonkeyPatch, interpreter: str | None = None
) -> None:
    """Exercise the Windows branch on any host without switching ``os.name``."""

    monkeypatch.setattr(python_install_owner, "_is_windows_host", lambda: True)
    if interpreter is not None:
        monkeypatch.setattr(sys, "executable", interpreter)


def pip_owner_upgrade_command() -> str:
    command = python_distribution_upgrade_command(
        owner=PythonInstallOwner("pip"),
        python_executable=sys.executable,
        doctor_command="loopx doctor",
    )
    assert command is not None
    return command


def pip_doctor_payload() -> dict[str, object]:
    """Just enough collected-doctor shape for a pip-owned update plan."""

    return {
        "package": {"install_kind": "python_distribution", "release_root": None},
        "install_freshness": {
            "install_kind": "python_distribution",
            "python_distribution_installer": "pip",
            "current_version": "1.4.0",
            "upgrade_command": pip_owner_upgrade_command(),
        },
    }


def interpreter_line_witness(rendered: str) -> str:
    """Swap the pip tail for a harmless probe of the same interpreter."""

    first_line = rendered.splitlines()[0]
    prefix, marker, _ = first_line.partition(" -m pip install")
    assert marker, first_line
    return f"{prefix} -c \"print('loopx-interpreter-witness-ok')\""


@pytest.mark.parametrize(
    "interpreter",
    (
        PLAIN_WINDOWS_INTERPRETER,
        SPACED_WINDOWS_INTERPRETER,
        APOSTROPHE_WINDOWS_INTERPRETER,
    ),
)
def test_windows_pip_upgrade_command_uses_the_call_operator(
    monkeypatch: pytest.MonkeyPatch, interpreter: str
) -> None:
    pretend_windows_host(monkeypatch, interpreter)

    command = pip_owner_upgrade_command()

    assert command.splitlines() == [
        f"& {powershell_literal(interpreter)} -m pip install --upgrade loopx",
        "loopx workflow-skills --install",
        "loopx slash-commands --install",
        "loopx doctor",
    ]


def test_windows_freshness_upgrade_command_uses_the_call_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pretend_windows_host(monkeypatch, SPACED_WINDOWS_INTERPRETER)

    freshness = build_install_freshness(
        command_path=tmp_path / "loopx",
        release_root=None,
        repo_root=tmp_path,
        skills={"loopx-project": {"exists": True, "required_phrases": True}},
        python_distribution={
            "available": True,
            "kind": "python_distribution",
            "version": "1.4.0",
            "installer": "pip",
        },
    )

    assert freshness["upgrade_command"].splitlines()[0] == (
        f"& {powershell_literal(SPACED_WINDOWS_INTERPRETER)} -m pip install --upgrade loopx"
    )


def test_windows_rollback_command_uses_the_call_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pretend_windows_host(monkeypatch, SPACED_WINDOWS_INTERPRETER)

    payload = build_update_plan(action="apply", doctor_payload=pip_doctor_payload())

    assert payload["plan"]["backup"]["rollback_command"].splitlines() == [
        f"& {powershell_literal(SPACED_WINDOWS_INTERPRETER)} -m pip install loopx==1.4.0",
        "loopx workflow-skills --install",
        "loopx slash-commands --install",
        "loopx doctor",
    ]


def test_posix_pip_upgrade_command_keeps_shell_quoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(python_install_owner, "_is_windows_host", lambda: False)

    command = python_distribution_upgrade_command(
        owner=PythonInstallOwner("pip"),
        python_executable=SPACED_POSIX_INTERPRETER,
        doctor_command="loopx doctor",
    )

    assert command is not None
    assert command.splitlines()[0] == (
        "'/opt/Loop X/bin/python' -m pip install --upgrade loopx"
    )


def test_windows_doctor_fix_hint_uses_the_call_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pretend_windows_host(monkeypatch, PLAIN_WINDOWS_INTERPRETER)
    monkeypatch.setattr(
        "loopx.doctor.python_distribution_install",
        lambda module_path: {
            "available": True,
            "kind": "python_distribution",
            "version": "1.4.0",
            "installer": "pip",
            "root": str(tmp_path),
        },
    )

    payload = collect_doctor()

    assert (
        f"`& {powershell_literal(PLAIN_WINDOWS_INTERPRETER)} "
        "-m pip install --upgrade loopx`"
    ) in str(payload["fix"])


def test_native_host_prints_a_runnable_interpreter_line() -> None:
    first_line = pip_owner_upgrade_command().splitlines()[0]

    if os.name == "nt":
        assert first_line.startswith("& '")
    else:
        assert not first_line.startswith("& ")


@pytest.mark.skipif(PWSH is None, reason="requires pwsh to run the printed command")
@pytest.mark.parametrize("surface", ("upgrade", "rollback"))
def test_windows_interpreter_line_runs_a_real_executable(
    monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    pwsh = PWSH
    assert pwsh is not None
    pretend_windows_host(monkeypatch)
    if surface == "rollback":
        payload = build_update_plan(action="apply", doctor_payload=pip_doctor_payload())
        rendered = str(payload["plan"]["backup"]["rollback_command"])
    else:
        rendered = pip_owner_upgrade_command()

    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-Command", interpreter_line_witness(rendered)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr
    assert "loopx-interpreter-witness-ok" in completed.stdout
