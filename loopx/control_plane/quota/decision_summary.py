from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from ...state_projection import actions_are_projection_aligned
from ..goals.contract_health import project_contract_health_for_goal
from ..goals.goal_frontier import (
    autonomous_replan_is_required,
    autonomous_replan_scope_decision,
    goal_frontier_is_terminal_no_followup,
)
from ..effect_runtime import effect_runtime_result
from ..todos.contract import normalize_todo_claimed_by
from ..work_items.work_lane import work_lane_contract_is_due_monitor_attempt


def quota_plan_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten quota plan state groups into ordered goal items."""

    groups = plan.get("groups") if isinstance(plan.get("groups"), dict) else {}
    items: list[dict[str, Any]] = []
    for state_items in groups.values():
        if not isinstance(state_items, list):
            continue
        items.extend(item for item in state_items if isinstance(item, dict))
    return items


class QuotaDecisionPacket(TypedDict):
    should_run: bool
    normal_delivery_allowed: bool
    recovery_delivery_allowed: bool
    effective_action: str | None
    self_repair_allowed: bool
    capability_repair_allowed: bool
    workspace_repair_allowed: bool
    state: str
    safe_bypass_allowed: bool
    safe_bypass_kind: str | None
    blocked_action_scope: str | None
    compute: float | None
    window_hours: int | None
    slot_minutes: int | None
    spent_slots: int | None
    allowed_slots: int | None


def compact_quota_decision(decision: dict[str, Any]) -> QuotaDecisionPacket:
    quota = decision.get("quota") if isinstance(decision.get("quota"), dict) else {}
    return {
        "should_run": bool(decision.get("should_run")),
        "normal_delivery_allowed": bool(decision.get("normal_delivery_allowed")),
        "recovery_delivery_allowed": bool(decision.get("recovery_delivery_allowed")),
        "effective_action": decision.get("effective_action"),
        "self_repair_allowed": bool(decision.get("self_repair_allowed")),
        "capability_repair_allowed": bool(decision.get("capability_repair_allowed")),
        "workspace_repair_allowed": bool(decision.get("workspace_repair_allowed")),
        "state": str(decision.get("state") or ""),
        "safe_bypass_allowed": bool(decision.get("safe_bypass_allowed")),
        "safe_bypass_kind": decision.get("safe_bypass_kind"),
        "blocked_action_scope": decision.get("blocked_action_scope"),
        "compute": quota.get("compute"),
        "window_hours": quota.get("window_hours"),
        "slot_minutes": quota.get("slot_minutes"),
        "spent_slots": quota.get("spent_slots"),
        "allowed_slots": quota.get("allowed_slots"),
    }


def quota_decision_agent_id(decision: dict[str, Any]) -> str | None:
    agent_identity = (
        decision.get("agent_identity")
        if isinstance(decision.get("agent_identity"), dict)
        else {}
    )
    return normalize_todo_claimed_by(agent_identity.get("agent_id"))


# A Goal whose declared active state file is gone cannot be advanced by an
# automatic turn: there is no Goal intent, acceptance or stop condition to read.
# `loopx status` already reports the per-goal `state_file_missing` finding with
# its reconnect/retire repair, so the quota gate must not keep authorizing
# compute for it. Source-registry provenance findings are deliberately shadowed
# by the status projection and stay out of this guard.
_GOAL_MISSING_STATE_FINDING = "state_file_missing"


def goal_missing_state_blocker(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
) -> dict[str, Any] | None:
    """Return the status finding that says this Goal's active state is gone."""

    global_registry = status_payload.get("global_registry")
    if not isinstance(global_registry, dict):
        return None
    findings = global_registry.get("findings")
    if not isinstance(findings, list):
        return None
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        kind = finding.get("kind")
        if (
            isinstance(kind, str)
            and kind == _GOAL_MISSING_STATE_FINDING
            and str(finding.get("goal_id") or "") == goal_id
        ):
            return dict(finding)
    return None


def goal_status_health_ok(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    fallback: bool,
) -> bool:
    """Keep global guards fail-closed without importing unrelated goal errors."""

    if goal_missing_state_blocker(status_payload, goal_id=goal_id) is not None:
        return False
    contract = (
        status_payload.get("contract")
        if isinstance(status_payload.get("contract"), dict)
        else {}
    )
    if "error_diagnostics" not in contract:
        return fallback
    global_registry = status_payload.get("global_registry")
    if isinstance(global_registry, dict) and global_registry.get("ok") is False:
        return False
    scoped = project_contract_health_for_goal(contract, goal_id=goal_id)
    return scoped.get("ok") is True


@dataclass(frozen=True)
class QuotaRunDecision:
    normal_delivery_allowed: bool
    recovery_delivery_allowed: bool
    self_repair_allowed: bool
    capability_repair_allowed: bool
    workspace_repair_allowed: bool
    should_run: bool
    effective_action: str
    reason: str
    state: str
    quota: dict[str, Any]
    replan_decision_allowed: bool


def refine_quota_recommended_action(
    selected_action: Any,
    *,
    task_orchestration_contract: dict[str, Any] | None,
    capability_gate: dict[str, Any] | None,
    capability_monitor_fallback: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
    workspace_guard: dict[str, Any] | None,
    automation_prompt_upgrade: dict[str, Any] | None,
    automation_prompt_upgrade_required: bool,
    replan_obligation: dict[str, Any] | None,
    replan_decision_allowed: bool,
) -> Any:
    """Apply ordered quota guards before agent-lane frontier refinement."""

    refined = (
        str(
            task_orchestration_contract.get("coordinator_obligation")
            or selected_action
            or ""
        )
        if task_orchestration_contract
        and str(task_orchestration_contract.get("execution_state") or "ready")
        == "ready"
        else selected_action
    )
    if (
        capability_monitor_fallback
        and isinstance(work_lane_contract, dict)
        and work_lane_contract.get("action")
    ):
        refined = work_lane_contract["action"]

    due_monitor_attempt = work_lane_contract_is_due_monitor_attempt(work_lane_contract)
    if capability_gate and not due_monitor_attempt and not capability_monitor_fallback:
        if capability_gate.get("action") in {"repair_bridge", "ask_owner", "skip"}:
            refined = (
                capability_gate.get("owner_action")
                or capability_gate.get("reason")
                or refined
            )
        elif capability_gate.get("action") == "run":
            blocked = capability_gate.get("blocked_candidates")
            blocked = blocked if isinstance(blocked, list) else []
            runnable = capability_gate.get("runnable_candidates")
            runnable = runnable if isinstance(runnable, list) else []
            if any(
                isinstance(item, dict)
                and actions_are_projection_aligned(refined, item.get("text"))
                for item in blocked
            ):
                refined = next(
                    (
                        text
                        for item in runnable
                        if isinstance(item, dict)
                        and (text := str(item.get("text") or "").strip())
                    ),
                    refined,
                )

    if workspace_guard:
        refined = (
            workspace_guard.get("required_action")
            or workspace_guard.get("reason")
            or refined
        )
    if automation_prompt_upgrade_required:
        upgrade = automation_prompt_upgrade or {}
        refined = upgrade.get("recommended_action") or upgrade.get("reason") or refined
    if replan_decision_allowed:
        obligation = replan_obligation or {}
        refined = (
            str(obligation.get("recommended_action") or "").strip()
            or str(obligation.get("stop_condition") or "").strip()
            or (
                "Run one bounded autonomous replan slice and write back the "
                "selected todo/frontier changes."
            )
        )
    return refined


def _guard_strings(guard: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, str] | None:
    # Preserve the compatibility caller's Python string coercion, including whitespace.
    return {field: str(guard.get(field) or "") for field in fields} if guard else None


def resolve_quota_run_decision(
    *,
    normal_delivery_allowed: bool,
    recovery_delivery_allowed: bool,
    self_repair_allowed: bool,
    stall_self_repair: dict[str, Any] | None,
    state: str,
    quota: dict[str, Any],
    reason: str,
    capability_gate: dict[str, Any] | None,
    capability_monitor_fallback: dict[str, Any] | None,
    workspace_guard: dict[str, Any] | None,
    automation_prompt_upgrade: dict[str, Any] | None,
    automation_prompt_upgrade_required: bool,
    replan_obligation: dict[str, Any] | None,
    goal_health_ok: bool,
    inbox_reply_due: bool,
    inbox_material_review_due: bool,
    agent_frontier_id: str | None,
    registered_agent_ids: list[str],
    goal_frontier_projection: dict[str, Any] | None,
    task_orchestration_contract: dict[str, Any] | None,
) -> QuotaRunDecision:
    """Apply the ordered final guards for one quota run decision."""

    replan_required = autonomous_replan_is_required(replan_obligation)
    # Scope evaluation can synthesize a legacy identity; preserve its lazy read boundary.
    scope = autonomous_replan_scope_decision(
        replan_obligation, agent_id=agent_frontier_id,
        registered_agent_ids=registered_agent_ids,
    ) if replan_required and not (inbox_reply_due or inbox_material_review_due) else {}
    repair_action = (stall_self_repair.get("effective_action")
        if isinstance(stall_self_repair, dict) else None)
    result = effect_runtime_result("quota.run_decision", {
        "normal_delivery_allowed": normal_delivery_allowed,
        "recovery_delivery_allowed": recovery_delivery_allowed,
        "self_repair_allowed": self_repair_allowed,
        "repair_effective_action": str(repair_action or ""),
        "state": state, "reason": reason,
        "quota_focus_wait": bool(quota.get("focus_wait")),
        "capability_gate": _guard_strings(capability_gate, ("action", "reason")),
        "capability_monitor_fallback": bool(capability_monitor_fallback),
        "workspace_guard": _guard_strings(workspace_guard, ("reason",)),
        "automation_prompt_upgrade": _guard_strings(automation_prompt_upgrade, ("reason",)),
        "automation_prompt_upgrade_required": automation_prompt_upgrade_required,
        "replan_required": replan_required,
        "replan_applies": bool(scope.get("applies")),
        "goal_health_ok": goal_health_ok,
        "inbox_reply_due": inbox_reply_due,
        "inbox_material_review_due": inbox_material_review_due,
        "terminal_no_followup": goal_frontier_is_terminal_no_followup(
            projection=goal_frontier_projection),
        "task_orchestration_contract": _guard_strings(
            task_orchestration_contract, ("execution_state", "mode")),
    })
    patch = result.pop("quota_patch")
    # Only typed edits cross the wire; preserve source numbers and unmodified metadata.
    return QuotaRunDecision(quota={**quota, **patch} if patch else quota, **result)
