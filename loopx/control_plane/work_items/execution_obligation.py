from __future__ import annotations

from typing import Any, cast

from ..quota.effective_action import EffectiveAction


SUCCESSOR_REPLAN_REQUIRED_MODE = "successor_replan_required"


def build_execution_obligation(
    *,
    should_run: bool,
    effective_action: str,
    heartbeat_recommendation: dict[str, Any],
    work_lane_contract: dict[str, Any] | None = None,
    external_evidence_observation: dict[str, Any] | None = None,
    user_gate_owns_frontier: bool = False,
    successor_replan_mode: str = SUCCESSOR_REPLAN_REQUIRED_MODE,
) -> dict[str, Any]:
    """Separate the worker execution contract from user-facing notification."""

    recommended_mode = str(heartbeat_recommendation.get("recommended_mode") or "")
    work_lane_contract = work_lane_contract if isinstance(work_lane_contract, dict) else {}
    external_evidence_observation = (
        external_evidence_observation
        if isinstance(external_evidence_observation, dict)
        else {}
    )
    if should_run and recommended_mode == "repair_agent_workspace":
        return {
            "must_attempt_work": True,
            "kind": "agent_workspace_repair",
            "minimum": "one_workspace_move_then_guard_rerun",
            "delivery_allowed": False,
            "notify_is_execution_gate": False,
            "contract": "agent_workspace_guard",
            "contract_obligation": (
                "do not edit repository files from a shared checkout; "
                "create or switch to an independent worktree/branch, then rerun "
                "quota should-run with the same agent id"
            ),
            "spend_policy": heartbeat_recommendation.get("spend_policy"),
            "reason": (
                "peer delivery includes repository writes while the current workspace "
                "is shared or is not an independent worktree"
            ),
        }
    if external_evidence_observation.get("required") is True:
        return {
            "must_attempt_work": True,
            "kind": "external_evidence_observation_required",
            "minimum": "one_read_only_observation_or_compact_blocker",
            "delivery_allowed": False,
            "notify_is_execution_gate": False,
            "contract": "external_evidence_observation",
            "contract_obligation": (
                "verify the watched controller/thread/job/marker/writeback handle; "
                "if it is absent or never launched, write a compact blocker instead "
                "of treating the poll as unchanged evidence"
            ),
            "spend_policy": external_evidence_observation.get("spend_policy"),
            "reason": (
                "waiting external evidence still requires a read-only observation "
                "contract before a quiet no-op is allowed"
            ),
        }
    if heartbeat_recommendation.get("stop_if_unchanged"):
        return {
            "must_attempt_work": False,
            "kind": "quiet_noop_if_unchanged",
            "notify_is_execution_gate": False,
            "reason": (
                "this mode allows a quiet no-op only after confirming the current state "
                "source is unchanged and no concrete safe handoff exists"
            ),
        }
    if should_run and (
        effective_action == EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value
        or recommended_mode == "autonomous_replan_required"
    ):
        raw_replan_obligation = heartbeat_recommendation.get("replan_obligation")
        replan_obligation = (
            cast(dict[str, Any], raw_replan_obligation)
            if isinstance(raw_replan_obligation, dict)
            else {}
        )
        obligation = {
            "must_attempt_work": True,
            "kind": "autonomous_replan_required",
            "minimum": "one_bounded_replan_with_typed_outcome",
            "notify_is_execution_gate": False,
            "stall_threshold": replan_obligation.get("stall_threshold"),
            "contract_obligation": (
                "apply autonomous_replan_obligation; create a typed concrete "
                "runnable agent todo only when an executable target is known, "
                "otherwise record an accepted typed semantic or coverage-backed "
                "terminal outcome"
            ),
            "reason": (
                "autonomous_replan_obligation is a machine execution contract; "
                "quiet no-op is not allowed until the replan slice is validated or blocked"
            ),
        }
        return obligation
    if should_run and recommended_mode == successor_replan_mode:
        return {
            "must_attempt_work": True,
            "kind": successor_replan_mode,
            "minimum": "one_successor_replan_or_no_followup_writeback",
            "delivery_allowed": False,
            "notify_is_execution_gate": False,
            "contract": "deferred_resume_projection",
            "contract_obligation": (
                "reopen or supersede the ready deferred successor, or record a "
                "public-safe no-follow-up rationale before another quiet no-op"
            ),
            "spend_policy": heartbeat_recommendation.get("spend_policy"),
            "reason": (
                "a deferred successor resume condition is satisfied; the agent must "
                "repair the todo projection before normal delivery"
            ),
        }
    if should_run and recommended_mode == "repair_state_projection_gap":
        return {
            "must_attempt_work": True,
            "kind": "state_projection_gap_repair",
            "minimum": "one_replan_or_todo_expansion_or_blocker_writeback_segment",
            "delivery_allowed": False,
            "notify_is_execution_gate": False,
            "contract": "state_projection_gap",
            "contract_obligation": (
                "repair the active-state projection before normal delivery: expand "
                "Next Action into open Agent Todo/User Todo or write a compact blocker"
            ),
            "spend_policy": heartbeat_recommendation.get("spend_policy"),
            "reason": (
                "should_run=true exposed actionable prose but no open todo projection; "
                "normal bounded delivery is blocked until projection is repaired"
            ),
        }
    if should_run and recommended_mode == "repair_boundary_projection":
        return {
            "must_attempt_work": True,
            "kind": "boundary_projection_repair",
            "minimum": "one_boundary_projection_or_blocker_writeback_segment",
            "delivery_allowed": False,
            "notify_is_execution_gate": False,
            "contract": "goal_boundary.write_scope",
            "contract_obligation": (
                "do not execute the selected write; repair goal_boundary.write_scope "
                "projection, rewrite the selected todo within boundary, or create a "
                "concrete user/controller gate"
            ),
            "spend_policy": heartbeat_recommendation.get("spend_policy"),
            "reason": (
                "selected executable todo requires a write scope that the current "
                "goal_boundary does not project"
            ),
        }
    if should_run and recommended_mode == "outcome_floor_recovery":
        return {
            "must_attempt_work": True,
            "kind": "outcome_floor_recovery",
            "minimum": "one_outcome_floor_evidence_or_blocker_segment",
            "delivery_allowed": True,
            "notify_is_execution_gate": False,
            "contract": "delivery_outcome_floor",
            "contract_obligation": (
                "produce the required outcome-floor evidence artifact or write "
                "the concrete blocker; do not spend for another surface-only report"
            ),
            "spend_policy": heartbeat_recommendation.get("spend_policy"),
            "reason": (
                "outcome-floor recovery is the selected safe-bypass execution "
                "contract and must stay aligned with interaction_contract.mode"
            ),
        }
    if should_run and recommended_mode == "repair_capability_bridge":
        return {
            "must_attempt_work": True,
            "kind": "capability_bridge_repair",
            "minimum": "one_bridge_or_environment_repair_or_blocker_writeback_segment",
            "delivery_allowed": False,
            "notify_is_execution_gate": False,
            "contract": "capability_gate",
            "contract_obligation": (
                "do not execute the selected capability-blocked todo; repair or "
                "materialize the missing bridge/capability, rewrite the todo to an "
                "available capability, or write a compact blocker"
            ),
            "spend_policy": heartbeat_recommendation.get("spend_policy"),
            "reason": (
                "all executable todos require unavailable bridge-style capabilities, "
                "but a bounded bridge repair may be attempted"
            ),
        }
    if should_run and work_lane_contract:
        return {
            "must_attempt_work": bool(work_lane_contract.get("must_attempt_work", should_run)),
            "kind": "work_lane_contract",
            "contract": "work_lane_contract",
            "contract_obligation": work_lane_contract.get("obligation"),
            "notify_is_execution_gate": False,
            "reason": (
                "work_lane_contract.obligation is the machine execution contract; "
                "heartbeat_recommendation is explanatory"
            ),
        }
    if should_run and not user_gate_owns_frontier:
        return {
            "must_attempt_work": True,
            "kind": effective_action or recommended_mode or "bounded_delivery",
            "minimum": "one_bounded_segment",
            "notify_is_execution_gate": False,
            "reason": (
                "should_run=true means a Codex-actionable turn exists; heartbeat notify "
                "only controls whether to interrupt the user"
            ),
        }
    if should_run:
        return {
            "must_attempt_work": False,
            "kind": "user_gate_quiet_wait",
            "delivery_allowed": False,
            "notify_is_execution_gate": False,
            "reason": (
                "the user owns the next step and the agent lane has no executable "
                "work; wait quietly for the user instead of repeating the same turn"
            ),
        }
    return {
        "must_attempt_work": False,
        "kind": effective_action or recommended_mode or "skip",
        "notify_is_execution_gate": False,
        "reason": (
            "should_run=false blocks delivery unless an explicit safe-bypass or "
            "self-repair action is exposed"
        ),
    }
