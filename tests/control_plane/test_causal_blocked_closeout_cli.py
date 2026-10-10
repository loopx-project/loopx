"""Real CLI/provider causal wait closeout, not a completion or delivery credit."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.cli import main as cli_main
from test_quota_settlement_cli import (
    AGENT_ID, ALTERNATIVE_TODO_ID, GOAL_ID, TODO_ID,
    _classification_count, _configure_completion_validation_todo,
    _configure_selectable_alternative, _initialize_git_checkout, _spend_run_count, _write_fixture,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

MONITOR_ID = "todo_causal_monitor"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("kind,defer", [("monitor_changed", False), ("todo_done", False), ("todo_done", True)])
def test_pending_causal_wait_settles_once_and_releases_independent_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    provider: str, kind: str, defer: bool,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)

    def cli(*args: str, cwd: Path = project) -> tuple[int, dict]:
        # Exercise the production CLI parser/dispatch with real TS processes
        # and provider files, without paying Python cold-start on every read.
        with monkeypatch.context() as context:
            context.chdir(cwd)
            rc = cli_main(["--registry", str(registry), "--runtime-root", str(runtime),
                           "--format", "json", *args])
            return rc, json.loads(capsys.readouterr().out)

    state = _configure_completion_validation_todo(project)
    _configure_selectable_alternative(project)
    dependency_owner = AGENT_ID if kind == "monitor_changed" else "codex-dependency-peer"
    if kind == "todo_done":
        configuration = json.loads(registry.read_text())
        configuration["goals"][0]["coordination"]["registered_agents"].append(dependency_owner)
        registry.write_text(json.dumps(configuration))
        _initialize_git_checkout(project)
        subprocess.run(["git", "-c", "user.name=LoopX Test", "-c",
                        "user.email=loopx-test@example.invalid", "commit", "--quiet",
                        "--allow-empty", "-s", "-m", "causal fixture"], cwd=project, check=True)
        workspace = tmp_path / "linked-worktree"
        subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(workspace)],
                       cwd=project, check=True)
    else:
        workspace = project
    dependency_metadata = (
        "task_class=continuous_monitor target_key=causal-test cadence=6h "
        "next_due_at=2099-01-01T00:00:00Z expires_at=2099-01-08T00:00:00Z "
        "material_change_generation=0"
        if kind == "monitor_changed" else "task_class=advancement_task priority=2"
    )
    state.write_text(state.read_text() + (
        "\n- [ ] [P2] Observe a dependency change.\n"
        f"  <!-- loopx:todo todo_id={MONITOR_ID} status=open "
        f"claimed_by={dependency_owner} {dependency_metadata} -->\n"
    ))
    rc, listed = cli("todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    if defer:
        next(todo for todo in listed["todos"] if todo["todo_id"] == TODO_ID)["claimed_by"] = AGENT_ID
    initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, todos=listed["todos"], handoff_mode="legacy" if defer else "soft_claim", leases=[],
    ), state_path=state, provider=provider)
    binding = ("--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", f"causal-blocked-{kind}-{provider}")
    rc, guard = cli("quota", "should-run", "--codex-app",
                        "--goal-id", GOAL_ID, *binding, "--scan-path", str(project), cwd=project)
    assert rc == 0 and guard["should_run"] is True, guard
    rc, original = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0, original
    digest = original["todo"]["completion_validation_sha256"]
    if defer:
        # Admit the existing executable work before its dependency is installed.
        # A new lease after the causal wait would violate the completion fence.
        rc, acquired = cli("task-lease", "acquire", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
                           "--owner", AGENT_ID, "--idempotency-key", "execution-wait", "--ttl-seconds", "900")
        assert rc == 0, acquired
        rc, wait = cli("todo", "update", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
                       "--agent-id", AGENT_ID, "--resume-when", f"{kind}:{MONITOR_ID}",
                       "--successor-todo-id", ALTERNATIVE_TODO_ID,
                       "--task-lease-idempotency-key", "execution-wait",
                       "--task-lease-expected-version", str(acquired["lease"]["version"]))
        assert rc == 0, wait
        # The atomic owner-deferral accepts only unchanged work and its wait.
        # Link planning is a separate fenced edit, not part of lease retirement.
        rc, suspended = cli("todo", "update", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
                            "--agent-id", AGENT_ID, "--status", "deferred",
                            "--resume-when", f"{kind}:{MONITOR_ID}", "--reason", "Dependency pending",
                            "--task-lease-idempotency-key", "execution-wait",
                            "--task-lease-expected-version", str(acquired["lease"]["version"]))
        assert rc == 0, json.dumps(suspended, indent=2)
        assert suspended["deferred_transition"]["lease_retirement"] == "released"
        rc, lease = cli("task-lease", "inspect", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
        assert rc == 0 and lease["lease"]["status"] == "released", lease
    else:
        rc, wait = cli("todo", "update", "--goal-id", GOAL_ID,
                       "--todo-id", TODO_ID, "--agent-id", AGENT_ID,
                       "--resume-when", f"{kind}:{MONITOR_ID}",
                       "--successor-todo-id", ALTERNATIVE_TODO_ID)
        assert rc == 0, wait
    refresh_args = ("refresh-state", "--goal-id", GOAL_ID,
                    "--classification", "causal_wait_writeback", "--delivery-batch-scale", "single_surface",
                    "--delivery-outcome", "outcome_gap", *binding,
                    "--progress-result-class", "blocked", "--progress-blocker-id", MONITOR_ID,
                    "--progress-evidence-id", "evidence:registered-wait",
                    "--delivery-workspace-path", str(workspace),
                    "--no-global-sync", "--suppress-external-sinks")
    rc, refresh = cli(*refresh_args, cwd=project)
    assert rc == 0, json.dumps(refresh, indent=2)
    assert refresh["blocked_retry"]["schema_version"] == "quota_blocked_causal_wait_v0"
    assert refresh["settlement_progress"]["state"] == "settled"
    assert refresh["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
    assert [r["step_kind"] for r in refresh["settlement_result"]["receipts"]] == ["validation", "durable_writeback"]
    rc, replay = cli(*refresh_args, cwd=project)
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    assert replay["blocked_retry"] == refresh["blocked_retry"]
    assert _classification_count(runtime, "causal_wait_writeback") == 1
    rc, spend = cli("quota", "spend-slot", "--goal-id", GOAL_ID,
                        "--slots", "1", "--source", "heartbeat", "--execute", *binding,
                        "--scan-path", str(project), cwd=project)
    assert rc == 0 and spend["appended"] is False, spend
    assert _spend_run_count(runtime) == 0
    rc, after = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0, after
    todo = after["todo"]
    assert todo["status"] == ("deferred" if defer else "open") and todo["resume_ready"] is False
    assert todo["resume_when"] == f"{kind}:{MONITOR_ID}"
    assert todo["completion_validation_sha256"] == digest
    rc, next_turn = cli("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
                            "--agent-id", AGENT_ID, "--turn-instance-id", f"after-causal-{provider}",
                            "--scan-path", str(project), cwd=project)
    assert rc == 0, next_turn
    assert next_turn["effective_action"] != "unsettled_host_turn_recovery"
    assert next_turn["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    # No continuous monitor poll was needed or forced by the advancement closeout.
    assert _classification_count(runtime, "quota_monitor_poll") == 0
