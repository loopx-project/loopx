#!/usr/bin/env python3
"""Release-only Claude Code + Doubao work-loop qualification, never a default live test."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location("goal_release", REPO / "scripts/qualify-native-goal-release.py")
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)

from loopx.control_plane.testing.doubao_model_behavior_actor import (  # noqa: E402
    DOUBAO_SEED_EVOLVING_MODEL,
)

ARK_ANTHROPIC_BASE = "https://ark.cn-beijing.volces.com/api/compatible"
MANIFEST_ASSETS = {"assets/alpha.txt": b"alpha\n", "assets/nested/beta.bin": bytes(range(256))}


def _replan_active_state() -> str:
    return (
        "---\nstatus: active\n---\n\n## Objective\n\nDeliver and validate TASK.md.\n\n"
        "## Next Action\n\nVerify artifact integrity, beyond the completed filename inventory.\n\n"
        "## User Todo\n\n## Agent Todo\n\n"
        "- [ ] [P1] Inventory the regular asset filenames in manifest.json.\n"
        "  <!-- loopx:todo todo_id=todo_inventory status=open task_class=advancement_task "
        "action_kind=implement claimed_by=worker-a task_repository=git:example.com/ledger -->\n"
    )


def run_host(command: list[str], *, cwd: Path, env: dict, timeout: float) -> str:
    """Bound output memory and clean this test's process group, including on timeout."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        assert code == 0, "claude_host_failed"
        stdout.seek(0)
        output = stdout.read(32 * 1024 * 1024 + 1)
        assert len(output) <= 32 * 1024 * 1024, "claude_output_exceeded_limit"
        return output.decode("utf-8")


def prerequisite_failure(claude: str) -> str | None:
    if not all(shutil.which(command) for command in (claude, "node", "git")):
        return "required_executable_unavailable"
    if not os.environ.get("ARK_API_KEY"):
        return "ark_api_key_unavailable"
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError:
        return "mcp_sdk_v1_unavailable"
    return None


def host_environment(root: Path, launcher: Path) -> dict[str, str]:
    # No user settings, OAuth/keychain import or persistent host installation.
    env = shared.host_environment(root, launcher)
    env.update(
        ANTHROPIC_API_KEY=os.environ["ARK_API_KEY"],
        ANTHROPIC_BASE_URL=ARK_ANTHROPIC_BASE,
        ANTHROPIC_MODEL=DOUBAO_SEED_EVOLVING_MODEL,
        ANTHROPIC_DEFAULT_HAIKU_MODEL=DOUBAO_SEED_EVOLVING_MODEL,
        ANTHROPIC_DEFAULT_SONNET_MODEL=DOUBAO_SEED_EVOLVING_MODEL,
        ANTHROPIC_DEFAULT_OPUS_MODEL=DOUBAO_SEED_EVOLVING_MODEL,
        CLAUDE_CONFIG_DIR=str(root / "claude-config"),
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        PATH=str(launcher.parent) + os.pathsep + os.environ.get("PATH", ""),
        PYTHONPATH=str(REPO),
    )
    return env


def verify_mcp_completions(events: list[dict], expected_todos: set[str] | None = None) -> None:
    """A tool invocation is not evidence that the MCP transaction succeeded."""
    pending: dict[str, str] = {}
    completed: set[str] = set()
    for event in events:
        for block in (event.get("message") or {}).get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "mcp__loopx__complete_task":
                pending[block["id"]] = (block.get("input") or {}).get("todo_id")
            if block.get("type") != "tool_result" or block.get("tool_use_id") not in pending:
                continue
            content = block.get("content")
            if isinstance(content, list):
                content = "".join(item.get("text", "") for item in content if item.get("type") == "text")
            try:
                payload = json.loads(content)
                if isinstance(payload, dict) and isinstance(payload.get("result"), str):
                    payload = json.loads(payload["result"])
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                continue
            todo_id = pending[block["tool_use_id"]]
            assert payload.get("todo_id") == todo_id and payload.get("completed") is True
            assert (payload.get("settlement") or {}).get("ok") is True
            completed.add(todo_id)
    assert completed == (shared.TODOS if expected_todos is None else expected_todos), "mcp_delivery_transactions_not_completed"


def exercise_host(root: Path, project: Path, launcher: Path, claude: str, timeout: int) -> list[dict]:
    from loopx.claude_goal_mode.scripts.goalmode_cmd import write_loop_md

    write_loop_md(project, shared.GOAL, shared.AGENT)
    config = root / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"loopx": {
        "command": sys.executable,
        "args": [str(REPO / "loopx/claude_goal_mode/mcp/loopx_mcp.py")],
    }}}))
    command = [
        claude, "--bare", "--setting-sources", "", "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", str(config),
        "--model", DOUBAO_SEED_EVOLVING_MODEL,
        "--permission-mode", "dontAsk", "--allowedTools",
        "Read", "Edit", "Write", "Bash", "mcp__loopx__*",
        "--output-format", "stream-json", "--verbose", "-p",
        "Read .claude/loop.md and follow this project's active LoopX work contract. "
        "Read TASK.md for acceptance. Use the bound LoopX MCP tools; preserve the "
        "isolated project binding. Do not create timers in this headless qualification. "
        "Use product contracts, not qualification scripts or their external acceptance oracles.",
    ]
    # This executes the actual per-iteration adapter, not Claude's interactive
    # /loop timer. Never report headless delivery as scheduler qualification.
    output = run_host(command, cwd=project, env=host_environment(root, launcher), timeout=timeout)
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    results = [e for e in events if e.get("type") == "result"]
    assert len(results) == 1 and results[0].get("is_error") is False, "claude_turn_failed"
    return events


def qualify(root: Path, claude: str, timeout: int) -> dict:
    project, runtime, launcher = shared.setup(root)
    events = exercise_host(root, project, launcher, claude, timeout)
    calls = [block.get("name") for event in events if event.get("type") == "assistant"
             for block in (event.get("message") or {}).get("content", [])
             if block.get("type") == "tool_use"]
    assert "mcp__loopx__should_run" in calls and "mcp__loopx__complete_task" in calls, "mcp_not_exercised"
    verify_mcp_completions(events)
    return {"status": "passed", "model_executed": True, "model": DOUBAO_SEED_EVOLVING_MODEL,
            "host": "claude_code", "scheduler_qualification": "not_run_headless",
            "mcp_tool_calls": sum(str(c).startswith("mcp__loopx__") for c in calls),
            **shared.verify_delivery(project, runtime, launcher, "claude_code")}


def setup_replan(root: Path) -> tuple[Path, Path, Path]:
    """A legitimately finished inventory stage, not fabricated Goal acceptance."""
    from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane

    project, runtime, launcher = shared.setup(
        root,
        active_state_text=_replan_active_state(),
    )
    assets = project / "assets"
    for name, content in MANIFEST_ASSETS.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (project / "manifest.json").write_text(json.dumps(["assets/alpha.txt", "assets/nested/beta.bin"]))
    shutil.copyfile(REPO / "tests/fixtures/host_vision_replan/TASK.md", project / "TASK.md")
    assert json.loads((project / "manifest.json").read_text()) == sorted(
        str(path.relative_to(project)) for path in assets.rglob("*") if path.is_file())
    control = GoalModeMCPControlPlane(GoalModeMCPConfig(server_name="fixture",
        runtime_profile="claude_code", legacy_host_surface="claude_code"),
        lambda: {"goal_id": shared.GOAL, "agent_id": shared.AGENT})
    control.command_prefix = lambda: [str(launcher)]
    previous = Path.cwd()
    try:
        os.chdir(project)
        completed = json.loads(control.complete_task("todo_inventory", shared.AGENT,
            "Filename inventory matches both regular asset files; integrity delivery remains unverified.",
            no_follow_up=True, agent_vision={
                "schema_version": "goal_vision_replan_contract_v0", "state": "vision_closed",
                "vision_patch": {
                    "vision_summary": "Inventory regular asset filenames.",
                    "acceptance_summary": "Both filenames match the actual asset directory.",
                    "last_patch_summary": "Filename inventory stage verified; full integrity acceptance remains.",
                },
            }))
    finally:
        os.chdir(previous)
    assert completed["ok"] is True, "inventory_stage_not_settled"
    return project, runtime, launcher


def verify_manifest_delivery(project: Path) -> None:
    """Independent acceptance, including destructive-input *copies* and no self-repair."""
    import hashlib

    observed = {str(path.relative_to(project)): path.read_bytes()
        for path in (project / "assets").rglob("*") if path.is_file()}
    assert observed == MANIFEST_ASSETS, "manifest_inputs_modified"
    expected = {name: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in MANIFEST_ASSETS.items()}
    assert json.loads((project / "manifest.json").read_text()) == expected, "manifest_acceptance_failed"
    assert (project / "README.md").is_file(), "manifest_readme_missing"
    for mutation in ("none", "changed", "missing", "additional"):
        with tempfile.TemporaryDirectory(prefix="loopx-manifest-oracle-") as raw:
            copy = Path(raw)
            shutil.copytree(project / "assets", copy / "assets")
            for name in ("manifest.json", "verify_manifest.py"):
                shutil.copyfile(project / name, copy / name)
            if mutation == "changed":
                (copy / "assets/alpha.txt").write_text("corrupted")
            elif mutation == "missing":
                (copy / "assets/alpha.txt").unlink()
            elif mutation == "additional":
                (copy / "assets/additional.txt").write_text("extra")
            before = {str(path.relative_to(copy)): path.read_bytes() for path in copy.rglob("*") if path.is_file()}
            result = subprocess.run([sys.executable, "-B", "verify_manifest.py"], cwd=copy,
                                    capture_output=True, timeout=30)
            assert (result.returncode == 0) is (mutation == "none"), "manifest_verifier_unsound"
            after = {str(path.relative_to(copy)): path.read_bytes() for path in copy.rglob("*") if path.is_file()}
            assert before == after, "manifest_verifier_mutated_evidence"


def qualify_replan(root: Path, claude: str, timeout: int) -> dict:
    project, runtime, launcher = setup_replan(root)
    before = shared.cli(launcher, "quota", "should-run", "--goal-id", shared.GOAL,
        "--agent-id", shared.AGENT, "--runtime-profile", "claude_code")
    assert before["should_run"] is True, "finished_stage_must_not_hide_goal_gap"
    events = exercise_host(root, project, launcher, claude, timeout)
    return verify_replan_delivery(project, runtime, launcher, events)


def verify_replan_delivery(project: Path, runtime: Path, launcher: Path, events: list[dict]) -> dict:
    """Prove gap -> delivered successor -> scoped vision decision, not a magic label."""
    verify_manifest_delivery(project)
    assert (project / "TASK.md").read_bytes() == (REPO / "tests/fixtures/host_vision_replan/TASK.md").read_bytes()
    todos = shared.cli(launcher, "todo", "list", "--goal-id", shared.GOAL, "--role", "agent")["todos"]
    successors = [t for t in todos if t["todo_id"] != "todo_inventory"]
    assert successors and all(t["status"] == "done" for t in todos), "replan_must_deliver_concrete_successor"
    assert all(t["task_class"] == "advancement_task" for t in successors)
    verify_mcp_completions(events, {t["todo_id"] for t in successors})
    rows = [json.loads(line) for line in (runtime / "goals" / shared.GOAL / "runs/index.jsonl").read_text().splitlines()]
    visions = [r["agent_vision"] for r in rows if isinstance(r.get("agent_vision"), dict)
               and r.get("todo_id") != "todo_inventory"]
    # Replan is an operation, not a mandatory final path disposition. After
    # actual successor delivery, no_followup + stop is valid scoped closure.
    # It cannot shortcut this oracle's independent artifact/successor checks.
    assert any(v.get("path_delta", {}).get("outcome") == "replan" or
               (v.get("state") == "no_followup" and v.get("path_delta", {}).get("outcome") == "stop")
               for v in visions), "no_successor_vision_decision"
    spent = [r for r in rows if r.get("classification") == "quota_slot_spent"]
    effects = [r["settlement_identity"]["effect_id"] for r in spent]
    assert len(effects) == len(set(effects)), "duplicate_replan_spend"
    for todo in todos:
        assert sum(r.get("todo_id") == todo["todo_id"] for r in spent) == 1, "todo_completion_spend_not_exactly_once"
    shared.verify_spend_receipts(runtime, spent)
    final = shared.cli(launcher, "quota", "should-run", "--goal-id", shared.GOAL,
        "--agent-id", shared.AGENT, "--runtime-profile", "claude_code")
    assert final["interaction_contract"]["mode"] == "terminal_no_followup"
    return {"status": "passed", "host": "claude_code", "model_executed": True,
        "model": DOUBAO_SEED_EVOLVING_MODEL, "scenario": "replan", "successor_count": len(successors),
        "settled_spends": len(spent), "host_events": len(events), "independent_acceptance": "passed",
        "scheduler_qualification": "not_run_headless"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-live", action="store_true")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--scenario", choices=("delivery", "replan"), default="delivery")
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("timeout must be positive")
    if not args.release_live:
        result = {"status": "skipped", "reason": "release_opt_in_required", "model_executed": False}
    else:
        try:
            reason = prerequisite_failure(args.claude_bin)
            if reason:
                result = {"status": "skipped", "reason": reason, "model_executed": False}
            else:
                with tempfile.TemporaryDirectory(prefix="loopx-claude-release-") as raw:
                    qualification = qualify_replan if args.scenario == "replan" else qualify
                    result = qualification(Path(raw), args.claude_bin, args.timeout_seconds)
        except Exception as exc:
            result = {"status": "failed", "error_kind": type(exc).__name__}
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
