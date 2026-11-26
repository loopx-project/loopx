#!/usr/bin/env python3
"""Check the projected Kiro goal command against the host's own registry.

LoopX must not claim native command arguments the host does not contract. The
installed CLI advertises its command registry over ACP
(`_kiro.dev/commands/available`), which makes the shape verifiable on any
machine that has the binary instead of transcribed from documentation.

This probe is replayable and public-safe: it starts the host as an ACP stdio
agent, reads the advertised `/goal` entry, and compares it with what
`loopx.hosts.kiro_cli` projects. It exits 0 and reports `skipped` when the
binary is absent, so it is safe in environments without the host.

    python3 examples/kiro-cli-goal-command-contract-probe.py
"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.hosts.kiro_cli import (  # noqa: E402
    KIRO_CLI_GOAL_CLEAR_COMMAND,
    KIRO_CLI_GOAL_COMMAND,
    KIRO_CLI_GOAL_MAX_FLAG,
    kiro_cli_chat_command,
    kiro_cli_goal_invocation,
)

HANDSHAKE_TIMEOUT_SEC = 15.0
REGISTRY_NOTIFICATION = "_kiro.dev/commands/available"


def _read_frames(stdout, sel, deadline: float) -> list[dict]:
    frames: list[dict] = []
    buffer = ""
    while time.monotonic() < deadline:
        for key, _ in sel.select(timeout=0.4):
            chunk = os.read(key.fileobj.fileno(), 65536).decode("utf-8", "replace")
            if not chunk:
                return frames
            buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            if not line.strip():
                continue
            try:
                frames.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return frames


def advertised_goal_command() -> dict | None:
    """Return the host's own `/goal` registry entry, or None when unavailable."""
    command = kiro_cli_chat_command()
    if shutil.which(command[0]) is None:
        return None
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        for request_id, method, params in (
            (
                1,
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False}
                    },
                },
            ),
            (2, "session/new", {"cwd": str(REPO_ROOT), "mcpServers": []}),
        ):
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            process.stdin.write((json.dumps(payload) + "\n").encode())
            process.stdin.flush()
            frames = _read_frames(
                process.stdout,
                selector,
                time.monotonic() + HANDSHAKE_TIMEOUT_SEC,
            )
            for frame in frames:
                if frame.get("method") != REGISTRY_NOTIFICATION:
                    continue
                for entry in frame.get("params", {}).get("commands", []):
                    if entry.get("name") == KIRO_CLI_GOAL_COMMAND:
                        return entry
        return {}
    finally:
        # Close stdin first so the host releases its session before exiting.
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> int:
    projected = kiro_cli_goal_invocation()
    # The projection itself must stay inside the contracted shape regardless of
    # whether the host is installed, so assert that part unconditionally.
    assert projected.startswith(
        f"{KIRO_CLI_GOAL_COMMAND} {KIRO_CLI_GOAL_MAX_FLAG} "
    ), f"the iteration flag must precede the description: {projected}"
    for unsupported in ("--validate", "--agent"):
        assert unsupported not in projected, (
            f"{unsupported} is not a contracted argument of "
            f"{KIRO_CLI_GOAL_COMMAND}: {projected}"
        )

    entry = advertised_goal_command()
    if entry is None:
        print(
            "kiro-cli-goal-command-contract-probe: skipped "
            "(host binary not on PATH); projection shape checked"
        )
        return 0
    assert entry, (
        f"the host advertised no {KIRO_CLI_GOAL_COMMAND} entry; "
        "LoopX must not project a command the host does not expose"
    )

    subcommands = sorted(entry.get("meta", {}).get("subcommands", []))
    expected = sorted({KIRO_CLI_GOAL_CLEAR_COMMAND.split()[-1]})
    assert subcommands == expected, (
        f"advertised {KIRO_CLI_GOAL_COMMAND} subcommands {subcommands} do not "
        f"match the projected {expected}; update the projection rather than the "
        "expectation"
    )

    print(
        "kiro-cli-goal-command-contract-probe: ok "
        f"(advertised subcommands={subcommands}, projection={projected!r})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
