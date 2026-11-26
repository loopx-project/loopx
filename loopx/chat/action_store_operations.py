"""Operation envelope confirmation, agent handoff, and delivery persistence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
import uuid

from .action_store_common import (
    OPERATION_ENVELOPE_SCHEMA_VERSION,
    OPERATION_LIFECYCLE_STATES,
    ActionConflictError,
    ActionStoreBackend,
    JsonDict,
    _canonical_digest,
    _opaque_id,
    commit_proposal_mutation,
    current_utc_now,
    is_agent_operation_executor,
    is_str_dict,
    operation_confirmation_digest,
    parse_utc_timestamp,
    require_operation,
    require_proposal,
    require_str_dict,
    safe_object_dict,
    store_lock,
    store_proposals,
    validate_decision,
    validate_human_confirmation,
    validate_safe_object,
)

_OPERATION_DELIVERY_FIELDS = (
    "provider",
    "message_id",
    "chat_id",
    "app_id",
    "binding_digest",
    "card_digest",
    "delivered_at",
)
_OPERATION_CONFIRMATION_FIELDS = frozenset(
    {
        "provider",
        "event_id",
        "principal",
        "message_id",
        "chat_id",
        "app_id",
        "surface_kind",
        "interaction_kind",
        "confirmation_digest",
        "card_digest",
        "confirmed_at",
        "actor_class",
        "verification_kind",
    }
)
_OPERATION_RESULT_FIELDS = frozenset(
    {
        "provider",
        "message_id",
        "chat_id",
        "app_id",
        "card_digest",
        "transport",
        "delivered_at",
    }
)


def _list_from_sequence_or_empty(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    raise ValueError("authorized_principals must be a sequence")


def arm_operation(store: ActionStoreBackend, proposal_id: str) -> JsonDict:
    """Turn one provider-neutral preview into the canonical confirmation gate.

    The immutable request already lives on the proposal.  This method adds
    only lifecycle state derived from that request; Lark, the Dashboard and
    domain executors must all read this same envelope.
    """
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="arm_operation_confirmation"):
        payload = store._read()
        proposal = require_proposal(payload, token)
        if proposal.get("action_kind") != "operation.execute":
            raise ActionConflictError("only operation.execute previews can be armed")
        existing = proposal.get("operation")
        if is_str_dict(existing):
            if (
                existing.get("schema_version") != OPERATION_ENVELOPE_SCHEMA_VERSION
                or existing.get("lifecycle_state") not in OPERATION_LIFECYCLE_STATES
            ):
                raise ValueError("typed operation envelope is malformed")
            return proposal
        if proposal.get("status") != "preview_ready":
            raise ActionConflictError(
                f"proposal in {proposal.get('status')} state cannot await confirmation"
            )
        parameters = proposal.get("normalized_parameters")
        if not is_str_dict(parameters):
            raise ValueError("typed operation parameters are malformed")
        now = current_utc_now()
        executor = parameters.get("executor")
        operation: JsonDict = {
            "schema_version": OPERATION_ENVELOPE_SCHEMA_VERSION,
            "operation_id": token,
            "lifecycle_state": "awaiting_confirmation",
            "confirmation_digest": operation_confirmation_digest(proposal, parameters),
            "payload_digest": parameters.get("payload_digest"),
            "projection_digest": parameters.get("projection_digest"),
            "executor_revision": (
                executor.get("revision") if is_str_dict(executor) else None
            ),
            "destination_account_ref": parameters.get("destination_account_ref"),
            "expires_at": parameters.get("expires_at"),
            "authorized_principals": _list_from_sequence_or_empty(
                parameters.get("authorized_principals")
            ),
            "delivery": None,
            "confirmation": None,
            "claim": None,
            "outcome": None,
            "result_delivery": None,
            "armed_at": now,
        }
        proposal["operation"] = safe_object_dict(
            operation, path="operation", label="operation"
        )
        proposal["status"] = "gated"
        proposal["gate"] = {
            "kind": "human_operation_confirmation",
            "summary": "This exact operation is awaiting an authenticated human decision.",
            "next_action": "Use the bound operation card; ordinary typed-action apply is not confirmation.",
        }
        return commit_proposal_mutation(store, payload, proposal, updated_at=now)


def record_operation_delivery(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    delivery: Mapping[str, object],
) -> JsonDict:
    """Bind the exact provider message which may attest a later click."""
    safe_delivery = validate_safe_object(
        delivery,
        path="operation.delivery",
        label="operation delivery",
        bounded_fields=_OPERATION_DELIVERY_FIELDS,
        field_prefix="operation.delivery",
    )
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="record_operation_delivery"):
        payload = store._read()
        proposal, operation = require_operation(payload, token)
        if operation.get("lifecycle_state") != "awaiting_confirmation":
            existing = operation.get("delivery")
            if is_str_dict(existing) and existing == safe_delivery:
                return proposal
            raise ActionConflictError("operation is no longer awaiting delivery")
        existing = operation.get("delivery")
        if existing is not None:
            if existing != safe_delivery:
                raise ActionConflictError(
                    "operation is already bound to another provider message"
                )
            return proposal
        operation["delivery"] = safe_delivery
        return commit_proposal_mutation(store, payload, proposal)


def record_operation_delivery_snapshot(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    submitted_card: Mapping[str, object],
) -> JsonDict:
    """Persist the immutable sent card after verifying its recorded digest."""
    safe_card = safe_object_dict(
        submitted_card,
        path="operation.delivery.submitted_card",
        label="operation submitted card",
    )
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="record_operation_delivery_snapshot"):
        payload = store._read()
        proposal = store_proposals(payload).get(token)
        operation = (
            proposal.get("operation") if is_str_dict(proposal) else None
        )
        delivery = operation.get("delivery") if is_str_dict(operation) else None
        if not is_str_dict(proposal) or not is_str_dict(delivery):
            raise ActionConflictError("operation card delivery was not recorded")
        if _canonical_digest(safe_card) != delivery.get("card_digest"):
            raise ActionConflictError("operation submitted card digest drifted")
        existing = delivery.get("submitted_card")
        if existing is not None:
            if existing != safe_card:
                raise ActionConflictError(
                    "operation submitted card snapshot already differs"
                )
            return proposal
        delivery["submitted_card"] = safe_card
        return commit_proposal_mutation(store, payload, proposal)


def decide_operation(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    decision: str,
    confirmation: Mapping[str, object],
) -> JsonDict:
    """Atomically reject, or confirm and claim, one delivered operation.

    The transport owner must supply a verified callback envelope.  A
    successful confirmation consumes the operation in this transaction so
    concurrent web/Lark clicks cannot both dispatch it.
    """
    selected_decision = validate_decision(decision, label="operation")
    safe_confirmation = validate_human_confirmation(
        confirmation,
        path="operation.confirmation",
        label="operation confirmation",
        required_keys=_OPERATION_CONFIRMATION_FIELDS,
    )
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="decide_operation"):
        payload = store._read()
        proposal, operation = require_operation(payload, token)
        existing_confirmation = operation.get("confirmation")
        if is_str_dict(existing_confirmation):
            if existing_confirmation.get("event_id") == safe_confirmation["event_id"]:
                return proposal
            raise ActionConflictError("operation already consumed another decision")
        if (
            proposal.get("status") != "gated"
            or operation.get("lifecycle_state") != "awaiting_confirmation"
        ):
            raise ActionConflictError("operation is not awaiting confirmation")
        delivery = operation.get("delivery")
        if not is_str_dict(delivery):
            raise ActionConflictError("operation card delivery is not verified")
        expected = {
            "provider": delivery.get("provider"),
            "message_id": delivery.get("message_id"),
            "chat_id": delivery.get("chat_id"),
            "app_id": delivery.get("app_id"),
            "card_digest": delivery.get("card_digest"),
            "confirmation_digest": operation.get("confirmation_digest"),
        }
        if any(
            safe_confirmation.get(field) != value for field, value in expected.items()
        ):
            raise ActionConflictError(
                "operation callback does not match the delivered request"
            )
        principals = _list_from_sequence_or_empty(
            operation.get("authorized_principals")
        )
        if safe_confirmation.get("principal") not in set(principals):
            raise ActionConflictError(
                "principal is not authorized for this operation"
            )
        expires_at = parse_utc_timestamp(
            operation.get("expires_at"),
            error="operation timestamps require a timezone",
        )
        confirmed_at = parse_utc_timestamp(
            safe_confirmation["confirmed_at"],
            error="operation timestamps require a timezone",
        )
        delivered_at = parse_utc_timestamp(
            delivery.get("delivered_at"),
            error="operation timestamps require a timezone",
        )
        if confirmed_at < delivered_at - timedelta(minutes=5):
            raise ActionConflictError(
                "operation confirmation predates the delivered request"
            )
        if confirmed_at > expires_at:
            raise ActionConflictError("operation confirmation arrived after expiry")
        now = current_utc_now()
        now_at = datetime.fromisoformat(now.replace("Z", "+00:00"))
        if confirmed_at > now_at + timedelta(minutes=5):
            raise ActionConflictError(
                "operation confirmation timestamp is in the future"
            )
        operation["confirmation"] = {
            **safe_confirmation,
            "decision": selected_decision,
        }
        if selected_decision == "reject":
            outcome: JsonDict = {
                "schema_version": "loopx_operation_outcome_v0",
                "outcome": "rejected_by_operator",
                "projection_verified": True,
                "observed_at": now,
            }
            operation["lifecycle_state"] = "outcome_observed"
            operation["outcome"] = outcome
            proposal["status"] = "rejected"
            proposal["rejected_at"] = now
            proposal["receipt"] = outcome
        else:
            operation["lifecycle_state"] = "claimed"
            operation["claim"] = {
                "claim_id": "claim-" + uuid.uuid4().hex,
                "event_id": safe_confirmation["event_id"],
                "claimed_at": now,
            }
            proposal["status"] = "applying"
        proposal["gate"] = None
        return commit_proposal_mutation(store, payload, proposal, updated_at=now)


def agent_operation_plan(
    proposal: Mapping[str, object], **request: object
) -> JsonDict:
    """One typed decision, using the existing canonical JSON byte format."""
    from ..control_plane.effect_runtime import (
        EffectRuntimeRemoteError,
        effect_runtime_result,
    )

    raw_params = proposal.get("normalized_parameters")
    parameters = raw_params if is_str_dict(raw_params) else {}
    raw_payload = parameters.get("payload")
    payload_map = raw_payload if is_str_dict(raw_payload) else {}
    raw_projection = parameters.get("projection")
    projection_map = raw_projection if is_str_dict(raw_projection) else {}
    raw_op = proposal.get("operation")
    raw_outcome = raw_op.get("outcome") if is_str_dict(raw_op) else None
    digests = {
        "payload_digest": _canonical_digest(payload_map),
        "projection_digest": _canonical_digest(projection_map),
        "confirmation_digest": operation_confirmation_digest(proposal, parameters),
        "outcome_digest": (
            _canonical_digest(raw_outcome) if is_str_dict(raw_outcome) else None
        ),
    }
    try:
        raw_plan: object = effect_runtime_result(
            "operation.agent_handoff.plan",
            {
                "proposal": dict(proposal),
                "digests": digests,
                "now": current_utc_now(),
                **request,
            },
        )
    except EffectRuntimeRemoteError as exc:
        raise ActionConflictError(str(exc)) from exc
    if not is_str_dict(raw_plan):
        raise ValueError("agent operation plan must be an object")
    return dict(raw_plan)


def observe_operation_outcome(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    outcome: Mapping[str, object],
    agent_actor: Mapping[str, object] | None = None,
    agent_binding_current: bool = False,
    agent_actor_binding_current: bool = False,
) -> JsonDict:
    """Persist the domain result without making it a retryable submission."""
    safe_outcome = safe_object_dict(
        outcome, path="operation.outcome", label="operation outcome"
    )
    if safe_outcome.get("schema_version") != "loopx_operation_outcome_v0":
        raise ValueError("operation outcome schema is unsupported")
    _opaque_id(safe_outcome.get("outcome"), field="operation.outcome.outcome")
    if safe_outcome.get("projection_verified") is not True:
        raise ValueError("operation outcome projection must be verified")
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="observe_operation_outcome"):
        payload = store._read()
        proposal, operation = require_operation(payload, token)
        report_provenance: object = None
        if is_agent_operation_executor(proposal):
            plan = agent_operation_plan(
                proposal,
                action="report",
                actor=agent_actor,
                binding_current=agent_binding_current,
                actor_binding_current=agent_actor_binding_current,
                outcome=safe_outcome,
            )
            report_provenance = plan["report_provenance"]
            if plan.get("write_reconciliation") is True:
                existing = operation.get("reconciliation")
                if existing is not None:
                    if existing != safe_outcome:
                        raise ActionConflictError(
                            "operation reconciliation is already immutable"
                        )
                    return proposal
                operation["reconciliation"] = safe_outcome
                operation["reconciliation_report"] = report_provenance
                proposal["receipt"] = safe_outcome
                return commit_proposal_mutation(store, payload, proposal)
        if operation.get("lifecycle_state") == "outcome_observed":
            if operation.get("outcome") != safe_outcome:
                raise ActionConflictError("operation outcome is already immutable")
            return proposal
        if (
            operation.get("lifecycle_state") != "claimed"
            or proposal.get("status") != "applying"
        ):
            raise ActionConflictError("only a claimed operation can record an outcome")
        now = current_utc_now()
        operation["lifecycle_state"] = "outcome_observed"
        operation["outcome"] = safe_outcome
        if report_provenance is not None:
            operation["outcome_report"] = report_provenance
        proposal["status"] = "applied"
        proposal["receipt"] = safe_outcome
        proposal["applied_at"] = now
        return commit_proposal_mutation(store, payload, proposal, updated_at=now)


def _apply_agent_operation_plan(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    lock_operation: str,
    agent_id: str | None,
    plan_request: JsonDict,
    pop_key: str,
    operation_field: str,
) -> tuple[JsonDict, object | None]:
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, agent_id=agent_id, operation=lock_operation):
        payload = store._read()
        proposal = require_proposal(
            payload, token, message="typed operation was not found"
        )
        plan = agent_operation_plan(proposal, **plan_request)
        receipt = plan.pop(pop_key, None)
        if receipt is not None:
            operation = require_str_dict(
                proposal.get("operation"),
                error="typed operation envelope is malformed",
            )
            operation[operation_field] = receipt
            commit_proposal_mutation(store, payload, proposal)
        return plan, receipt


def reserve_agent_operation_turn(
    store: ActionStoreBackend,
    proposal_id: str,
    **launch_context: object,
) -> JsonDict:
    """Internal callback launch intent.

    Commit before spawning. A lost/failed spawn is reconciled from the
    original Turn; callback replay never launches a replacement process.
    """
    plan, receipt = _apply_agent_operation_plan(
        store,
        proposal_id,
        lock_operation="reserve_operation_turn",
        agent_id=None,
        plan_request={"action": "wake", **launch_context},
        pop_key="write_turn_launch",
        operation_field="turn_launch",
    )
    if receipt is not None:
        plan["turn_launch"] = receipt
    return plan


def record_agent_operation_host_start(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    actor: Mapping[str, object],
    binding_current: bool,
    turn_key: str,
) -> JsonDict:
    """Internal native transport observation; never a caller proof or permit."""
    plan, receipt = _apply_agent_operation_plan(
        store,
        proposal_id,
        lock_operation="observe_operation_host_start",
        agent_id=None,
        plan_request={
            "action": "observe_host_start",
            "actor": dict(actor),
            "binding_current": binding_current,
            "turn_key": turn_key,
        },
        pop_key="write_host_start",
        operation_field="host_start",
    )
    if receipt is not None:
        plan.update(host_start=receipt, host_delivery="native_start_accepted")
    return plan


def consume_agent_operation(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    actor: Mapping[str, object],
    binding_current: bool,
    consumption_id: str,
) -> JsonDict:
    """Commit before external execution. Even an exact replay grants no retry."""
    plan, handoff = _apply_agent_operation_plan(
        store,
        proposal_id,
        lock_operation="consume_agent_operation",
        agent_id="loopx-chat",
        plan_request={
            "action": "consume",
            "actor": dict(actor),
            "binding_current": binding_current,
            "consumption_id": consumption_id,
        },
        pop_key="write_handoff",
        operation_field="agent_handoff",
    )
    if is_str_dict(handoff):
        plan["consumption_id"] = handoff["consumption_id"]
    return plan


def record_operation_result_delivery(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    delivery: Mapping[str, object],
) -> JsonDict:
    """Persist exact result-card readback without changing domain outcome."""
    safe_delivery = validate_safe_object(
        delivery,
        path="operation.result_delivery",
        label="operation result delivery",
        required_keys=_OPERATION_RESULT_FIELDS,
        allowed_extra_keys={"outcome_stage"},
        bounded_fields=_OPERATION_RESULT_FIELDS,
        field_prefix="operation.result_delivery",
    )
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="record_operation_result_delivery"):
        payload = store._read()
        proposal, operation = require_operation(payload, token)
        if operation.get("lifecycle_state") != "outcome_observed":
            raise ActionConflictError(
                "operation outcome is unavailable for result delivery"
            )
        is_agent = is_agent_operation_executor(proposal)
        expected_stage = (
            "reconciled" if operation.get("reconciliation") is not None else "initial"
        )
        if is_agent and safe_delivery.get("outcome_stage") != expected_stage:
            raise ActionConflictError(
                "result delivery is not for the current operation outcome"
            )
        if not is_agent and "outcome_stage" in safe_delivery:
            raise ValueError(
                "result outcome stage is only supported for Agent handoff"
            )
        source_delivery = operation.get("delivery")
        if not is_str_dict(source_delivery) or any(
            safe_delivery.get(field) != source_delivery.get(field)
            for field in ("provider", "message_id", "chat_id", "app_id")
        ):
            raise ActionConflictError(
                "operation result delivery does not match the original card"
            )
        existing = operation.get("result_delivery")
        if is_str_dict(existing):
            if existing != safe_delivery:
                if not (
                    is_agent
                    and existing.get("outcome_stage") == "initial"
                    and expected_stage == "reconciled"
                ):
                    raise ActionConflictError(
                        "operation result delivery is already immutable"
                    )
            else:
                return proposal
        operation["result_delivery"] = safe_delivery
        return commit_proposal_mutation(store, payload, proposal)
