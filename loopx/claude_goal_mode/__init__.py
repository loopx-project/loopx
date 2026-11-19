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

__all__: list[str] = []
