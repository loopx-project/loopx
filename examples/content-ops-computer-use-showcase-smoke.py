#!/usr/bin/env python3
"""Hermetic end-to-end showcase for content-ops's computer_use_runtime_v0 slice.

This is the vertical-slice showcase promised by #3110: a standard `content-ops`
item drives a bounded computer-use action request, a provider attempts it and
returns a compact receipt, and content-ops's own capability-local reducer
decides what (if anything) happens to the item -- never the provider.

Two scenarios per the maintainer's acceptance bar:

- draft-until-gate: fill approved fields, stop before the final submit
  control, and land in `review_ready` (not published).
- unknown modal: an unrecognized modal appears; hand off without confirming,
  dismissing, or changing item state.

Plus the two race-condition acceptance checks called out in review:

- a stale gate revision (an approval that was revoked and re-approved without
  any content change) must be rejected, not silently honored;
- the exact same receipt (same idempotency_key) replayed against the
  already-updated item must be idempotent, not double-applied or rejected as
  a conflicting reuse.

CI runs this in fully hermetic mode: FakeComputerUseProvider is a declarative
stand-in driven by the fixtures in
examples/content-ops-computer-use-showcase-fixtures/, so no real browser,
model call, or network access is used anywhere here. A contributor can swap
in a real host browser/CUA tool locally (anything implementing the same
ComputerUseProvider protocol) to collect real-world evidence; that swap
changes nothing about content-ops's own code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.content_ops.computer_use_provider import (  # noqa: E402
    ContentOpsCuaContractViolation,
    FakeComputerUseProvider,
    build_content_ops_browser_action_request,
    build_content_ops_browser_action_request_packet,
)
from loopx.capabilities.content_ops.computer_use_reducer import (  # noqa: E402
    apply_content_ops_browser_receipt,
)
from loopx.capabilities.content_ops.item_lifecycle import (  # noqa: E402
    apply_content_ops_item_event,
    build_content_ops_item,
)

FIXTURES_DIR = REPO_ROOT / "examples" / "content-ops-computer-use-showcase-fixtures"
GOAL_ID = "loopx-content-ops-cua-showcase"
TODO_ID = "todo_content_ops_cua_showcase"
DIGEST = "sha256:" + "7" * 64


def _load_ui_state(case_id: str) -> dict[str, Any]:
    payload = json.loads((FIXTURES_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
    return dict(payload["ui_state"])


def _fresh_item() -> dict[str, Any]:
    return build_content_ops_item(
        item_id="cua-showcase-post",
        item_kind="post",
        channel="x",
        content_digest=DIGEST,
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


def run_draft_until_gate_scenario() -> None:
    item = _fresh_item()
    request_packet = build_content_ops_browser_action_request_packet(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID, occurred_at="2026-08-18T09:04:00+00:00"
    )
    request = request_packet["action_request"]
    assert request["effect_class"] == "draft"
    assert "gate_binding" not in request, "nothing is approved yet; no gate to bind"

    provider = FakeComputerUseProvider(
        _load_ui_state("01_draft_until_gate"), session_reference="showcase_session_draft"
    )
    receipt = provider.attempt(request)
    assert receipt["stop_reason"] == "stopped_at_gate"

    packet = apply_content_ops_browser_receipt(
        item=item,
        action_request=request,
        receipt=receipt,
        occurred_at="2026-08-18T09:05:00+00:00",
        expected_transition=request_packet["expected_transition"],
    )
    assert packet["ok"] is True
    assert packet["decision"] == "propose_submit_review"
    assert packet["item"]["state"] == "review_ready", "must stop at the gate, not publish"

    # Idempotent replay of the exact same receipt, against the *refreshed*
    # item -- safe because expected_transition stays pinned to request-build
    # time, so apply_content_ops_item_event's own digest check recognizes
    # this as the same event rather than a conflicting reuse. Same
    # occurred_at as the first call: a retry is one event happening once.
    replay = apply_content_ops_browser_receipt(
        item=packet["item"],
        action_request=request,
        receipt=receipt,
        occurred_at="2026-08-18T09:05:00+00:00",
        expected_transition=request_packet["expected_transition"],
    )
    assert replay["ok"] is True
    assert replay["transition_receipt"]["status"] == "already_applied"
    assert replay["item"]["state"] == "review_ready"


def run_unknown_modal_scenario() -> None:
    item = _fresh_item()
    request_packet = build_content_ops_browser_action_request_packet(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID, occurred_at="2026-08-18T09:04:00+00:00"
    )
    request = request_packet["action_request"]
    provider = FakeComputerUseProvider(
        _load_ui_state("02_unknown_modal"), session_reference="showcase_session_modal"
    )
    receipt = provider.attempt(request)
    assert receipt["stop_reason"] == "blocked_by_unknown_modal"

    packet = apply_content_ops_browser_receipt(
        item=item,
        action_request=request,
        receipt=receipt,
        occurred_at="2026-08-18T09:05:00+00:00",
        expected_transition=request_packet["expected_transition"],
    )
    assert packet["ok"] is True
    assert packet["decision"] == "handoff_blocked"
    assert "blocker" in packet
    assert packet["item"]["state"] == "captured", (
        "an unrecognized modal must hand off, not click through or change item state"
    )


def run_stale_gate_revision_is_rejected_scenario() -> None:
    item = apply_content_ops_item_event(
        _fresh_item(),
        {
            "event_id": "event-review",
            "action": "submit_review",
            "expected_state": "captured",
            "expected_revision": 1,
            "occurred_at": "2026-08-18T09:04:00+00:00",
            "payload": {},
        },
    )["item"]
    item = _approve(item, event_id="event-approve-1", approval_ref="decision:showcase-1")
    # Durably declares delivery intent (approved -> delivery_ready) before
    # returning a request -- item moves on immediately, use the packet's item.
    stale_request_packet = build_content_ops_browser_action_request_packet(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID, occurred_at="2026-08-18T09:12:00+00:00"
    )
    stale_request = stale_request_packet["action_request"]
    assert stale_request["gate_binding"]["revision"] == 1

    # Content never changes, but the approval is revoked (allowed from
    # delivery_ready) and re-granted -- only approval_sequence (not
    # item.revision) can tell the two apart.
    revoked = apply_content_ops_item_event(
        stale_request_packet["item"],
        {
            "event_id": "event-revoke",
            "action": "revoke_approval",
            "expected_state": "delivery_ready",
            "expected_revision": item["revision"],
            "occurred_at": "2026-08-18T09:15:00+00:00",
            "payload": {"reason": "owner wants another look before publish"},
        },
    )["item"]
    reapproved = _approve(revoked, event_id="event-approve-2", approval_ref="decision:showcase-2")
    assert reapproved["approval_sequence"] == 2

    provider = FakeComputerUseProvider(
        _load_ui_state("03_approved_write"), session_reference="showcase_session_stale"
    )
    stale_receipt = provider.attempt(stale_request)
    assert stale_receipt["stop_reason"] == "completed"

    packet = apply_content_ops_browser_receipt(
        item=reapproved,
        action_request=stale_request,
        receipt=stale_receipt,
        occurred_at="2026-08-18T09:20:00+00:00",
        expected_transition=stale_request_packet["expected_transition"],
    )
    assert packet["ok"] is False
    assert packet["decision"] == "rejected_stale_gate"
    assert packet["item"]["state"] == reapproved["state"], "a rejected receipt must not touch state"


def run_approved_write_under_current_gate_scenario() -> None:
    item = apply_content_ops_item_event(
        _fresh_item(),
        {
            "event_id": "event-review",
            "action": "submit_review",
            "expected_state": "captured",
            "expected_revision": 1,
            "occurred_at": "2026-08-18T09:04:00+00:00",
            "payload": {},
        },
    )["item"]
    item = _approve(item, event_id="event-approve-1", approval_ref="decision:showcase-3")
    # The durable fence: delivery intent is declared (approved -> delivery_ready)
    # right here, *before* the provider is ever invoked below -- not after a
    # receipt comes back. The packet's `item` reflects that transition; a real
    # caller MUST persist it before calling a provider with `action_request`.
    request_packet = build_content_ops_browser_action_request_packet(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID, occurred_at="2026-08-18T09:12:00+00:00"
    )
    request = request_packet["action_request"]
    assert request_packet["item"]["state"] == "delivery_ready"

    provider = FakeComputerUseProvider(
        _load_ui_state("03_approved_write"), session_reference="showcase_session_write"
    )
    receipt = provider.attempt(request)
    assert receipt["stop_reason"] == "completed"

    packet = apply_content_ops_browser_receipt(
        item=request_packet["item"],
        action_request=request,
        receipt=receipt,
        occurred_at="2026-08-18T09:20:00+00:00",
        expected_transition=request_packet["expected_transition"],
    )
    assert packet["ok"] is True
    assert packet["decision"] == "confirmed_external_write_attempted"
    assert packet["item"]["state"] == "delivery_ready", (
        "unchanged -- there is nothing left to apply. The durable fence already "
        "happened before the provider was invoked above, not here; recording "
        "the real delivery stays with content-ops's existing readback tooling, "
        "since the receipt schema has no public_url field to carry it"
    )

    # Negative case: restart after the provider already executed. Even
    # without ever processing the receipt above, a fresh call using only the
    # durably-persisted item must not be able to issue a second external-write
    # request for the same approval.
    try:
        build_content_ops_browser_action_request_packet(
            item=request_packet["item"],
            goal_id=GOAL_ID,
            todo_id=TODO_ID,
            occurred_at="2026-08-18T09:25:00+00:00",
        )
    except ContentOpsCuaContractViolation:
        pass
    else:
        raise AssertionError(
            "a delivery_ready item must not be able to issue a second "
            "external_write action request for the same approval"
        )


def _approved_delivery_ready_showcase_item(item_id: str) -> dict[str, Any]:
    item = build_content_ops_item(
        item_id=item_id,
        item_kind="post",
        channel="x",
        content_digest=DIGEST,
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
            "occurred_at": "2026-08-18T09:04:00+00:00",
            "payload": {},
        },
    )["item"]
    item = _approve(item, event_id="event-approve", approval_ref=f"decision:{item_id}")
    packet = build_content_ops_browser_action_request_packet(
        item=item, goal_id=GOAL_ID, todo_id=TODO_ID, occurred_at="2026-08-18T09:12:00+00:00"
    )
    return packet["item"]


def run_cross_item_receipt_is_rejected_scenario() -> None:
    """Two different items, both delivery_ready on their first approval (a
    plausible, ordinary coincidence -- same approval_sequence). A request and
    receipt built for one item must not be able to advance the other, even
    though the gate revision numbers happen to line up."""

    # Both items are already delivery_ready (the helper declares intent as
    # part of reaching that state); use the pure, lower-level builder here to
    # get item A's request without tripping the packet's own already-declared
    # refusal (that refusal is exactly what the earlier scenario tests).
    item_a = _approved_delivery_ready_showcase_item("cua-showcase-item-a")
    item_b = _approved_delivery_ready_showcase_item("cua-showcase-item-b")
    assert item_a["approval_sequence"] == item_b["approval_sequence"] == 1

    request_for_a = build_content_ops_browser_action_request(
        item=item_a, goal_id=GOAL_ID, todo_id=TODO_ID
    )
    provider = FakeComputerUseProvider(
        _load_ui_state("03_approved_write"), session_reference="showcase_session_cross_item"
    )
    receipt_for_a = provider.attempt(request_for_a)
    assert receipt_for_a["stop_reason"] == "completed"

    packet = apply_content_ops_browser_receipt(
        item=item_b,
        action_request=request_for_a,
        receipt=receipt_for_a,
        occurred_at="2026-08-18T09:20:00+00:00",
        expected_item_id=item_a["item_id"],
    )
    assert packet["ok"] is False
    assert packet["decision"] == "rejected_item_mismatch"
    assert packet["item"]["state"] == "delivery_ready", "item B must be completely untouched"


def main() -> int:
    run_draft_until_gate_scenario()
    run_unknown_modal_scenario()
    run_stale_gate_revision_is_rejected_scenario()
    run_approved_write_under_current_gate_scenario()
    run_cross_item_receipt_is_rejected_scenario()
    print(
        "content-ops-computer-use-showcase ok "
        "scenarios=draft_until_gate,unknown_modal,stale_gate_rejected,"
        "approved_write,cross_item_rejected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
