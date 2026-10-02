from __future__ import annotations
from .effective_action import EffectiveAction
from typing import Any

from ..agents.agent_scope_frontier import AgentScopeFrontierAction
from ..agents.runtime_model import peer_work_key
from ..effect_runtime import effect_runtime_result
from ..todos.contract import (
    normalize_required_capabilities,
    normalize_todo_claimed_by,
)
from ..work_items.work_lane import WORK_LANE_CONTRACT_SCHEMA_VERSION
from ..work_items.work_lane_context import build_work_lane_context_contract
from .recent_runs import latest_unchanged_monitor_observation
from .task_orchestration_admission import (
    SUBAGENT_SPAWN_CAPABILITY,
    build_adaptive_task_orchestration_contract,
)

AGENT_SCOPE_NON_EXECUTION_ACTIONS = {
    AgentScopeFrontierAction.AGENT_SCOPE_EXHAUSTED.value,
    AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
    AgentScopeFrontierAction.REASSIGNMENT_REQUIRED.value,
}
PEER_AGENT_ACTIVATION_CAPABILITY = "peer_agent_activation"
PEER_COORDINATION_BLOCKED_ACTION = EffectiveAction.PEER_COORDINATION_BLOCKED.value


def task_orchestration_contract_is_actionable(
    contract: dict[str, Any] | None,
) -> bool:
    if not isinstance(contract, dict):
        return False
    return str(contract.get("execution_state") or "ready") == "ready"


def task_orchestration_requires_material_change_stop(
    contract: dict[str, Any] | None,
    *,
    effective_action: str,
) -> bool:
    """Stop an explicitly coordinated peer lane that has no local fallback.

    A blocked peer bundle remains useful diagnostic state, but it is not a
    runnable obligation.  The caller supplies the already-derived agent-scope
    action so this rule only stops a coordinator after its own runnable
    frontier has been exhausted.
    """

    if not isinstance(contract, dict):
        return False
    return bool(
        contract.get("mode") == "task_scoped_peer"
        and contract.get("execution_state") == "blocked"
        and contract.get("retry_policy") == "material_peer_state_change_only"
        and effective_action in AGENT_SCOPE_NON_EXECUTION_ACTIONS
    )


def build_quota_work_lane_contract(
    item: dict[str, Any],
    *,
    status_payload: dict[str, Any],
    goal_id: str,
    agent_id: str | None,
    agent_todo_summary: dict[str, Any] | None,
    monitor_due_item_limit: int,
    monitor_debt_arbitration: dict[str, Any] | None = None,
    advancement_allowed: bool = True,
) -> dict[str, Any] | None:
    monitor_attempt_already_recorded = bool(
        latest_unchanged_monitor_observation(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
        )
    )
    return build_work_lane_context_contract(
        item,
        agent_todo_summary=agent_todo_summary,
        monitor_due_item_limit=monitor_due_item_limit,
        monitor_attempt_already_recorded=monitor_attempt_already_recorded,
        monitor_debt_backoff_active=bool(
            isinstance(monitor_debt_arbitration, dict)
            and monitor_debt_arbitration.get("active") is True
        ),
        advancement_allowed=advancement_allowed,
    )


def payload_work_lane_contract(
    work_lane_contract: dict[str, Any] | None,
    *,
    effective_action: str,
    recovery_allowed: bool,
    agent_scope_frontier: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if recovery_allowed and effective_action == EffectiveAction.OUTCOME_FLOOR_RECOVERY.value:
        return None
    if not isinstance(work_lane_contract, dict):
        return work_lane_contract
    if (
        effective_action in AGENT_SCOPE_NON_EXECUTION_ACTIONS
        and work_lane_contract.get("must_attempt_work") is True
        and isinstance(agent_scope_frontier, dict)
    ):
        return _agent_scope_payload_work_lane_contract(
            work_lane_contract,
            effective_action=effective_action,
            agent_scope_frontier=agent_scope_frontier,
        )
    return work_lane_contract


def apply_task_orchestration_contract(
    *,
    fallback_work_lane_contract: dict[str, Any] | None,
    goal_boundary: dict[str, Any] | None,
    agent_identity: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any],
    raw_agent_todo_summary: dict[str, Any] | None = None,
    raw_user_todo_summary: dict[str, Any] | None = None,
    agent_todo_source_items: list[dict[str, Any]] | None = None,
    user_todo_source_items: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
    parent_goal_id: str | None = None,
    agent_management_projection: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    del agent_todo_summary
    contract = _task_orchestration_contract(
        goal_boundary=goal_boundary,
        agent_identity=agent_identity,
        raw_agent_todo_summary=raw_agent_todo_summary,
        raw_user_todo_summary=raw_user_todo_summary,
        agent_todo_source_items=agent_todo_source_items,
        user_todo_source_items=user_todo_source_items,
        available_capabilities=available_capabilities,
        parent_goal_id=parent_goal_id,
        agent_management_projection=agent_management_projection,
    )
    if not contract:
        return None, fallback_work_lane_contract
    if not task_orchestration_contract_is_actionable(contract):
        return contract, fallback_work_lane_contract
    return contract, _task_orchestration_work_lane_contract(contract)


def task_goal_route_hint(
    goal_route_hint: dict[str, Any] | None,
    contract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not contract or not isinstance(goal_route_hint, dict):
        return goal_route_hint
    if not task_orchestration_contract_is_actionable(contract):
        return goal_route_hint
    lanes = contract.get("eligible_child_lanes")
    if not isinstance(lanes, list):
        lanes = contract.get("eligible_peer_lanes")
    return {
        **{
            key: value
            for key, value in goal_route_hint.items()
            if key != "current_agent_next_action"
        },
        "kind": "task_orchestration",
        "route_decision": "coordinate_task_bundle",
        "reason": (
            "task-scoped coordinator may spawn admitted child lanes"
            if contract.get("mode") == "adaptive"
            else "task-scoped coordinator must activate/resume eligible peer lanes"
        ),
        "peer_lane_count": len(lanes) if isinstance(lanes, list) else 0,
    }


def attach_task_orchestration_payload(
    payload: dict[str, Any],
    contract: dict[str, Any] | None,
) -> dict[str, Any]:
    if contract:
        payload["task_orchestration_contract"] = contract
    return payload


def _agent_scope_payload_work_lane_contract(
    work_lane_contract: dict[str, Any],
    *,
    effective_action: str,
    agent_scope_frontier: dict[str, Any],
) -> dict[str, Any]:
    reason_codes = [
        str(value)
        for value in (
            work_lane_contract.get("reason_codes")
            if isinstance(work_lane_contract.get("reason_codes"), list)
            else []
        )
        if str(value).strip()
    ]
    for code in ("agent_scope_no_current_runnable_candidate", effective_action):
        if code not in reason_codes:
            reason_codes.append(code)
    deferred_work_lane = {
        key: work_lane_contract.get(key)
        for key in ("lane", "next_lane", "obligation", "monitor_policy")
        if work_lane_contract.get(key) is not None
    }
    if work_lane_contract.get("reason_codes") is not None:
        deferred_work_lane["reason_codes"] = work_lane_contract.get("reason_codes")
    return {
        "schema_version": str(
            work_lane_contract.get("schema_version")
            or WORK_LANE_CONTRACT_SCHEMA_VERSION
        ),
        "lane": effective_action,
        "next_lane": str(work_lane_contract.get("lane") or "advancement_task"),
        "obligation": "wait_for_current_agent_or_unclaimed_advancement",
        "must_attempt_work": False,
        "reason_codes": reason_codes,
        "monitor_policy": "no_delivery_until_current_agent_frontier_exists",
        "blocked_by_agent_scope": True,
        "agent_scope_action": effective_action,
        "deferred_work_lane": deferred_work_lane,
        "action": (
            agent_scope_frontier.get("recommended_action")
            or agent_scope_frontier.get("reason")
            or "wait for a current-agent or unclaimed advancement todo before delivery"
        ),
    }


def _task_orchestration_contract(
    *,
    goal_boundary: dict[str, Any] | None,
    agent_identity: dict[str, Any] | None,
    raw_agent_todo_summary: dict[str, Any] | None,
    raw_user_todo_summary: dict[str, Any] | None,
    agent_todo_source_items: list[dict[str, Any]] | None,
    user_todo_source_items: list[dict[str, Any]] | None,
    available_capabilities: Any,
    parent_goal_id: str | None,
    agent_management_projection: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(agent_identity, dict):
        return None
    if not isinstance(goal_boundary, dict):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    if not agent_id:
        return None
    orchestration = (
        goal_boundary.get("orchestration")
        if isinstance(goal_boundary.get("orchestration"), dict)
        else {}
    )
    available = normalize_required_capabilities(available_capabilities)
    peer_coordination = (
        goal_boundary.get("peer_task_coordination")
        if isinstance(goal_boundary.get("peer_task_coordination"), dict)
        else {}
    )
    configured_coordinator = normalize_todo_claimed_by(
        peer_coordination.get("coordinator_agent_id")
    )
    peer_contract = None
    if (
        peer_coordination.get("enabled") is True
        and configured_coordinator == agent_id
    ):
        peer_contract = _registered_peer_task_orchestration_contract(
            agent_id=agent_id,
            agent_identity=agent_identity,
            raw_agent_todo_summary=raw_agent_todo_summary,
            agent_todo_source_items=agent_todo_source_items,
            available_capabilities=available,
            agent_management_projection=agent_management_projection,
        )
        if task_orchestration_contract_is_actionable(peer_contract):
            return peer_contract
    if orchestration.get("mode") != "multi_subagent":
        return peer_contract
    if orchestration.get("spawn_allowed") is not True:
        return peer_contract
    max_children = orchestration.get("max_children")
    if not isinstance(max_children, int) or max_children <= 0:
        return peer_contract
    if SUBAGENT_SPAWN_CAPABILITY in available:
        native_contract = build_adaptive_task_orchestration_contract(
            agent_id=agent_id,
            agent_identity=agent_identity,
            goal_boundary=goal_boundary,
            orchestration=orchestration,
            raw_agent_todo_summary=raw_agent_todo_summary,
            raw_user_todo_summary=raw_user_todo_summary,
            agent_todo_source_items=agent_todo_source_items,
            user_todo_source_items=user_todo_source_items,
            available_capabilities=available,
            parent_goal_id=parent_goal_id,
            max_children=max_children,
        )
        if native_contract:
            if peer_contract:
                native_contract["peer_activation_diagnostic"] = peer_contract
            return native_contract
    return peer_contract


def _registered_peer_task_orchestration_contract(
    *,
    agent_id: str,
    agent_identity: dict[str, Any],
    raw_agent_todo_summary: dict[str, Any] | None,
    agent_todo_source_items: list[dict[str, Any]] | None,
    available_capabilities: list[str],
    agent_management_projection: dict[str, Any] | None,
) -> dict[str, Any] | None:
    # Canonical inventory, including an explicitly empty list, outranks display rows.
    source_items = agent_todo_source_items
    if source_items is None:
        source_items = (raw_agent_todo_summary or {}).get("items", [])
    fields = ("todo_id", "done", "status", "task_class", "priority", "action_kind",
              "title", "text", "resume_when", "resume_ready")
    items = [
        {**{key: item[key] for key in fields if key in item},
         "claimed_by": normalize_todo_claimed_by(item.get("claimed_by"))}
        for item in (source_items or []) if isinstance(item, dict)
    ]
    management_rows = (agent_management_projection or {}).get("agents")
    agents = [
        {"agent_id": normalize_todo_claimed_by(row.get("agent_id")),
         "state": row.get("state"), "stale_claim_hint": row.get("stale_claim_hint")}
        for row in (management_rows if isinstance(management_rows, list) else [])
        if isinstance(row, dict)
    ]
    contract = effect_runtime_result("quota.peer_orchestration.project", {
        "agent_id": agent_id,
        "registered_agents": agent_identity.get("registered_agents") or [],
        "items": items,
        "available_capabilities": available_capabilities,
        "agents": agents,
    })
    if contract is None:
        return None
    lanes = contract["eligible_peer_lanes"] + contract["blocked_peer_lanes"]
    contract["assignment_key"] = peer_work_key({
        "mode": "task_scoped_peer",
        "lanes": sorted(
            [{"agent_id": lane["agent_id"], "todo_id": lane["todo_id"]} for lane in lanes],
            key=lambda lane: (lane["agent_id"], lane["todo_id"]),
        ),
    }, fallback="task_orchestration")
    return contract


def _task_orchestration_work_lane_contract(
    contract: dict[str, Any],
) -> dict[str, Any]:
    peer_lanes = contract.get("eligible_child_lanes")
    if not isinstance(peer_lanes, list):
        peer_lanes = contract.get("eligible_peer_lanes")
    return {
        "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
        "lane": "task_orchestration",
        "next_lane": "peer_evidence_review",
        "obligation": "coordinate_task_bundle",
        "must_attempt_work": True,
        "reason_codes": [
            "eligible_child_lanes"
            if contract.get("mode") == "adaptive"
            else "eligible_peer_lanes"
        ],
        "monitor_policy": "material_transition_only",
        "action": contract["coordinator_obligation"],
        "eligible_peer_lane_count": (
            len(peer_lanes) if isinstance(peer_lanes, list) else 0
        ),
    }
