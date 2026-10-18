"""Identify the Python installer that owns this LoopX install, and render the
commands that installer needs so the host that prints them can run them.

Native Windows prints PowerShell guidance, where a quoted executable path is a
string literal rather than a command; POSIX keeps shell quoting.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex


@dataclass(frozen=True)
class PythonInstallOwner:
    manager: str
    environment: str | None = None


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_windows_host() -> bool:
    """Whether printed guidance targets PowerShell instead of a POSIX shell."""

    return os.name == "nt"


def interpreter_invocation(python_executable: str, arguments: list[str] | None = None) -> str:
    """The text that runs an interpreter path on the host that prints it.

    POSIX shells run a quoted path directly. PowerShell reads a bare quoted
    path as a string literal, so the ``&`` call operator with a single-quoted
    literal is required; that form also carries spaces and apostrophes in the
    interpreter path. This module owns the renderer because the pip-owned
    upgrade command is the first caller; doctor and update reuse it.
    Arguments, when supplied, use the same host quoting and retain empty values.
    """

    if _is_windows_host():
        return " ".join(
            [f"& {_powershell_literal(python_executable)}",
             *(_powershell_literal(value) for value in arguments or [])]
        )
    return shlex.join([python_executable, *(arguments or [])])


def resolve_python_install_owner(
    *,
    default_installer: str,
    prefix: Path,
) -> PythonInstallOwner:
    """Preserve pipx ownership instead of treating its internal pip as plain pip."""

    metadata_path = prefix / "pipx_metadata.json"
    if not metadata_path.is_file():
        return PythonInstallOwner(manager=default_installer)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    recorded_environment = metadata.get("environment") if isinstance(metadata, dict) else None
    environment = (
        recorded_environment
        if isinstance(recorded_environment, str) and recorded_environment
        else prefix.name
    )
    return PythonInstallOwner(manager="pipx", environment=environment)


def python_distribution_upgrade_command(
    *,
    owner: PythonInstallOwner,
    python_executable: str,
    doctor_command: str,
) -> str | None:
    if owner.manager == "pipx":
        package_command = f"pipx upgrade {shlex.quote(owner.environment or 'loopx')}"
    elif owner.manager == "pip":
        package_command = (
            f"{interpreter_invocation(python_executable)} -m pip install --upgrade loopx"
        )
    else:
        return None
    return (
        f"{package_command}\n"
        "loopx workflow-skills --install\n"
        "loopx slash-commands --install\n"
        f"{doctor_command}"
    )
