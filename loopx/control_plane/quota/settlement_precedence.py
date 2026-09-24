from __future__ import annotations
from .effective_action import EffectiveAction

from typing import Any



HEARTBEAT_SETTLED_REPLAY_REASON = (
    "the receipt-bound work binding and required settlement receipts "
    "are complete for this heartbeat turn; defer successor selection to a new turn"
)
RECEIPT_BOUND_DEFERRED_REASON = (
    "the Todo bound to this heartbeat receipt is deferred; do not "
    "select or spend an independent successor in the same Turn"
)

_ACTION_PROJECTION_KEYS = (
    "agent_command",
    "action_portfolio",
    "agent_lane_frontier_hint",
    "agent_lane_next_action",
    "agent_scope_frontier",
    "autonomous_replan_decision",
    "autonomous_replan_obligation",
    "autonomous_replan_scope",
    "blocked_priority_fallback",
    "capability_gate",
    "capability_monitor_fallback",
    "external_evidence_observation",
    "goal_route_hint",
    "notify_user_on_capability_gate",
    "notify_user_on_gate",
    "notify_user_on_open_todo",
    "open_todo_notification_policy",
    "open_todo_notify_reason",
    "required_reads",
    "replan_action_packet",
    "scoped_user_gate_fallback",
    "stall_self_repair",
    "vision_continuation_audit",
    "vision_wait_state",
    "workspace_guard",
)


def clear_quota_action_projections(
    payload: dict[str, Any],
    *,
    additional_keys: tuple[str, ...] = (),
) -> None:
    for key in (*_ACTION_PROJECTION_KEYS, *additional_keys):
        payload.pop(key, None)


def settled_replay_fields() -> dict[str, Any]:
    """Construct the authority fields of a verified, already-settled Turn."""
    reason = HEARTBEAT_SETTLED_REPLAY_REASON
    return {
        "decision": "skip",
        "should_run": False,
        "normal_delivery_allowed": False,
        "recovery_delivery_allowed": False,
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        # A settled Turn grants no safe bypass: the heartbeat task body reads
        # safe_bypass_allowed as permission to run a bounded step and spend, so
        # a fresh Turn must recompute any fallback instead of inheriting one.
        "safe_bypass_allowed": False,
        "safe_bypass_kind": None,
        "safe_bypass_policy": None,
        "effective_action": EffectiveAction.HEARTBEAT_SETTLED_SKIP.value,
        "actionable_by_codex": False,
        "reason": reason,
        "requires_user_action": False,
        "recommended_action": (
            "Finish this heartbeat without another action; use a fresh turn "
            "identity for successor selection."
        ),
        "heartbeat_recommendation": {
            "recommended_mode": "heartbeat_settled_skip",
            "notify": "DONT_NOTIFY",
            "reason": reason,
            "spend_policy": "no quota spend for an already-settled heartbeat turn",
            "agent_must_attempt": False,
        },
        "execution_obligation": {
            "must_attempt_work": False,
            "kind": "heartbeat_settled_skip",
            "delivery_allowed": False,
            "notify_is_execution_gate": False,
            "reason": reason,
            "spend_policy": "no quota spend for an already-settled heartbeat turn",
        },
    }


def deferred_receipt_bound_skip_fields(
    quota: dict[str, Any],
    heartbeat_recommendation: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Project a deferred receipt without borrowing a successor's authority."""

    reason = RECEIPT_BOUND_DEFERRED_REASON
    return (
        reason,
        {**quota, "safe_bypass_allowed": False},
        {
            **heartbeat_recommendation,
            "recommended_mode": EffectiveAction.QUOTA_SKIP.value,
            "notify": "DONT_NOTIFY",
            "reason": reason,
            "spend_policy": "no quota spend for a deferred receipt-bound Todo",
            "stop_if_unchanged": True,
        },
    )
