#!/usr/bin/env python3
"""Claude Code entrypoint for the shared LoopX MCP control plane."""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from goal_state import goal_context  # noqa: E402
from loopx.goal_mode_mcp import GoalModeMCPConfig, create_fastmcp_server  # noqa: E402


CONFIG = GoalModeMCPConfig(
    server_name="loopx",
    runtime_profile="claude_code",
    legacy_host_surface="claude_code",
    setup_hint="run /loopx <task> first",
)
def _resolve_mcp_context() -> dict | None:
    cwd = Path.cwd()
    ctx = goal_context(cwd)
    if ctx is not None:
        return ctx
    try:
        if cwd.resolve() == Path("/"):
            from loopx.agy_goal_mode.host_runtime import resolve_bound_mcp_goal_context

            return resolve_bound_mcp_goal_context()
    except Exception:
        return None
    return None


mcp, control = create_fastmcp_server(CONFIG, _resolve_mcp_context)

# Compatibility names used by existing smoke tests and local integrations.
should_run = control.should_run
list_todos = control.list_todos
claim_task = control.claim_task
complete_task = control.complete_task


if __name__ == "__main__":
    mcp.run()
