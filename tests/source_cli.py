"""Run the source-checkout LoopX CLI on the host's native launcher.

Tests that exercise CLI behavior must not exec ``scripts/loopx`` directly:
that file is a POSIX bash shim, so a native Windows host cannot start it
(``OSError: [WinError 193]``), and ``bash`` found on Windows is the WSL relay
that fails when WSL has no ``/bin/bash``. The shipped Windows launcher entry
is ``scripts/loopx_entry.py``: ``loopx/windows_install.py`` invokes it for
installed releases and ``scripts/loopx.ps1`` delegates to it. This module
selects that entry on Windows and keeps the bash shim on POSIX hosts.

The CLI pins UTF-8 stdio (``loopx/entrypoint.py``), so text reads pin
``encoding="utf-8"`` instead of the host locale codec; a cp936 host otherwise
fails to decode the CLI's own output.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def source_cli_command(*args: str) -> list[str]:
    """Build the argv for the host's source-checkout LoopX launcher."""
    if os.name == "nt":
        return [
            sys.executable,
            "-I",
            str(REPO_ROOT / "scripts" / "loopx_entry.py"),
            *args,
        ]
    return [str(REPO_ROOT / "scripts" / "loopx"), *args]


def source_cli_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the launcher environment; Windows needs the release root."""
    env = dict(os.environ if base is None else base)
    if os.name == "nt":
        # The shipped launchers pin the release root instead of inheriting an
        # ambient value; mirror that so a stale root cannot redirect the run.
        env["LOOPX_RELEASE_ROOT"] = str(REPO_ROOT)
    return env


def run_source_cli(
    args: Sequence[str],
    *,
    cwd: str | Path | None = REPO_ROOT,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run the source CLI and read its UTF-8 text result."""
    kwargs.setdefault("text", True)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("encoding", "utf-8")
    return subprocess.run(
        source_cli_command(*args),
        cwd=cwd,
        env=source_cli_env(env),
        check=check,
        **kwargs,
    )
