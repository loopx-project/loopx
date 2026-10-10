"""Install, repair, and fallback command text printed to LoopX operators.

One platform-aware owner keeps the bootstrap/connect hint, the doctor freshness
command, and the archive fallback consistent. POSIX keeps the documented public
defaults; native Windows has neither a ``python3`` launcher nor Bash, so it
names the interpreter and snapshot installer this host owns.
"""

from __future__ import annotations

import os
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


def _powershell_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _is_windows_host() -> bool:
    """Whether printed commands target PowerShell instead of a POSIX shell."""

    return os.name == "nt"


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
        return (
            f"{local_install_command(repo_root, skip_skills=skip_skills)}\n"
            f"loopx doctor{doctor_agent_arg}"
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
        return (
            f"& {_powershell_literal(sys.executable)} -m pip install --upgrade loopx\n"
            f"{DEFAULT_WORKFLOW_SKILL_INSTALL_COMMAND}\n"
            "loopx doctor"
        )
    return DEFAULT_INSTALL_REPAIR_COMMAND


def archive_fallback_install_command() -> str:
    """Archive fallback guidance that runs as printed on this host."""

    if _is_windows_host():
        return no_clone_upgrade_command()
    return ARCHIVE_FALLBACK_INSTALL_COMMAND
