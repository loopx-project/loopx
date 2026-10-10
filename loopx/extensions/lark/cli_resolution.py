"""Resolve the local lark-cli executable without loading a login shell.

The resolved command is private runtime state.  Public surfaces must use
``LarkCliResolution.public_snapshot`` so local filesystem paths never leak into
status payloads or diagnostics.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SEMVER_DIRECTORY = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_VERSION_TEXT = re.compile(r"(?:lark-cli\s+)?v?(\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9._-]+)?)")
_DEFAULT_SYSTEM_DIRS = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path("/usr/bin"),
    Path("/bin"),
)


@dataclass(frozen=True)
class LarkCliResolution:
    command: str | None
    available: bool
    source: str
    version: str | None
    error_code: str | None

    def public_snapshot(self) -> dict[str, Any]:
        """Return the path-free subset allowed on public status surfaces."""

        return {
            "available": self.available,
            "source": self.source,
            "version": self.version,
            "error_code": self.error_code,
        }

    def subprocess_env(
        self,
        environ: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        env = dict(os.environ if environ is None else environ)
        if self.command:
            parent = str(Path(self.command).expanduser().parent)
            current = [part for part in env.get("PATH", "").split(os.pathsep) if part]
            env["PATH"] = os.pathsep.join([parent, *(part for part in current if part != parent)])
        return env


class LarkCliUnavailableError(ValueError):
    """A path-free, operator-actionable lark-cli availability error."""

    def __init__(self, error_code: str) -> None:
        messages = {
            "lark_cli_not_installed": "Install lark-cli, then restart the LoopX Chat service.",
            "lark_cli_not_executable": "The configured lark-cli command is not executable.",
            "lark_cli_start_failed": "lark-cli could not start. Restart LoopX after checking the installation.",
        }
        self.error_code = error_code
        super().__init__(messages.get(error_code, "lark-cli is unavailable."))


def _candidate_command(value: str, *, path: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.parent != Path(".") or candidate.is_absolute():
        return candidate
    discovered = shutil.which(raw, path=path)
    return Path(discovered) if discovered else candidate


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _semantic_version(path: Path) -> tuple[int, int, int] | None:
    match = _SEMVER_DIRECTORY.fullmatch(path.name)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _nvm_candidates(home: Path) -> list[Path]:
    version_root = home / ".nvm" / "versions" / "node"
    versions: list[tuple[tuple[int, int, int], Path]] = []
    if version_root.is_dir():
        for directory in version_root.iterdir():
            semantic = _semantic_version(directory)
            if semantic is not None:
                versions.append((semantic, directory / "bin" / "lark-cli"))
    return [path for _version, path in sorted(versions, key=lambda item: item[0], reverse=True)]


def _version(
    command: Path,
    *,
    environ: Mapping[str, str],
    subprocess_run: Callable[..., Any],
) -> str | None:
    env = LarkCliResolution(
        command=str(command),
        available=True,
        source="probe",
        version=None,
        error_code=None,
    ).subprocess_env(environ)
    try:
        completed = subprocess_run(
            [str(command), "--version"],
            capture_output=True,
            check=False,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if int(getattr(completed, "returncode", 1)) != 0:
        return None
    rendered = " ".join(str(getattr(completed, "stdout", "") or "").split())
    match = _VERSION_TEXT.search(rendered)
    return match.group(1) if match else None


def _available_resolution(
    command: Path,
    *,
    source: str,
    environ: Mapping[str, str],
    subprocess_run: Callable[..., Any],
) -> LarkCliResolution:
    return LarkCliResolution(
        command=str(command),
        available=True,
        source=source,
        version=_version(command, environ=environ, subprocess_run=subprocess_run),
        error_code=None,
    )


def resolve_lark_cli(
    *,
    explicit: str | None = None,
    target_cli_bin: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    system_dirs: Sequence[Path] = _DEFAULT_SYSTEM_DIRS,
    subprocess_run: Callable[..., Any] = subprocess.run,
) -> LarkCliResolution:
    """Resolve lark-cli from a bounded set of non-shell runtime locations."""

    env = dict(os.environ if environ is None else environ)
    path_value = env.get("PATH", "")
    resolved_home = (home or Path.home()).expanduser()

    for value, source in ((explicit, "explicit"), (target_cli_bin, "target")):
        if value is None:
            continue
        candidate = _candidate_command(value, path=path_value)
        if candidate is not None and _is_executable(candidate):
            return _available_resolution(
                candidate,
                source=source,
                environ=env,
                subprocess_run=subprocess_run,
            )
        return LarkCliResolution(
            command=None,
            available=False,
            source=source,
            version=None,
            error_code="lark_cli_not_executable",
        )

    path_candidate = shutil.which("lark-cli", path=path_value)
    if path_candidate and _is_executable(Path(path_candidate)):
        return _available_resolution(
            Path(path_candidate),
            source="path",
            environ=env,
            subprocess_run=subprocess_run,
        )

    nvm_bin = str(env.get("NVM_BIN") or "").strip()
    if nvm_bin:
        candidate = Path(nvm_bin).expanduser() / "lark-cli"
        if _is_executable(candidate):
            return _available_resolution(
                candidate,
                source="nvm",
                environ=env,
                subprocess_run=subprocess_run,
            )

    for candidate in _nvm_candidates(resolved_home):
        if _is_executable(candidate):
            return _available_resolution(
                candidate,
                source="nvm",
                environ=env,
                subprocess_run=subprocess_run,
            )

    for candidate in (
        resolved_home / ".local" / "bin" / "lark-cli",
        resolved_home / ".npm-global" / "bin" / "lark-cli",
    ):
        if _is_executable(candidate):
            return _available_resolution(
                candidate,
                source="user_local",
                environ=env,
                subprocess_run=subprocess_run,
            )

    for directory in system_dirs:
        candidate = Path(directory) / "lark-cli"
        if _is_executable(candidate):
            source = "homebrew" if str(directory) in {"/opt/homebrew/bin", "/usr/local/bin"} else "system"
            return _available_resolution(
                candidate,
                source=source,
                environ=env,
                subprocess_run=subprocess_run,
            )

    return LarkCliResolution(
        command=None,
        available=False,
        source="missing",
        version=None,
        error_code="lark_cli_not_installed",
    )


def build_lark_command_runner(
    resolution: LarkCliResolution,
    *,
    environ: Mapping[str, str] | None = None,
    subprocess_run: Callable[..., Any] = subprocess.run,
) -> Callable[[list[str], Path | None, float | None], dict[str, Any]]:
    """Build the dashboard runner with the resolved Node wrapper environment."""

    def run(
        args: list[str],
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not resolution.available:
            raise LarkCliUnavailableError(resolution.error_code or "lark_cli_not_installed")
        env = resolution.subprocess_env(environ)
        invoked = Path(args[0]).expanduser() if args else None
        if invoked is not None and (invoked.is_absolute() or invoked.parent != Path(".")):
            parent = str(invoked.parent)
            current = [part for part in env.get("PATH", "").split(os.pathsep) if part]
            env["PATH"] = os.pathsep.join(
                [parent, *(part for part in current if part != parent)]
            )
        completed = subprocess_run(
            args,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            capture_output=True,
            env=env,
            text=True,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }

    return run
