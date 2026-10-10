"""Host-neutral per-tool-call gate for goal-mode hosts.

A host adapter (Claude Code ``PreToolUse``, Kiro CLI ``preToolUse``) maps its
own event onto one typed :class:`ToolCall` and asks :func:`decide_tool_call`
for a verdict. The decision owner stays ``loopx quota should-run``; this module
only turns its boolean into a per-tool verdict, so the rule lives in one place
instead of being copied into every host hook:

- read-only tools are allowed before the gate is consulted;
- ``should_run == false`` denies every other tool;
- an unavailable probe (``None``) also denies it — the gate fails closed;
- when the gate is open, file writes are confined to the goal's write scope,
  shell commands are screened against a destructive-command denylist, and
  anything else defers to the host's own permission flow.

This is a deterministic policy layer, not a sandbox. The shell denylist is a
substring screen kept only as defense in depth for the most obviously
destructive commands; it misses equivalents (for example ``find -delete``) and
can match harmless text, which is why a shell command inside the gate still
reaches the host's permission flow and the docs point untrusted work at a
container or VM.

Stdlib only: host hooks run under whatever interpreter the host launches.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class ToolKind(str, Enum):
    # Cannot deliver work: always allowed, before the gate is consulted.
    READ_ONLY = "read_only"
    # Writes one file path: gated, then confined to the goal's write scope.
    FILE_WRITE = "file_write"
    # Runs a shell command: gated, then screened by the destructive denylist.
    SHELL = "shell"
    # Anything else (subagents, cloud calls, other MCP tools): gated, then
    # deferred to the host's own permission flow.
    OTHER = "other"


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    # No LoopX opinion: the host's normal permission flow decides.
    DEFER = "defer"


@dataclass(frozen=True)
class ToolCall:
    kind: ToolKind
    write_path: str = ""
    command: str = ""


@dataclass(frozen=True)
class GateDecision:
    verdict: Verdict
    reason: str


ShouldRunProbe = Callable[[], "bool | None"]

DESTRUCTIVE_SHELL_TOKENS = (
    "rm -rf", "rm -fr", "mkfs", "dd if=", ":(){", "shutdown", "reboot",
    "git push --force", "git reset --hard", "> /dev/sd", "format ",
)


def within(path: str, root: str, *, base: str | None = None) -> bool:
    """True if ``path`` resolves to ``root`` or a descendant of it.

    A relative ``path`` is resolved against ``base`` (the host's working
    directory) rather than this process's, because hooks are not guaranteed
    to run in the directory the tool call targets.
    """
    try:
        candidate = Path(path)
        if not candidate.is_absolute() and base:
            candidate = Path(base) / candidate
        resolved = candidate.resolve()
        resolved_root = Path(root).resolve()
        return resolved_root == resolved or resolved_root in resolved.parents
    except (OSError, RuntimeError, ValueError):
        return False


def runtime_profile_flag_is_unsupported(
    result: subprocess.CompletedProcess[str],
) -> bool:
    """An older CLI that predates ``--runtime-profile`` rejected the flag."""
    diagnostic = str(result.stderr or "").lower()
    return (
        result.returncode == 2
        and "--runtime-profile" in diagnostic
        and (
            "unrecognized arguments" in diagnostic
            or "invalid choice" in diagnostic
        )
    )


def probe_should_run(
    *,
    command_prefix: Sequence[str],
    registry: str | None,
    goal_id: str | None,
    agent_id: str | None,
    runtime_profile: str,
    legacy_scheduler_args: Sequence[str] | None = None,
    timeout_seconds: float = 10,
    env: Mapping[str, str] | None = None,
) -> bool | None:
    """``should_run`` from ``loopx quota should-run``, or None when unknown.

    None covers every way the answer can be missing — no goal, a launch
    failure, a timeout, unparseable output, or a non-boolean field — so the
    caller can fail closed on one value instead of guessing.
    """
    if not goal_id:
        return None
    command = list(command_prefix)
    if registry:
        command += ["--registry", registry]
    command += ["--format", "json", "quota", "should-run", "--goal-id", goal_id]
    if agent_id:
        command += ["--agent-id", agent_id]
    options: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout_seconds,
    }
    if env is not None:
        options["env"] = dict(env)
    try:
        result = subprocess.run(
            [*command, "--runtime-profile", runtime_profile], **options
        )
        if legacy_scheduler_args and runtime_profile_flag_is_unsupported(result):
            result = subprocess.run([*command, *legacy_scheduler_args], **options)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("should_run")
    return value if isinstance(value, bool) else None


def decide_tool_call(
    call: ToolCall,
    *,
    goal_id: str | None,
    write_scope: Sequence[str],
    should_run: ShouldRunProbe,
    cwd: str | None = None,
) -> GateDecision:
    """The per-tool verdict for one call under an armed goal."""
    if call.kind is ToolKind.READ_ONLY:
        return GateDecision(Verdict.ALLOW, "read-only/safe tool under goal-mode")

    gate = should_run()
    if gate is False:
        return GateDecision(
            Verdict.DENY, f"goal '{goal_id}' should_run=false (quota/gate closed)"
        )
    if gate is not True:
        return GateDecision(
            Verdict.DENY,
            "loopx should_run probe unavailable — failing closed under goal-mode",
        )

    if call.kind is ToolKind.FILE_WRITE:
        scope = list(write_scope)
        if scope and not any(within(call.write_path, root, base=cwd) for root in scope):
            return GateDecision(
                Verdict.DENY, f"'{call.write_path}' outside goal write_scope {scope}"
            )
        return GateDecision(Verdict.ALLOW, "write within goal scope")
    if call.kind is ToolKind.SHELL:
        lowered = call.command.lower()
        if any(token in lowered for token in DESTRUCTIVE_SHELL_TOKENS):
            return GateDecision(
                Verdict.DENY, "destructive command blocked by goal policy"
            )
        return GateDecision(Verdict.ALLOW, "bash permitted under goal policy")
    return GateDecision(Verdict.DEFER, "gate open; host permission flow decides")
