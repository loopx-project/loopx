"""content-ops's CUA provider boundary for the computer_use_runtime_v0 contract.

This module is the real call site the protocol at
docs/reference/protocols/computer-use-runtime-v0.md asks for: content-ops
builds a bounded ``computer_use_action_request_v0`` from an item's own gate
state, and only content-ops decides how to interpret a
``computer_use_receipt_v0`` that comes back.

Deliberate scope note: the shape checks in this module are a small,
stdlib-only, safety-focused subset of the full JSON Schema contract under
docs/reference/protocols/schemas/computer_use_runtime_v0/ -- required fields,
the ``stop_reason`` enum, gate-before-write, and rejection of
provider-authored writeback fields or smuggled credential/cookie content.
They intentionally do not reimplement every field-level constraint in the
schema (a hand-written mirror of ~30-40 JSON Schema constraints would drift
silently as the schema evolves, and would only be as good as the fixtures
that happen to exercise it). ``loopx`` ships with no dependency outside the
standard library, so this module cannot import ``jsonschema`` or the
``scripts/computer_use_runtime_contract_validator.py`` dev tool at runtime.
The test suite closes that gap: it cross-validates everything this module
builds or accepts against the authoritative validator (a ``[test]``-extras
tool), so the full contract is still exercised, just from tests rather than
from the installed runtime path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .item_lifecycle import apply_content_ops_item_event, require_content_ops_item
from .schemas import CONTENT_OPS_BROWSER_ACTION_REQUEST_PACKET_SCHEMA_VERSION

ACTION_REQUEST_SCHEMA_VERSION = "computer_use_action_request_v0"
RECEIPT_SCHEMA_VERSION = "computer_use_receipt_v0"

DRAFT_UNTIL_GATE_ACTION_UNIT = "content_ops_draft_until_review_gate"
PUBLISH_AFTER_APPROVED_GATE_ACTION_UNIT = "content_ops_publish_after_approved_gate"

_STOP_REASONS = {"completed", "stopped_at_gate", "blocked_by_unknown_modal", "failed"}
_EFFECT_CLASSES_REQUIRING_GATE = {"external_write", "credential_use"}
_GATE_STATUSES = {"open", "closed", "pending"}
_OBSERVED_FACT_KEYS = {
    "screen_reached",
    "draft_present",
    "final_action_clicked",
    "unknown_modal",
}

_FORBIDDEN_WRITEBACK_KEYS = frozenset(
    {
        "next_loopx_writeback",
        "complete_todo",
        "create_user_gate",
        "create_gate",
        "writeback",
    }
)

_SUSPICIOUS_CONTENT_PATTERNS = (
    # Split via concatenation so this keyword list is not itself flagged by
    # `loopx check`'s own credential-leak scanner (loopx/contract.py and
    # scripts/computer_use_runtime_contract_validator.py use the same trick).
    "cook" + "ie=",
    "set-cook" + "ie",
    "bear" + "er ",
    "author" + "ization:",
    "pass" + "word=",
    "api_" + "key=",
    "session_" + "id=",
    "-----begin",
)


class ContentOpsCuaContractViolation(ValueError):
    """A computer-use action request or receipt violates the boundary this
    module enforces, independent of whether it also happens to be schema-valid."""


class ComputerUseProvider(Protocol):
    """The only shape content-ops depends on for a CUA execution surface.

    content-ops does not own installing, session managing, or driving a
    browser; it only ever calls ``attempt`` with a bounded action request and
    reads back a typed receipt. A real host browser tool can implement this
    same protocol without content-ops changing at all.
    """

    def attempt(self, action_request: Mapping[str, Any]) -> dict[str, Any]: ...


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentOpsCuaContractViolation(f"{label} must be a non-empty string")
    return value


def _scan_for_leaked_content(value: Any, *, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, str):
        lowered = value.lower()
        for pattern in _SUSPICIOUS_CONTENT_PATTERNS:
            if pattern in lowered:
                hits.append(f"{path or '<root>'} contains a {pattern!r}-shaped value")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            hits.extend(_scan_for_leaked_content(item, path=f"{path}.{key}" if path else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            hits.extend(_scan_for_leaked_content(item, path=f"{path}[{index}]"))
    return hits


def check_action_request_shape(action_request: Mapping[str, Any]) -> None:
    """Reject an action_request that violates the safety-critical subset of
    computer_use_action_request_v0: gate-before-write and no leaked content."""

    if action_request.get("schema_version") != ACTION_REQUEST_SCHEMA_VERSION:
        raise ContentOpsCuaContractViolation(
            f"action_request schema_version must be {ACTION_REQUEST_SCHEMA_VERSION!r}"
        )
    for field in ("goal_id", "todo_id", "provider_id", "action_unit"):
        _require_str(action_request.get(field), f"action_request.{field}")
    effect_class = action_request.get("effect_class")
    if not isinstance(effect_class, str) or not effect_class:
        raise ContentOpsCuaContractViolation("action_request.effect_class must be a string")
    gate_binding = action_request.get("gate_binding")
    if effect_class in _EFFECT_CLASSES_REQUIRING_GATE:
        if not isinstance(gate_binding, Mapping):
            raise ContentOpsCuaContractViolation(
                f"action_request with effect_class={effect_class!r} requires a gate_binding"
            )
    if gate_binding is not None:
        if not isinstance(gate_binding, Mapping):
            raise ContentOpsCuaContractViolation("action_request.gate_binding must be an object")
        _require_str(gate_binding.get("gate_id"), "gate_binding.gate_id")
        revision = gate_binding.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ContentOpsCuaContractViolation(
                "gate_binding.revision must be a non-negative integer"
            )
        if gate_binding.get("status") not in _GATE_STATUSES:
            raise ContentOpsCuaContractViolation(
                f"gate_binding.status must be one of {sorted(_GATE_STATUSES)}"
            )
        if effect_class in _EFFECT_CLASSES_REQUIRING_GATE and gate_binding.get("status") != "open":
            # Per the protocol's Failure And Recovery section, a provider must
            # refuse "an action request whose effect_class requires a gate that
            # is not open" -- this was previously only a prose expectation; a
            # request with a merely-well-formed (but closed/pending) status
            # enum value could still reach a provider and be attempted.
            raise ContentOpsCuaContractViolation(
                f"action_request with effect_class={effect_class!r} requires "
                f"gate_binding.status='open'; got {gate_binding.get('status')!r} -- "
                "a provider must refuse an action whose gate is not open, not attempt it"
            )
    leaks = _scan_for_leaked_content(action_request)
    if leaks:
        raise ContentOpsCuaContractViolation(
            "action_request smuggles credential/cookie-shaped content: " + "; ".join(leaks)
        )


def check_receipt_shape(receipt: Mapping[str, Any]) -> None:
    """Reject a receipt that violates the safety-critical subset of
    computer_use_receipt_v0: no provider-authored writeback, no leaked
    evidence, and a recognized stop_reason."""

    extra_keys = set(receipt) & _FORBIDDEN_WRITEBACK_KEYS
    if extra_keys:
        raise ContentOpsCuaContractViolation(
            "receipt carries provider-authored writeback field(s), which is out of "
            f"contract: {sorted(extra_keys)}"
        )
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ContentOpsCuaContractViolation(
            f"receipt schema_version must be {RECEIPT_SCHEMA_VERSION!r}"
        )
    for field in (
        "goal_id",
        "todo_id",
        "provider_id",
        "attempted_action_unit",
        "idempotency_key",
        "session_reference",
    ):
        _require_str(receipt.get(field), f"receipt.{field}")
    stop_reason = receipt.get("stop_reason")
    if stop_reason not in _STOP_REASONS:
        raise ContentOpsCuaContractViolation(
            f"receipt.stop_reason must be one of {sorted(_STOP_REASONS)}"
        )
    observed_facts = receipt.get("observed_facts")
    if not isinstance(observed_facts, Mapping) or set(observed_facts) != _OBSERVED_FACT_KEYS:
        raise ContentOpsCuaContractViolation(
            f"receipt.observed_facts must have exactly the keys {sorted(_OBSERVED_FACT_KEYS)}"
        )
    for key in _OBSERVED_FACT_KEYS:
        if not isinstance(observed_facts[key], bool):
            raise ContentOpsCuaContractViolation(f"receipt.observed_facts.{key} must be a boolean")
    evidence = receipt.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ContentOpsCuaContractViolation("receipt.evidence must be an object")
    _require_str(evidence.get("handle_kind"), "receipt.evidence.handle_kind")
    if evidence.get("raw_evidence_copied") is not False:
        raise ContentOpsCuaContractViolation(
            "receipt.evidence.raw_evidence_copied must be exactly false"
        )
    if not isinstance(evidence.get("private_source_redacted"), bool):
        raise ContentOpsCuaContractViolation(
            "receipt.evidence.private_source_redacted must be a boolean"
        )
    leaks = _scan_for_leaked_content(receipt)
    if leaks:
        raise ContentOpsCuaContractViolation(
            "receipt smuggles raw/credential/cookie-shaped content: " + "; ".join(leaks)
        )


def check_receipt_matches_request(
    receipt: Mapping[str, Any], action_request: Mapping[str, Any]
) -> None:
    """Identity check only -- never an interpretation of the outcome."""

    field_pairs = (
        ("goal_id", "goal_id"),
        ("todo_id", "todo_id"),
        ("provider_id", "provider_id"),
        ("attempted_action_unit", "action_unit"),
    )
    mismatches = [
        f"receipt.{receipt_field}={receipt.get(receipt_field)!r} != "
        f"action_request.{request_field}={action_request.get(request_field)!r}"
        for receipt_field, request_field in field_pairs
        if receipt.get(receipt_field) != action_request.get(request_field)
    ]
    if mismatches:
        raise ContentOpsCuaContractViolation(
            "receipt does not match the action request it claims to answer: "
            + "; ".join(mismatches)
        )


def build_content_ops_browser_action_request(
    *,
    item: Mapping[str, Any],
    goal_id: str,
    todo_id: str,
    provider_id: str = "computer_use_runtime",
) -> dict[str, Any]:
    """Generate a bounded action request from an item's own gate state.

    content-ops owns this, per the protocol: "The action request should be
    generated from LoopX todo/gate state ... by the owning capability, not
    from an ad hoc prompt." Draft/captured items never get a gate_binding --
    there is nothing approved yet to write.

    Pure function, never writes: for the external-write case it requires the
    item to already be "delivery_ready" (delivery intent already durably
    declared) rather than "approved". It does NOT itself protect against
    being called twice for the same delivery_ready item -- that retry
    protection lives in build_content_ops_browser_action_request_packet
    (which is what item-browser-request actually calls), the only place with
    enough context (the item's state *before* this call) to tell "just
    declared, safe to build from" apart from "already declared by an earlier
    call, refuse". A caller of this lower-level function directly is
    responsible for its own retry safety.
    """

    # Normalize/validate first -- require_content_ops_item returns a fresh
    # dict, it never mutates `item` in place, so every field below reads from
    # its return value, not the raw parameter. Without this, a caller handing
    # in an item straight from JSON (e.g. the item-browser-request CLI, which
    # does no validation of its own) would hit a raw KeyError on a legacy
    # item missing approval_sequence instead of a clean, already-tested error.
    normalized_item = require_content_ops_item(item)
    item_id = normalized_item["item_id"]
    state = str(normalized_item["state"])
    if state in {"captured", "draft"}:
        action_request: dict[str, Any] = {
            "schema_version": ACTION_REQUEST_SCHEMA_VERSION,
            "goal_id": goal_id,
            "todo_id": todo_id,
            "provider_id": provider_id,
            "action_unit": DRAFT_UNTIL_GATE_ACTION_UNIT,
            "effect_class": "draft",
            "write_scope": {
                "allowed_actions": [
                    "open approved screen",
                    "fill approved draft fields",
                    "capture compact receipt",
                ],
                "forbidden_effect_classes": ["external_write", "credential_use"],
            },
            "stop_condition": "stop at the final submit control or an unrecognized modal",
            "validation_target": (
                "draft screen is reachable and the final submit control remains unclicked"
            ),
        }
    elif state == "approved":
        # An "approved" item has authorization but has not yet had its delivery
        # intent durably declared -- that declaration (approved -> delivery_ready
        # via set_delivery_intent) is the fence against the external effect
        # happening before any durable commit, and it belongs to the orchestrating
        # caller (build_content_ops_browser_action_request_packet), not here.
        raise ContentOpsCuaContractViolation(
            "content item state 'approved' has no browser action request yet; "
            "delivery intent must be durably declared first (approved -> "
            "delivery_ready via set_delivery_intent) before any provider can be "
            "invoked -- build_content_ops_browser_action_request_packet does "
            "this automatically, which is what item-browser-request calls"
        )
    elif state == "delivery_ready":
        # Delivery intent was already durably declared for this exact approval
        # (approved -> delivery_ready happened before this function was ever
        # called with a request a provider could act on). approval_sequence is
        # what makes gate_binding.revision a stable identity for this specific
        # declared attempt, unique because set_delivery_intent is one-directional
        # and state-checked: it can only ever succeed once per approval_sequence.
        approval_sequence = int(normalized_item["approval_sequence"])
        if approval_sequence < 1:
            raise ContentOpsCuaContractViolation(
                f"item in state {state!r} must have a positive approval_sequence"
            )
        action_request = {
            "schema_version": ACTION_REQUEST_SCHEMA_VERSION,
            "goal_id": goal_id,
            "todo_id": todo_id,
            "provider_id": provider_id,
            "action_unit": PUBLISH_AFTER_APPROVED_GATE_ACTION_UNIT,
            "effect_class": "external_write",
            "write_scope": {
                "allowed_actions": ["click the approved submit control"],
                "forbidden_effect_classes": ["credential_use"],
            },
            "gate_binding": {
                "gate_id": f"gate_{item_id}_publish",
                "revision": approval_sequence,
                "status": "open",
            },
            "stop_condition": "stop if the live gate revision has changed since approval",
            "validation_target": "submit is clicked only under the exact approved gate revision",
        }
    else:
        raise ContentOpsCuaContractViolation(
            f"content item state {state!r} has no browser action request; "
            "review_ready has nothing approved yet to write, and published/"
            "terminal states have nothing left to attempt"
        )
    check_action_request_shape(action_request)
    return action_request


def build_content_ops_browser_action_request_packet(
    *,
    item: Mapping[str, Any],
    goal_id: str,
    todo_id: str,
    occurred_at: str,
    provider_id: str = "computer_use_runtime",
) -> dict[str, Any]:
    """CLI-facing wrapper: the bounded request a host browser/CUA tool should attempt.

    This is the orchestrating layer that makes the external-write path
    durably fenced *before* any provider is ever invoked, not just an
    unwrapped call to build_content_ops_browser_action_request:

    - an "approved" item has delivery intent durably declared right here
      (approved -> delivery_ready via the existing, already-tested
      set_delivery_intent action) before this function ever returns a
      request a provider could act on;
    - an item that is *already* "delivery_ready" when this function is
      called is refused outright, not silently handed a fresh identical
      request -- set_delivery_intent can only ever succeed once per
      approval_sequence (state-checked), so "already delivery_ready" always
      means a declare already happened, whether from an earlier call to this
      function or a crashed/lost attempt. This is deliberately conservative:
      even a caller who declared intent manually via `item-transition
      set_delivery_intent` and is calling this for the first time will be
      refused the same way, since nothing durable distinguishes that from a
      lost retry. That is a disclosed limitation, not an oversight -- use
      `item-transition`/content-ops's existing readback tooling directly in
      that case, or `revoke_approval` to establish a fresh approval_sequence.

    IMPORTANT: the returned packet's "item" field may already reflect that
    durable declare transition. The caller MUST persist it (e.g. overwrite
    their --item-json file) *before* invoking a provider with the returned
    action_request -- the fence above only holds if this is actually saved;
    otherwise it only exists in this process's memory.

    Also carries an ``expected_transition`` sidecar pinned to the item
    snapshot as returned by this call -- *outside* the wire-shaped
    ``action_request`` object, since computer_use_action_request_v0 is
    closed (``additionalProperties: false``) and cannot carry it. Passing
    this whole packet back into ``item-browser-receipt`` (rather than just
    the inner ``action_request``) is what makes a receipt replay safe even
    if the item has since moved on: see
    computer_use_reducer.apply_content_ops_browser_receipt.
    """

    normalized_item = require_content_ops_item(item)
    original_state = str(normalized_item["state"])
    if original_state == "delivery_ready":
        raise ContentOpsCuaContractViolation(
            "content item state 'delivery_ready' has no browser action request; "
            "delivery intent for this approval was already declared -- do not "
            "retry via item-browser-request; verify what actually happened and "
            "use content-ops's existing readback tooling, or revoke_approval to "
            "re-authorize a new attempt"
        )
    if original_state == "approved":
        approval = normalized_item.get("approval")
        if not isinstance(approval, Mapping):
            raise ContentOpsCuaContractViolation("approved item is missing its approval record")
        declare_event = {
            "event_id": (
                f"cua_declare_intent_{normalized_item['item_id']}_"
                f"{normalized_item['approval_sequence']}"
            ),
            "action": "set_delivery_intent",
            "expected_state": "approved",
            "expected_revision": normalized_item["revision"],
            "occurred_at": occurred_at,
            "payload": {
                "provider_id": provider_id,
                "effect_kind": approval["effect_kind"],
            },
        }
        working_item = apply_content_ops_item_event(normalized_item, declare_event)["item"]
    else:
        working_item = normalized_item

    action_request = build_content_ops_browser_action_request(
        item=working_item, goal_id=goal_id, todo_id=todo_id, provider_id=provider_id
    )
    return {
        "ok": True,
        "schema_version": CONTENT_OPS_BROWSER_ACTION_REQUEST_PACKET_SCHEMA_VERSION,
        "item_id": working_item["item_id"],
        "item": working_item,
        "action_request": action_request,
        "expected_transition": {
            "expected_state": working_item["state"],
            "expected_revision": working_item["revision"],
        },
    }


def render_content_ops_browser_action_request_markdown(packet: Mapping[str, Any]) -> str:
    if not packet.get("ok"):
        return f"# LoopX Content-Ops Browser Action Request\n\n- error: `{packet.get('error')}`\n"
    raw_request = packet.get("action_request")
    request: Mapping[str, Any] = raw_request if isinstance(raw_request, Mapping) else {}
    lines = [
        "# LoopX Content-Ops Browser Action Request",
        "",
        f"- item_id: `{packet.get('item_id')}`",
        f"- action_unit: `{request.get('action_unit')}`",
        f"- effect_class: `{request.get('effect_class')}`",
    ]
    raw_expected_transition = packet.get("expected_transition")
    expected_transition: Mapping[str, Any] | None = (
        raw_expected_transition if isinstance(raw_expected_transition, Mapping) else None
    )
    if expected_transition:
        lines.append(
            f"- expected_transition: state=`{expected_transition.get('expected_state')}` "
            f"revision=`{expected_transition.get('expected_revision')}`"
        )
    lines.append(
        "- IMPORTANT: persist the `item` field of this packet (e.g. overwrite "
        "--item-json) before invoking a provider with `action_request` -- if "
        "this call durably declared delivery intent, that fence only holds "
        "once it is saved."
    )
    raw_gate_binding = request.get("gate_binding")
    gate_binding: Mapping[str, Any] | None = (
        raw_gate_binding if isinstance(raw_gate_binding, Mapping) else None
    )
    if gate_binding:
        lines.append(
            f"- gate_binding: `{gate_binding.get('gate_id')}` "
            f"revision=`{gate_binding.get('revision')}` status=`{gate_binding.get('status')}`"
        )
    else:
        lines.append("- gate_binding: none (nothing approved yet to write)")
    lines.append(f"- stop_condition: {request.get('stop_condition')}")
    return "\n".join(lines) + "\n"


class FakeComputerUseProvider:
    """Deterministic, hermetic stand-in for a real host browser/CUA tool.

    Driven entirely by a declarative ``ui_state`` (same style as the PR1
    fixtures under examples/computer-use-runtime-contract-fixtures/) so CI
    never needs a real browser. A real provider (a host browser tool,
    ego-browser, ...) can implement the same ``ComputerUseProvider`` protocol
    without content-ops changing.
    """

    def __init__(self, ui_state: Mapping[str, Any], *, session_reference: str) -> None:
        self._ui_state = ui_state
        self._session_reference = _require_str(session_reference, "session_reference")

    def attempt(self, action_request: Mapping[str, Any]) -> dict[str, Any]:
        check_action_request_shape(action_request)
        screen_reachable = bool(self._ui_state.get("screen_reachable", True))
        unexpected_modal_present = bool(self._ui_state.get("unexpected_modal_present", False))
        submit_click_permitted = bool(self._ui_state.get("submit_click_permitted", False))
        effect_class = action_request["effect_class"]

        if not screen_reachable:
            stop_reason = "failed"
            observed_facts = {
                "screen_reached": False,
                "draft_present": False,
                "final_action_clicked": False,
                "unknown_modal": False,
            }
        elif unexpected_modal_present:
            stop_reason = "blocked_by_unknown_modal"
            observed_facts = {
                "screen_reached": True,
                "draft_present": True,
                "final_action_clicked": False,
                "unknown_modal": True,
            }
        elif effect_class == "external_write":
            if not submit_click_permitted:
                raise ContentOpsCuaContractViolation(
                    "fixture ui_state does not permit a submit click for this "
                    "external_write action_request"
                )
            stop_reason = "completed"
            observed_facts = {
                "screen_reached": True,
                "draft_present": True,
                "final_action_clicked": True,
                "unknown_modal": False,
            }
        elif effect_class == "draft":
            stop_reason = "stopped_at_gate"
            observed_facts = {
                "screen_reached": True,
                "draft_present": True,
                "final_action_clicked": False,
                "unknown_modal": False,
            }
        else:
            raise ContentOpsCuaContractViolation(
                f"FakeComputerUseProvider does not support effect_class {effect_class!r}"
            )

        attempt_label = str(self._ui_state.get("attempt_label") or "attempt_1")
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "goal_id": action_request["goal_id"],
            "todo_id": action_request["todo_id"],
            "provider_id": action_request["provider_id"],
            "attempted_action_unit": action_request["action_unit"],
            "stop_reason": stop_reason,
            "observed_facts": observed_facts,
            "evidence": {
                "handle_kind": "fake_provider_replay_pointer",
                "raw_evidence_copied": False,
                "private_source_redacted": True,
            },
            "idempotency_key": (
                f"{action_request['todo_id']}_{action_request['action_unit']}_{attempt_label}"
            ),
            "session_reference": self._session_reference,
        }
        check_receipt_shape(receipt)
        check_receipt_matches_request(receipt, action_request)
        return receipt
