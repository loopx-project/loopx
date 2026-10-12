#!/usr/bin/env python3
"""Smoke-test the optional --harden PreToolUse policy gate (goal_policy.decide).

Focus (review P1): `Task` must NOT be unconditionally allowed — it can spawn a
subagent that performs writes, so it must go through the gate. When
should_run=false it is denied; when should_run=true it defers to Claude Code's
normal permission flow (it is not auto-allowed). The test also pins the rest of
the documented boundary so the README and code stay in sync: read-only allowed
before the gate, Edit/Write confined to write_scope, Bash gated by a destructive
denylist, unknown tools deferred.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "loopx" / "claude_goal_mode" / "hooks"))
import goal_policy  # noqa: E402

CTX = {"goal_id": "g", "registry": "/r", "agent_id": "cc",
       "write_scope": ["/proj"], "project_root": "/proj"}


def decision(tool, sr, **tool_input):
    """Run goal_policy.decide for `tool` with a fixed armed context and a forced
    should_run value; return the permissionDecision ('allow'/'deny') or None=defer."""
    goal_policy.active_context = lambda cwd: CTX
    goal_policy.should_run = lambda *a, **k: sr
    ev = {"cwd": "/proj", "tool_name": tool, "tool_input": tool_input}
    return goal_policy.decide(ev).get("hookSpecificOutput", {}).get("permissionDecision")


def main() -> int:
    commands = []
    real_run = goal_policy.subprocess.run
    real_prefix = goal_policy._gh_prefix
    goal_policy._gh_prefix = lambda: ["loopx"]

    def capture_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"should_run": True}), ""
        )

    goal_policy.subprocess.run = capture_run
    try:
        assert goal_policy.should_run("/r", "g", "cc") is True
    finally:
        goal_policy.subprocess.run = real_run
        goal_policy._gh_prefix = real_prefix
    assert commands == [
        [
            "loopx", "--registry", "/r", "--format", "json", "quota",
            "should-run", "--goal-id", "g", "--agent-id", "cc",
            "--runtime-profile", "claude_code",
        ]
    ], commands

    commands.clear()

    def capture_legacy_fallback(command, **_kwargs):
        commands.append(command)
        if "--runtime-profile" in command:
            return subprocess.CompletedProcess(
                command,
                2,
                "",
                "loopx: error: unrecognized arguments: --runtime-profile claude_code",
            )
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"should_run": True}), ""
        )

    goal_policy._gh_prefix = lambda: ["loopx"]
    goal_policy.subprocess.run = capture_legacy_fallback
    try:
        assert goal_policy.should_run("/r", "g", "cc") is True
    finally:
        goal_policy.subprocess.run = real_run
        goal_policy._gh_prefix = real_prefix
    assert len(commands) == 2, commands
    assert commands[0][-2:] == ["--runtime-profile", "claude_code"], commands
    assert commands[1][-6:] == [
        "--host-surface", "claude_code",
        "--scheduler-owner", "agent_cli_loop",
        "--execution-mode", "interactive",
    ], commands

    commands.clear()

    def capture_health_failure(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            1,
            json.dumps({"should_run": False, "status": "quota_collection_failed"}),
            "",
        )

    goal_policy._gh_prefix = lambda: ["loopx"]
    goal_policy.subprocess.run = capture_health_failure
    try:
        assert goal_policy.should_run("/r", "g", "cc") is False
    finally:
        goal_policy.subprocess.run = real_run
        goal_policy._gh_prefix = real_prefix
    assert len(commands) == 1, commands
    assert commands[0][-2:] == ["--runtime-profile", "claude_code"], commands

    # should_run == False: the gate is closed for everything except read-only.
    assert decision("Task", False, description="x", prompt="y") == "deny", \
        "Task must be DENIED when should_run=false (it can spawn a writing subagent)"
    assert decision("Write", False, file_path="/proj/a.txt") == "deny", "write denied when gate closed"
    assert decision("Read", False, file_path="/anywhere") == "allow", "read-only stays allowed"

    # should_run == True: Task is NOT auto-allowed; it defers to normal flow.
    assert decision("Task", True, description="x", prompt="y") is None, \
        "Task must DEFER (not be unconditionally allowed) when should_run=true"
    assert decision("Write", True, file_path="/proj/a.txt") == "allow", "in-scope write allowed"
    assert decision("Write", True, file_path="/etc/passwd") == "deny", "out-of-scope write denied"
    assert decision("Bash", True, command="rm -rf /") == "deny", "destructive bash denied"
    assert decision("Bash", True, command="make test") == "allow", "non-destructive bash allowed"
    assert decision("Read", True, file_path="/anywhere") == "allow", "read-only allowed"

    # An event with no session cwd (what an unreadable event becomes in main)
    # cannot be attributed to an armed project, so the gate must refuse it
    # rather than defer: rc=1 with no decision is a non-blocking hook error to
    # Claude Code, which then runs the tool with no gate at all.
    assert goal_policy.decide({}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny", \
        "a cwd-less/unreadable event must be DENIED, not deferred"

    # A non-read-only tool whose tool_input is not an object is unreadable the
    # same way: decide must refuse it rather than crash on `ti.get` (rc=1 with
    # no decision, which Claude Code reports and then runs the tool ungated).
    goal_policy.active_context = lambda cwd: CTX
    goal_policy.should_run = lambda *a, **k: True
    non_object_input = goal_policy.decide(
        {"cwd": "/proj", "tool_name": "Bash", "tool_input": "not-an-object"})
    assert non_object_input.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", \
        "a non-object tool_input must be DENIED, not crash the gate (rc=1 fail-open)"
    assert goal_policy.decide(
        {"cwd": "/proj", "tool_name": "Read", "tool_input": "not-an-object"}
    ).get("hookSpecificOutput", {}).get("permissionDecision") == "allow", \
        "read-only tools keep their unconditional allow"

    # A decoded cwd can still be unusable as a path: an embedded NUL raises
    # ValueError inside active_context, which would escape decide as the same
    # rc=1/no-decision fail-open. The gate must refuse it instead.
    assert goal_policy.decide(
        {"cwd": "/bad\u0000cwd", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    ).get("hookSpecificOutput", {}).get("permissionDecision") == "deny", \
        "a session cwd that cannot be resolved as a path must be DENIED"

    # `tool_input` is type-checked before any default: only absent/null gets {};
    # a present falsy non-object (`[]`, `false`, `0`, `""`) is unreadable and
    # refused, so an open gate cannot allow it as if it carried no input.
    for falsy_input in ([], False, 0, ""):
        assert goal_policy.decide(
            {"cwd": "/proj", "tool_name": "Bash", "tool_input": falsy_input}
        ).get("hookSpecificOutput", {}).get("permissionDecision") == "deny", \
            f"falsy non-object tool_input {falsy_input!r} must be DENIED, not coerced"
    assert goal_policy.decide(
        {"cwd": "/proj", "tool_name": "Bash"}
    ).get("hookSpecificOutput", {}).get("permissionDecision") == "allow", \
        "an absent tool_input keeps the documented empty-input default"

    # A decoded value the gate cannot use is refused the same way instead of
    # crashing on `.lower()` / set membership or silently matching the scope.
    assert goal_policy.decide(
        {"cwd": "/proj", "tool_name": ["Bash"], "tool_input": {}}
    ).get("hookSpecificOutput", {}).get("permissionDecision") == "deny", \
        "a non-string tool_name must be DENIED, not crash the gate"
    assert goal_policy.decide(
        {"cwd": "/proj", "tool_name": "Bash", "tool_input": {"command": ["rm", "-rf", "/"]}}
    ).get("hookSpecificOutput", {}).get("permissionDecision") == "deny", \
        "a non-string command must be DENIED, not crash the gate"

    print("claude-goalmode-hook-policy-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
