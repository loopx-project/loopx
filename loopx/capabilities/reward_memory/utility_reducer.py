"""Deterministic, read-only reduction of post-outcome memory utility.

Stage 1 owns the observation contract.  This module only reduces already
validated observations into a bounded projection; it never calls a provider or
changes memory, ranking, authority, or the main work lane.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import TypedDict

from .memory_utility import (
    ATTRIBUTION_LEVELS,
    RewardMemoryUtilityObservationValidationError,
    _SCOPE_FIELDS,
    validate_reward_memory_utility_observation,
)


MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION = "memory_utility_projection_v0"
MEMORY_UTILITY_REDUCER_VERSION = "memory_utility_reducer_v0"

UTILITY_MIN = -1.0
UTILITY_MAX = 1.0
MAX_PROJECTION_SUBJECTS = 128
MAX_PROJECTION_HISTORY = 256
MAX_PROJECTION_REJECTIONS = 64
MAX_PROJECTION_OBSERVATIONS = 1024
MAX_CONFLICT_FINGERPRINTS = 16
MAX_REVIEW_REASON_CODES = 12
MAX_HISTORY_EVIDENCE_REFS = 16

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_MEMORY_DIGEST_RE = re.compile(r"^(?:[0-9a-f]{16}|sha256:[0-9a-f]{64})$")
_OBSERVATION_ID_RE = re.compile(r"^muo_[0-9a-f]{64}$")
_CONFLICT_FINGERPRINT_RE = re.compile(r"^muof_[0-9a-f]{64}$")
_EVIDENCE_ORDER = (
    "insufficient",
    "evaluator_inference",
    "deterministic_effect",
    "controlled_replay",
    "owner_correction",
)
_EVIDENCE_RANK = {name: index for index, name in enumerate(_EVIDENCE_ORDER)}
_LABEL_DIRECTIONS = {"helpful": 1.0, "harmful": -1.0, "neutral": 0.0}
_UTILITY_LABEL_ORDER = ("helpful", "harmful", "neutral", "unknown")
_PROJECTION_STATUSES = frozenset({"ready", "empty", "review_required", "rejected"})
_REVIEW_STATES = frozenset(
    {"none", "attenuation_proposed", "conflict", "unresolved_attribution"}
)
_REVIEW_ACTIONS = frozenset(
    {"none", "attenuate_or_review", "manual_review", "collect_attribution"}
)
_PROJECTION_BOOL_FIELDS = (
    "ok", "projection_ready", "observation_history_truncated", "read_only",
)
_PROJECTION_FALSE_FIELDS = (
    "grants_new_action_authority", "provider_write_performed",
    "external_writes_performed", "raw_content_captured",
)
_PROJECTION_COUNT_FIELDS = (
    "accepted_observation_count", "duplicate_observation_count",
    "conflicting_observation_count", "rejected_observation_count",
    "lineage_observation_count", "subject_count", "item_subject_count",
    "set_subject_count", "unresolved_lineage_count",
)
_REJECTED_ZERO_COUNT_FIELDS = tuple(
    key for key in _PROJECTION_COUNT_FIELDS if key != "rejected_observation_count"
)
_PROJECTION_FIELDS = frozenset({
    "schema_version", "status", "reducer_version", "reducer_identity", "scope",
    "retrieval_snapshot_ref", "policy_snapshot_ref", "last_observed_at",
    "last_observation_id", "subjects", "observation_history", "rejections",
    "review_proposals", *_PROJECTION_BOOL_FIELDS, *_PROJECTION_FALSE_FIELDS,
    *_PROJECTION_COUNT_FIELDS,
})
_SUBJECT_PARITY_FIELDS = (
    "utility_estimate", "effective_utility_label", "effective_evidence_basis",
    "confidence", "uncertainty", "support", "evidence_strength",
    "evidence_label_summary", "observation_count", "review",
)
_CONFLICT_REJECTION_FIELDS = frozenset({
    "observation_id", "reason_codes", "delivery_count", "delivery_fingerprints",
    "delivery_fingerprints_truncated", "quarantine_proposed",
    "automatic_deletion", "action_authority_granted",
})
_REVIEW_PROPOSAL_FIELDS = frozenset({
    "subject_id", "attribution_level", "memory_ref_digests", "state",
    "proposed_action", "reason_codes", "quarantine_proposed",
    "automatic_deletion", "action_authority_granted",
})


class _ReducerContext(TypedDict):
    scope: dict[str, str]
    retrieval_snapshot_ref: str
    policy_snapshot_ref: str
    reducer_version: str


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} is invalid")
    return value


def _exact_fields(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    keys = set(value)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{label} fields must be strings")
    if keys != expected:
        missing, unknown = sorted(expected - keys), sorted(keys - expected)
        raise ValueError(f"{label} has invalid fields: missing={missing}, unknown={unknown}")


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _str_list(value: object, label: str) -> list[str]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} must be a list of strings")
        result.append(item)
    return result


def _assert_canonical_list(result: list[str], raw: list[object], label: str) -> list[str]:
    if result != raw or result != sorted(result) or len(set(result)) != len(result):
        raise ValueError(f"{label} must be sorted, unique, and canonical")
    return result


def _ref(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an opaque public-safe reference")
    result = value.strip()
    if (
        result != value
        or not _REF_RE.fullmatch(result)
        or "://" in result
        or PurePosixPath(result).is_absolute()
        or PureWindowsPath(result).is_absolute()
    ):
        raise ValueError(f"{label} must be an opaque public-safe reference")
    return result


def _observation_ref(value: object, label: str) -> str:
    result = _ref(value, label)
    if not _OBSERVATION_ID_RE.fullmatch(result):
        raise ValueError(f"{label} must be a canonical observation id")
    return result


def _memory_digests(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 16:
        raise ValueError(f"{label} must contain between 1 and 16 memory digests")
    result: list[str] = []
    for item in value:
        digest = _ref(item, label)
        if not _MEMORY_DIGEST_RE.fullmatch(digest):
            raise ValueError(f"{label} must contain canonical memory digests")
        result.append(digest)
    return _assert_canonical_list(result, value, label)


def _opaque_refs(value: object, label: str, *, maximum: int, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} must contain between {minimum} and {maximum} opaque references")
    return _assert_canonical_list([_ref(item, f"{label}[]") for item in value], value, label)


def _scope(value: object, label: str = "scope") -> dict[str, str]:
    raw = _object(value, label)
    _exact_fields(raw, _SCOPE_FIELDS, label)
    return {key: _ref(raw.get(key), f"{label}.{key}") for key in sorted(_SCOPE_FIELDS)}


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or value.strip() != value:
        raise ValueError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value


def _observation_order_key(observation: Mapping[str, object]) -> tuple[datetime, str, str]:
    created_at = str(observation["created_at"])
    return (
        datetime.fromisoformat(created_at.replace("Z", "+00:00")),
        # Preserve a deterministic tie-breaker for equivalent instants written
        # with different timezone offsets.  Without this, retry order could
        # change the representative timestamp in the projection.
        created_at,
        str(observation["observation_id"]),
    )


def _canonical(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("projection input must be canonical JSON data") from exc
    return encoded.decode("utf-8")


def _digest(value: Mapping[str, object], prefix: str) -> str:
    return f"{prefix}{hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()}"


def _observation_fingerprint(observation: Mapping[str, object]) -> str:
    """Fingerprint one semantic delivery, ignoring retry-only creation time."""

    return _digest({k: v for k, v in observation.items() if k != "created_at"}, "muof_")


def _normalized_context(
    *,
    scope: object,
    retrieval_snapshot_ref: object,
    policy_snapshot_ref: object,
    reducer_version: object,
) -> _ReducerContext:
    return {
        "scope": _scope(scope),
        "retrieval_snapshot_ref": _ref(retrieval_snapshot_ref, "retrieval_snapshot_ref"),
        "policy_snapshot_ref": _ref(policy_snapshot_ref, "policy_snapshot_ref"),
        "reducer_version": _ref(reducer_version, "reducer_version"),
    }


def reward_memory_utility_reducer_identity(
    *,
    scope: Mapping[str, object],
    retrieval_snapshot_ref: str,
    policy_snapshot_ref: str,
    reducer_version: str = MEMORY_UTILITY_REDUCER_VERSION,
) -> str:
    """Return the stable identity of one reducer scope and version."""

    context = _normalized_context(
        scope=scope,
        retrieval_snapshot_ref=retrieval_snapshot_ref,
        policy_snapshot_ref=policy_snapshot_ref,
        reducer_version=reducer_version,
    )
    return _digest({"schema_version": MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION, **context}, "mur_")


def _subject_id(observation: Mapping[str, object]) -> tuple[str, str, tuple[str, ...]]:
    level = str(observation["attribution_level"])
    digests = tuple(_str_list(observation["memory_ref_digests"], "memory_ref_digests"))
    identity: dict[str, object] = {"attribution_level": level, "memory_ref_digests": list(digests)}
    if level == "none":
        identity["application_receipt_id"] = observation["application_receipt_id"]
        return _digest(identity, "mul_"), level, digests
    return _digest(identity, "mui_" if level == "item" else "mus_"), level, digests


class _ObservationContextMismatch(ValueError):
    def __init__(self, reason_codes: Sequence[str]) -> None:
        self.reason_codes = tuple(sorted(set(reason_codes)))
        super().__init__(",".join(self.reason_codes))


def _attribution_counts(subjects: Sequence[Mapping[str, object]]) -> tuple[int, int, int]:
    return (
        sum(item.get("attribution_level") == "item" for item in subjects),
        sum(item.get("attribution_level") == "set" for item in subjects),
        sum(item.get("attribution_level") == "none" for item in subjects),
    )


def _base_projection(
    context: _ReducerContext,
    *,
    ok: bool,
    status: str,
    projection_ready: bool,
    reducer_identity: str,
    accepted_observation_count: int = 0,
    duplicate_observation_count: int = 0,
    conflicting_observation_count: int = 0,
    rejected_observation_count: int = 0,
    lineage_observation_count: int = 0,
    subjects: list[dict[str, object]] | None = None,
    history: list[dict[str, object]] | None = None,
    history_truncated: bool = False,
    rejections: list[dict[str, object]] | None = None,
    review_proposals: list[dict[str, object]] | None = None,
    last_observed_at: str | None = None,
    last_observation_id: str | None = None,
) -> dict[str, object]:
    subject_rows = subjects or []
    item_count, set_count, unresolved_count = _attribution_counts(subject_rows)
    return {
        "ok": ok,
        "schema_version": MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION,
        "status": status,
        "projection_ready": projection_ready,
        "reducer_version": context["reducer_version"],
        "reducer_identity": reducer_identity,
        "scope": dict(context["scope"]),
        "retrieval_snapshot_ref": context["retrieval_snapshot_ref"],
        "policy_snapshot_ref": context["policy_snapshot_ref"],
        "accepted_observation_count": accepted_observation_count,
        "duplicate_observation_count": duplicate_observation_count,
        "conflicting_observation_count": conflicting_observation_count,
        "rejected_observation_count": rejected_observation_count,
        "lineage_observation_count": lineage_observation_count,
        "subject_count": len(subject_rows),
        "item_subject_count": item_count,
        "set_subject_count": set_count,
        "unresolved_lineage_count": unresolved_count,
        "last_observed_at": last_observed_at,
        "last_observation_id": last_observation_id,
        "subjects": subject_rows,
        "observation_history": history or [],
        "observation_history_truncated": history_truncated,
        "rejections": rejections or [],
        "review_proposals": review_proposals or [],
        "read_only": True,
        "grants_new_action_authority": False,
        "provider_write_performed": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }


def _rejected_projection(
    context: _ReducerContext | None, *, reason_codes: list[str], rejected_count: int = 1
) -> dict[str, object]:
    safe_context: _ReducerContext = context or {
        "scope": {key: "rejected" for key in sorted(_SCOPE_FIELDS)},
        "retrieval_snapshot_ref": "rejected",
        "policy_snapshot_ref": "rejected",
        "reducer_version": MEMORY_UTILITY_REDUCER_VERSION,
    }
    projection = _base_projection(
        safe_context,
        ok=False,
        status="rejected",
        projection_ready=False,
        reducer_identity=reward_memory_utility_reducer_identity(**safe_context),
        rejected_observation_count=rejected_count,
        rejections=[{"reason_codes": sorted(set(reason_codes))}],
    )
    # Rejected packets are still public projection packets.  Validate them at
    # the boundary so callers never receive a shape that the readback validator
    # would reject.
    return validate_reward_memory_utility_projection(projection)


def _validate_observation_batch(
    observations: Sequence[Mapping[str, object]], *, context: _ReducerContext
) -> tuple[list[dict[str, object]], list[dict[str, object]], int, int]:
    if isinstance(observations, (str, bytes, bytearray)) or not isinstance(observations, Sequence):
        raise ValueError("observations must be a list")
    if len(observations) > MAX_PROJECTION_OBSERVATIONS:
        raise ValueError("observations exceed the bounded reducer input limit")
    normalized: list[dict[str, object]] = []
    for index, raw in enumerate(observations):
        if not isinstance(raw, Mapping):
            raise ValueError(f"observation[{index}] must be an object")
        try:
            validated = validate_reward_memory_utility_observation(dict(raw))
        except RewardMemoryUtilityObservationValidationError as exc:
            raise RewardMemoryUtilityObservationValidationError(
                exc.reason_code, f"observation[{index}] {exc}"
            ) from exc
        item: dict[str, object] = {str(key): value for key, value in validated.items()}
        mismatches = [
            reason
            for field, expected, reason in (
                ("scope", context["scope"], "scope_mismatch"),
                (
                    "retrieval_snapshot_ref",
                    context["retrieval_snapshot_ref"],
                    "retrieval_snapshot_mismatch",
                ),
                (
                    "policy_snapshot_ref",
                    context["policy_snapshot_ref"],
                    "policy_snapshot_mismatch",
                ),
            )
            if item[field] != expected
        ]
        if mismatches:
            raise _ObservationContextMismatch(mismatches)
        normalized.append(item)

    deliveries: dict[str, dict[str, dict[str, object]]] = {}
    fingerprint_counts: dict[str, dict[str, int]] = {}
    delivery_counts: dict[str, int] = {}
    for item in normalized:
        fingerprint = _observation_fingerprint(item)
        observation_id = str(item["observation_id"])
        versions = deliveries.setdefault(observation_id, {})
        counts = fingerprint_counts.setdefault(observation_id, {})
        prior = versions.get(fingerprint)
        if prior is None or _observation_order_key(item) < _observation_order_key(prior):
            versions[fingerprint] = item
        counts[fingerprint] = counts.get(fingerprint, 0) + 1
        delivery_counts[observation_id] = delivery_counts.get(observation_id, 0) + 1

    unique: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    duplicate_count = conflicting_delivery_count = 0
    for observation_id in sorted(deliveries):
        versions = deliveries[observation_id]
        duplicate_count += sum(max(0, c - 1) for c in fingerprint_counts[observation_id].values())
        if len(versions) > 1:
            conflicting_delivery_count += delivery_counts[observation_id]
            conflicts.append({
                "observation_id": observation_id,
                "reason_codes": ["conflicting_observation_delivery"],
                "delivery_count": delivery_counts[observation_id],
                "delivery_fingerprints": sorted(versions)[:MAX_CONFLICT_FINGERPRINTS],
                "delivery_fingerprints_truncated": len(versions) > MAX_CONFLICT_FINGERPRINTS,
                "quarantine_proposed": True,
                "automatic_deletion": False,
                "action_authority_granted": False,
            })
            continue
        unique.append(next(iter(versions.values())))
    unique.sort(key=lambda item: str(item["observation_id"]))
    return unique, conflicts, duplicate_count, conflicting_delivery_count


def _latest(observations: Sequence[Mapping[str, object]]) -> tuple[str | None, str | None]:
    if not observations:
        return None, None
    latest = max(observations, key=_observation_order_key)
    return str(latest["created_at"]), str(latest["observation_id"])


def _bounded(value: float, lower: float, upper: float) -> float:
    return round(min(upper, max(lower, value)), 6)


def _finite_float(value: object, label: str) -> float:
    """Convert a projection number without allowing overflow to escape validation."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _combined_confidence(observations: Sequence[Mapping[str, object]]) -> float:
    remaining = 1.0
    for observation in observations:
        confidence = _finite_float(observation["confidence"], "observation.confidence")
        remaining *= 1.0 - min(1.0, max(0.0, confidence))
    return _bounded(1.0 - remaining, 0.0, 1.0)


def _evidence_label_summary(observations: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Return sparse sufficient facts for validating truncated projections."""

    result: list[dict[str, object]] = []
    for basis in _EVIDENCE_ORDER:
        for utility_label in _UTILITY_LABEL_ORDER:
            matching = [
                obs for obs in observations
                if obs["evidence_basis"] == basis and obs["utility_label"] == utility_label
            ]
            if matching:
                result.append({
                    "evidence_basis": basis,
                    "utility_label": utility_label,
                    "observation_count": len(matching),
                    "combined_confidence": _combined_confidence(matching),
                })
    return result


def _review(*, state: str, action: str, reason_codes: Sequence[str]) -> dict[str, object]:
    return {
        "state": state,
        "proposed_action": action,
        "reason_codes": sorted(set(reason_codes)),
        "quarantine_proposed": state == "conflict",
        "automatic_deletion": False,
        "action_authority_granted": False,
    }


def _reduce_subject(
    subject_id: str,
    attribution_level: str,
    memory_ref_digests: tuple[str, ...],
    observations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    support = {label: 0 for label in _UTILITY_LABEL_ORDER}
    evidence_strength = {basis: 0 for basis in _EVIDENCE_ORDER}
    for observation in observations:
        support[str(observation["utility_label"])] += 1
        evidence_strength[str(observation["evidence_basis"])] += 1

    strongest_basis = max(
        (basis for basis, count in evidence_strength.items() if count),
        key=lambda basis: (_EVIDENCE_RANK[basis], basis),
    )
    known = [item for item in observations if str(item["utility_label"]) in _LABEL_DIRECTIONS]
    effective_label, effective_basis = "unknown", strongest_basis
    confidence = utility = 0.0
    uncertainty = 1.0
    review_state = review_action = "none"
    review_reasons: list[str] = []

    if attribution_level == "none":
        review_state, review_action = "unresolved_attribution", "collect_attribution"
        review_reasons.append("attribution_not_established")
    elif known:
        strongest_rank = max(_EVIDENCE_RANK[str(item["evidence_basis"])] for item in observations)
        strongest = [
            item for item in observations if _EVIDENCE_RANK[str(item["evidence_basis"])] == strongest_rank
        ]
        effective_basis = str(strongest[0]["evidence_basis"])
        strongest_labels = {str(item["utility_label"]) for item in strongest}
        directional = [item for item in strongest if str(item["utility_label"]) in _LABEL_DIRECTIONS]
        directional_labels = {str(item["utility_label"]) for item in directional}
        has_lower_direction = any(
            str(item["utility_label"]) in _LABEL_DIRECTIONS
            and _EVIDENCE_RANK[str(item["evidence_basis"])] < strongest_rank
            for item in observations
        )
        if "unknown" in strongest_labels:
            # An explicit unknown at the strongest tier is a conservative
            # decision: weaker directional inference cannot manufacture a
            # utility direction that the stronger evidence did not establish.
            if directional or has_lower_direction:
                review_state, review_action = "conflict", "manual_review"
                review_reasons.append("strongest_evidence_unknown")
            if len(directional_labels) > 1:
                review_state, review_action = "conflict", "manual_review"
                review_reasons.append("same_evidence_tier_conflict")
        elif len(directional_labels) != 1:
            effective_basis = max(
                (str(item["evidence_basis"]) for item in strongest),
                key=lambda basis: (_EVIDENCE_RANK[basis], basis),
            )
            review_state, review_action = "conflict", "manual_review"
            review_reasons.append("same_evidence_tier_conflict")
        else:
            effective_label = next(iter(directional_labels))
            effective_basis = str(directional[0]["evidence_basis"])
            confidence = _combined_confidence(directional)
            utility = _bounded(_LABEL_DIRECTIONS[effective_label] * confidence, UTILITY_MIN, UTILITY_MAX)
            uncertainty = _bounded(1.0 - confidence, 0.0, 1.0)
            if effective_label == "harmful":
                review_state, review_action = "attenuation_proposed", "attenuate_or_review"
                review_reasons.append("negative_utility_requires_review")

    last_observed_at, last_observation_id = _latest(observations)
    return {
        "subject_id": subject_id,
        "attribution_level": attribution_level,
        "memory_ref_digests": list(memory_ref_digests),
        "utility_estimate": utility,
        "utility_bounds": {"min": UTILITY_MIN, "max": UTILITY_MAX},
        "effective_utility_label": effective_label,
        "effective_evidence_basis": effective_basis,
        "confidence": confidence,
        "uncertainty": uncertainty,
        "support": support,
        "evidence_strength": evidence_strength,
        "evidence_label_summary": _evidence_label_summary(observations),
        "observation_count": len(observations),
        "last_observed_at": last_observed_at,
        "last_observation_id": last_observation_id,
        "review": _review(state=review_state, action=review_action, reason_codes=review_reasons),
        "read_only": True,
        "automatic_deletion": False,
        "action_authority_granted": False,
    }


def _history_entry(observation: Mapping[str, object], subject_id: str) -> dict[str, object]:
    return {
        "observation_id": observation["observation_id"],
        "observation_fingerprint": _observation_fingerprint(observation),
        "subject_id": subject_id,
        "application_receipt_id": observation["application_receipt_id"],
        "outcome_ref": observation["outcome_ref"],
        "memory_ref_digests": _str_list(observation["memory_ref_digests"], "memory_ref_digests"),
        "attribution_level": observation["attribution_level"],
        "utility_label": observation["utility_label"],
        "evidence_basis": observation["evidence_basis"],
        "confidence": observation["confidence"],
        "reason_codes": _str_list(observation["reason_codes"], "reason_codes"),
        "evidence_refs": _str_list(observation["evidence_refs"], "evidence_refs"),
        "evaluator_ref": observation["evaluator_ref"],
        "evaluation_version": observation["evaluation_version"],
        "created_at": observation["created_at"],
    }


def _bound_history(history: Sequence[dict[str, object]]) -> tuple[list[dict[str, object]], bool]:
    """Retain every subject's latest entry before filling the history budget."""

    ordered = sorted(history, key=_observation_order_key)
    if len(ordered) <= MAX_PROJECTION_HISTORY:
        return ordered, False

    latest_by_subject: dict[str, dict[str, object]] = {}
    for entry in ordered:
        latest_by_subject[str(entry["subject_id"])] = entry

    retained = list(latest_by_subject.values())
    retained_ids = {str(entry["observation_id"]) for entry in retained}
    for entry in reversed(ordered):
        if len(retained) >= MAX_PROJECTION_HISTORY:
            break
        observation_id = str(entry["observation_id"])
        if observation_id not in retained_ids:
            retained.append(entry)
            retained_ids.add(observation_id)
    return sorted(retained, key=_observation_order_key), True


def reduce_reward_memory_utility_observations(
    observations: Sequence[Mapping[str, object]],
    *,
    scope: Mapping[str, object],
    retrieval_snapshot_ref: str,
    policy_snapshot_ref: str,
    reducer_version: str = MEMORY_UTILITY_REDUCER_VERSION,
    previous_projection: Mapping[str, object] | None = None,
    strict: bool = False,
) -> dict[str, object]:
    """Reduce a complete append-only observation stream into a read-only packet.

    Callers should pass the complete observation stream on every invocation.
    Exact duplicate deliveries are collapsed, while a changed payload under the
    same ``observation_id`` is quarantined as a conflicting delivery.  The
    optional previous projection is an identity guard; it is not a second
    source of observations and is never mutated.
    """

    context: _ReducerContext | None = None
    try:
        context = _normalized_context(
            scope=scope,
            retrieval_snapshot_ref=retrieval_snapshot_ref,
            policy_snapshot_ref=policy_snapshot_ref,
            reducer_version=reducer_version,
        )
        identity = reward_memory_utility_reducer_identity(**context)
        if previous_projection is not None:
            prior = validate_reward_memory_utility_projection(previous_projection)
            if (
                prior["scope"] != context["scope"]
                or prior["retrieval_snapshot_ref"] != context["retrieval_snapshot_ref"]
                or prior["policy_snapshot_ref"] != context["policy_snapshot_ref"]
                or prior["reducer_version"] != context["reducer_version"]
                or prior["reducer_identity"] != identity
            ):
                return _rejected_projection(context, reason_codes=["reducer_identity_mismatch"])
        normalized, conflicts, duplicate_count, conflicting_delivery_count = (
            _validate_observation_batch(observations, context=context)
        )
    except RewardMemoryUtilityObservationValidationError as exc:
        if strict:
            raise
        return _rejected_projection(context, reason_codes=[exc.reason_code], rejected_count=1)
    except _ObservationContextMismatch as exc:
        if strict:
            raise
        return _rejected_projection(context, reason_codes=list(exc.reason_codes), rejected_count=1)
    except (OverflowError, ValueError, TypeError):
        if strict:
            raise
        return _rejected_projection(context, reason_codes=["observation_malformed"], rejected_count=1)

    grouped: dict[tuple[str, tuple[str, ...]], list[dict[str, object]]] = {}
    subject_ids: dict[tuple[str, tuple[str, ...]], str] = {}
    for observation in normalized:
        subject_id, level, digests = _subject_id(observation)
        group_key = (level, digests if level != "none" else (subject_id,))
        grouped.setdefault(group_key, []).append(observation)
        subject_ids[group_key] = subject_id

    if len(grouped) > MAX_PROJECTION_SUBJECTS:
        return _rejected_projection(
            context, reason_codes=["projection_subject_limit_exceeded"], rejected_count=len(normalized)
        )

    subjects: list[dict[str, object]] = []
    history: list[dict[str, object]] = []
    review_proposals: list[dict[str, object]] = []
    for group_key in sorted(grouped):
        level, key_digests = group_key
        subject_observations = sorted(grouped[group_key], key=lambda item: str(item["observation_id"]))
        subject_id = subject_ids[group_key]
        digests = (
            tuple(_str_list(subject_observations[0]["memory_ref_digests"], "memory_ref_digests"))
            if level == "none"
            else key_digests
        )
        subject = _reduce_subject(subject_id, level, digests, subject_observations)
        subjects.append(subject)
        history.extend(_history_entry(item, subject_id) for item in subject_observations)
        review = _object(subject["review"], "subject.review")
        if review["state"] != "none":
            review_proposals.append({
                "subject_id": subject_id,
                "attribution_level": level,
                "memory_ref_digests": list(digests),
                **review,
                "reason_codes": _str_list(review["reason_codes"], "review.reason_codes"),
            })

    bounded_history, history_truncated = _bound_history(history)
    rejections = sorted(conflicts, key=lambda item: str(item.get("observation_id") or ""))[
        :MAX_PROJECTION_REJECTIONS
    ]
    accepted_count, conflict_count = len(normalized), len(conflicts)
    subject_conflict = any(proposal["state"] == "conflict" for proposal in review_proposals)
    latest_at, latest_id = _latest(normalized)
    status = (
        "review_required"
        if (conflict_count or subject_conflict)
        else ("ready" if accepted_count else "empty")
    )
    projection = _base_projection(
        context,
        ok=True,
        status=status,
        projection_ready=bool(accepted_count and not conflict_count and not subject_conflict),
        reducer_identity=identity,
        accepted_observation_count=accepted_count,
        duplicate_observation_count=duplicate_count,
        conflicting_observation_count=conflict_count,
        rejected_observation_count=conflicting_delivery_count,
        lineage_observation_count=accepted_count,
        subjects=subjects,
        history=bounded_history,
        history_truncated=history_truncated,
        rejections=rejections,
        review_proposals=review_proposals,
        last_observed_at=latest_at,
        last_observation_id=latest_id,
    )
    return validate_reward_memory_utility_projection(projection)


def _observation_from_history(entry: Mapping[str, object], context: _ReducerContext) -> dict[str, object]:
    """Rebuild the canonical observation fields retained in projection history."""

    normalized = validate_reward_memory_utility_observation({
        "schema_version": "memory_utility_observation_v0",
        "observation_id": entry["observation_id"],
        "scope": dict(context["scope"]),
        "application_receipt_id": entry["application_receipt_id"],
        "memory_ref_digests": _str_list(entry["memory_ref_digests"], "memory_ref_digests"),
        "retrieval_snapshot_ref": context["retrieval_snapshot_ref"],
        "policy_snapshot_ref": context["policy_snapshot_ref"],
        "outcome_ref": entry["outcome_ref"],
        "utility_label": entry["utility_label"],
        "attribution_level": entry["attribution_level"],
        "evidence_basis": entry["evidence_basis"],
        "confidence": entry["confidence"],
        "reason_codes": _str_list(entry["reason_codes"], "reason_codes"),
        "evidence_refs": _str_list(entry["evidence_refs"], "evidence_refs"),
        "evaluator_ref": entry["evaluator_ref"],
        "evaluation_version": entry["evaluation_version"],
        "created_at": entry["created_at"],
        "grants_new_action_authority": False,
        "provider_write_performed": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    })
    if not isinstance(normalized, Mapping):
        raise ValueError("history observation did not normalize to an object")
    return {str(key): value for key, value in normalized.items()}


def _validate_projection_header(raw: Mapping[str, object]) -> tuple[str, _ReducerContext, dict[str, int]]:
    _exact_fields(raw, _PROJECTION_FIELDS, "projection")
    if raw.get("schema_version") != MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION:
        raise ValueError("projection.schema_version mismatch")
    status = _enum(raw.get("status"), _PROJECTION_STATUSES, "projection.status")
    for key in _PROJECTION_BOOL_FIELDS:
        if not isinstance(raw.get(key), bool):
            raise ValueError(f"projection.{key} must be a boolean")
    if raw["read_only"] is not True:
        raise ValueError("projection.read_only must be true")
    for key in _PROJECTION_FALSE_FIELDS:
        if raw.get(key) is not False:
            raise ValueError(f"projection.{key} must be false")
    context = _normalized_context(
        scope=raw.get("scope"),
        retrieval_snapshot_ref=raw.get("retrieval_snapshot_ref"),
        policy_snapshot_ref=raw.get("policy_snapshot_ref"),
        reducer_version=raw.get("reducer_version"),
    )
    if raw.get("reducer_identity") != reward_memory_utility_reducer_identity(**context):
        raise ValueError("projection.reducer_identity is invalid")
    counts = {key: _non_negative_int(raw.get(key), f"projection.{key}") for key in _PROJECTION_COUNT_FIELDS}
    if counts["accepted_observation_count"] > MAX_PROJECTION_OBSERVATIONS:
        raise ValueError("projection.accepted_observation_count exceeds the input limit")
    if (
        counts["duplicate_observation_count"]
        > MAX_PROJECTION_OBSERVATIONS - counts["accepted_observation_count"]
    ):
        raise ValueError("projection.duplicate_observation_count exceeds delivery limit")
    for key in ("conflicting_observation_count", "rejected_observation_count"):
        if counts[key] > MAX_PROJECTION_OBSERVATIONS:
            raise ValueError(f"projection.{key} exceeds the input limit")
    if raw["last_observed_at"] is not None:
        _timestamp(raw["last_observed_at"], "projection.last_observed_at")
    if raw["last_observation_id"] is not None:
        _observation_ref(raw["last_observation_id"], "projection.last_observation_id")
    return status, context, counts


def _validate_projection_status(
    raw: Mapping[str, object],
    counts: Mapping[str, int],
    subjects: Sequence[Mapping[str, object]],
    status: str,
) -> None:
    if status == "rejected":
        if raw["ok"] is not False or raw["projection_ready"] is not False:
            raise ValueError("rejected projection has invalid readiness state")
        if any(counts[key] for key in _REJECTED_ZERO_COUNT_FIELDS):
            raise ValueError("rejected projection must not contain accepted state")
        if subjects or raw["last_observed_at"] is not None or raw["last_observation_id"] is not None:
            raise ValueError("rejected projection must not contain subjects or history")
    elif raw["ok"] is not True:
        raise ValueError("accepted projection must have ok=true")
    if status == "ready" and (
        counts["accepted_observation_count"] < 1
        or counts["conflicting_observation_count"]
        or counts["rejected_observation_count"]
        or raw["projection_ready"] is not True
    ):
        raise ValueError("ready projection has conflicting state")
    if status == "review_required":
        if raw["projection_ready"] is not False:
            raise ValueError("review-required projection must not be ready")
        if not counts["conflicting_observation_count"] and not any(
            _object(subject["review"], "subject.review")["state"] == "conflict" for subject in subjects
        ):
            raise ValueError("review-required projection has no review trigger")
    if status == "empty" and (
        counts["accepted_observation_count"]
        or counts["duplicate_observation_count"]
        or counts["conflicting_observation_count"]
        or counts["rejected_observation_count"]
        or subjects
        or raw["projection_ready"] is not False
    ):
        raise ValueError("empty projection has observation state")


def _validate_projection_history(
    raw: Mapping[str, object],
    counts: Mapping[str, int],
    context: _ReducerContext,
    status: str,
    subject_by_id: Mapping[str, Mapping[str, object]],
    validate_history_entry: Callable[[object, int], None],
) -> None:
    history = raw.get("observation_history")
    if not isinstance(history, list) or len(history) > MAX_PROJECTION_HISTORY:
        raise ValueError("projection.observation_history must be bounded")
    if status == "rejected" and history:
        raise ValueError("rejected projection must not contain observation history")
    history_observations: list[dict[str, object]] = []
    history_subject_counts: dict[str, int] = {}
    history_by_subject: dict[str, list[dict[str, object]]] = {}
    history_ids: list[str] = []
    for index, entry_value in enumerate(history):
        validate_history_entry(entry_value, index)
        entry = _object(entry_value, f"projection.observation_history[{index}]")
        history_ids.append(str(entry["observation_id"]))
        try:
            normalized_entry = _observation_from_history(entry, context)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"projection.observation_history[{index}] is not canonical") from exc
        if entry["observation_fingerprint"] != _observation_fingerprint(normalized_entry):
            raise ValueError(f"projection.observation_history[{index}].observation_fingerprint is invalid")
        expected_subject_id, _, _ = _subject_id(normalized_entry)
        if entry["subject_id"] != expected_subject_id:
            raise ValueError(f"projection.observation_history[{index}].subject_id is invalid")
        if expected_subject_id not in subject_by_id:
            raise ValueError(f"projection.observation_history[{index}] references an unknown subject")
        history_observations.append(normalized_entry)
        history_subject_counts[expected_subject_id] = history_subject_counts.get(expected_subject_id, 0) + 1
        history_by_subject.setdefault(expected_subject_id, []).append(normalized_entry)
    if len(history_ids) != len(set(history_ids)):
        raise ValueError("projection.observation_history contains duplicate ids")
    expected_truncated = counts["accepted_observation_count"] > MAX_PROJECTION_HISTORY
    if raw["observation_history_truncated"] != expected_truncated:
        raise ValueError("projection.observation_history_truncated is inconsistent")
    expected_length = MAX_PROJECTION_HISTORY if expected_truncated else counts["accepted_observation_count"]
    if len(history) != expected_length:
        raise ValueError("projection.observation_history length is inconsistent")
    if history_observations != sorted(history_observations, key=_observation_order_key):
        raise ValueError("projection.observation_history is not chronologically ordered")
    for subject_id, observed_count in history_subject_counts.items():
        subject_count = _non_negative_int(
            subject_by_id[subject_id]["observation_count"], "observation_count"
        )
        if observed_count > subject_count:
            raise ValueError("projection history exceeds subject observation count")
    for subject_id, subject in subject_by_id.items():
        subject_history = history_by_subject.get(subject_id, [])
        if not subject_history:
            raise ValueError(f"projection subject {subject_id} is missing observation history")
        if subject["observation_count"] != len(subject_history) and not raw["observation_history_truncated"]:
            raise ValueError(f"projection subject {subject_id} observation count is inconsistent")
        for entry in subject_history:
            if entry["attribution_level"] != subject["attribution_level"]:
                raise ValueError(f"projection subject {subject_id} attribution level is inconsistent")
            if entry["memory_ref_digests"] != subject["memory_ref_digests"]:
                raise ValueError(f"projection subject {subject_id} memory digests are inconsistent")
        if not raw["observation_history_truncated"]:
            digests = tuple(_str_list(subject["memory_ref_digests"], "memory_ref_digests"))
            expected_subject = _reduce_subject(
                subject_id, str(subject["attribution_level"]), digests, subject_history
            )
            for key in _SUBJECT_PARITY_FIELDS:
                if subject[key] != expected_subject[key]:
                    raise ValueError(f"projection subject {subject_id} {key} is inconsistent")
        expected_subject_last_at, expected_subject_last_id = _latest(subject_history)
        if (
            subject["last_observed_at"] != expected_subject_last_at
            or subject["last_observation_id"] != expected_subject_last_id
        ):
            raise ValueError(
                f"projection subject {subject_id} latest observation fields are inconsistent"
            )
    if history_observations:
        expected_last_at, expected_last_id = _latest(history_observations)
        if raw["last_observed_at"] != expected_last_at or raw["last_observation_id"] != expected_last_id:
            raise ValueError("projection latest observation fields are inconsistent")
    elif raw["last_observed_at"] is not None or raw["last_observation_id"] is not None:
        raise ValueError("projection latest observation fields require history")


def _validate_projection_rejections(
    raw: Mapping[str, object], counts: Mapping[str, int], status: str
) -> None:
    rejections = raw.get("rejections")
    if not isinstance(rejections, list) or len(rejections) > MAX_PROJECTION_REJECTIONS:
        raise ValueError("projection.rejections must be bounded")
    conflict_count = counts["conflicting_observation_count"]
    rejection_ids: list[str] = []
    rejection_delivery_counts: list[int] = []
    known_duplicate_lower_bound = 0
    for index, rejection in enumerate(rejections):
        item = _object(rejection, f"projection.rejections[{index}]")
        if set(item) - _CONFLICT_REJECTION_FIELDS or "reason_codes" not in item:
            raise ValueError("projection rejection shape is invalid")
        if status == "rejected":
            if set(item) != {"reason_codes"}:
                raise ValueError("rejected projection has delivery details")
        elif conflict_count and set(item) != _CONFLICT_REJECTION_FIELDS:
            raise ValueError("conflicting projection rejection shape is invalid")
        if "observation_id" in item:
            rejection_ids.append(
                _observation_ref(item["observation_id"], "projection rejection observation_id")
            )
        reason_codes = _opaque_refs(
            item["reason_codes"], f"projection.rejections[{index}].reason_codes",
            maximum=MAX_REVIEW_REASON_CODES, minimum=1,
        )
        if conflict_count and "conflicting_observation_delivery" not in reason_codes:
            raise ValueError("conflicting rejection reason is missing")
        delivery_count: int | None = None
        if "delivery_count" in item:
            value = item["delivery_count"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError("projection rejection delivery_count is invalid")
            delivery_count = value
            rejection_delivery_counts.append(value)
        truncated = item.get("delivery_fingerprints_truncated")
        if "delivery_fingerprints_truncated" in item and not isinstance(truncated, bool):
            raise ValueError("projection rejection truncation flag is invalid")
        if "delivery_fingerprints" in item:
            fingerprints = _opaque_refs(
                item["delivery_fingerprints"], f"projection.rejections[{index}].delivery_fingerprints",
                maximum=MAX_CONFLICT_FINGERPRINTS, minimum=2,
            )
            if any(not _CONFLICT_FINGERPRINT_RE.fullmatch(v) for v in fingerprints):
                raise ValueError("projection rejection fingerprint is invalid")
            if delivery_count is not None and delivery_count < len(fingerprints):
                raise ValueError("projection rejection delivery_count is too small")
            if truncated is False and delivery_count is not None:
                known_duplicate_lower_bound += delivery_count - len(fingerprints)
            if truncated is True and (
                len(fingerprints) != MAX_CONFLICT_FINGERPRINTS
                or (delivery_count is not None and delivery_count <= len(fingerprints))
            ):
                raise ValueError("projection rejection truncation flag is inconsistent")
        elif conflict_count:
            raise ValueError("conflicting rejection fingerprints are missing")
        if conflict_count:
            if item["quarantine_proposed"] is not True:
                raise ValueError("conflicting rejection must propose quarantine")
            if item["automatic_deletion"] is not False or item["action_authority_granted"] is not False:
                raise ValueError("conflicting rejection boundary is invalid")
    if len(rejection_ids) != len(set(rejection_ids)):
        raise ValueError("projection rejections contain duplicate observation ids")
    if counts["duplicate_observation_count"] < known_duplicate_lower_bound:
        raise ValueError("projection duplicate count is below known delivery duplicates")
    if conflict_count == 0:
        if status == "rejected":
            if not rejections or counts["rejected_observation_count"] < 1:
                raise ValueError("rejected projection must include a rejection reason")
        elif rejections or counts["rejected_observation_count"]:
            raise ValueError("projection has rejected deliveries without conflicts")
    else:
        if len(rejections) != min(conflict_count, MAX_PROJECTION_REJECTIONS):
            raise ValueError("projection rejection count does not match conflicts")
        if counts["rejected_observation_count"] < conflict_count * 2:
            raise ValueError("projection rejected count is inconsistent with conflicts")
        if (
            len(rejections) == conflict_count
            and len(rejection_delivery_counts) == len(rejections)
            and counts["rejected_observation_count"] != sum(rejection_delivery_counts)
        ):
            raise ValueError("projection rejected count does not match delivery facts")


def _validate_projection_review_proposals(
    raw: Mapping[str, object],
    status: str,
    subjects: Sequence[Mapping[str, object]],
    subject_by_id: Mapping[str, Mapping[str, object]],
) -> None:
    proposals = raw.get("review_proposals")
    if not isinstance(proposals, list) or len(proposals) > MAX_PROJECTION_SUBJECTS:
        raise ValueError("projection.review_proposals must be bounded")
    expected_proposal_subjects = {
        str(subject["subject_id"]): subject
        for subject in subjects
        if _object(subject["review"], "subject.review")["state"] != "none"
    }
    seen_proposal_subjects: set[str] = set()
    for index, proposal_value in enumerate(proposals):
        label = f"projection.review_proposals[{index}]"
        proposal = _object(proposal_value, label)
        _exact_fields(proposal, _REVIEW_PROPOSAL_FIELDS, label)
        proposal_subject_id = _ref(proposal["subject_id"], "review proposal subject_id")
        if proposal_subject_id in seen_proposal_subjects:
            raise ValueError("projection review proposals contain duplicate subjects")
        seen_proposal_subjects.add(proposal_subject_id)
        proposal_subject = subject_by_id.get(proposal_subject_id)
        if proposal_subject is None:
            raise ValueError("review proposal references an unknown subject")
        subject_review = _object(proposal_subject["review"], "subject.review")
        if subject_review["state"] == "none":
            raise ValueError("review proposal references a subject without review")
        _enum(proposal["attribution_level"], ATTRIBUTION_LEVELS, "review proposal attribution_level")
        _memory_digests(proposal["memory_ref_digests"], "review proposal memory_ref_digests")
        _enum(proposal["state"], _REVIEW_STATES, "review proposal state")
        _enum(proposal["proposed_action"], _REVIEW_ACTIONS, "review proposal action")
        _opaque_refs(proposal["reason_codes"], f"{label}.reason_codes", maximum=MAX_REVIEW_REASON_CODES)
        if not isinstance(proposal["quarantine_proposed"], bool):
            raise ValueError("review proposal quarantine flag is invalid")
        if proposal["quarantine_proposed"] != (proposal["state"] == "conflict"):
            raise ValueError("review proposal quarantine flag is inconsistent")
        if proposal["automatic_deletion"] is not False or proposal["action_authority_granted"] is not False:
            raise ValueError("review proposal boundary is invalid")
        if proposal["attribution_level"] != proposal_subject["attribution_level"]:
            raise ValueError("review proposal attribution level is inconsistent")
        if proposal["memory_ref_digests"] != proposal_subject["memory_ref_digests"]:
            raise ValueError("review proposal memory digests are inconsistent")
        for key in ("state", "proposed_action", "reason_codes", "quarantine_proposed"):
            if proposal[key] != subject_review[key]:
                raise ValueError(f"review proposal {key} is inconsistent")
    if seen_proposal_subjects != set(expected_proposal_subjects):
        raise ValueError("review proposals do not match subject review state")
    if status == "rejected" and proposals:
        raise ValueError("rejected projection must not contain review proposals")


def validate_reward_memory_utility_projection(projection: Mapping[str, object]) -> dict[str, object]:
    """Validate and return a normalized public utility projection."""

    # Keep readback rules in their own bounded module while retaining this
    # compatibility entry point for existing callers.
    from .utility_projection_validation import (
        _validate_aggregate_subject_semantics,
        _validate_history_entry,
        _validate_subject,
    )

    raw = _object(projection, "projection")
    status, context, counts = _validate_projection_header(raw)
    raw_subjects = raw.get("subjects")
    if not isinstance(raw_subjects, list) or len(raw_subjects) > MAX_PROJECTION_SUBJECTS:
        raise ValueError("projection.subjects must be a bounded list")
    seen_subject_ids: set[str] = set()
    subjects: list[Mapping[str, object]] = []
    subject_by_id: dict[str, Mapping[str, object]] = {}
    for index, subject_value in enumerate(raw_subjects):
        subject = _object(subject_value, f"projection.subjects[{index}]")
        _validate_subject(subject, index, seen_subject_ids)
        _validate_aggregate_subject_semantics(subject, f"projection.subjects[{index}]")
        subjects.append(subject)
        subject_by_id[str(subject["subject_id"])] = subject

    item_count, set_count, unresolved_count = _attribution_counts(subjects)
    for key, actual in (
        ("subject_count", len(subjects)),
        ("item_subject_count", item_count),
        ("set_subject_count", set_count),
        ("unresolved_lineage_count", unresolved_count),
    ):
        if counts[key] != actual:
            raise ValueError(f"projection.{key} does not match subjects")
    if counts["accepted_observation_count"] != sum(
        _non_negative_int(item["observation_count"], "observation_count") for item in subjects
    ):
        raise ValueError("projection.accepted_observation_count does not match subjects")
    if counts["lineage_observation_count"] != counts["accepted_observation_count"]:
        raise ValueError("projection.lineage_observation_count does not match accepted observations")

    _validate_projection_status(raw, counts, subjects, status)
    _validate_projection_history(raw, counts, context, status, subject_by_id, _validate_history_entry)
    _validate_projection_rejections(raw, counts, status)
    _validate_projection_review_proposals(raw, status, subjects, subject_by_id)
    return dict(raw)


# Compatibility-friendly names for callers that prefer a builder or a shorter reducer name.
build_reward_memory_utility_projection = reduce_reward_memory_utility_observations
reduce_reward_memory_utility = reduce_reward_memory_utility_observations


__all__ = [
    "MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION",
    "MEMORY_UTILITY_REDUCER_VERSION",
    "UTILITY_MIN",
    "UTILITY_MAX",
    "build_reward_memory_utility_projection",
    "reduce_reward_memory_utility",
    "reduce_reward_memory_utility_observations",
    "reward_memory_utility_reducer_identity",
    "validate_reward_memory_utility_projection",
]
