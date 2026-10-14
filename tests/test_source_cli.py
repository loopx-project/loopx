"""The source CLI launcher picks the host entry and reads UTF-8 output."""

from __future__ import annotations

import os
import sys

import tests.source_cli as source_cli
from tests.source_cli import (
    REPO_ROOT,
    run_source_cli,
    source_cli_command,
    source_cli_env,
)


def test_windows_hosts_use_the_shipped_windows_launcher_entry(
    monkeypatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    assert source_cli_command("version") == [
        sys.executable,
        "-I",
        str(REPO_ROOT / "scripts" / "loopx_entry.py"),
        "version",
    ]
    env = source_cli_env({"KEEP": "1"})
    assert env["LOOPX_RELEASE_ROOT"] == str(REPO_ROOT)
    assert env["KEEP"] == "1"
    stale = source_cli_env({"LOOPX_RELEASE_ROOT": "elsewhere"})
    assert stale["LOOPX_RELEASE_ROOT"] == str(REPO_ROOT)


def test_posix_hosts_keep_the_source_launcher_shim(monkeypatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    assert source_cli_command("version") == [
        str(REPO_ROOT / "scripts" / "loopx"),
        "version",
    ]
    env = source_cli_env({"KEEP": "1"})
    assert "LOOPX_RELEASE_ROOT" not in env
    assert env["KEEP"] == "1"


def test_run_source_cli_decodes_the_cli_output_as_utf8(monkeypatch) -> None:
    # loopx/entrypoint.py pins UTF-8 stdio, so the launcher read must not use
    # the host locale codec (cp936 fails on the CLI's own output).
    monkeypatch.setattr(
        source_cli,
        "source_cli_command",
        lambda *args: [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write('caf\u00e9 \u2615'.encode('utf-8'))",
        ],
    )
    completed = source_cli.run_source_cli(["version"])
    assert completed.stdout == "caf\u00e9 \u2615"


def test_source_cli_launches_through_the_host_entry() -> None:
    # Executes the real host entry end to end: scripts/loopx_entry.py on
    # Windows, the POSIX shim elsewhere.
    completed = run_source_cli(["--version"])
    assert completed.returncode == 0
    assert completed.stdout.strip()
