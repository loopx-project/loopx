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
- event unreadable                        -> deny (FAIL-CLOSED)
  (no usable session cwd, no usable tool_name)
- read-only/safe tools                    -> allow
- non-read-only tool, unreadable event value -> deny (FAIL-CLOSED)
  (non-object tool_input, non-string command/file path)
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
An event this hook cannot read joins that rule: ``rc=1`` with no decision is a
non-blocking hook error to Claude Code, which then continues the tool call with
no gate at all, so an unreadable event (undecodable stdin, malformed or
non-object JSON, no session cwd or one that cannot be resolved as a path, no
usable tool name, or - for a non-read-only tool - a non-object ``tool_input`` or
a non-string command/file path field) is answered with an explicit deny instead
of dying. Read-only tools keep their unconditional allow.

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


def _usable_cwd(value: str) -> bool:
    """True if a decoded session cwd can be resolved as a filesystem path.

    A string can still be unusable as a path: an embedded NUL makes
    ``Path.resolve`` raise ``ValueError``, and a lone surrogate raises
    ``UnicodeEncodeError`` (a ``ValueError``) against the filesystem encoding.
    ``active_context`` must never receive such a value, or the exception escapes
    ``decide`` as the rc=1/no-decision hook error the module docstring refuses.
    """
    try:
        Path(value).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return True


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
    Edit/Write write_scope + a destructive-Bash denylist. An event value this
    gate cannot use - no usable session cwd or tool name, or a non-object
    tool_input / non-string command or file path on a non-read-only tool - is
    refused rather than deferred, because the event cannot be classified (the
    module docstring states the fail-closed rule)."""
    def d(decision, reason):
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason}}

    # Claude Code feeds the session's working directory on the event; resolve the
    # goal from THAT project's registry, and gate only while goal-mode is armed
    # (the project's .claude/loop.md exists). Unrelated/unarmed projects -> no-op.
    cwd = ev.get("cwd")
    workspace = ev.get("workspace")
    if not isinstance(cwd, str) or not cwd:
        cwd = workspace.get("current_dir") if isinstance(workspace, dict) else None
    if not isinstance(cwd, str) or not cwd:
        # Every real PreToolUse event carries the session cwd. Without one this
        # hook cannot tell an armed project from an unrelated one, so it cannot
        # decide to stay out of the way: refusing is the same fail-closed rule
        # the unreachable-should_run branch below applies. Unreadable events
        # (see main) arrive here as an empty dict.
        return d("deny", "unreadable PreToolUse event (no session cwd) - failing closed under goal-mode")
    if not _usable_cwd(cwd):
        # A decoded cwd can still be unusable as a path (embedded NUL, lone
        # surrogate, OS-level resolution failure). active_context cannot
        # attribute the event to a project then, so the event stays unreadable
        # for this gate and is refused instead of raising out of decide.
        return d("deny", "unreadable PreToolUse event (session cwd is not a usable path) - failing closed under goal-mode")

    ctx = active_context(cwd)
    if not ctx:
        return {}  # no goal here, or goal-mode off -> defer to normal flow

    goal_id = ctx.get("goal_id")
    registry = ctx.get("registry")
    agent_id = ctx.get("agent_id")
    scope = ctx.get("write_scope") or []
    tool = ev.get("tool_name")
    if not isinstance(tool, str) or not tool:
        # Every real PreToolUse event names its tool; without a usable name the
        # gate cannot apply its read-only/gated classification (and the set
        # membership tests below would raise on an unhashable value).
        return d("deny", "unreadable PreToolUse event (tool_name is not a usable string) - failing closed under goal-mode")
    ti = ev.get("tool_input")
    if ti is None:
        # Only an absent/null field takes the empty-input default. Other falsy
        # values (`[]`, `false`, `0`, `""`) are present non-objects and must
        # stay visible to the check below instead of being hidden by `or {}`.
        ti = {}

    if tool in READONLY_TOOLS:
        return d("allow", "read-only/safe tool under goal-mode")

    if not isinstance(ti, dict):
        # A tool_input that is present but not an object cannot be read: refuse
        # the non-read-only tool instead of crashing on `ti.get` (same
        # fail-closed rule as an unreachable `should_run` probe).
        return d("deny", "unreadable PreToolUse event (tool_input is not an object) - failing closed under goal-mode")

    # Every value the gate reads below must be readable before the probe is
    # consulted, so an unreadable event is refused whether or not the gate is
    # open (an absent/null field keeps its empty default).
    if tool in WRITE_TOOLS:
        file_path = ti.get("file_path")
        notebook_path = ti.get("notebook_path")
        if file_path is not None and not isinstance(file_path, str):
            return d("deny", "unreadable PreToolUse event (tool_input.file_path is not a string) - failing closed under goal-mode")
        if notebook_path is not None and not isinstance(notebook_path, str):
            return d("deny", "unreadable PreToolUse event (tool_input.notebook_path is not a string) - failing closed under goal-mode")
    elif tool == "Bash":
        command = ti.get("command")
        if command is not None and not isinstance(command, str):
            return d("deny", "unreadable PreToolUse event (tool_input.command is not a string) - failing closed under goal-mode")

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
    try:
        # `ValueError` also covers `UnicodeDecodeError`: the strict stdin pin
        # (stdio_utf8) never repairs a bad byte into a different event, so an
        # event that cannot be read becomes the explicit deny in decide()
        # instead of a traceback with no decision.
        ev = json.loads(sys.stdin.read())
    except ValueError:
        ev = {}
    if not isinstance(ev, dict):
        ev = {}
    result = decide(ev)
    sys.stdout.write(json.dumps(result) if result else "{}")


if __name__ == "__main__":
    pin_utf8_stdio()
    main()
