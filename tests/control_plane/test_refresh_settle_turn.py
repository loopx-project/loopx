"""The combined CLI retains exact-Turn authority, recovery and task freedom."""
from __future__ import annotations

import json
import copy
import shlex
import subprocess

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, REPO_ROOT, _initialize_fixture_authority,
    _run_cli, _run_generated_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_caller_owns_writeback_spend_and_returns_recoverable_receipt(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, original = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding,
        "--context-delivery", "off")
    assert rc == 0, original
    manual = original["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert manual["schema_version"] == "quota_settlement_plan_v1"
    assert manual["ordered_steps"][2]["owner"] == "agent"
    assert "command_template" in manual["ordered_steps"][2]
    capture = tmp_path / "capture"
    rc, view = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding,
        "--context-delivery", "on", "--decision-output-dir", str(capture))
    assert rc == 0, view
    full = json.loads((capture / "decision.json").read_text())
    cli = full["interaction_contract"]["cli_channel"]
    plan = cli["settlement_plan"]
    assert plan["schema_version"] == "quota_settlement_plan_v2"
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    spend = next(step for step in plan["ordered_steps"] if step["kind"] == "quota_spend")
    assert spend["owner"] == "runtime" and "command_template" not in spend
    assert writeback["command_template"] in cli["next_cli_actions"]
    assert not any(" quota spend-slot " in action for action in cli["next_cli_actions"])
    assert plan["identity"] == manual["identity"]
    for before, after in zip(manual["ordered_steps"], plan["ordered_steps"], strict=True):
        assert after["precondition"].startswith(before["precondition"])
        assert after.get("vision_authoring") == before.get("vision_authoring")
        assert after["expected_receipt"] == before["expected_receipt"]
    assert plan["ordered_steps"][0] == manual["ordered_steps"][0]
    assert plan["ordered_steps"][-1] == manual["ordered_steps"][-1]
    command = writeback["command_template"]
    for key, value in {"<validated_progress>": "verified_native_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<boundary>": "in_flight_continuation"}.items():
        command = command.replace(key, value)
    command += " --delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks"
    for replay in (False, True):
        rc, receipt = _run_generated_cli(command, registry_path=registry)
        assert rc == 0, receipt
        assert receipt["settlement_identity"] == plan["identity"]
        assert receipt["settlement_progress"]["state"] == "settled"
        assert receipt.get("idempotent_replay", False) is replay
        assert {step["step_kind"] for step in receipt["settlement_result"]["receipts"]} == {
            "validation", "durable_writeback", "quota_spend"}
        assert "settlement_owed" not in receipt
        assert _spend_run_count(runtime) == 1
        from loopx.control_plane.quota.settlement import read_heartbeat_settlement

        scope = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)
        complete = read_heartbeat_settlement(runtime, registry_path=registry, **scope)
        controls = read_heartbeat_settlement(runtime, registry_path=registry, include_record_bodies=False, **scope)
        assert complete.writeback_run and complete.spend_run and complete.heartbeat_receipt
        assert controls.writeback_run is None and controls.spend_run is None and controls.heartbeat_receipt is None
        assert controls.progress == complete.progress == receipt["settlement_progress"]
        for field in ("writeback", "spend", "delivery", "settlement"):
            original_result, projected_result = getattr(complete, field), getattr(controls, field)
            assert original_result.value and projected_result.value is None
            assert projected_result.receipts == original_result.receipts
            assert projected_result.failure == original_result.failure
        assert controls.identity == complete.identity
        details = receipt["result_details"]
        readback = subprocess.run(shlex.split(details["command"]), cwd=project,
            capture_output=True, text=True, check=False)
        page = json.loads(readback.stdout)
        assert readback.returncode == 0, page
        saved = json.loads(page["content"])
        assert saved["settlement_result"] == receipt["settlement_result"]
        assert saved["settlement_identity"] == receipt["settlement_identity"]
        assert "state" in saved and "state" not in receipt
        for field in ("projection_outbox", "runtime_projection_route", "shared_runtime_projection", "explore_graph_sync"):
            if field not in saved:
                continue
            assert field in details["deferred_fields"] and field not in receipt
            # A new nested obligation cannot silently become supporting IO just
            # because the known producer still says disabled/successful.
            future = copy.deepcopy(saved)
            future[field]["future_control"] = {"required_action": "inspect before continuing"}
            from loopx.cli_commands.refresh_settlement import present_settled_refresh
            projected = present_settled_refresh(future, runtime_root=runtime)
            assert projected[field] == future[field]
            unresolved = copy.deepcopy(saved)
            if field == "projection_outbox":
                unresolved[field]["next_action"] = "retry"
            elif field == "runtime_projection_route":
                unresolved[field]["conflict_count"] = 1
            elif field == "shared_runtime_projection":
                unresolved[field]["raw_artifacts_copied"] = True
            else:
                unresolved[field]["delivery_postcondition"]["blocks_delivery"] = True
            projected = present_settled_refresh(unresolved, runtime_root=runtime)
            assert projected[field] == unresolved[field]
        assert "vision_checkpoint" in receipt  # Settlement does not close acceptance.
        if replay:
            assert saved["projection_outbox"]["status"] == "current"
            assert "projection_outbox" in details["deferred_fields"]
            assert "projection_outbox" not in receipt
        assert len(json.dumps(receipt)) < len(json.dumps(saved))
    # Unknown future plans remain complete and unchanged, rather than acquiring
    # the current v2 execution ownership by a permissive fallback.
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    future = {key: copy.deepcopy(value) for key, value in original.items()
              if key not in {"schema_version", "contract_binding"}}
    future_plan = future["interaction_contract"]["cli_channel"]["settlement_plan"]
    future_plan["schema_version"] = "future_plan_for_readback"
    recomposed = compose_turn_decision(future, composed_settlement=True)
    assert recomposed["interaction_contract"]["cli_channel"]["settlement_plan"] == future_plan
    rc, todo = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0 and todo["todo"]["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_complete_source_budget_recovery_keeps_original_turn_and_single_debit(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    capture = tmp_path / "capture"
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding,
        "--context-delivery", "on", "--decision-output-dir", str(capture))
    assert rc == 0, guard
    full = json.loads((capture / "decision.json").read_text())
    assert full["schema_version"] == "loopx_turn_envelope_v1"
    plan = full["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["schema_version"] == "quota_settlement_plan_v2"
    command = next(step["command_template"] for step in plan["ordered_steps"]
                   if step["kind"] == "durable_writeback")
    for key, value in {"<validated_progress>": "verified_budget_recovery", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<boundary>": "in_flight_continuation"}.items():
        command = command.replace(key, value)
    assert "--settle-turn" in shlex.split(command)
    assert "--settlement-receipt" in shlex.split(command)
    document = tmp_path / "vision.json"
    command += " " + shlex.join(["--agent-vision-json", str(document),
        "--no-global-sync", "--suppress-external-sinks"])
    packet = {"vision_patch": {"vision_summary": "Keep the original objective.", "last_patch_summary": "s" * 244},
              "path_delta": {"outcome": "continue", "prior_assumption": "Retain the route.",
                             "observed_reality": "o" * 354, "retained": ["a", "b", "c", "d"],
                             "changed": ["Measured change.", "c" * 178], "evidence_refs": ["a", "b", "c", "d", "e"]}}
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before_state = state.read_bytes()
    before_index = index.read_bytes() if index.exists() else None
    cases = [
        (packet, ("last_patch_summary uses 244", "path_delta.observed_reality uses 354",
                  "path_delta.retained has 4 items", "path_delta.changed[1] uses 178",
                  "path_delta.evidence_refs has 5 items")),
        ({"vision_patch": {"advancement_policy": "x" * 41}},
         ("advancement_policy uses 41 chars; limit is 32",)),
        ({"vision_patch": {"advancement_policy": "invalid"}}, ("advancement_policy",)),
    ]
    for invalid, diagnostics in cases:
        document.write_text(json.dumps(invalid))
        authored = document.read_bytes()
        rc, rejected = _run_generated_cli(command, registry_path=registry)
        assert rc == 1 and rejected["appended"] is False, rejected
        for diagnostic in diagnostics:
            assert diagnostic in rejected["error"], rejected
        assert document.read_bytes() == authored and state.read_bytes() == before_state
        assert (index.read_bytes() if index.exists() else None) == before_index
        assert _spend_run_count(runtime) == 0
    # The caller explicitly fixes the authored packet. The validator never
    # truncates it, changes the decision, re-admits the Turn or spends on error.
    packet["vision_patch"]["last_patch_summary"] = "Verified the bounded change."
    packet["path_delta"].update(observed_reality="Evidence supports continuing.", retained=["Keep acceptance."],
                               changed=["Measured change."], evidence_refs=["evidence:checked"])
    document.write_text(json.dumps(packet))
    for replay in (False, True):
        rc, accepted = _run_generated_cli(command, registry_path=registry)
        assert rc == 0, accepted
        assert accepted["settlement_identity"] == plan["identity"]
        assert accepted["settlement_progress"]["state"] == "settled"
        assert accepted.get("idempotent_replay", False) is replay
        assert {step["step_kind"] for step in accepted["settlement_result"]["receipts"]} == {
            "validation", "durable_writeback", "quota_spend"}
        assert _spend_run_count(runtime) == 1
    rc, history = _run_cli(registry, runtime, "history", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    assert rc == 0, history
    goal = next(row for row in history["goals"] if row["id"] == GOAL_ID)
    saved = next(row["agent_vision"] for row in goal["latest_runs"] if row.get("agent_vision"))
    assert saved["vision_patch"] == packet["vision_patch"]
    assert saved["path_delta"] == {"schema_version": "goal_path_delta_v0", **packet["path_delta"]}
    from loopx.rollout_event_log import rollout_event_log_path
    events = [json.loads(line) for line in rollout_event_log_path(runtime, GOAL_ID).read_text().splitlines()]
    assert sum(row.get("event_kind") == "quota_should_run" for row in events) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_receipt_capture_failure_keeps_committed_receipts_and_exact_retry(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    private = runtime / "context-delivery"
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o755)
    command = ["refresh-state", *binding, "--classification", "verified_receipt_recovery",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
        "--settle-turn", "--settlement-receipt"]
    rc, failed = _run_cli(registry, runtime, *command)
    assert rc != 0 and failed["ok"] is False
    assert failed["settlement_progress"]["state"] == "settled"
    assert len(failed["settlement_result"]["receipts"]) == 3
    assert _spend_run_count(runtime) == 1
    private.chmod(0o700)
    rc, replay = _run_cli(registry, runtime, *command)
    assert rc == 0, replay
    assert replay["settlement_identity"] == failed["settlement_identity"]
    assert len(replay["settlement_result"]["receipts"]) == 3
    assert _spend_run_count(runtime) == 1


def test_receipt_keeps_unknown_controls_and_never_hides_partial_failure(tmp_path):
    from loopx.cli_commands.refresh_settlement import present_settled_refresh

    payload = {"ok": True, "settlement_progress": {"state": "settled"},
        "state": {"large_supporting_snapshot": "x" * 3000},
        "future_control": {"required_resolution": "preserve exactly"},
        "projection_outbox": {"status": "pending", "next_action": "retry"}}
    receipt = present_settled_refresh(payload, runtime_root=tmp_path)
    assert receipt["future_control"] == payload["future_control"]
    assert receipt["projection_outbox"] == payload["projection_outbox"]
    for update in ({"ok": False}, {"dry_run": True}, {"settlement_owed": {"command": "original"}},
                   {"settlement_progress": {"state": "spend_required"}}):
        original = {**payload, **update}
        assert present_settled_refresh(original, runtime_root=tmp_path) is original
    large = {**payload, "future_control": {"required_resolution": "x" * 100000}}
    bounded = present_settled_refresh(large, runtime_root=tmp_path)
    assert "Read the complete selected result controls before acting" in bounded["instruction"]
    assert "Supporting snapshots" in bounded["instruction"]
    command, bodies = bounded["command"], []
    while command:
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, check=False)
        page = json.loads(result.stdout)
        assert result.returncode == 0 and len(result.stdout.encode()) <= 64000
        bodies.append(page["content"])
        command = page.get("next_command")
    selected = {row["pointer"]: row["value"] for row in json.loads("".join(bodies))["selections"]}
    assert selected["/future_control"] == large["future_control"]


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
    assert "semantic_closeout" in writeback["precondition"]
    assert " --delivery-boundary semantic_closeout " in writeback["semantic_closeout_command_template"]
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
    rc, receipt_only = _run_cli(registry, runtime, "refresh-state", *binding, "--settlement-receipt")
    assert rc != 0 and "requires --settle-turn" in receipt_only["error"] and _spend_run_count(runtime) == 0


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
            replan_obligation_id=None, turn_instance_id=TURN_ID, goal_ref=None,
            delivery_workspace_path=REPO_ROOT)
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
