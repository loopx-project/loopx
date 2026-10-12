"""CLI rollout helpers for heartbeat settlement identity and receipt wiring."""

from __future__ import annotations
from .effective_action import EffectiveAction

import argparse
from collections.abc import Mapping
from pathlib import Path

from ..todos.contract import (
    normalize_todo_id,
    normalize_todo_replan_obligation_id,
)
from ..work_items.autonomous_replan_obligation import (
    replan_obligation_id_from_packet,
)
from .effect_program import SettlementIdentity
from .error_codes import HeartbeatReceiptIdentityConflictError
from .heartbeat_receipt import (
    heartbeat_receipt_view,
    upgrade_identityless_heartbeat_receipt,
    requalify_bound_heartbeat_receipt,
)
from .settlement import (
    attach_settlement_progress,
    read_heartbeat_settlement,
    settlement_result_payload,
)
from .settlement_workspace_causality import (
    build_delivery_workspace_causality,
    delivery_workspace_causality_event_fields,
)


def reconcile_existing_heartbeat_receipt(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    turn_instance_id: str,
    existing: dict[str, object],
    goal_ref: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], str, bool, str]:
    """Bind or qualify same-Turn work without replacing its settlement identity."""

    receipt = existing
    receipt_status = "replayed"
    receipt_appended = False
    rollout_todo_id = quota_rollout_todo_id(payload, args)
    rollout_replan_obligation_id = quota_rollout_replan_obligation_id(payload, args)
    if rollout_todo_id or rollout_replan_obligation_id:
        existing_details_value = receipt.get("details")
        existing_details = (
            existing_details_value
            if isinstance(existing_details_value, Mapping)
            else {}
        )
        existing_todo_id = normalize_todo_id(existing_details.get("todo_id"))
        existing_replan_obligation_id = normalize_todo_replan_obligation_id(
            existing_details.get("replan_obligation_id")
        )
        existing_effect_id = str(
            existing_details.get("settlement_effect_id") or ""
        ).strip()
        expected_effect_id = SettlementIdentity(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            todo_id=rollout_todo_id or None,
            turn_instance_id=turn_instance_id,
            replan_obligation_id=rollout_replan_obligation_id,
        ).effect_id
        if (existing_todo_id or existing_replan_obligation_id) and existing_effect_id:
            # A fully settled Turn owns its immutable receipt regardless of a
            # later explicit CLI choice.  The live decision has already read
            # the exact settlement and projected ``heartbeat_settled_skip``;
            # action-selection preflight distinguishes a qualified current
            # Todo from a conflicting explicit choice before this replay path.
            settled_replay = (
                payload.get("effective_action")
                == EffectiveAction.HEARTBEAT_SETTLED_SKIP.value
            )
            if not settled_replay:
                requested_todo_id = normalize_todo_id(args.todo_id)
                if requested_todo_id and requested_todo_id != existing_todo_id:
                    raise HeartbeatReceiptIdentityConflictError(
                        "heartbeat receipt settlement identity conflicts with the "
                        "current selected Todo: explicitly requested Todo differs"
                    )
                requested_replan_obligation_id = normalize_todo_replan_obligation_id(
                    getattr(args, "replan_obligation_id", None)
                )
                if (
                    requested_replan_obligation_id
                    and requested_replan_obligation_id
                    != existing_replan_obligation_id
                ):
                    raise HeartbeatReceiptIdentityConflictError(
                        "heartbeat receipt settlement identity conflicts with the "
                        "explicitly requested autonomous replan obligation"
                    )
                if (
                    existing_todo_id != rollout_todo_id
                    or existing_replan_obligation_id != rollout_replan_obligation_id
                    or existing_effect_id != expected_effect_id
                ):
                    raise HeartbeatReceiptIdentityConflictError(
                        "heartbeat receipt settlement identity conflicts with the "
                        "current settlement binding: receipt="
                        f"{existing_todo_id or existing_replan_obligation_id}, "
                        "current="
                        f"{rollout_todo_id or rollout_replan_obligation_id}"
                    )
                rollout_details = quota_rollout_details(payload, args,
                    todo_id=rollout_todo_id, replan_obligation_id=rollout_replan_obligation_id)
                rollout_details["settlement_effect_id"] = expected_effect_id
                receipt, qualified = requalify_bound_heartbeat_receipt(
                    runtime_root, goal_id=args.goal_id, agent_id=args.agent_id,
                    turn_instance_id=turn_instance_id, todo_id=rollout_todo_id,
                    replan_obligation_id=rollout_replan_obligation_id,
                    status=str(payload.get("effective_action") or payload.get("decision") or "should-run"),
                    details=rollout_details,
                    registry_path=Path(str(payload["registry"])) if payload.get("registry") else None,
                    goal_ref=goal_ref,
                )
                if qualified:
                    receipt_status, receipt_appended = "upgraded", True
        else:
            rollout_details = quota_rollout_details(
                payload,
                args,
                todo_id=rollout_todo_id,
                replan_obligation_id=rollout_replan_obligation_id,
            )
            receipt, upgraded = upgrade_identityless_heartbeat_receipt(
                runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                turn_instance_id=turn_instance_id,
                todo_id=rollout_todo_id,
                replan_obligation_id=rollout_replan_obligation_id,
                settlement_effect_id=expected_effect_id,
                status=str(
                    payload.get("effective_action")
                    or payload.get("decision")
                    or "should-run"
                ),
                summary=f"heartbeat quota receipt upgraded for turn={turn_instance_id}",
                details=rollout_details,
                goal_ref=goal_ref,
            )
            if upgraded:
                receipt_status = "upgraded"
                receipt_appended = True
    details_value = receipt.get("details")
    details: Mapping[str, object] = (
        details_value if isinstance(details_value, Mapping) else {}
    )
    stall_observation = str(details.get("stall_observation") or "not_applicable")
    return receipt, receipt_status, receipt_appended, stall_observation


def reconcile_existing_heartbeat_receipt_for_turn(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    turn_instance_id: str,
    existing: dict[str, object],
    goal_ref: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], str, bool, str, bool]:
    """Reconcile an existing receipt and report whether the turn is receipt-ready."""

    receipt, status, appended, stall_observation = reconcile_existing_heartbeat_receipt(
        payload,
        args,
        runtime_root=runtime_root,
        turn_instance_id=turn_instance_id,
        existing=existing,
        goal_ref=goal_ref,
    )
    return receipt, status, appended, stall_observation, True


def render_existing_heartbeat_receipt_payload(
    payload: dict[str, object],
    *,
    receipt: dict[str, object],
    turn_instance_id: str,
    status: str,
    appended: bool,
) -> None:
    """Render the committed existing heartbeat receipt into a quota payload."""

    payload["heartbeat_receipt"] = heartbeat_receipt_view(
        receipt,
        turn_instance_id=turn_instance_id,
        status=status,
    )
    payload["rollout_event"] = {
        "schema_version": receipt.get("schema_version"),
        "event_id": receipt.get("event_id"),
        "event_kind": receipt.get("event_kind"),
        "recorded_at": receipt.get("recorded_at"),
        "status": receipt.get("status"),
        "appended": appended,
    }


def quota_rollout_todo_id(
    payload: Mapping[str, object],
    args: argparse.Namespace,
) -> str | None:
    todo_id, _ = quota_rollout_settlement_binding(payload, args)
    return todo_id


def quota_rollout_settlement_binding(
    payload: Mapping[str, object],
    args: argparse.Namespace,
) -> tuple[str | None, str | None]:
    """Resolve one rollout binding without mixing authority levels.

    Explicit settlement arguments and the typed plan are causal identities.
    The current selected Todo is a later projection, while a replan action
    packet is only a diagnostic fallback when no concrete Todo is selected.
    """

    if payload.get("effective_action") == EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value:
        # This Turn only repairs the preceding Turn's closeout.  A concurrently
        # projected Todo or autonomous replan belongs to the post-recovery
        # decision and must not become this receipt's settlement identity.
        # Keeping the recovery receipt identity-less lets a verified same-Turn
        # reentry bind the next independent action without rewriting history.
        return None, None

    explicit_todo_id = normalize_todo_id(getattr(args, "todo_id", None))
    explicit_replan_obligation_id = normalize_todo_replan_obligation_id(
        getattr(args, "replan_obligation_id", None)
    )
    if explicit_todo_id and explicit_replan_obligation_id:
        raise ValueError(
            "quota rollout cannot bind both todo_id and replan_obligation_id"
        )
    if explicit_todo_id or explicit_replan_obligation_id:
        return explicit_todo_id, explicit_replan_obligation_id

    action_portfolio_value = payload.get("action_portfolio")
    action_portfolio: Mapping[str, object] = (
        action_portfolio_value
        if isinstance(action_portfolio_value, Mapping)
        else {}
    )
    selection_policy_value = action_portfolio.get("selection_policy")
    selection_policy: Mapping[str, object] = (
        selection_policy_value
        if isinstance(selection_policy_value, Mapping)
        else {}
    )
    if selection_policy.get("requires_explicit_turn_binding") is True:
        # A portfolio guard without an explicit selection is intentionally
        # identity-less, so recommendation order cannot become hidden
        # settlement authority.
        return None, None

    interaction = (
        payload.get("interaction_contract")
        if isinstance(payload.get("interaction_contract"), Mapping)
        else {}
    )
    cli_channel = (
        interaction.get("cli_channel")
        if isinstance(interaction.get("cli_channel"), Mapping)
        else {}
    )
    settlement_plan = (
        cli_channel.get("settlement_plan")
        if isinstance(cli_channel.get("settlement_plan"), Mapping)
        else {}
    )
    identity = (
        settlement_plan.get("identity")
        if isinstance(settlement_plan.get("identity"), Mapping)
        else {}
    )
    planned_todo_id = normalize_todo_id(identity.get("todo_id"))
    planned_replan_obligation_id = normalize_todo_replan_obligation_id(
        identity.get("replan_obligation_id")
    )
    if planned_todo_id and planned_replan_obligation_id:
        raise ValueError(
            "quota settlement plan cannot bind both todo_id and replan_obligation_id"
        )
    if planned_todo_id or planned_replan_obligation_id:
        return planned_todo_id, planned_replan_obligation_id

    selected_todo = (
        payload.get("selected_todo")
        if isinstance(payload.get("selected_todo"), Mapping)
        else {}
    )
    selected_todo_id = normalize_todo_id(payload.get("todo_id")) or normalize_todo_id(
        selected_todo.get("todo_id")
    )
    if selected_todo_id:
        return selected_todo_id, None

    replan_packet = (
        payload.get("replan_action_packet")
        if isinstance(payload.get("replan_action_packet"), Mapping)
        else {}
    )
    return None, normalize_todo_replan_obligation_id(replan_packet.get("obligation_id"))


def quota_rollout_replan_obligation_id(
    payload: Mapping[str, object],
    args: argparse.Namespace,
) -> str | None:
    _, replan_obligation_id = quota_rollout_settlement_binding(payload, args)
    return replan_obligation_id


def quota_rollout_details(
    payload: Mapping[str, object],
    args: argparse.Namespace,
    *,
    todo_id: str | None,
    replan_obligation_id: str | None = None,
) -> dict[str, object]:
    successor_todo_ids = (
        payload.get("successor_todo_ids")
        if isinstance(payload.get("successor_todo_ids"), list)
        else []
    )
    selected_todo = (
        payload.get("selected_todo")
        if isinstance(payload.get("selected_todo"), Mapping)
        else None
    )
    semantic_replan_obligation_id = replan_obligation_id_from_packet(
        payload.get("replan_action_packet")
    )
    retained_selection = (
        payload.get("retained_action_selection")
        if isinstance(payload.get("retained_action_selection"), Mapping)
        else {}
    )
    retained_disposition = str(retained_selection.get("disposition") or "")
    workspace_causality = build_delivery_workspace_causality(selected_todo)
    interaction = (
        payload.get("interaction_contract")
        if isinstance(payload.get("interaction_contract"), Mapping)
        else {}
    )
    agent_channel = (
        interaction.get("agent_channel")
        if isinstance(interaction.get("agent_channel"), Mapping)
        else {}
    )
    closeout_required = bool(
        todo_id
        and payload.get("ok") is True
        and payload.get("should_run") is True
        and agent_channel.get("must_attempt") is True
        and agent_channel.get("delivery_allowed") is True
        and agent_channel.get("quiet_noop_allowed") is False
    )
    details: dict[str, object] = {
        "command": "quota",
        "quota_command": args.quota_command,
        "ok": bool(payload.get("ok")),
        "should_run": bool(payload.get("should_run")),
        "appended": bool(payload.get("appended")),
        "slots": payload.get("slots") or "",
        "source": payload.get("source") or "",
        "todo_id": todo_id or "",
        "replan_obligation_id": replan_obligation_id or "",
        "semantic_replan_obligation_id": semantic_replan_obligation_id or "",
        "target_key": payload.get("target_key") or "",
        "successor_todo_ids": ",".join(
            str(successor_id)
            for successor_id in successor_todo_ids
            if str(successor_id).strip()
        ),
        "applied_rrule": payload.get("applied_rrule") or "",
        "settlement_effect_id": (
            payload.get("settlement_identity", {}).get("effect_id")
            if isinstance(payload.get("settlement_identity"), Mapping)
            else ""
        ),
        "interaction_mode": interaction.get("mode") or "",
        "must_attempt_work": bool(agent_channel.get("must_attempt")),
        "delivery_allowed": bool(agent_channel.get("delivery_allowed")),
        "quiet_noop_allowed": bool(agent_channel.get("quiet_noop_allowed")),
        "closeout_required": closeout_required,
    }
    cli_channel = interaction.get("cli_channel")
    if isinstance(cli_channel, Mapping) and cli_channel.get("quota_spend_source"):
        details["quota_spend_source"] = cli_channel["quota_spend_source"]
    retained_todo_id = normalize_todo_id(retained_selection.get("retained_todo_id"))
    if retained_todo_id:
        retained_bound = retained_disposition == "preserve_retained_todo"
        details.update(
            {
                "pending_action_selection_todo_id": retained_todo_id,
                "pending_action_selection_state": (
                    "bound" if retained_bound else "deferred_to_fresh_turn"
                ),
                "pending_action_selection_reason": (
                    "explicit_choice_preserved"
                    if retained_bound
                    else "autonomous_replan_preemption"
                ),
                "pending_action_selection_settlement_bound": retained_bound,
            }
        )
    if workspace_causality:
        details.update(delivery_workspace_causality_event_fields(workspace_causality))
    return details


def attach_spend_settlement_result(
    payload: dict[str, object],
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    todo_id: str | None,
    turn_instance_id: str,
    replan_obligation_id: str | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> None:
    registry_path = (
        Path(str(payload["registry"])) if payload.get("registry") else None
    )
    readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=replan_obligation_id,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    if readback is None:
        raise RuntimeError("exact settlement readback unexpectedly returned not-found")
    attach_settlement_progress(
        payload, readback, runtime_root=runtime_root,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    identity = readback.identity.value
    if identity is None:
        settlement_result = readback.identity
    else:
        settlement_result = readback.settlement
    payload["settlement_result"] = settlement_result_payload(settlement_result)
    if settlement_result.failure is not None:
        payload["ok"] = False
        payload["receipt_repair_required"] = True
        payload["reason"] = settlement_result.failure.reason
    elif payload.get("receipt_repair_required"):
        payload["receipt_repair_required"] = False
        payload["receipt_repaired"] = True
