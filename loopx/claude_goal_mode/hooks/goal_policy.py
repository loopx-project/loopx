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
import subprocess
import sys
from pathlib import Path

# Goal context is resolved PER PROJECT from the event's cwd via the registry, and
# "armed" = the project's .claude/loop.md exists (see goal_state.py). The registry
# is the single source of truth; there is no separate active-state file.
from goal_state import active_context
from stdio_utf8 import pin_utf8_stdio

# Read-only tools are always allowed under goal-mode. Edit/Write are scoped to
# write_scope; Bash is gated by a destructive-command denylist. (No OS sandbox in
# this design — the hook is the whole gate; see the module docstring.)
# NOTE: `Task` is deliberately NOT here. It can launch a subagent that performs
# writes, so allowing it unconditionally would bypass the gate when
# should_run=false. It must go through the gate: denied when should_run=false,
# otherwise deferred to Claude Code's normal permission flow (unknown tool).
READONLY_TOOLS = {"Read", "Glob", "Grep", "NotebookRead", "TodoWrite", "WebFetch", "WebSearch", "ToolSearch"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DESTRUCTIVE = (
    "rm -rf", "rm -fr", "mkfs", "dd if=", ":(){", "shutdown", "reboot",
    "git push --force", "git reset --hard", "> /dev/sd", "format ",
)
CLAUDE_RUNTIME_PROFILE_ARGS = ["--runtime-profile", "claude_code"]
CLAUDE_LEGACY_SCHEDULER_ARGS = [
    "--host-surface", "claude_code",
    "--scheduler-owner", "agent_cli_loop",
    "--execution-mode", "interactive",
]


def within(path: str, root: str) -> bool:
    """True if `path` resolves to `root` or a descendant of it."""
    try:
        p = Path(path).resolve()
        r = Path(root).resolve()
        return r == p or r in p.parents
    except Exception:
        return False


def _gh_prefix():
    _exe = shutil.which("loopx")
    return [_exe] if _exe else [__import__("sys").executable, "-m", "loopx.cli"]


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


def _runtime_profile_flag_is_unsupported(out: subprocess.CompletedProcess) -> bool:
    diagnostic = str(out.stderr or "").lower()
    return (
        out.returncode == 2
        and "--runtime-profile" in diagnostic
        and (
            "unrecognized arguments" in diagnostic
            or "invalid choice" in diagnostic
        )
    )


def should_run(registry, goal_id, agent_id=None) -> bool | None:
    """Return True/False from loopx quota should-run, or None if unknown."""
    if not goal_id:
        return None
    cmd = list(_gh_prefix())
    if registry:
        cmd += ["--registry", registry]
    cmd += ["--format", "json", "quota", "should-run", "--goal-id", goal_id]
    if agent_id:
        cmd += ["--agent-id", agent_id]
    try:
        out = subprocess.run(
            [*cmd, *CLAUDE_RUNTIME_PROFILE_ARGS],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if _runtime_profile_flag_is_unsupported(out):
            out = subprocess.run(
                [*cmd, *CLAUDE_LEGACY_SCHEDULER_ARGS],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=10,
            )
    except Exception:
        return None  # caller fails CLOSED on None for non-read-only tools
    try:
        data = json.loads(out.stdout or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    should_run_value = data.get("should_run")
    return should_run_value if isinstance(should_run_value, bool) else None


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
    registry = ctx.get("registry")
    agent_id = ctx.get("agent_id")
    scope = ctx.get("write_scope") or []
    tool = ev.get("tool_name", "")
    ti = ev.get("tool_input", {}) or {}

    def d(decision, reason):
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason}}

    if tool in READONLY_TOOLS:
        return d("allow", "read-only/safe tool under goal-mode")

    sr = should_run(registry, goal_id, agent_id)  # True / False / None
    if sr is False:
        return d("deny", f"goal '{goal_id}' should_run=false (quota/gate closed)")
    if sr is None:
        # deterministic should_run probe unavailable -> fail closed (non-read-only tool)
        return d("deny", "loopx should_run probe unavailable — failing closed under goal-mode")

    # should_run == True. Scope the Edit/Write file tools to write_scope, and
    # gate Bash with the destructive denylist (the hook is the whole gate).
    if tool in WRITE_TOOLS:
        fp = ti.get("file_path") or ti.get("notebook_path") or ""
        if scope and not any(within(fp, s) for s in scope):
            return d("deny", f"'{fp}' outside goal write_scope {scope}")
        return d("allow", "write within goal scope")
    if tool == "Bash":
        low = (ti.get("command") or "").lower()
        if any(tok in low for tok in DESTRUCTIVE):
            return d("deny", "destructive command blocked by goal policy")
        return d("allow", "bash permitted under goal policy")
    return {}  # unknown tool: defer to normal flow


def main():
    raw = sys.stdin.read() or "{}"
    try:
        ev = json.loads(raw)
    except Exception:
        ev = {}
    result = decide(ev)
    sys.stdout.write(json.dumps(result) if result else "{}")


if __name__ == "__main__":
    pin_utf8_stdio()
    main()
