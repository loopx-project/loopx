from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..control_plane.quota.error_codes import (
    HeartbeatReceiptIdentityConflictError,
    QuotaActionSelectionConflictError,
    QuotaActionSelectionConflictKind,
)
from ..control_plane.quota.heartbeat_receipt import (
    find_heartbeat_receipt,
    heartbeat_receipt_pending_action_todo_id,
    heartbeat_receipt_settlement_replan_obligation_id,
    heartbeat_receipt_settlement_todo_id,
    retain_pending_heartbeat_action_selection,
)
from ..control_plane.scheduler.execution_context import (
    GUIDED_START_TURN_RUNTIME_PROFILES,
    render_scheduler_execution_args,
)
from ..control_plane.todos.contract import normalize_todo_id
from ..control_plane.work_items.action_selection_contract import (
    bind_action_selection_recovery_command,
    build_action_selection_recovery_fields,
    current_action_selection_admission,
)
from .quota_context import QuotaCommandContext


@dataclass(frozen=True, slots=True)
class RequestedQuotaActionSelection:
    requested_todo_id: str | None
    receipt: dict[str, object] | None
    receipt_bound_todo_id: str | None
    receipt_bound_replan_obligation_id: str | None
    receipt_pending_action_todo_id: str | None
    receipt_identity_upgraded: bool

    @property
    def requested_todo_id_for_decision(self) -> str | None:
        if self.receipt_bound_todo_id is not None:
            return None
        return self.requested_todo_id

    @property
    def retained_todo_id_for_decision(self) -> str | None:
        if (
            self.requested_todo_id is not None
            or self.receipt_bound_todo_id is not None
            or self.receipt_bound_replan_obligation_id is not None
        ):
            return None
        return self.receipt_pending_action_todo_id


@dataclass(frozen=True, slots=True)
class ActionSelectionPreflightResult:
    rejected: bool
    receipt: dict[str, object] | None
    receipt_status: str
    receipt_appended: bool


def _requested_quota_action_todo_id(
    args: argparse.Namespace,
) -> str | None:
    if not (
        # Hosts may supply their own Turn identity without guided-start support.
        # Forward their explicit selection to the same typed admission owner.
        bool(getattr(args, "turn_instance_id", None))
        or bool(args.codex_app)
        or bool(getattr(args, "trae_app", False))
        or args.runtime_profile
        in {profile.value for profile in GUIDED_START_TURN_RUNTIME_PROFILES}
    ):
        return None
    return normalize_todo_id(args.todo_id)


def load_requested_quota_action_selection(
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    turn_instance_id: str | None,
    goal_ref: Mapping[str, object] | None = None,
) -> RequestedQuotaActionSelection:
    requested_todo_id = _requested_quota_action_todo_id(args)
    if not turn_instance_id:
        return RequestedQuotaActionSelection(
            requested_todo_id=requested_todo_id,
            receipt=None,
            receipt_bound_todo_id=None,
            receipt_bound_replan_obligation_id=None,
            receipt_pending_action_todo_id=None,
            receipt_identity_upgraded=False,
        )
    existing = find_heartbeat_receipt(
        runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        turn_instance_id=turn_instance_id,
        goal_ref=goal_ref,
    )
    if not existing:
        return RequestedQuotaActionSelection(
            requested_todo_id=requested_todo_id,
            receipt=None,
            receipt_bound_todo_id=None,
            receipt_bound_replan_obligation_id=None,
            receipt_pending_action_todo_id=None,
            receipt_identity_upgraded=False,
        )
    details_value = existing.get("details")
    details: Mapping[str, object] = (
        details_value if isinstance(details_value, Mapping) else {}
    )
    return RequestedQuotaActionSelection(
        requested_todo_id=requested_todo_id,
        receipt=existing,
        receipt_bound_todo_id=heartbeat_receipt_settlement_todo_id(existing),
        receipt_bound_replan_obligation_id=(
            heartbeat_receipt_settlement_replan_obligation_id(existing)
        ),
        receipt_pending_action_todo_id=(
            heartbeat_receipt_pending_action_todo_id(existing)
        ),
        receipt_identity_upgraded=(
            str(details.get("settlement_receipt_revision") or "") == "identity_upgrade"
        ),
    )


def _requested_quota_action_selection_preflight(
    payload: dict[str, object],
    *,
    requested_todo_id: str | None,
    receipt_bound_todo_id: str | None,
    receipt_bound_replan_obligation_id: str | None,
    receipt_pending_action_todo_id: str | None = None,
    receipt_identity_upgraded: bool = False,
) -> dict[str, object] | None:
    if not requested_todo_id:
        return None
    if receipt_bound_todo_id:
        if requested_todo_id != receipt_bound_todo_id:
            raise HeartbeatReceiptIdentityConflictError(
                "heartbeat receipt settlement identity conflicts with the "
                "current selected Todo: explicitly requested Todo differs"
            )
        return None
    interaction = payload.get("interaction_contract")
    agent_channel = (
        interaction.get("agent_channel") if isinstance(interaction, Mapping) else None
    )
    agent_channel = agent_channel if isinstance(agent_channel, Mapping) else {}
    # These two facts are what decide admission, so read them once and keep them
    # for the refusal: a caller that is told "not admitted" has to be able to see
    # which side of the delivery boundary refused it without re-deriving it from
    # prose.
    agent_must_attempt = agent_channel.get("must_attempt") is True
    agent_delivery_allowed = agent_channel.get("delivery_allowed") is not False
    selected_todo_id, admitted = current_action_selection_admission(
        payload,
        requested_todo_id=requested_todo_id,
        agent_must_attempt=agent_must_attempt,
        agent_delivery_refused=not agent_delivery_allowed,
    )
    if receipt_bound_replan_obligation_id:
        if not receipt_identity_upgraded:
            # A Turn that started directly in autonomous replan has no Todo
            # selection authority to replace.  A later same-Turn --todo-id is
            # therefore a harmless settled replay, not successor delivery.
            return None
        if requested_todo_id == receipt_pending_action_todo_id:
            return None
        raise QuotaActionSelectionConflictError(
            QuotaActionSelectionConflictKind.CONFLICT,
            requested_todo_id=requested_todo_id,
            selected_todo_id=receipt_pending_action_todo_id,
            qualification_state=(
                "retained_selection" if receipt_pending_action_todo_id else None
            ),
            receipt_replan_obligation_id=receipt_bound_replan_obligation_id,
        )
    if admitted:
        return None

    qualification_value = payload.get("action_selection_qualification")
    qualification: Mapping[str, object] = (
        qualification_value if isinstance(qualification_value, Mapping) else {}
    )
    if not isinstance(qualification_value, Mapping):
        raise QuotaActionSelectionConflictError(
            QuotaActionSelectionConflictKind.UNQUALIFIED,
            requested_todo_id=requested_todo_id,
            selected_todo_id=selected_todo_id,
        )
    qualification_state = str(qualification.get("state") or "")
    if qualification_state not in {"deferred", "rejected"}:
        if selected_todo_id == requested_todo_id:
            # The projection selects exactly what was requested, so there is no
            # selection to reconcile: the Turn simply was not admitted to settle
            # it.  Naming a conflict here would put the same id on both sides of
            # the sentence and hide the refusal that actually happened, so name
            # the admission facts instead, and the unsettled prior Turn when the
            # same payload already carries that typed recovery.
            recovery_value = payload.get("unsettled_host_turn_recovery")
            recovery: Mapping[str, object] = (
                recovery_value if isinstance(recovery_value, Mapping) else {}
            )
            prior_turn_id = str(recovery.get("prior_turn_instance_id") or "") or None
            repair = str(recovery.get("repair") or "") or None
            raise QuotaActionSelectionConflictError(
                QuotaActionSelectionConflictKind.NOT_ADMITTED,
                requested_todo_id=requested_todo_id,
                selected_todo_id=selected_todo_id,
                qualification_state=qualification_state,
                unsettled_prior_turn_instance_id=prior_turn_id,
                unsettled_repair=repair,
                admission_must_attempt=agent_must_attempt,
                admission_delivery_allowed=agent_delivery_allowed,
            )
        raise QuotaActionSelectionConflictError(
            QuotaActionSelectionConflictKind.CONFLICT,
            requested_todo_id=requested_todo_id,
            selected_todo_id=selected_todo_id,
            qualification_state=qualification_state,
        )
    return build_action_selection_recovery_fields(payload)


def reconcile_requested_quota_action_selection(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    registry_path: Path,
    context: QuotaCommandContext,
    selection: RequestedQuotaActionSelection,
    goal_ref: Mapping[str, object] | None = None,
) -> ActionSelectionPreflightResult:
    recovery = _requested_quota_action_selection_preflight(
        payload,
        requested_todo_id=selection.requested_todo_id,
        receipt_bound_todo_id=selection.receipt_bound_todo_id,
        receipt_bound_replan_obligation_id=(
            selection.receipt_bound_replan_obligation_id
        ),
        receipt_pending_action_todo_id=selection.receipt_pending_action_todo_id,
        receipt_identity_upgraded=selection.receipt_identity_upgraded,
    )
    if recovery is None:
        return ActionSelectionPreflightResult(
            rejected=False,
            receipt=selection.receipt,
            receipt_status="replayed",
            receipt_appended=False,
        )

    payload.update(recovery)
    bind_action_selection_recovery_command(
        payload,
        registry_path=str(registry_path),
        runtime_root=str(context.runtime_root),
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        turn_instance_id=context.heartbeat_turn_id,
        available_capabilities=args.available_capabilities,
        scheduler_args=render_scheduler_execution_args(
            scheduler_execution_context=context.scheduler_context
        ),
        goal_ref=goal_ref,
    )
    receipt, receipt_status, receipt_appended = _retain_deferred_action_selection(
        payload,
        args,
        runtime_root=context.runtime_root,
        turn_instance_id=context.heartbeat_turn_id,
        selection=selection,
        goal_ref=goal_ref,
    )
    return ActionSelectionPreflightResult(
        rejected=True,
        receipt=receipt,
        receipt_status=receipt_status,
        receipt_appended=receipt_appended,
    )


def inline_action_selection_reentry_args(
    payload: Mapping[str, object],
    args: argparse.Namespace,
    *,
    selection: RequestedQuotaActionSelection,
    preflight: ActionSelectionPreflightResult,
    turn_instance_id: str | None,
) -> argparse.Namespace | None:
    """Adapt one typed reentry instruction after the exact choice is durable."""
    qualification = payload.get("action_selection_qualification")
    if (
        not isinstance(qualification, Mapping)
        or qualification.get("inline_reentry_allowed") is not True
        or preflight.receipt is None
        or selection.requested_todo_id is None
        or turn_instance_id is None
        or heartbeat_receipt_pending_action_todo_id(preflight.receipt)
        != selection.requested_todo_id
    ):
        return None
    # Re-enter the real guard; changed quota, gates, leases and capabilities
    # must be read again. No explicit selection on this hop means no recursion.
    reentry = argparse.Namespace(**vars(args))
    reentry.todo_id = None
    reentry.begin_turn = False
    reentry.turn_instance_id = turn_instance_id
    return reentry


def commit_requested_action_selection(
    payload: Mapping[str, object],
    *,
    requested_todo_id: str | None,
) -> None:
    """Project the exact requested selection only after receipt reconciliation."""

    selected_todo = payload.get("selected_todo")
    if (
        requested_todo_id
        and isinstance(selected_todo, dict)
        and normalize_todo_id(selected_todo.get("todo_id")) == requested_todo_id
    ):
        selected_todo["selection_binding"] = "heartbeat_receipt"


def _retain_deferred_action_selection(
    payload: Mapping[str, object],
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    turn_instance_id: str | None,
    selection: RequestedQuotaActionSelection,
    goal_ref: Mapping[str, object] | None,
) -> tuple[dict[str, object] | None, str, bool]:
    """Append a deferred explicit choice without granting settlement authority."""

    qualification = payload.get("action_selection_qualification")
    if (
        not turn_instance_id
        or selection.receipt is None
        or selection.requested_todo_id is None
        or not isinstance(qualification, Mapping)
        or qualification.get("state") != "deferred"
    ):
        return selection.receipt, "replayed", False
    retained, appended = retain_pending_heartbeat_action_selection(
        runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        turn_instance_id=turn_instance_id,
        todo_id=selection.requested_todo_id,
        reason=str(qualification.get("reason") or "current_delivery_gate"),
        goal_ref=goal_ref,
    )
    return retained, "selection_retained" if appended else "replayed", appended
