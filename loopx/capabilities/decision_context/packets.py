"""Deterministic, public-safe packet contracts for Decision Context."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ...control_plane.runtime.public_safety import SECRET_LIKE_SURFACE_PATTERN

DECISION_EVIDENCE_PACKET_SCHEMA_VERSION = "decision_evidence_packet_v0"
DECISION_PROPOSAL_SCHEMA_VERSION = "decision_proposal_v0"
DECISION_REVIEW_RECEIPT_SCHEMA_VERSION = "decision_review_receipt_v0"
DECISION_OUTCOME_RECEIPT_SCHEMA_VERSION = "decision_outcome_receipt_v0"

# Packet-contract namespace. Capability packets identify the capability with
# the underscore spelling (the sibling capability emits "material_lifecycle").
# No consumer joins this value with the hyphenated catalog/extension id in
# extension_provider.py: they are two slots for the same capability, both
# consistent with their own siblings, so the spellings stay separate.
DECISION_CONTEXT_PACKET_CAPABILITY_ID = "decision_context"
DECISION_OUTCOME_VERIFICATION_STATUSES = {
    "pending",
    "verified",
    "refuted",
    "inconclusive",
}
DECISION_REVIEW_DISPOSITIONS = {
    "approve",
    "reject",
    "defer",
    "no_change",
}

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_LOCAL_PATH_RE = re.compile(r"(^|[\s:=])(?:/Users/|/private/|/tmp/|~/)")
_RAW_LOCATION_RE = re.compile(r"(?i)\b(?:https?|file|s3|gs|tos|hdfs)://")
# Local threshold policy only: the credential *shapes* are decided once by
# SECRET_LIKE_SURFACE_PATTERN, which this site consults in addition to this list.
_CREDENTIAL_RE = re.compile(
    "(?i)("
    + "|".join(
        [
            "Author" + "ization:",
            "Bear" + r"er\s+[A-Za-z0-9._-]+",
            "api" + r"[_-]?key",
            "pass" + "word",
            "sec" + "ret",
            "begin " + r"(?:rsa |open)?private key",
        ]
    )
    + ")"
)
_UNSAFE_FIELDS = {
    "api_key",
    "content",
    "credential",
    "credentials",
    "provider_payload",
    "raw_chat",
    "raw_content",
    "raw_provider_payload",
    "token",
    "tool_output",
}


def _compact_text(value: Any, *, field: str, max_len: int = 320) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{field} must be non-empty")
    if len(text) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters")
    if _LOCAL_PATH_RE.search(text):
        raise ValueError(f"{field} must not contain a local path")
    if _RAW_LOCATION_RE.search(text):
        raise ValueError(f"{field} must use an opaque source reference, not a raw URL")
    if SECRET_LIKE_SURFACE_PATTERN.search(text) or _CREDENTIAL_RE.search(text):
        raise ValueError(f"{field} contains a credential-like value")
    return text


def _compact_token(value: Any, *, field: str) -> str:
    token = _compact_text(value, field=field, max_len=128)
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(
            f"{field} must contain only letters, digits, dot, colon, dash, or underscore"
        )
    return token


def _iso_timestamp(value: Any, *, field: str) -> str:
    timestamp = _compact_text(value, field=field, max_len=64)
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp


def _score(value: Any, *, field: str) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(f"{field} must be finite and between 0 and 1")
    return score


def _normalize_value(value: Any, *, field: str, kind: str) -> Any:
    if kind == "token":
        return _compact_token(value, field=field)
    if kind == "text":
        return _compact_text(value, field=field)
    if kind == "timestamp":
        return _iso_timestamp(value, field=field)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be a boolean")
        return value
    if kind == "score":
        return _score(value, field=field)
    if kind == "tokens":
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError(f"{field} must be a sequence of compact tokens")
        return sorted({_compact_token(item, field=f"{field}[]") for item in value})
    raise ValueError(f"unsupported field kind: {kind}")


def _normalize_record(
    value: Mapping[str, Any],
    *,
    field: str,
    spec: Mapping[str, str],
    required: set[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    keys = {str(key) for key in value}
    unsafe = sorted(keys & _UNSAFE_FIELDS)
    if unsafe:
        raise ValueError(f"{field} contains unsafe fields: {', '.join(unsafe)}")
    unexpected = sorted(keys - set(spec))
    if unexpected:
        raise ValueError(
            f"{field} contains unsupported fields: {', '.join(unexpected)}"
        )
    missing = sorted(key for key in required if value.get(key) is None)
    if missing:
        raise ValueError(f"{field} is missing required fields: {', '.join(missing)}")
    normalized: dict[str, Any] = {}
    for key, kind in spec.items():
        item = value.get(key)
        if item is None:
            continue
        normalized[key] = _normalize_value(
            item,
            field=f"{field}.{key}",
            kind=kind,
        )
    return normalized


def _normalize_records(
    values: Sequence[Mapping[str, Any]] | None,
    *,
    field: str,
    spec: Mapping[str, str],
    required: set[str],
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field} must be a sequence of objects")
    records = [
        _normalize_record(
            value,
            field=f"{field}[{index}]",
            spec=spec,
            required=required,
        )
        for index, value in enumerate(values)
    ]
    return sorted(
        records,
        key=lambda item: json.dumps(
            item,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _packet_ref(prefix: str, packet: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            packet,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _capability_contract(*, packet_role: str) -> dict[str, Any]:
    return {
        "capability_id": DECISION_CONTEXT_PACKET_CAPABILITY_ID,
        "scope": "goal",
        "default_enabled": False,
        "packet_role": packet_role,
        "creates_authority": False,
        "mutates_core_state": False,
    }


_CHANGED_FACT_SPEC = {
    "fact_id": "token",
    "summary": "text",
    "source_ref": "token",
    "source_revision": "token",
    "observed_at": "timestamp",
    "freshness": "token",
    "authority": "token",
}
_RECALLED_CLAIM_SPEC = {
    "claim_id": "token",
    "summary": "text",
    "provider_ref": "token",
    "source_ref": "token",
    "source_revision": "token",
    "observed_at": "timestamp",
    "exact_read_verified": "bool",
    "confidence": "score",
}
_REJECTED_CLAIM_SPEC = {
    "claim_id": "token",
    "summary": "text",
    "source_ref": "token",
    "source_revision": "token",
    "observed_at": "timestamp",
    "reason_code": "token",
}
_CONFLICT_SPEC = {
    "conflict_id": "token",
    "summary": "text",
    "source_refs": "tokens",
    "conflict_rule": "token",
    "status": "token",
}
_SOURCE_REVISION_SPEC = {
    "source_ref": "token",
    "revision": "token",
    "observed_at": "timestamp",
    "freshness": "token",
}
_PROVIDER_HEALTH_SPEC = {
    "provider": "token",
    "status": "token",
    "observed_at": "timestamp",
    "reason_code": "token",
    "fail_open": "bool",
}


def build_decision_evidence_packet(
    *,
    goal_id: str,
    decision_id: str,
    observed_at: str,
    changed_facts: Sequence[Mapping[str, Any]] | None = None,
    recalled_claims: Sequence[Mapping[str, Any]] | None = None,
    stale_or_rejected_claims: Sequence[Mapping[str, Any]] | None = None,
    conflicts: Sequence[Mapping[str, Any]] | None = None,
    source_revisions: Sequence[Mapping[str, Any]] | None = None,
    provider_health: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an evidence-only packet without recommendations or raw context."""

    normalized_recalled_claims = _normalize_records(
        recalled_claims,
        field="recalled_claims",
        spec=_RECALLED_CLAIM_SPEC,
        required={
            "claim_id",
            "summary",
            "provider_ref",
            "source_ref",
            "source_revision",
            "observed_at",
            "exact_read_verified",
        },
    )
    if any(not claim["exact_read_verified"] for claim in normalized_recalled_claims):
        raise ValueError(
            "recalled_claims must be exact-read verified; reject stale or "
            "unverified claims instead"
        )
    normalized_provider_health = _normalize_records(
        provider_health,
        field="provider_health",
        spec=_PROVIDER_HEALTH_SPEC,
        required={"provider", "status", "observed_at", "fail_open"},
    )
    if any(not health["fail_open"] for health in normalized_provider_health):
        raise ValueError("provider_health must preserve fail-open behavior")

    packet: dict[str, Any] = {
        "schema_version": DECISION_EVIDENCE_PACKET_SCHEMA_VERSION,
        "goal_id": _compact_token(goal_id, field="goal_id"),
        "decision_id": _compact_token(decision_id, field="decision_id"),
        "observed_at": _iso_timestamp(observed_at, field="observed_at"),
        "visibility": "public_safe",
        "capability": _capability_contract(packet_role="evidence"),
        "changed_facts": _normalize_records(
            changed_facts,
            field="changed_facts",
            spec=_CHANGED_FACT_SPEC,
            required={"fact_id", "summary", "source_ref", "observed_at"},
        ),
        "recalled_claims": normalized_recalled_claims,
        "stale_or_rejected_claims": _normalize_records(
            stale_or_rejected_claims,
            field="stale_or_rejected_claims",
            spec=_REJECTED_CLAIM_SPEC,
            required={"claim_id", "summary", "observed_at", "reason_code"},
        ),
        "conflicts": _normalize_records(
            conflicts,
            field="conflicts",
            spec=_CONFLICT_SPEC,
            required={
                "conflict_id",
                "summary",
                "source_refs",
                "conflict_rule",
                "status",
            },
        ),
        "source_revisions": _normalize_records(
            source_revisions,
            field="source_revisions",
            spec=_SOURCE_REVISION_SPEC,
            required={"source_ref", "revision", "observed_at", "freshness"},
        ),
        "provider_health": normalized_provider_health,
        "provider_fail_open": True,
        "external_writes_performed": False,
        "raw_context_captured": False,
        "credentials_captured": False,
    }
    packet["packet_ref"] = _packet_ref("decision-evidence", packet)
    return packet


_OBJECTIVE_SCORE_SPEC = {
    "objective_id": "token",
    "score": "score",
    "rationale": "text",
}
_RECOMMENDED_DECISION_SPEC = {
    "option_id": "token",
    "summary": "text",
    "rationale": "text",
    "confidence": "score",
}
_ALTERNATIVE_SPEC = {
    "alternative_id": "token",
    "summary": "text",
    "tradeoff": "text",
}
_NEXT_ACTION_SPEC = {
    "action_id": "token",
    "owner_ref": "token",
    "summary": "text",
    "acceptance": "text",
    "stop_condition": "text",
}
_STOP_ITEM_SPEC = {
    "stop_id": "token",
    "summary": "text",
    "condition": "text",
}


def build_decision_proposal(
    *,
    goal_id: str,
    decision_id: str,
    evidence_packet_ref: str,
    observed_at: str,
    objective_scores: Sequence[Mapping[str, Any]],
    recommended_decision: Mapping[str, Any],
    alternatives: Sequence[Mapping[str, Any]] | None = None,
    next_actions: Sequence[Mapping[str, Any]] | None = None,
    stop_list: Sequence[Mapping[str, Any]] | None = None,
    review_at: str,
) -> dict[str, Any]:
    """Build an advisory proposal that references, but cannot replace, evidence."""

    normalized_scores = _normalize_records(
        objective_scores,
        field="objective_scores",
        spec=_OBJECTIVE_SCORE_SPEC,
        required={"objective_id", "score", "rationale"},
    )
    if not normalized_scores:
        raise ValueError("objective_scores must contain at least one objective")
    packet: dict[str, Any] = {
        "schema_version": DECISION_PROPOSAL_SCHEMA_VERSION,
        "goal_id": _compact_token(goal_id, field="goal_id"),
        "decision_id": _compact_token(decision_id, field="decision_id"),
        "evidence_packet_ref": _compact_token(
            evidence_packet_ref,
            field="evidence_packet_ref",
        ),
        "observed_at": _iso_timestamp(observed_at, field="observed_at"),
        "review_at": _iso_timestamp(review_at, field="review_at"),
        "visibility": "public_safe",
        "capability": _capability_contract(packet_role="advisory_proposal"),
        "objective_scores": normalized_scores,
        "recommended_decision": _normalize_record(
            recommended_decision,
            field="recommended_decision",
            spec=_RECOMMENDED_DECISION_SPEC,
            required={"option_id", "summary", "rationale", "confidence"},
        ),
        "alternatives": _normalize_records(
            alternatives,
            field="alternatives",
            spec=_ALTERNATIVE_SPEC,
            required={"alternative_id", "summary", "tradeoff"},
        ),
        "next_actions": _normalize_records(
            next_actions,
            field="next_actions",
            spec=_NEXT_ACTION_SPEC,
            required={
                "action_id",
                "owner_ref",
                "summary",
                "acceptance",
                "stop_condition",
            },
        ),
        "stop_list": _normalize_records(
            stop_list,
            field="stop_list",
            spec=_STOP_ITEM_SPEC,
            required={"stop_id", "summary", "condition"},
        ),
        "authority_confirmation_required": True,
        "external_writes_performed": False,
        "raw_context_captured": False,
    }
    packet["packet_ref"] = _packet_ref("decision-proposal", packet)
    return packet


def build_decision_review_receipt(
    *,
    goal_id: str,
    decision_id: str,
    evidence_packet_ref: str,
    recorded_at: str,
    disposition: str,
    actor_ref: str,
    reason_code: str,
    summary: str,
    proposal_packet_ref: str | None = None,
    gate_todo_id: str | None = None,
    source_event_id: str | None = None,
) -> dict[str, Any]:
    """Record one reviewed proposal or an explicit semantic no-change result.

    Review settlement is deliberately separate from outcome observation. A
    user may approve, reject, or defer a proposal before its real-world outcome
    exists, while a semantic no-change result needs no user authority at all.
    """

    normalized_disposition = _compact_token(
        disposition,
        field="disposition",
    )
    if normalized_disposition not in DECISION_REVIEW_DISPOSITIONS:
        raise ValueError(
            "disposition must be approve, reject, defer, or no_change"
        )
    gated = normalized_disposition != "no_change"
    gated_values = {
        "proposal_packet_ref": proposal_packet_ref,
        "gate_todo_id": gate_todo_id,
        "source_event_id": source_event_id,
    }
    if gated:
        missing = sorted(
            field for field, value in gated_values.items() if value is None
        )
        if missing:
            raise ValueError(
                "gated review receipt is missing required fields: "
                + ", ".join(missing)
            )
    elif any(value is not None for value in gated_values.values()):
        raise ValueError(
            "no_change review receipt must not reference a proposal or user gate"
        )

    packet: dict[str, Any] = {
        "schema_version": DECISION_REVIEW_RECEIPT_SCHEMA_VERSION,
        "goal_id": _compact_token(goal_id, field="goal_id"),
        "decision_id": _compact_token(decision_id, field="decision_id"),
        "evidence_packet_ref": _compact_token(
            evidence_packet_ref,
            field="evidence_packet_ref",
        ),
        "recorded_at": _iso_timestamp(recorded_at, field="recorded_at"),
        "disposition": normalized_disposition,
        "actor_ref": _compact_token(actor_ref, field="actor_ref"),
        "reason_code": _compact_token(reason_code, field="reason_code"),
        "summary": _compact_text(summary, field="summary"),
        "proposal_packet_ref": (
            _compact_token(proposal_packet_ref, field="proposal_packet_ref")
            if proposal_packet_ref is not None
            else None
        ),
        "gate_todo_id": (
            _compact_token(gate_todo_id, field="gate_todo_id")
            if gate_todo_id is not None
            else None
        ),
        "source_event_id": (
            _compact_token(source_event_id, field="source_event_id")
            if source_event_id is not None
            else None
        ),
        "visibility": "public_safe",
        "capability": _capability_contract(packet_role="review_receipt"),
        "authority_confirmation_required": gated,
        "authority_confirmed": gated,
        "quiet_noop": normalized_disposition == "no_change",
        "outcome_observation_required": normalized_disposition == "approve",
        "cursor_commit_allowed": True,
        "external_writes_performed": False,
        "raw_context_captured": False,
    }
    packet["packet_ref"] = _packet_ref("decision-review", packet)
    return packet


_ACCEPTED_DECISION_SPEC = {
    "option_id": "token",
    "summary": "text",
    "accepted_by": "token",
    "accepted_at": "timestamp",
}
_TRANSITION_SPEC = {
    "transition_id": "token",
    "summary": "text",
    "event_ref": "token",
    "status": "token",
}
_OUTCOME_SPEC = {
    "outcome_id": "token",
    "summary": "text",
    "evidence_ref": "token",
    "status": "token",
    "observed_at": "timestamp",
}
_INVALIDATED_ASSUMPTION_SPEC = {
    "assumption_id": "token",
    "summary": "text",
    "evidence_ref": "token",
    "invalidated_at": "timestamp",
}


def build_decision_outcome_receipt(
    *,
    goal_id: str,
    decision_id: str,
    proposal_packet_ref: str,
    recorded_at: str,
    verification_status: str,
    accepted_decision: Mapping[str, Any],
    resulting_transitions: Sequence[Mapping[str, Any]] | None = None,
    observed_outcomes: Sequence[Mapping[str, Any]] | None = None,
    invalidated_assumptions: Sequence[Mapping[str, Any]] | None = None,
    review_at: str,
) -> dict[str, Any]:
    """Build an append-only receipt suitable for existing event/run history."""

    status = _compact_token(verification_status, field="verification_status")
    if status not in DECISION_OUTCOME_VERIFICATION_STATUSES:
        raise ValueError(
            "verification_status must be pending, verified, refuted, or inconclusive"
        )
    normalized_outcomes = _normalize_records(
        observed_outcomes,
        field="observed_outcomes",
        spec=_OUTCOME_SPEC,
        required={"outcome_id", "summary", "evidence_ref", "status", "observed_at"},
    )
    has_verified_outcome = any(
        outcome["status"] == "verified" for outcome in normalized_outcomes
    )
    if status == "verified" and not has_verified_outcome:
        raise ValueError(
            "verified receipts require at least one verified observed outcome"
        )
    packet: dict[str, Any] = {
        "schema_version": DECISION_OUTCOME_RECEIPT_SCHEMA_VERSION,
        "goal_id": _compact_token(goal_id, field="goal_id"),
        "decision_id": _compact_token(decision_id, field="decision_id"),
        "proposal_packet_ref": _compact_token(
            proposal_packet_ref,
            field="proposal_packet_ref",
        ),
        "recorded_at": _iso_timestamp(recorded_at, field="recorded_at"),
        "review_at": _iso_timestamp(review_at, field="review_at"),
        "verification_status": status,
        "visibility": "public_safe",
        "capability": _capability_contract(packet_role="append_only_outcome_receipt"),
        "accepted_decision": _normalize_record(
            accepted_decision,
            field="accepted_decision",
            spec=_ACCEPTED_DECISION_SPEC,
            required={"option_id", "summary", "accepted_by", "accepted_at"},
        ),
        "resulting_transitions": _normalize_records(
            resulting_transitions,
            field="resulting_transitions",
            spec=_TRANSITION_SPEC,
            required={"transition_id", "summary", "event_ref", "status"},
        ),
        "observed_outcomes": normalized_outcomes,
        "invalidated_assumptions": _normalize_records(
            invalidated_assumptions,
            field="invalidated_assumptions",
            spec=_INVALIDATED_ASSUMPTION_SPEC,
            required={
                "assumption_id",
                "summary",
                "evidence_ref",
                "invalidated_at",
            },
        ),
        "reward_memory_candidate_eligible": (
            status == "verified" and has_verified_outcome
        ),
        "external_writes_performed": False,
        "raw_context_captured": False,
    }
    packet["packet_ref"] = _packet_ref("decision-outcome", packet)
    return packet
