#!/usr/bin/env python3
"""Kiro CLI stdio entrypoint for the shared LoopX MCP control plane.

Kiro CLI starts every configured MCP server as a child of the session and
passes it that session's ``KIRO_SESSION_ID`` (checked against 2.24.1 over
ACP: the child sees the same id ``session/new`` returned) with the workspace
as its working directory. ``start-goal --host-surface kiro-cli`` binds exactly
that id to one registered agent through ``bind-agent-thread``, so the server
resolves its Goal and agent from the durable thread binding and never from
registry order: an unbound or ambiguous session gets no identity, and every
tool answers with the setup hint instead of acting as another host's agent.

The server itself is the host-neutral ``goal_mode_mcp`` control plane; only the
typed runtime profile and the identity resolver are Kiro-specific.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# The provisioned MCP interpreter does not have LoopX installed; the package
# root is two levels above this file in both a checkout and a wheel install.
PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from loopx.goal_mode_context import find_registry, resolve_goal_context  # noqa: E402
from loopx.goal_mode_mcp import GoalModeMCPConfig, create_fastmcp_server  # noqa: E402
from loopx.kiro_cli_goal_mode import (  # noqa: E402
    KIRO_CLI_AGENT_TYPE,
    KIRO_CLI_SESSION_ID_ENV,
)
from loopx.thread_agent_binding import (  # noqa: E402
    ThreadBindingRequestError,
    resolve_registry_thread_agent_binding,
)

KIRO_CLI_RUNTIME_PROFILE = "kiro_cli"

CONFIG = GoalModeMCPConfig(
    server_name="loopx",
    runtime_profile=KIRO_CLI_RUNTIME_PROFILE,
    legacy_host_surface=KIRO_CLI_RUNTIME_PROFILE,
    setup_hint=(
        "run /loopx <task> from this Kiro CLI session so start-goal binds "
        f"{KIRO_CLI_SESSION_ID_ENV} to a registered agent"
    ),
)


def goal_context(
    cwd: str | Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """The Goal and agent this Kiro session is bound to, or None.

    Identity comes only from the thread binding for this exact session id.
    A missing id, a missing binding and an ambiguous binding all resolve to
    None so the control plane fails closed rather than guessing an agent.
    """
    session_id = str((environ or os.environ).get(KIRO_CLI_SESSION_ID_ENV) or "")
    if not session_id.strip():
        return None
    registry = find_registry(cwd)
    if registry is None:
        return None
    try:
        binding = resolve_registry_thread_agent_binding(
            registry_path=registry,
            host_surface=KIRO_CLI_AGENT_TYPE,
            thread_id=session_id,
        )
    except (ThreadBindingRequestError, OSError, ValueError):
        return None
    if binding.get("status") != "bound":
        return None
    return resolve_goal_context(
        cwd,
        preferred_goal_id=str(binding["goal_id"]),
        preferred_agent_id=str(binding["agent_id"]),
        require_preferred_binding=True,
    )


mcp, control = create_fastmcp_server(CONFIG, lambda: goal_context(Path.cwd()))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
