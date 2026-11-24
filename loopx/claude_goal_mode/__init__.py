"""Claude Code adapter for loopx.

loopx is a deterministic (no-LLM) control plane. On Claude Code the RUN LOOP is
Claude Code's native ``/loop``; loopx provides the control-plane protocol (we do
NOT use ``/goal``, which judges completion from the transcript). Pieces:

- ``mcp/loopx_mcp.py``        — MCP server: should_run / list_todos / claim_task /
                                complete_task (the deterministic control plane)
- ``scripts/goalmode_cmd.py`` — the ``/loopx`` setup helper: sets a goal and writes
                                ``.claude/loop.md`` (the per-iteration protocol that
                                native ``/loop`` runs each tick)
- ``hooks/goal_state.py``     — registry-driven goal context + "armed" (loop.md) check
- ``hooks/goal_policy.py``    — OPTIONAL PreToolUse should_run gate (opt-in via --harden)
- ``statusline/goal_status.py`` — optional glanceable goal state
- ``scripts/install.py``      — explicit, scoped, opt-in installer (MCP + /loopx [+ --harden])
- ``scripts/connect.py``      — project-scoped connect + install
- ``plugin`` assets           — ``.claude-plugin/plugin.json``, ``commands/loopx.md``,
                                ``hooks/hooks.json`` (the optional gate)

The retired benchmark-only baseline contract remains in Git history and is
no longer part of the working tree or a LoopX runtime module.
"""

from __future__ import annotations

from ..hosts.capabilities import (
    HostHarnessCapabilities,
    _register_builtin_host_capabilities,
)

CLAUDE_HOST_CAPABILITIES: HostHarnessCapabilities = _register_builtin_host_capabilities(
    HostHarnessCapabilities(
        host_id="claude",
        integration_tier="boundary_cli",
        adapter_module="loopx.claude_goal_mode",
        supported_capabilities=("verifier_hook",),
        hook_events=("PreToolUse",),
        requires_utf8_stdio=True,
        native_goal_primitive=False,
        native_scheduler_primitive=True,
        description=(
            "Claude Code boundary CLI adapter driven by native /loop, MCP "
            "control-plane tools, and opt-in PreToolUse should_run/write_scope "
            "policy verification."
        ),
    )
)

__all__: list[str] = ["CLAUDE_HOST_CAPABILITIES"]
