#!/usr/bin/env python3
"""PreToolUse policy gate for goal-mode — OPTIONAL deterministic hardening.

In the native-`/loop` design, Claude Code's `/loop` is the runtime and loopx's
MCP `should_run` is the per-tick gate. This hook is NOT installed by default —
it is opt-in (`install.py --harden` / `connect.py --harden`), project-scoped, and
adds a per-TOOL-CALL gate on top of the loop:

- should_run gate (fail-closed)  -> may the agent spend this turn right now?
- Edit/Write -> write_scope        -> deny file edits outside the goal's scope
- Bash -> destructive denylist     -> deny obviously dangerous commands

It only acts in a project where loopx is armed (a `.claude/loop.md` exists).

Limitation (by design): Bash is gated only by a denylist, so a determined shell
command can still write outside write_scope or reach the network. For STRONG
isolation (untrusted code / unattended high-stakes), run Claude Code inside a dev
container or VM, which contains the whole process at the environment level.

Behavior (only while goal-mode is ARMED for the event's project):
- no goal here / goal-mode off            -> {} (no-op; normal permission flow)
- read-only/safe tools                    -> allow
- should_run == false (quota/gate closed) -> deny
- should_run probe unavailable (error)    -> deny for non-read-only tools (FAIL-CLOSED)
- should_run == true:
    * Edit/Write outside write_scope      -> deny
    * Edit/Write within write_scope       -> allow
    * destructive Bash                    -> deny
    * Bash / other                        -> allow

Fail-closed: if the deterministic ``should_run`` probe cannot be reached, a
non-read-only tool is denied rather than allowed, so a flaky/broken control
plane pauses delivery instead of running ungated. Read-only tools stay allowed.

Identity-aware: when the active goal registered an agent (loopx
``coordination.registered_agents``), the context carries ``agent_id`` and this
hook forwards it as ``--agent-id`` to ``loopx quota should-run``. Without it
loopx returns ``automation_prompt_upgrade_required`` and should_run=false — the
Claude Code analogue of Codex's identity-scoped heartbeat prompt.
"""
from __future__ import annotations

import json
import shutil
import subprocess  # noqa: F401 - smokes patch goal_policy.subprocess.run
import sys

# Goal context is resolved PER PROJECT from the event's cwd via the registry, and
# "armed" = the project's .claude/loop.md exists (see goal_state.py). The registry
# is the single source of truth; there is no separate active-state file.
# goal_state also puts the repository root on sys.path, so it must be imported
# before the shared policy module.
from goal_state import active_context

from loopx.control_plane.goal_mode_tool_policy import (  # noqa: E402
    DESTRUCTIVE_SHELL_TOKENS,
    ToolCall,
    ToolKind,
    Verdict,
    decide_tool_call,
    probe_should_run,
    runtime_profile_flag_is_unsupported,
    within,
)

# Read-only tools are always allowed under goal-mode. Edit/Write are scoped to
# write_scope; Bash is gated by a destructive-command denylist. (No OS sandbox in
# this design — the hook is the whole gate; see the module docstring.) The rule
# itself is host-neutral and lives in loopx.control_plane.goal_mode_tool_policy; this module
# only maps Claude Code's event and output shapes onto it.
# NOTE: `Task` is deliberately NOT here. It can launch a subagent that performs
# writes, so allowing it unconditionally would bypass the gate when
# should_run=false. It must go through the gate: denied when should_run=false,
# otherwise deferred to Claude Code's normal permission flow (unknown tool).
READONLY_TOOLS = {"Read", "Glob", "Grep", "NotebookRead", "TodoWrite", "WebFetch", "WebSearch", "ToolSearch"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DESTRUCTIVE = DESTRUCTIVE_SHELL_TOKENS
CLAUDE_RUNTIME_PROFILE = "claude_code"
CLAUDE_RUNTIME_PROFILE_ARGS = ["--runtime-profile", CLAUDE_RUNTIME_PROFILE]
CLAUDE_LEGACY_SCHEDULER_ARGS = [
    "--host-surface", "claude_code",
    "--scheduler-owner", "agent_cli_loop",
    "--execution-mode", "interactive",
]

__all__ = ["decide", "emit", "should_run", "within"]


def _gh_prefix():
    _exe = shutil.which("loopx")
    return [_exe] if _exe else [sys.executable, "-m", "loopx.cli"]


def emit(decision=None, reason=""):
    if decision is None:
        sys.stdout.write("{}")  # no-op: defer to normal permission flow
        return
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))


_runtime_profile_flag_is_unsupported = runtime_profile_flag_is_unsupported


def should_run(registry, goal_id, agent_id=None) -> bool | None:
    """Return True/False from loopx quota should-run, or None if unknown."""
    return probe_should_run(
        command_prefix=_gh_prefix(),
        registry=registry,
        goal_id=goal_id,
        agent_id=agent_id,
        runtime_profile=CLAUDE_RUNTIME_PROFILE,
        legacy_scheduler_args=CLAUDE_LEGACY_SCHEDULER_ARGS,
        timeout_seconds=10,
    )


def _tool_call(tool: str, tool_input: dict) -> ToolCall:
    if tool in READONLY_TOOLS:
        return ToolCall(ToolKind.READ_ONLY)
    if tool in WRITE_TOOLS:
        return ToolCall(
            ToolKind.FILE_WRITE,
            write_path=tool_input.get("file_path") or tool_input.get("notebook_path") or "",
        )
    if tool == "Bash":
        return ToolCall(ToolKind.SHELL, command=tool_input.get("command") or "")
    return ToolCall(ToolKind.OTHER)


def decide(ev: dict) -> dict:
    """Pure policy: given a PreToolUse event, return the hookSpecificOutput dict
    (or {} for no-op). Reused by the CLI hook AND the Agent SDK in-process hook.

    The hook is the whole gate (no OS sandbox): should_run (fail-closed) +
    Edit/Write write_scope + a destructive-Bash denylist."""
    # Claude Code feeds the session's working directory on the event; resolve the
    # goal from THAT project's registry, and gate only while goal-mode is armed
    # (the project's .claude/loop.md exists). Unrelated/unarmed projects -> no-op.
    cwd = ev.get("cwd") or (ev.get("workspace") or {}).get("current_dir")
    ctx = active_context(cwd)
    if not ctx:
        return {}  # no goal here, or goal-mode off -> defer to normal flow

    goal_id = ctx.get("goal_id")
    decision = decide_tool_call(
        _tool_call(ev.get("tool_name", ""), ev.get("tool_input", {}) or {}),
        goal_id=goal_id,
        write_scope=ctx.get("write_scope") or [],
        # Looked up at call time so an in-process caller can substitute it.
        should_run=lambda: should_run(ctx.get("registry"), goal_id, ctx.get("agent_id")),
    )
    if decision.verdict is Verdict.DEFER:
        return {}  # unknown tool: defer to normal flow
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision.verdict.value,
        "permissionDecisionReason": decision.reason}}


def main():
    raw = sys.stdin.read() or "{}"
    try:
        ev = json.loads(raw)
    except Exception:
        ev = {}
    result = decide(ev)
    sys.stdout.write(json.dumps(result) if result else "{}")


if __name__ == "__main__":
    main()
