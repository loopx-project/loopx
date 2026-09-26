from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .schemas import (
    CONTENT_OPS_ITEM_PACKET_SCHEMA_VERSION,
    CONTENT_OPS_ITEM_PROJECTION_SCHEMA_VERSION,
    CONTENT_OPS_ITEM_SCHEMA_VERSION,
    CONTENT_OPS_ITEM_TRANSITION_PACKET_SCHEMA_VERSION,
    CONTENT_OPS_ITEM_TRANSITION_RECEIPT_SCHEMA_VERSION,
    CONTENT_OPS_QUEUE_PROJECTION_SCHEMA_VERSION,
    CONTENT_OPS_QUEUE_STATUS_PACKET_SCHEMA_VERSION,
)

ALLOWED_ITEM_KINDS = {"article", "post", "profile_update", "reply", "repost"}
ALLOWED_ITEM_STATES = {
    "captured",
    "draft",
    "review_ready",
    "approved",
    "delivery_ready",
    "published",
    "readback_verified",
    "skipped",
    "superseded",
}
ALLOWED_EFFECT_KINDS = {"profile_update", "publish", "reply", "repost"}
TERMINAL_STATES = {"readback_verified", "skipped", "superseded"}

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ITEM_KEYS = {
    "schema_version",
    "item_id",
    "item_kind",
    "channel",
    "state",
    "revision",
    "content_digest",
    "content_ref",
    "source_refs",
    "approval",
    "delivery_intent",
    "delivery_receipt",
    "readback_receipt",
    "superseded_by",
    "terminal_reason",
    "last_event",
    "created_at",
    "updated_at",
    "autopublish_allowed",
    "approval_sequence",
}


def _token(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{label} must be an opaque token")
    return text


def _digest(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _DIGEST_RE.fullmatch(text):
        raise ValueError(f"{label} must use sha256:<64 lowercase hex chars>")
    return text


def _timestamp(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return text


def _timestamp_value(value: Any, label: str) -> datetime:
    return datetime.fromisoformat(_timestamp(value, label))


def _public_url(value: Any, label: str) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{label} must be a public https URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain credentials, query, or fragment")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError(f"{label} must not target a local host")
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", "", ""))


def _short_reason(value: Any, label: str) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > 180:
        raise ValueError(f"{label} must contain 1-180 public-safe characters")
    lowered = text.lower()
    if any(
        marker in lowered for marker in ("/users/", "/private/", "bearer ", "secret")
    ):
        raise ValueError(f"{label} must not contain private paths or credentials")
    return text


def _source_refs(values: Sequence[Any] | None) -> list[str]:
    refs = [_token(value, "source_ref") for value in values or []]
    if len(refs) != len(set(refs)):
        raise ValueError("source_refs must be unique")
    return refs


def _event_digest(event: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        event, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _record(
    value: Any,
    label: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object or null")
    record = dict(value)
    optional = optional or set()
    missing = sorted(required - set(record))
    extra = sorted(set(record) - required - optional)
    if missing or extra:
        raise ValueError(
            f"{label} fields invalid: missing={missing}, unsupported={extra}"
        )
    return record


def _validate_effect_records(item: Mapping[str, Any]) -> None:
    approval = _record(
        item.get("approval"),
        "approval",
        required={
            "approval_ref",
            "revision",
            "content_digest",
            "effect_kind",
            "account_ref",
            "valid_from",
            "valid_until",
        },
    )
    intent = _record(
        item.get("delivery_intent"),
        "delivery_intent",
        required={
            "provider_id",
            "effect_kind",
            "account_ref",
            "not_before",
            "not_after",
        },
    )
    delivery = _record(
        item.get("delivery_receipt"),
        "delivery_receipt",
        required={
            "provider_id",
            "effect_kind",
            "account_ref",
            "content_digest",
            "public_url",
            "receipt_ref",
            "recorded_at",
        },
    )
    readback = _record(
        item.get("readback_receipt"),
        "readback_receipt",
        required={"public_url", "content_digest", "readback_ref", "verified_at"},
    )

    state = str(item["state"])
    if state in {"approved", "delivery_ready", "published", "readback_verified"}:
        if approval is None:
            raise ValueError(f"{state} item requires approval")
    elif approval is not None:
        raise ValueError(f"{state} item must not retain approval")
    if state == "delivery_ready" and intent is None:
        raise ValueError("delivery_ready item requires delivery_intent")
    if intent is not None and state not in {
        "delivery_ready",
        "published",
        "readback_verified",
    }:
        raise ValueError(f"{state} item must not retain delivery_intent")
    if state in {"published", "readback_verified"} and delivery is None:
        raise ValueError(f"{state} item requires delivery_receipt")
    if delivery is not None and state not in {"published", "readback_verified"}:
        raise ValueError(f"{state} item must not retain delivery_receipt")
    if state == "readback_verified" and readback is None:
        raise ValueError("readback_verified item requires readback_receipt")
    if readback is not None and state != "readback_verified":
        raise ValueError(f"{state} item must not retain readback_receipt")

    if approval is not None:
        _token(approval["approval_ref"], "approval.approval_ref")
        if approval["revision"] != item["revision"]:
            raise ValueError("approval revision does not match item revision")
        if (
            _digest(approval["content_digest"], "approval.content_digest")
            != item["content_digest"]
        ):
            raise ValueError("approval content_digest does not match item")
        if approval["effect_kind"] not in ALLOWED_EFFECT_KINDS:
            raise ValueError("approval effect_kind is invalid")
        if approval["account_ref"] is not None:
            _token(approval["account_ref"], "approval.account_ref")
        for field in ("valid_from", "valid_until"):
            if approval[field] is not None:
                _timestamp(approval[field], f"approval.{field}")
    if intent is not None:
        _token(intent["provider_id"], "delivery_intent.provider_id")
        if intent["effect_kind"] != approval["effect_kind"]:
            raise ValueError("delivery_intent effect_kind does not match approval")
        if intent["account_ref"] is not None:
            _token(intent["account_ref"], "delivery_intent.account_ref")
        if (
            approval.get("account_ref")
            and intent["account_ref"] != approval["account_ref"]
        ):
            raise ValueError("delivery_intent account_ref does not match approval")
        for field in ("not_before", "not_after"):
            if intent[field] is not None:
                _timestamp(intent[field], f"delivery_intent.{field}")
    if delivery is not None:
        _token(delivery["provider_id"], "delivery_receipt.provider_id")
        _token(delivery["receipt_ref"], "delivery_receipt.receipt_ref")
        _timestamp(delivery["recorded_at"], "delivery_receipt.recorded_at")
        _public_url(delivery["public_url"], "delivery_receipt.public_url")
        if delivery["effect_kind"] != approval["effect_kind"]:
            raise ValueError("delivery_receipt effect_kind does not match approval")
        if (
            _digest(delivery["content_digest"], "delivery_receipt.content_digest")
            != item["content_digest"]
        ):
            raise ValueError("delivery_receipt content_digest does not match item")
        expected_provider = intent.get("provider_id") if intent else None
        if expected_provider and delivery["provider_id"] != expected_provider:
            raise ValueError("delivery_receipt provider_id does not match intent")
        expected_account = (
            intent.get("account_ref") if intent else approval.get("account_ref")
        )
        if delivery["account_ref"] is not None:
            _token(delivery["account_ref"], "delivery_receipt.account_ref")
        if expected_account and delivery["account_ref"] != expected_account:
            raise ValueError("delivery_receipt account_ref does not match approval")
    if readback is not None:
        _token(readback["readback_ref"], "readback_receipt.readback_ref")
        _timestamp(readback["verified_at"], "readback_receipt.verified_at")
        if (
            _public_url(readback["public_url"], "readback_receipt.public_url")
            != delivery["public_url"]
        ):
            raise ValueError("readback_receipt public_url does not match delivery")
        if (
            _digest(readback["content_digest"], "readback_receipt.content_digest")
            != item["content_digest"]
        ):
            raise ValueError("readback_receipt content_digest does not match item")


def require_content_ops_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a raw content_ops_item_v0 mapping into a fresh,
    fully-populated dict -- never mutates ``item`` in place. Every reader of
    a caller-supplied item mapping (inside this module or elsewhere in
    content-ops, e.g. computer_use_provider.py / computer_use_reducer.py)
    must call this first and use its *return value*, not the raw input --
    that's what makes old content_ops_item_v0 data (missing fields added
    after it was written, e.g. approval_sequence) still safely readable."""

    candidate = copy.deepcopy(dict(item))
    extra = sorted(set(candidate) - _ITEM_KEYS)
    if extra:
        raise ValueError(f"content item contains unsupported fields: {extra}")
    if candidate.get("schema_version") != CONTENT_OPS_ITEM_SCHEMA_VERSION:
        raise ValueError("content item schema_version is invalid")
    _token(candidate.get("item_id"), "item_id")
    if candidate.get("item_kind") not in ALLOWED_ITEM_KINDS:
        raise ValueError(f"item_kind must be one of {sorted(ALLOWED_ITEM_KINDS)}")
    _token(candidate.get("channel"), "channel")
    if candidate.get("state") not in ALLOWED_ITEM_STATES:
        raise ValueError(f"state must be one of {sorted(ALLOWED_ITEM_STATES)}")
    revision = candidate.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("revision must be a positive integer")
    if "approval_sequence" not in candidate:
        # Backward compatibility: a persisted content_ops_item_v0 written before
        # approval_sequence existed has no way to report its true historical
        # approval count, and any approval it may already carry predates CUA
        # gate semantics entirely. Default to 0 unconditionally rather than
        # inferring 1 for a currently-approved item -- the migration point
        # only needs future approvals to increment correctly, not a
        # retroactively "correct" historical value. A consequence, not a bug:
        # a pre-existing approved item cannot issue a CUA action request until
        # a fresh approve event establishes CUA-aware gate authority for it
        # (see build_content_ops_browser_action_request's own
        # approval_sequence < 1 check, which is where that requirement
        # belongs -- this generic item-lifecycle validation does not need to
        # know anything about CUA). schema_version stays content_ops_item_v0
        # on purpose: this is additive normalization at the read boundary,
        # not a breaking schema change.
        candidate["approval_sequence"] = 0
    approval_sequence = candidate.get("approval_sequence")
    if (
        isinstance(approval_sequence, bool)
        or not isinstance(approval_sequence, int)
        or approval_sequence < 0
    ):
        raise ValueError("approval_sequence must be a non-negative integer")
    _digest(candidate.get("content_digest"), "content_digest")
    _token(candidate.get("content_ref"), "content_ref")
    _source_refs(candidate.get("source_refs"))
    _timestamp(candidate.get("created_at"), "created_at")
    _timestamp(candidate.get("updated_at"), "updated_at")
    if candidate.get("autopublish_allowed") is not False:
        raise ValueError("autopublish_allowed must be false")
    last_event = _record(
        candidate.get("last_event"),
        "last_event",
        required={"event_id", "event_digest", "action"},
    )
    if last_event is not None:
        _token(last_event["event_id"], "last_event.event_id")
        _digest(last_event["event_digest"], "last_event.event_digest")
        _token(last_event["action"], "last_event.action")
    if candidate.get("superseded_by") is not None:
        _token(candidate["superseded_by"], "superseded_by")
    if candidate.get("terminal_reason") is not None:
        _short_reason(candidate["terminal_reason"], "terminal_reason")
    if candidate["state"] == "superseded" and not candidate.get("superseded_by"):
        raise ValueError("superseded item requires superseded_by")
    if candidate.get("superseded_by") and candidate["state"] != "superseded":
        raise ValueError("only a superseded item may set superseded_by")
    if candidate["state"] in {"skipped", "superseded"}:
        if not candidate.get("terminal_reason"):
            raise ValueError(f"{candidate['state']} item requires terminal_reason")
    elif candidate.get("terminal_reason") is not None:
        raise ValueError("non-terminal item must not set terminal_reason")
    _validate_effect_records(candidate)
    return candidate


def build_content_ops_item(
    *,
    item_id: str,
    item_kind: str,
    channel: str,
    content_digest: str,
    content_ref: str,
    source_refs: Sequence[str] | None = None,
    created_at: str,
) -> dict[str, Any]:
    """Create a compact content item without copying draft content into LoopX."""

    if item_kind not in ALLOWED_ITEM_KINDS:
        raise ValueError(f"item_kind must be one of {sorted(ALLOWED_ITEM_KINDS)}")
    timestamp = _timestamp(created_at, "created_at")
    item = {
        "schema_version": CONTENT_OPS_ITEM_SCHEMA_VERSION,
        "item_id": _token(item_id, "item_id"),
        "item_kind": item_kind,
        "channel": _token(channel, "channel"),
        "state": "captured",
        "revision": 1,
        "content_digest": _digest(content_digest, "content_digest"),
        "content_ref": _token(content_ref, "content_ref"),
        "source_refs": _source_refs(source_refs),
        "approval_sequence": 0,
        "approval": None,
        "delivery_intent": None,
        "delivery_receipt": None,
        "readback_receipt": None,
        "superseded_by": None,
        "terminal_reason": None,
        "last_event": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "autopublish_allowed": False,
    }
    return require_content_ops_item(item)


def validate_content_ops_item(item: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    try:
        require_content_ops_item(item)
    except ValueError as exc:
        errors.append(str(exc))
    return {
        "ok": not errors,
        "schema_version": CONTENT_OPS_ITEM_SCHEMA_VERSION,
        "errors": errors,
    }


def _require_event(event: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    candidate = copy.deepcopy(dict(event))
    required = {
        "event_id",
        "action",
        "expected_state",
        "expected_revision",
        "occurred_at",
    }
    missing = sorted(key for key in required if key not in candidate)
    extra = sorted(set(candidate) - required - {"payload"})
    if missing or extra:
        raise ValueError(
            f"event fields invalid: missing={missing}, unsupported={extra}"
        )
    candidate["event_id"] = _token(candidate["event_id"], "event_id")
    candidate["occurred_at"] = _timestamp(candidate["occurred_at"], "occurred_at")
    revision = candidate["expected_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("expected_revision must be a positive integer")
    if candidate["expected_state"] not in ALLOWED_ITEM_STATES:
        raise ValueError("expected_state is invalid")
    if not isinstance(candidate.get("payload", {}), Mapping):
        raise TypeError("event payload must be an object")
    candidate["payload"] = dict(candidate.get("payload") or {})
    return candidate, _event_digest(candidate)


def _payload(
    payload: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    optional = optional or set()
    missing = sorted(required - set(payload))
    extra = sorted(set(payload) - required - optional)
    if missing or extra:
        raise ValueError(
            f"event payload fields invalid: missing={missing}, unsupported={extra}"
        )
    return dict(payload)


def _clear_effect_state(item: dict[str, Any]) -> None:
    item["approval"] = None
    item["delivery_intent"] = None
    item["delivery_receipt"] = None
    item["readback_receipt"] = None


def _assert_delivery_window(item: Mapping[str, Any], occurred_at: str) -> None:
    observed = _timestamp_value(occurred_at, "occurred_at")
    approval = item.get("approval") if isinstance(item.get("approval"), Mapping) else {}
    intent = (
        item.get("delivery_intent")
        if isinstance(item.get("delivery_intent"), Mapping)
        else {}
    )
    for field, is_lower in (
        ("valid_from", True),
        ("valid_until", False),
        ("not_before", True),
        ("not_after", False),
    ):
        raw = approval.get(field) if field.startswith("valid_") else intent.get(field)
        if raw is None:
            continue
        boundary = _timestamp_value(raw, field)
        if (is_lower and observed < boundary) or (not is_lower and observed > boundary):
            raise ValueError(f"delivery occurred outside {field}")


def _transition(item: dict[str, Any], event: Mapping[str, Any]) -> None:
    action = str(event["action"])
    state = str(item["state"])
    payload = dict(event["payload"])

    if action == "revise":
        if state not in {
            "captured",
            "draft",
            "review_ready",
            "approved",
            "delivery_ready",
        }:
            raise ValueError(f"revise is not allowed from {state}")
        values = _payload(
            payload,
            required={"content_digest", "content_ref"},
            optional={"source_refs"},
        )
        item["revision"] += 1
        item["content_digest"] = _digest(values["content_digest"], "content_digest")
        item["content_ref"] = _token(values["content_ref"], "content_ref")
        if "source_refs" in values:
            if not isinstance(values["source_refs"], list):
                raise ValueError("source_refs must be a list")
            item["source_refs"] = _source_refs(values["source_refs"])
        _clear_effect_state(item)
        item["state"] = "draft"
        return

    if action == "submit_review":
        if state not in {"captured", "draft"}:
            raise ValueError(f"submit_review is not allowed from {state}")
        _payload(payload, required=set())
        item["state"] = "review_ready"
        return

    if action == "approve":
        if state != "review_ready":
            raise ValueError(f"approve is not allowed from {state}")
        values = _payload(
            payload,
            required={"approval_ref", "revision", "content_digest", "effect_kind"},
            optional={"account_ref", "valid_from", "valid_until"},
        )
        if values["revision"] != item["revision"]:
            raise ValueError("approval revision does not match current item revision")
        if (
            _digest(values["content_digest"], "content_digest")
            != item["content_digest"]
        ):
            raise ValueError("approval content_digest does not match current item")
        effect_kind = str(values["effect_kind"])
        if effect_kind not in ALLOWED_EFFECT_KINDS:
            raise ValueError(
                f"effect_kind must be one of {sorted(ALLOWED_EFFECT_KINDS)}"
            )
        approval = {
            "approval_ref": _token(values["approval_ref"], "approval_ref"),
            "revision": item["revision"],
            "content_digest": item["content_digest"],
            "effect_kind": effect_kind,
            "account_ref": _token(values["account_ref"], "account_ref")
            if values.get("account_ref")
            else None,
            "valid_from": _timestamp(values["valid_from"], "valid_from")
            if values.get("valid_from")
            else None,
            "valid_until": _timestamp(values["valid_until"], "valid_until")
            if values.get("valid_until")
            else None,
        }
        if (
            approval["valid_from"]
            and approval["valid_until"]
            and _timestamp_value(approval["valid_from"], "valid_from")
            > _timestamp_value(approval["valid_until"], "valid_until")
        ):
            raise ValueError("valid_from must not be after valid_until")
        item["approval"] = approval
        item["approval_sequence"] = int(item["approval_sequence"]) + 1
        item["state"] = "approved"
        return

    if action == "set_delivery_intent":
        if state != "approved":
            raise ValueError(f"set_delivery_intent is not allowed from {state}")
        values = _payload(
            payload,
            required={"provider_id", "effect_kind"},
            optional={"account_ref", "not_before", "not_after"},
        )
        approval = dict(item["approval"])
        if values["effect_kind"] != approval["effect_kind"]:
            raise ValueError("delivery intent effect_kind does not match approval")
        account_ref = (
            _token(values["account_ref"], "account_ref")
            if values.get("account_ref")
            else approval.get("account_ref")
        )
        if approval.get("account_ref") and account_ref != approval["account_ref"]:
            raise ValueError("delivery intent account_ref does not match approval")
        intent = {
            "provider_id": _token(values["provider_id"], "provider_id"),
            "effect_kind": values["effect_kind"],
            "account_ref": account_ref,
            "not_before": _timestamp(values["not_before"], "not_before")
            if values.get("not_before")
            else None,
            "not_after": _timestamp(values["not_after"], "not_after")
            if values.get("not_after")
            else None,
        }
        if (
            intent["not_before"]
            and intent["not_after"]
            and _timestamp_value(intent["not_before"], "not_before")
            > _timestamp_value(intent["not_after"], "not_after")
        ):
            raise ValueError("not_before must not be after not_after")
        item["delivery_intent"] = intent
        item["state"] = "delivery_ready"
        return

    if action == "record_delivery":
        if state not in {"approved", "delivery_ready"}:
            raise ValueError(f"record_delivery is not allowed from {state}")
        values = _payload(
            payload,
            required={
                "provider_id",
                "effect_kind",
                "content_digest",
                "public_url",
                "receipt_ref",
            },
            optional={"account_ref"},
        )
        approval = dict(item["approval"])
        if values["effect_kind"] != approval["effect_kind"]:
            raise ValueError("delivery effect_kind does not match approval")
        if (
            _digest(values["content_digest"], "content_digest")
            != approval["content_digest"]
        ):
            raise ValueError("delivery content_digest does not match approval")
        intent = dict(item["delivery_intent"] or {})
        provider_id = _token(values["provider_id"], "provider_id")
        if intent and provider_id != intent["provider_id"]:
            raise ValueError("delivery provider_id does not match intent")
        account_ref = (
            _token(values["account_ref"], "account_ref")
            if values.get("account_ref")
            else intent.get("account_ref") or approval.get("account_ref")
        )
        expected_account = intent.get("account_ref") or approval.get("account_ref")
        if expected_account and account_ref != expected_account:
            raise ValueError("delivery account_ref does not match approval or intent")
        _assert_delivery_window(item, str(event["occurred_at"]))
        item["delivery_receipt"] = {
            "provider_id": provider_id,
            "effect_kind": values["effect_kind"],
            "account_ref": account_ref,
            "content_digest": item["content_digest"],
            "public_url": _public_url(values["public_url"], "public_url"),
            "receipt_ref": _token(values["receipt_ref"], "receipt_ref"),
            "recorded_at": event["occurred_at"],
        }
        item["state"] = "published"
        return

    if action == "verify_readback":
        if state != "published":
            raise ValueError(f"verify_readback is not allowed from {state}")
        values = _payload(
            payload,
            required={"public_url", "content_digest", "readback_ref"},
        )
        delivery = dict(item["delivery_receipt"])
        public_url = _public_url(values["public_url"], "public_url")
        if public_url != delivery["public_url"]:
            raise ValueError("readback public_url does not match delivery receipt")
        if (
            _digest(values["content_digest"], "content_digest")
            != delivery["content_digest"]
        ):
            raise ValueError("readback content_digest does not match delivery receipt")
        item["readback_receipt"] = {
            "public_url": public_url,
            "content_digest": item["content_digest"],
            "readback_ref": _token(values["readback_ref"], "readback_ref"),
            "verified_at": event["occurred_at"],
        }
        item["state"] = "readback_verified"
        return

    if action == "revoke_approval":
        if state not in {"approved", "delivery_ready"}:
            raise ValueError(f"revoke_approval is not allowed from {state}")
        values = _payload(payload, required={"reason"})
        _clear_effect_state(item)
        _short_reason(values["reason"], "reason")
        item["state"] = "review_ready"
        return

    if action == "skip":
        if state in {"published", *TERMINAL_STATES}:
            raise ValueError(f"skip is not allowed from {state}")
        values = _payload(payload, required={"reason"})
        _clear_effect_state(item)
        item["terminal_reason"] = _short_reason(values["reason"], "reason")
        item["state"] = "skipped"
        return

    if action == "supersede":
        if state in {"published", *TERMINAL_STATES}:
            raise ValueError(f"supersede is not allowed from {state}")
        values = _payload(payload, required={"successor_item_id", "reason"})
        successor = _token(values["successor_item_id"], "successor_item_id")
        if successor == item["item_id"]:
            raise ValueError("successor_item_id must differ from item_id")
        _clear_effect_state(item)
        item["superseded_by"] = successor
        item["terminal_reason"] = _short_reason(values["reason"], "reason")
        item["state"] = "superseded"
        return

    raise ValueError(f"unsupported content item action {action!r}")


def project_content_ops_item(item: Mapping[str, Any]) -> dict[str, Any]:
    current = require_content_ops_item(item)
    state = str(current["state"])
    next_actions = {
        "captured": ["revise", "submit_review", "skip", "supersede"],
        "draft": ["revise", "submit_review", "skip", "supersede"],
        "review_ready": ["revise", "approve", "skip", "supersede"],
        "approved": [
            "revise",
            "set_delivery_intent",
            "record_delivery",
            "revoke_approval",
            "skip",
            "supersede",
        ],
        "delivery_ready": [
            "revise",
            "record_delivery",
            "revoke_approval",
            "skip",
            "supersede",
        ],
        "published": ["verify_readback"],
        "readback_verified": [],
        "skipped": [],
        "superseded": [],
    }[state]
    delivery = current.get("delivery_receipt") or {}
    return {
        "schema_version": CONTENT_OPS_ITEM_PROJECTION_SCHEMA_VERSION,
        "item_id": current["item_id"],
        "item_kind": current["item_kind"],
        "channel": current["channel"],
        "state": state,
        "revision": current["revision"],
        "approval_sequence": current["approval_sequence"],
        "approval_present": current.get("approval") is not None,
        "delivery_intent_present": current.get("delivery_intent") is not None,
        "published_url": delivery.get("public_url"),
        "readback_verified": current.get("readback_receipt") is not None,
        "terminal": state in TERMINAL_STATES,
        "superseded_by": current.get("superseded_by"),
        "next_actions": next_actions,
        "truth_contract": {
            "projection_is_writable": False,
            "external_effect_authority": "none",
            "autopublish_allowed": False,
        },
    }


def build_content_ops_queue_projection(
    *,
    items: Sequence[Mapping[str, Any]],
    queue_id: str = "content_ops_managed_queue",
    generated_at: str = "2026-08-10T00:00:00Z",
) -> dict[str, Any]:
    """Project caller-owned content items into one managed queue surface."""

    queue_token = _token(queue_id, "queue_id")
    timestamp = _timestamp(generated_at, "generated_at")
    if not items:
        raise ValueError("content queue requires at least one item")
    normalized = [require_content_ops_item(item) for item in items]
    item_projections = [project_content_ops_item(item) for item in normalized]

    counts: dict[str, int] = {}
    terminal_count = 0
    for projection in item_projections:
        state = str(projection["state"])
        counts[state] = counts.get(state, 0) + 1
        if projection["terminal"]:
            terminal_count += 1

    actionable = [
        {
            "priority_index": index,
            "item_id": projection["item_id"],
            "item_kind": projection["item_kind"],
            "channel": projection["channel"],
            "state": projection["state"],
            "revision": projection["revision"],
            "next_actions": projection["next_actions"],
        }
        for index, projection in enumerate(item_projections, start=1)
        if not projection["terminal"]
    ]
    next_action = None
    if actionable:
        first = actionable[0]
        next_action = {
            "item_id": first["item_id"],
            "state": first["state"],
            "priority_index": first["priority_index"],
            "next_actions": first["next_actions"],
        }

    return {
        "schema_version": CONTENT_OPS_QUEUE_PROJECTION_SCHEMA_VERSION,
        "queue_id": queue_token,
        "generated_at": timestamp,
        "item_count": len(item_projections),
        "counts": counts,
        "terminal_count": terminal_count,
        "items": [
            {
                "priority_index": index,
                "item_id": projection["item_id"],
                "item_kind": projection["item_kind"],
                "channel": projection["channel"],
                "state": projection["state"],
                "revision": projection["revision"],
                "content_ref": normalized[index - 1]["content_ref"],
                "terminal": projection["terminal"],
                "published_url": projection["published_url"],
                "next_actions": projection["next_actions"],
            }
            for index, projection in enumerate(item_projections, start=1)
        ],
        "next_action": next_action,
        "truth_contract": {
            "projection_is_writable": False,
            "external_effect_authority": "none",
            "autopublish_allowed": False,
        },
    }


def build_content_ops_queue_status_packet(
    *,
    items: Sequence[Mapping[str, Any]],
    queue_id: str = "content_ops_managed_queue",
    generated_at: str = "2026-08-10T00:00:00Z",
) -> dict[str, Any]:
    """Build a read-only queue status packet without draft bodies."""

    projection = build_content_ops_queue_projection(
        items=items,
        queue_id=queue_id,
        generated_at=generated_at,
    )
    return {
        "ok": True,
        "schema_version": CONTENT_OPS_QUEUE_STATUS_PACKET_SCHEMA_VERSION,
        "queue_id": projection["queue_id"],
        "generated_at": projection["generated_at"],
        "item_count": projection["item_count"],
        "projection": projection,
        "external_reads_performed": False,
        "external_writes_performed": False,
        "private_source_bodies_read": False,
        "autopublish_allowed": False,
    }


def build_content_ops_item_packet(**kwargs: Any) -> dict[str, Any]:
    item = build_content_ops_item(**kwargs)
    return {
        "ok": True,
        "schema_version": CONTENT_OPS_ITEM_PACKET_SCHEMA_VERSION,
        "item": item,
        "projection": project_content_ops_item(item),
        "external_reads_performed": False,
        "external_writes_performed": False,
        "autopublish_allowed": False,
    }


def apply_content_ops_item_event(
    item: Mapping[str, Any], event: Mapping[str, Any]
) -> dict[str, Any]:
    current = require_content_ops_item(item)
    normalized_event, event_digest = _require_event(event)
    last_event = current.get("last_event")
    if (
        isinstance(last_event, Mapping)
        and last_event.get("event_id") == normalized_event["event_id"]
    ):
        if last_event.get("event_digest") != event_digest:
            raise ValueError("event_id was already used with different content")
        return {
            "ok": True,
            "schema_version": CONTENT_OPS_ITEM_TRANSITION_PACKET_SCHEMA_VERSION,
            "item": current,
            "projection": project_content_ops_item(current),
            "receipt": {
                "schema_version": CONTENT_OPS_ITEM_TRANSITION_RECEIPT_SCHEMA_VERSION,
                "status": "already_applied",
                "event_id": normalized_event["event_id"],
                "event_digest": event_digest,
                "from_state": current["state"],
                "to_state": current["state"],
                "revision": current["revision"],
            },
            "external_reads_performed": False,
            "external_writes_performed": False,
            "autopublish_allowed": False,
        }
    if normalized_event["expected_state"] != current["state"]:
        raise ValueError("event expected_state does not match current item state")
    if normalized_event["expected_revision"] != current["revision"]:
        raise ValueError("event expected_revision does not match current item revision")

    before_state = str(current["state"])
    updated = copy.deepcopy(current)
    _transition(updated, normalized_event)
    updated["updated_at"] = normalized_event["occurred_at"]
    updated["last_event"] = {
        "event_id": normalized_event["event_id"],
        "event_digest": event_digest,
        "action": normalized_event["action"],
    }
    require_content_ops_item(updated)
    return {
        "ok": True,
        "schema_version": CONTENT_OPS_ITEM_TRANSITION_PACKET_SCHEMA_VERSION,
        "item": updated,
        "projection": project_content_ops_item(updated),
        "receipt": {
            "schema_version": CONTENT_OPS_ITEM_TRANSITION_RECEIPT_SCHEMA_VERSION,
            "status": "applied",
            "event_id": normalized_event["event_id"],
            "event_digest": event_digest,
            "from_state": before_state,
            "to_state": updated["state"],
            "revision": updated["revision"],
        },
        "external_reads_performed": False,
        "external_writes_performed": False,
        "autopublish_allowed": False,
    }


def render_content_ops_item_packet_markdown(packet: Mapping[str, Any]) -> str:
    if not packet.get("ok"):
        return f"# LoopX Content Item\n\n- error: `{packet.get('error')}`\n"
    projection = (
        packet.get("projection")
        if isinstance(packet.get("projection"), Mapping)
        else {}
    )
    receipt = (
        packet.get("receipt") if isinstance(packet.get("receipt"), Mapping) else {}
    )
    lines = [
        "# LoopX Content Item",
        "",
        f"- item_id: `{projection.get('item_id')}`",
        f"- state: `{projection.get('state')}`",
        f"- revision: `{projection.get('revision')}`",
        f"- approval_present: `{projection.get('approval_present')}`",
        f"- readback_verified: `{projection.get('readback_verified')}`",
        f"- external_writes_performed: `{packet.get('external_writes_performed')}`",
    ]
    if receipt:
        lines.extend(
            [
                f"- transition_status: `{receipt.get('status')}`",
                f"- transition: `{receipt.get('from_state')} -> {receipt.get('to_state')}`",
            ]
        )
    return "\n".join(lines) + "\n"


def render_content_ops_queue_status_markdown(packet: Mapping[str, Any]) -> str:
    if not packet.get("ok"):
        return f"# LoopX Content-Ops Queue\n\n- error: `{packet.get('error')}`\n"
    projection = (
        packet.get("projection")
        if isinstance(packet.get("projection"), Mapping)
        else {}
    )
    counts = projection.get("counts") if isinstance(projection.get("counts"), Mapping) else {}
    next_action = (
        projection.get("next_action")
        if isinstance(projection.get("next_action"), Mapping)
        else None
    )
    lines = [
        "# LoopX Content-Ops Queue",
        "",
        f"- queue_id: `{projection.get('queue_id')}`",
        f"- item_count: `{projection.get('item_count')}`",
        f"- terminal_count: `{projection.get('terminal_count')}`",
        f"- counts: `{counts}`",
        f"- external_writes_performed: `{packet.get('external_writes_performed')}`",
    ]
    if next_action:
        lines.extend(
            [
                "",
                "## Next Action",
                "",
                f"- item_id: `{next_action.get('item_id')}`",
                f"- state: `{next_action.get('state')}`",
                f"- priority_index: `{next_action.get('priority_index')}`",
                f"- next_actions: `{next_action.get('next_actions')}`",
            ]
        )
    items = projection.get("items")
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
        lines.extend(["", "## Queue Items", ""])
        for item in items:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"- `{item.get('priority_index')}` "
                f"`{item.get('item_id')}` "
                f"state=`{item.get('state')}` "
                f"kind=`{item.get('item_kind')}` "
                f"rev=`{item.get('revision')}`"
            )
    return "\n".join(lines) + "\n"
