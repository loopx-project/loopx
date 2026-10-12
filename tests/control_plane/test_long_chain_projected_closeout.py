"""Follow the long-chain packet through one real, durable CLI Turn (#4667)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, TURN_ID,
    _bind_selected_replan_guard, _configure_selected_todo_replan_fixture,
    _projected_cli_args,
    _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("advancement,monitors,replan_required", [
    (9, 19, False), (14, 6, False), (15, 19, True),
])
def test_managed_turn_counts_advancement_without_monitor_backlog(
    tmp_path: Path, advancement: int, monitors: int, replan_required: bool,
) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    header = state.read_text(encoding="utf-8").split("## Agent Todo\n\n")[0]
    rows = [
        f"- [ ] [P1] Validate bounded slice {index}.\n"
        f"  <!-- loopx:todo todo_id=todo_chain_{index:012d} status=open "
        "task_class=advancement_task action_kind=validate "
        f"claimed_by={AGENT_ID} updated_at=2026-08-01T00%3A00%3A00Z -->\n"
        for index in range(advancement)
    ]
    rows.extend(
        f"- [ ] [P2-monitor] Watch independent target {index}.\n"
        f"  <!-- loopx:todo todo_id=todo_monitor_{index:012d} status=open "
        "task_class=continuous_monitor action_kind=observe "
        f"claimed_by={AGENT_ID} target_key=synthetic-target-{index} "
        "cadence=1h next_due_at=2099-01-01T00%3A00%3A00Z "
        "updated_at=2026-08-01T00%3A00%3A00Z -->\n"
        for index in range(monitors)
    )
    state.write_text(header + "## Agent Todo\n\n" + "".join(rows), encoding="utf-8")

    # The public managed-Turn entrypoint must keep both the first read and replay
    # free of a long-chain duty introduced solely by independent monitors.
    for _ in range(2):
        rc, result = _run_cli(
            registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--turn-instance-id", TURN_ID, "--scan-path", str(project), cwd=project,
        )
        assert rc == 0, result
        frontier = result["goal_frontier_projection"]
        assert frontier["remaining_advancement_frontier"][
            "current_agent_claimed_advancement_count"
        ] == advancement
        assert frontier["replan_required"] is replan_required
        assert result["selected_todo"]["todo_id"] == SELECTED_REPLAN_TODO_ID
        obligation = result["heartbeat_recommendation"].get("replan_obligation")
        if replan_required:
            assert [trigger["kind"] for trigger in obligation["triggers"]] == ["long_todo_chain"]
            assert obligation["triggers"][0]["count_kind"] == "claimed_advancement_todos"
            assert obligation["triggers"][0]["trigger_count"] == advancement
        else:
            assert obligation is None
        assert _spend_run_count(runtime) == 0


def test_projected_vision_replan_settles_without_a_meta_successor(tmp_path: Path) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    # Complete material source, independently authored before admission.
    source = state.read_text().replace(" -->", " updated_at=2026-08-01T00%3A00%3A00Z -->")
    # Fourteen commitments plus a shared candidate is not this lane's long chain.
    last_id = "todo_chain_000000000014"
    source = source.replace(
        f"todo_id={last_id} status=open task_class=advancement_task action_kind=validate claimed_by={AGENT_ID}",
        f"todo_id={last_id} status=open task_class=advancement_task action_kind=validate",
    )
    state.write_text(source)

    def call(*args):
        rc, result = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0, result.get("error") or result
        return result

    def guard(turn):
        return call("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
                    "--agent-id", AGENT_ID, "--turn-instance-id", turn,
                    "--scan-path", str(project))

    call("configure-goal", "--goal-id", GOAL_ID,
         "--execution-replan-after-turns", "6",
         "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")

    shared = call("quota", "should-run", "--runtime-profile", "generic_cli",
                  "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    assert shared["goal_frontier_projection"]["replan_required"] is False
    call("todo", "claim", "--goal-id", GOAL_ID, "--todo-id", last_id,
         "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID)
    before = guard(TURN_ID)
    original = before["heartbeat_recommendation"]["replan_obligation"]
    assert [trigger["kind"] for trigger in original["triggers"]] == ["long_todo_chain"]
    assert original["triggers"][0]["count_kind"] == "claimed_advancement_todos"
    assert before["selected_todo"]["todo_id"] == SELECTED_REPLAN_TODO_ID
    assert before["heartbeat_receipt"]["settlement_binding_owed"] is True
    before = _bind_selected_replan_guard(
        registry, runtime, project, TURN_ID,
    )
    actions = before["interaction_contract"]["cli_channel"]["next_cli_actions"]
    contract = before["interaction_contract"]["cli_channel"]["replan_settlement_contract"]
    assert contract["settlement_binding"] == {
        "kind": "todo", "id": SELECTED_REPLAN_TODO_ID, "cli_argument": "--todo-id",
    }
    assert contract["semantic_obligation"] == {
        "kind": "autonomous_replan", "id": original["obligation_id"],
        "settlement_bound": False, "discharge": "todo_bound_writeback",
    }
    control = before["replan_action_packet"]["stride_control"]
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
    for transport in (build_turn_envelope(before), compact_quota_should_run_cli_payload(before)):
        assert transport["replan_action_packet"]["stride_control"] == control
    refresh, spend = actions
    assert "--agent-vision-json" in refresh
    assert "--replan-obligation-id" not in refresh
    # No invented Todo, progress identifiers, or legacy repair ACK is necessary.
    assert "--autonomous-replan-recorded" not in refresh
    # Shared-pool churn after admission must not invalidate this Turn's duty.
    call("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
         "--text", "Validate a separate shared candidate",
         "--task-class", "advancement_task", "--action-kind", "validate",
         "--target-key", "shared-candidate")
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {
            "vision_summary": "Validate the existing bounded slices in dependency order.",
            "acceptance_summary": "Each slice has independent validation before dependent work proceeds.",
            "advancement_policy": "as_needed",
        },
        "stride_decision": {
            "schema_version": "stride_decision_v1",
            "strategy_version": "evidence_conditioned_v1",
            "reference_context_id": before["replan_action_packet"]["stride_control"]["reference_context_id"],
            "reference_obligation_id": before["replan_action_packet"]["stride_control"]["reference_obligation_id"],
            "basis": "unexplained_gap",
            "next_probe": {
                "scope": "Execute one alternative mechanism probe.",
                "prediction": "The alternative distinguishes the remaining explanation.",
                "adoption_criterion": "Retain only a validated improvement on accepted requirements.",
                "cost_bound": "One isolated probe before another scope decision.",
            },
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "replan",
            "prior_assumption": "The long chain needed a bounded review.",
            "observed_reality": "The reviewed chain has a runnable validation slice.",
            "retained": ["Existing acceptance boundaries"],
            "changed": ["Proceed with the first validation slice"],
            "evidence_refs": ["evidence:synthetic-chain-review"],
        },
    }))
    refresh = refresh.replace("<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(decision))
    # Opt-in control is a real validation rule, not only extra prompt text.
    valid_packet = json.loads(decision.read_text())
    legacy_packet = {key: value for key, value in valid_packet.items() if key != "stride_decision"}
    decision.write_text(json.dumps(legacy_packet))
    rc, refused = _run_cli(registry, runtime,
        *_projected_cli_args(refresh, turn_instance_id=TURN_ID), cwd=project)
    assert rc == 1
    assert "requires agent_vision.stride_decision" in refused["error"]
    assert _spend_run_count(runtime) == 0
    decision.write_text(json.dumps(valid_packet))
    refreshed = call(*_projected_cli_args(refresh, turn_instance_id=TURN_ID))
    assert refreshed["vision_checkpoint"]["satisfied"] is True
    ack = refreshed["autonomous_replan_ack"]
    assert ack["recorded"] and ack["semantic_delta"]["accepted"]
    assert ack["semantic_delta"]["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    assert ack["semantic_delta"]["trigger_checkpoints"][0]["frontier_revision"]
    identity = refreshed["settlement_identity"]
    assert identity["todo_id"] == SELECTED_REPLAN_TODO_ID
    assert identity["turn_instance_id"] == TURN_ID
    persisted = json.loads(Path(refreshed["json_path"]).read_text())
    assert persisted["autonomous_replan_ack"] == ack
    assert persisted["agent_vision"]["stride_decision"]["axes"] == {
        "work_slice": "shrink", "route_distance": "grow", "validation_scope": "shrink"}
    from loopx.control_plane.goals.goal_vision import compact_goal_vision_packet
    assert compact_goal_vision_packet(persisted["agent_vision"])["stride_decision"] == persisted["agent_vision"]["stride_decision"]
    assert _spend_run_count(runtime) == 0
    settled = call(*_projected_cli_args(spend, turn_instance_id=TURN_ID))
    assert settled["settlement_result"]["ok"] is True
    assert _spend_run_count(runtime) == 1
    assert state.read_text().count("<!-- loopx:todo ") == 16
    # Timestamp/evidence bookkeeping is not a new material frontier.
    call("todo", "update", "--goal-id", GOAL_ID, "--todo-id", SELECTED_REPLAN_TODO_ID,
         "--agent-id", AGENT_ID, "--evidence", "evidence:synthetic-chain-review")
    after = guard("turn-after-chain-review")
    assert after["decision"] == "run"
    assert after["goal_frontier_projection"]["replan_required"] is False
    assert _spend_run_count(runtime) == 1
    # A real second admitted Turn executes and records a bounded synthetic
    # probe. The next review may observe that work, but cannot infer utility.
    _bind_selected_replan_guard(registry, runtime, project, "turn-after-chain-review")
    probe = project / "bounded-probe.txt"
    probe.write_text("Alternative mechanism: isolated synthetic execution.\n")
    assert probe.read_text().startswith("Alternative mechanism:")
    call("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
         "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", "turn-after-chain-review",
         "--classification", "bounded_probe", "--delivery-batch-scale", "single_surface",
         "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
         "--next-action", "Compare the executed alternative against the accepted requirement.",
         "--progress-result-class", "advanced", "--progress-surface-id", "surface-alternative",
         "--progress-probe-kind", "controlled-mechanism-comparison",
         "--progress-evidence-id", "evidence:executed-alternative-probe")
    call("quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
         "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", "turn-after-chain-review",
         "--slots", "1", "--source", "heartbeat", "--execute")
    assert _spend_run_count(runtime) == 2
    call("todo", "update", "--goal-id", GOAL_ID, "--todo-id", SELECTED_REPLAN_TODO_ID,
         "--agent-id", AGENT_ID, "--text", "Validate a changed acceptance boundary")
    changed = call("quota", "should-run", "--runtime-profile", "generic_cli",
                   "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rearmed = changed["autonomous_replan_obligation"]
    assert rearmed["rearmed_after_obligation_id"] == ack["semantic_delta"]["obligation_id"]
    follow = rearmed["replan_context"]["decision_followthrough"]
    assert follow["previous_decision"]["turn_instance_id"] == TURN_ID
    assert follow["previous_decision"]["path_delta"]["changed"] == ["Proceed with the first validation slice"]
    assert follow["previous_decision"]["stride_decision"] == persisted["agent_vision"]["stride_decision"]
    assert follow["recorded_progress_count"] == 1
    assert follow["execution_and_goal_gain"] == "unknown"
    assert follow["source_history_complete"] is True
    resolved = call("history", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                    "--evidence-ref", follow["previous_decision"]["evidence_ref"])
    assert resolved["evidence"]["path_delta"] == follow["previous_decision"]["path_delta"]
    assert "_source_facts" not in resolved["evidence"]
    assert _spend_run_count(runtime) == 2
