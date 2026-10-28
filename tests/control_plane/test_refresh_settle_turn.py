"""The combined CLI retains exact-Turn authority, recovery and task freedom."""
from __future__ import annotations

import json
import copy
import shlex
import subprocess

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _initialize_fixture_authority,
    _run_cli, _run_generated_cli, _spend_run_count, _write_fixture,
)


def _repository_delivery(tmp_path, provider):
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        "action_kind=validate", "action_kind=validate task_repository=git:github.com/example/delivery"))
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "remote", "add", "origin",
                    "https://github.com/example/delivery.git"], check=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
    delivery = tmp_path / "producing workspace"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "--detach", str(delivery)],
                   check=True, capture_output=True)
    # The delivered project may itself contain a different LoopX implementation.
    # Changing the accounting cwd must not execute that implementation.
    trap = delivery / "loopx"
    trap.mkdir()
    (trap / "__init__.py").write_text("raise RuntimeError('wrong implementation selected')\n")
    _initialize_fixture_authority(project, runtime, provider=provider)
    return project, runtime, registry, delivery


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_combined_settlement_uses_producing_worktree_and_originating_cli(
    tmp_path, monkeypatch, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _delivery = _repository_delivery(tmp_path, provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    identity = guard["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
    refresh = ["refresh-state", *binding, "--classification", "verified_worktree",
               "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
               "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
               "--delivery-workspace-path", "../producing workspace", "--settle-turn"]
    for replay in (False, True):
        rc, result = _run_cli(registry, runtime, *refresh, cwd=project)
        assert rc == 0, result
        assert result["settlement_progress"]["state"] == "settled"
        assert result["settlement_identity"] == identity
        assert result["delivery_workspace"]["workspace_kind"] == "independent_git_worktree"
        assert _spend_run_count(runtime) == 1
        assert result.get("idempotent_replay", False) is replay


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_wrong_or_missing_child_workspace_retains_owed_command_for_exact_recovery(
    tmp_path, monkeypatch, provider,
):
    from loopx.cli_commands.refresh_settlement import settle_refreshed_turn

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, delivery = _repository_delivery(tmp_path, provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    refresh = ["refresh-state", *binding, "--classification", "verified_worktree",
               "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
               "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
               "--delivery-workspace-path", str(delivery)]
    rc, original = _run_cli(registry, runtime, *refresh, cwd=project)
    assert rc == 0 and _spend_run_count(runtime) == 0, original
    for workspace in (project, tmp_path / "missing"):
        failed = copy.deepcopy(original)
        settle_refreshed_turn(failed, registry_path=registry, runtime_root=runtime,
            goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
            replan_obligation_id=None, turn_instance_id=TURN_ID, goal_ref=None,
            delivery_workspace_path=workspace)
        assert failed["ok"] is False, failed
        assert failed["settlement_progress"]["state"] == "spend_required"
        assert failed["settlement_owed"]["command"] == original["settlement_owed"]["command"]
        assert _spend_run_count(runtime) == 0
    rc, recovered = _run_cli(registry, runtime, *refresh, "--settle-turn", cwd=project)
    assert rc == 0, recovered
    assert recovered["settlement_progress"]["state"] == "settled"
    assert recovered["settlement_identity"] == original["settlement_identity"]
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("finish_todo", [False, True])
def test_same_turn_progress_or_completion_and_replay_without_extra_guard(
    tmp_path, monkeypatch, provider, finish_todo,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID, cwd=project)
    assert rc == 0, guard
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    validation, writeback = plan["ordered_steps"][:2]
    assert validation["command_condition"] == "todo_deliverable_complete"
    assert "semantic_closeout" in validation["precondition"]
    vision_args = []
    if finish_todo:
        rc, complete = _run_generated_cli(validation["command_template"].replace(
            "<validated evidence>", "Independent validation of the original deliverable."), registry_path=registry)
        assert rc == 0, complete
        vision = tmp_path / "vision.json"
        vision.write_text(json.dumps({
            "goal_id": GOAL_ID, "agent_id": AGENT_ID, "state": "vision_patch_proposed",
            "vision_patch": {"acceptance_summary": "Original scoped validation completed; wider Goal remains open."},
            "path_delta": {"outcome": "continue", "prior_assumption": "The scoped deliverable needed validation.",
                           "observed_reality": "The original deliverable passed independent validation.",
                           "retained": ["Original requirements"], "evidence_refs": ["evidence:validation"]},
        }))
        vision_args = ["--agent-vision-json", str(vision)]
    command = writeback["command_template"]
    for key, value in {"<validated_progress>": "verified_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<boundary>": "semantic_closeout" if finish_todo else "in_flight_continuation"}.items():
        command = command.replace(key, value)
    command += " " + shlex.join(["--settle-turn", "--no-global-sync", "--suppress-external-sinks",
        "--delivery-workspace-path", str(project), *vision_args])
    # A hostile ambient executable must not select another checkout/accounting owner.
    trap = tmp_path / "bin"
    trap.mkdir()
    (trap / "loopx").write_text("#!/bin/sh\nexit 73\n")
    (trap / "loopx").chmod(0o700)
    monkeypatch.setenv("PATH", str(trap) + ":" + __import__("os").environ["PATH"])
    for replay in (False, True):
        rc, result = _run_generated_cli(command, registry_path=registry)
        assert rc == 0, json.dumps(result, ensure_ascii=False)
        assert result["settlement_progress"]["state"] == "settled"
        assert result["settlement_identity"] == plan["identity"]
        assert _spend_run_count(runtime) == 1
        assert result.get("idempotent_replay", False) is replay
    rc, todo = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0 and todo["todo"]["status"] == ("done" if finish_todo else "open")
    events = runtime / "goals" / GOAL_ID / "rollout-events.jsonl"
    if not events.exists():
        from loopx.rollout_event_log import rollout_event_log_path
        events = rollout_event_log_path(runtime, GOAL_ID)
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    assert sum(row.get("event_kind") == "quota_should_run" for row in rows) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_failed_writeback_dry_run_and_missing_binding_cannot_spend(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    base = ["refresh-state", *binding, "--classification", "verified_slice",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--no-global-sync", "--suppress-external-sinks", "--settle-turn"]
    rc, rejected = _run_cli(registry, runtime, *base, "--delivery-outcome", "surface_only")
    assert rc != 0 and _spend_run_count(runtime) == 0, rejected
    rc, bad_path = _run_cli(registry, runtime, *base,
        "--delivery-workspace-path", "~loopx-missing-fixture-user")
    assert rc != 0 and bad_path["ok"] is False and _spend_run_count(runtime) == 0, bad_path
    rc, preview = _run_cli(registry, runtime, *base, "--delivery-boundary", "in_flight_continuation", "--dry-run")
    assert rc == 0 and preview["dry_run"] and _spend_run_count(runtime) == 0, preview
    # Invalid vision fails before writeback; no direct-debit bypass is allowed.
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "vision_patch": {"acceptance_summary": "x" * 421}}))
    rc, checkpoint = _run_cli(registry, runtime, *base, "--delivery-boundary", "semantic_closeout",
                              "--agent-vision-json", str(invalid))
    assert rc != 0 and _spend_run_count(runtime) == 0, checkpoint
    rc, missing = _run_cli(registry, runtime, "refresh-state", "--goal-id", GOAL_ID, "--settle-turn")
    assert rc != 0 and "exactly one" in missing["error"] and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("decision", ["replan", "semantic_closeout"])
def test_fresh_turn_honors_the_written_route_and_keeps_unfinished_requirements(
    tmp_path, monkeypatch, provider, decision,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text() + f"\n- [ ] [P0] Verify the independent remaining acceptance gap.\n"
        f"  <!-- loopx:todo todo_id=todo_independent_gap status=open claimed_by={AGENT_ID} task_class=advancement_task action_kind=validate -->\n")
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Scoped experiment is answered; independent acceptance remains."},
        "path_delta": {"outcome": "replan" if decision == "replan" else "continue",
            "prior_assumption": "The experiment could identify useful defects.",
            "observed_reality": "The scoped experiment is answered with retained evidence.",
            "retained": ["All original acceptance requirements"], "evidence_refs": ["evidence:scoped-result"]},
    }))
    rc, result = _run_cli(registry, runtime, "refresh-state", *binding,
        "--classification", "experiment_answered", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--delivery-boundary",
        "semantic_closeout",
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks", "--settle-turn")
    assert rc == 0, result
    rc, next_turn = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "next-route")
    assert rc == 0, next_turn
    assert next_turn["selected_todo"]["todo_id"] == "todo_independent_gap"
    assert next_turn["selected_todo"].get("selected_by") != "in_flight_todo"
    rc, original = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0 and original["todo"]["status"] == "open"
    assert "Validate and settle the selected delivery" in original["todo"]["text"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_owed_command_identity_and_lost_response_recover_without_another_debit(tmp_path, monkeypatch, provider):
    from loopx.cli_commands import refresh_settlement

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    rc, original = _run_cli(registry, runtime, "refresh-state", *binding, "--classification", "verified",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks")
    assert rc == 0 and _spend_run_count(runtime) == 0, original
    def settle(payload):
        refresh_settlement.settle_refreshed_turn(payload, registry_path=registry,
            runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
            replan_obligation_id=None, turn_instance_id=TURN_ID, goal_ref=None)
    real_run = subprocess.run
    calls = []
    def lose_response(command, **kwargs):
        calls.append(command)
        real_run(command, **kwargs)
        raise OSError("Synthetic response loss after the real commitment")
    monkeypatch.setattr(refresh_settlement.subprocess, "run", lose_response)
    wrong = copy.deepcopy(original)
    wrong["settlement_owed"]["command"] = wrong["settlement_owed"]["command"].replace(TURN_ID, "different-turn")
    settle(wrong)
    assert not wrong["ok"] and not calls and _spend_run_count(runtime) == 0
    result = copy.deepcopy(original)
    settle(result)
    assert result["ok"] and result["settlement_progress"]["state"] == "settled", result
    assert len(calls) == 1 and _spend_run_count(runtime) == 1
    monkeypatch.setattr(refresh_settlement.subprocess, "run", real_run)
    replay = copy.deepcopy(original)
    settle(replay)
    assert replay["ok"] and replay["quota_spend"]["idempotent_replay"] and _spend_run_count(runtime) == 1
