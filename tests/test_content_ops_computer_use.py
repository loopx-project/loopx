"""Tests for content-ops's computer_use_runtime_v0 vertical slice.

These tests do two jobs at once:

1. Exercise content-ops's own stdlib-only provider/reducer boundary
   (loopx/capabilities/content_ops/computer_use_provider.py and
   computer_use_reducer.py), which is all that ships in a plain
   ``pip install loopx``.
2. Cross-validate everything that boundary builds or accepts against the
   authoritative jsonschema-based validator in
   scripts/computer_use_runtime_contract_validator.py (a ``[test]``-extras
   dev tool, not shipped). This is how the full computer_use_runtime_v0
   contract stays a real, exercised call site even though production code
   deliberately does not import jsonschema -- see the module docstring in
   computer_use_provider.py for why.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.content_ops.computer_use_provider import (
    ContentOpsCuaContractViolation,
    FakeComputerUseProvider,
    build_content_ops_browser_action_request,
    build_content_ops_browser_action_request_packet,
    check_action_request_shape,
    check_receipt_shape,
)
from loopx.capabilities.content_ops.computer_use_reducer import (
    apply_content_ops_browser_receipt,
    reduce_content_ops_browser_receipt,
)
from loopx.capabilities.content_ops.item_lifecycle import (
    apply_content_ops_item_event,
    build_content_ops_item,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from computer_use_runtime_contract_validator import (  # noqa: E402
    ContractViolation as RealContractViolation,
)
from computer_use_runtime_contract_validator import (  # noqa: E402
    validate_action_request as real_validate_action_request,
)
from computer_use_runtime_contract_validator import (  # noqa: E402
    validate_receipt as real_validate_receipt,
)
from computer_use_runtime_contract_validator import (  # noqa: E402
    validate_receipt_matches_request as real_validate_receipt_matches_request,
)

GOAL_ID = "loopx-content-ops-cua-showcase"
TODO_ID = "todo_content_ops_cua_showcase"
DIGEST_V1 = "sha256:" + "1" * 64


def _item() -> dict[str, Any]:
    return build_content_ops_item(
        item_id="cua-showcase-post",
        item_kind="post",
        channel="x",
        content_digest=DIGEST_V1,
        content_ref="draft:cua-showcase-post",
        created_at="2026-08-18T09:00:00+00:00",
    )


def _approve(item: dict[str, Any], *, event_id: str, approval_ref: str) -> dict[str, Any]:
    packet = apply_content_ops_item_event(
        item,
        {
            "event_id": event_id,
            "action": "approve",
            "expected_state": item["state"],
            "expected_revision": item["revision"],
            "occurred_at": "2026-08-18T09:10:00+00:00",
            "payload": {
                "approval_ref": approval_ref,
                "revision": item["revision"],
                "content_digest": item["content_digest"],
                "effect_kind": "publish",
            },
        },
    )
    return packet["item"]


def _declare_delivery_intent(
    item: dict[str, Any],
    *,
    event_id: str,
    provider_id: str = "computer_use_runtime",
    occurred_at: str = "2026-08-18T09:12:00+00:00",
) -> dict[str, Any]:
    """Directly construct a delivery_ready item via the existing
    set_delivery_intent action -- bypassing build_content_ops_browser_action_request_packet's
    own orchestration, for tests that want to exercise a lower-level function
    in isolation without also exercising the packet's declare-then-build
    behavior itself."""

    packet = apply_content_ops_item_event(
        item,
        {
            "event_id": event_id,
            "action": "set_delivery_intent",
            "expected_state": item["state"],
            "expected_revision": item["revision"],
            "occurred_at": occurred_at,
            "payload": {
                "provider_id": provider_id,
                "effect_kind": item["approval"]["effect_kind"],
            },
        },
    )
    return packet["item"]


def test_draft_action_request_has_no_gate_binding_and_passes_real_validator() -> None:
    item = _item()
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    assert request["effect_class"] == "draft"
    assert "gate_binding" not in request
    real_validate_action_request(request)


def test_approved_item_has_no_request_until_delivery_intent_is_declared() -> None:
    """build_content_ops_browser_action_request is pure and never writes --
    an "approved" item alone is not enough to get a request; delivery intent
    must already be durably declared (delivery_ready) first."""

    item = _apply_review_and_approve()
    assert item["state"] == "approved"
    with pytest.raises(ContentOpsCuaContractViolation, match="delivery intent must be durably declared"):
        build_content_ops_browser_action_request(item=item, goal_id=GOAL_ID, todo_id=TODO_ID)


def test_delivery_ready_action_request_binds_gate_to_approval_sequence() -> None:
    item = _declare_delivery_intent(_apply_review_and_approve(), event_id="event-declare-1")
    assert item["state"] == "delivery_ready"
    assert item["approval_sequence"] == 1
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    assert request["effect_class"] == "external_write"
    assert request["gate_binding"] == {
        "gate_id": "gate_cua-showcase-post_publish",
        "revision": 1,
        "status": "open",
    }
    real_validate_action_request(request, known_gate_revision=1)
    with pytest.raises(RealContractViolation, match="stale"):
        real_validate_action_request(request, known_gate_revision=2)


def test_packet_declares_delivery_intent_before_returning_a_request_and_refuses_on_retry() -> None:
    """The actual durable-fence behavior item-browser-request relies on:
    build_content_ops_browser_action_request_packet durably transitions an
    approved item to delivery_ready *before* ever returning a request a
    provider could act on, and refuses outright on a second call for the
    same (now already-delivery_ready) item -- fail closed on retry, not a
    silent rebuild of an identical request."""

    item = _apply_review_and_approve()
    assert item["state"] == "approved"

    packet = build_content_ops_browser_action_request_packet(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID, occurred_at="2026-08-18T09:12:00+00:00"
    )
    assert packet["ok"] is True
    assert packet["item"]["state"] == "delivery_ready"
    assert packet["action_request"]["gate_binding"]["revision"] == 1
    assert packet["expected_transition"] == {
        "expected_state": "delivery_ready",
        "expected_revision": item["revision"],
    }

    # "restart": call it again using the item exactly as the first call
    # returned it (what a caller must persist before invoking a provider).
    with pytest.raises(ContentOpsCuaContractViolation, match="already declared"):
        build_content_ops_browser_action_request_packet(
            item=packet["item"],
            goal_id=GOAL_ID,
            todo_id=TODO_ID,
            occurred_at="2026-08-18T09:13:00+00:00",
        )


def test_provider_already_executed_but_never_landed_must_not_execute_again_after_restart() -> None:
    """The exact scenario from review: the provider has already executed
    (we hold a completed receipt) but nothing about that was ever applied
    anywhere (simulating a crash/lost receipt before any commit). After
    "restart" -- a fresh call with only the durably-persisted item in hand --
    a second external-write request must not be producible."""

    item = _apply_review_and_approve()
    packet = build_content_ops_browser_action_request_packet(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID, occurred_at="2026-08-18T09:12:00+00:00"
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "submit_click_permitted": True},
        session_reference="fake_crash_session",
    )
    receipt = provider.attempt(packet["action_request"])
    assert receipt["stop_reason"] == "completed"
    # Simulate the crash: the receipt is never processed by item-browser-receipt
    # at all. "Restart" means only packet["item"] (already persisted) survives.

    with pytest.raises(ContentOpsCuaContractViolation, match="already declared"):
        build_content_ops_browser_action_request_packet(
            item=packet["item"],
            goal_id=GOAL_ID,
            todo_id=TODO_ID,
            occurred_at="2026-08-18T09:20:00+00:00",
        )


def test_legacy_approved_item_needs_a_fresh_approve_before_it_can_drive_cua() -> None:
    """A content_ops_item_v0 approved before approval_sequence existed --
    e.g. handed straight from a JSON file to item-browser-request, which
    does no validation of its own -- must fail closed rather than crash or
    silently issue a request at an inferred-nothing gate revision. Only a
    fresh approve event (establishing real, CUA-aware gate identity) unlocks
    it. set_delivery_intent itself does not care about approval_sequence (it
    only checks approval/effect_kind), so a legacy item CAN still reach
    delivery_ready with approval_sequence=0 -- the check that matters lives
    in build_content_ops_browser_action_request's delivery_ready branch,
    which is where this must actually fail."""

    legacy_approved = dict(_apply_review_and_approve())
    del legacy_approved["approval_sequence"]

    legacy_delivery_ready = _declare_delivery_intent(legacy_approved, event_id="event-declare-legacy")
    assert legacy_delivery_ready["approval_sequence"] == 0
    with pytest.raises(ContentOpsCuaContractViolation, match="positive approval_sequence"):
        build_content_ops_browser_action_request(
            item=legacy_delivery_ready, goal_id=GOAL_ID, todo_id=TODO_ID
        )

    # Re-establishing real, CUA-aware gate authority goes through
    # revoke_approval (allowed from delivery_ready) then a fresh approve.
    back_to_review = apply_content_ops_item_event(
        legacy_delivery_ready,
        {
            "event_id": "event-revoke-legacy",
            "action": "revoke_approval",
            "expected_state": "delivery_ready",
            "expected_revision": 1,
            "occurred_at": "2026-08-18T09:11:00+00:00",
            "payload": {"reason": "re-establish CUA-aware gate authority"},
        },
    )["item"]
    reapproved = _approve(
        back_to_review, event_id="event-approve-legacy", approval_ref="decision:legacy-1"
    )
    assert reapproved["approval_sequence"] == 1
    redeclared = _declare_delivery_intent(reapproved, event_id="event-declare-legacy-2")
    request = build_content_ops_browser_action_request(
        item=redeclared, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    assert request["gate_binding"]["revision"] == 1


def _apply_review_and_approve() -> dict[str, Any]:
    item = apply_content_ops_item_event(
        _item(),
        {
            "event_id": "event-review",
            "action": "submit_review",
            "expected_state": "captured",
            "expected_revision": 1,
            "occurred_at": "2026-08-18T09:05:00+00:00",
            "payload": {},
        },
    )["item"]
    return _approve(item, event_id="event-approve-1", approval_ref="decision:cua-showcase-1")


def test_external_write_action_request_without_gate_binding_is_rejected() -> None:
    forged = {
        "schema_version": "computer_use_action_request_v0",
        "goal_id": GOAL_ID,
        "todo_id": TODO_ID,
        "provider_id": "computer_use_runtime",
        "action_unit": "content_ops_publish_after_approved_gate",
        "effect_class": "external_write",
        "write_scope": {
            "allowed_actions": ["click the approved submit control"],
            "forbidden_effect_classes": ["credential_use"],
        },
        "stop_condition": "stop if the live gate revision has changed since approval",
        "validation_target": "submit is clicked only under the exact approved gate revision",
    }
    with pytest.raises(ContentOpsCuaContractViolation, match="requires a gate_binding"):
        check_action_request_shape(forged)
    with pytest.raises(RealContractViolation):
        real_validate_action_request(forged)


def test_draft_round_trip_stops_at_gate_and_proposes_submit_review() -> None:
    item = _item()
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "unexpected_modal_present": False},
        session_reference="fake_session_1",
    )
    receipt = provider.attempt(request)
    assert receipt["stop_reason"] == "stopped_at_gate"
    real_validate_receipt(receipt)
    real_validate_receipt_matches_request(receipt, request)

    decision = reduce_content_ops_browser_receipt(item=item, action_request=request, receipt=receipt)
    assert decision["decision"] == "propose_submit_review"
    event = {
        "event_id": receipt["idempotency_key"],
        "expected_state": item["state"],
        "expected_revision": item["revision"],
        "occurred_at": "2026-08-18T09:05:00+00:00",
        **decision["proposed_event"],
    }
    packet = apply_content_ops_item_event(item, event)
    assert packet["item"]["state"] == "review_ready"
    assert packet["receipt"]["status"] == "applied"

    # Idempotent replay: the same provider retry (same idempotency_key/event_id)
    # must not be double-processed.
    replay = apply_content_ops_item_event(packet["item"], event)
    assert replay["receipt"]["status"] == "already_applied"
    assert replay["item"]["state"] == "review_ready"


def test_unknown_modal_hands_off_without_any_state_transition() -> None:
    item = _item()
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "unexpected_modal_present": True},
        session_reference="fake_session_2",
    )
    receipt = provider.attempt(request)
    assert receipt["stop_reason"] == "blocked_by_unknown_modal"
    real_validate_receipt(receipt)

    decision = reduce_content_ops_browser_receipt(item=item, action_request=request, receipt=receipt)
    assert decision["decision"] == "handoff_blocked"
    assert decision["proposed_event"] is None
    assert decision["blocker"]["item_id"] == item["item_id"]
    # The reducer proposed nothing; the item itself must be untouched.
    assert item["state"] == "captured"


def test_unreachable_screen_hands_off_without_any_state_transition() -> None:
    """The other stop_reason that means 'hand off, don't guess' --
    stop_reason=failed, when the provider can't even reach the target
    screen. Exercises FakeComputerUseProvider's screen_reachable=False branch
    and the reducer's `failed` branch, neither of which any other test in
    this file drives."""

    item = _item()
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": False}, session_reference="fake_session_unreachable"
    )
    receipt = provider.attempt(request)
    assert receipt["stop_reason"] == "failed"
    assert receipt["observed_facts"]["screen_reached"] is False
    real_validate_receipt(receipt)

    decision = reduce_content_ops_browser_receipt(item=item, action_request=request, receipt=receipt)
    assert decision["decision"] == "handoff_blocked"
    assert decision["proposed_event"] is None
    assert decision["blocker"]["item_id"] == item["item_id"]
    assert item["state"] == "captured"


def test_stale_gate_revision_after_revoke_and_reapprove_is_rejected() -> None:
    item = _declare_delivery_intent(_apply_review_and_approve(), event_id="event-declare-stale")
    stale_request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    assert stale_request["gate_binding"]["revision"] == 1

    # revoke_approval is allowed from delivery_ready too.
    revoked = apply_content_ops_item_event(
        item,
        {
            "event_id": "event-revoke",
            "action": "revoke_approval",
            "expected_state": item["state"],
            "expected_revision": item["revision"],
            "occurred_at": "2026-08-18T09:15:00+00:00",
            "payload": {"reason": "owner wants another look before publish"},
        },
    )["item"]
    reapproved = _approve(revoked, event_id="event-approve-2", approval_ref="decision:cua-showcase-2")
    assert reapproved["approval_sequence"] == 2, (
        "content revision never changed across revoke/reapprove, so only the "
        "dedicated approval_sequence counter distinguishes the two approvals"
    )

    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "submit_click_permitted": True},
        session_reference="fake_session_3",
    )
    stale_receipt = provider.attempt(stale_request)
    assert stale_receipt["stop_reason"] == "completed"
    real_validate_receipt(stale_receipt)

    decision = reduce_content_ops_browser_receipt(
        item=reapproved, action_request=stale_request, receipt=stale_receipt
    )
    assert decision["decision"] == "rejected_stale_gate"
    assert decision["proposed_event"] is None

    with pytest.raises(RealContractViolation, match="stale"):
        real_validate_action_request(stale_request, known_gate_revision=2)


def test_reducer_normalizes_a_legacy_item_before_the_gate_staleness_check() -> None:
    """The receipt-side counterpart to
    test_legacy_approved_item_needs_a_fresh_approve_before_it_can_drive_cua:
    reduce_content_ops_browser_receipt must normalize its raw `item` input
    (require_content_ops_item) before reading approval_sequence for the gate
    staleness check, or a legacy item missing that field -- e.g. handed
    straight from a JSON file to item-browser-receipt, which does no
    validation of its own -- would crash with a raw KeyError instead of
    being handled by the same, already-tested logic as everywhere else."""

    item = _declare_delivery_intent(_apply_review_and_approve(), event_id="event-declare-legacy-recv")
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "submit_click_permitted": True},
        session_reference="fake_session_legacy_receipt",
    )
    receipt = provider.attempt(request)

    legacy_item = dict(item)
    del legacy_item["approval_sequence"]

    decision = reduce_content_ops_browser_receipt(
        item=legacy_item, action_request=request, receipt=receipt
    )
    # require_content_ops_item defaults the missing field to 0, which for
    # this *already-approved* legacy item does not match the live request's
    # gate_binding.revision=1 -- so this is correctly rejected as stale, not
    # silently accepted and not a crash.
    assert decision["decision"] == "rejected_stale_gate"

    packet = apply_content_ops_browser_receipt(
        item=legacy_item,
        action_request=request,
        receipt=receipt,
        occurred_at="2026-08-18T09:20:00+00:00",
    )
    assert packet["ok"] is False
    assert packet["decision"] == "rejected_stale_gate"
    # The echoed-back item is the normalized form (approval_sequence now
    # present), not the raw legacy input -- the same "heal on every
    # read/write" behavior apply_content_ops_item_event already has.
    assert packet["item"]["approval_sequence"] == 0


def test_completed_write_under_current_gate_is_confirmed_with_no_further_transition() -> None:
    """The durable fence against a duplicate external effect now happens
    *before* this receipt is ever processed (delivery intent was already
    declared -- see test_packet_declares_delivery_intent_before_returning_a_request_and_refuses_on_retry
    and test_provider_already_executed_but_never_landed_must_not_execute_again_after_restart).
    So a completed receipt for the current, live gate proposes nothing
    further: record_delivery (with a real public_url) stays content-ops's
    existing readback-verified delivery flow, not this reducer's job."""

    item = _declare_delivery_intent(_apply_review_and_approve(), event_id="event-declare-completed")
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "submit_click_permitted": True},
        session_reference="fake_session_4",
    )
    receipt = provider.attempt(request)
    real_validate_receipt(receipt)
    real_validate_action_request(request, known_gate_revision=item["approval_sequence"])

    decision = reduce_content_ops_browser_receipt(item=item, action_request=request, receipt=receipt)
    assert decision["decision"] == "confirmed_external_write_attempted"
    assert decision["proposed_event"] is None

    packet = apply_content_ops_browser_receipt(
        item=item, action_request=request, receipt=receipt, occurred_at="2026-08-18T09:20:00+00:00"
    )
    assert packet["ok"] is True
    assert packet["item"]["state"] == "delivery_ready", "unchanged: nothing left to apply"


def test_completed_receipt_for_an_item_that_moved_past_delivery_ready_is_stale() -> None:
    """A stale replay of an old completed receipt after the item has since
    moved on through other means (e.g. an already-completed record_delivery
    via content-ops's existing tooling) must not be silently accepted just
    because the gate revision still numerically matches."""

    item = _declare_delivery_intent(_apply_review_and_approve(), event_id="event-declare-moved-on")
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "submit_click_permitted": True},
        session_reference="fake_session_moved_on",
    )
    receipt = provider.attempt(request)

    published = apply_content_ops_item_event(
        item,
        {
            "event_id": "event-record-delivery",
            "action": "record_delivery",
            "expected_state": "delivery_ready",
            "expected_revision": item["revision"],
            "occurred_at": "2026-08-18T09:25:00+00:00",
            "payload": {
                "provider_id": "computer_use_runtime",
                "effect_kind": item["approval"]["effect_kind"],
                "content_digest": item["content_digest"],
                "public_url": "https://x.com/example/status/1",
                "receipt_ref": "receipt:x-1",
            },
        },
    )["item"]
    assert published["state"] == "published"
    assert published["approval_sequence"] == item["approval_sequence"], (
        "record_delivery does not touch approval_sequence, so the stale receipt's "
        "gate revision still numerically matches -- state must still be checked"
    )

    decision = reduce_content_ops_browser_receipt(item=published, action_request=request, receipt=receipt)
    assert decision["decision"] == "rejected_stale_gate"
    assert decision["proposed_event"] is None


def test_provider_authored_writeback_field_is_rejected_by_both_validators() -> None:
    item = _item()
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True}, session_reference="fake_session_5"
    )
    receipt = dict(provider.attempt(request))
    receipt["complete_todo"] = True

    with pytest.raises(ContentOpsCuaContractViolation, match="writeback"):
        check_receipt_shape(receipt)
    with pytest.raises(RealContractViolation):
        real_validate_receipt(receipt)


def test_smuggled_credential_in_evidence_is_rejected_by_both_validators() -> None:
    item = _item()
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True}, session_reference="fake_session_6"
    )
    receipt = dict(provider.attempt(request))
    receipt = dict(receipt, evidence=dict(receipt["evidence"], handle_kind="session_id=abc123"))

    with pytest.raises(ContentOpsCuaContractViolation, match="smuggles"):
        check_receipt_shape(receipt)
    with pytest.raises(RealContractViolation):
        real_validate_receipt(receipt)


def _approved_delivery_ready_item(item_id: str, *, declare_event_id: str) -> dict[str, Any]:
    item = build_content_ops_item(
        item_id=item_id,
        item_kind="post",
        channel="x",
        content_digest=DIGEST_V1,
        content_ref=f"draft:{item_id}",
        created_at="2026-08-18T09:00:00+00:00",
    )
    item = apply_content_ops_item_event(
        item,
        {
            "event_id": "event-review",
            "action": "submit_review",
            "expected_state": "captured",
            "expected_revision": 1,
            "occurred_at": "2026-08-18T09:05:00+00:00",
            "payload": {},
        },
    )["item"]
    item = _approve(item, event_id="event-approve", approval_ref=f"decision:{item_id}")
    return _declare_delivery_intent(item, event_id=declare_event_id)


def test_receipt_built_for_a_different_item_cannot_advance_this_one() -> None:
    """The concrete A-on-B reproduction from review: two different items,
    both delivery_ready with the same approval_sequence (a plausible,
    ordinary coincidence -- both are simply on their first approval). A's
    request/receipt must not be able to advance B, even though the gate
    revision numbers line up, because gate_binding.gate_id already encodes
    which item a gate belongs to and the reducer must check it."""

    item_a = _approved_delivery_ready_item("cua-item-a", declare_event_id="event-declare-a")
    item_b = _approved_delivery_ready_item("cua-item-b", declare_event_id="event-declare-b")
    assert item_a["approval_sequence"] == item_b["approval_sequence"] == 1

    request_for_a = build_content_ops_browser_action_request(
        item=item_a, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "submit_click_permitted": True},
        session_reference="fake_session_a",
    )
    receipt_for_a = provider.attempt(request_for_a)

    # Unconditional protection: reduce_content_ops_browser_receipt itself
    # derives the expected gate_id from item_b and refuses, with no
    # expected_item_id needed -- this is what protects a bare/hand-built
    # request path too.
    decision = reduce_content_ops_browser_receipt(
        item=item_b, action_request=request_for_a, receipt=receipt_for_a
    )
    assert decision["decision"] == "rejected_item_mismatch"
    assert decision["proposed_event"] is None

    packet = apply_content_ops_browser_receipt(
        item=item_b,
        action_request=request_for_a,
        receipt=receipt_for_a,
        occurred_at="2026-08-18T09:20:00+00:00",
    )
    assert packet["ok"] is False
    assert packet["decision"] == "rejected_item_mismatch"
    assert packet["item"]["state"] == "delivery_ready", "B must be completely untouched"

    # The packet-path check (expected_item_id) catches the same thing even
    # for a request with no gate_binding at all (the draft path).
    draft_item_a = _item()
    draft_request_a = build_content_ops_browser_action_request(
        item=draft_item_a, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    draft_provider = FakeComputerUseProvider(
        {"screen_reachable": True}, session_reference="fake_session_draft_a"
    )
    draft_receipt_a = draft_provider.attempt(draft_request_a)
    draft_item_b = build_content_ops_item(
        item_id="cua-draft-item-b",
        item_kind="post",
        channel="x",
        content_digest=DIGEST_V1,
        content_ref="draft:cua-draft-item-b",
        created_at="2026-08-18T09:00:00+00:00",
    )
    mismatch = apply_content_ops_browser_receipt(
        item=draft_item_b,
        action_request=draft_request_a,
        receipt=draft_receipt_a,
        occurred_at="2026-08-18T09:20:00+00:00",
        expected_item_id=draft_item_a["item_id"],
    )
    assert mismatch["ok"] is False
    assert mismatch["decision"] == "rejected_item_mismatch"
    assert mismatch["item"]["state"] == "captured", "B must be completely untouched"


def test_gated_write_with_non_open_status_is_rejected_by_both_shape_check_and_provider() -> None:
    """The protocol's Failure And Recovery obligation -- a provider must
    refuse an action request whose effect_class requires a gate that is not
    open -- was previously only prose; a merely well-formed 'closed' or
    'pending' status enum value could still reach and be attempted by a
    provider."""

    item = _approved_delivery_ready_item("cua-item-gate-status", declare_event_id="event-declare-status")
    request = build_content_ops_browser_action_request(item=item, goal_id=GOAL_ID, todo_id=TODO_ID)
    assert request["gate_binding"]["status"] == "open"

    for bad_status in ("closed", "pending"):
        tampered = dict(request, gate_binding=dict(request["gate_binding"], status=bad_status))
        with pytest.raises(ContentOpsCuaContractViolation, match="requires gate_binding.status='open'"):
            check_action_request_shape(tampered)
        with pytest.raises(RealContractViolation):
            real_validate_action_request(tampered)

        provider = FakeComputerUseProvider(
            {"screen_reachable": True, "submit_click_permitted": True},
            session_reference=f"fake_session_gate_{bad_status}",
        )
        with pytest.raises(ContentOpsCuaContractViolation, match="requires gate_binding.status='open'"):
            provider.attempt(tampered)


def test_completed_receipt_with_final_action_not_clicked_is_rejected() -> None:
    """The exact reproduction from review: shape validation alone does not
    catch an internally contradictory receipt (completed, but
    final_action_clicked=false) -- that domain judgment belongs to the
    reducer, not the wire schema."""

    item = _approved_delivery_ready_item("cua-item-facts-1", declare_event_id="event-declare-facts-1")
    request = build_content_ops_browser_action_request(item=item, goal_id=GOAL_ID, todo_id=TODO_ID)
    provider = FakeComputerUseProvider(
        {"screen_reachable": True, "submit_click_permitted": True},
        session_reference="fake_session_facts_1",
    )
    receipt = provider.attempt(request)
    assert receipt["stop_reason"] == "completed"
    contradictory = dict(
        receipt,
        observed_facts=dict(receipt["observed_facts"], final_action_clicked=False),
    )
    # Shape check alone (field types only) does not catch this.
    check_receipt_shape(contradictory)

    decision = reduce_content_ops_browser_receipt(
        item=item, action_request=request, receipt=contradictory
    )
    assert decision["decision"] == "rejected_inconsistent_facts"
    assert decision["proposed_event"] is None


def test_stopped_at_gate_receipt_with_final_action_clicked_is_rejected() -> None:
    """A different stop_reason's invariant: stopped_at_gate claims the final
    submit control was *not* clicked -- a receipt claiming both cannot be
    trusted."""

    item = _item()
    request = build_content_ops_browser_action_request(item=item, goal_id=GOAL_ID, todo_id=TODO_ID)
    provider = FakeComputerUseProvider(
        {"screen_reachable": True}, session_reference="fake_session_facts_2"
    )
    receipt = provider.attempt(request)
    assert receipt["stop_reason"] == "stopped_at_gate"
    contradictory = dict(
        receipt,
        observed_facts=dict(receipt["observed_facts"], final_action_clicked=True),
    )
    check_receipt_shape(contradictory)

    decision = reduce_content_ops_browser_receipt(
        item=item, action_request=request, receipt=contradictory
    )
    assert decision["decision"] == "rejected_inconsistent_facts"
    assert decision["proposed_event"] is None


def _run_cli(args: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", "content-ops", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode in (0, 1), result.stderr
    return json.loads(result.stdout)


def test_cli_drives_the_full_draft_until_gate_round_trip(tmp_path: Path) -> None:
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(_item()), encoding="utf-8")

    request_packet = _run_cli(
        [
            "item-browser-request",
            "--item-json",
            str(item_path),
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            TODO_ID,
            "--occurred-at",
            "2026-08-18T09:04:00+00:00",
        ],
        cwd=REPO_ROOT,
    )
    assert request_packet["ok"] is True
    assert request_packet["action_request"]["effect_class"] == "draft"
    assert "gate_binding" not in request_packet["action_request"]
    assert request_packet["expected_transition"] == {
        "expected_state": "captured",
        "expected_revision": 1,
    }
    assert request_packet["item"]["state"] == "captured", "draft path never writes"

    # Write the *full* item-browser-request packet (not just the inner
    # action_request) -- this is the preferred path, since it carries
    # expected_transition and stays replay-safe even after the item moves on.
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request_packet), encoding="utf-8")

    provider = FakeComputerUseProvider(
        {"screen_reachable": True}, session_reference="fake_cli_session"
    )
    receipt = provider.attempt(request_packet["action_request"])
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    receipt_packet = _run_cli(
        [
            "item-browser-receipt",
            "--item-json",
            str(item_path),
            "--action-request-json",
            str(request_path),
            "--receipt-json",
            str(receipt_path),
            "--occurred-at",
            "2026-08-18T09:05:00+00:00",
        ],
        cwd=REPO_ROOT,
    )
    assert receipt_packet["ok"] is True
    assert receipt_packet["decision"] == "propose_submit_review"
    assert receipt_packet["item"]["state"] == "review_ready"
    assert receipt_packet["transition_receipt"]["status"] == "applied"

    # Same receipt replayed (same idempotency_key -> same event_id) must be a
    # no-op -- using the *same* occurred_at as the first call, since a retry
    # of the same physical attempt is one event happening once, not two.
    updated_item_path = tmp_path / "item_after.json"
    updated_item_path.write_text(
        json.dumps(receipt_packet["item"]), encoding="utf-8"
    )
    replay_packet = _run_cli(
        [
            "item-browser-receipt",
            "--item-json",
            str(updated_item_path),
            "--action-request-json",
            str(request_path),
            "--receipt-json",
            str(receipt_path),
            "--occurred-at",
            "2026-08-18T09:05:00+00:00",
        ],
        cwd=REPO_ROOT,
    )
    assert replay_packet["ok"] is True
    assert replay_packet["transition_receipt"]["status"] == "already_applied"
    assert replay_packet["item"]["state"] == "review_ready"


def test_bare_action_request_retry_with_unrefreshed_item_is_safe_by_determinism() -> None:
    """A caller that never went through item-browser-request (no
    expected_transition sidecar) can still retry safely -- but only by
    resubmitting the exact same, unrefreshed item snapshot every time, which
    converges to the same result deterministically rather than by hitting
    apply_content_ops_item_event's idempotent-replay path."""

    item = _item()
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True}, session_reference="fake_bare_session"
    )
    receipt = provider.attempt(request)

    first = apply_content_ops_browser_receipt(
        item=item, action_request=request, receipt=receipt, occurred_at="2026-08-18T09:05:00+00:00"
    )
    assert first["ok"] is True
    assert first["transition_receipt"]["status"] == "applied"
    assert first["item"]["state"] == "review_ready"

    # Retry against the *same* pre-transition `item` (not first["item"]), with
    # the *same* occurred_at -- a retry describes one event happening once,
    # not a second, later one.
    second = apply_content_ops_browser_receipt(
        item=item, action_request=request, receipt=receipt, occurred_at="2026-08-18T09:05:00+00:00"
    )
    assert second["ok"] is True
    assert second["item"]["state"] == "review_ready"
    assert second["item"] == first["item"], "deterministic reapplication converges to the same item"


def test_bare_action_request_retry_against_a_refreshed_item_is_rejected_not_silently_wrong() -> None:
    """The unsafe half of the bare-path characterization: a caller that DOES
    refresh --item-json between retries (a very plausible, arguably more
    correct calling pattern) does not get silent corruption or a spurious
    already_applied -- it gets a loud, clear error, because
    reduce_content_ops_browser_receipt recomputes expected_state from
    whatever item it is given, and that recomputed value now disagrees with
    what was actually stored in last_event."""

    item = _item()
    request = build_content_ops_browser_action_request(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        {"screen_reachable": True}, session_reference="fake_bare_session_2"
    )
    receipt = provider.attempt(request)

    first = apply_content_ops_browser_receipt(
        item=item, action_request=request, receipt=receipt, occurred_at="2026-08-18T09:05:00+00:00"
    )
    assert first["item"]["state"] == "review_ready"

    with pytest.raises(ValueError, match="different content"):
        apply_content_ops_browser_receipt(
            item=first["item"],  # refreshed: already reflects the first transition
            action_request=request,
            receipt=receipt,
            occurred_at="2026-08-18T09:06:00+00:00",
        )
