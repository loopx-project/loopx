from __future__ import annotations

from collections.abc import Mapping
from itertools import chain

from ..agents.agent_scope_frontier import AgentScopeFrontierAction
from ..agents.runtime_model import peer_work_key
from ..effect_runtime import effect_runtime_result
from ..todos.contract import (
    normalize_required_capabilities,
    normalize_todo_claimed_by,
)
from ..work_items.work_lane import WORK_LANE_CONTRACT_SCHEMA_VERSION
from ..work_items.work_lane_context import build_work_lane_context_contract
from .effective_action import EffectiveAction
from .recent_runs import latest_unchanged_monitor_observation
from .task_orchestration_admission import (
    build_adaptive_task_orchestration_contract,
)

AGENT_SCOPE_NON_EXECUTION_ACTIONS = {
    AgentScopeFrontierAction.AGENT_SCOPE_EXHAUSTED.value,
    AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
    AgentScopeFrontierAction.REASSIGNMENT_REQUIRED.value,
}
SUBAGENT_SPAWN_CAPABILITY = "subagent_spawn"
PEER_AGENT_ACTIVATION_CAPABILITY = "peer_agent_activation"
PEER_COORDINATION_BLOCKED_ACTION = EffectiveAction.PEER_COORDINATION_BLOCKED.value

_DEFERRED_WORK_LANE_FIELDS = ("lane", "next_lane", "obligation", "monitor_policy")
_PEER_SOURCE_ITEM_FIELDS = (
    "todo_id",
    "done",
    "status",
    "task_class",
    "priority",
    "action_kind",
    "title",
    "text",
    "resume_when",
    "resume_ready",
)


def _as_dict_or_none(value: object) -> dict[str, object] | None:
    """Copy a mapping to a string-keyed dict, returning None for non-mappings."""
    if not isinstance(value, Mapping):
        return None
    copied: dict[str, object] = {}
    for key, item in value.items():
        copied[str(key)] = item
    return copied


def _list_or_empty(value: object) -> list[object]:
    """Return a list when already a list, else an empty list."""
    return value if isinstance(value, list) else []


def _peer_lane_refs(contract: Mapping[str, object]) -> list[dict[str, object]]:
    """Collect sorted minimal lane descriptors from peer contract buckets."""
    lane_refs: list[dict[str, object]] = []
    for lane in chain(
        _list_or_empty(contract.get("eligible_peer_lanes")),
        _list_or_empty(contract.get("blocked_peer_lanes")),
    ):
        if isinstance(lane, Mapping):
            lane_refs.append(
                {
                    "agent_id": lane.get("agent_id"),
                    "todo_id": lane.get("todo_id"),
                }
            )
    lane_refs.sort(
        key=lambda lane: (
            str(lane["agent_id"] or ""),
            str(lane["todo_id"] or ""),
        )
    )
    return lane_refs


def task_orchestration_contract_is_actionable(
    contract: Mapping[str, object] | None,
) -> bool:
    if not isinstance(contract, Mapping):
        return False
    return str(contract.get("execution_state") or "ready") == "ready"


def apply_task_route_hint_override(
    hint: dict[str, object] | None,
    override: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """Apply the route patch returned by the TypeScript decision owner."""
    if hint is None or override is None:
        return hint
    omit_next_action = override.get("omit_current_agent_next_action") is True
    projected_hint: dict[str, object] = dict(hint)
    if omit_next_action:
        projected_hint.pop("current_agent_next_action", None)
    for key, value in override.items():
        if key != "omit_current_agent_next_action":
            projected_hint[key] = value
    return projected_hint


def task_orchestration_requires_material_change_stop(
    contract: Mapping[str, object] | None,
    *,
    effective_action: str,
) -> bool:
    """Stop an explicitly coordinated peer lane that has no local fallback.

    A blocked peer bundle remains useful diagnostic state, but it is not a
    runnable obligation.  The caller supplies the already-derived agent-scope
    action so this rule only stops a coordinator after its own runnable
    frontier has been exhausted.
    """

    if not isinstance(contract, Mapping):
        return False
    return (
        contract.get("mode") == "task_scoped_peer"
        and contract.get("execution_state") == "blocked"
        and contract.get("retry_policy") == "material_peer_state_change_only"
        and effective_action in AGENT_SCOPE_NON_EXECUTION_ACTIONS
    )


def build_quota_work_lane_contract(
    item: dict[str, object],
    *,
    status_payload: dict[str, object],
    goal_id: str,
    agent_id: str | None,
    agent_todo_summary: dict[str, object] | None,
    monitor_due_item_limit: int,
    monitor_debt_arbitration: Mapping[str, object] | None = None,
    advancement_allowed: bool = True,
) -> dict[str, object] | None:
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
        monitor_debt_backoff_active=(
            isinstance(monitor_debt_arbitration, Mapping)
            and monitor_debt_arbitration.get("active") is True
        ),
        advancement_allowed=advancement_allowed,
    )


def payload_work_lane_contract(
    work_lane_contract: Mapping[str, object] | None,
    *,
    effective_action: str,
    recovery_allowed: bool,
    agent_scope_frontier: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if (
        recovery_allowed
        and effective_action == EffectiveAction.OUTCOME_FLOOR_RECOVERY.value
    ):
        return None
    if not isinstance(work_lane_contract, Mapping):
        return None
    if (
        effective_action in AGENT_SCOPE_NON_EXECUTION_ACTIONS
        and work_lane_contract.get("must_attempt_work") is True
        and isinstance(agent_scope_frontier, Mapping)
    ):
        return _agent_scope_payload_work_lane_contract(
            work_lane_contract,
            effective_action=effective_action,
            agent_scope_frontier=agent_scope_frontier,
        )
    if isinstance(work_lane_contract, dict):
        return work_lane_contract
    return _as_dict_or_none(work_lane_contract)


def apply_task_orchestration_contract(
    *,
    fallback_work_lane_contract: dict[str, object] | None,
    goal_boundary: dict[str, object] | None,
    agent_identity: dict[str, object] | None,
    agent_todo_summary: dict[str, object] | None,
    raw_agent_todo_summary: dict[str, object] | None = None,
    raw_user_todo_summary: dict[str, object] | None = None,
    agent_todo_source_items: list[dict[str, object]] | None = None,
    user_todo_source_items: list[dict[str, object]] | None = None,
    available_capabilities: object = None,
    parent_goal_id: str | None = None,
    agent_management_projection: Mapping[str, object] | None = None,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
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


def attach_task_orchestration_payload(
    payload: dict[str, object],
    contract: Mapping[str, object] | None,
) -> dict[str, object]:
    if contract:
        payload["task_orchestration_contract"] = (
            contract if isinstance(contract, dict) else _as_dict_or_none(contract)
        )
    return payload


def _agent_scope_payload_work_lane_contract(
    work_lane_contract: Mapping[str, object],
    *,
    effective_action: str,
    agent_scope_frontier: Mapping[str, object],
) -> dict[str, object]:
    raw_reason_codes = work_lane_contract.get("reason_codes")
    reason_codes: list[str] = []
    if isinstance(raw_reason_codes, list):
        for value in raw_reason_codes:
            code = str(value)
            if code.strip():
                reason_codes.append(code)
    for code in ("agent_scope_no_current_runnable_candidate", effective_action):
        if code not in reason_codes:
            reason_codes.append(code)
    deferred_work_lane: dict[str, object] = {}
    for key in _DEFERRED_WORK_LANE_FIELDS:
        value = work_lane_contract.get(key)
        if value is not None:
            deferred_work_lane[key] = value
    if raw_reason_codes is not None:
        deferred_work_lane["reason_codes"] = raw_reason_codes
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
    goal_boundary: dict[str, object] | None,
    agent_identity: dict[str, object] | None,
    raw_agent_todo_summary: dict[str, object] | None,
    raw_user_todo_summary: dict[str, object] | None,
    agent_todo_source_items: list[dict[str, object]] | None,
    user_todo_source_items: list[dict[str, object]] | None,
    available_capabilities: object,
    parent_goal_id: str | None,
    agent_management_projection: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(agent_identity, dict) or not isinstance(goal_boundary, dict):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    if not agent_id:
        return None
    raw_peer_coordination = goal_boundary.get("peer_task_coordination")
    peer_coordination = (
        raw_peer_coordination if isinstance(raw_peer_coordination, Mapping) else {}
    )
    raw_orchestration = goal_boundary.get("orchestration")
    orchestration = (
        raw_orchestration if isinstance(raw_orchestration, Mapping) else {}
    )
    peer_active = (
        peer_coordination.get("enabled") is True
        and normalize_todo_claimed_by(peer_coordination.get("coordinator_agent_id"))
        == agent_id
    )
    max_children = orchestration.get("max_children")
    adaptive_eligible = (
        orchestration.get("mode") == "multi_subagent"
        and orchestration.get("spawn_allowed") is True
        and isinstance(max_children, int)
        and not isinstance(max_children, bool)
        and max_children > 0
    )
    if not peer_active and not adaptive_eligible:
        return None
    available = normalize_required_capabilities(available_capabilities)
    peer_contract: dict[str, object] | None = None
    if peer_active:
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
    if not adaptive_eligible or SUBAGENT_SPAWN_CAPABILITY not in available:
        return peer_contract
    assert isinstance(max_children, int) and not isinstance(max_children, bool)
    orchestration_dict = (
        raw_orchestration
        if isinstance(raw_orchestration, dict)
        else _as_dict_or_none(raw_orchestration)
    )
    if orchestration_dict is None:
        return peer_contract
    native_contract = build_adaptive_task_orchestration_contract(
        agent_id=agent_id,
        agent_identity=agent_identity,
        goal_boundary=goal_boundary,
        orchestration=orchestration_dict,
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
    agent_identity: Mapping[str, object],
    raw_agent_todo_summary: Mapping[str, object] | None,
    agent_todo_source_items: list[dict[str, object]] | None,
    available_capabilities: list[str],
    agent_management_projection: Mapping[str, object] | None,
) -> dict[str, object] | None:
    # Canonical inventory, including an explicitly empty list, outranks display rows.
    source_items: object = agent_todo_source_items
    if source_items is None:
        source_items = (
            raw_agent_todo_summary.get("items")
            if isinstance(raw_agent_todo_summary, Mapping)
            else None
        )
    items: list[dict[str, object]] = []
    if isinstance(source_items, list):
        for item in source_items:
            if not isinstance(item, Mapping):
                continue
            projected_item: dict[str, object] = {
                key: item[key] for key in _PEER_SOURCE_ITEM_FIELDS if key in item
            }
            projected_item["claimed_by"] = normalize_todo_claimed_by(
                item.get("claimed_by")
            )
            items.append(projected_item)
    management_rows = (
        agent_management_projection.get("agents")
        if isinstance(agent_management_projection, Mapping)
        else None
    )
    agents: list[dict[str, object]] = []
    if isinstance(management_rows, list):
        for row in management_rows:
            if not isinstance(row, Mapping):
                continue
            agents.append(
                {
                    "agent_id": normalize_todo_claimed_by(row.get("agent_id")),
                    "state": row.get("state"),
                    "stale_claim_hint": row.get("stale_claim_hint"),
                }
            )
    raw_contract: object = effect_runtime_result(
        "quota.peer_orchestration.project",
        {
            "agent_id": agent_id,
            "registered_agents": agent_identity.get("registered_agents") or [],
            "items": items,
            "available_capabilities": available_capabilities,
            "agents": agents,
        },
    )
    contract = _as_dict_or_none(raw_contract)
    if contract is None:
        return None
    contract["assignment_key"] = peer_work_key(
        {
            "mode": "task_scoped_peer",
            "lanes": _peer_lane_refs(contract),
        },
        fallback="task_orchestration",
    )
    return contract


def _task_orchestration_work_lane_contract(
    contract: Mapping[str, object],
) -> dict[str, object]:
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
