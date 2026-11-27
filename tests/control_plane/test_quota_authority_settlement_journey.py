"""Select current work and execute its returned settlement, using real stores/CLI."""
from __future__ import annotations

from canonical_authority_fixture import assert_todo_read_unchanged

import json
import os
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import test_quota_settlement_cli as cli
from canonical_authority_fixture import initialize_canonical_authority
from codex_checkpoint_fixture import read_cli_policy_payload, selected_context_values

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota import slot_accounting
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.entrypoint import main as cli_main
from loopx.presentation.renderers.quota_event_markdown import render_quota_slot_preview_markdown
from loopx.rollout_event_log import rollout_event_log_path
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




def _refresh(project: Path, runtime: Path, registry: Path, *extra: str, outcome: str = "outcome_progress"):
    return cli._run_cli(
        registry, runtime, "refresh-state", "--goal-id", cli.GOAL_ID,
        "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", outcome, "--agent-id", cli.AGENT_ID,
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
    assert plan["schema_version"] == "quota_settlement_plan_v2"
    steps = {step["kind"]: step for step in plan["ordered_steps"]}
    assert steps["quota_spend"]["owner"] == "runtime"
    assert "command_template" not in steps["quota_spend"]
    writeback = steps["durable_writeback"]["command_template"]
    assert channel["next_cli_actions"] == [writeback]
    code, refused = cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--todo-id", "todo_ready_0", "--turn-instance-id", cli.TURN_ID, cwd=project,
    )
    assert code == 1 and refused["error_code"] == "heartbeat_receipt_identity_conflict", refused
    refresh = writeback.replace(
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
    assert refreshed["settlement_identity"] == original
    assert refreshed["settlement_progress"]["state"] == "settled"
    assert [row["step_kind"] for row in refreshed["settlement_result"]["receipts"]] == [
        "validation", "durable_writeback", "quota_spend",
    ]
    assert "settlement_owed" not in refreshed
    code, retried = _execute(refresh, project, runtime, registry)
    assert code == 0 and retried["idempotent_replay"] and not retried["appended"], retried
    assert retried["settlement_result"] == refreshed["settlement_result"]
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


def _workspace_repair_source(tmp_path, provider, goal_scopes=None, todo_scopes=None):
    project, runtime, registry = cli._write_fixture(tmp_path)
    state = cli._configure_repository_write_todo(project)
    body = state.read_text().replace("action_kind=validate", "action_kind=implement")
    if todo_scopes is not None:
        body = body.replace("required_capabilities=filesystem_write -->",
                            "required_capabilities=filesystem_write "
                            f"required_write_scopes={','.join(todo_scopes)} -->")
    state.write_text(body)
    registered = json.loads(registry.read_text())
    coordination = registered["goals"][0]["coordination"]
    coordination["registered_agents"].append("fixture-peer")
    if goal_scopes is not None:
        coordination["write_scope"] = goal_scopes
    registry.write_text(json.dumps(registered))
    cli._initialize_git_checkout(project)
    subprocess.run([
        "git", "-c", "user.name=LoopX Test", "-c", "user.email=loopx-test@example.invalid",
        "commit", "--quiet", "--allow-empty", "-m", "fixture",
    ], cwd=project, check=True)
    cli._initialize_fixture_authority(project, runtime, provider=provider)
    return project, runtime, registry


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native", [
    pytest.param(False, id="stateless"),
    pytest.param(True, id="native", marks=pytest.mark.skipif(
        os.name != "posix", reason="Native session continuity uses a private POSIX journal",
    )),
])
def test_workspace_repair_opening_keeps_action_and_recovers_scopes(tmp_path, monkeypatch, provider, native):
    goal_path = "/goal_boundary/write_scope"
    goal_scopes = ["src/**"] + [f"docs/area-{i:03d}/reference/specifications/**" for i in range(300)]
    todo_scopes = [f"src/area-{i:03d}/implementations/shared/**" for i in range(300)]
    project, runtime, registry = _workspace_repair_source(tmp_path, provider, goal_scopes, todo_scopes)
    turn = "workspace-repair-context"
    code, plain = _guard(project, runtime, registry, turn_id=turn)
    assert code == 0 and plain["effective_action"] == "agent_workspace_repair"
    assert plain["goal_boundary"]["write_scope"] == goal_scopes
    assert "required_write_scopes" not in plain["selected_todo"]
    assert cli._heartbeat_receipt_count(runtime, turn) == 1
    identity = plain["heartbeat_receipt"]["settlement_identity"]
    repair_action = plain["interaction_contract"]["agent_channel"]["primary_action"]
    captures = tmp_path / "captures"
    captures.mkdir(mode=0o700)
    context_args = ["--codex-context-root", str(captures)]
    if native:
        from loopx.control_plane.turn_driver.codex_context import codex_context_hook

        journal = tmp_path / "native"
        journal.mkdir(mode=0o700)
        session_id = "workspace-repair-native"
        monkeypatch.setenv("CODEX_THREAD_ID", session_id)
        assert codex_context_hook(str(journal), {"session_id": session_id, "cwd": str(project),
            "hook_event_name": "SessionStart", "source": "startup"}) == {}
        context_args = ["--codex-context-dir", str(journal)]
    code, view = cli._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
        "--turn-instance-id", turn, "--context-delivery", "on", "--delta-delivery", "auto" if native else "off",
        "--decision-output-root", str(captures), *context_args, cwd=project,
    )
    assert code == 0 and view["ok"], view
    if native:
        assert json.loads((journal / "session.json").read_bytes())["recovery"]

    def selection(command):
        parts = bytearray()
        pages = []
        while command:
            args = shlex.split(command)
            code, page = cli._run_cli(registry, runtime, *args[args.index("quota"):], cwd=project)
            assert code == 0 and page["ok"] and page["offset"] == len(parts), page
            parts.extend(page["content"].encode())
            pages.append(page)
            command = page.get("next_command")
        assert pages[-1]["eof"] and pages[-1]["total_bytes"] == len(parts)
        return pages, selected_context_values(parts.decode())

    pages, initial = selection(view["first_read_command"])
    first = pages[0]
    if remainder := view.get("remaining_read_command"):
        more, remaining = selection(remainder)
        assert initial.keys().isdisjoint(remaining)
        pages.extend(more)
        initial.update(remaining)
    print(json.dumps({"provider": provider, "pages_before_worktree_move": len(pages),
                      "selection_bytes": sum(len(page["content"].encode()) for page in pages)}))
    assert json.dumps(repair_action, ensure_ascii=False) in first["content"]
    assert initial["/execution_obligation"] == plain["execution_obligation"]
    assert initial["/goal_boundary/stop_condition"] == plain["goal_boundary"]["stop_condition"]
    assert initial["/heartbeat_receipt/settlement_identity"] == identity
    assert initial["/interaction_contract/agent_channel/delivery_allowed"] is False
    assert goal_path not in initial
    assert initial["/goal_boundary/write_scope_pointer"] == goal_path
    detail = initial["/context_details/unread_context_index/source"]
    template = shlex.split(initial["/context_details/command_template"])
    delivered_reader = shlex.split(view["recovery"]["command"])
    assert template[-2:] == ["--select", "<pointer>"]
    assert template[:template.index("--format")] == delivered_reader[:delivered_reader.index("--format")]
    assert template[template.index("--capture-file") + 1] == detail["path"]
    assert template[template.index("--capture-sha256") + 1] == detail["sha256"]
    code, rejected = cli._run_cli(registry, runtime, *delivered_reader[delivered_reader.index("quota"):],
        "--select", goal_path, cwd=project)
    assert code != 0 and rejected["diagnostic_code"] == "context_selection_missing_pointer"
    _, exact = selection(shlex.join([*template[:-1], goal_path]))
    assert exact[goal_path] == goal_scopes
    assert cli._heartbeat_receipt_count(runtime, turn) == 1

    workspace = tmp_path / "repair-worktree"
    subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(workspace)], cwd=project, check=True)
    code, ready = _guard(workspace, runtime, registry, turn_id=turn)
    assert code == 0 and ready["normal_delivery_allowed"] is True
    assert ready["heartbeat_receipt"]["settlement_identity"] == identity
    assert ready["goal_boundary"]["write_scope"] == goal_scopes
    assert cli._heartbeat_receipt_count(runtime, turn) == 2  # Repair and legitimate same-Turn reentry.
    assert cli._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("qualified", [False, True])
def test_workspace_repair_wait_requires_original_business_admission(tmp_path, provider, qualified):
    project, runtime, registry = _workspace_repair_source(tmp_path, provider)
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

    proof_args = ("--task-lease-idempotency-key", lease_key, "--task-lease-expected-version", version)
    code, unprepared = _refresh(project, runtime, registry, *proof_args)
    assert code == 1 and unprepared["error_code"] == "refresh_task_lease_unavailable", unprepared
    assert unprepared["appended"] is False and cli._spend_run_count(runtime) == 0
    before_ready = read_heartbeat_settlement(runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID, registry_path=registry)
    assert before_ready is not None and not before_ready.writeback.receipts and not before_ready.spend.receipts

    code, ready = _guard(project, runtime, registry)
    assert code == 0 and ready["normal_delivery_allowed"], ready
    assert ready["heartbeat_receipt"]["settlement_identity"] == guard["heartbeat_receipt"]["settlement_identity"]
    prep = ready["interaction_contract"]["cli_channel"]["execution_preparation"]
    assert prep["state"] == "ready"
    assert prep["lease_proof"] == {"idempotency_key": lease_key, "expected_version": int(version)}

    captures = tmp_path / "captures"
    captures.mkdir(mode=0o700)
    code, captured = run(
        "quota", "should-run", "--codex-app", "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
        "--turn-instance-id", cli.TURN_ID, "--context-delivery", "on", "--delta-delivery", "off",
        "--decision-output-root", str(captures), "--codex-context-root", str(captures),
    )
    assert code == 0 and captured["ok"], captured
    selector = "/interaction_contract/cli_channel/settlement_plan/ordered_steps/1/vision_authoring"
    reader = shlex.split(captured["recovery"]["command"])
    code, page = cli._run_cli(registry, runtime, *reader[reader.index("quota"):], "--select", selector, cwd=project)
    assert code == 0 and page["eof"], page
    authoring = selected_context_values(page["content"])[selector]
    assert authoring["path_delta"]["open_stage_closeout_outcomes"] == ["continue", "no_change", "replan"]
    assert "wait" in authoring["path_delta"]["outcomes"]
    assert "Record waits in reentry_condition" in authoring["rule"]

    dependency = "Continue when the next verification input is available."
    vision = authoring["minimal_example"]
    vision["path_delta"]["reentry_condition"] = dependency
    vision_file = tmp_path / "vision.json"
    vision_file.write_text(json.dumps(vision))
    code, refreshed = _refresh(project, runtime, registry, *proof_args, "--agent-vision-json", str(vision_file))
    assert code == 0, refreshed
    assert refreshed["vision_checkpoint"]["satisfied"] is True
    persisted = json.loads(Path(refreshed["json_path"]).read_text())
    assert persisted["agent_vision"]["path_delta"]["reentry_condition"] == dependency
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
    code, newer = run("todo", "claim", "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID,
                      "--claimed-by", cli.AGENT_ID)
    assert code == 0 and newer["execution_managed"], newer
    code, inspected = run("task-lease", "inspect", "--todo-id", cli.TODO_ID)
    assert code == 0 and inspected["active"], inspected
    newer_epoch = inspected["lease"]["lease_epoch"]
    assert newer_epoch > acquired["lease"]["lease_epoch"]
    code, qualified = _guard(project, runtime, registry, turn_id="turn-after-policy-migration")
    assert code == 0 and qualified["normal_delivery_allowed"], qualified
    assert qualified["execution_preparation"]["lease_epoch"] == newer_epoch
    assert qualified["heartbeat_receipt"]["settlement_identity"] == next_wake["heartbeat_receipt"]["settlement_identity"]
    assert cli._spend_run_count(runtime) == 1


@pytest.fixture
def hard_lease_todo_cli(tmp_path, provider):
    project, runtime, registry, _, _ = _source(
        tmp_path, provider=provider, handoff_mode="hard_lease",
        extra=f"claimed_by={cli.AGENT_ID}",
    )
    scope = ("--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID)

    def run(*args):
        return cli._run_cli(registry, runtime, *args, *scope, cwd=project)

    return project, runtime, registry, run


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("transition", ["complete", "defer"])
@pytest.mark.parametrize("explicit_proof", [False, True])
def test_first_writeback_refuses_generation_retired_by_later_work(
    hard_lease_todo_cli, transition, explicit_proof,
):
    project, runtime, registry, run = hard_lease_todo_cli

    code, claim = run("todo", "claim", "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID)
    assert code == 0 and claim["ok"], claim
    code, guard = _guard(project, runtime, registry)
    assert code == 0 and guard["normal_delivery_allowed"], guard
    proof = guard["execution_preparation"]["lease_proof"]
    code, released = run("task-lease", "release", "--owner", cli.AGENT_ID,
                         "--idempotency-key", proof["idempotency_key"],
                         "--expected-version", str(proof["expected_version"]))
    assert code == 0 and released["released"], released
    code, newer = run("todo", "claim", "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID)
    assert code == 0 and newer["ok"] and newer["claim_operation_id"] != claim["claim_operation_id"], newer
    lifecycle = ("todo", "complete", "--turn-instance-id", cli.TURN_ID,
                 "--evidence", "evidence:fixture-complete") if transition == "complete" else (
        "todo", "update", "--status", "deferred", "--resume-when", "resume_at:2099-01-01T00:00:00Z",
        "--reason", "Wait for the fixture's external dependency",
    )
    code, committed = run(*lifecycle, "--agent-id", cli.AGENT_ID,
                          "--claim-operation-id", newer["claim_operation_id"])
    assert code == 0 and committed["ok"], committed
    journal = rollout_event_log_path(runtime, cli.GOAL_ID)
    before_rejection = journal.read_bytes()
    arguments = (
        "--task-lease-idempotency-key", proof["idempotency_key"],
        "--task-lease-expected-version", str(proof["expected_version"]),
    ) if explicit_proof else ()
    code, denied = _refresh(project, runtime, registry, *arguments)
    assert code == 1 and not denied["appended"], denied
    assert denied["error_code"] == "refresh_task_lease_unavailable", denied
    # The rejected operation must not change the external delivery preference.
    assert journal.read_bytes() == before_rejection
    result = read_heartbeat_settlement(runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID, registry_path=registry)
    assert result is not None and result.identity.value is not None
    assert result.identity.value.as_dict() == guard["heartbeat_receipt"]["settlement_identity"]
    assert not result.writeback.receipts and not result.spend.receipts
    assert cli._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("replace_lease", [False, True])
@pytest.mark.parametrize("retire_for_blocker", [False, True])
def test_first_blocked_writeback_checks_original_lease(
    hard_lease_todo_cli, replace_lease, retire_for_blocker,
):
    project, runtime, registry, run = hard_lease_todo_cli

    code, claim = run("todo", "claim", "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID)
    assert code == 0 and claim["execution_managed"], claim
    code, guard = _guard(project, runtime, registry)
    assert code == 0 and guard["normal_delivery_allowed"], guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    proof = guard["execution_preparation"]["lease_proof"]
    proof_args = (
        "--task-lease-idempotency-key", proof["idempotency_key"],
        "--task-lease-expected-version", str(proof["expected_version"]),
    )
    if replace_lease:
        code, released = run("task-lease", "release", "--owner", cli.AGENT_ID,
                             "--idempotency-key", proof["idempotency_key"],
                             "--expected-version", str(proof["expected_version"]))
        assert code == 0 and released["released"], released
        prior_claim = claim["claim_operation_id"]
        code, claim = run("todo", "claim", "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID)
        assert code == 0 and claim["claim_operation_id"] != prior_claim, claim
    if retire_for_blocker:
        code, updated = run("todo", "update", "--status", "blocked", "--agent-id", cli.AGENT_ID,
                            "--reason", "The fixture's dependency requires a later decision.",
                            "--claim-operation-id", claim["claim_operation_id"])
        assert code == 0 and updated["ok"], updated
    arguments = (
        "--progress-result-class", "blocked", "--progress-blocker-id", "blocker:fixture-dependency",
        "--progress-evidence-id", "evidence:fixture-dependency", "--settle-turn", "--settlement-receipt",
    )
    journal = rollout_event_log_path(runtime, cli.GOAL_ID)
    before = journal.read_bytes()
    if replace_lease:
        for supplied_proof in ((), proof_args):
            code, refused = _refresh(project, runtime, registry, *arguments, *supplied_proof, outcome="outcome_gap")
            assert code == 1 and refused["error_code"] == "refresh_task_lease_unavailable", refused
            assert refused["appended"] is False
        assert journal.read_bytes() == before
        assert cli._classification_count(runtime, "validated_progress") == 0
    else:
        arguments += () if retire_for_blocker else proof_args
        code, closed = _refresh(project, runtime, registry, *arguments, outcome="outcome_gap")
        assert code == 0 and closed["settlement_identity"] == identity, closed
        assert closed["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
        code, replay = _refresh(project, runtime, registry, *arguments, outcome="outcome_gap")
        assert code == 0 and replay["idempotent_replay"] and not replay["appended"], replay
        assert closed["settlement_result"] == replay["settlement_result"]
        assert cli._classification_count(runtime, "validated_progress") == 1
    assert cli._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_turn_refuses_readiness_from_a_replacement_lease(hard_lease_todo_cli):
    project, runtime, registry, run = hard_lease_todo_cli

    code, claim = run("todo", "claim", "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID)
    assert code == 0 and claim["ok"], claim
    code, admitted = _guard(project, runtime, registry)
    assert code == 0 and admitted["normal_delivery_allowed"], admitted
    original_proof = admitted["execution_preparation"]["lease_proof"]
    journal = rollout_event_log_path(runtime, cli.GOAL_ID)
    code, released = run("task-lease", "release", "--owner", cli.AGENT_ID,
                         "--idempotency-key", original_proof["idempotency_key"],
                         "--expected-version", str(original_proof["expected_version"]))
    assert code == 0 and released["released"], released
    code, replacement = run("todo", "claim", "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID)
    assert code == 0 and replacement["ok"], replacement
    assert replacement["claim_operation_id"] != claim["claim_operation_id"]
    code, inspection = run("task-lease", "inspect")
    assert code == 0 and inspection["active"], inspection
    replacement_lease = inspection["lease"]
    assert replacement_lease["lease_epoch"] > admitted["execution_preparation"]["lease_epoch"]
    before_retry = journal.read_bytes()

    code, recovery = _guard(project, runtime, registry)
    assert code == 0 and recovery["ok"], recovery
    assert recovery["normal_delivery_allowed"] is False
    assert recovery["interaction_contract"]["cli_channel"]["spend_allowed_now"] is False
    debt = recovery["unsettled_host_turn_recovery"]
    assert debt["prior_turn_instance_id"] == cli.TURN_ID
    assert debt["repair"] == "execution_loss_closeout"
    assert debt["execution_loss"]["original_prepared_epoch"] == admitted["execution_preparation"]["lease_epoch"]
    assert debt["execution_loss"]["current_lease_epoch"] == replacement_lease["lease_epoch"]
    assert "settlement_owed" not in recovery
    assert journal.read_bytes() == before_retry

    code, refused = _refresh(
        project, runtime, registry,
        "--task-lease-idempotency-key", replacement_lease["idempotency_key"],
        "--task-lease-expected-version", str(replacement_lease["version"]),
    )
    assert code == 1 and refused["error_code"] == "refresh_task_lease_unavailable", refused
    assert not refused["appended"]
    assert journal.read_bytes() == before_retry
    readback = read_heartbeat_settlement(
        runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID, todo_id=cli.TODO_ID,
        turn_instance_id=cli.TURN_ID, registry_path=registry,
    )
    assert readback is not None and readback.heartbeat_receipt["event_id"] == admitted["heartbeat_receipt"]["event_id"]
    assert not readback.writeback.receipts and not readback.spend.receipts
    assert cli._spend_run_count(runtime) == 0

    assert readback.prepared_lease["prepared_lease_epoch"] == admitted["execution_preparation"]["lease_epoch"]
    assert readback.prepared_lease["prepared_lease_version"] == original_proof["expected_version"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unprepared_turn_does_not_become_admitted_after_deferral(tmp_path, provider):
    project, runtime, registry, _, _ = _source(
        tmp_path, provider=provider, handoff_mode="hard_lease",
        extra=f"claimed_by={cli.AGENT_ID}",
    )
    code, pending = _guard(project, runtime, registry)
    assert code == 0 and pending["execution_preparation"]["state"] == "claim_required", pending
    scope = ("--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID)
    code, claim = cli._run_cli(registry, runtime, "todo", "claim", *scope,
                              "--claimed-by", cli.AGENT_ID, cwd=project)
    assert code == 0 and claim["ok"], claim
    code, deferred = cli._run_cli(registry, runtime, "todo", "update", *scope, "--status", "deferred",
        "--resume-when", "resume_at:2099-01-01T00:00:00Z", "--reason", "Wait for the fixture's dependency",
        "--claim-operation-id", claim["claim_operation_id"], cwd=project)
    assert code == 0 and deferred["ok"], deferred
    code, denied = _refresh(project, runtime, registry)
    assert code == 1 and not denied["appended"], denied
    assert denied["error_code"] == "refresh_task_lease_unavailable", denied
    readback = read_heartbeat_settlement(runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID, registry_path=registry)
    assert readback is not None and readback.prepared_lease_required and readback.prepared_lease is None
    assert not readback.writeback.receipts and not readback.spend.receipts
    assert cli._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_open_todo_with_committed_run_only_recovers_its_receipt(tmp_path, provider):
    project, runtime, registry = cli._write_fixture(tmp_path)
    cli._initialize_fixture_authority(project, runtime, provider=provider)
    code, initial = _guard(project, runtime, registry)
    assert code == 0 and initial["normal_delivery_allowed"]
    identity = initial["heartbeat_receipt"]["settlement_identity"]
    code, written = _refresh(project, runtime, registry)
    assert code == 0 and written["appended"]
    # Retain the real Run and original admission at the durable boundary just
    # before the CLI records its refresh event.
    path = rollout_event_log_path(runtime, cli.GOAL_ID)
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    retained = [row for row in rows if row["event_kind"] != "refresh_state"]
    assert len(retained) + 1 == len(rows)
    path.write_text("".join(json.dumps(row) + "\n" for row in retained))
    for explicit in (False, True):
        code, guard = cli._run_cli(
            registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID, "--turn-instance-id", cli.TURN_ID,
            *(["--todo-id", cli.TODO_ID] if explicit else []), cwd=project,
        )
        assert code == 0 and guard["normal_delivery_allowed"] is False
        assert guard["unsettled_host_turn_recovery"]["repair"] == "durable_writeback"
        assert guard["settlement_progress"]["state"] == "writeback_receipt_required"
        channel = guard["interaction_contract"]["cli_channel"]
        assert channel["settlement_plan"]["identity"] == identity
        assert channel["spend_allowed_now"] is False and "settlement_owed" not in guard
    template, = channel["next_cli_actions"]
    command = (template.replace("<validated_progress>", written["classification"])
               .replace("<scale>", written["delivery_batch_scale"])
               .replace("<outcome>", written["delivery_outcome"]))
    code, repaired = _execute(command, project, runtime, registry)
    assert code == 0 and repaired["settlement_progress"]["state"] == "settled", repaired
    assert repaired["appended"] is False and repaired["settlement_identity"] == identity
    code, replay = _refresh(project, runtime, registry, "--settle-turn", "--settlement-receipt")
    assert code == 0 and not replay["appended"] and replay["settlement_progress"]["state"] == "settled"
    assert cli._classification_count(runtime, "validated_progress") == 1
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("binding", ["explicit", "inferred", "unbound"])
def test_spend_index_conflict_keeps_its_diagnostic_and_retries_the_same_turn(
    tmp_path, monkeypatch, capsys, provider, binding,
):
    project, runtime, registry = cli._write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["quota"]["allowed_slots"] = 10
    registry.write_text(json.dumps(config))
    cli._initialize_fixture_authority(project, runtime, provider=provider)
    code, guard = _guard(project, runtime, registry)
    assert code == 0, guard
    # Older writes may settle accounting while leaving the work binding open.
    boundary = ("--delivery-boundary", "in_flight_continuation") if binding == "explicit" else ()
    code, refreshed = _refresh(project, runtime, registry, *boundary)
    assert code == 0, refreshed
    command = refreshed["settlement_owed"]["command"]

    commit = slot_accounting.record_quota_slot_spend_from_preview

    def spend_with_competing_write(preview, status, **kwargs):
        # Commit another real spend after the original preview captured its basis.
        code, competing = cli._run_cli(
            registry, runtime, "quota", "spend-slot", "--execute", "--source", "controller",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID, cwd=project,
        )
        assert code == 0 and competing["appended"] is True, competing
        return commit(preview, status, **kwargs)

    attempt = shlex.split(command)[1:]
    if binding == "inferred":
        for flag in ("--turn-instance-id", "--max-page-output-bytes"):
            index = attempt.index(flag)
            del attempt[index:index + 2]
    elif binding == "unbound":
        attempt = [
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "quota", "spend-slot", "--execute", "--source", "adapter",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        ]
    with monkeypatch.context() as patch:
        patch.chdir(project)
        patch.setattr(
            slot_accounting, "record_quota_slot_spend_from_preview", spend_with_competing_write,
        )
        code = cli_main(attempt)
    rejected = json.loads(capsys.readouterr().out)
    assert code == 1 and rejected["ok"] is False, rejected
    assert cli._spend_run_count(runtime) == 1
    assert rejected["error_code"] == "index_digest_conflict", rejected
    assert rejected["status"] == "index_digest_conflict"
    assert rejected["appended"] is False
    assert "same Turn" in rejected["recommended_action"]

    if binding == "unbound":
        assert "settlement_recovery" not in rejected
        assert "fresh preview" in rejected["recommended_action"]
        return

    recovery = rejected["settlement_recovery"]
    assert recovery["settlement_identity"] == refreshed["settlement_identity"]
    [reentry] = recovery["next_cli_actions"]
    assert "--execute" not in shlex.split(reentry)
    assert reentry in render_quota_slot_preview_markdown(rejected)
    code, guard_view = _execute(reentry, project, runtime, registry)
    still_owed = read_cli_policy_payload(json.dumps(guard_view), returncode=code)
    assert code == 0 and still_owed["settlement_owed"]["command"] == command, still_owed
    assert cli._spend_run_count(runtime) == 1
    code, recovered = _execute(still_owed["settlement_owed"]["command"], project, runtime, registry)
    assert code == 0 and recovered["settlement_progress"]["state"] == "settled", recovered
    assert recovered["settlement_identity"] == refreshed["settlement_identity"]
    assert recovered["appended"] is True
    code, replay = _execute(command, project, runtime, registry)
    assert code == 0 and replay["appended"] is False, replay
    assert replay["settlement_identity"] == recovered["settlement_identity"]
    code, guard_view = _execute(reentry, project, runtime, registry)
    settled = read_cli_policy_payload(json.dumps(guard_view), returncode=code)
    assert code == 0 and settled["heartbeat_receipt"]["settlement_identity"] == recovered["settlement_identity"]
    assert settled["heartbeat_receipt"]["status"] == "replayed"
    assert "settlement_owed" not in settled
    assert cli._spend_run_count(runtime) == 2


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_default_refresh_spend_conflict_recovers_across_profiles_and_remote_wait(
    tmp_path, monkeypatch, capsys, provider,
):
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    project, runtime, registry = cli._write_fixture(tmp_path)
    reg_data = json.loads(registry.read_text(encoding="utf-8"))
    reg_data["goals"][0]["quota"]["allowed_slots"] = 10
    registry.write_text(json.dumps(reg_data, indent=2), encoding="utf-8")
    cli._initialize_fixture_authority(project, runtime, provider=provider)

    code, guard = _guard(project, runtime, registry)
    assert code == 0, guard
    code, refreshed = cli._run_cli(
        registry, runtime, "refresh-state",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID,
        "--classification", "verified", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--no-global-sync",
        "--suppress-external-sinks", cwd=project,
    )
    assert code == 0, refreshed
    assert refreshed["settlement_progress"]["state"] == "spend_required", refreshed
    command = refreshed["settlement_owed"]["command"]

    commit = slot_accounting.record_quota_slot_spend_from_preview

    def spend_with_competing_write(preview, status, **kwargs):
        comp_code, competing = cli._run_cli(
            registry, runtime, "quota", "spend-slot", "--execute", "--source", "controller",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID, cwd=project,
        )
        assert comp_code == 0 and competing["appended"] is True, competing
        return commit(preview, status, **kwargs)

    with monkeypatch.context() as patch:
        patch.chdir(project)
        patch.setattr(
            slot_accounting, "record_quota_slot_spend_from_preview", spend_with_competing_write,
        )
        code = cli_main(shlex.split(command)[1:])
    rejected = json.loads(capsys.readouterr().out)
    assert code == 1 and rejected["error_code"] == "index_digest_conflict", rejected
    assert rejected["appended"] is False and cli._spend_run_count(runtime) == 1

    readback = read_heartbeat_settlement(
        runtime,
        goal_id=cli.GOAL_ID,
        agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID,
        turn_instance_id=cli.TURN_ID,
        registry_path=registry,
    )
    assert readback is not None and readback.replay_phase.value == "settlement_pending"

    for extra in (
        ["--codex-app", "--todo-id", cli.TODO_ID],
        ["--codex-app"],
        ["--runtime-profile", "generic_cli"],
        [],
    ):
        code, rerun = cli._run_cli(
            registry, runtime, "quota", "should-run",
            "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
            "--turn-instance-id", cli.TURN_ID, *extra, cwd=project,
        )
        assert code == 0, (extra, rerun)
        assert rerun["decision"] == "unsettled_host_turn_recovery", (extra, rerun)
        assert rerun["effective_action"] == "unsettled_host_turn_recovery", (extra, rerun)
        assert rerun["unsettled_host_turn_recovery"]["repair"] == "quota_spend", (extra, rerun)
        assert rerun["settlement_progress"]["state"] == "spend_required", (extra, rerun)
        assert rerun["settlement_owed"]["command"] == command, (extra, rerun)
        assert rerun["interaction_contract"]["cli_channel"]["next_cli_actions"] == [command]

    # Remote authority wait gating on readback.replay_phase == SETTLEMENT_PENDING must also recover.
    remote_reg = json.loads(registry.read_text(encoding="utf-8"))
    remote_reg["goals"][0]["remote_authority"] = {"enabled": True}
    registry.write_text(json.dumps(remote_reg, indent=2), encoding="utf-8")
    try:
        for extra in (["--codex-app", "--todo-id", cli.TODO_ID], ["--codex-app"], []):
            code, remote_rerun = cli._run_cli(
                registry, runtime, "quota", "should-run",
                "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
                "--turn-instance-id", cli.TURN_ID, *extra, cwd=project,
            )
            assert code == 0, (extra, remote_rerun)
            assert remote_rerun["decision"] == "unsettled_host_turn_recovery", (extra, remote_rerun)
            assert remote_rerun["effective_action"] == "unsettled_host_turn_recovery", (extra, remote_rerun)
            assert remote_rerun["unsettled_host_turn_recovery"]["repair"] == "quota_spend", (extra, remote_rerun)
            assert remote_rerun["settlement_owed"]["command"] == command, (extra, remote_rerun)
    finally:
        registry.write_text(json.dumps(reg_data, indent=2), encoding="utf-8")

    code, recovered = _execute(command, project, runtime, registry)
    assert code == 0 and recovered["appended"] is True, recovered
    assert recovered["settlement_progress"]["state"] == "settled", recovered
    assert cli._spend_run_count(runtime) == 2

    code, next_turn = _guard(project, runtime, registry, turn_id=f"{cli.TURN_ID}-next")
    assert code == 0 and next_turn["effective_action"] != "unsettled_host_turn_recovery", next_turn
