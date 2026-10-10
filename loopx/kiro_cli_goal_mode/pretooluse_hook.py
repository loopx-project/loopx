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

The event is validated once, at the entry, into a typed ``HookEvent``; a
parseable but malformed event (a ``tool_input`` that is not an object, a
non-string ``tool_name`` or path) is refused rather than crashing. No path may
end in an exit status other than ``0`` or ``2``: every fault, including a
failure to import LoopX itself, is converted to ``2`` for anything that is not
a read-only tool, and the process leaves through ``os._exit`` so a late flush
error cannot change the status.

The per-tool rule is ``loopx.control_plane.goal_mode_tool_policy``; this file only maps
Kiro's event and exit-code contract onto it.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The only status Kiro 2.24.1 honours as "block". Owned here, with no LoopX
# import, so the gate can still refuse when the package fails to load.
BLOCK_EXIT_STATUS = 2
ALLOW_EXIT_STATUS = 0

# Kiro's built-in tool names and their documented aliases, typed by what the
# call can do. Anything unlisted — code rewrites, aws, subagents, delegation,
# knowledge writes and every other MCP tool — is gated as OTHER. Plain data,
# kept above the LoopX imports so a read-only call can be recognised even
# when those imports fail.
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

# Kiro launches this file by path; the package root is two levels up in both a
# checkout and a wheel install.
PACKAGE_PARENT = Path(__file__).resolve().parents[2]

_IMPORT_FAILURE: BaseException | None = None
try:
    if str(PACKAGE_PARENT) not in sys.path:
        sys.path.insert(0, str(PACKAGE_PARENT))
    from loopx.control_plane.goal_mode_tool_policy import (
        GateDecision,
        ToolCall,
        ToolKind,
        Verdict,
        decide_tool_call,
        probe_should_run,
    )
    from loopx.control_plane.scheduler.execution_context import (
        SchedulerRuntimeProfile,
    )
    from loopx.kiro_cli_goal_mode import (
        KIRO_CLI_HOOK_PROBE_TIMEOUT_SECONDS as PROBE_TIMEOUT_SECONDS,
        kiro_home,
    )
    from loopx.kiro_cli_goal_mode.gate_arming import (
        arm,
        armed_record,
        arming_root,
        is_armed,
    )
    from loopx.kiro_cli_goal_mode.session_context import (
        PRE_BINDING_STATUSES,
        SessionBinding,
        SessionBindingStatus,
        resolve_session_binding,
    )

    # Same typed owner as the MCP server, so the gate and the settlement path
    # can never disagree on which profile a Kiro session runs under.
    KIRO_CLI_RUNTIME_PROFILE = SchedulerRuntimeProfile.KIRO_CLI_VISIBLE.value
except Exception as exc:  # noqa: BLE001 - a broken install must still refuse
    _IMPORT_FAILURE = exc

BindingResolver = Callable[[str | None, str | None], "SessionBinding"]
ProbeFactory = Callable[[Mapping[str, Any]], Callable[[], "bool | None"]]


class MalformedEvent(ValueError):
    """A parseable preToolUse event whose fields do not have the host's shape."""


@dataclass(frozen=True)
class HookEvent:
    tool_name: str
    tool_input: dict[str, Any]
    cwd: str | None
    session_id: str


def _optional_string(raw: Mapping[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is None or isinstance(value, str):
        return value
    raise MalformedEvent(f"{field} is not a string")


def parse_event(raw: Any) -> HookEvent:
    """The one place a host event is checked; everything after reads HookEvent."""
    if not isinstance(raw, dict):
        raise MalformedEvent("event is not an object")
    tool_name = raw.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise MalformedEvent("tool_name is missing or not a string")
    tool_input = raw.get("tool_input")
    if tool_input is None:
        tool_input = {}
    if not isinstance(tool_input, dict):
        raise MalformedEvent("tool_input is not an object")
    return HookEvent(
        tool_name=tool_name,
        tool_input=tool_input,
        cwd=_optional_string(raw, "cwd"),
        session_id=_optional_string(raw, "session_id") or "",
    )


def _required_string(tool_input: Mapping[str, Any], field: str, tool_name: str) -> str:
    value = tool_input.get(field)
    if not isinstance(value, str) or not value:
        raise MalformedEvent(f"{tool_name} input has no string {field}")
    return value


def tool_call(tool_name: str, tool_input: Mapping[str, Any]) -> ToolCall:
    """Type one call; a write or shell call without its target is malformed.

    A missing path must not default to "" — that resolves to the working
    directory and would pass the write-scope check.
    """
    if tool_name in READ_ONLY_TOOLS:
        return ToolCall(ToolKind.READ_ONLY)
    if tool_name in FILE_WRITE_TOOLS:
        return ToolCall(
            ToolKind.FILE_WRITE,
            write_path=_required_string(tool_input, "path", tool_name),
        )
    if tool_name in SHELL_TOOLS:
        return ToolCall(
            ToolKind.SHELL,
            command=_required_string(tool_input, "command", tool_name),
        )
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
    event: HookEvent | Mapping[str, Any],
    *,
    resolve_binding: BindingResolver | None = None,
    probe_for: ProbeFactory | None = None,
    armed_root: Path | None = None,
) -> GateDecision | None:
    """The gate verdict for one Kiro event, or None when goal-mode is off.

    Raises MalformedEvent for an event without the host's shape; the caller
    turns that into a refusal.
    """
    parsed = event if isinstance(event, HookEvent) else parse_event(event)
    call = tool_call(parsed.tool_name, parsed.tool_input)
    cwd = parsed.cwd
    session_id = parsed.session_id
    root = armed_root if armed_root is not None else arming_root(kiro_home())
    binding = (resolve_binding or resolve_session_binding)(cwd, session_id)
    probe_for = probe_for or _probe_for

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


def _refusal(tool_name: Any, message: str) -> tuple[int, str]:
    """Refuse unless the tool name alone proves the call cannot change state."""
    if isinstance(tool_name, str) and tool_name in READ_ONLY_TOOLS:
        return ALLOW_EXIT_STATUS, ""
    return BLOCK_EXIT_STATUS, f"LoopX gate: {message}; failing closed."


def run(stdin_text: str) -> tuple[int, str]:
    """Exit status and stderr text for one hook invocation: always 0 or 2."""
    try:
        raw = json.loads(stdin_text or "")
    except ValueError:
        return BLOCK_EXIT_STATUS, (
            "LoopX gate: unreadable preToolUse event; failing closed. "
            "Run `loopx slash-commands --install --surface kiro-cli "
            "--with-gated-agent` to refresh the hook."
        )
    tool_name = raw.get("tool_name") if isinstance(raw, dict) else None
    if _IMPORT_FAILURE is not None:
        return _refusal(
            tool_name, f"cannot load LoopX ({type(_IMPORT_FAILURE).__name__})"
        )
    try:
        event = parse_event(raw)
    except MalformedEvent as exc:
        return _refusal(tool_name, f"malformed preToolUse event ({exc})")
    try:
        decision = decide(event)
    except MalformedEvent as exc:
        return _refusal(event.tool_name, f"malformed preToolUse event ({exc})")
    except Exception as exc:  # noqa: BLE001 - any gate fault must not wave a tool through
        return _refusal(event.tool_name, f"gate error ({type(exc).__name__})")
    if decision is None or decision.verdict is not Verdict.DENY:
        return ALLOW_EXIT_STATUS, ""
    return BLOCK_EXIT_STATUS, f"LoopX gate: {decision.reason}"


def main() -> None:
    try:
        status, message = run(sys.stdin.read())
    except BaseException as exc:  # noqa: BLE001 - e.g. undecodable stdin
        status = BLOCK_EXIT_STATUS
        message = f"LoopX gate crashed ({type(exc).__name__}); failing closed."
    if status not in (ALLOW_EXIT_STATUS, BLOCK_EXIT_STATUS):
        status = BLOCK_EXIT_STATUS
    try:
        if message:
            sys.stderr.write(message + "\n")
        sys.stderr.flush()
        sys.stdout.flush()
    except BaseException:  # noqa: BLE001 - reporting must not change the verdict
        pass
    # Skip interpreter shutdown: a flush failure there would exit 120, which
    # Kiro treats as "allow".
    os._exit(status)


if __name__ == "__main__":
    main()
