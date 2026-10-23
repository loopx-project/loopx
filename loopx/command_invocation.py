from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import shutil
from typing import Iterator, Mapping, Sequence


_loopx_cli_bin: ContextVar[str] = ContextVar("loopx_cli_bin", default="loopx")


def validate_cli_bin(value: str) -> str:
    """Accept one executable name/path, never a shell command or empty route."""
    if not value.strip() or value.startswith("-") or any(ord(c) < 32 for c in value):
        raise ValueError("--cli-bin requires one non-empty executable name or path")
    return value


@contextmanager
def loopx_cli_route(cli_bin: str | None) -> Iterator[None]:
    """Bind only this invocation's command rendering; retain no host policy."""
    token = _loopx_cli_bin.set(cli_bin if cli_bin is not None else "loopx")
    try:
        yield
    finally:
        _loopx_cli_bin.reset(token)


def loopx_cli_argv(cli_bin: str | None = None) -> list[str]:
    program = validate_cli_bin(cli_bin if cli_bin is not None else _loopx_cli_bin.get())
    # The executable selects this process; the option preserves that selection
    # when its returned commands start another process. Neither grants authority.
    return [program] if program == "loopx" else [program, "--cli-bin", program]


def resolve_command_path(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    """Resolve executables plus PowerShell scripts discoverable by PowerShell 7."""

    source_env = os.environ if env is None else env
    explicit = source_env.get("LOOPX_COMMAND_PATH") if name == "loopx" else None
    if os.name == "nt" and explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.is_file():
            return explicit_path
    if os.name != "nt":
        resolved = shutil.which(name, path=source_env.get("PATH"))
        return Path(resolved).expanduser() if resolved else None

    # Preserve PATH directory precedence across native executables and .ps1
    # launchers. A single global ``which`` would find a later loopx.exe before
    # considering an earlier loopx.ps1, unlike PowerShell command discovery.
    for entry in source_env.get("PATH", "").split(os.pathsep):
        normalized = entry.strip().strip('"')
        if not normalized:
            continue
        directory = Path(normalized).expanduser()
        resolved = shutil.which(name, path=str(directory))
        if resolved:
            return Path(resolved).expanduser()
        powershell_script = directory / f"{name}.ps1"
        if powershell_script.is_file():
            return powershell_script

    fallback = (home or Path.home()) / ".local" / "bin" / f"{name}.ps1"
    return fallback if fallback.is_file() else None


def command_argv(
    command_path: Path,
    args: Sequence[str],
    *,
    pwsh: str | None = None,
) -> list[str]:
    """Build a subprocess argv for native executables or PowerShell scripts."""

    if command_path.suffix.lower() != ".ps1":
        return [str(command_path), *args]
    powershell = pwsh or shutil.which("pwsh")
    if not powershell:
        raise FileNotFoundError("PowerShell 7 executable `pwsh` was not found on PATH")
    return [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-File",
        str(command_path),
        *args,
    ]
