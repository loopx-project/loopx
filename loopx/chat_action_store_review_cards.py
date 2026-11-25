"""Review-card delivery and multi-audience decision persistence for Chat actions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .chat_action_store_common import (
    ActionConflictError,
    ActionStoreBackend,
    JsonDict,
    _canonical_digest,
    _opaque_id,
    commit_proposal_mutation,
    current_utc_now,
    is_str_dict,
    require_proposal,
    store_lock,
    store_proposals,
    validate_decision,
    validate_human_confirmation,
    validate_safe_object,
)

_REVIEW_CARD_DELIVERY_FIELDS = frozenset(
    {
        "provider",
        "message_id",
        "chat_id",
        "app_id",
        "cli_bin",
        "sender_profile",
        "binding_digest",
        "card_digest",
        "submitted_card",
        "delivered_at",
        "authorized_principal",
    }
)
_REVIEW_CARD_CONFIRMATION_FIELDS = frozenset(
    {
        "provider",
        "event_id",
        "principal",
        "message_id",
        "chat_id",
        "app_id",
        "audience_id",
        "state_fingerprint",
        "card_digest",
        "confirmed_at",
        "actor_class",
        "verification_kind",
    }
)
_REVIEW_CARD_RESULT_FIELDS = frozenset({"card_digest", "transport", "delivered_at"})


def record_review_card_delivery(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    audience_id: str,
    delivery: Mapping[str, object],
) -> JsonDict:
    """Bind one verified external review surface to a typed proposal.

    A proposal may be rendered into more than one audience, but every card
    retains the proposal id and state fingerprint owned by the typed action
    store.  Recording all audiences on that one proposal is what prevents a
    manager card and a Goal card from becoming two independent approvals.
    """
    audience = _opaque_id(audience_id, field="review_card.audience_id")
    safe_delivery = validate_safe_object(
        delivery,
        path=f"review_card.deliveries.{audience}",
        label="review card delivery",
        required_keys=_REVIEW_CARD_DELIVERY_FIELDS,
        bounded_fields=_REVIEW_CARD_DELIVERY_FIELDS - {"submitted_card"},
        field_prefix="review_card.delivery",
    )
    submitted_card = safe_delivery.get("submitted_card")
    if not is_str_dict(submitted_card):
        raise ValueError("review card submitted payload must be an object")
    if _canonical_digest(submitted_card) != safe_delivery["card_digest"]:
        raise ActionConflictError("review card submitted payload drifted")
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="record_review_card_delivery"):
        payload = store._read()
        proposal = require_proposal(payload, token)
        if proposal.get("action_kind") != "team.plan":
            raise ActionConflictError("only a team plan can bind this review card")
        review_card = proposal.get("review_card")
        if review_card is None:
            raise ActionConflictError(
                "review card audiences must be prepared before delivery"
            )
        if not is_str_dict(review_card):
            raise ValueError("typed review card state is malformed")
        if review_card.get("state_fingerprint") != proposal.get(
            "expected_state_fingerprint"
        ):
            raise ActionConflictError("review card state fingerprint drifted")
        if review_card.get("authorized_principal") != safe_delivery.get(
            "authorized_principal"
        ):
            raise ActionConflictError(
                "review card audience changed the authorized principal"
            )
        deliveries = review_card.get("deliveries")
        if not is_str_dict(deliveries):
            raise ValueError("typed review card deliveries are malformed")
        expected_audiences = review_card.get("expected_audience_ids")
        if (
            not isinstance(expected_audiences, list)
            or audience not in expected_audiences
        ):
            raise ActionConflictError("review card audience was not prepared")
        existing = deliveries.get(audience)
        if existing is not None:
            immutable_fields = _REVIEW_CARD_DELIVERY_FIELDS - {"delivered_at"}
            if not isinstance(existing, Mapping) or any(
                existing.get(field) != safe_delivery.get(field)
                for field in immutable_fields
            ):
                raise ActionConflictError(
                    "review card audience is already bound to another message"
                )
            return proposal
        if review_card.get("confirmation") is not None:
            raise ActionConflictError("review card decision is already consumed")
        deliveries[audience] = safe_delivery
        return commit_proposal_mutation(store, payload, proposal)


def prepare_review_card_delivery(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    audience_ids: Sequence[str],
    authorized_principal: str,
) -> JsonDict:
    """Freeze every audience before the first actionable card is sent."""
    normalized_audiences = sorted(
        {_opaque_id(value, field="review_card.audience_id") for value in audience_ids}
    )
    if not normalized_audiences or len(normalized_audiences) != len(audience_ids):
        raise ValueError("review card audiences must be non-empty and unique")
    principal = _opaque_id(
        authorized_principal, field="review_card.authorized_principal"
    )
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="prepare_review_card_delivery"):
        payload = store._read()
        proposal = require_proposal(payload, token)
        if proposal.get("action_kind") != "team.plan":
            raise ActionConflictError("only a team plan can bind this review card")
        expected: JsonDict = {
            "schema_version": "loopx_review_card_delivery_v0",
            "state_fingerprint": proposal.get("expected_state_fingerprint"),
            "expected_audience_ids": normalized_audiences,
            "deliveries": {},
            "confirmation": None,
            "authorized_principal": principal,
        }
        existing = proposal.get("review_card")
        if existing is None:
            proposal["review_card"] = expected
            return commit_proposal_mutation(store, payload, proposal)
        if not is_str_dict(existing):
            raise ValueError("typed review card state is malformed")
        immutable = {
            "schema_version": expected["schema_version"],
            "state_fingerprint": expected["state_fingerprint"],
            "expected_audience_ids": expected["expected_audience_ids"],
            "authorized_principal": expected["authorized_principal"],
        }
        if any(existing.get(key) != value for key, value in immutable.items()):
            raise ActionConflictError("review card audience plan drifted")
        return proposal


def decide_review_card(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    decision: str,
    confirmation: Mapping[str, object],
) -> JsonDict:
    """Consume one verified card decision across every delivered audience."""
    selected_decision = validate_decision(decision, label="review card")
    safe_confirmation = validate_human_confirmation(
        confirmation,
        path="review_card.confirmation",
        label="review card confirmation",
        required_keys=_REVIEW_CARD_CONFIRMATION_FIELDS,
    )
    token = _opaque_id(proposal_id, field="proposal_id")
    audience = _opaque_id(
        safe_confirmation["audience_id"], field="review_card.audience_id"
    )
    with store_lock(store.path, operation="decide_review_card"):
        payload = store._read()
        proposal = store_proposals(payload).get(token)
        review_card = (
            proposal.get("review_card") if is_str_dict(proposal) else None
        )
        if not is_str_dict(proposal) or not is_str_dict(review_card):
            raise KeyError("typed review card was not found")
        deliveries = review_card.get("deliveries")
        delivery = deliveries.get(audience) if is_str_dict(deliveries) else None
        if not is_str_dict(deliveries) or not is_str_dict(delivery):
            raise ActionConflictError("review card audience was not delivered")
        expected_audiences = review_card.get("expected_audience_ids")
        if (
            not isinstance(expected_audiences, list)
            or set(deliveries) != set(expected_audiences)
        ):
            raise ActionConflictError(
                "review card audiences are not completely delivered"
            )
        expected = {
            "provider": delivery.get("provider"),
            "message_id": delivery.get("message_id"),
            "chat_id": delivery.get("chat_id"),
            "app_id": delivery.get("app_id"),
            "state_fingerprint": review_card.get("state_fingerprint"),
            "card_digest": delivery.get("card_digest"),
        }
        if any(
            safe_confirmation.get(field) != value for field, value in expected.items()
        ):
            raise ActionConflictError(
                "review card callback does not match the delivered request"
            )
        if safe_confirmation.get("principal") != review_card.get(
            "authorized_principal"
        ):
            raise ActionConflictError(
                "principal is not authorized for this review card"
            )
        existing = review_card.get("confirmation")
        if is_str_dict(existing):
            # Every audience is a view of the same proposal. Once one exact
            # decision wins, later clicks only observe that decision and can
            # never launch a second canonical effect.
            return proposal
        status = str(proposal.get("status") or "")
        if status not in {"preview_ready", "deferred"}:
            raise ActionConflictError(
                "review card proposal is no longer awaiting confirmation"
            )
        now = current_utc_now()
        review_card["confirmation"] = {
            **safe_confirmation,
            "decision": selected_decision,
        }
        if selected_decision == "confirm":
            proposal["status"] = "applying"
            proposal["gate"] = None
            proposal["failure"] = None
        else:
            proposal["status"] = "rejected"
            proposal["rejected_at"] = now
        return commit_proposal_mutation(store, payload, proposal, updated_at=now)


def record_review_card_result_delivery(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    audience_id: str,
    result: Mapping[str, object],
) -> JsonDict:
    """Record exact result-card readback for one already-bound audience."""
    audience = _opaque_id(audience_id, field="review_card.audience_id")
    safe_result = validate_safe_object(
        result,
        path=f"review_card.deliveries.{audience}.result",
        label="review card result delivery",
        required_keys=_REVIEW_CARD_RESULT_FIELDS,
        bounded_fields=_REVIEW_CARD_RESULT_FIELDS,
        field_prefix="review_card.result",
    )
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="record_review_card_result_delivery"):
        payload = store._read()
        proposal = store_proposals(payload).get(token)
        review_card = (
            proposal.get("review_card") if is_str_dict(proposal) else None
        )
        deliveries = (
            review_card.get("deliveries") if is_str_dict(review_card) else None
        )
        delivery = deliveries.get(audience) if is_str_dict(deliveries) else None
        if not is_str_dict(proposal) or not is_str_dict(delivery):
            raise KeyError("typed review card audience was not found")
        existing = delivery.get("result")
        if existing is not None:
            if existing != safe_result:
                raise ActionConflictError(
                    "review card result delivery is already immutable"
                )
            return proposal
        delivery["result"] = safe_result
        return commit_proposal_mutation(store, payload, proposal)
