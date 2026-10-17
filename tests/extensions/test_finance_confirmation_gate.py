"""Synthetic coverage for the provider-neutral confirmation gate."""

from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "loopx-finance-execution" / "src"))

from loopx_finance_execution.confirmation_gate import (  # noqa: E402
    CARD_BINDING_CONFLICT,
    CARD_BINDING_MISSING,
    CLAIM_MISSING,
    CONFIRMATION_CARD_MISMATCH,
    CONFIRMATION_MISSING,
    CONFIRMATION_PRINCIPAL_MISMATCH,
    CONFIRMATION_PROVIDER_MISMATCH,
    DEADLINE_INVALID,
    DEADLINE_MISSING,
    EXPIRED,
    OPERATION_NOT_LIVE,
    PAYLOAD_TERMS_MISSING,
    TERMS_MISMATCH,
    verify_bound_confirmation,
)


NOW = datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc)
CARD = "om_synthetic_card_1"
PRINCIPAL = "lark:ou_synthetic_operator"
CANONICAL_EXPIRY = "2026-10-17T05:00:00Z"
TERMS = {
    "enabled": True,
    "type": "market",
    "side": "long",
    "amount_usdc": "20",
    "limit_price": None,
    "reduce_only": False,
    "take_profit_stop_loss": None,
}


def _proposal() -> dict:
    return {
        "proposal_id": "proposal-synthetic-1",
        "status": "applying",
        "normalized_parameters": json.dumps(
            {
                "payload": {
                    "schema_version": "finance_bound_order_v1",
                    "bound_order": copy.deepcopy(TERMS),
                    "expires_at": CANONICAL_EXPIRY,
                }
            }
        ),
        "operation": {
            "operation_id": "proposal-synthetic-1",
            "lifecycle_state": "claimed",
            "confirmation_digest": "a" * 64,
            "expires_at": CANONICAL_EXPIRY,
            "confirmation": {
                "provider": "lark",
                "event_id": "synthetic-event-1",
                "principal": PRINCIPAL,
                "message_id": CARD,
            },
            "claim": {"claim_id": "claim-synthetic-1"},
            "delivery": {"message_id": CARD},
        },
    }


def _plan() -> dict:
    return {
        "terms_key": "bound_order",
        "terms": copy.deepcopy(TERMS),
        "authorized_principal": PRINCIPAL,
    }


def _payload_of(proposal: dict) -> dict:
    return json.loads(proposal["normalized_parameters"])["payload"]


def _store_payload(proposal: dict, payload: dict) -> None:
    parameters = json.loads(proposal["normalized_parameters"])
    parameters["payload"] = payload
    proposal["normalized_parameters"] = json.dumps(parameters)


def test_confirmed_operation_is_admitted() -> None:
    result = verify_bound_confirmation(_proposal(), _plan(), now=NOW)
    assert result["allowed"] is True
    assert result["refusals"] == []
    assert result["evidence"]["claim_id"] == "claim-synthetic-1"
    assert result["evidence"]["origin_card_message_id"] == CARD
    assert set(result["evidence"]["enforced_deadlines"]) == {"canonical", "payload"}


def test_missing_confirmation_refuses() -> None:
    proposal = _proposal()
    proposal["operation"].pop("confirmation")
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert result["allowed"] is False
    assert CONFIRMATION_MISSING in result["refusals"]


def test_confirmation_from_another_channel_refuses() -> None:
    proposal = _proposal()
    proposal["operation"]["confirmation"]["provider"] = "web"
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert CONFIRMATION_PROVIDER_MISMATCH in result["refusals"]


def test_confirmation_from_another_principal_refuses() -> None:
    proposal = _proposal()
    proposal["operation"]["confirmation"]["principal"] = "lark:ou_someone_else"
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert CONFIRMATION_PRINCIPAL_MISMATCH in result["refusals"]


def test_confirmation_from_another_card_refuses() -> None:
    proposal = _proposal()
    proposal["operation"]["confirmation"]["message_id"] = "om_other_card"
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert CONFIRMATION_CARD_MISMATCH in result["refusals"]


def test_missing_origin_card_identity_fails_closed() -> None:
    proposal = _proposal()
    proposal["operation"].pop("delivery")
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert result["allowed"] is False
    assert CARD_BINDING_MISSING in result["refusals"]
    assert CONFIRMATION_CARD_MISMATCH in result["refusals"]


def test_conflicting_origin_card_identity_fails_closed() -> None:
    proposal = _proposal()
    proposal["operation"]["card_message_id"] = "om_second_card"
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert CARD_BINDING_CONFLICT in result["refusals"]
    assert set(result["evidence"]["card_conflict_ids"]) == {CARD, "om_second_card"}


def test_cancelled_operation_is_not_live() -> None:
    proposal = _proposal()
    proposal["status"] = "cancelled"
    proposal["operation"]["lifecycle_state"] = "prepared"
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert OPERATION_NOT_LIVE in result["refusals"]


def test_omitted_claim_refuses() -> None:
    proposal = _proposal()
    proposal["operation"].pop("claim")
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert CLAIM_MISSING in result["refusals"]


def test_empty_claim_refuses() -> None:
    proposal = _proposal()
    proposal["operation"]["claim"] = {}
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert CLAIM_MISSING in result["refusals"]


def test_foreign_claim_shape_refuses() -> None:
    proposal = _proposal()
    proposal["operation"]["claim"] = {"claimed_at": "2026-10-17T03:59:00Z"}
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert CLAIM_MISSING in result["refusals"]


def test_missing_payload_terms_refuses() -> None:
    proposal = _proposal()
    payload = _payload_of(proposal)
    payload.pop("bound_order")
    _store_payload(proposal, payload)
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert PAYLOAD_TERMS_MISSING in result["refusals"]


def test_terms_drift_refuses() -> None:
    plan = _plan()
    plan["terms"]["amount_usdc"] = "500"
    result = verify_bound_confirmation(_proposal(), plan, now=NOW)
    assert TERMS_MISMATCH in result["refusals"]
    assert result["evidence"]["terms_digest"] != result["evidence"]["expected_terms_digest"]


def test_missing_canonical_deadline_refuses() -> None:
    proposal = _proposal()
    proposal["operation"].pop("expires_at")
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert result["allowed"] is False
    assert DEADLINE_MISSING in result["refusals"]


def test_invalid_deadline_refuses() -> None:
    proposal = _proposal()
    proposal["operation"]["expires_at"] = "not-a-timestamp"
    result = verify_bound_confirmation(proposal, _plan(), now=NOW)
    assert DEADLINE_MISSING in result["refusals"]
    plan = _plan()
    plan["expires_at"] = "31/12/2026"
    result = verify_bound_confirmation(_proposal(), plan, now=NOW)
    assert DEADLINE_INVALID in result["refusals"]


def test_deadline_boundary_is_inclusive() -> None:
    exact = datetime(2026, 10, 17, 5, 0, tzinfo=timezone.utc)
    assert EXPIRED in verify_bound_confirmation(_proposal(), _plan(), now=exact)["refusals"]
    before = datetime(2026, 10, 17, 4, 59, 59, tzinfo=timezone.utc)
    assert verify_bound_confirmation(_proposal(), _plan(), now=before)["allowed"] is True


def test_conflicting_deadlines_all_enforced() -> None:
    """A later payload deadline must not extend an expired canonical envelope."""

    proposal = _proposal()
    payload = _payload_of(proposal)
    payload["expires_at"] = "2026-10-17T09:00:00Z"
    _store_payload(proposal, payload)
    later = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)
    result = verify_bound_confirmation(proposal, _plan(), now=later)
    assert EXPIRED in result["refusals"]
    assert result["allowed"] is False


def test_plan_deadline_is_independent() -> None:
    plan = _plan()
    plan["expires_at"] = "2026-10-17T04:30:00Z"
    later = datetime(2026, 10, 17, 4, 45, tzinfo=timezone.utc)
    result = verify_bound_confirmation(_proposal(), plan, now=later)
    assert EXPIRED in result["refusals"]
