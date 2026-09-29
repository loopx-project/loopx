#!/usr/bin/env python3
"""Kiro CLI ``preToolUse`` gate: every tool call answers to ``quota should-run``.

Installed only into the opt-in ``loopx`` agent. Kiro runs it before each tool
call with the event on stdin and, as checked on 2.24.1, honours exactly one
blocking signal: exit status ``2``, whose stderr is shown to the model. Any
other non-zero status lets the tool run, and a hook that outlives the entry's
``timeout_ms`` is abandoned and the tool runs too. So every denial here exits
``2``, and the probe deadline is kept well inside the configured timeout so a
slow control plane is refused here instead of being waved through by the host.

Identity is the session's ``bind-agent-thread`` binding, the same rule the MCP
server uses, resolved to a typed state rather than "context or nothing":

- bound: the Goal's ``quota should-run`` decides, and the session is recorded
  as armed (see ``gate_arming``);
- never bound, in a project with no registry or no binding yet: the gate stays
  out of the way, which is what lets ``/loopx`` run ``start-goal``;
- armed but no longer resolvable (registry gone or unreadable, binding
  deleted, ambiguous, or naming a removed Goal/agent), or any binding fault in
  a session that was never armed, or an event with no session id: state-
  changing calls are denied.

Read-only calls pass in every state, so a denied session can still inspect why.

The per-tool rule is ``loopx.control_plane.goal_mode_tool_policy``; this file only maps
Kiro's event and exit-code contract onto it.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# Kiro launches this file by path; the package root is two levels up in both a
# checkout and a wheel install.
PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    SchedulerRuntimeProfile,
)
from loopx.control_plane.goal_mode_tool_policy import (  # noqa: E402
    GateDecision,
    ToolCall,
    ToolKind,
    Verdict,
    decide_tool_call,
    probe_should_run,
)
from loopx.kiro_cli_goal_mode import (  # noqa: E402
    KIRO_CLI_HOOK_BLOCK_EXIT_STATUS as BLOCK_EXIT_STATUS,
    KIRO_CLI_HOOK_PROBE_TIMEOUT_SECONDS as PROBE_TIMEOUT_SECONDS,
    kiro_home,
)
from loopx.kiro_cli_goal_mode.gate_arming import (  # noqa: E402
    arm,
    armed_record,
    arming_root,
    is_armed,
)
from loopx.kiro_cli_goal_mode.session_context import (  # noqa: E402
    PRE_BINDING_STATUSES,
    SessionBinding,
    SessionBindingStatus,
    resolve_session_binding,
)

# Same typed owner as the MCP server, so the gate and the settlement path
# can never disagree on which profile a Kiro session runs under.
KIRO_CLI_RUNTIME_PROFILE = SchedulerRuntimeProfile.KIRO_CLI_VISIBLE.value

# Kiro's built-in tool names and their documented aliases, typed by what the
# call can do. Anything unlisted — code rewrites, aws, subagents, delegation,
# knowledge writes and every other MCP tool — is gated as OTHER.
READ_ONLY_TOOLS = frozenset(
    {
        "read", "fs_read", "fsRead",
        "glob", "grep",
        "web_search", "web_fetch",
        "introspect", "thinking", "tool_search",
        "todo", "todo_list",
        # Registers or settles the host's own goal loop; never delivers work,
        # and ending a loop must stay possible while the gate is closed.
        "goal",
        # The LoopX server's read paths must stay reachable so a closed gate
        # can still be inspected.
        "@loopx/should_run", "@loopx/list_todos",
    }
)
FILE_WRITE_TOOLS = frozenset({"write", "fs_write", "fsWrite"})
SHELL_TOOLS = frozenset({"shell", "execute_bash", "execute_cmd"})

BindingResolver = Callable[[str | None, str | None], SessionBinding]
ProbeFactory = Callable[[Mapping[str, Any]], Callable[[], "bool | None"]]


def tool_call(tool_name: str, tool_input: Mapping[str, Any]) -> ToolCall:
    if tool_name in READ_ONLY_TOOLS:
        return ToolCall(ToolKind.READ_ONLY)
    if tool_name in FILE_WRITE_TOOLS:
        return ToolCall(ToolKind.FILE_WRITE, write_path=str(tool_input.get("path") or ""))
    if tool_name in SHELL_TOOLS:
        return ToolCall(ToolKind.SHELL, command=str(tool_input.get("command") or ""))
    return ToolCall(ToolKind.OTHER)


def _probe_for(context: Mapping[str, Any]) -> Callable[[], bool | None]:
    # Run the CLI from the same package this hook was loaded from, not a
    # `loopx` on PATH that may belong to another release. `-P` keeps Kiro's
    # working directory off sys.path, so a project that is itself a LoopX
    # checkout cannot substitute its own package for the gate's.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(PACKAGE_PARENT), env.get("PYTHONPATH")) if part
    )
    return lambda: probe_should_run(
        command_prefix=[sys.executable, "-P", "-m", "loopx.cli"],
        registry=context.get("registry"),
        goal_id=context.get("goal_id"),
        agent_id=context.get("agent_id"),
        runtime_profile=KIRO_CLI_RUNTIME_PROFILE,
        timeout_seconds=PROBE_TIMEOUT_SECONDS,
        env=env,
    )


REBIND_HINT = (
    "re-bind this session with `loopx bind-agent-thread --host-surface kiro-cli` "
    "or start a new Kiro session"
)


def decide(
    event: Mapping[str, Any],
    *,
    resolve_binding: BindingResolver = resolve_session_binding,
    probe_for: ProbeFactory = _probe_for,
    armed_root: Path | None = None,
) -> GateDecision | None:
    """The gate verdict for one Kiro event, or None when goal-mode is off."""
    call = tool_call(str(event.get("tool_name") or ""), event.get("tool_input") or {})
    cwd = event.get("cwd")
    session_id = str(event.get("session_id") or "")
    root = armed_root if armed_root is not None else arming_root(kiro_home())
    binding = resolve_binding(cwd, session_id)

    if binding.bound and binding.context is not None:
        context = binding.context
        try:
            arm(root, session_id, context)
        except OSError:
            if call.kind is not ToolKind.READ_ONLY:
                return GateDecision(
                    Verdict.DENY,
                    "cannot record that this session is gated; failing closed",
                )
        return decide_tool_call(
            call,
            goal_id=context.get("goal_id"),
            write_scope=context.get("write_scope") or [],
            should_run=probe_for(context),
            cwd=str(cwd) if cwd else None,
        )

    if call.kind is ToolKind.READ_ONLY:
        return GateDecision(Verdict.ALLOW, "read-only tool")
    if binding.status is SessionBindingStatus.NO_SESSION_ID:
        return GateDecision(
            Verdict.DENY, "preToolUse event carries no session id; failing closed"
        )
    if is_armed(root, session_id):
        goal = armed_record(root, session_id).get("goal_id") or "unknown"
        return GateDecision(
            Verdict.DENY,
            f"this session is gated for goal '{goal}' but its binding can no "
            f"longer be resolved ({binding.status.value}); failing closed — "
            f"{REBIND_HINT}",
        )
    if binding.status in PRE_BINDING_STATUSES:
        return None
    return GateDecision(
        Verdict.DENY,
        f"session binding is {binding.status.value}; failing closed — {REBIND_HINT}",
    )


def run(stdin_text: str) -> tuple[int, str]:
    """Exit status and stderr text for one hook invocation."""
    try:
        event = json.loads(stdin_text or "")
        if not isinstance(event, dict):
            raise ValueError("event is not an object")
    except ValueError:
        return BLOCK_EXIT_STATUS, (
            "LoopX gate: unreadable preToolUse event; failing closed. "
            "Run `loopx slash-commands --install --surface kiro-cli "
            "--with-gated-agent` to refresh the hook."
        )
    try:
        decision = decide(event)
    except Exception as exc:  # noqa: BLE001 - any gate fault must not wave a tool through
        if tool_call(str(event.get("tool_name") or ""), event.get("tool_input") or {}).kind is ToolKind.READ_ONLY:
            return 0, ""
        return BLOCK_EXIT_STATUS, f"LoopX gate error ({type(exc).__name__}); failing closed."
    if decision is None or decision.verdict is not Verdict.DENY:
        return 0, ""
    return BLOCK_EXIT_STATUS, f"LoopX gate: {decision.reason}"


def main() -> None:
    status, message = run(sys.stdin.read())
    if message:
        sys.stderr.write(message + "\n")
    raise SystemExit(status)


if __name__ == "__main__":
    main()
