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
server uses. A session with no binding, or a project with no registry, is not
under goal-mode and the gate stays out of the way; that is also what lets
``/loopx`` run ``start-goal`` before the binding exists.

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
)
from loopx.kiro_cli_goal_mode.session_context import session_goal_context  # noqa: E402

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

ContextResolver = Callable[[str | None, str | None], "dict[str, Any] | None"]
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


def decide(
    event: Mapping[str, Any],
    *,
    resolve_context: ContextResolver = session_goal_context,
    probe_for: ProbeFactory = _probe_for,
) -> GateDecision | None:
    """The gate verdict for one Kiro event, or None when goal-mode is off."""
    call = tool_call(str(event.get("tool_name") or ""), event.get("tool_input") or {})
    cwd = event.get("cwd")
    context = resolve_context(cwd, event.get("session_id"))
    if not context:
        return None
    return decide_tool_call(
        call,
        goal_id=context.get("goal_id"),
        write_scope=context.get("write_scope") or [],
        should_run=probe_for(context),
        cwd=str(cwd) if cwd else None,
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
