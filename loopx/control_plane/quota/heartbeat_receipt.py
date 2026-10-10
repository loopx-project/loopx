from __future__ import annotations
from .effective_action import EffectiveAction

from collections.abc import Mapping
from pathlib import Path

from ...file_lock import exclusive_cross_runtime_file_lock
from ...rollout_event_log import (
    _append_rollout_event_line,
    build_rollout_event,
    load_rollout_events,
    rollout_event_log_path,
)
from ..todos.contract import normalize_todo_id, normalize_todo_replan_obligation_id
from .effect_program import SettlementBindingKind, SettlementIdentity
from .error_codes import HeartbeatReceiptIdentityConflictError
from .settlement_workspace_causality import (
    delivery_workspace_causality_from_event_details,
)

HEARTBEAT_RECEIPT_SCHEMA_VERSION = "heartbeat_quota_receipt_v0"


def _heartbeat_receipt_events(
    events: list[dict[str, object]],
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    goal_ref: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    from ..goals.source_session_registry_state import exact_goal_ref

    expected_goal_ref = (
        exact_goal_ref(
            str(goal_ref.get("goal_id") or ""),
            str(goal_ref.get("goal_instance_id") or ""),
        )
        if goal_ref is not None
        else None
    )
    if expected_goal_ref is not None and expected_goal_ref["goal_id"] != goal_id:
        raise ValueError("heartbeat receipt GoalRef does not match goal_id")
    return [
        event
        for event in events
        if event.get("event_kind") == "quota_should_run"
        and str(event.get("goal_id") or "") == goal_id
        and str(event.get("agent_id") or "") == agent_id
        and str(event.get("run_id") or "") == turn_instance_id
        and (
            event.get("goal_ref") == expected_goal_ref
            if expected_goal_ref is not None
            else "goal_ref" not in event
        )
    ]


def _receipt_settlement_identity(
    event: Mapping[str, object],
) -> tuple[SettlementBindingKind, str, str] | None:
    details_value = event.get("details")
    details: Mapping[str, object] = (
        details_value if isinstance(details_value, Mapping) else {}
    )
    todo_id = str(details.get("todo_id") or "").strip()
    replan_obligation_id = normalize_todo_replan_obligation_id(
        details.get("replan_obligation_id")
    )
    effect_id = str(details.get("settlement_effect_id") or "").strip()
    if todo_id and replan_obligation_id:
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt has conflicting Todo and autonomous replan bindings"
        )
    if effect_id and not todo_id and not replan_obligation_id:
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt has an effect identity without a Todo or "
            "autonomous replan binding; refuse to infer or upgrade it"
        )
    if not todo_id and not replan_obligation_id:
        return None
    identity = SettlementIdentity(
        goal_id=str(event.get("goal_id") or "").strip(),
        agent_id=str(event.get("agent_id") or "").strip(),
        todo_id=todo_id or None,
        turn_instance_id=str(event.get("run_id") or "").strip(),
        replan_obligation_id=replan_obligation_id,
    )
    if not effect_id:
        effect_id = identity.effect_id
    return identity.binding_kind, identity.binding_id, effect_id


def heartbeat_receipt_settlement_todo_id(
    event: Mapping[str, object],
) -> str | None:
    """Return the Todo already bound to a committed heartbeat receipt."""

    identity = _receipt_settlement_identity(event)
    if identity is None or identity[0] is not SettlementBindingKind.TODO:
        return None
    return identity[1]


def heartbeat_receipt_settlement_replan_obligation_id(
    event: Mapping[str, object],
) -> str | None:
    """Return the autonomous replan bound to a committed heartbeat receipt."""

    identity = _receipt_settlement_identity(event)
    if identity is None or identity[0] is not SettlementBindingKind.AUTONOMOUS_REPLAN:
        return None
    return identity[1]


def heartbeat_receipt_semantic_replan_obligation_id(
    event: Mapping[str, object] | None,
) -> str | None:
    """Return the semantic replan target selected by the original guard.

    This is deliberately separate from the settlement identity: a replan Turn
    may settle through a concrete Todo while that same guard requires a typed
    semantic delta for an autonomous replan obligation.
    """

    if not isinstance(event, Mapping):
        return None
    details = event.get("details")
    if not isinstance(details, Mapping):
        return None
    return normalize_todo_replan_obligation_id(
        details.get("semantic_replan_obligation_id")
    )


def heartbeat_receipt_pending_action_todo_id(
    event: Mapping[str, object] | None,
) -> str | None:
    """Return an explicit Todo choice retained before settlement binding."""

    if not isinstance(event, Mapping):
        return None
    details = event.get("details")
    if not isinstance(details, Mapping):
        return None
    return normalize_todo_id(details.get("pending_action_selection_todo_id"))


def _effective_heartbeat_receipt(
    events: list[dict[str, object]],
) -> dict[str, object] | None:
    if not events:
        return None
    identities: dict[tuple[SettlementBindingKind, str, str], dict[str, object]] = {}
    for event in events:
        identity = _receipt_settlement_identity(event)
        if identity is not None:
            identities[identity] = event
    if len(identities) > 1:
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt has conflicting settlement identities for the "
            "same goal, agent, and turn"
        )
    if identities:
        return next(iter(identities.values()))
    return events[-1]


def find_heartbeat_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    events = load_rollout_events(rollout_event_log_path(runtime_root, goal_id))
    return _effective_heartbeat_receipt(
        _heartbeat_receipt_events(
            events,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
    )


def ensure_turn_heartbeat_settlement_receipt(
    runtime_root: Path,
    identity: SettlementIdentity,
    *,
    semantic_replan_guard_scoped: bool,
    semantic_replan_obligation_id: str | None,
    semantic_replan_capability_guard: Mapping[str, object] | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Idempotently bind a Turn-created quota guard to its settlement identity.

    Current Turn envelopes always carry ``replan_action_packet`` even when no
    obligation was selected.  Persist that explicit empty selection so an
    obligation opened while the host is running cannot retroactively reject
    the admitted Turn.  Old envelopes without the field stay legacy-unscoped.
    """

    normalized_semantic_replan_obligation_id = (
        normalize_todo_replan_obligation_id(semantic_replan_obligation_id)
    )
    if (
        semantic_replan_obligation_id is not None
        and normalized_semantic_replan_obligation_id is None
    ):
        raise ValueError("Turn semantic replan obligation id is malformed")

    log_path = rollout_event_log_path(runtime_root, identity.goal_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_cross_runtime_file_lock(
        log_path, operation="bind_heartbeat_settlement_receipt"
    ):
        events = load_rollout_events(log_path)
        matching = _heartbeat_receipt_events(
            events,
            goal_id=identity.goal_id,
            agent_id=identity.agent_id,
            turn_instance_id=identity.turn_instance_id,
            goal_ref=goal_ref,
        )
        effective = _effective_heartbeat_receipt(matching)
        expected = (
            identity.binding_kind,
            identity.binding_id,
            identity.effect_id,
        )
        if effective is not None:
            observed = _receipt_settlement_identity(effective)
            if observed is not None:
                if observed != expected:
                    raise HeartbeatReceiptIdentityConflictError(
                        "Turn heartbeat receipt belongs to another settlement identity"
                    )
                effective_details = effective.get("details")
                effective_details = (
                    effective_details
                    if isinstance(effective_details, Mapping)
                    else {}
                )
                if not semantic_replan_guard_scoped:
                    return effective
                if "semantic_replan_obligation_id" in effective_details:
                    raw_existing_guard = effective_details.get(
                        "semantic_replan_obligation_id"
                    )
                    existing_guard = normalize_todo_replan_obligation_id(
                        raw_existing_guard
                    )
                    if (
                        str(raw_existing_guard or "").strip()
                        and existing_guard is None
                    ):
                        raise HeartbeatReceiptIdentityConflictError(
                            "Turn heartbeat receipt has a malformed semantic replan guard"
                        )
                    if existing_guard != normalized_semantic_replan_obligation_id:
                        raise HeartbeatReceiptIdentityConflictError(
                            "Turn heartbeat receipt belongs to another semantic replan guard"
                        )
                    if semantic_replan_capability_guard is not None and any(
                        effective_details.get(source) != semantic_replan_capability_guard[field]
                        for source, field in (
                            ("semantic_replan_capability_id", "capability_id"),
                            ("semantic_replan_gap_id", "gap_id"),
                            ("semantic_replan_frontier_revision", "frontier_revision"),
                        )
                    ):
                        raise HeartbeatReceiptIdentityConflictError(
                            "Turn heartbeat receipt belongs to another capability guard"
                        )
                    return effective

        details = {
            "turn_instance_id": identity.turn_instance_id,
            "todo_id": str(identity.todo_id or ""),
            "replan_obligation_id": str(identity.replan_obligation_id or ""),
            "settlement_effect_id": identity.effect_id,
            "stall_observation": "not_applicable",
            "source": "loopx_turn_run_once",
        }
        if semantic_replan_guard_scoped:
            details["semantic_replan_obligation_id"] = (
                normalized_semantic_replan_obligation_id or ""
            )
            if semantic_replan_capability_guard is not None:
                details["semantic_replan_capability_id"] = semantic_replan_capability_guard["capability_id"]
                details["semantic_replan_gap_id"] = semantic_replan_capability_guard["gap_id"]
                details["semantic_replan_frontier_revision"] = semantic_replan_capability_guard["frontier_revision"]
        source_event_id = (
            str(effective.get("event_id") or "").strip()
            if effective is not None
            else ""
        )
        receipt = build_rollout_event(
            goal_id=identity.goal_id,
            event_kind="quota_should_run",
            goal_ref=goal_ref,
            agent_id=identity.agent_id,
            todo_id=identity.todo_id,
            run_id=identity.turn_instance_id,
            status="turn_run_once",
            summary=f"Turn settlement guard prepared for turn={identity.turn_instance_id}",
            source_event_id=source_event_id or None,
            caused_by=source_event_id or None,
            details=details,
        )
        _append_rollout_event_line(log_path, receipt)
        return receipt


def retain_pending_heartbeat_action_selection(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    todo_id: str,
    reason: str,
    goal_ref: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], bool]:
    """Append an identity-less revision retaining one explicit Todo choice.

    The choice is not settlement authority.  It only fences a later no-argument
    reentry from silently replacing the agent's explicit selection with a new
    recommendation.  A later explicit selection may replace it until the Turn
    acquires its single settlement identity.
    """

    normalized_todo_id = normalize_todo_id(todo_id)
    if normalized_todo_id is None:
        raise HeartbeatReceiptIdentityConflictError(
            "pending heartbeat action selection requires a legal Todo id"
        )
    normalized_reason = str(reason or "current_delivery_gate").strip()
    log_path = rollout_event_log_path(runtime_root, goal_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_cross_runtime_file_lock(
        log_path, operation="retain_heartbeat_action_selection"
    ):
        events = load_rollout_events(log_path)
        matching = _heartbeat_receipt_events(
            events,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
        effective = _effective_heartbeat_receipt(matching)
        if effective is None:
            raise ValueError(
                "identity-less heartbeat receipt is missing; rerun the original guard"
            )
        if _receipt_settlement_identity(effective) is not None:
            raise HeartbeatReceiptIdentityConflictError(
                "a settled heartbeat Turn cannot retain a different pending selection"
            )
        details_value = effective.get("details")
        details = (
            dict(details_value) if isinstance(details_value, Mapping) else {}
        )
        if (
            normalize_todo_id(details.get("pending_action_selection_todo_id"))
            == normalized_todo_id
            and str(details.get("pending_action_selection_reason") or "").strip()
            == normalized_reason
        ):
            return effective, False
        details.update(
            {
                "pending_action_selection_todo_id": normalized_todo_id,
                "pending_action_selection_state": "deferred",
                "pending_action_selection_reason": normalized_reason,
                "settlement_effect_id": "",
                "todo_id": "",
                "replan_obligation_id": "",
            }
        )
        source_event_id = str(effective.get("event_id") or "").strip() or None
        retained = build_rollout_event(
            goal_id=goal_id,
            event_kind="quota_should_run",
            goal_ref=goal_ref,
            agent_id=agent_id,
            run_id=turn_instance_id,
            status="action_selection_deferred",
            summary=(
                "heartbeat explicit action selection retained without settlement "
                f"binding for turn={turn_instance_id}"
            ),
            source_event_id=source_event_id,
            caused_by=source_event_id,
            details=details,
        )
        _append_rollout_event_line(log_path, retained)
        return retained, True


def upgrade_identityless_heartbeat_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    settlement_effect_id: str,
    status: str,
    summary: str,
    details: Mapping[str, object],
    goal_ref: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], bool]:
    """Append one settlement-bound receipt after an identity-less same-turn guard.

    The correction is append-only and serialized with the rollout log lock. A
    matching correction replays, a legacy Todo-only receipt gains its derived
    effect id, and effect-only or conflicting identities fail closed.
    """

    normalized_todo_id = str(todo_id or "").strip()
    normalized_replan_obligation_id = normalize_todo_replan_obligation_id(
        replan_obligation_id
    )
    normalized_effect_id = str(settlement_effect_id).strip()
    if bool(normalized_todo_id) == bool(normalized_replan_obligation_id):
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt upgrade requires exactly one deterministic "
            "settlement binding"
        )
    try:
        expected_identity_value = SettlementIdentity(
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=normalized_todo_id or None,
            turn_instance_id=turn_instance_id,
            replan_obligation_id=normalized_replan_obligation_id,
        )
    except ValueError as exc:
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt upgrade requires exactly one deterministic "
            "settlement binding"
        ) from exc
    expected_effect_id = expected_identity_value.effect_id
    if normalized_effect_id != expected_effect_id:
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt upgrade requires the deterministic settlement identity"
        )

    log_path = rollout_event_log_path(runtime_root, goal_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_cross_runtime_file_lock(
        log_path, operation="upgrade_heartbeat_receipt_identity"
    ):
        events = load_rollout_events(log_path)
        matching = _heartbeat_receipt_events(
            events,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
        effective = _effective_heartbeat_receipt(matching)
        if effective is None:
            raise ValueError(
                "identity-less heartbeat receipt is missing; rerun the original guard"
            )
        existing_identity = _receipt_settlement_identity(effective)
        expected_identity = (
            expected_identity_value.binding_kind,
            expected_identity_value.binding_id,
            normalized_effect_id,
        )
        if existing_identity is not None:
            if existing_identity != expected_identity:
                if (
                    existing_identity[0] is SettlementBindingKind.TODO
                    and expected_identity[0] is SettlementBindingKind.TODO
                ):
                    conflict_target = "current selected Todo"
                elif (
                    existing_identity[0] is SettlementBindingKind.AUTONOMOUS_REPLAN
                    and expected_identity[0] is SettlementBindingKind.AUTONOMOUS_REPLAN
                ):
                    conflict_target = "current autonomous replan obligation"
                else:
                    conflict_target = "current settlement binding"
                raise HeartbeatReceiptIdentityConflictError(
                    "heartbeat receipt settlement identity conflicts with the "
                    f"{conflict_target}"
                )
            existing_details_value = effective.get("details")
            existing_details = (
                existing_details_value
                if isinstance(existing_details_value, Mapping)
                else {}
            )
            if str(existing_details.get("settlement_effect_id") or "").strip():
                return effective, False

        corrected_details = dict(details)
        corrected_details.update(
            {
                "turn_instance_id": turn_instance_id,
                "todo_id": normalized_todo_id,
                "replan_obligation_id": normalized_replan_obligation_id or "",
                "settlement_effect_id": normalized_effect_id,
                "settlement_receipt_revision": "identity_upgrade",
            }
        )
        source_event_id = str(effective.get("event_id") or "").strip() or None
        corrected = build_rollout_event(
            goal_id=goal_id,
            event_kind="quota_should_run",
            goal_ref=goal_ref,
            agent_id=agent_id,
            todo_id=normalized_todo_id or None,
            run_id=turn_instance_id,
            status=status,
            summary=summary,
            source_event_id=source_event_id,
            caused_by=source_event_id,
            details=corrected_details,
        )
        _append_rollout_event_line(log_path, corrected)
        return corrected, True


def requalify_bound_heartbeat_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    todo_id: str | None,
    replan_obligation_id: str | None,
    status: str,
    details: dict[str, object],
    registry_path: Path | None,
    goal_ref: Mapping[str, object] | None,
) -> tuple[dict[str, object], bool]:
    """Transport current guard facts; TS owns same-binding work qualification."""
    from .settlement import read_heartbeat_settlement

    log_path = rollout_event_log_path(runtime_root, goal_id)
    with exclusive_cross_runtime_file_lock(log_path, operation="requalify_heartbeat_work"):
        original = _effective_heartbeat_receipt(_heartbeat_receipt_events(
            load_rollout_events(log_path), goal_id=goal_id, agent_id=agent_id,
            turn_instance_id=turn_instance_id, goal_ref=goal_ref,
        ))
        readback = read_heartbeat_settlement(
            runtime_root, goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
            turn_instance_id=turn_instance_id, replan_obligation_id=replan_obligation_id,
            registry_path=registry_path, goal_ref=goal_ref, heartbeat_reentry_guard=details,
        )
        if original is None or readback is None or readback.identity.value is None:
            raise ValueError("work requalification requires the original admitted settlement identity")
        if readback.heartbeat_receipt is None or readback.heartbeat_receipt.get("event_id") != original.get("event_id"):
            raise ValueError("work requalification receipt readback changed")
        qualification = readback.heartbeat_reentry_qualification
        if not isinstance(qualification, Mapping) or not isinstance(qualification.get("append"), bool):
            raise RuntimeError("TypeScript heartbeat work qualification shape mismatch")
        if not qualification["append"]:
            return original, False
        qualified_details = qualification.get("details")
        if not isinstance(qualified_details, Mapping):
            raise RuntimeError("TypeScript heartbeat work qualification details missing")
        event_id = str(original.get("event_id") or "")
        corrected = build_rollout_event(
            goal_id=goal_id, goal_ref=goal_ref, event_kind="quota_should_run",
            agent_id=agent_id, todo_id=todo_id, run_id=turn_instance_id, status=status,
            summary="same-Turn work admission requalified without rebinding",
            source_event_id=event_id, caused_by=event_id, details=dict(qualified_details),
        )
        _append_rollout_event_line(log_path, corrected)
        return corrected, True


def heartbeat_receipt_view(
    event: Mapping[str, object],
    *,
    turn_instance_id: str,
    status: str,
) -> dict[str, object]:
    details_value = event.get("details")
    details: Mapping[str, object] = (
        details_value if isinstance(details_value, Mapping) else {}
    )
    receipt: dict[str, object] = {
        "schema_version": HEARTBEAT_RECEIPT_SCHEMA_VERSION,
        "turn_instance_id": turn_instance_id,
        "status": status,
        "stall_observation": str(details.get("stall_observation") or "not_applicable"),
        "event_id": event.get("event_id"),
        "recorded_at": event.get("recorded_at"),
    }
    if details.get("closeout_required") is True:
        receipt["closeout_required"] = True
    todo_id = str(details.get("todo_id") or "").strip()
    replan_obligation_id = normalize_todo_replan_obligation_id(
        details.get("replan_obligation_id")
    )
    effect_id = str(details.get("settlement_effect_id") or "").strip()
    if (todo_id or replan_obligation_id) and effect_id:
        identity = SettlementIdentity(
            goal_id=str(event.get("goal_id") or ""),
            agent_id=str(event.get("agent_id") or ""),
            todo_id=todo_id or None,
            turn_instance_id=turn_instance_id,
            replan_obligation_id=replan_obligation_id,
        )
        receipt["settlement_identity"] = identity.as_dict()
        workspace_causality = delivery_workspace_causality_from_event_details(
            details,
            todo_id=todo_id or None,
        )
        if workspace_causality:
            receipt["delivery_workspace_causality"] = workspace_causality
    else:
        # The documented wake order runs the guard before a work item is chosen,
        # so a turn-scoped receipt can legitimately commit with no settlement
        # binding. Saying so here is what keeps that receipt from reading as a
        # finished guard: the caller otherwise discovers the debt only when the
        # closeout cannot settle the turn and the work stays unaccounted.
        receipt["settlement_binding_owed"] = True
    semantic_replan_obligation_id = (
        heartbeat_receipt_semantic_replan_obligation_id(event)
    )
    if semantic_replan_obligation_id:
        receipt["semantic_replan_obligation_id"] = (
            semantic_replan_obligation_id
        )
    if details.get("semantic_replan_capability_id"):
        receipt["semantic_replan_capability_guard"] = {
            "schema_version": "semantic_replan_capability_guard_v0",
            "capability_id": details["semantic_replan_capability_id"],
            "gap_id": details.get("semantic_replan_gap_id"),
            "frontier_revision": details.get("semantic_replan_frontier_revision"),
        }
    pending_action_todo_id = heartbeat_receipt_pending_action_todo_id(event)
    if pending_action_todo_id:
        receipt["pending_action_selection"] = {
            "todo_id": pending_action_todo_id,
            "state": str(
                details.get("pending_action_selection_state") or "deferred"
            ),
            "reason": str(
                details.get("pending_action_selection_reason")
                or "current_delivery_gate"
            ),
            "settlement_bound": (
                details.get("pending_action_selection_settlement_bound") is True
            ),
        }
    return receipt


def attach_uncommitted_heartbeat_receipt(
    payload: dict[str, object],
    *,
    turn_instance_id: str,
) -> None:
    """Expose an accurate non-durable receipt for an incomplete preflight."""

    payload["heartbeat_receipt"] = {
        "schema_version": HEARTBEAT_RECEIPT_SCHEMA_VERSION,
        "turn_instance_id": turn_instance_id,
        "status": "not_committed",
        "stall_observation": "not_evaluated",
        "reason_code": str(
            payload.get("error_code") or "quota_preflight_incomplete"
        ),
    }


def fail_heartbeat_receipt(
    payload: dict[str, object],
    *,
    turn_instance_id: str,
    stall_observation: str,
    reason: str,
) -> None:
    payload.update(
        {
            "ok": False,
            "decision": "skip",
            "should_run": False,
            "effective_action": EffectiveAction.HEARTBEAT_RECEIPT_WRITE_FAILED.value,
            "state": "blocked_health",
            "waiting_on": "codex",
            "reason": reason,
            "recommended_action": (
                "retry quota should-run with the same --turn-instance-id after "
                "repairing heartbeat receipt writeback"
            ),
            "heartbeat_receipt": {
                "schema_version": HEARTBEAT_RECEIPT_SCHEMA_VERSION,
                "turn_instance_id": turn_instance_id,
                "status": "write_failed",
                "stall_observation": stall_observation,
            },
        }
    )
