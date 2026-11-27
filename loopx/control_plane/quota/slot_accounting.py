"""Quota slot preview accounting facade and settlement preview orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from ...paths import validate_goal_id_path_segment as _validate_goal_id_path_segment
from ..effect_program import (
    SettlementFailure,
    settlement_result_payload,
)
from ..todos.contract import (
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_replan_obligation_id,
)
from .effective_action import EffectiveAction
from .ledger_readback import (
    QUOTA_SLOT_SPENT_CLASSIFICATION as QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION as QUOTA_SLOT_VOIDED_CLASSIFICATION,
    _int_number,
    load_quota_event_from_run as load_quota_event_from_run,
    net_quota_slot_spend as net_quota_slot_spend,
    quota_slot_contribution as quota_slot_contribution,
)
from .slot_preview_settlement import (
    DELIVERY_COMPLETION_SPEND_STATES,
    TERMINAL_NO_FOLLOWUP_DECISION_STATE,
    admits_delivery_completion_spend_state,
    resolve_preview_settlement,
    select_latest_unspent_turn_settlement_run,
    todo_binding_error,
    unbound_visible_goal_settlement_failure,
)
from .slot_preview_workspace import (
    build_preview_success_payload,
    evaluate_delivery_workspace_preview,
    preview_failure_payload,
)
from .spend_commit import (
    build_quota_slot_spend_event as build_quota_slot_spend_event,
    record_quota_slot_spend_from_preview as record_quota_slot_spend_from_preview,
)
from .spend_sources import DEFAULT_SLOT_SPEND_SOURCE
from .void_commit import (
    build_quota_slot_void_event as build_quota_slot_void_event,
    build_quota_slot_void_preview_for_decision as build_quota_slot_void_preview_for_decision,
    record_quota_slot_void_from_preview as record_quota_slot_void_from_preview,
)

QuotaDecisionBuilder = Callable[[dict[str, object]], dict[str, object]]
QuotaStatusBuilder = Callable[..., dict[str, object]]


def _latest_unspent_turn_settlement_run(
    runtime_root: Path,
    goal_id: str,
    *,
    agent_id: str | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Return the latest same-agent Turn settlement that still needs accounting.

    Unchanged monitor polls, scheduler acknowledgements, and plain state
    refreshes are quota-neutral. They may occur after validation and before
    accounting, so they must not hide the accountable run. A state refresh
    with a non-settling delivery outcome and other non-delivery events remain
    fail closed. A typed blocked ``outcome_gap`` settles the Turn without being
    reclassified as delivery progress.
    """
    return select_latest_unspent_turn_settlement_run(
        runtime_root,
        goal_id,
        agent_id=agent_id,
        goal_ref=goal_ref,
    )


def build_quota_slot_preview_for_decision(
    status_payload: Mapping[str, object],
    *,
    goal_id: str,
    before: Mapping[str, object],
    after_decision: QuotaDecisionBuilder,
    quota_status_builder: QuotaStatusBuilder,
    self_repair_spend_actions: set[str] | frozenset[str],
    slots: int = 1,
    agent_id: str | None = None,
    workspace_path: Path | None = None,
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    turn_instance_id: str | None = None,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Validate settlement admission and preview a quota spend state transition."""
    safe_goal_id = _validate_goal_id_path_segment(str(goal_id or ""))
    safe_slots = max(1, _int_number(slots, default=1))
    safe_requested_agent_id = normalize_todo_claimed_by(agent_id)
    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    normalized_replan_obligation_id = normalize_todo_replan_obligation_id(
        replan_obligation_id
    )
    unbound_visible_goal_failure = unbound_visible_goal_settlement_failure(
        source=source,
        before=before,
        requested_todo_id=normalized_todo_id,
        requested_replan_obligation_id=normalized_replan_obligation_id,
        turn_instance_id=turn_instance_id,
    )
    if unbound_visible_goal_failure is not None:
        return preview_failure_payload(
            goal_id=safe_goal_id,
            slots=safe_slots,
            agent_id=safe_requested_agent_id,
            reason=cast(SettlementFailure, unbound_visible_goal_failure.failure).reason,
            before=before,
            extra={
                "settlement_result": settlement_result_payload(
                    unbound_visible_goal_failure
                ),
                "delivery_workspace": None,
                "delivery_workspace_causality": None,
                "delivery_workspace_validated": False,
            },
        )
    raw_runtime_root = status_payload.get("runtime_root")
    settlement = resolve_preview_settlement(
        raw_runtime_root=raw_runtime_root,
        source=source,
        goal_id=safe_goal_id,
        agent_id=safe_requested_agent_id,
        todo_id=normalized_todo_id,
        replan_obligation_id=normalized_replan_obligation_id,
        turn_instance_id=turn_instance_id,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    settlement_identity = settlement.identity
    settlement_result = settlement.result
    if settlement.reason:
        return preview_failure_payload(
            goal_id=safe_goal_id,
            slots=safe_slots,
            agent_id=safe_requested_agent_id,
            reason=settlement.reason,
            before=before,
            extra={
                "settlement_result": (
                    settlement_result_payload(settlement_result)
                    if settlement_result is not None
                    else None
                )
            },
        )
    binding_error = todo_binding_error(
        source=source,
        status_payload=status_payload,
        goal_id=safe_goal_id,
        before=before,
        requested_todo_id=normalized_todo_id,
        requested_replan_obligation_id=normalized_replan_obligation_id,
        agent_id=safe_requested_agent_id,
        settlement_identity=settlement_identity,
        delivery_run=(
            settlement.delivery_run if settlement_identity is not None else None
        ),
    )
    if binding_error:
        return preview_failure_payload(
            goal_id=safe_goal_id,
            slots=safe_slots,
            agent_id=safe_requested_agent_id,
            reason=binding_error,
            before=before,
        )
    safe_bypass_requested = (
        before.get("state") == "operator_gate"
        or before.get("recovery_delivery_allowed") is True
        or before.get("effective_action") == EffectiveAction.OUTCOME_FLOOR_RECOVERY.value
    ) and before.get("safe_bypass_allowed") is True
    self_repair_spend = before.get("effective_action") in self_repair_spend_actions
    capability_repair_spend = (
        before.get("effective_action") == EffectiveAction.CAPABILITY_BRIDGE_REPAIR.value
        and before.get("capability_repair_allowed") is True
    )
    delivery_completion_run = settlement.delivery_run or (
        _latest_unspent_turn_settlement_run(
            Path(str(raw_runtime_root)).expanduser(),
            safe_goal_id,
            agent_id=safe_requested_agent_id,
            goal_ref=goal_ref,
        )
        if raw_runtime_root
        else None
    )
    workspace_state = evaluate_delivery_workspace_preview(
        status_payload,
        goal_id=safe_goal_id,
        slots=safe_slots,
        agent_id=safe_requested_agent_id,
        workspace_path=workspace_path,
        before=before,
        settlement_identity=settlement_identity,
        settlement_result=settlement_result,
        causality=settlement.delivery_workspace_causality,
        delivery_completion_run=delivery_completion_run,
        safe_bypass_requested=safe_bypass_requested,
    )
    if workspace_state.failure_preview is not None:
        return workspace_state.failure_preview

    safe_bypass_spend = safe_bypass_requested
    delivery_completion_spend = (
        delivery_completion_run is not None
        and bool(before.get("ok"))
        and (
            settlement_identity is not None
            or not before.get("should_run")
            or before.get("effective_action")
            == EffectiveAction.EXTERNAL_EVIDENCE_OBSERVE.value
            or (
                before.get("effective_action")
                == EffectiveAction.AGENT_WORKSPACE_REPAIR.value
                and workspace_state.delivery_workspace_validated
            )
        )
        and before.get("effective_action")
        != EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED.value
        and not safe_bypass_spend
        and admits_delivery_completion_spend_state(
            before=before,
            identity=settlement_identity,
            settlement=settlement,
        )
    )
    if delivery_completion_spend:
        capability_repair_spend = False
    if not before.get("ok") or (
        not before.get("should_run")
        and not safe_bypass_spend
        and not capability_repair_spend
        and not delivery_completion_spend
    ):
        return preview_failure_payload(
            goal_id=safe_goal_id,
            slots=safe_slots,
            agent_id=safe_requested_agent_id,
            reason=(
                before.get("reason")
                or "goal is not eligible for quota accounting preview"
            ),
            before=before,
            extra={
                # Transport the actual rejecting action, not an incidental quota
                # snapshot on an earlier validation/workspace refusal. TS owns the
                # mapping to settlement failure kinds; prose carries no such rule.
                "quota_rejection_action": (
                    before.get("effective_action")
                    if before.get("ok") is True
                    else None
                ),
            },
        )
    raw_before_quota = before.get("quota")
    before_quota = (
        cast(dict[str, object], raw_before_quota)
        if isinstance(raw_before_quota, dict)
        else {}
    )
    if not before_quota:
        return preview_failure_payload(
            goal_id=safe_goal_id,
            slots=safe_slots,
            agent_id=safe_requested_agent_id,
            reason="goal has no quota payload to preview",
            before=before,
        )
    return build_preview_success_payload(
        status_payload,
        goal_id=safe_goal_id,
        slots=safe_slots,
        agent_id=safe_requested_agent_id,
        before=before,
        before_quota=before_quota,
        after_decision=after_decision,
        quota_status_builder=quota_status_builder,
        todo_id=normalized_todo_id,
        replan_obligation_id=normalized_replan_obligation_id,
        settlement_identity=settlement_identity,
        settlement_result=settlement_result,
        delivery_completion_run=delivery_completion_run,
        workspace_state=workspace_state,
        safe_bypass_spend=safe_bypass_spend,
        self_repair_spend=self_repair_spend,
        capability_repair_spend=capability_repair_spend,
        delivery_completion_spend=delivery_completion_spend,
    )


__all__ = [
    "DELIVERY_COMPLETION_SPEND_STATES",
    "QUOTA_SLOT_SPENT_CLASSIFICATION",
    "QUOTA_SLOT_VOIDED_CLASSIFICATION",
    "QuotaDecisionBuilder",
    "QuotaStatusBuilder",
    "TERMINAL_NO_FOLLOWUP_DECISION_STATE",
    "_int_number",
    "_latest_unspent_turn_settlement_run",
    "_validate_goal_id_path_segment",
    "build_quota_slot_preview_for_decision",
    "build_quota_slot_spend_event",
    "build_quota_slot_void_event",
    "build_quota_slot_void_preview_for_decision",
    "load_quota_event_from_run",
    "net_quota_slot_spend",
    "quota_slot_contribution",
    "record_quota_slot_spend_from_preview",
    "record_quota_slot_void_from_preview",
]
