"""content-ops's capability-local reducer for computer_use_runtime_v0 receipts.

Per the protocol, the reducer is deliberately not specified by the contract
itself -- it is capability-local, combining a receipt with the action request
that carried the current gate binding, plus domain policy that only
content-ops knows. This module never writes LoopX state directly: it returns
a *proposed* content-ops item-lifecycle event (or no event at all), and the
caller applies it through the existing, already-tested
``apply_content_ops_item_event`` -- the same optimistic-concurrency path used
by every other content-ops writer.

Gate identity note: ``gate_binding.revision`` is bound to the item's
``approval_sequence`` counter (loopx/capabilities/content_ops/item_lifecycle.py),
not to the item's content ``revision``. The two are independent: content
``revision`` only changes on ``revise``, but ``approval_sequence`` advances on
every ``approve`` -- including a re-approval that follows a ``revoke_approval``
with no content change in between. Reusing content ``revision`` for gate
staleness would let a revoked-then-reapproved gate collide with the revoked
one whenever the content itself never changed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any, Literal

from .computer_use_provider import (
    check_action_request_shape,
    check_receipt_matches_request,
    check_receipt_shape,
)
from .item_lifecycle import (
    apply_content_ops_item_event,
    project_content_ops_item,
    require_content_ops_item,
)
from .schemas import CONTENT_OPS_BROWSER_RECEIPT_PACKET_SCHEMA_VERSION

_EVENT_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

ReducerDecision = Literal[
    "propose_submit_review",
    "confirmed_external_write_attempted",
    "handoff_blocked",
    "rejected_stale_gate",
    "rejected_item_mismatch",
    "rejected_inconsistent_facts",
]

_OBSERVED_FACT_INVARIANTS: dict[str, tuple[tuple[str, bool], ...]] = {
    # stop_reason -> required (observed_facts key, required value) pairs.
    # Anything not listed for a key is unconstrained for that stop_reason.
    "completed": (
        ("screen_reached", True),
        ("final_action_clicked", True),
        ("unknown_modal", False),
    ),
    "stopped_at_gate": (
        ("screen_reached", True),
        ("final_action_clicked", False),
    ),
    "blocked_by_unknown_modal": (
        ("unknown_modal", True),
        ("final_action_clicked", False),
    ),
    "failed": (
        ("screen_reached", False),
    ),
}


def _check_observed_facts_consistent(
    *, stop_reason: str, observed_facts: Mapping[str, Any]
) -> str | None:
    """A receipt's own observed_facts must not contradict its stop_reason --
    e.g. stop_reason='completed' with final_action_clicked=false. Wire schema
    validation (check_receipt_shape) only checks field types; this is the
    domain-level judgment call the protocol leaves to content-ops's reducer.
    Returns a violation reason, or None if consistent."""

    for key, required in _OBSERVED_FACT_INVARIANTS.get(stop_reason, ()):
        if observed_facts[key] is not required:
            return (
                f"stop_reason={stop_reason!r} is inconsistent with "
                f"observed_facts.{key}={observed_facts[key]!r} (expected {required!r})"
            )
    return None


def reduce_content_ops_browser_receipt(
    *,
    item: Mapping[str, Any],
    action_request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    expected_item_id: str | None = None,
) -> dict[str, Any]:
    """Turn one computer_use_receipt_v0 into a proposed content-ops transition.

    Returns a dict with ``decision``, ``reason``, and either
    ``proposed_event`` (to be applied via ``apply_content_ops_item_event``)
    or ``blocker`` (a human-facing report; no state change is proposed).

    ``expected_item_id``, when supplied, must equal the current item's own
    ``item_id`` -- this is the general-purpose defense against a
    request/receipt built for a *different* item being fed into this item's
    processing path (see ``apply_content_ops_browser_receipt`` for how it is
    sourced from the item-browser-request packet's outer ``item_id``).
    Separately, and unconditionally regardless of ``expected_item_id``, a
    ``gate_binding`` (only present for external_write/credential_use) is
    checked against a gate id *derived from this item* -- gate_binding.gate_id
    already encodes item_id (see build_content_ops_browser_action_request),
    so this closes the same hole even for a bare/hand-built request that
    never went through the packet path at all.
    """

    check_action_request_shape(action_request)
    check_receipt_shape(receipt)
    check_receipt_matches_request(receipt, action_request)
    # Normalize/validate the item before reading any field from it -- same
    # requirement as computer_use_provider.py's request builder, and for the
    # same reason: a caller handing in an item straight from JSON (e.g. the
    # item-browser-receipt CLI, which does no validation of its own) must not
    # hit a raw KeyError on a legacy item missing approval_sequence.
    item = require_content_ops_item(item)

    if expected_item_id is not None and item["item_id"] != expected_item_id:
        return {
            "decision": "rejected_item_mismatch",
            "reason": (
                f"expected_item_id={expected_item_id!r} does not match this item's "
                f"item_id={item['item_id']!r}; refusing to interpret a request/receipt "
                "that was not built for this item"
            ),
            "proposed_event": None,
        }

    gate_binding = action_request.get("gate_binding")
    if gate_binding is not None:
        expected_gate_id = f"gate_{item['item_id']}_publish"
        if gate_binding["gate_id"] != expected_gate_id:
            return {
                "decision": "rejected_item_mismatch",
                "reason": (
                    f"action_request gate_binding.gate_id={gate_binding['gate_id']!r} was "
                    f"not derived from this item (expected {expected_gate_id!r}); refusing "
                    "to let a gate/approval authorized for a different item advance this one"
                ),
                "proposed_event": None,
            }
        current_approval_sequence = int(item["approval_sequence"])
        requested_revision = int(gate_binding["revision"])
        if requested_revision != current_approval_sequence:
            return {
                "decision": "rejected_stale_gate",
                "reason": (
                    f"action_request gate_binding.revision={requested_revision} does not "
                    f"match the item's current approval_sequence={current_approval_sequence}; "
                    "the approval this request was authorized under has since been revoked "
                    "and/or replaced"
                ),
                "proposed_event": None,
            }

    stop_reason = receipt["stop_reason"]
    inconsistency = _check_observed_facts_consistent(
        stop_reason=stop_reason, observed_facts=receipt["observed_facts"]
    )
    if inconsistency is not None:
        return {
            "decision": "rejected_inconsistent_facts",
            "reason": (
                f"receipt is internally inconsistent and cannot be trusted: {inconsistency}"
            ),
            "proposed_event": None,
        }

    if stop_reason == "blocked_by_unknown_modal":
        return {
            "decision": "handoff_blocked",
            "reason": (
                "provider reported an unrecognized modal and stopped rather than "
                "clicking through it; hand off to a human"
            ),
            "proposed_event": None,
            "blocker": {
                "item_id": item["item_id"],
                "attempted_action_unit": receipt["attempted_action_unit"],
                "evidence_handle": receipt["evidence"]["handle_kind"],
                "session_reference": receipt["session_reference"],
            },
        }

    if stop_reason == "failed":
        return {
            "decision": "handoff_blocked",
            "reason": "provider could not reach the target screen",
            "proposed_event": None,
            "blocker": {
                "item_id": item["item_id"],
                "attempted_action_unit": receipt["attempted_action_unit"],
                "evidence_handle": receipt["evidence"]["handle_kind"],
                "session_reference": receipt["session_reference"],
            },
        }

    if stop_reason == "stopped_at_gate":
        return {
            "decision": "propose_submit_review",
            "reason": "provider stopped at the final submit control as instructed",
            "proposed_event": {
                "action": "submit_review",
                "expected_state": item["state"],
                "expected_revision": item["revision"],
                "payload": {},
            },
        }

    if stop_reason == "completed":
        if action_request.get("effect_class") != "external_write":
            raise ValueError(
                "a 'completed' receipt for a request whose effect_class is not "
                "external_write is out of contract for this reducer"
            )
        if item["state"] != "delivery_ready":
            # The gate revision matched (checked above), but the item is not
            # currently delivery_ready -- it moved on through some other means
            # since this request was issued (e.g. an existing-tooling
            # record_delivery already ran). A stale replay of an old completed
            # receipt, not a live one; treat it the same as a stale gate rather
            # than acting on it.
            return {
                "decision": "rejected_stale_gate",
                "reason": (
                    f"item state is {item['state']!r}, not 'delivery_ready'; this "
                    "completed receipt no longer answers the item's live attempt"
                ),
                "proposed_event": None,
            }
        return {
            "decision": "confirmed_external_write_attempted",
            "reason": (
                "provider reports the approved write completed. The durable fence "
                "against a duplicate external effect already happened *before* this "
                "receipt -- build_content_ops_browser_action_request_packet declared "
                "delivery intent (approved -> delivery_ready) before ever returning a "
                "request a provider could act on, and that transition cannot repeat "
                "for the same approval. This receipt itself proposes no further "
                "item-lifecycle transition: recording a durable public_url/receipt_ref "
                "stays content-ops's existing readback-verified delivery flow, since "
                "the receipt schema has no field for it"
            ),
            "proposed_event": None,
        }

    raise ValueError(f"unsupported receipt.stop_reason {stop_reason!r}")


def _event_id_from_idempotency_key(idempotency_key: str) -> str:
    """content-ops item events require an opaque token id; a receipt's
    idempotency_key is a looser 1-200 char string with no such constraint.
    Reuse it directly when it already fits, otherwise derive a stable token
    so the same idempotency_key always maps to the same event_id and a
    provider retry lands on content-ops's existing idempotent-replay path."""

    if _EVENT_ID_TOKEN_RE.fullmatch(idempotency_key):
        return idempotency_key
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"cua_receipt_{digest}"


_REJECTED_DECISIONS = frozenset(
    {"rejected_stale_gate", "rejected_item_mismatch", "rejected_inconsistent_facts"}
)


def apply_content_ops_browser_receipt(
    *,
    item: Mapping[str, Any],
    action_request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    occurred_at: str,
    expected_transition: Mapping[str, Any] | None = None,
    expected_item_id: str | None = None,
) -> dict[str, Any]:
    """CLI-facing wrapper: reduce a receipt and, if it proposes one, apply the
    transition through the existing, already-tested item-lifecycle event path.

    A rejected receipt (stale gate, wrong item, or internally inconsistent
    facts) or a handoff never touches item state; the caller gets back the
    item exactly as it was.

    ``expected_item_id`` should be the outer ``item_id`` from the
    ``item-browser-request`` packet that produced ``action_request`` -- see
    ``reduce_content_ops_browser_receipt`` for what it protects against. The
    preferred (packet-based) CLI path always supplies it; only a caller using
    a bare/hand-built ``action_request`` goes without this specific check
    (the unconditional gate_id-derivation check inside the reducer still
    applies for external_write/credential_use regardless).

    ``expected_transition`` should be the ``expected_transition`` sidecar from
    the ``item-browser-request`` packet that produced ``action_request`` --
    i.e. the item's state/revision *at request-build time*, not whatever the
    item's state happens to be right now. Passing it makes a receipt replay
    safe even after the item has already moved on: the same
    idempotency_key always builds the exact same event body, so
    ``apply_content_ops_item_event``'s own digest-based idempotent-replay
    check (not a bespoke one here) is what recognizes the retry and no-ops it.

    Without it (a caller that hand-built ``action_request`` without going
    through ``item-browser-request``), expected_state/expected_revision are
    read from ``item`` fresh on every call. That is only replay-safe if the
    caller keeps re-submitting the *same, unrefreshed* item snapshot on every
    retry (safe by determinism, not by the idempotent-replay path); a caller
    that refreshes the item between retries will get a clear
    "reused with different content" error rather than a silently wrong
    result -- see test_bare_action_request_retry_against_a_refreshed_item_is_rejected_not_silently_wrong.
    """

    # Normalize once: echoed back below (and handed to apply_content_ops_item_event
    # further down, which normalizes internally regardless) as the canonical
    # form, the same "heal on every read/write" behavior every other
    # content-ops writer already has -- a legacy item's missing
    # approval_sequence gets filled in going forward rather than being
    # tolerated forever on every subsequent call.
    item = require_content_ops_item(item)
    decision = reduce_content_ops_browser_receipt(
        item=item,
        action_request=action_request,
        receipt=receipt,
        expected_item_id=expected_item_id,
    )
    base = {
        "schema_version": CONTENT_OPS_BROWSER_RECEIPT_PACKET_SCHEMA_VERSION,
        "decision": decision["decision"],
        "reason": decision["reason"],
    }
    if "blocker" in decision:
        base["blocker"] = decision["blocker"]

    if decision["decision"] in _REJECTED_DECISIONS:
        return {
            **base,
            "ok": False,
            "item": dict(item),
            "projection": project_content_ops_item(item),
        }

    if decision["proposed_event"] is None:
        return {
            **base,
            "ok": True,
            "item": dict(item),
            "projection": project_content_ops_item(item),
        }

    proposed_event = dict(decision["proposed_event"])
    if expected_transition is not None:
        proposed_event["expected_state"] = expected_transition["expected_state"]
        proposed_event["expected_revision"] = expected_transition["expected_revision"]

    event = {
        "event_id": _event_id_from_idempotency_key(str(receipt["idempotency_key"])),
        "occurred_at": occurred_at,
        **proposed_event,
    }
    transition_packet = apply_content_ops_item_event(item, event)
    return {
        **base,
        "ok": True,
        "item": transition_packet["item"],
        "projection": transition_packet["projection"],
        "transition_receipt": transition_packet["receipt"],
    }


def render_content_ops_browser_receipt_markdown(packet: Mapping[str, Any]) -> str:
    raw_projection = packet.get("projection")
    projection: Mapping[str, Any] = raw_projection if isinstance(raw_projection, Mapping) else {}
    lines = [
        "# LoopX Content-Ops Browser Receipt",
        "",
        f"- ok: `{packet.get('ok')}`",
        f"- decision: `{packet.get('decision')}`",
        f"- reason: {packet.get('reason')}",
        f"- item_id: `{projection.get('item_id')}`",
        f"- state: `{projection.get('state')}`",
    ]
    raw_blocker = packet.get("blocker")
    blocker: Mapping[str, Any] | None = raw_blocker if isinstance(raw_blocker, Mapping) else None
    if blocker:
        lines.extend(
            [
                "",
                "## Blocker",
                "",
                f"- attempted_action_unit: `{blocker.get('attempted_action_unit')}`",
                f"- evidence_handle: `{blocker.get('evidence_handle')}`",
            ]
        )
    raw_transition_receipt = packet.get("transition_receipt")
    transition_receipt: Mapping[str, Any] | None = (
        raw_transition_receipt if isinstance(raw_transition_receipt, Mapping) else None
    )
    if transition_receipt:
        lines.extend(
            [
                "",
                "## Transition",
                "",
                f"- transition_status: `{transition_receipt.get('status')}`",
                f"- transition: `{transition_receipt.get('from_state')} -> {transition_receipt.get('to_state')}`",
            ]
        )
    return "\n".join(lines) + "\n"
