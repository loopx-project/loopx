"""Select current work and execute its returned settlement, using real stores/CLI."""
from __future__ import annotations

from canonical_authority_fixture import assert_todo_read_unchanged

import json
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import test_quota_settlement_cli as cli
from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.rollout_event_log import rollout_event_log_path
from loopx.presentation.renderers.quota_event_markdown import render_quota_slot_preview_markdown
from loopx.state_refresh import render_state_refresh_markdown
from loopx.todos import list_goal_todos


def _row(todo_id: str, *, status: str = "open", extra: str = "") -> str:
    return (
        f"- [{'x' if status == 'done' else ' '}] [P1] Validate {todo_id}.\n"
        f"  <!-- loopx:todo todo_id={todo_id} status={status} "
        f"task_class=advancement_task action_kind=validate {extra} -->\n"
    )


def _source(root: Path, *, provider: str, status: str = "open", extra: str = "", empty: bool = False,
            handoff_mode: str = "soft_claim", user_rows: str = ""):
    project, runtime, registry = cli._write_fixture(root)
    goal = json.loads(registry.read_text())["goals"][0]
    path = project / goal["state_file"]
    prefix = path.read_text().split("## Agent Todo")[0]
    if user_rows:
        prefix += "## User Todo / Owner Review Reading Queue\n\n" + user_rows + "\n"
    prefix += "## Agent Todo\n\n"
    rows = "".join(_row(f"todo_ready_{i}") for i in range(35))
    path.write_text(prefix + ("" if empty else rows + _row(cli.TODO_ID, status=status, extra=extra)))
    fields = parse_active_state_todos(path.read_text(), goal=goal, item_limit=None)
    initialize_canonical_authority(
        runtime, cli.GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=cli.GOAL_ID,
            todos=fields["agent_todos"]["items"] + fields.get("user_todos", {}).get("items", []),
            handoff_mode=handoff_mode,
        ),
        state_path=path, provider=provider,
    )
    return project, runtime, registry, path, prefix


def _guard(project: Path, runtime: Path, registry: Path, *, turn_id: str = cli.TURN_ID):
    return cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--todo-id", cli.TODO_ID, "--turn-instance-id", turn_id,
         cwd=project,
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("status", ["done", "blocked"])
def test_stale_markdown_cannot_admit_terminal_or_blocked_work(tmp_path, provider, status):
    project, runtime, registry, path, prefix = _source(tmp_path, provider=provider, status=status)
    path.write_text(prefix + _row(cli.TODO_ID))
    listed = list_goal_todos(registry_path=registry, goal_id=cli.GOAL_ID,
                            runtime_root_arg=str(runtime), todo_id=cli.TODO_ID)
    assert listed["todo"]["status"] == status
    code, guard = _guard(project, runtime, registry)
    assert code == 1
    assert guard["decision"] == "skip"
    assert guard["heartbeat_receipt"]["status"] == "not_committed"
    assert not guard.get("selected_todo")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_exact_selection_reaches_work_beyond_display_limits(tmp_path, provider):
    project, runtime, registry, path, prefix = _source(tmp_path, provider=provider)
    # Goal intent remains readable, while its bounded Todo display cannot
    # supply the exact candidate from the canonical provider.
    path.write_text(prefix)
    code, guard = _guard(project, runtime, registry)
    assert code == 0, guard
    assert guard["decision"] == "run"
    assert guard["selected_todo"]["todo_id"] == cli.TODO_ID
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == cli.TODO_ID


def test_unmigrated_goal_cannot_select_work_from_markdown(tmp_path):
    project, runtime, registry = cli._write_fixture(tmp_path)
    state = project / json.loads(registry.read_text())["goals"][0]["state_file"]
    before = state.read_bytes()
    code, guard = _guard(project, runtime, registry)
    assert code != 0 and not guard["ok"], guard
    assert "canonical authority" in json.dumps(guard).lower()
    assert not guard.get("selected_todo")
    assert cli._heartbeat_receipt_count(runtime, cli.TURN_ID) == 0
    assert cli._spend_run_count(runtime) == 0
    assert state.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_todos_cannot_replace_missing_goal_intent(tmp_path, provider):
    project, runtime, registry, path, _ = _source(tmp_path, provider=provider)
    before = list_goal_todos(registry_path=registry, goal_id=cli.GOAL_ID,
                            runtime_root_arg=str(runtime), todo_id=cli.TODO_ID)
    path.unlink()
    # A read-only canonical Todo lookup still works; it cannot authorize a
    # delivery whose Goal objective and constraints are unavailable.
    assert_todo_read_unchanged(list_goal_todos(registry_path=registry, goal_id=cli.GOAL_ID,
                           runtime_root_arg=str(runtime), todo_id=cli.TODO_ID), before)
    code, guard = _guard(project, runtime, registry)
    assert code == 1
    assert guard["should_run"] is False
    assert not guard.get("selected_todo")
    assert cli._heartbeat_receipt_count(runtime, cli.TURN_ID) == 0
    assert cli._spend_run_count(runtime) == 0
    assert not path.exists()
    assert_todo_read_unchanged(list_goal_todos(registry_path=registry, goal_id=cli.GOAL_ID,
                           runtime_root_arg=str(runtime), todo_id=cli.TODO_ID), before)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("conflicting_target", [False, True])
def test_canonical_decision_gate_cannot_be_bypassed_by_exact_selection(
    tmp_path, provider, conflicting_target,
):
    target = "todo_other" if conflicting_target else cli.TODO_ID
    user_rows = (
        "- [ ] [P0] Decide whether the selected write may proceed.\n"
        f"  <!-- loopx:todo todo_id=todo_gate status=open task_class=user_gate "
        f"blocks_agent={cli.AGENT_ID} decision_scope=write_scope:action:release "
        f"unblocks_todo_id={target} -->\n"
    )
    project, runtime, registry, path, prefix = _source(
        tmp_path, provider=provider,
        extra=f"claimed_by={cli.AGENT_ID} required_decision_scopes=write_scope:action:release",
        user_rows=user_rows,
    )
    # An apparently unblocked display cannot erase the canonical gate/requirement.
    path.write_text(prefix.split("## User Todo")[0] + "## Agent Todo\n\n" + _row(cli.TODO_ID))
    _, guard = _guard(project, runtime, registry)
    assert guard["normal_delivery_allowed"] is False, guard
    if conflicting_target:
        consistency = guard["todo_decision_scope_consistency"]
        assert consistency["ok"] is False
        assert any(error["reason_code"] == "required_decision_scope_target_mismatch"
                   for error in consistency["errors"])
    current = list_goal_todos(registry_path=registry, goal_id=cli.GOAL_ID,
                             runtime_root_arg=str(runtime), todo_id=cli.TODO_ID)["todo"]
    assert current["required_decision_scopes"][0]["scope_key"] == "release"
    assert cli._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("extra", [f"excluded_agents={cli.AGENT_ID}", "claimed_by=agent-other",
                                   "required_capabilities=production_access"])
def test_exact_selection_keeps_executor_and_capability_gates(tmp_path, provider, extra):
    project, runtime, registry, _, _ = _source(
        tmp_path, provider=provider, extra=extra,
    )
    code, guard = _guard(project, runtime, registry)
    assert code == 1
    assert guard["decision"] == "skip"
    assert guard["heartbeat_receipt"]["status"] == "not_committed"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_absent_current_identity_cannot_be_selected(tmp_path, provider):
    project, runtime, registry, path, prefix = _source(tmp_path, provider=provider, empty=True)
    path.write_text(prefix + _row(cli.TODO_ID))
    code, guard = _guard(project, runtime, registry)
    assert code == 1
    assert guard["decision"] == "skip"
    assert guard["heartbeat_receipt"]["status"] == "not_committed"


def test_failed_canonical_read_cannot_fall_back_to_markdown(tmp_path):
    project, runtime, registry, path, _ = _source(tmp_path, provider="file")
    (runtime / "authority" / "file-v0").rename(runtime / "unavailable-provider")
    assert cli.TODO_ID in path.read_text()
    code, guard = _guard(project, runtime, registry)
    assert code != 0
    assert not guard.get("selected_todo")
    assert cli._heartbeat_receipt_count(runtime, cli.TURN_ID) == 0




def _refresh(project: Path, runtime: Path, registry: Path, *extra: str):
    return cli._run_cli(
        registry, runtime, "refresh-state", "--goal-id", cli.GOAL_ID,
        "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--agent-id", cli.AGENT_ID,
        "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID,
        "--no-global-sync", "--suppress-external-sinks", *extra, cwd=project,
    )


def _execute(command: str, project: Path, runtime: Path, registry: Path):
    # Execute the returned argv unchanged. The fixture supplies only CLI output
    # format and the same registry/runtime, never missing actor/binding arguments.
    return cli._run_cli(registry, runtime, *shlex.split(command)[1:], cwd=project)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("wait_kind", ["resume_at", "pr_merged"])
@pytest.mark.parametrize("waiting_status", ["open", "deferred"])
@pytest.mark.parametrize("wait_phase", ["pending", "due"])
def test_semantic_progress_replay_keeps_original_turn_after_todo_wait(
    tmp_path, provider, wait_kind, waiting_status, wait_phase,
):
    project, runtime, registry, _, _ = _source(
        tmp_path, provider=provider, extra=f"claimed_by={cli.AGENT_ID}",
    )
    code, guard = _guard(project, runtime, registry)
    assert code == 0 and guard["decision"] == "run", guard
    original = guard["heartbeat_receipt"]["settlement_identity"]

    # A legitimate native wait changes the runnable frontier; it neither
    # completes the original Todo nor changes the already admitted host Turn.
    delta = timedelta(minutes=20 if wait_phase == "pending" else -1)
    due = (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")
    resume_when = f"resume_at:{due}" if wait_kind == "resume_at" else "pr_merged:example/project#1"
    code, updated = cli._run_cli(
        registry, runtime, "todo", "update", "--goal-id", cli.GOAL_ID,
        "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID,
        "--status", waiting_status, "--resume-when", resume_when, cwd=project,
    )
    assert code == 0 and updated["ok"] is True, json.dumps(updated)
    code, recovery = cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--turn-instance-id", cli.TURN_ID, cwd=project,
    )
    assert code == 0, recovery
    channel = recovery["interaction_contract"]["cli_channel"]
    plan = channel.get("settlement_plan")
    assert plan is not None, recovery
    assert plan["identity"] == original
    assert not recovery["normal_delivery_allowed"]
    assert not recovery.get("selected_todo")
    assert channel["spend_allowed_now"] is False
    commands = {step["kind"]: step["command_template"] for step in plan["ordered_steps"]}
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope

    envelope = build_turn_envelope(recovery)
    assert envelope["writeback"]["settlement_plan"] == plan
    code, refused = cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--todo-id", "todo_ready_0", "--turn-instance-id", cli.TURN_ID, cwd=project,
    )
    assert code == 1 and refused["error_code"] == "heartbeat_receipt_identity_conflict", refused
    refresh = commands["durable_writeback"].replace(
        "<validated_progress>", "validated_progress",
    ).replace("<scale>", "implementation").replace("<outcome>", "outcome_progress")
    refresh += (
        " --vision-state vision_on_track --vision-summary 'Continue current eligible work.'"
        " --vision-acceptance 'The waiting Todo remains open; the next Turn selects fresh work.'"
        " --no-global-sync --suppress-external-sinks"
    )
    code, refreshed = _execute(refresh, project, runtime, registry)
    assert code == 0, refreshed
    assert refreshed["vision_checkpoint"]["delivery_boundary"] == "semantic_closeout"
    assert refreshed["vision_checkpoint"]["satisfied"] is True
    code, owed = cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--turn-instance-id", cli.TURN_ID, cwd=project,
    )
    assert code == 0 and owed["unsettled_host_turn_recovery"]["repair"] == "quota_spend", owed
    assert owed["settlement_owed"]["command"] == refreshed["settlement_owed"]["command"]
    for replayed in (False, True):
        code, spent = _execute(owed["settlement_owed"]["command"], project, runtime, registry)
        assert code == 0, spent
        if replayed:
            assert spent["idempotent_replay"] and not spent["appended"], spent
        else:
            assert spent["settlement_progress"]["state"] == "settled", spent
    assert cli._classification_count(runtime, "validated_progress") == 1

    readback = read_heartbeat_settlement(
        runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID,
    )
    assert readback is not None and readback.replay_phase.value == "settled"
    for explicit in (False, True):
        code, replay = cli._run_cli(
            registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
            "--turn-instance-id", cli.TURN_ID,
            *(["--todo-id", cli.TODO_ID] if explicit else []), cwd=project,
        )
        assert code == 0 and replay["effective_action"] == "heartbeat_settled_skip", replay
        assert replay["heartbeat_receipt"]["settlement_identity"] == original
        assert replay["interaction_contract"]["agent_channel"]["must_attempt"] is False
        channel = replay["interaction_contract"]["cli_channel"]
        assert channel["spend_allowed_now"] is False
        assert channel["spend_after_validation"] is False
        assert all("refresh-state" not in command and "spend-slot" not in command
                   for command in channel["next_cli_actions"])
        assert not replay.get("selected_todo")
        assert not replay.get("autonomous_replan_obligation")
    assert cli._heartbeat_receipt_count(runtime, cli.TURN_ID) == 1
    assert cli._spend_run_count(runtime) == 1
    current = list_goal_todos(registry_path=registry, goal_id=cli.GOAL_ID,
                             runtime_root_arg=str(runtime), todo_id=cli.TODO_ID)["todo"]
    assert current["status"] == waiting_status
    timer_due = wait_kind == "resume_at" and wait_phase == "due"
    assert current["resume_ready"] is timer_due

    code, fresh = cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--turn-instance-id", "turn-after-semantic-progress",  cwd=project,
    )
    assert code == 0 and fresh["decision"] in ("run", "autonomous_replan_required"), fresh
    assert fresh["heartbeat_receipt"]["turn_instance_id"] == "turn-after-semantic-progress"
    selected_fresh_id = (fresh.get("selected_todo") or {}).get("todo_id")
    if timer_due and waiting_status == "open":
        assert selected_fresh_id == cli.TODO_ID
    else:
        assert selected_fresh_id != cli.TODO_ID
    if fresh["decision"] == "autonomous_replan_required":
        assert fresh["replan_action_packet"]["obligation_id"]
    assert fresh["effective_action"] != "unsettled_host_turn_recovery"
    assert cli._spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("qualified", [False, True])
def test_workspace_repair_wait_requires_original_business_admission(tmp_path, provider, qualified):
    project, runtime, registry = cli._write_fixture(tmp_path)
    state = cli._configure_repository_write_todo(project)
    state.write_text(state.read_text().replace("action_kind=validate", "action_kind=implement"))
    registered = json.loads(registry.read_text())
    registered["goals"][0]["coordination"]["registered_agents"].append("fixture-peer")
    registry.write_text(json.dumps(registered))
    cli._initialize_git_checkout(project)
    subprocess.run([
        "git", "-c", "user.name=LoopX Test", "-c", "user.email=loopx-test@example.invalid",
        "commit", "--quiet", "--allow-empty", "-m", "fixture",
    ], cwd=project, check=True)
    cli._initialize_fixture_authority(project, runtime, provider=provider)
    turn_id = "turn-workspace-repair-wait"
    code, initial = _guard(project, runtime, registry, turn_id=turn_id)
    assert code == 0, initial
    assert initial["interaction_contract"]["mode"] == "agent_workspace_repair", initial
    assert initial["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    original = initial["heartbeat_receipt"]["settlement_identity"]
    workspace = project
    if qualified:
        workspace = tmp_path / "independent-worktree"
        subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(workspace)],
                       cwd=project, check=True)
        code, admitted = cli._run_cli(
            registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
            "--turn-instance-id", turn_id, "--todo-id", cli.TODO_ID, cwd=workspace,
        )
        assert code == 0, admitted
        assert admitted["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
        assert admitted["heartbeat_receipt"]["settlement_identity"] == original
    code, updated = cli._run_cli(
        registry, runtime, "todo", "update", "--goal-id", cli.GOAL_ID,
        "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID,
        "--resume-when", "resume_at:2099-01-01T00:00:00Z", cwd=workspace,
    )
    assert code == 0, updated
    for explicit in (False, True):
        code, replay = cli._run_cli(
            registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
            "--turn-instance-id", turn_id,
            *(["--todo-id", cli.TODO_ID] if explicit else []), cwd=workspace,
        )
        assert code == 0, replay
        assert replay["heartbeat_receipt"]["settlement_identity"] == original
        recovery = replay.get("unsettled_host_turn_recovery") or {}
        plan = replay["interaction_contract"]["cli_channel"].get("settlement_plan")
        if qualified:
            assert replay["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
            assert recovery.get("repair") == "durable_writeback", replay
            assert plan["identity"] == original
        else:
            assert recovery.get("repair") != "durable_writeback", replay
            # Replanning may still be admitted as planning. It cannot be
            # replaced by a closeout for an unperformed business result.
            if plan:
                assert replay.get("replan_action_packet"), replay
                assert replay["replan_action_packet"].get("settlement_only") is not True
    assert cli._classification_count(runtime, "validated_progress") == 0
    assert cli._spend_run_count(runtime) == 0
    readback = read_heartbeat_settlement(
        runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID, turn_instance_id=turn_id,
    )
    assert readback is not None
    assert readback.native_child_admission["report_permission"] == (
        "new_operation" if qualified else "not_admitted"
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_initial_due_wait_preserves_original_replan_on_reentry(
    tmp_path, provider,
):
    """Planning admission has no completed business result to recover."""
    project, runtime, registry = cli._write_fixture(tmp_path)
    cli._configure_selected_todo_replan_fixture(project, registry)
    state = project / f".codex/goals/{cli.GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        f"todo_id={cli.SELECTED_REPLAN_TODO_ID} status=open",
        f"todo_id={cli.SELECTED_REPLAN_TODO_ID} status=open "
        "resume_when=resume_at%3A2001-01-01T00%3A00%3A00Z",
    ))
    cli._initialize_git_checkout(project)
    cli._initialize_fixture_authority(project, runtime, provider=provider)
    turn_id = "turn-original-due-wait-replan"
    code, pending = cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--turn-instance-id", turn_id, cwd=project,
    )
    assert code == 0, pending
    code, admitted = cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--turn-instance-id", turn_id, "--todo-id", cli.SELECTED_REPLAN_TODO_ID,
        cwd=project,
    )
    assert code == 0, json.dumps(admitted, ensure_ascii=False)
    assert admitted["decision"] == "autonomous_replan_required", admitted
    original = admitted["heartbeat_receipt"]["settlement_identity"]
    obligation = admitted["replan_action_packet"]["obligation_id"]
    admission_receipts = cli._heartbeat_receipt_count(runtime, turn_id)
    before = list_goal_todos(
        registry_path=registry, goal_id=cli.GOAL_ID,
        runtime_root_arg=str(runtime), todo_id=cli.SELECTED_REPLAN_TODO_ID,
    )
    assert before["todo"]["status"] == "open"
    assert before["todo"]["resume_ready"] is True
    for explicit in (False, True):
        code, replay = cli._run_cli(
            registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
            "--turn-instance-id", turn_id,
            *(["--todo-id", cli.SELECTED_REPLAN_TODO_ID] if explicit else []),
            cwd=project,
        )
        assert code == 0 and replay["decision"] == "autonomous_replan_required", replay
        assert replay["heartbeat_receipt"]["settlement_identity"] == original
        assert replay["replan_action_packet"]["obligation_id"] == obligation
        assert replay["replan_action_packet"].get("settlement_only") is not True
        assert not replay.get("unsettled_host_turn_recovery")
        assert not replay["interaction_contract"]["cli_channel"]["spend_allowed_now"]
    after = list_goal_todos(
        registry_path=registry, goal_id=cli.GOAL_ID,
        runtime_root_arg=str(runtime), todo_id=cli.SELECTED_REPLAN_TODO_ID,
    )
    assert_todo_read_unchanged(before, after)
    assert cli._heartbeat_receipt_count(runtime, turn_id) == admission_receipts
    assert cli._spend_run_count(runtime) == 0
    assert cli._classification_count(runtime, "validated_progress") == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_migrated_hard_lease_can_write_settle_retry_and_continue(tmp_path, provider):
    project, runtime, registry, path, prefix = _source(
        tmp_path, provider=provider, handoff_mode="legacy",
        extra=f"claimed_by={cli.AGENT_ID}",
    )

    def run(*args):
        return cli._run_cli(registry, runtime, *args, "--goal-id", cli.GOAL_ID, cwd=project)

    plan = tmp_path / "handoff-plan.json"
    code, planned = run("handoff-mode", "plan-migration", "--mode", "hard_lease", "--plan", str(plan))
    assert code == 0 and planned["status"] == "planned", planned
    assert planned["preserved_claim_count"] == 1
    migration = ("handoff-mode", "migrate", "--plan", str(plan),
                 "--plan-sha256", planned["plan_sha256"], "--execute")
    code, migrated = run(*migration)
    assert code == 0 and migrated["status"] == "applied", migrated
    assert Path(migrated["backup_path"]).is_file()
    assert migrated["execution_authority_granted"] is False
    goal_context = path.read_text().split("## Agent Todo")[0]
    path.write_text(goal_context + "## Agent Todo\n\n")

    code, guard = _guard(project, runtime, registry)
    assert code == 0 and guard["decision"] == "run", guard
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == cli.TODO_ID
    update = ("todo", "update", "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID,
              "--text", "Continue after policy migration")
    # A migrated owner may edit copy, but migration supplies no execution proof.
    code, rejected = run(*update, "--task-lease-idempotency-key", "not-acquired",
                         "--task-lease-expected-version", "1")
    assert code == 1 and rejected["error_code"] == "handoff_mode_requires_lease", rejected

    lease_key = "migrated-delivery"
    code, acquired = run("task-lease", "acquire", "--todo-id", cli.TODO_ID, "--owner", cli.AGENT_ID,
                         "--idempotency-key", lease_key, "--expected-version", "0", "--ttl-seconds", "3600",
                         "--write-scope", "tests/**")
    assert code == 0 and acquired["acquired"] is True, acquired
    version = str(acquired["lease"]["version"])
    code, updated = run(*update, "--task-lease-idempotency-key", lease_key,
                        "--task-lease-expected-version", version)
    assert code == 0 and updated["ok"] is True, updated

    code, refreshed = _refresh(project, runtime, registry)
    assert code == 0, refreshed
    command = refreshed["settlement_owed"]["command"]
    code, spent = _execute(command, project, runtime, registry)
    assert code == 0 and spent["appended"] is True, spent
    assert spent["settlement_progress"]["state"] == "settled"
    code, retried = _execute(command, project, runtime, registry)
    assert code == 0 and retried["appended"] is False, retried
    assert cli._spend_run_count(runtime) == 1

    # A migration retry after actual work cannot reset the current projection.
    before = list_goal_todos(registry_path=registry, goal_id=cli.GOAL_ID,
                            runtime_root_arg=str(runtime), todo_id=cli.TODO_ID)["todo"]
    assert before["text"] == "[P1] Continue after policy migration"
    code, replayed = run(*migration)
    assert code == 0 and replayed["status"] == "replayed", replayed
    assert list_goal_todos(registry_path=registry, goal_id=cli.GOAL_ID,
                          runtime_root_arg=str(runtime), todo_id=cli.TODO_ID)["todo"] == before
    code, released = run("task-lease", "release", "--todo-id", cli.TODO_ID, "--owner", cli.AGENT_ID,
                         "--idempotency-key", lease_key, "--expected-version", version)
    assert code == 0 and released["released"] is True, released
    code, next_wake = _guard(project, runtime, registry, turn_id="turn-after-policy-migration")
    assert code == 0 and next_wake["decision"] == "run", next_wake
    assert next_wake["selected_todo"]["todo_id"] == cli.TODO_ID
    assert next_wake["effective_action"] != "unsettled_host_turn_recovery"
    assert cli._spend_run_count(runtime) == 1


def test_returned_command_settles_and_repairs_receipts_without_another_debit(tmp_path):
    project, runtime, registry = cli._write_fixture(tmp_path)
    cli._initialize_fixture_authority(project, runtime)
    code, guard = _guard(project, runtime, registry)
    assert code == 0, guard
    code, refreshed = _refresh(project, runtime, registry)
    assert code == 0, refreshed
    command = refreshed["settlement_owed"]["command"]
    code, spent = _execute(command, project, runtime, registry)
    assert code == 0, spent
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    assert command in render_state_refresh_markdown(refreshed)
    argv = shlex.split(command)
    assert argv[argv.index("--registry") + 1] == str(registry)
    assert argv[argv.index("--runtime-root") + 1] == str(runtime)
    assert spent["appended"] is True
    assert spent["settlement_progress"]["state"] == "settled"
    assert "settlement: `settled`" in render_quota_slot_preview_markdown(spent)
    assert cli._spend_run_count(runtime) == 1

    path = rollout_event_log_path(runtime, cli.GOAL_ID)
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    retained = [event for event in events if event["event_kind"] != "quota_spend"]
    assert len(events) - len(retained) == 1
    path.write_text("".join(json.dumps(event) + "\n" for event in retained))
    code, recovery = _refresh(project, runtime, registry)
    assert code == 0, recovery
    assert recovery["settlement_progress"]["state"] == "spend_receipt_required"
    assert recovery["settlement_owed"]["recovery_does_not_spend"] is True
    repair_command = recovery["settlement_owed"]["command"]
    assert repair_command in render_state_refresh_markdown(recovery)
    code, repaired = _execute(repair_command, project, runtime, registry)
    assert code == 0, repaired
    assert repaired["appended"] is False
    assert cli._spend_run_count(runtime) == 1
    readback = read_heartbeat_settlement(runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
                                       todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID)
    assert readback.settlement.failure is None
    code, replay = _refresh(project, runtime, registry)
    assert code == 0, replay
    assert replay["settlement_progress"]["state"] == "settled"
    assert "settlement_owed" not in replay
    code, next_wake = _guard(project, runtime, registry, turn_id="turn-after-repair")
    assert code == 0, next_wake
    assert next_wake["effective_action"] != "unsettled_host_turn_recovery"
    assert cli._spend_run_count(runtime) == 1
