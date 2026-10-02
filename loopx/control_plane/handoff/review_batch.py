from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ...public_safe_text import PUBLIC_SAFE_SLUG_PATTERN

REQUEST_SCHEMA = "review_batch_request_v0"
BATCH_SCHEMA = "review_batch_v0"
DECISIONS_SCHEMA = "review_batch_decisions_v0"
DECISION_RECEIPT_SCHEMA = "review_batch_decision_receipt_v0"

_FORBIDDEN_RAW_KEYS = {
    "credential",
    "credentials",
    "private_path",
    "raw_body",
    "raw_content",
    "raw_log",
    "raw_logs",
    "raw_message",
    "raw_transcript",
    "secret",
    "token",
}
_RECEIPT_STATUSES = {"preview", "sent", "failed", "skipped"}


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _text(value: object, label: str, *, maximum: int = 1000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _optional_text(value: object, label: str, *, maximum: int = 1000) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=maximum)


def _token(value: object, label: str) -> str:
    token = _text(value, label, maximum=128).lower()
    if not PUBLIC_SAFE_SLUG_PATTERN.fullmatch(token):
        raise ValueError(f"{label} must be a lower-snake-like public token")
    return token


def _reject_raw_keys(value: object, label: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if key in _FORBIDDEN_RAW_KEYS:
                raise ValueError(f"{label} contains forbidden raw/private field {raw_key!r}")
            _reject_raw_keys(item, f"{label}.{raw_key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_raw_keys(item, f"{label}[{index}]")


def _digest(value: object, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _candidate_decision_digest(candidate: Mapping[str, Any]) -> str:
    digest_input = {
        key: value
        for key, value in candidate.items()
        if key != "decision_digest" and value is not None
    }
    return _digest(digest_input, prefix="candidate")


def _batch_decision_digest(
    *,
    batch_id: str,
    policy: Mapping[str, Any],
    candidate_decision_digests: list[str],
) -> str:
    return _digest(
        {
            "batch_id": batch_id,
            "policy": dict(policy),
            "candidate_decision_digests": candidate_decision_digests,
        },
        prefix="batch",
    )


def _normalize_policy(raw: object) -> dict[str, Any]:
    policy = _object(raw, "policy")
    soft_limit = int(policy.get("soft_limit", 8))
    hard_limit = int(policy.get("hard_limit", max(soft_limit, 10)))
    if not 1 <= soft_limit <= hard_limit <= 50:
        raise ValueError("policy limits must satisfy 1 <= soft_limit <= hard_limit <= 50")
    reason_order = [
        _token(value, f"policy.priority_reason_order[{index}]")
        for index, value in enumerate(
            _list(policy.get("priority_reason_order"), "policy.priority_reason_order")
        )
    ]
    if not reason_order:
        raise ValueError("policy.priority_reason_order must not be empty")
    if len(reason_order) != len(set(reason_order)):
        raise ValueError("policy.priority_reason_order must contain unique codes")
    decision_values = [
        _token(value, f"policy.decision_values[{index}]")
        for index, value in enumerate(
            _list(
                policy.get("decision_values", ["approve", "revise", "hold", "skip"]),
                "policy.decision_values",
            )
        )
    ]
    if not decision_values or len(decision_values) != len(set(decision_values)):
        raise ValueError("policy.decision_values must contain unique values")
    return {
        "soft_limit": soft_limit,
        "hard_limit": hard_limit,
        "priority_reason_order": reason_order,
        "decision_values": decision_values,
    }


def _normalize_priority_reasons(
    raw: object,
    *,
    candidate_label: str,
    reason_order: list[str],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, value in enumerate(_list(raw, f"{candidate_label}.priority_reasons")):
        if isinstance(value, str):
            code = _token(value, f"{candidate_label}.priority_reasons[{index}]")
            detail = code.replace("_", " ")
        else:
            item = _object(value, f"{candidate_label}.priority_reasons[{index}]")
            code = _token(item.get("code"), f"{candidate_label}.priority_reasons[{index}].code")
            detail = _text(
                item.get("detail"),
                f"{candidate_label}.priority_reasons[{index}].detail",
                maximum=400,
            )
        if code not in reason_order:
            raise ValueError(
                f"{candidate_label}.priority_reasons contains unregistered code {code!r}"
            )
        if code not in seen:
            reasons.append({"code": code, "detail": detail})
            seen.add(code)
    if not reasons:
        raise ValueError(f"{candidate_label}.priority_reasons must not be empty")
    reasons.sort(key=lambda item: reason_order.index(item["code"]))
    return reasons


def _normalize_candidate(
    raw: object,
    *,
    source_id: str,
    source_kind: str,
    source_index: int,
    candidate_index: int,
    policy: dict[str, Any],
) -> dict[str, Any]:
    label = f"candidate_sources[{source_index}].candidates[{candidate_index}]"
    candidate = _object(raw, label)
    _reject_raw_keys(candidate, label)
    candidate_id = _token(candidate.get("candidate_id"), f"{label}.candidate_id")
    priority_tier = int(candidate.get("priority_tier", 1))
    if not 0 <= priority_tier <= 9:
        raise ValueError(f"{label}.priority_tier must be between 0 and 9")
    reasons = _normalize_priority_reasons(
        candidate.get("priority_reasons"),
        candidate_label=label,
        reason_order=policy["priority_reason_order"],
    )
    proposal = _object(candidate.get("proposal"), f"{label}.proposal")
    action = _optional_text(proposal.get("action"), f"{label}.proposal.action", maximum=600)
    draft = _optional_text(proposal.get("draft"), f"{label}.proposal.draft", maximum=2000)
    if not action and not draft:
        raise ValueError(f"{label}.proposal requires action or draft")
    evidence_refs = [
        _text(value, f"{label}.evidence_refs[{index}]", maximum=500)
        for index, value in enumerate(_list(candidate.get("evidence_refs", []), f"{label}.evidence_refs"))
    ]
    normalized = {
        "candidate_id": candidate_id,
        "source": {
            "source_id": source_id,
            "source_kind": source_kind,
            "source_ref": _text(candidate.get("source_ref"), f"{label}.source_ref", maximum=500),
        },
        "title": _text(candidate.get("title"), f"{label}.title", maximum=300),
        "summary": _text(candidate.get("summary"), f"{label}.summary", maximum=1200),
        "priority_tier": priority_tier,
        "priority_reasons": reasons,
        "evidence_status": _token(
            candidate.get("evidence_status"), f"{label}.evidence_status"
        ),
        "evidence_refs": evidence_refs,
        "proposal": {key: value for key, value in (("action", action), ("draft", draft)) if value},
        "observed_at": _optional_text(
            candidate.get("observed_at"), f"{label}.observed_at", maximum=80
        ),
    }
    normalized["decision_digest"] = _candidate_decision_digest(normalized)
    return normalized


def _candidate_sort_key(candidate: dict[str, Any], reason_order: list[str]) -> tuple[Any, ...]:
    reason_ranks = tuple(
        reason_order.index(str(item["code"])) for item in candidate["priority_reasons"]
    )
    return (
        int(candidate["priority_tier"]),
        reason_ranks,
        str(candidate["candidate_id"]),
    )


def _normalize_sink_receipts(raw: object) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(_list(raw, "sink_receipts")):
        label = f"sink_receipts[{index}]"
        item = _object(value, label)
        _reject_raw_keys(item, label)
        sink_id = _token(item.get("sink_id"), f"{label}.sink_id")
        if sink_id in seen:
            raise ValueError(f"duplicate sink_id {sink_id!r}")
        seen.add(sink_id)
        status = _token(item.get("status"), f"{label}.status")
        if status not in _RECEIPT_STATUSES:
            raise ValueError(f"{label}.status is invalid")
        receipt = {
            "sink_id": sink_id,
            "sink_kind": _token(item.get("sink_kind"), f"{label}.sink_kind"),
            "status": status,
            "idempotency_key": _optional_text(
                item.get("idempotency_key"), f"{label}.idempotency_key", maximum=300
            ),
            "receipt_ref": _optional_text(
                item.get("receipt_ref"), f"{label}.receipt_ref", maximum=500
            ),
            "readback_verified": bool(item.get("readback_verified", False)),
        }
        if status == "sent" and not (
            receipt["idempotency_key"]
            and receipt["receipt_ref"]
            and receipt["readback_verified"]
        ):
            raise ValueError(
                f"{label} sent receipts require idempotency_key, receipt_ref, and readback_verified=true"
            )
        receipts.append(receipt)
    return receipts


def build_review_batch(request: Mapping[str, Any]) -> dict[str, Any]:
    """Compose one deterministic, provider-neutral review batch."""

    payload = _object(request, "request")
    if payload.get("schema_version") != REQUEST_SCHEMA:
        raise ValueError(f"request must use {REQUEST_SCHEMA}")
    _reject_raw_keys(payload, "request")
    batch_id = _token(payload.get("batch_id"), "batch_id")
    policy = _normalize_policy(payload.get("policy"))
    sources = _list(payload.get("candidate_sources"), "candidate_sources")
    if not sources:
        raise ValueError("candidate_sources must not be empty")
    normalized_candidates: list[dict[str, Any]] = []
    source_summary: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_candidates: set[str] = set()
    for source_index, raw_source in enumerate(sources):
        label = f"candidate_sources[{source_index}]"
        source = _object(raw_source, label)
        source_id = _token(source.get("source_id"), f"{label}.source_id")
        source_kind = _token(source.get("source_kind"), f"{label}.source_kind")
        if source_id in seen_sources:
            raise ValueError(f"duplicate source_id {source_id!r}")
        seen_sources.add(source_id)
        candidates = _list(source.get("candidates"), f"{label}.candidates")
        source_summary.append(
            {"source_id": source_id, "source_kind": source_kind, "candidate_count": len(candidates)}
        )
        for candidate_index, raw_candidate in enumerate(candidates):
            candidate = _normalize_candidate(
                raw_candidate,
                source_id=source_id,
                source_kind=source_kind,
                source_index=source_index,
                candidate_index=candidate_index,
                policy=policy,
            )
            candidate_id = str(candidate["candidate_id"])
            if candidate_id in seen_candidates:
                raise ValueError(f"duplicate candidate_id {candidate_id!r}")
            seen_candidates.add(candidate_id)
            normalized_candidates.append(candidate)

    ordered = sorted(
        normalized_candidates,
        key=lambda candidate: _candidate_sort_key(candidate, policy["priority_reason_order"]),
    )
    bounded = ordered[: policy["hard_limit"]]
    selected = bounded[: policy["soft_limit"]]
    decision_digest = _batch_decision_digest(
        batch_id=batch_id,
        policy=policy,
        candidate_decision_digests=[item["decision_digest"] for item in selected],
    )
    return {
        "ok": True,
        "schema_version": BATCH_SCHEMA,
        "batch_id": batch_id,
        "generated_at": _text(payload.get("generated_at"), "generated_at", maximum=80),
        "policy": policy,
        "source_summary": source_summary,
        "candidate_counts": {
            "input": len(ordered),
            "within_hard_limit": len(bounded),
            "selected": len(selected),
            "overflow": max(0, len(ordered) - len(selected)),
        },
        "candidates": selected,
        "decision_digest": decision_digest,
        "sink_receipts": _normalize_sink_receipts(payload.get("sink_receipts", [])),
        "boundary": {
            "provider_neutral": True,
            "raw_content_persisted": False,
            "external_writes_performed": False,
            "candidate_adapters_execute_outside_core": True,
            "sink_delivery_executes_outside_core": True,
        },
    }


def bind_review_batch_decisions(
    batch: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate decisions against exact batch and candidate digests without effects."""

    batch_payload = _object(batch, "batch")
    decision_payload = _object(decisions, "decisions")
    if batch_payload.get("schema_version") != BATCH_SCHEMA:
        raise ValueError(f"batch must use {BATCH_SCHEMA}")
    if decision_payload.get("schema_version") != DECISIONS_SCHEMA:
        raise ValueError(f"decisions must use {DECISIONS_SCHEMA}")
    _reject_raw_keys(batch_payload, "batch")
    _reject_raw_keys(decision_payload, "decisions")
    batch_id = _token(batch_payload.get("batch_id"), "batch.batch_id")
    if batch_payload.get("batch_id") != batch_id:
        raise ValueError("batch.batch_id must be normalized")
    raw_policy = _object(batch_payload.get("policy"), "batch.policy")
    policy = _normalize_policy(raw_policy)
    if raw_policy != policy:
        raise ValueError("batch.policy must be normalized")
    candidates = _list(batch_payload.get("candidates"), "batch.candidates")
    candidate_by_id: dict[str, dict[str, Any]] = {}
    candidate_digests: list[str] = []
    for index, value in enumerate(candidates):
        label = f"batch.candidates[{index}]"
        candidate = _object(value, label)
        candidate_id = _token(candidate.get("candidate_id"), f"{label}.candidate_id")
        if candidate.get("candidate_id") != candidate_id:
            raise ValueError(f"{label}.candidate_id must be normalized")
        if candidate_id in candidate_by_id:
            raise ValueError(f"duplicate batch candidate_id {candidate_id!r}")
        supplied_candidate_digest = _text(
            candidate.get("decision_digest"), f"{label}.decision_digest"
        )
        expected_candidate_digest = _candidate_decision_digest(candidate)
        if supplied_candidate_digest != expected_candidate_digest:
            raise ValueError(
                f"batch candidate digest does not match candidate {candidate_id!r}"
            )
        candidate_by_id[candidate_id] = candidate
        candidate_digests.append(supplied_candidate_digest)
    expected_batch_digest = _text(batch_payload.get("decision_digest"), "batch.decision_digest")
    recomputed_batch_digest = _batch_decision_digest(
        batch_id=batch_id,
        policy=policy,
        candidate_decision_digests=candidate_digests,
    )
    if expected_batch_digest != recomputed_batch_digest:
        raise ValueError("batch decision digest does not match the exact review batch")
    supplied_batch_digest = _text(
        decision_payload.get("decision_digest"), "decisions.decision_digest"
    )
    if supplied_batch_digest != expected_batch_digest:
        raise ValueError("decision batch digest does not match the exact review batch")
    allowed = set(policy["decision_values"])
    bound: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(_list(decision_payload.get("decisions"), "decisions.decisions")):
        label = f"decisions.decisions[{index}]"
        item = _object(value, label)
        candidate_id = _token(item.get("candidate_id"), f"{label}.candidate_id")
        if candidate_id in seen:
            raise ValueError(f"duplicate decision for {candidate_id!r}")
        seen.add(candidate_id)
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(f"decision references unknown candidate {candidate_id!r}")
        supplied_candidate_digest = _text(
            item.get("candidate_decision_digest"), f"{label}.candidate_decision_digest"
        )
        if supplied_candidate_digest != candidate.get("decision_digest"):
            raise ValueError(f"decision digest does not match candidate {candidate_id!r}")
        decision = _token(item.get("decision"), f"{label}.decision")
        if decision not in allowed:
            raise ValueError(f"decision {decision!r} is not allowed by the batch policy")
        bound.append(
            {
                "candidate_id": candidate_id,
                "candidate_decision_digest": supplied_candidate_digest,
                "decision": decision,
                "note": _optional_text(item.get("note"), f"{label}.note", maximum=600),
            }
        )
    receipt_input = {
        "batch_id": batch_payload.get("batch_id"),
        "decision_digest": expected_batch_digest,
        "decisions": bound,
    }
    return {
        "ok": True,
        "schema_version": DECISION_RECEIPT_SCHEMA,
        **receipt_input,
        "receipt_id": _digest(receipt_input, prefix="decision_receipt"),
        "boundary": {
            "exact_digest_binding": True,
            "external_writes_performed": False,
            "raw_content_persisted": False,
        },
    }
