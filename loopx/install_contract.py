"""Install, repair, and fallback command text printed to LoopX operators.

One platform-aware owner keeps the bootstrap/connect hint, the preflight prompt,
the doctor freshness command, and the archive fallback consistent. POSIX keeps
the documented public defaults; native Windows has neither a ``python3`` launcher
nor Bash, so it names the interpreter that runs here and prints the snapshot
installer only for a checkout that actually ships it.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any


NO_CLONE_INSTALL_URL = "https://loopx-project.github.io/loopx/install.sh"

DEFAULT_INSTALL_COMMAND = "python3 -m pip install --upgrade loopx"
DEFAULT_WORKFLOW_SKILL_INSTALL_COMMAND = "loopx workflow-skills --install"
DEFAULT_INSTALL_REPAIR_COMMAND = (
    f"{DEFAULT_INSTALL_COMMAND}\n"
    f"{DEFAULT_WORKFLOW_SKILL_INSTALL_COMMAND}\n"
    "loopx doctor"
)
ARCHIVE_FALLBACK_INSTALL_COMMAND = (
    f"curl -fsSL {NO_CLONE_INSTALL_URL} | bash\n"
    'export PATH="$HOME/.local/bin:$PATH"\n'
    "loopx doctor"
)


_POWERSHELL_SAFE_COMMAND = re.compile(r"^[A-Za-z0-9_./-]+$")


def _powershell_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell_command(value: str) -> str:
    """A command name or path PowerShell can invoke from the start of a pipeline."""

    if _POWERSHELL_SAFE_COMMAND.fullmatch(value):
        return value
    return f"& {_powershell_literal(value)}"


def host_command_name(value: str) -> str:
    """Render an executable as the first token of a host-shell command."""

    if _is_windows_host():
        return _powershell_command(value)
    return shlex.quote(value)


def _is_windows_host() -> bool:
    """Whether printed commands target PowerShell instead of a POSIX shell."""

    return os.name == "nt"


def _checkout_snapshot_installer(repo_root: Path) -> Path | None:
    """The snapshot installer only when this module runs from the checkout that owns it.

    An installed distribution places this module under site-packages, which does not
    ship ``scripts/install-windows.ps1``. Printing that path would hand the operator a
    ``pwsh -File`` command PowerShell rejects, so the project marker and the script
    itself decide whether the checkout route is real.
    """

    installer = repo_root / "scripts" / "install-windows.ps1"
    if installer.is_file() and (repo_root / "pyproject.toml").is_file():
        return installer
    return None


def _windows_pypi_upgrade_command(*, doctor_agent_arg: str, skip_skills: bool) -> str:
    """No-clone upgrade through the interpreter that owns this installation."""

    lines = [f"& {_powershell_literal(sys.executable)} -m pip install --upgrade loopx"]
    if not skip_skills:
        lines.append(DEFAULT_WORKFLOW_SKILL_INSTALL_COMMAND)
    lines.append(f"loopx doctor{doctor_agent_arg}")
    return "\n".join(lines)


def local_install_command(repo_root: Path, *, skip_skills: bool = False) -> str:
    if _is_windows_host():
        return (
            "pwsh -NoLogo -NoProfile -File "
            f"{_powershell_literal(repo_root / 'scripts' / 'install-windows.ps1')} "
            f"-Python {_powershell_literal(sys.executable)}"
            + (" -SkipSkills" if skip_skills else "")
        )
    command = str(repo_root / "scripts" / "install-local.sh")
    return f"LOOPX_INSTALL_SKILL=0 {command}" if skip_skills else command


def no_clone_upgrade_command(
    source_ref: Any = None,
    *,
    doctor_agent_type: str | None = None,
    skip_skills: bool = False,
) -> str:
    if _is_windows_host():
        repo_root = Path(__file__).resolve().parents[1]
        doctor_agent_arg = (
            f" --agent-type {_powershell_literal(doctor_agent_type)}"
            if doctor_agent_type
            else ""
        )
        if _checkout_snapshot_installer(repo_root) is not None:
            return (
                f"{local_install_command(repo_root, skip_skills=skip_skills)}\n"
                f"loopx doctor{doctor_agent_arg}"
            )
        return _windows_pypi_upgrade_command(
            doctor_agent_arg=doctor_agent_arg, skip_skills=skip_skills
        )
    ref = str(source_ref or "").strip()
    installer = f"curl -fsSL {NO_CLONE_INSTALL_URL}"
    doctor_agent_arg = (
        f" --agent-type {shlex.quote(doctor_agent_type)}"
        if doctor_agent_type
        else ""
    )
    install_env: list[str] = []
    if ref and ref != "stable":
        install_env.append(f"LOOPX_REF={shlex.quote(ref)}")
    if skip_skills:
        install_env.append("LOOPX_INSTALL_SKILL=0")
    if install_env:
        installer = f"{installer} | env {' '.join(install_env)} bash"
    else:
        installer = f"{installer} | bash"
    return (
        f"{installer}\n"
        'export PATH="$HOME/.local/bin:$PATH"\n'
        f"loopx doctor{doctor_agent_arg}"
    )


def install_repair_command() -> str:
    """PyPI repair guidance that runs as printed on this host."""

    if _is_windows_host():
        return _windows_pypi_upgrade_command(doctor_agent_arg="", skip_skills=False)
    return DEFAULT_INSTALL_REPAIR_COMMAND


def archive_fallback_install_command() -> str:
    """Archive fallback guidance that runs as printed on this host."""

    if _is_windows_host():
        return no_clone_upgrade_command()
    return ARCHIVE_FALLBACK_INSTALL_COMMAND


def doctor_preflight_command(
    *,
    cli_bin: str = "loopx",
    doctor_agent_type: str | None = None,
    install_skills: bool = False,
) -> str:
    """Ensure the CLI is reachable without upgrading on every scheduled turn."""

    if _is_windows_host():
        doctor_arg = (
            f" --agent-type {_powershell_literal(doctor_agent_type)}"
            if doctor_agent_type
            else ""
        )
        install_lines = [
            f"if (-not (Get-Command {_powershell_literal(cli_bin)} -ErrorAction SilentlyContinue)) {{",
            f"  & {_powershell_literal(sys.executable)} -m pip install --upgrade loopx",
        ]
        if install_skills:
            install_lines.append(
                f"  {_powershell_command(cli_bin)} workflow-skills --install"
            )
        install_lines.append("}")
        install_lines.append(
            f"{_powershell_command(cli_bin)} doctor{doctor_arg} | Out-Null"
        )
        return "\n".join(install_lines)
    cli_bin_arg = shlex.quote(cli_bin)
    doctor_arg = (
        f" --agent-type {shlex.quote(doctor_agent_type)}"
        if doctor_agent_type
        else ""
    )
    return f"""export PATH="$HOME/.local/bin:$PATH"
install_script="$HOME/loopx/scripts/install-local.sh"
if ! command -v {cli_bin_arg} >/dev/null 2>&1; then
  if [ -x "$install_script" ]; then
    "$install_script"
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "loopx is not on PATH; clone the LoopX repo and run scripts/install-local.sh" >&2
    exit 1
  fi
fi
{cli_bin_arg} doctor{doctor_arg} >/dev/null"""


def codex_cli_install_preflight(
    *,
    cli_bin: str = "loopx",
    doctor_agent_type: str | None = None,
) -> str:
    """Install-or-doctor guidance a Codex CLI operator can paste on this host."""

    if _is_windows_host():
        return doctor_preflight_command(
            cli_bin=cli_bin,
            doctor_agent_type=doctor_agent_type,
            install_skills=True,
        )
    cli_bin_arg = shlex.quote(cli_bin)
    doctor_agent_arg = (
        f" --agent-type {shlex.quote(doctor_agent_type)}"
        if doctor_agent_type
        else ""
    )
    return f"""export PATH="$HOME/.local/bin:$PATH"
if ! command -v {cli_bin_arg} >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1 && python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install --upgrade loopx
    if command -v {cli_bin_arg} >/dev/null 2>&1; then
      {cli_bin_arg} workflow-skills --install --cli-bin {cli_bin_arg}
    fi
  fi
  if ! command -v {cli_bin_arg} >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    curl -fsSL {NO_CLONE_INSTALL_URL} | bash
    export PATH="$HOME/.local/bin:$PATH"
  fi
  if ! command -v {cli_bin_arg} >/dev/null 2>&1; then
    echo "loopx is not on PATH; use a Python 3.11+ environment, the archive fallback, or a contributor checkout" >&2
    exit 1
  fi
fi
{cli_bin_arg} doctor{doctor_agent_arg} >/dev/null"""
