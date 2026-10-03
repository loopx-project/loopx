from __future__ import annotations

from loopx.control_plane.effect_program import interpret_quota_should_run_packet
from loopx.control_plane.quota.cli_projection import (
    compact_quota_should_run_cli_payload,
)
from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.quota.turn_envelope import (
    ACTION_SIGNATURE_COVERAGE_V2,
    build_turn_envelope,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)
from loopx.control_plane.work_items.autonomous_replan_obligation import (
    build_autonomous_replan_obligation_payload,
)


GOAL_ID = "action-portfolio-fixture"
AGENT_ID = "codex-main"
PRIMARY_ID = "todo_primary001"
FALLBACK_ID = "todo_fallback001"


def test_periodic_replan_keeps_recommendations_unbound_before_selection() -> None:
    status = _legacy_future_primary_status()
    obligation = build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0",
        stall_threshold=2,
        trigger_count=1,
        triggers=[{"kind": "periodic_review_due", "source": "fixture"}],
        guidance_actions=["create_successor"],
        todo_actions=[],
        stop_condition="stop on owner-only authority",
        recommended_action="Review the current route before delivery.",
        agent_id=AGENT_ID,
        include_agent_id=True,
    )
    item = status["attention_queue"]["items"][0]
    item["autonomous_replan_obligation"] = obligation
    item["project_asset"]["autonomous_replan_obligation"] = obligation
    packet = build_quota_should_run(
        status, goal_id=GOAL_ID, agent_id=AGENT_ID,
        turn_instance_id="turn-periodic-selection",
        available_capabilities=["fallback_runner"],
    )
    assert packet["effective_action"] == "autonomous_replan_required"
    assert packet["autonomous_replan_obligation"]["required"] is True
    assert packet["normal_delivery_allowed"] is False
    assert packet["action_portfolio"]["selection_policy"][
        "requires_explicit_turn_binding"
    ] is True
    cli = packet["interaction_contract"]["cli_channel"]
    assert cli["selection_required"] is True
    assert "settlement_plan" not in cli
    assert cli["next_cli_actions"] == []
    assert "--turn-instance-id turn-periodic-selection" in cli[
        "selection_command"
    ]["command_args_template"]


def _legacy_future_primary_status() -> dict:
    fallback = quota_todo_item(
        todo_id=FALLBACK_ID,
        index=2,
        priority="P2",
        title="Advance the independent fallback slice.",
        claimed_by=AGENT_ID,
        required_capabilities=["fallback_runner"],
        required_write_scopes=["artifacts/fallback/**"],
        note="Start from the independent fallback validation boundary.",
    )
    primary = quota_todo_item(
        todo_id=PRIMARY_ID,
        index=1,
        priority="P0",
        title="Run the Monday-only primary operation after its window opens.",
        claimed_by=AGENT_ID,
        action_kind="monitor",
        note="Do not poll before the scheduled primary window.",
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[primary, fallback],
        recommended_action=primary["text"],
        next_action=primary["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
        claim_scope_agent_id=AGENT_ID,
        latest_runs=[
            {
                "generated_at": "2026-08-22T10:00:00Z",
                "classification": "bounded_implementation_progress",
                "progress_scope": "agent_lane",
                "agent_id": AGENT_ID,
                "todo_id": PRIMARY_ID,
                "delivery_outcome": "outcome_progress",
                "recommended_action": primary["text"],
            }
        ],
    )


def test_sticky_primary_exposes_bounded_agent_selection_on_every_hot_path() -> None:
    packet = build_quota_should_run(
        _legacy_future_primary_status(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id="turn-portfolio-001",
        available_capabilities=["fallback_runner"],
    )

    assert packet["selected_todo"]["todo_id"] == PRIMARY_ID
    portfolio = packet["action_portfolio"]
    assert portfolio["primary"]["todo_id"] == PRIMARY_ID
    assert "fallback_actions" not in portfolio
    assert "fallback_policy" not in portfolio
    assert portfolio["selection_policy"] == {
        "decision_owner": "agent",
        "mode": "explicit_turn_binding",
        "recommendation_role": "default_not_binding",
        "requires_explicit_turn_binding": True,
        "direct_delivery_before_selection": False,
        "max_alternative_actions": 2,
        "candidate_scope": "current_authoritative_eligible_todos",
        "suggestions_exhaustive": False,
    }
    assert [item["todo_id"] for item in portfolio["suggested_actions"]] == [
        PRIMARY_ID,
        FALLBACK_ID,
    ]
    assert portfolio["suggested_actions"][1]["required_capabilities"] == [
        "fallback_runner"
    ]
    assert portfolio["suggested_actions"][1]["required_write_scopes"] == [
        "artifacts/fallback/**"
    ]
    assert packet["interaction_contract"]["agent_channel"][
        "action_portfolio_ref"
    ] == "$.action_portfolio"
    assert packet["interaction_contract"]["agent_channel"][
        "selection_required"
    ] is True
    assert packet["interaction_contract"]["agent_channel"][
        "delivery_allowed"
    ] is False
    cli_channel = packet["interaction_contract"]["cli_channel"]
    assert cli_channel["selection_required"] is True
    assert cli_channel["spend_after_validation"] is False
    assert cli_channel["next_cli_actions"] == []
    selection_command = cli_channel["selection_command"]
    assert selection_command["route_prefix"] == "loopx --format json"
    assert "--todo-id '{todo_id}'" in selection_command["command_args_template"]
    assert "--turn-instance-id turn-portfolio-001" in (
        selection_command["command_args_template"]
    )
    assert selection_command["candidate_discovery_args"].startswith("todo list")
    assert "settlement_plan" not in cli_channel

    compact = compact_quota_should_run_cli_payload(packet)
    assert compact["action_portfolio"]["selection_policy"] == (
        portfolio["selection_policy"]
    )
    compact_suggestions = compact["action_portfolio"]["suggested_actions"]
    assert compact_suggestions == [
        {
            "todo_id": PRIMARY_ID,
            "selection_role": "recommended",
            "priority": "P0",
            "action_kind": "monitor",
            "text": "[P0] Run the Monday-only primary operation after its window opens.",
            "continuation_hint": "Do not poll before the scheduled primary window.",
        },
        {
            "todo_id": FALLBACK_ID,
            "selection_role": "alternative",
            "priority": "P2",
            "text": "[P2] Advance the independent fallback slice.",
            "continuation_hint": (
                "Start from the independent fallback validation boundary."
            ),
        },
    ]
    assert compact["action_portfolio"]["suggested_action_details"] == {
        "schema_version": "quota_cli_action_portfolio_compaction_v1",
        "inlined_fields": [
            "todo_id",
            "selection_role",
            "priority",
            "action_kind",
            "text",
            "continuation_hint",
        ],
        "ref": "$.agent_todo_summary.first_executable_items",
    }
    compact_candidates = compact["agent_todo_summary"]["first_executable_items"]
    assert compact_candidates[1]["required_capabilities"] == ["fallback_runner"]
    assert compact_candidates[1]["required_write_scopes"] == [
        "artifacts/fallback/**"
    ]
    effect_turn = interpret_quota_should_run_packet(packet)
    assert effect_turn.observation.action_portfolio == portfolio

    envelope = build_turn_envelope(packet)
    assert envelope["action"]["action_portfolio"] == portfolio
    assert envelope["writeback"]["selection_required"] is True
    assert envelope["writeback"]["suggested_todo_ids"] == [
        PRIMARY_ID,
        FALLBACK_ID,
    ]
    assert envelope["writeback"]["selection_command_ref"] == (
        "full_decision.interaction_contract.cli_channel.selection_command"
    )
    assert "next_cli_actions" not in envelope["writeback"]
    assert envelope["action_signature"]["coverage"] == (
        ACTION_SIGNATURE_COVERAGE_V2
    )
    assert envelope["action_signature"]["matches"] is True
    assert envelope["compaction"]["within_budget"] is True


def test_explicit_action_selection_preserves_runtime_root_route() -> None:
    packet = build_quota_should_run(
        _legacy_future_primary_status(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id="turn-portfolio-002",
        available_capabilities=["fallback_runner"],
        runtime_root="/tmp/loopx runtime",
    )

    selection_command = packet["interaction_contract"]["cli_channel"][
        "selection_command"
    ]
    assert selection_command["route_prefix"] == (
        "loopx --runtime-root '/tmp/loopx runtime' --format json"
    )


def test_typed_future_monitor_selects_ready_work_and_preserves_priority() -> None:
    future_monitor = quota_todo_item(
        todo_id=PRIMARY_ID,
        index=1,
        priority="P0",
        title="Poll the primary target at its next due window.",
        claimed_by=AGENT_ID,
        task_class="continuous_monitor",
        action_kind="monitor",
        target_key="fixture-primary",
        cadence="daily",
        next_due_at="2099-01-01T00:00:00Z",
        watch_only=True,
    )
    fallback = quota_todo_item(
        todo_id=FALLBACK_ID,
        index=2,
        priority="P1",
        title="Advance the ready fallback slice.",
        claimed_by=AGENT_ID,
    )
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[future_monitor, fallback],
        recommended_action=future_monitor["text"],
        next_action=future_monitor["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
        claim_scope_agent_id=AGENT_ID,
    )

    packet = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert packet["selected_todo"]["todo_id"] == FALLBACK_ID
    assert packet["recommended_action"] == fallback["text"]
    unavailable = packet["action_portfolio"]["unavailable_higher_priority"]
    assert unavailable[0]["todo_id"] == PRIMARY_ID
    assert unavailable[0]["availability_reason"] == "scheduled_for_future"
    assert unavailable[0]["next_due_at"] == "2099-01-01T00:00:00Z"


def test_typed_external_wait_selects_ready_work_and_preserves_wait_context() -> None:
    waiting = quota_todo_item(
        todo_id=PRIMARY_ID,
        index=1,
        priority="P0",
        title="Resume the validated slice after external state changes.",
        claimed_by=AGENT_ID,
        resume_when="monitor_changed:todo_monitor001",
        resume_monitor_generation=4,
        successor_todo_ids=[FALLBACK_ID],
        note="Do not poll here; wait for the typed monitor transition.",
    )
    fallback = quota_todo_item(
        todo_id=FALLBACK_ID,
        index=2,
        priority="P1",
        title="Advance the independent fallback slice.",
        claimed_by=AGENT_ID,
        note="Implement the bounded fallback and run its focused validation.",
    )
    monitor = quota_todo_item(
        todo_id="todo_monitor001",
        index=3,
        priority="P2",
        title="Observe the external state for a material change.",
        claimed_by=AGENT_ID,
        task_class="continuous_monitor",
        action_kind="monitor",
        target_key="fixture-external-state",
        cadence="daily",
        next_due_at="2099-01-01T00:00:00Z",
        watch_only=True,
        material_change_generation=4,
    )
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[waiting, fallback, monitor],
        recommended_action=waiting["text"],
        next_action=waiting["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
        claim_scope_agent_id=AGENT_ID,
    )

    packet = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert packet["selected_todo"]["todo_id"] == FALLBACK_ID
    assert packet["recommended_action"] == fallback["text"]
    unavailable = packet["action_portfolio"]["unavailable_higher_priority"]
    assert unavailable[0]["todo_id"] == PRIMARY_ID
    assert unavailable[0]["availability_reason"] == "resume_condition_pending"
    wait_visibility = packet["agent_todo_summary"]["resume_blocked_items"][0]
    assert wait_visibility["todo_id"] == PRIMARY_ID
    assert wait_visibility["resume_ready"] is False
    assert wait_visibility["resume_condition"]["baseline_generation"] == 4
    assert wait_visibility["resume_condition"]["material_change_generation"] == 4

    compact = compact_quota_should_run_cli_payload(packet)
    selected_action = compact["action_portfolio"]["suggested_actions"][0]
    assert selected_action["todo_id"] == FALLBACK_ID
    assert selected_action["text"] == fallback["text"]
    assert selected_action["continuation_hint"] == (
        "Implement the bounded fallback and run its focused validation."
    )


def test_receipt_bound_turn_cannot_switch_to_a_fallback_todo() -> None:
    packet = build_quota_should_run(
        _legacy_future_primary_status(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        receipt_bound_todo_id=PRIMARY_ID,
    )

    assert packet["selected_todo"]["todo_id"] == PRIMARY_ID
    assert packet["agent_lane_next_action"]["selection_binding"] == (
        "heartbeat_receipt"
    )
    assert "action_portfolio" not in packet
