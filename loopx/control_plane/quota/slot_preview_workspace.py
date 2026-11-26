"""Workspace gating, status projection, and preview payloads for slot accounting."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from ..agents.workspace_guard import (
    build_delivery_workspace_guard,
    delivery_workspace_identity,
)
from ..effect_program import (
    SettlementFailure,
    SettlementIdentity,
    SettlementReceipt,
    settlement_result_payload,
)
from ..todos.contract import normalize_todo_claimed_by
from .effective_action import EffectiveAction
from .ledger_readback import _int_number
from .settlement_workspace_causality import (
    LEGACY_SETTLEMENT_RECEIPT_EVIDENCE_SCHEMA_VERSION,
    completed_todo_workspace_causality,
    missing_delivery_workspace_resolution,
    resolve_settlement_workspace_requirement,
)
from .slot_preview_settlement import PreviewSettlementResult


class _SettlementReceiptCarrier(Protocol):
    @property
    def failure(self) -> SettlementFailure | None: ...

    @property
    def receipts(self) -> tuple[SettlementReceipt, ...]: ...


@dataclass(frozen=True, slots=True)
class DeliveryWorkspacePreviewState:
    """Evaluated workspace gating and causality metadata for one preview."""

    delivery_workspace: dict[str, object] | None = None
    delivery_workspace_causality: dict[str, str] | None = None
    settlement_workspace_requirement: dict[str, str] | None = None
    delivery_workspace_validated: bool = False
    failure_preview: dict[str, object] | None = None


def preview_failure_payload(
    *,
    goal_id: str,
    slots: int,
    agent_id: str | None,
    reason: object,
    before: Mapping[str, object],
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Construct one normalized dry-run spend failure preview payload."""
    payload: dict[str, object] = {
        "ok": False,
        "mode": "spend-slot",
        "dry_run": True,
        "goal_id": goal_id,
        "slots": slots,
        "agent_id": agent_id,
        "appended": False,
        "registry_mutated": False,
        "reason": reason,
        "before": before,
        "after": None,
    }
    if extra:
        payload.update(extra)
    return payload


def repair_settlement_workspace_causality(
    status_payload: Mapping[str, object],
    *,
    goal_id: str,
    settlement_identity: SettlementIdentity | None,
    causality: dict[str, str] | None,
) -> dict[str, str] | None:
    """Recover workspace causality from a completed Todo contract when unknown."""
    requirement = str((causality or {}).get("requirement") or "")
    if settlement_identity is None or requirement not in {"", "unknown"}:
        return causality
    repaired = completed_todo_workspace_causality(
        status_payload,
        goal_id=goal_id,
        todo_id=settlement_identity.todo_id,
        source=(
            "current_todo_contract_repair"
            if requirement == "unknown"
            else "completed_todo_contract_fallback"
        ),
    )
    if str((repaired or {}).get("requirement") or "") == "unknown":
        return causality
    return repaired or causality


def settlement_workspace_requirement(
    delivery_workspace_causality: dict[str, str] | None,
    settlement_identity: SettlementIdentity | None,
    settlement_result: _SettlementReceiptCarrier | None,
    delivery_completion_run: Mapping[str, object] | None,
) -> dict[str, str] | None:
    """Resolve the TypeScript-owned workspace requirement for a settled turn."""
    if settlement_identity is None:
        return None
    legacy_evidence: dict[str, object] | None = (
        {
            "schema_version": LEGACY_SETTLEMENT_RECEIPT_EVIDENCE_SCHEMA_VERSION,
            "settlement_effect_id": settlement_identity.effect_id,
            "delivery_workspace_present": bool(
                isinstance(delivery_completion_run, Mapping)
                and delivery_completion_run.get("delivery_workspace") is not None
            ),
            "receipts": [
                {
                    "step_kind": receipt.step_kind.value,
                    "status": receipt.status,
                    "effect_id": receipt.effect_id,
                }
                for receipt in settlement_result.receipts
            ],
        }
        if settlement_result is not None and settlement_result.failure is None
        else None
    )
    return resolve_settlement_workspace_requirement(
        delivery_workspace_causality,
        settlement_binding_kind=settlement_identity.binding_kind.value,
        legacy_settlement_evidence=legacy_evidence,
    )


def workspace_requirement_value(
    requirement_payload: Mapping[str, object] | None,
    delivery_workspace_causality: Mapping[str, object] | None,
) -> str:
    """Extract the effective requirement string from workspace causality payloads."""
    return str(
        (requirement_payload or {}).get("requirement")
        or (delivery_workspace_causality or {}).get("requirement")
        or ""
    )


def missing_delivery_workspace_preview(
    *,
    delivery_workspace_causality: dict[str, str] | None,
    requirement_payload: dict[str, str] | None,
    delivery_workspace: dict[str, object] | None,
    goal_id: str,
    slots: int,
    agent_id: str | None,
    before: Mapping[str, object],
) -> dict[str, object] | None:
    """Return a failure preview when a required delivery workspace snapshot is missing."""
    if delivery_workspace_identity(delivery_workspace):
        return None
    requirement = workspace_requirement_value(
        requirement_payload, delivery_workspace_causality
    )
    if not requirement or requirement == "not_required":
        return None
    resolution = missing_delivery_workspace_resolution(delivery_workspace_causality)
    if resolution is not None and resolution["decision"] == "omit_snapshot":
        return None
    reason = (
        "quota spend requires a valid delivery workspace snapshot for "
        f"settlement causality requirement {requirement}"
        if resolution is not None and resolution["decision"] == "require_snapshot"
        else (
            "quota spend requires an explicit Todo delivery contract; declare "
            "repository/write requirements or mark the Todo as explicit "
            "non-delivery, then retry the same settlement"
        )
    )
    return preview_failure_payload(
        goal_id=goal_id,
        slots=slots,
        agent_id=agent_id,
        reason=reason,
        before=before,
        extra={
            "delivery_workspace": delivery_workspace,
            "delivery_workspace_causality": delivery_workspace_causality,
            "settlement_workspace_requirement": requirement_payload,
            "delivery_workspace_resolution": resolution,
            "delivery_workspace_validated": False,
        },
    )


def workspace_repair_blocks_spend(
    before: Mapping[str, object],
    *,
    delivery_workspace_validated: bool,
    delivery_run: Mapping[str, object] | None,
    settlement_identity: SettlementIdentity | None,
    workspace_requirement: str,
) -> bool:
    """Keep the current workspace guard separate from an original settlement."""
    settlement_does_not_require_workspace = (
        delivery_run is not None
        and settlement_identity is not None
        and workspace_requirement == "not_required"
    )
    return (
        before.get("effective_action") == EffectiveAction.AGENT_WORKSPACE_REPAIR.value
        and before.get("workspace_repair_allowed") is True
        and not delivery_workspace_validated
        and not settlement_does_not_require_workspace
    )


def evaluate_delivery_workspace_preview(
    status_payload: Mapping[str, object],
    *,
    goal_id: str,
    slots: int,
    agent_id: str | None,
    workspace_path: Path | None,
    before: Mapping[str, object],
    settlement_identity: SettlementIdentity | None,
    settlement_result: _SettlementReceiptCarrier | None,
    causality: dict[str, str] | None,
    delivery_completion_run: dict[str, object] | None,
    safe_bypass_requested: bool,
) -> DeliveryWorkspacePreviewState:
    """Evaluate workspace causality, snapshot sufficiency, and workspace guards."""
    delivery_workspace_causality = repair_settlement_workspace_causality(
        status_payload,
        goal_id=goal_id,
        settlement_identity=settlement_identity,
        causality=causality,
    )
    requirement_payload = settlement_workspace_requirement(
        delivery_workspace_causality,
        settlement_identity,
        settlement_result,
        delivery_completion_run,
    )
    if safe_bypass_requested and delivery_completion_run is None:
        return DeliveryWorkspacePreviewState(
            failure_preview=preview_failure_payload(
                goal_id=goal_id,
                slots=slots,
                agent_id=agent_id,
                reason=(
                    "safe-bypass quota spend requires a latest "
                    "unspent Turn settlement writeback"
                ),
                before=before,
            )
        )
    raw_workspace_obj = (
        delivery_completion_run.get("delivery_workspace")
        if isinstance(delivery_completion_run, dict)
        else None
    )
    raw_delivery_workspace = (
        cast(dict[str, object], raw_workspace_obj)
        if isinstance(raw_workspace_obj, dict)
        else None
    )
    missing_preview = missing_delivery_workspace_preview(
        delivery_workspace_causality=delivery_workspace_causality,
        requirement_payload=requirement_payload,
        delivery_workspace=raw_delivery_workspace,
        goal_id=goal_id,
        slots=slots,
        agent_id=agent_id,
        before=before,
    )
    if missing_preview:
        return DeliveryWorkspacePreviewState(failure_preview=missing_preview)
    requirement_value = workspace_requirement_value(
        requirement_payload, delivery_workspace_causality
    )
    delivery_workspace = (
        raw_delivery_workspace
        if delivery_workspace_identity(raw_delivery_workspace)
        and requirement_value != "not_required"
        else None
    )
    delivery_workspace_guard = (
        build_delivery_workspace_guard(
            delivery_completion_run,
            agent_id=agent_id,
            current_path=workspace_path,
        )
        if delivery_completion_run and delivery_workspace
        else None
    )
    if delivery_workspace_guard:
        return DeliveryWorkspacePreviewState(
            failure_preview=preview_failure_payload(
                goal_id=goal_id,
                slots=slots,
                agent_id=agent_id,
                reason=delivery_workspace_guard["reason"],
                before=before,
                extra={
                    "workspace_guard": delivery_workspace_guard,
                    "delivery_workspace": delivery_workspace,
                    "delivery_workspace_causality": delivery_workspace_causality,
                    "delivery_workspace_validated": False,
                },
            )
        )
    delivery_workspace_validated = bool(delivery_workspace)
    if workspace_repair_blocks_spend(
        before,
        delivery_workspace_validated=delivery_workspace_validated,
        delivery_run=delivery_completion_run,
        settlement_identity=settlement_identity,
        workspace_requirement=requirement_value,
    ):
        return DeliveryWorkspacePreviewState(
            failure_preview=preview_failure_payload(
                goal_id=goal_id,
                slots=slots,
                agent_id=agent_id,
                reason=(
                    "agent workspace guard requires moving to an independent "
                    "worktree and rerunning quota should-run before quota spend"
                ),
                before=before,
                extra={
                    "delivery_workspace": delivery_workspace,
                    "delivery_workspace_causality": delivery_workspace_causality,
                    "delivery_workspace_validated": False,
                },
            )
        )
    return DeliveryWorkspacePreviewState(
        delivery_workspace=delivery_workspace,
        delivery_workspace_causality=delivery_workspace_causality,
        settlement_workspace_requirement=requirement_payload,
        delivery_workspace_validated=delivery_workspace_validated,
    )


def queue_item_for_goal(
    status_payload: Mapping[str, object],
    *,
    goal_id: str,
) -> dict[str, object]:
    """Return the attention-queue item matching ``goal_id``, or an empty dict."""
    queue = status_payload.get("attention_queue")
    if not isinstance(queue, dict):
        return {}
    queue_items = queue.get("items")
    if not isinstance(queue_items, list):
        return {}
    for item in queue_items:
        if isinstance(item, dict) and str(item.get("goal_id") or "") == goal_id:
            return cast(dict[str, object], item)
    return {}


def set_quota_for_goal(
    status_payload: dict[str, object],
    *,
    goal_id: str,
    quota: Mapping[str, object],
) -> None:
    """Overwrite a Goal's projected quota snapshot in both history and queue entries."""
    run_history = status_payload.get("run_history")
    if isinstance(run_history, dict):
        run_goals = run_history.get("goals")
        if isinstance(run_goals, list):
            for goal in run_goals:
                if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
                    goal["quota"] = dict(quota)

    queue = status_payload.get("attention_queue")
    if isinstance(queue, dict):
        queue_items = queue.get("items")
        if isinstance(queue_items, list):
            for item in queue_items:
                if isinstance(item, dict) and str(item.get("goal_id") or "") == goal_id:
                    item["quota"] = dict(quota)
                    project_asset = item.get("project_asset")
                    if isinstance(project_asset, dict) and project_asset:
                        project_asset["quota"] = dict(quota)


def build_preview_success_payload(
    status_payload: Mapping[str, object],
    *,
    goal_id: str,
    slots: int,
    agent_id: str | None,
    before: Mapping[str, object],
    before_quota: Mapping[str, object],
    after_decision: Callable[[dict[str, object]], dict[str, object]],
    quota_status_builder: Callable[..., dict[str, object]],
    todo_id: str | None,
    replan_obligation_id: str | None,
    settlement_identity: SettlementIdentity | None,
    settlement_result: PreviewSettlementResult | None,
    delivery_completion_run: Mapping[str, object] | None,
    workspace_state: DeliveryWorkspacePreviewState,
    safe_bypass_spend: bool,
    self_repair_spend: bool,
    capability_repair_spend: bool,
    delivery_completion_spend: bool,
) -> dict[str, object]:
    """Project before -> after quota status and build the admitted spend preview."""
    queue_item = queue_item_for_goal(status_payload, goal_id=goal_id)
    after_status = deepcopy(dict(status_payload))
    after_goal: dict[str, object] = {
        "quota": {
            **before_quota,
            "spent_slots": _int_number(before_quota.get("spent_slots"), default=0)
            + slots,
        }
    }
    after_quota = quota_status_builder(
        after_goal,
        waiting_on=str(before.get("waiting_on") or ""),
        severity=str(queue_item.get("severity") or ""),
        lifecycle_phase=before.get("lifecycle_phase") or queue_item.get("lifecycle_phase"),
        lifecycle_flags=before.get("lifecycle_flags") or queue_item.get("lifecycle_flags"),
        status=before.get("status") or queue_item.get("status"),
    )
    set_quota_for_goal(after_status, goal_id=goal_id, quota=after_quota)
    after = after_decision(after_status)

    return {
        "ok": True,
        "mode": "spend-slot",
        "dry_run": True,
        "goal_id": goal_id,
        "slots": slots,
        "agent_id": agent_id,
        "appended": False,
        "registry_mutated": False,
        "before": before,
        "after": after,
        "would_throttle": after.get("state") == "throttled",
        "reason": (
            f"dry-run preview: spending {slots} slot(s) accounts for latest "
            f"validated delivery {delivery_completion_run.get('classification')} "
            f"after current {before.get('state')} guard"
            if delivery_completion_spend and delivery_completion_run
            else (
                f"dry-run preview: spending {slots} slot(s) would move "
                f"{goal_id} from {before.get('state')} to {after.get('state')}"
            )
        ),
        "accounting_projection": {
            "schema_version": "quota_slot_accounting_projection_v0",
            "settlement_event_semantics": "append_only",
            "spent_slots_semantics": "rolling_window_aggregate",
            "before_after_semantics": "same_status_payload_projection",
            "window_hours": _int_number(before_quota.get("window_hours"), default=0),
        },
        "rolling_window_note": (
            "before -> after is a same-status-payload projection. Later quota status "
            "recomputes spent_slots from quota_slot_spent events still inside window_hours, "
            "so the visible total can stay flat or decrease as older spends expire; that "
            "does not replay or undo the appended settlement event."
        ),
        "todo_id": todo_id,
        "replan_obligation_id": replan_obligation_id,
        "turn_instance_id": (
            settlement_identity.turn_instance_id
            if settlement_identity is not None
            else None
        ),
        "settlement_identity": (
            settlement_identity.as_dict()
            if settlement_identity is not None
            else None
        ),
        "settlement_result": (
            settlement_result_payload(settlement_result)
            if settlement_result is not None
            else None
        ),
        "safe_bypass_spend": safe_bypass_spend,
        "self_repair_spend": self_repair_spend,
        "capability_repair_spend": capability_repair_spend,
        "delivery_completion_spend": delivery_completion_spend,
        "delivery_run_generated_at": (
            delivery_completion_run.get("generated_at")
            if delivery_completion_run
            else None
        ),
        "delivery_run_classification": (
            delivery_completion_run.get("classification")
            if delivery_completion_run
            else None
        ),
        "delivery_run_agent_id": (
            normalize_todo_claimed_by(delivery_completion_run.get("agent_id"))
            if delivery_completion_run
            else None
        ),
        "delivery_run_recommended_action": (
            delivery_completion_run.get("recommended_action")
            if delivery_completion_run
            else None
        ),
        "delivery_workspace": workspace_state.delivery_workspace,
        "delivery_workspace_causality": workspace_state.delivery_workspace_causality,
        "settlement_workspace_requirement": (
            workspace_state.settlement_workspace_requirement
        ),
        "delivery_workspace_validated": workspace_state.delivery_workspace_validated,
    }
