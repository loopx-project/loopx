"""Readback validation rules for the Reward Memory utility projection.

The reducer owns observation grouping and utility calculation.  This module
owns the structural and semantic checks applied when a public projection is
read back, keeping the two bounded responsibilities independently reviewable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .memory_utility import ATTRIBUTION_LEVELS, EVIDENCE_BASES, UTILITY_LABELS
from .utility_reducer import (
    MAX_HISTORY_EVIDENCE_REFS,
    MAX_REVIEW_REASON_CODES,
    UTILITY_MAX,
    UTILITY_MIN,
    _EVIDENCE_RANK,
    _EVIDENCE_ORDER,
    _LABEL_DIRECTIONS,
    _REVIEW_ACTIONS,
    _REVIEW_STATES,
    _bounded,
    _exact_fields,
    _enum,
    _finite_float,
    _memory_digests,
    _object,
    _observation_ref,
    _opaque_refs,
    _ref,
    _timestamp,
)


def _validate_subject(
    subject: Mapping[str, Any],
    index: int,
    seen_subject_ids: set[str],
) -> None:
    expected = frozenset(
        {
            "subject_id",
            "attribution_level",
            "memory_ref_digests",
            "utility_estimate",
            "utility_bounds",
            "effective_utility_label",
            "effective_evidence_basis",
            "confidence",
            "uncertainty",
            "support",
            "evidence_strength",
            "observation_count",
            "last_observed_at",
            "last_observation_id",
            "review",
            "read_only",
            "automatic_deletion",
            "action_authority_granted",
        }
    )
    label = f"projection.subjects[{index}]"
    _exact_fields(subject, expected, label)
    subject_id = _ref(subject["subject_id"], f"{label}.subject_id")
    if subject_id in seen_subject_ids:
        raise ValueError("projection subjects must be unique")
    seen_subject_ids.add(subject_id)
    level = subject["attribution_level"]
    _enum(level, ATTRIBUTION_LEVELS, f"{label}.attribution_level")
    digests = _memory_digests(
        subject["memory_ref_digests"], f"{label}.memory_ref_digests"
    )
    if level == "item" and len(digests) != 1:
        raise ValueError(f"{label}.item attribution must contain one digest")
    utility = subject["utility_estimate"]
    confidence = subject["confidence"]
    uncertainty = subject["uncertainty"]
    try:
        utility_value = _finite_float(utility, f"{label}.utility_estimate")
        confidence_value = _finite_float(confidence, f"{label}.confidence")
        uncertainty_value = _finite_float(uncertainty, f"{label}.uncertainty")
    except ValueError:
        raise ValueError(f"{label} numeric fields are invalid")
    if not UTILITY_MIN <= utility_value <= UTILITY_MAX:
        raise ValueError(f"{label}.utility_estimate is out of bounds")
    if not 0.0 <= confidence_value <= 1.0 or not 0.0 <= uncertainty_value <= 1.0:
        raise ValueError(f"{label} confidence is out of bounds")
    _enum(
        subject["effective_utility_label"],
        UTILITY_LABELS,
        f"{label}.effective_utility_label",
    )
    _enum(
        subject["effective_evidence_basis"],
        EVIDENCE_BASES,
        f"{label}.effective_evidence_basis",
    )
    bounds = _object(subject["utility_bounds"], f"{label}.utility_bounds")
    _exact_fields(bounds, frozenset({"min", "max"}), f"{label}.utility_bounds")
    if bounds["min"] != UTILITY_MIN or bounds["max"] != UTILITY_MAX:
        raise ValueError(f"{label}.utility_bounds are invalid")
    _counts(
        subject["support"],
        ("helpful", "harmful", "neutral", "unknown"),
        f"{label}.support",
    )
    _counts(subject["evidence_strength"], _EVIDENCE_ORDER, f"{label}.evidence_strength")
    count = subject["observation_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"{label}.observation_count is invalid")
    if sum(subject["support"].values()) != count:
        raise ValueError(f"{label}.support does not match observation_count")
    if sum(subject["evidence_strength"].values()) != count:
        raise ValueError(f"{label}.evidence_strength does not match observation_count")
    if subject["evidence_strength"][subject["effective_evidence_basis"]] < 1:
        raise ValueError(
            f"{label}.effective_evidence_basis has no supporting observation"
        )
    expected_utility = {
        "helpful": _bounded(confidence_value, UTILITY_MIN, UTILITY_MAX),
        "harmful": _bounded(-confidence_value, UTILITY_MIN, UTILITY_MAX),
        "neutral": 0.0,
        "unknown": 0.0,
    }[subject["effective_utility_label"]]
    if utility_value != expected_utility:
        raise ValueError(f"{label}.utility_estimate is inconsistent with label")
    if uncertainty_value != _bounded(1.0 - confidence_value, 0.0, 1.0):
        raise ValueError(f"{label}.uncertainty is inconsistent with confidence")
    _timestamp(subject["last_observed_at"], f"{label}.last_observed_at")
    _observation_ref(subject["last_observation_id"], f"{label}.last_observation_id")
    review = _object(subject["review"], f"{label}.review")
    _validate_review(review, f"{label}.review")
    known_count = sum(
        subject["support"][key] for key in ("helpful", "harmful", "neutral")
    )
    if level == "none":
        expected_review = ("unresolved_attribution", "collect_attribution")
    elif subject["effective_utility_label"] == "harmful":
        expected_review = ("attenuation_proposed", "attenuate_or_review")
    elif subject["effective_utility_label"] == "unknown" and known_count:
        expected_review = ("conflict", "manual_review")
    else:
        expected_review = ("none", "none")
    if (review["state"], review["proposed_action"]) != expected_review:
        raise ValueError(f"{label}.review is inconsistent with effective utility")
    if (
        subject["read_only"] is not True
        or subject["automatic_deletion"] is not False
        or subject["action_authority_granted"] is not False
    ):
        raise ValueError(f"{label} boundary is invalid")


def _validate_aggregate_subject_semantics(
    subject: Mapping[str, Any],
    label: str,
) -> None:
    """Validate subject facts that remain derivable after history truncation."""

    evidence_strength = subject["evidence_strength"]
    strongest_basis = max(
        (basis for basis, count in evidence_strength.items() if count),
        key=lambda basis: (_EVIDENCE_RANK[basis], basis),
    )
    if subject["effective_evidence_basis"] != strongest_basis:
        raise ValueError(f"{label}.effective_evidence_basis is inconsistent")

    support = subject["support"]
    effective_label = subject["effective_utility_label"]
    if effective_label in _LABEL_DIRECTIONS and support[effective_label] < 1:
        raise ValueError(
            f"{label}.effective_utility_label has no supporting observation"
        )
    if effective_label == "unknown" and support["unknown"] == 0:
        review = subject["review"]
        if (
            review["state"] != "conflict"
            or "same_evidence_tier_conflict" not in review["reason_codes"]
            or evidence_strength[strongest_basis] < 2
        ):
            raise ValueError(f"{label}.unknown utility has no supporting evidence")


def _counts(value: object, keys: Sequence[str], label: str) -> None:
    mapping = _object(value, label)
    _exact_fields(mapping, frozenset(keys), label)
    for key in keys:
        count = mapping[key]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{label}.{key} must be a non-negative integer")


def _validate_review(value: Mapping[str, Any], label: str) -> None:
    _exact_fields(
        value,
        frozenset(
            {
                "state",
                "proposed_action",
                "reason_codes",
                "quarantine_proposed",
                "automatic_deletion",
                "action_authority_granted",
            }
        ),
        label,
    )
    _enum(value["state"], _REVIEW_STATES, f"{label}.state")
    _enum(value["proposed_action"], _REVIEW_ACTIONS, f"{label}.proposed_action")
    _opaque_refs(
        value["reason_codes"],
        f"{label}.reason_codes",
        maximum=MAX_REVIEW_REASON_CODES,
    )
    if (value["state"] != "none") != bool(value["reason_codes"]):
        raise ValueError(f"{label}.reason_codes are inconsistent with state")
    if not isinstance(value["quarantine_proposed"], bool):
        raise ValueError(f"{label}.quarantine_proposed is invalid")
    if value["quarantine_proposed"] != (value["state"] == "conflict"):
        raise ValueError(f"{label}.quarantine_proposed is inconsistent")
    if (
        value["automatic_deletion"] is not False
        or value["action_authority_granted"] is not False
    ):
        raise ValueError(f"{label} boundary is invalid")


def _validate_history_entry(value: object, index: int) -> None:
    entry = _object(value, f"projection.observation_history[{index}]")
    expected = frozenset(
        {
            "observation_id",
            "observation_fingerprint",
            "subject_id",
            "application_receipt_id",
            "outcome_ref",
            "memory_ref_digests",
            "attribution_level",
            "utility_label",
            "evidence_basis",
            "confidence",
            "reason_codes",
            "evidence_refs",
            "evaluator_ref",
            "evaluation_version",
            "created_at",
        }
    )
    _exact_fields(entry, expected, f"projection.observation_history[{index}]")
    for key in (
        "observation_id",
        "subject_id",
        "application_receipt_id",
        "outcome_ref",
        "evaluator_ref",
        "evaluation_version",
    ):
        if key == "observation_id":
            _observation_ref(entry[key], f"history.{key}")
        else:
            _ref(entry[key], f"history.{key}")
    fingerprint = entry["observation_fingerprint"]
    if not isinstance(fingerprint, str) or not re.fullmatch(
        r"muof_[0-9a-f]{64}", fingerprint
    ):
        raise ValueError("history.observation_fingerprint is invalid")
    _memory_digests(entry["memory_ref_digests"], "history.memory_ref_digests")
    _enum(entry["attribution_level"], ATTRIBUTION_LEVELS, "history attribution_level")
    _enum(entry["utility_label"], UTILITY_LABELS, "history utility_label")
    _enum(entry["evidence_basis"], EVIDENCE_BASES, "history evidence_basis")
    _opaque_refs(
        entry["reason_codes"],
        "history.reason_codes",
        maximum=MAX_REVIEW_REASON_CODES,
    )
    _opaque_refs(
        entry["evidence_refs"],
        "history.evidence_refs",
        maximum=MAX_HISTORY_EVIDENCE_REFS,
    )
    confidence = entry["confidence"]
    try:
        confidence_value = _finite_float(confidence, "history.confidence")
    except ValueError:
        raise ValueError("history confidence is invalid")
    if not 0.0 <= confidence_value <= 1.0:
        raise ValueError("history confidence is invalid")
    _timestamp(entry["created_at"], "history.created_at")


__all__ = [
    "_validate_aggregate_subject_semantics",
    "_validate_history_entry",
    "_validate_subject",
]
