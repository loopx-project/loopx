"""A same-Turn pending wait must expose closeout, not successor authority."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.cli import main as cli_main
from control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    ALTERNATIVE_TODO_ID,
    GOAL_ID,
    TODO_ID,
    _classification_count,
    _configure_completion_validation_todo,
    _configure_selectable_alternative,
    _initialize_git_checkout,
    _spend_run_count,
    _write_fixture,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)

MONITOR_ID = "todo_causal_monitor"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("kind", ["monitor_changed", "todo_done"])
def test_bound_wait_recovers_original_turn_before_selecting_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
    kind: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)

    def cli(*args: str, cwd: Path = project) -> tuple[int, dict]:
        # Exercise the production CLI parser/dispatch with real TS processes
        # and provider files, without paying Python cold-start on every read.
        with monkeypatch.context() as context:
            context.chdir(cwd)
            rc = cli_main(
                [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    *args,
                ]
            )
            return rc, json.loads(capsys.readouterr().out)

    state = _configure_completion_validation_todo(project)
    _configure_selectable_alternative(project)
    dependency_owner = (
        AGENT_ID if kind == "monitor_changed" else "codex-dependency-peer"
    )
    if kind == "todo_done":
        configuration = json.loads(registry.read_text())
        configuration["goals"][0]["coordination"]["registered_agents"].append(
            dependency_owner
        )
        registry.write_text(json.dumps(configuration))
        _initialize_git_checkout(project)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=LoopX Test",
                "-c",
                "user.email=loopx-test@example.invalid",
                "commit",
                "--quiet",
                "--allow-empty",
                "-s",
                "-m",
                "causal fixture",
            ],
            cwd=project,
            check=True,
        )
        workspace = tmp_path / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", "--quiet", "--detach", str(workspace)],
            cwd=project,
            check=True,
        )
    else:
        workspace = project
    dependency_metadata = (
        "task_class=continuous_monitor target_key=causal-test cadence=6h "
        "next_due_at=2099-01-01T00:00:00Z expires_at=2099-01-08T00:00:00Z "
        "material_change_generation=0"
        if kind == "monitor_changed"
        else "task_class=advancement_task priority=2"
    )
    state.write_text(
        state.read_text()
        + (
            "\n- [ ] [P2] Observe a dependency change.\n"
            f"  <!-- loopx:todo todo_id={MONITOR_ID} status=open "
            f"claimed_by={dependency_owner} {dependency_metadata} -->\n"
        )
    )
    rc, listed = cli("todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=listed["todos"],
            handoff_mode="soft_claim",
            leases=[],
        ),
        state_path=state,
        provider=provider,
    )
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        f"causal-blocked-{kind}-{provider}",
    )
    rc, guard = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0 and guard["should_run"] is True, guard
    rc, original = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0, original
    digest = original["todo"]["completion_validation_sha256"]
    rc, wait = cli(
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--resume-when",
        f"{kind}:{MONITOR_ID}",
        "--successor-todo-id",
        ALTERNATIVE_TODO_ID,
    )
    assert rc == 0, wait
    rc, replay = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        f"causal-blocked-{kind}-{provider}",
        "--scan-path",
        str(project),
    )
    assert replay["effective_action"] == "unsettled_host_turn_recovery", replay
    assert replay["unsettled_host_turn_recovery"]["binding_id"] == TODO_ID
    assert replay["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert ALTERNATIVE_TODO_ID not in json.dumps(replay["interaction_contract"])
    assert (
        replay["heartbeat_receipt"]["event_id"]
        == guard["heartbeat_receipt"]["event_id"]
    )
    recovery = replay["unsettled_host_turn_recovery"]
    assert (
        recovery["scope"] == "current_turn"
        and recovery["repair"] == "blocked_writeback"
    )
    command = next(
        command
        for command in replay["interaction_contract"]["cli_channel"]["next_cli_actions"]
        if command.startswith("loopx ") and " refresh-state " in command
    )
    command_args = shlex.split(command)
    assert command_args[command_args.index("--registry") + 1] == str(registry)
    assert command_args[command_args.index("--runtime-root") + 1] == str(runtime)
    assert command_args[command_args.index("--todo-id") + 1] == TODO_ID
    rc, rejected = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        f"causal-blocked-{kind}-{provider}",
        "--todo-id",
        ALTERNATIVE_TODO_ID,
        "--scan-path",
        str(project),
    )
    assert rc != 0
    assert rejected["error_code"] == "heartbeat_receipt_identity_conflict"
    assert rejected["heartbeat_receipt"]["status"] == "replayed"
    assert rejected["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID
    assert rejected["effective_action"] != "heartbeat_receipt_write_failed"
    assert "without" in rejected["recommended_action"]
    # Execute the advertised recovery action, supplying independently verified
    # evidence and the actual delivery workspace, not a parallel test-only command.
    refresh_args = tuple(
        {
            "<verified-blocker-id>": MONITOR_ID,
            "<verified-evidence-ref>": "evidence:registered-wait",
        }.get(arg, arg)
        for arg in command_args[command_args.index("refresh-state") :]
    ) + (
        "--delivery-workspace-path",
        str(workspace),
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    rc, refresh = cli(*refresh_args, cwd=project)
    assert rc == 0, json.dumps(refresh, indent=2)
    assert refresh["blocked_retry"]["schema_version"] == "quota_blocked_causal_wait_v0"
    assert refresh["settlement_progress"]["state"] == "settled"
    assert (
        refresh["settlement_progress"]["closeout_kind"]
        == "typed_blocked_writeback_no_spend"
    )
    assert [r["step_kind"] for r in refresh["settlement_result"]["receipts"]] == [
        "validation",
        "durable_writeback",
    ]
    rc, replay = cli(*refresh_args, cwd=project)
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    assert replay["blocked_retry"] == refresh["blocked_retry"]
    assert _classification_count(runtime, "blocked_dependency") == 1
    rc, spend = cli(
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0 and spend["appended"] is False, spend
    assert _spend_run_count(runtime) == 0
    rc, after = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0, after
    todo = after["todo"]
    assert todo["status"] == "open" and todo["resume_ready"] is False
    assert todo["resume_when"] == f"{kind}:{MONITOR_ID}"
    assert todo["completion_validation_sha256"] == digest
    rc, next_turn = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        f"after-causal-{provider}",
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0, next_turn
    assert next_turn["effective_action"] != "unsettled_host_turn_recovery"
    assert next_turn["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    # No continuous monitor poll was needed or forced by the advancement closeout.
    assert _classification_count(runtime, "quota_monitor_poll") == 0
