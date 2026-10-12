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

import sys
from pathlib import Path

# The provisioned MCP interpreter does not have LoopX installed; the package
# root is two levels above this file in both a checkout and a wheel install.
PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    SchedulerRuntimeProfile,
)
from loopx.goal_mode_mcp import GoalModeMCPConfig, create_fastmcp_server  # noqa: E402
from loopx.kiro_cli_goal_mode import KIRO_CLI_SESSION_ID_ENV  # noqa: E402
from loopx.kiro_cli_goal_mode.session_context import goal_context  # noqa: E402

# The typed scheduler profile is the single owner of the Kiro profile id.
KIRO_CLI_RUNTIME_PROFILE = SchedulerRuntimeProfile.KIRO_CLI_VISIBLE.value

CONFIG = GoalModeMCPConfig(
    server_name="loopx",
    runtime_profile=KIRO_CLI_RUNTIME_PROFILE,
    legacy_host_surface=KIRO_CLI_RUNTIME_PROFILE,
    setup_hint=(
        "run /loopx <task> from this Kiro CLI session so start-goal binds "
        f"{KIRO_CLI_SESSION_ID_ENV} to a registered agent"
    ),
)

__all__ = ["CONFIG", "goal_context", "main"]


mcp, control = create_fastmcp_server(CONFIG, lambda: goal_context(Path.cwd()))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
