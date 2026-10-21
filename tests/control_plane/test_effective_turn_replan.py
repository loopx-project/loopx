"""Receipt-backed cadence over an open Todo, through the real CLI and runtime."""

import json
import shlex
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from loopx.capabilities.todo_replan_cadence.machine_defaults import resolve_todo_replan_cadence_goal
from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from tests.control_plane.test_quota_settlement_cli import (
    _write_fixture,
    _run_cli,
    AGENT_ID,
    GOAL_ID,
    TODO_ID,
    _configure_selectable_alternative,
    _initialize_fixture_authority,
    ALTERNATIVE_TODO_ID,
)
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.work_items.replan_history_codec import (
    effective_turn_cadence_context,
)
from loopx.control_plane.work_items.replan_history_codec import project_replan_history
from loopx.control_plane.work_items.semantic_replan_writeback import (
    qualify_replan_writeback,
)


@pytest.mark.parametrize("threshold_override,device_count,threshold,declare_outcome", [
    (None, None, 6, False), (None, None, 6, True), (2, 3, 2, False), (None, 3, 3, False),
    (5, 6, 5, False), (None, 5, 5, False), (6, None, 6, False), (None, 6, 6, False),
])
def test_open_todo_settled_turn_cadence_and_evidence_linked_review(
    tmp_path: Path, threshold_override: int | None, device_count: int | None,
    threshold: int, declare_outcome: bool,
) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    fixture = json.loads(registry.read_text())
    fixture["goals"][0]["quota"]["allowed_slots"] = threshold + 2
    if declare_outcome:
        fixture["goals"][0]["status"] = "active"
    registry.write_text(json.dumps(fixture))
    if device_count is not None:
        machine_registry = build_builtin_machine_configuration_registry()
        configuration = {
            "schema_version": "loopx_machine_configuration_v0",
            "namespaces": {"todo_replan_cadence": {
                "schema_version": "todo_replan_cadence_machine_defaults_v1",
                "count_unit": "effective_turns", "count": device_count,
            }},
        }
        preview = configure_machine_configuration(
            runtime_root=runtime, registry=machine_registry, configuration=configuration,
        )
        configure_machine_configuration(
            runtime_root=runtime, registry=machine_registry, configuration=configuration,
            execute=True, expected_plan_revision=preview["plan_revision"],
        )
    rc, configured = _run_cli(
        registry,
        runtime,
        "configure-goal",
        "--goal-id",
        GOAL_ID,
        *(["--execution-replan-after-turns", str(threshold_override), "--execute"]
          if threshold_override else []),
    )
    assert rc == 0, configured
    goal = json.loads(registry.read_text())["goals"][0]
    if threshold_override:
        assert goal["execution_profile"]["replan_after_effective_turns"] == threshold_override
    else:
        assert "replan_after_effective_turns" not in goal.get("execution_profile", {})
    context = effective_turn_cadence_context(
        resolve_todo_replan_cadence_goal(goal, runtime), runtime,
    )
    original_outcomes = None
    if declare_outcome:
        _initialize_fixture_authority(project, runtime)
        rc, basis = _run_cli(registry, runtime, "goal-acceptance", "inspect", "--goal-id", GOAL_ID)
        assert rc == 0, basis
        outcome_path = tmp_path / "ongoing-outcome.json"
        outcome_path.write_text(json.dumps({"outcome_id": "ongoing-delivery", "owner_agent_id": AGENT_ID,
            "objective": "Deliver the complete requested result", "todo_ids": [TODO_ID],
            "requirements": [{"id": "complete", "description": "Meet the complete acceptance scope"}],
            "source": {"ref": "request:complete-delivery", "summary": "Preserve the requested result across Turns"}}))
        rc, declared = _run_cli(registry, runtime, "goal-acceptance", "refine", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--document", str(outcome_path), "--operation-id", "declare-ongoing-outcome",
            "--expected-provider-revision", basis["provider_revision"], "--execute")
        assert rc == 0, declared
        original_outcomes = declared["goal_acceptance_contract"]["outcome_requirements"]

    def periodic():
        path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        runs = (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )
        # Deliberate retries cannot inflate the number of accepted Turns.
        return project_replan_history(
            list(reversed(runs)) * 3,
            operation="periodic",
            agent_id=AGENT_ID,
            effective_turn_cadence=context,
        )

    for i in range(threshold):
        turn = f"effective-turn-{i}"
        rc, guard = _run_cli(
            registry,
            runtime,
            "quota",
            "should-run",
            "--codex-app",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--turn-instance-id",
            turn,
            "--scan-path",
            str(project),
        )
        assert rc == 0, guard
        assert guard["selected_todo"]["todo_id"] == TODO_ID
        rc, refreshed = _run_cli(
            registry,
            runtime,
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--classification",
            "validated_progress",
            "--delivery-batch-scale",
            "implementation",
            "--delivery-outcome",
            "outcome_progress",
            "--delivery-boundary",
            "in_flight_continuation",
            "--agent-id",
            AGENT_ID,
            "--todo-id",
            TODO_ID,
            "--turn-instance-id",
            turn,
            "--no-global-sync",
            "--suppress-external-sinks",
        )
        assert rc == 0, json.dumps(refreshed)
        assert periodic() is None, "unsettled writeback must not advance cadence"
        rc, spent = _run_cli(
            registry,
            runtime,
            "quota",
            "spend-slot",
            "--goal-id",
            GOAL_ID,
            "--slots",
            "1",
            "--source",
            "heartbeat",
            "--execute",
            "--agent-id",
            AGENT_ID,
            "--todo-id",
            TODO_ID,
            "--turn-instance-id",
            turn,
        )
        assert rc == 0, spent
        if i < threshold - 1:
            assert periodic() is None
    trigger = periodic()
    assert trigger is not None
    assert trigger["kind"] == "periodic_review_due"
    assert trigger["run_count"] == threshold
    rc, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    assert (
        next(row for row in listed["todos"] if row["todo_id"] == TODO_ID)["status"]
        == "open"
    )
    rc, next_guard = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "effective-turn-next",
        "--scan-path",
        str(project),
    )
    assert rc == 0, next_guard
    obligation = next_guard.get("autonomous_replan_obligation")
    assert obligation is not None, next_guard
    assert "periodic_review_due" in json.dumps(obligation)
    if original_outcomes:
        assert obligation["replan_context"]["core_goal"]["acceptance_contract"]["outcome_requirements"] == original_outcomes
        assert original_outcomes["acceptance_gaps"] == [], "periodic review must include requirements covered by open work"
    else:
        assert obligation["replan_context"]["core_goal"]["acceptance_contract"] is None
    assert any("even with open Todos" in line for line in next_guard["replan_action_packet"]["planning_guidance"])
    runs = [
        json.loads(line)
        for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl")
        .read_text()
        .splitlines()
    ]
    writeback_obligation, _ = qualify_replan_writeback(
        newest_first_runs=list(reversed(runs)),
        state_text=(project / goal["state_file"]).read_text(),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        registry_goal=goal,
        effective_turn_cadence=context,
    )
    assert writeback_obligation is not None
    assert writeback_obligation["obligation_id"] == obligation["obligation_id"]

    # A durable debit without its committed receipt is not an effective Turn.
    # This is an isolated fixture, never a mutation of a live Goal.
    log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    original = log.read_bytes()
    events = [json.loads(line) for line in original.splitlines()]
    log.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in events
            if not (
                row.get("run_id") == f"effective-turn-{threshold - 1}"
                and row.get("event_kind") == "quota_spend"
            )
        )
    )
    try:
        assert periodic() is None
    finally:
        log.write_bytes(original)

    # A cadence checkpoint is a review, not an obligation to invent a new route.
    # Execute the projected vision path against the real isolated File runtime.
    selected_rc, selected = _run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "effective-turn-next", "--scan-path", str(project),
    )
    assert selected_rc == 0, selected
    contract = selected["replan_action_packet"]["writeback_contract"]
    assert contract["preferred_input"] == "evidence_linked_vision_path"
    plan = selected["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert contract["vision_authoring_ref"] == (
        "$.interaction_contract.cli_channel.settlement_plan.ordered_steps[1].vision_authoring"
    )
    authoring = plan["ordered_steps"][1]["vision_authoring"]
    assert authoring["schema_version"] == "goal_vision_replan_contract_v0"
    if threshold_override is None and device_count is None:
        rc, envelope = _run_cli(
            registry, runtime, "quota", "should-run", "--codex-app", "--turn-envelope",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
            "--turn-instance-id", "effective-turn-next", "--scan-path", str(project),
        )
        assert rc == 0, envelope
        assert envelope["replan_action_packet"]["planning_guidance"] == selected["replan_action_packet"]["planning_guidance"]
        assert envelope["replan_action_packet"]["writeback_contract"]["vision_authoring"] == authoring
        assert envelope["writeback"]["settlement_plan"] == plan
    vision_path = tmp_path / "periodic-vision.json"
    vision = json.loads(json.dumps(authoring["minimal_example"]))
    vision["vision_patch"] = {"acceptance_summary": "Original acceptance remains open."}
    vision["path_delta"] = {"schema_version": "goal_path_delta_v0", "outcome": "replan",
                       "prior_assumption": "The existing method remains viable.",
                       "observed_reality": "Current validation supports continuing it.",
                       "retained": ["Existing method and open Todo"], "evidence_refs": []}
    command = shlex.split(selected["interaction_contract"]["cli_channel"]["next_cli_actions"][0])
    command = command[command.index("refresh-state"):]
    command[command.index("--agent-vision-json") + 1] = str(vision_path)
    command += ["--delivery-workspace-path", str(project), "--no-global-sync", "--suppress-external-sinks"]
    vision_path.write_text(json.dumps(vision))
    rc, rejected = _run_cli(registry, runtime, *command)
    assert rc == 1 and rejected["appended"] is False, rejected
    assert "typed semantic delta" in rejected["error"], rejected
    vision["path_delta"]["evidence_refs"] = ["evidence:method-validation"]
    vision_path.write_text(json.dumps(vision))
    rc, accepted = _run_cli(registry, runtime, *command)
    assert rc == 0, accepted
    assert accepted["autonomous_replan_ack"]["semantic_delta"]["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    rc, spent = _run_cli(
        registry, runtime, "quota", "spend-slot", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "effective-turn-next", "--slots", "1",
        "--source", "heartbeat", "--execute",
    )
    assert rc == 0 and spent["settlement_progress"]["state"] == "settled", json.dumps(spent)
    rc, following = _run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "after-periodic-review", "--scan-path", str(project),
    )
    assert rc == 0 and following["decision"] == "run", following
    assert following["selected_todo"]["status"] == "open"

    rc, cleared = _run_cli(
        registry,
        runtime,
        "configure-goal",
        "--goal-id",
        GOAL_ID,
        "--clear-execution-replan-after-turns",
        "--execute",
    )
    assert rc == 0, cleared
    assert (
        effective_turn_cadence_context(
            resolve_todo_replan_cadence_goal(
                json.loads(registry.read_text())["goals"][0], runtime,
            ), runtime,
        )["threshold"]
        == (device_count or 6)
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_due_stride_review_survives_successor_transition_until_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    data = json.loads(registry.read_text())
    data["goals"][0]["quota"]["allowed_slots"] = 12
    registry.write_text(json.dumps(data))
    rc, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, todos=listed["todos"], handoff_mode="soft_claim", leases=[]),
        state_path=project / data["goals"][0]["state_file"], provider=provider)

    def call(*args: str, expected_code: int = 0) -> dict:
        code, result = _run_cli(registry, runtime, *args, cwd=project)
        assert code == expected_code, json.dumps(result, indent=2)
        return result

    call("configure-goal", "--goal-id", GOAL_ID, "--execution-replan-after-turns", "2",
         "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")
    for index in range(2):
        turn = f"stride-cadence-{index}"
        guard = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
                     "--agent-id", AGENT_ID, "--turn-instance-id", turn,
                     "--scan-path", str(project))
        assert guard["selected_todo"]["todo_id"] == TODO_ID
        call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--todo-id", TODO_ID, "--turn-instance-id", turn,
             "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
             "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
             "--no-global-sync", "--suppress-external-sinks")
        call("quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--todo-id", TODO_ID, "--turn-instance-id", turn,
             "--slots", "1", "--source", "heartbeat", "--execute")

    guard = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
                 "--agent-id", AGENT_ID, "--turn-instance-id", "stride-due-review",
                 "--scan-path", str(project))
    obligation = guard["autonomous_replan_obligation"]
    assert "periodic_review_due" in {trigger["kind"] for trigger in obligation["triggers"]}
    assert guard["replan_action_packet"]["stride_control"]["required_on_periodic_review"] is True
    value_review = guard["replan_action_packet"]["stride_control"]["decision_value_review"]
    assert set(value_review) == {"work_slice", "route_distance", "validation_scope"}
    # Exercise the real File/SQLite CLI delivery of both sides of the advice:
    # a heuristic can be reconsidered prospectively, but failure and authority
    # cannot be rewritten. The existing decision/writeback contract stays legal.
    validation_advice = value_review["validation_scope"]
    assert "original constraint or frozen study criterion" in validation_advice
    assert "agent-proposed candidate heuristic" in validation_advice
    assert "compare the incumbent under the same relevant control" in validation_advice
    assert "cannot turn an earlier failure into a pass" in validation_advice
    assert "authorized informative evaluation" in validation_advice
    assert "evidence sufficient to replace the incumbent" in validation_advice
    successor = call("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--claimed-by", AGENT_ID, "--text", "Validate a new independent source",
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--target-key", "independent-source", "--replan-obligation-id", obligation["obligation_id"])
    assert successor["todo_id"]
    # Choose the alternative to the owned default explicitly. The autonomous
    # replan route must not depend on the original delivery being unclaimed.
    assert guard["agent_lane_next_action"]["todo_id"] == TODO_ID
    reread = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
                  "--agent-id", AGENT_ID, "--todo-id", successor["todo_id"],
                  "--turn-instance-id", "stride-due-review",
                  "--scan-path", str(project))
    assert reread["heartbeat_receipt"]["settlement_identity"]["replan_obligation_id"] == obligation["obligation_id"]
    assert reread["autonomous_replan_obligation"]["obligation_id"] == obligation["obligation_id"]
    assert reread["autonomous_replan_obligation"].get("resolution_mode") != "receipt_bound_replan_settlement"
    assert reread["replan_action_packet"]["stride_control"]["required_on_periodic_review"] is True
    assert reread["replan_action_packet"]["stride_control"]["decision_value_review"] == value_review
    rejected = call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--replan-obligation-id", obligation["obligation_id"],
        "--turn-instance-id", "stride-due-review",
        "--classification", "typed_blocker_writeback", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
        "--progress-blocker-id", "fixture:blocked-source",
        "--progress-evidence-id", "fixture:blocked-source-evidence",
        "--no-global-sync", "--suppress-external-sinks", expected_code=1)
    assert rejected.get("appended") is not True
    assert "stride_decision" in rejected["error"]
    decision_path = tmp_path / "stride-review.json"
    control = reread["replan_action_packet"]["stride_control"]
    decision = {
        "schema_version": "stride_decision_v2", "strategy_version": "evidence_conditioned_v1",
        "reference_context_id": control["reference_context_id"],
        "reference_obligation_id": obligation["obligation_id"],
        "basis": "uncertain_evidence", "action": "verify",
        "axes": {
            "work_slice": {"plan": "Complete the independent source validation.",
                           "reason": "One complete check resolves the pending evidence question."},
            "route_distance": {"plan": "Retain the current delivery route while checking its source.",
                               "reason": "No result yet supports replacing the route."},
            "validation_scope": {"plan": "Check the source result and a contrary case.",
                                 "reason": "Both cases distinguish a usable source from a false lead."},
        },
        "next_probe": {"scope": "One bounded independent source validation.",
                       "prediction": "The source supports the original acceptance.",
                       "adoption_criterion": "Adopt only if the checked source meets acceptance.",
                       "cost_bound": "One source and its contrary case before replanning.",
                       "minimum_evidence": "A checked result and a contrary-case result."},
    }
    vision_packet = {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "The original delivery remains open while the new source is validated."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "replan",
            "prior_assumption": "The first source was sufficient for the next validation.",
            "observed_reality": "The reviewed evidence identifies another source to validate.",
            "retained": ["Original acceptance and validation boundary"],
            "changed": ["Validate the independent source before claiming completion"],
            "evidence_refs": ["evidence:independent-source-review"]},
        "stride_decision": decision,
        "todo_delta": [f"create:{successor['todo_id']}", "create:compare two methods"],
    }
    decision_path.write_text(json.dumps({**vision_packet,
        "stride_decision": {**decision, "reference_context_id": "replan-context-" + "b" * 24}}))
    foreign = call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--replan-obligation-id", obligation["obligation_id"],
        "--turn-instance-id", "stride-due-review", "--agent-vision-json", str(decision_path),
        "--classification", "bounded_replan_progress", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--no-global-sync", "--suppress-external-sinks",
        expected_code=1)
    assert foreign.get("appended") is not True
    assert "context" in foreign["error"].lower()
    decision_path.write_text(json.dumps(vision_packet))
    accepted = call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--replan-obligation-id", obligation["obligation_id"],
        "--turn-instance-id", "stride-due-review", "--agent-vision-json", str(decision_path),
        "--classification", "bounded_replan_progress", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--no-global-sync", "--suppress-external-sinks")
    assert accepted["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
    persisted = json.loads(Path(accepted["json_path"]).read_text())
    assert persisted["agent_vision"]["stride_decision"] == decision
    assert persisted["settlement_identity"]["schema_version"] == "quota_settlement_identity_v1"
    assert persisted["replan_obligation_id"] == obligation["obligation_id"]
    call("quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
         "--replan-obligation-id", obligation["obligation_id"], "--turn-instance-id", "stride-due-review",
         "--slots", "1", "--source", "heartbeat", "--execute")

    # Continue through real authority/history, rather than injecting a crafted
    # context. The next periodic review must see the accepted autonomous decision.
    for index in range(2):
        turn = f"stride-followthrough-{index}"
        call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--todo-id", successor["todo_id"], "--turn-instance-id", turn,
             "--scan-path", str(project))
        # Synthetic candidate work under the real selected Todo. A worse
        # result is still evidence; it must not be rewritten into goal gain.
        subprocess.run([sys.executable, "-c",
            "import json,sys; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(json.dumps({'reference_error':sum(x*x for x in (1,1)),"
            "'candidate_error':sum(x*x for x in (1,2))}))",
            str(project / f"candidate-result-{index}.json")], check=True)
        work = call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--todo-id", successor["todo_id"], "--turn-instance-id", turn,
             "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
             "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
             "--recommended-action", "The candidate regressed (error 5 versus reference 2); retain the reference and compare another source.",
             *(["--progress-result-class", "unchanged", "--progress-evidence-id", f"fixture:negative-result-{index}"]
               if index == 0 else []),
             "--no-global-sync", "--suppress-external-sinks")
        call("quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--todo-id", successor["todo_id"], "--turn-instance-id", turn,
             "--slots", "1", "--source", "heartbeat", "--execute")
    call("todo", "complete", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
         "--todo-id", successor["todo_id"])
    next_guard = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID,
        "--turn-instance-id", "stride-next-review", "--scan-path", str(project))
    next_obligation = next_guard["autonomous_replan_obligation"]
    delivered_result = next_obligation["replan_context"]["decision_followthrough"]["ordinary_work"]["evidence"][0]
    observed_result = call("history", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                          "--evidence-ref", delivered_result["evidence_ref"])["evidence"]
    assert "regressed (error 5 versus reference 2)" in observed_result["recommended_action"]
    # The actor can now discover the actual ordinary result from the review,
    # retain the reference, and choose a successor through the existing owner.
    measurement = json.loads((project / "candidate-result-1.json").read_text())
    next_task = "Compare another source while retaining the reference" if measurement["candidate_error"] > measurement["reference_error"] else "Adopt the candidate"
    next_successor = call("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--claimed-by", AGENT_ID, "--text", next_task,
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--target-key", "source-uncertainty", "--replan-obligation-id", next_obligation["obligation_id"])
    next_guard = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", next_successor["todo_id"],
        "--turn-instance-id", "stride-next-review", "--scan-path", str(project))
    next_obligation = next_guard["autonomous_replan_obligation"]
    next_control = next_guard["replan_action_packet"]["stride_control"]
    follow = next_obligation["replan_context"]["decision_followthrough"]
    previous = follow["previous_decision"]
    assert previous["todo_id"] is None
    assert previous["replan_obligation_id"] == obligation["obligation_id"]
    assert previous["stride_decision"] == decision
    assert follow["execution_and_goal_gain"] == "unknown"
    assert next_control["previous_decision_ref"] == previous["evidence_ref"]
    assert next_control["decision_followthrough"] == follow
    assert follow["todo_source_available"] is True
    planned = follow["planned_work"]
    assert len(planned) == 1 and planned[0]["todo_id"] == successor["todo_id"]
    assert planned[0]["current"]["claimed_by"] == AGENT_ID
    assert planned[0]["current"]["status"] == "done", "A negative experiment can end while the Goal continues"
    assert len(planned[0]["observation_refs"]) == 1
    assert follow["unresolved_planned_work_count"] == 1
    last_work = json.loads(Path(work["json_path"]).read_text())
    assert "progress_observation" not in last_work
    ordinary_ref = delivered_result["evidence_ref"]
    # The optional typed-progress attachment is not required for the Agent to
    # discover a declared experiment's ordinary result in its next review.
    assert follow["ordinary_work"]["recorded_count"] == 1
    assert follow["ordinary_work"]["evidence"] == [{
        "evidence_ref": ordinary_ref, "generated_at": last_work["generated_at"],
        "todo_id": successor["todo_id"],
    }]
    assert planned[0]["ordinary_evidence_refs"] == [ordinary_ref]
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    envelope = build_turn_envelope(next_guard)
    assert envelope["replan_action_packet"]["stride_control"]["decision_followthrough"] == follow
    resolved = call("history", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                    "--evidence-ref", previous["evidence_ref"])
    assert resolved["evidence"]["stride_decision"] == decision
    next_decision = {**decision, "reference_context_id": next_control["reference_context_id"],
                     "reference_obligation_id": next_obligation["obligation_id"], "action": "switch"}
    vision_packet = {**vision_packet, "todo_delta": [f"create:{next_successor['todo_id']}"]}
    refresh_args = ("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--replan-obligation-id", next_obligation["obligation_id"], "--turn-instance-id", "stride-next-review",
        "--agent-vision-json", str(decision_path), "--classification", "bounded_replan_progress",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks")
    decision_path.write_text(json.dumps({**vision_packet, "stride_decision": next_decision}))
    missing_review = call(*refresh_args, expected_code=1)
    assert missing_review.get("appended") is not True
    assert "previous" in missing_review["error"].lower()
    next_decision["previous_review"] = {"reference_decision_ref": previous["evidence_ref"],
        "adoption": "executed", "information_gain": "new_evidence", "goal_gain": "regressed",
        "evidence_refs": [previous["history_evidence_ref"]],
        "lesson": "The completed candidate regressed; retain the reference artifact and scoped negative result."}
    decision_path.write_text(json.dumps({**vision_packet, "stride_decision": next_decision}))
    old_evidence = call(*refresh_args, expected_code=1)
    assert old_evidence.get("appended") is not True
    assert "subsequent" in old_evidence["error"].lower()
    next_decision["previous_review"]["evidence_refs"] = [ordinary_ref]
    decision_path.write_text(json.dumps({**vision_packet, "stride_decision": next_decision}))
    next_accepted = call(*refresh_args)
    assert next_accepted["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
    replay = call(*refresh_args)
    assert replay.get("appended") is not True
    # A review/citation alone leaves this result pending. Actual subsequent
    # work under the declared next Todo supplies the recorded association.
    call("quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
         "--replan-obligation-id", next_obligation["obligation_id"], "--turn-instance-id", "stride-next-review",
         "--slots", "1", "--source", "heartbeat", "--execute")
    # An unbound observation may name the declared work without claiming a Turn
    # settlement. The public CLI rejects surface-only Turn settlement, but this
    # ordinary status route must not consume the cited negative result either.
    status_update = call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--progress-work-item-id", next_successor["todo_id"], "--progress-result-class", "unchanged",
        "--classification", "status_readback",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "surface_only",
        "--recommended-action", "The next probe remains planned; no new experiment has run.",
        "--vision-summary", "The accepted source comparison is still pending; this is a status clarification only.",
        "--no-global-sync", "--suppress-external-sinks")
    status_run = json.loads(Path(status_update["json_path"]).read_text())
    assert status_run["delivery_outcome"] == "surface_only"
    assert status_run["agent_vision"], "the ordinary source must enter the causal history replay"
    from loopx.control_plane.work_items.replan_context_codec import project_replan_context
    from loopx.history import load_index_snapshot

    context = project_replan_context(goal_id=GOAL_ID, agent_id=AGENT_ID,
        runs=load_index_snapshot(runtime / "goals" / GOAL_ID / "runs/index.jsonl",
                                 include_artifact_status=False).records,
        history_newest_first=False, stride_strategy="evidence_conditioned_v1")
    assert any(row["coverage_ref"] == status_run["progress_observation"]["fingerprint"]
               for row in context["decision_followthrough"]["observations"])
    pending = context["decision_followthrough"]["work_result_use"]
    assert any(row["evidence_ref"] == ordinary_ref for row in pending["unresolved"])
    assert not any(row["result_evidence_ref"] == ordinary_ref for row in pending["recorded_followthrough"])
    for index in range(2):
        turn = f"adopted-probe-{index}"
        guard = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", next_successor["todo_id"], "--turn-instance-id", turn,
            "--scan-path", str(project))
        assert guard["selected_todo"]["todo_id"] == next_successor["todo_id"]
        subprocess.run([sys.executable, "-c",
            "import json,sys; from pathlib import Path; "
            "old=json.loads(Path(sys.argv[1]).read_text()); "
            "Path(sys.argv[2]).write_text(json.dumps({'retained_error':old['reference_error'],'alternative_error':1}))",
            str(project / "candidate-result-1.json"), str(project / f"adopted-probe-{index}.json")], check=True)
        call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--todo-id", next_successor["todo_id"], "--turn-instance-id", turn,
            "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
            "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
            "--recommended-action", "The alternate probe has an attributable artifact; validate transfer before replacing the incumbent.",
            "--no-global-sync", "--suppress-external-sinks")
        call("quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--todo-id", next_successor["todo_id"], "--turn-instance-id", turn,
            "--slots", "1", "--source", "heartbeat", "--execute")
    final = call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", "review-adopted-probe", "--scan-path", str(project))
    use = final["autonomous_replan_obligation"]["replan_context"]["decision_followthrough"]["work_result_use"]
    assert any(row["result_evidence_ref"] == ordinary_ref and row["work_todo_id"] == next_successor["todo_id"]
               for row in use["recorded_followthrough"])
    assert final["autonomous_replan_obligation"]["replan_context"]["decision_followthrough"]["execution_and_goal_gain"] == "unknown"
    assert json.loads((project / "adopted-probe-1.json").read_text())["retained_error"] == 2


@pytest.mark.parametrize("invalid", [0, 7, True, 2.5, "2"])
def test_effective_cadence_rejects_invalid_units(invalid):
    from loopx.execution_profile import compact_execution_profile

    with pytest.raises(ValueError, match="integer from 1 to 6"):
        compact_execution_profile({"replan_after_effective_turns": invalid})


def test_verified_negative_work_counts_without_a_score_gain(tmp_path: Path) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    _initialize_fixture_authority(project, runtime)
    binding = (
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "negative-turn",
    )
    rc, guard = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0, guard
    due = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(
        timespec="seconds"
    )
    rc, waiting = _run_cli(
        registry,
        runtime,
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--status",
        "open",
        "--resume-when",
        f"resume_at:{due}",
        "--successor-todo-id",
        ALTERNATIVE_TODO_ID,
    )
    assert rc == 0, json.dumps(waiting)
    rc, refreshed = _run_cli(
        registry,
        runtime,
        "refresh-state",
        *binding,
        "--classification",
        "typed_blocker_writeback",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_gap",
        "--progress-result-class",
        "blocked",
        "--progress-blocker-id",
        "fixture:blocker",
        "--progress-evidence-id",
        "fixture:negative-result",
        "--no-global-sync",
        "--suppress-external-sinks",
        cwd=project,
    )
    assert rc == 0, json.dumps(refreshed)
    assert refreshed["settlement_progress"]["state"] == "settled"
    runs = [
        json.loads(line)
        for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl")
        .read_text()
        .splitlines()
    ]
    trigger = project_replan_history(
        list(reversed(runs)),
        operation="periodic",
        agent_id=AGENT_ID,
        effective_turn_cadence={
            "threshold": 1,
            "settlement_source": {"runtime_root": str(runtime), "goal_id": GOAL_ID},
        },
    )
    assert trigger is not None
    assert trigger["run_count"] == 1


def test_profile_rejects_two_counting_units():
    from loopx.execution_profile import compact_execution_profile

    with pytest.raises(ValueError, match="choose one review cadence unit"):
        compact_execution_profile(
            {"replan_after_effective_turns": 2, "replan_after_completed_todos": 3}
        )


def test_source_cadence_uses_live_admission_and_borrows_refresh_locks(tmp_path):
    from tests.control_plane.test_quota_spend_commit_runtime import (
        _write_source_registry,
        INSTANCE_A,
        INSTANCE_B,
        GOAL_ID as source_goal_id,
    )
    from loopx.control_plane.quota.accounting_admission import (
        quota_accounting_admission,
    )
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    runtime = tmp_path / "runtime"
    registry = tmp_path / "project" / ".loopx" / "registry.json"
    _write_source_registry(registry, runtime, INSTANCE_A)
    goal = {
        "id": source_goal_id,
        "goal_instance_id": INSTANCE_A,
        "execution_profile": {"replan_after_effective_turns": 2},
    }
    context = effective_turn_cadence_context(goal, runtime, registry_path=registry)
    assert (
        project_replan_history(operation="periodic", effective_turn_cadence=context)
        is None
    )
    with quota_accounting_admission(
        runtime_root=runtime,
        registry_path=registry,
        goal_id=source_goal_id,
        goal_ref=context["goal_ref"],
        operation="fixture-enclosing-refresh",
    ) as admission:
        borrowed = effective_turn_cadence_context(
            goal, runtime, registry_path=registry, source_admission=admission
        )
        assert (
            project_replan_history(
                operation="periodic", effective_turn_cadence=borrowed
            )
            is None
        )
        # An actual second RPC must still adopt the enclosing live lock witnesses.
        read_heartbeat_settlement(
            runtime,
            goal_id=source_goal_id,
            agent_id=AGENT_ID,
            todo_id=TODO_ID,
            turn_instance_id="empty-source-turn",
            goal_ref=context["goal_ref"],
            registry_path=registry,
            source_admission=admission,
            borrow_source_admission=True,
        )
    _write_source_registry(registry, runtime, INSTANCE_B)
    from loopx.control_plane.effect_runtime import EffectRuntimeConflict

    with pytest.raises(EffectRuntimeConflict, match="stale_goal_instance"):
        project_replan_history(operation="periodic", effective_turn_cadence=context)
    without_ref = effective_turn_cadence_context(
        {key: value for key, value in goal.items() if key != "goal_instance_id"},
        runtime,
        registry_path=registry,
    )
    with pytest.raises(ValueError, match="requires an exact GoalRef"):
        project_replan_history(operation="periodic", effective_turn_cadence=without_ref)
