from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import shutil
import sys
from typing import Iterator, Mapping, Sequence


_loopx_cli_bin: ContextVar[tuple[str, ...]] = ContextVar("loopx_cli_bin", default=("loopx",))


def validate_cli_bin(value: str) -> str:
    """Accept one executable name/path, never a shell command or empty route."""
    if not value.strip() or value.startswith("-") or any(ord(c) < 32 for c in value):
        raise ValueError("--cli-bin requires one non-empty executable name or path")
    return value


@contextmanager
def loopx_cli_route(
    cli_bin: str | None, *, default_argv: Sequence[str] | None = None,
) -> Iterator[None]:
    """Bind only this invocation's command rendering; retain no host policy."""
    program = cli_bin if cli_bin is not None else "loopx"
    route = tuple(default_argv) if cli_bin is None and default_argv is not None else (
        (program,) if program == "loopx" else (program, "--cli-bin", program)
    )
    if not route:
        raise ValueError("the default CLI invocation must not be empty")
    token = _loopx_cli_bin.set(route)
    try:
        yield
    finally:
        _loopx_cli_bin.reset(token)


def loopx_cli_argv(cli_bin: str | None = None) -> list[str]:
    if cli_bin is None:
        route = _loopx_cli_bin.get()
        validate_cli_bin(route[0])
        return list(route)
    program = validate_cli_bin(cli_bin)
    # The executable selects this process; the option preserves that selection
    # when its returned commands start another process. Neither grants authority.
    return [program] if program == "loopx" else [program, "--cli-bin", program]


def validate_context_reader_route(route: object) -> list[str]:
    """Validate the existing absolute CLI route grammar."""
    if not isinstance(route, list) or not route or not all(isinstance(arg, str) for arg in route):
        raise ValueError("reader reference has an invalid CLI route")
    validate_cli_bin(route[0])
    if not os.path.isabs(route[0]):
        raise ValueError("reader reference has an invalid CLI route")
    if route[1:] in ([], ["-m", "loopx.entrypoint"], ["-I", "-m", "loopx.entrypoint"],
                     ["--cli-bin", route[0]]):
        return route
    raise ValueError("reader reference has an invalid CLI route")


def same_context_reader_route(current: list[str], recorded: list[str]) -> bool:
    """Require the current executable to be the one bound by the private reader."""
    if not current or not os.path.isabs(current[0]):
        return False
    current_entry = validate_context_reader_route(current)
    recorded_entry = validate_context_reader_route(recorded)
    if current_entry[1:2] == ["--cli-bin"]:
        current_entry = current_entry[:1]
    if recorded_entry[1:2] == ["--cli-bin"]:
        recorded_entry = recorded_entry[:1]
    if current_entry == recorded_entry:
        return True
    if (len(recorded_entry) == 1 and len(current_entry) > 1 and sys.argv
            and sys.orig_argv[1:2] == sys.argv[:1] and os.path.isabs(sys.argv[0])):
        # A console script is run by its shebang interpreter. The normal
        # route describes the interpreter, while this reference names the
        # actual script. A module launch has -m in the original argv.
        current_entry = sys.argv[:1]
    if current_entry[1:] != recorded_entry[1:]:
        return False
    try:
        return Path(current_entry[0]).samefile(recorded_entry[0])
    except OSError:
        return False


def cli_option_args(flag: str, value: object) -> list[str]:
    """Keep a legal option-looking value from becoming another CLI option."""
    text = str(value)
    return [f"{flag}={text}"] if text.startswith("-") else [flag, text]


def current_loopx_cli_argv(*, preserve_override: bool = True) -> list[str]:
    """Pin captured readers to this installed release or explicit CLI.

    A native Host can run from a project outside the source checkout. An ambient
    ``loopx`` may be another installation and lack this capture's reader. The
    installed launcher also preserves the release root that made this module
    importable; invoking its Python without that launcher can load a stale module.
    """
    route = loopx_cli_argv()
    if route != ["loopx"] and (preserve_override or route[1:2] != ["--cli-bin"]):
        return route

    release_root_text = os.environ.get("LOOPX_RELEASE_ROOT", "").strip()
    if release_root_text:
        release_root: Path | None = None
        module_root: Path | None = None
        launcher: Path | None = None
        launcher_ready = False
        try:
            release_root = Path(release_root_text).expanduser().resolve()
            module_root = Path(__file__).resolve().parents[1]
            launcher = release_root / "scripts" / (
                "loopx.ps1" if os.name == "nt" else "loopx"
            )
            launcher_ready = launcher.is_file() and (
                os.name == "nt" or os.access(launcher, os.X_OK)
            )
        except (OSError, RuntimeError):
            pass
        if release_root == module_root and launcher is not None and launcher_ready:
            return loopx_cli_argv(str(launcher))

    return [sys.executable, "-m", "loopx.entrypoint"]


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
