from __future__ import annotations

from typing import Any

import pytest

from loopx.control_plane.goals.goal_frontier import (
    GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION,
    GOAL_TERMINAL_STATE_SCHEMA_VERSION,
)
from loopx.control_plane.quota.decision_summary import (
    QuotaRunDecision,
    resolve_quota_run_decision,
)


def _resolve(**overrides: Any) -> QuotaRunDecision:
    values: dict[str, Any] = {
        "normal_delivery_allowed": True,
        "recovery_delivery_allowed": False,
        "self_repair_allowed": False,
        "stall_self_repair": None,
        "state": "eligible",
        "quota": {"state": "eligible"},
        "reason": "eligible",
        "capability_gate": None,
        "capability_monitor_fallback": None,
        "workspace_guard": None,
        "automation_prompt_upgrade": None,
        "automation_prompt_upgrade_required": False,
        "replan_obligation": None,
        "goal_health_ok": True,
        "inbox_reply_due": False,
        "inbox_material_review_due": False,
        "agent_frontier_id": "agent-a",
        "registered_agent_ids": ["agent-a"],
        "goal_frontier_projection": None,
        "task_orchestration_contract": None,
    }
    values.update(overrides)
    return resolve_quota_run_decision(**values)


def test_base_eligible_decision_runs_normally() -> None:
    decision = _resolve()

    assert decision.should_run is True
    assert decision.normal_delivery_allowed is True
    assert decision.effective_action == "normal_run"


def test_workspace_guard_overrides_capability_repair() -> None:
    decision = _resolve(
        capability_gate={"action": "repair_bridge", "reason": "repair capability"},
        workspace_guard={"reason": "use independent worktree"},
    )

    assert decision.should_run is True
    assert decision.capability_repair_allowed is False
    assert decision.workspace_repair_allowed is True
    assert decision.effective_action == "agent_workspace_repair"
    assert decision.reason == "use independent worktree"


def test_automation_upgrade_precedes_inbox_reply() -> None:
    decision = _resolve(
        automation_prompt_upgrade={"reason": "refresh prompt identity"},
        automation_prompt_upgrade_required=True,
        inbox_reply_due=True,
    )

    assert decision.should_run is False
    assert decision.normal_delivery_allowed is False
    assert decision.effective_action == "automation_prompt_upgrade_required"
    assert decision.reason == "refresh prompt identity"


def test_inbox_reply_prevents_replan_and_precedes_normal_delivery() -> None:
    decision = _resolve(
        replan_obligation={"required": True, "agent_id": "agent-a"},
        inbox_reply_due=True,
    )

    assert decision.replan_decision_allowed is False
    assert decision.should_run is True
    assert decision.normal_delivery_allowed is True
    assert decision.effective_action == "lark_inbox_reply_due"


def test_material_review_prevents_replan_without_claiming_reply_due() -> None:
    decision = _resolve(
        replan_obligation={"required": True, "agent_id": "agent-a"},
        inbox_material_review_due=True,
    )

    assert decision.should_run is True
    assert decision.normal_delivery_allowed is True
    assert decision.effective_action == "operator_inbox_material_review_due"
    assert decision.replan_decision_allowed is False


def test_replan_precedes_normal_delivery() -> None:
    decision = _resolve(
        replan_obligation={"required": True, "agent_id": "agent-a"},
    )

    assert decision.replan_decision_allowed is True
    assert decision.should_run is True
    assert decision.normal_delivery_allowed is False
    assert decision.effective_action == "autonomous_replan_required"


def _terminal_projection() -> dict[str, Any]:
    return {
        "terminal_state": {
            "schema_version": GOAL_TERMINAL_STATE_SCHEMA_VERSION,
            "kind": "no_followup",
            "derived": True,
            "source": "validated_goal_closure",
        },
        "source_completeness": {
            "schema_version": GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION,
            "user_todos": "valid",
            "agent_todos": "valid",
        },
        "normalized_progress": {
            "user_open_count": 0,
            "agent_open_count": 0,
            "agent_advancement_open_count": 0,
            "agent_monitor_open_count": 0,
            "agent_monitor_due_count": 0,
        },
        "remaining_advancement_frontier": {
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_advancement_count": 0,
            "other_agent_claimed_advancement_count": 0,
        },
        "monitor_only_lanes": {
            "present": False,
            "quiet_until_material_transition": False,
        },
        "deferred_successors": {
            "ready_count": 0,
            "blocked_count": 0,
            "current_agent_ready_count": 0,
        },
        "acceptance_gaps": [],
        "autonomy_blockers": [],
        "replan_required": False,
    }


def test_terminal_closure_precedes_automation_upgrade() -> None:
    decision = _resolve(
        automation_prompt_upgrade={"reason": "refresh prompt identity"},
        automation_prompt_upgrade_required=True,
        goal_frontier_projection=_terminal_projection(),
    )

    assert decision.should_run is False
    assert decision.state == "terminal_no_followup"
    assert decision.effective_action == "terminal_no_followup"
    assert decision.quota["state"] == "terminal_no_followup"


def test_task_orchestration_refines_normal_run_action() -> None:
    decision = _resolve(
        task_orchestration_contract={
            "coordinator_obligation": "coordinate eligible peers"
        }
    )

    assert decision.should_run is True
    assert decision.normal_delivery_allowed is True
    assert decision.effective_action == "coordinate_task_bundle"


@pytest.mark.parametrize(
    "reply,material,action,reason",
    [
        (
            True,
            False,
            "lark_inbox_reply_due",
            "a direct Lark question, bot mention, or verified reply to the bot is pending reply",
        ),
        (
            False,
            True,
            "operator_inbox_material_review_due",
            "captured unaddressed operator-inbox material is pending bounded review",
        ),
        (
            True,
            True,
            "lark_inbox_reply_due",
            "a direct Lark question, bot mention, or verified reply to the bot is pending reply",
        ),
    ],
)
@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("upgrade", [False, True])
def test_inbox_admission_preserves_guard_and_source_priority(
    reply: bool,
    material: bool,
    action: str,
    reason: str,
    terminal: bool,
    upgrade: bool,
) -> None:
    decision = _resolve(
        normal_delivery_allowed=False,
        recovery_delivery_allowed=True,
        self_repair_allowed=True,
        capability_gate={"action": "repair_bridge"},
        workspace_guard={"reason": "workspace repair"},
        inbox_reply_due=reply,
        inbox_material_review_due=material,
        goal_frontier_projection=_terminal_projection() if terminal else None,
        automation_prompt_upgrade_required=upgrade,
        automation_prompt_upgrade={"reason": "refresh prompt identity"},
        replan_obligation={"required": True, "agent_id": "agent-a"},
        task_orchestration_contract={"execution_state": "ready", "mode": "adaptive"},
    )

    # A nonterminal prompt upgrade wins; terminal inbox work retains the
    # existing exception. Neither inbox source becomes coordinator work.
    blocked = upgrade and not terminal
    assert decision.should_run is (not blocked)
    assert decision.normal_delivery_allowed is (not blocked)
    assert decision.effective_action == (
        "automation_prompt_upgrade_required" if blocked else action
    )
    assert decision.reason == ("refresh prompt identity" if blocked else reason)
    assert decision.recovery_delivery_allowed is False
    assert decision.self_repair_allowed is False
    assert decision.capability_repair_allowed is False
    assert decision.workspace_repair_allowed is False
    assert decision.replan_decision_allowed is False
    assert decision.state == "eligible"
    assert decision.quota == {"state": "eligible"}
