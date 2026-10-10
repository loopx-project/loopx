"""The opt-in ``loopx`` Kiro CLI agent whose ``preToolUse`` hook is the gate.

Kiro CLI 2.x only runs hooks declared in an agent config, so enforcement needs
an agent of its own; LoopX never edits the user's default agent. The agent
keeps the default agent's reach — every tool, the global and workspace MCP
servers (including the ``loopx`` server), and the installed skills — and adds
one hook that sends every tool call through ``pretooluse_hook.py``.

The whole file is LoopX-owned and says so in ``description``: a file without
that marker is the user's and is never overwritten or removed. The marker lives
inside the config instead of beside it because Kiro loads every ``*.json`` in
the agents directory as an agent.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loopx.kiro_cli_goal_mode import (
    KIRO_CLI_GATED_AGENT_LAUNCH as GATED_AGENT_LAUNCH,
    KIRO_CLI_GATED_AGENT_NAME as GATED_AGENT_NAME,
    KIRO_CLI_BIN,
    KIRO_CLI_HOOK_TIMEOUT_MS,
)
from loopx.kiro_cli_goal_mode.gate_arming import arming_root, clear_all

GATED_AGENT_MARKER = "[loopx-managed-kiro-agent:v1]"

__all__ = [
    "VERIFIED_HOST_MAJORS",
    "detect_host_version",
    "host_version_blocks_install",
    "GATED_AGENT_LAUNCH",
    "GATED_AGENT_MARKER",
    "GATED_AGENT_NAME",
    "build_gated_agent",
    "gated_agent_path",
    "sync_gated_agent",
]


# Host majors on which the embedded `hooks` of this agent file were observed to
# load and to block with exit 2 (probed on 2.24.1). Kiro CLI 3.0 documents
# hooks moving to `.kiro/hooks/*.json`; on a host whose embedded hooks are not
# loaded this agent would run every tool ungated while looking gated, so an
# unverified major is refused instead of installed.
VERIFIED_HOST_MAJORS = frozenset({2})

HostVersionProbe = Callable[[], "str | None"]


def detect_host_version(bin_name: str = KIRO_CLI_BIN) -> str | None:
    """``X.Y.Z`` from ``kiro-cli --version``, or None when it cannot be read."""
    executable = shutil.which(bin_name)
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout or "")
    return match.group(0) if match else None


def host_version_blocks_install(version: str | None) -> bool:
    """True when the detected host is a major this gate is not verified on.

    An undetectable version (for example the CLI is not on PATH yet) does not
    block: the file is inert until the host loads it, and the README states the
    verified range.
    """
    if version is None:
        return False
    return int(version.split(".", 1)[0]) not in VERIFIED_HOST_MAJORS


def gated_agent_path(kiro_root: Path) -> Path:
    return kiro_root / "agents" / f"{GATED_AGENT_NAME}.json"


def hook_script() -> Path:
    return Path(__file__).resolve().with_name("pretooluse_hook.py")


def _command_line(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def build_gated_agent(kiro_root: Path, *, interpreter: str | None = None) -> dict[str, Any]:
    return {
        "name": GATED_AGENT_NAME,
        "description": (
            "LoopX-gated Kiro agent. Until `/loopx` binds this session to a "
            "LoopX agent, tools run normally. Once bound, a preToolUse hook "
            "denies every state-changing tool call unless LoopX `quota "
            "should-run` allows it, and keeps denying if the binding is later "
            f"lost. {GATED_AGENT_MARKER}"
        ),
        "tools": ["*"],
        # The global/workspace mcp.json, where the kiro-cli surface registers
        # the `loopx` server.
        "includeMcpJson": True,
        # Inspecting a closed gate must not need a permission prompt.
        "allowedTools": ["@loopx/should_run", "@loopx/list_todos"],
        "resources": [
            f"skill://{(kiro_root / 'skills').as_posix()}/**/SKILL.md",
            "skill://.kiro/skills/**/SKILL.md",
        ],
        "hooks": {
            "preToolUse": [
                {
                    "matcher": "*",
                    "command": _command_line(
                        [interpreter or sys.executable, str(hook_script())]
                    ),
                    # A hook that outlives this is abandoned and the tool runs,
                    # so the hook's own probe deadline sits well inside it.
                    "timeout_ms": KIRO_CLI_HOOK_TIMEOUT_MS,
                }
            ]
        },
    }


def _is_managed(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and GATED_AGENT_MARKER in str(
        payload.get("description") or ""
    )


def sync_gated_agent(
    kiro_root: Path,
    *,
    uninstall: bool,
    execute: bool,
    interpreter: str | None = None,
    host_version: HostVersionProbe | None = None,
) -> str:
    """Install, refresh or retire the managed agent; returns the row status."""
    path = gated_agent_path(kiro_root)
    exists = path.exists()
    managed = exists and _is_managed(path)
    if uninstall:
        if not exists:
            return "absent"
        if not managed:
            return "skipped_user_owned_agent"
        if execute:
            path.unlink()
            # Arming records only mean something while the agent exists.
            clear_all(arming_root(kiro_root))
            return "retired"
        return "would_retire"
    if exists and not managed:
        return "skipped_user_owned_agent"
    if host_version_blocks_install((host_version or detect_host_version)()):
        return "blocked_unverified_kiro_cli_version"
    text = json.dumps(build_gated_agent(kiro_root, interpreter=interpreter), indent=2) + "\n"
    if exists and path.read_text(encoding="utf-8") == text:
        return "unchanged"
    if not execute:
        return "would_update" if exists else "would_write"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return "updated" if exists else "written"
