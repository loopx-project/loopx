#!/usr/bin/env python3
"""Smoke-test two Claude-adapter goal-mode lifecycle fixes.

1. The active-goal resolver must pick the goal `/loopx` ARMED for the project
   (persisted in `.claude/loop.md`'s `loopx:armed` marker), not the first goal in
   registry order. Otherwise the hook/statusline/MCP gate/claim/complete the wrong
   goal in a multi-goal repo.

2. `complete_task(next_agent_todo=...)` must hand the next todo to a REGISTERED
   agent. `/loopx` registers `cc`; the old code hard-coded `--next-claimed-by
   cc-controller`, which is unregistered and breaks the complete+create-next path.
   LoopX assigns the next todo to the completing agent when `--next-claimed-by` is
   omitted, so the fix is to stop hard-coding it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "loopx" / "claude_goal_mode" / "hooks"))
import goal_state  # noqa: E402
from loopx import goal_mode_mcp  # noqa: E402
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane  # noqa: E402


def loopx(args, home=None, **kw):
    # Isolate HOME so the global runtime root (~/.loopx) is a temp dir — the
    # test never reads or pollutes the real global registry, and re-runs don't
    # collide on goal ids.
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run([sys.executable, "-m", "loopx.cli", *args],
                          capture_output=True, text=True, env=env, **kw)


def test_goal_context_prefers_armed_goal():
    with tempfile.TemporaryDirectory(prefix="loopx-armed-") as d:
        root = Path(d)
        (root / ".loopx").mkdir()
        # Two goals share peer registration; the armed marker, not registry order,
        # first (goal-A). The armed marker points at goal-B.
        registry = {
            "schema_version": "0.1",
            "goals": [
                {"id": "goal-A", "repo": str(root),
                 "coordination": {"agent_model": "peer_v1", "registered_agents": ["agentA"]}},
                {"id": "goal-B", "repo": str(root),
                 "coordination": {"agent_model": "peer_v1", "registered_agents": ["agentB"]}},
            ],
        }
        (root / ".loopx" / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

        # not armed yet -> falls back to first-with-primary (goal-A)
        ctx = goal_state.goal_context(root)
        assert ctx and ctx["goal_id"] == "goal-A", f"unarmed should fall back to goal-A, got {ctx}"

        # arm goal-B exactly as /loopx does (marker at the top of .claude/loop.md)
        (root / ".claude").mkdir()
        marker = json.dumps({"goal_id": "goal-B", "agent_id": "cc"})
        (root / ".claude" / "loop.md").write_text(f"<!-- loopx:armed {marker} -->\nloopx tick ...\n", encoding="utf-8")

        ctx = goal_state.goal_context(root)
        assert ctx["goal_id"] == "goal-B", f"armed goal must win, got {ctx['goal_id']}"
        assert ctx["agent_id"] == "cc", f"armed agent must win, got {ctx['agent_id']}"

        # a stale marker (goal not in registry) must not break -> fall back
        bad = json.dumps({"goal_id": "goal-GONE", "agent_id": "cc"})
        (root / ".claude" / "loop.md").write_text(f"<!-- loopx:armed {bad} -->\n", encoding="utf-8")
        ctx = goal_state.goal_context(root)
        assert ctx["goal_id"] == "goal-A", f"stale marker should fall back to goal-A, got {ctx['goal_id']}"
        assert ctx["agent_id"] == "agentA", f"stale marker agent must not leak, got {ctx['agent_id']}"


def test_complete_with_next_todo_uses_registered_agent():
    with tempfile.TemporaryDirectory(prefix="loopx-next-") as d:
        home = Path(d) / "home"
        home.mkdir()
        proj = Path(d) / "proj"
        proj.mkdir()
        gid = "cc-lifecycle"
        state_file = f".claude/goals/{gid}/ACTIVE_GOAL_STATE.md"
        r = loopx(["bootstrap", "--project", str(proj), "--goal-id", gid,
                   "--objective", "lifecycle smoke", "--state-file", state_file], home=home)
        assert r.returncode == 0, f"bootstrap failed:\n{r.stdout}\n{r.stderr}"
        registry = str(proj / ".loopx" / "registry.json")
        # /loopx registers only `cc` (primary + registered)
        r = loopx(["--registry", registry, "configure-goal", "--goal-id", gid,
                   "--agent-model", "peer_v1", "--registered-agent", "cc", "--execute"], home=home)
        assert r.returncode == 0, f"configure-goal failed:\n{r.stdout}\n{r.stderr}"
        add = loopx(["--registry", registry, "--format", "json", "todo", "add",
                     "--goal-id", gid, "--role", "agent", "--text", "first segment"], home=home)
        assert add.returncode == 0, f"todo add failed:\n{add.stdout}\n{add.stderr}"
        todo_id = json.loads(add.stdout)["todo_id"]
        r = loopx(["--registry", registry, "todo", "claim", "--goal-id", gid,
                   "--todo-id", todo_id, "--claimed-by", "cc"], home=home)
        assert r.returncode == 0, f"claim failed:\n{r.stdout}\n{r.stderr}"

        # The exact shape loopx_mcp.complete_task now runs: --next-agent-todo and
        # NO --next-claimed-by. This is the maintainer's repro that used to fail.
        comp = loopx(["--registry", registry, "todo", "complete", "--goal-id", gid,
                      "--todo-id", todo_id, "--claimed-by", "cc", "--evidence", "ran build; pass",
                      "--next-agent-todo", "second segment"], home=home)
        assert comp.returncode == 0, f"complete+next failed:\n{comp.stdout}\n{comp.stderr}"
        blob = (comp.stdout + comp.stderr).lower()
        assert "not registered" not in blob, f"unexpected registration error:\n{comp.stdout}\n{comp.stderr}"
        assert "cc-controller" not in blob, f"must not reference cc-controller:\n{comp.stdout}\n{comp.stderr}"

        # the new todo exists and is NOT owned by an unregistered controller id.
        # (LoopX assigns next_claimed_by = the completing agent `cc` when omitted.)
        state = (proj / state_file).read_text(encoding="utf-8")
        assert "second segment" in state, f"next todo not created:\n{state}"
        assert "cc-controller" not in state, f"next todo must not reference cc-controller:\n{state}"


def test_mcp_should_run_prefers_profile_and_falls_back_for_old_cli():
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {"goal_id": "g", "registry": "/r", "agent_id": "cc"},
    )
    commands = []
    control.command_prefix = lambda: ["loopx"]

    def capture_run(command, **_kwargs):
        commands.append(command)
        if "--runtime-profile" in command:
            return subprocess.CompletedProcess(
                command,
                2,
                "",
                "loopx: error: argument --runtime-profile: invalid choice: "
                "'claude_code' (choose from 'codex_app_heartbeat')",
            )
        return subprocess.CompletedProcess(command, 0, '{"should_run": true}', "")

    goal_mode_mcp.subprocess.run = capture_run

    assert control.should_run() == '{"should_run": true}'
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
            '{"should_run": false, "status": "quota_collection_failed"}',
            "",
        )

    goal_mode_mcp.subprocess.run = capture_health_failure
    assert control.should_run().strip() == (
        '{"should_run": false, "status": "quota_collection_failed"}'
    )
    assert len(commands) == 1, commands
    assert commands[0][-2:] == ["--runtime-profile", "claude_code"], commands


def main() -> int:
    test_goal_context_prefers_armed_goal()
    test_complete_with_next_todo_uses_registered_agent()
    test_mcp_should_run_prefers_profile_and_falls_back_for_old_cli()
    print("claude-goalmode-lifecycle-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
