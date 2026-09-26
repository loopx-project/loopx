from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .application import (
    APPLICATION_OUTCOMES,
    RECALL_MODES,
    RECALL_QUERY_KINDS,
    REWARD_MEMORY_APPLICATION_RECEIPT_SCHEMA_VERSION,
)


MEMORY_UTILITY_OBSERVATION_SCHEMA_VERSION = "memory_utility_observation_v0"

UTILITY_LABELS = frozenset({"helpful", "harmful", "neutral", "unknown"})
ATTRIBUTION_LEVELS = frozenset({"item", "set", "none"})
EVIDENCE_BASES = frozenset(
    {
        "owner_correction",
        "controlled_replay",
        "deterministic_effect",
        "evaluator_inference",
        "insufficient",
    }
)

_EXISTING_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$")
_OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_MEMORY_REF_DIGEST_RE = re.compile(r"^(?:[0-9a-f]{16}|sha256:[0-9a-f]{64})$")
_MAX_MEMORY_DIGESTS = 16
_MAX_REASON_CODES = 12
_MAX_EVIDENCE_REFS = 16

_APPLICATION_RECEIPT_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "application_id",
        "artifact_ref",
        "corpus_id",
        "surface_id",
        "mode",
        "query_kind",
        "query_evidence",
        "outcome",
        "memory_ref_digests",
        "reasoning_summary",
        "current_artifact_verified",
        "result_readback_verified",
        "provider_call_count",
        "model_reasoning_preserved",
        "grants_new_action_authority",
        "external_writes_performed",
        "raw_content_captured",
    }
)
_APPLICATION_RECEIPT_OPTIONAL_FIELDS = frozenset(
    {"retrieval_snapshot_ref", "policy_snapshot_ref"}
)
_QUERY_EVIDENCE_FIELDS = frozenset(
    {"query_digest", "query_summary", "exact_query_exposed"}
)

_VERIFIED_OUTCOME_FIELDS = frozenset(
    {"verified", "outcome_ref", "artifact_ref", "outcome_status"}
)
_ATTRIBUTION_CONTEXT_FIELDS = frozenset(
    {"scope", "retrieval_snapshot_ref", "policy_snapshot_ref"}
)
_SCOPE_FIELDS = frozenset({"agent_id", "project_id", "corpus_id", "surface_id"})
_EVALUATOR_PROPOSAL_FIELDS = frozenset(
    {
        "scope",
        "application_id",
        "artifact_ref",
        "outcome_ref",
        "outcome_status",
        "retrieval_snapshot_ref",
        "policy_snapshot_ref",
        "memory_ref_digests",
        "utility_label",
        "attribution_level",
        "evidence_basis",
        "confidence",
        "reason_codes",
        "evidence_refs",
        "evaluator_ref",
        "evaluation_version",
    }
)
_OBSERVATION_FIELDS = frozenset(
    {
        "schema_version",
        "observation_id",
        "scope",
        "application_receipt_id",
        "memory_ref_digests",
        "retrieval_snapshot_ref",
        "policy_snapshot_ref",
        "outcome_ref",
        "utility_label",
        "attribution_level",
        "evidence_basis",
        "confidence",
        "reason_codes",
        "evidence_refs",
        "evaluator_ref",
        "evaluation_version",
        "created_at",
        "grants_new_action_authority",
        "provider_write_performed",
        "external_writes_performed",
        "raw_content_captured",
    }
)


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], label: str
) -> None:
    keys = set(value)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{label} fields must be strings")
    if keys == expected:
        return
    missing = sorted(expected - keys)
    unknown = sorted(keys - expected)
    raise ValueError(
        f"{label} has invalid fields: missing={missing}, unknown={unknown}"
    )


def _existing_token(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not _EXISTING_TOKEN_RE.fullmatch(result):
        raise ValueError(f"{label} must be a compact public-safe token")
    return result


def _opaque_ref(value: object, label: str) -> str:
    result = str(value or "").strip()
    if (
        not _OPAQUE_REF_RE.fullmatch(result)
        or "://" in result
        or PurePosixPath(result).is_absolute()
        or PureWindowsPath(result).is_absolute()
    ):
        raise ValueError(f"{label} must be an opaque public-safe reference")
    return result


def _timestamp(value: object, label: str) -> str:
    result = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return result


def _false_boundary(value: Mapping[str, Any], key: str, label: str) -> bool:
    if value.get(key) is not False:
        raise ValueError(f"{label}.{key} must be false")
    return False


def _boolean(value: Mapping[str, Any], key: str, label: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        raise ValueError(f"{label}.{key} must be a boolean")
    return result


def _non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _compact_text(value: object, label: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be compact public-safe text")
    result = value.strip()
    if not result or len(result) > maximum or any(char in result for char in "\r\n"):
        raise ValueError(f"{label} must be compact public-safe text")
    return result


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence must be a finite number between 0 and 1")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("confidence must be a finite number between 0 and 1") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("confidence must be a finite number between 0 and 1")
    return result


def _refs(
    value: object,
    label: str,
    *,
    maximum: int,
    minimum: int = 0,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(
            f"{label} must be a list containing between {minimum} and "
            f"{maximum} opaque references"
        )
    result = sorted(_opaque_ref(item, label) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def normalize_reward_memory_ref_digests(
    value: object,
    label: str,
    *,
    minimum: int = 1,
) -> list[str]:
    result = _refs(
        value,
        label,
        minimum=minimum,
        maximum=_MAX_MEMORY_DIGESTS,
    )
    if any(not _MEMORY_REF_DIGEST_RE.fullmatch(item) for item in result):
        raise ValueError(f"{label} must contain only canonical opaque memory digests")
    return result


def _scope(value: object, label: str) -> dict[str, str]:
    raw = _object(value, label)
    _require_exact_fields(raw, _SCOPE_FIELDS, label)
    return {
        key: _opaque_ref(raw.get(key), f"{label}.{key}")
        for key in ("agent_id", "project_id", "corpus_id", "surface_id")
    }


def _canonical_digest(value: Mapping[str, Any], *, prefix: str) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("application_receipt must be canonical JSON data") from exc
    return f"{prefix}{hashlib.sha256(encoded).hexdigest()}"


def _validate_application_receipt_envelope(raw: Mapping[str, Any]) -> None:
    keys = set(raw)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError("application_receipt fields must be strings")
    missing = sorted(_APPLICATION_RECEIPT_REQUIRED_FIELDS - keys)
    unknown = sorted(
        keys
        - _APPLICATION_RECEIPT_REQUIRED_FIELDS
        - _APPLICATION_RECEIPT_OPTIONAL_FIELDS
    )
    if missing or unknown:
        raise ValueError(
            "application_receipt has invalid fields: "
            f"missing={missing}, unknown={unknown}"
        )

    mode = str(raw.get("mode") or "").strip()
    if mode not in RECALL_MODES:
        raise ValueError("application_receipt.mode is invalid")
    query_kind = str(raw.get("query_kind") or "").strip()
    if query_kind not in RECALL_QUERY_KINDS:
        raise ValueError("application_receipt.query_kind is invalid")
    query_evidence = raw.get("query_evidence")
    if not isinstance(query_evidence, list) or len(query_evidence) > 3:
        raise ValueError("application_receipt.query_evidence must be a bounded list")
    for index, item in enumerate(query_evidence):
        label = f"application_receipt.query_evidence[{index}]"
        evidence = _object(item, label)
        _require_exact_fields(evidence, _QUERY_EVIDENCE_FIELDS, label)
        digest = str(evidence.get("query_digest") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{16}", digest):
            raise ValueError(f"{label}.query_digest must be a canonical digest")
        _compact_text(
            evidence.get("query_summary"), f"{label}.query_summary", maximum=220
        )
        if evidence.get("exact_query_exposed") is not False:
            raise ValueError(f"{label}.exact_query_exposed must be false")

    _compact_text(
        raw.get("reasoning_summary"),
        "application_receipt.reasoning_summary",
        maximum=500,
    )
    _boolean(raw, "current_artifact_verified", "application_receipt")
    _boolean(raw, "result_readback_verified", "application_receipt")
    if _boolean(raw, "model_reasoning_preserved", "application_receipt") is not True:
        raise ValueError("application_receipt.model_reasoning_preserved must be true")
    _non_negative_integer(
        raw.get("provider_call_count"), "application_receipt.provider_call_count"
    )


def reward_memory_application_receipt_id(value: Mapping[str, Any]) -> str:
    """Return the stable opaque id for one complete application receipt."""

    _, receipt_id = _application_receipt(value, minimum_memory_digests=0)
    return receipt_id


def _application_receipt(
    value: object,
    *,
    minimum_memory_digests: int = 1,
) -> tuple[dict[str, Any], str]:
    raw = _object(value, "application_receipt")
    if raw.get("schema_version") != REWARD_MEMORY_APPLICATION_RECEIPT_SCHEMA_VERSION:
        raise ValueError(
            "application_receipt must use reward_memory_application_receipt_v0"
        )
    _validate_application_receipt_envelope(raw)
    outcome = str(raw.get("outcome") or "").strip()
    if outcome not in APPLICATION_OUTCOMES:
        raise ValueError("application_receipt.outcome is invalid")
    memory_ref_digests = normalize_reward_memory_ref_digests(
        raw.get("memory_ref_digests"),
        "application_receipt.memory_ref_digests",
        minimum=minimum_memory_digests,
    )
    if memory_ref_digests != raw.get("memory_ref_digests"):
        raise ValueError(
            "application_receipt.memory_ref_digests must be sorted and canonical"
        )
    normalized: dict[str, Any] = {
        "application_id": _existing_token(
            raw.get("application_id"), "application_receipt.application_id"
        ),
        "artifact_ref": _existing_token(
            raw.get("artifact_ref"), "application_receipt.artifact_ref"
        ),
        "corpus_id": _opaque_ref(raw.get("corpus_id"), "application_receipt.corpus_id"),
        "surface_id": _opaque_ref(
            raw.get("surface_id"), "application_receipt.surface_id"
        ),
        "outcome": outcome,
        "memory_ref_digests": memory_ref_digests,
        "grants_new_action_authority": _false_boundary(
            raw, "grants_new_action_authority", "application_receipt"
        ),
        "external_writes_performed": _false_boundary(
            raw, "external_writes_performed", "application_receipt"
        ),
        "raw_content_captured": _false_boundary(
            raw, "raw_content_captured", "application_receipt"
        ),
    }
    for key in ("retrieval_snapshot_ref", "policy_snapshot_ref"):
        if key in raw:
            normalized[key] = _opaque_ref(raw.get(key), f"application_receipt.{key}")
    # Bind the complete v0 receipt rather than a hand-picked identity subset.
    return normalized, _canonical_digest(raw, prefix="rma_")


def _verified_outcome(value: object) -> dict[str, Any]:
    raw = _object(value, "verified_outcome")
    _require_exact_fields(raw, _VERIFIED_OUTCOME_FIELDS, "verified_outcome")
    if raw.get("verified") is not True:
        raise ValueError("verified_outcome.verified must be true")
    return {
        "verified": True,
        "outcome_ref": _opaque_ref(
            raw.get("outcome_ref"), "verified_outcome.outcome_ref"
        ),
        "artifact_ref": _existing_token(
            raw.get("artifact_ref"), "verified_outcome.artifact_ref"
        ),
        "outcome_status": _opaque_ref(
            raw.get("outcome_status"), "verified_outcome.outcome_status"
        ),
    }


def _attribution_context(value: object) -> dict[str, Any]:
    raw = _object(value, "attribution_context")
    _require_exact_fields(raw, _ATTRIBUTION_CONTEXT_FIELDS, "attribution_context")
    return {
        "scope": _scope(raw.get("scope"), "attribution_context.scope"),
        "retrieval_snapshot_ref": _opaque_ref(
            raw.get("retrieval_snapshot_ref"),
            "attribution_context.retrieval_snapshot_ref",
        ),
        "policy_snapshot_ref": _opaque_ref(
            raw.get("policy_snapshot_ref"), "attribution_context.policy_snapshot_ref"
        ),
    }


def _utility_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    utility_label = str(value.get("utility_label") or "").strip()
    attribution_level = str(value.get("attribution_level") or "").strip()
    evidence_basis = str(value.get("evidence_basis") or "").strip()
    if utility_label not in UTILITY_LABELS:
        raise ValueError("utility_label is invalid")
    if attribution_level not in ATTRIBUTION_LEVELS:
        raise ValueError("attribution_level is invalid")
    if evidence_basis not in EVIDENCE_BASES:
        raise ValueError("evidence_basis is invalid")

    evidence_refs = _refs(
        value.get("evidence_refs"),
        "evidence_refs",
        maximum=_MAX_EVIDENCE_REFS,
    )
    if evidence_basis == "insufficient" and utility_label != "unknown":
        raise ValueError("insufficient evidence must produce unknown utility")
    if (
        evidence_basis
        in {
            "owner_correction",
            "controlled_replay",
            "deterministic_effect",
        }
        and not evidence_refs
    ):
        raise ValueError("strong evidence basis requires evidence_refs")
    if attribution_level == "none" and utility_label != "unknown":
        raise ValueError("none attribution must produce unknown utility")
    if utility_label != "unknown" and not evidence_refs:
        raise ValueError("non-unknown utility requires evidence_refs")

    return {
        "utility_label": utility_label,
        "attribution_level": attribution_level,
        "evidence_basis": evidence_basis,
        "confidence": _confidence(value.get("confidence")),
        "reason_codes": _refs(
            value.get("reason_codes"), "reason_codes", maximum=_MAX_REASON_CODES
        ),
        "evidence_refs": evidence_refs,
        "evaluator_ref": _opaque_ref(value.get("evaluator_ref"), "evaluator_ref"),
        "evaluation_version": _opaque_ref(
            value.get("evaluation_version"), "evaluation_version"
        ),
    }


def _validate_attribution_subject(
    memory_ref_digests: list[str],
    *,
    attribution_level: str,
    application_memory_ref_digests: list[str],
) -> None:
    if attribution_level == "item":
        if (
            len(memory_ref_digests) != 1
            or memory_ref_digests[0] not in application_memory_ref_digests
        ):
            raise ValueError(
                "item attribution must bind exactly one digest from the application receipt"
            )
        return
    if memory_ref_digests != application_memory_ref_digests:
        raise ValueError(
            "set or none attribution must bind the complete application memory set"
        )


def _validate_bindings(
    application_receipt: Mapping[str, Any],
    verified_outcome: Mapping[str, Any],
    attribution_context: Mapping[str, Any],
) -> None:
    if verified_outcome["artifact_ref"] != application_receipt["artifact_ref"]:
        raise ValueError(
            "verified_outcome.artifact_ref must match the application receipt"
        )
    scope = attribution_context["scope"]
    if scope["corpus_id"] != application_receipt["corpus_id"]:
        raise ValueError(
            "attribution_context.scope.corpus_id must match the application receipt"
        )
    if scope["surface_id"] != application_receipt["surface_id"]:
        raise ValueError(
            "attribution_context.scope.surface_id must match the application receipt"
        )
    for key in ("retrieval_snapshot_ref", "policy_snapshot_ref"):
        expected = application_receipt.get(key)
        if expected is not None and attribution_context[key] != expected:
            raise ValueError(
                f"attribution_context.{key} must match the application receipt"
            )


def _evaluator_proposal(
    value: object,
    *,
    application_receipt: Mapping[str, Any],
    verified_outcome: Mapping[str, Any],
    attribution_context: Mapping[str, Any],
) -> dict[str, Any]:
    raw = _object(value, "evaluator_proposal")
    _require_exact_fields(raw, _EVALUATOR_PROPOSAL_FIELDS, "evaluator_proposal")
    proposal = {
        "scope": _scope(raw.get("scope"), "evaluator_proposal.scope"),
        "application_id": _existing_token(
            raw.get("application_id"), "evaluator_proposal.application_id"
        ),
        "artifact_ref": _existing_token(
            raw.get("artifact_ref"), "evaluator_proposal.artifact_ref"
        ),
        "outcome_ref": _opaque_ref(
            raw.get("outcome_ref"), "evaluator_proposal.outcome_ref"
        ),
        "outcome_status": _opaque_ref(
            raw.get("outcome_status"), "evaluator_proposal.outcome_status"
        ),
        "retrieval_snapshot_ref": _opaque_ref(
            raw.get("retrieval_snapshot_ref"),
            "evaluator_proposal.retrieval_snapshot_ref",
        ),
        "policy_snapshot_ref": _opaque_ref(
            raw.get("policy_snapshot_ref"), "evaluator_proposal.policy_snapshot_ref"
        ),
        "memory_ref_digests": normalize_reward_memory_ref_digests(
            raw.get("memory_ref_digests"),
            "evaluator_proposal.memory_ref_digests",
        ),
    } | _utility_fields(raw)

    expected_echoes = {
        "scope": attribution_context["scope"],
        "application_id": application_receipt["application_id"],
        "artifact_ref": verified_outcome["artifact_ref"],
        "outcome_ref": verified_outcome["outcome_ref"],
        "outcome_status": verified_outcome["outcome_status"],
        "retrieval_snapshot_ref": attribution_context["retrieval_snapshot_ref"],
        "policy_snapshot_ref": attribution_context["policy_snapshot_ref"],
    }
    for key, expected in expected_echoes.items():
        if proposal[key] != expected:
            raise ValueError(f"evaluator_proposal.{key} does not match trusted context")
    _validate_attribution_subject(
        proposal["memory_ref_digests"],
        attribution_level=proposal["attribution_level"],
        application_memory_ref_digests=application_receipt["memory_ref_digests"],
    )
    return proposal


def _observation_id(observation: Mapping[str, Any]) -> str:
    identity = {
        key: observation[key]
        for key in (
            "schema_version",
            "scope",
            "application_receipt_id",
            "memory_ref_digests",
            "retrieval_snapshot_ref",
            "policy_snapshot_ref",
            "outcome_ref",
            "attribution_level",
            "evidence_basis",
            "evidence_refs",
            "evaluator_ref",
            "evaluation_version",
        )
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"muo_{hashlib.sha256(encoded).hexdigest()}"


def _normalize_observation(value: object) -> dict[str, Any]:
    raw = _object(value, "observation")
    _require_exact_fields(raw, _OBSERVATION_FIELDS, "observation")
    if raw.get("schema_version") != MEMORY_UTILITY_OBSERVATION_SCHEMA_VERSION:
        raise ValueError(
            "observation.schema_version must be memory_utility_observation_v0"
        )
    for key in (
        "grants_new_action_authority",
        "provider_write_performed",
        "external_writes_performed",
        "raw_content_captured",
    ):
        _false_boundary(raw, key, "observation")

    normalized = (
        {
            "schema_version": MEMORY_UTILITY_OBSERVATION_SCHEMA_VERSION,
            "observation_id": _opaque_ref(
                raw.get("observation_id"), "observation.observation_id"
            ),
            "scope": _scope(raw.get("scope"), "observation.scope"),
            "application_receipt_id": _opaque_ref(
                raw.get("application_receipt_id"),
                "observation.application_receipt_id",
            ),
            "memory_ref_digests": normalize_reward_memory_ref_digests(
                raw.get("memory_ref_digests"),
                "observation.memory_ref_digests",
            ),
            "retrieval_snapshot_ref": _opaque_ref(
                raw.get("retrieval_snapshot_ref"),
                "observation.retrieval_snapshot_ref",
            ),
            "policy_snapshot_ref": _opaque_ref(
                raw.get("policy_snapshot_ref"), "observation.policy_snapshot_ref"
            ),
            "outcome_ref": _opaque_ref(
                raw.get("outcome_ref"), "observation.outcome_ref"
            ),
        }
        | _utility_fields(raw)
        | {
            "created_at": _timestamp(raw.get("created_at"), "observation.created_at"),
            "grants_new_action_authority": False,
            "provider_write_performed": False,
            "external_writes_performed": False,
            "raw_content_captured": False,
        }
    )
    if normalized["observation_id"] != _observation_id(normalized):
        raise ValueError("observation.observation_id does not match canonical identity")
    return normalized


def build_reward_memory_utility_observation(
    application_receipt: Mapping[str, Any],
    verified_outcome: Mapping[str, Any],
    attribution_context: Mapping[str, Any],
    evaluator_proposal: Mapping[str, Any],
    *,
    created_at: str,
) -> dict[str, Any]:
    """Bind a typed evaluator proposal to trusted execution and outcome facts."""

    application, application_receipt_id = _application_receipt(application_receipt)
    outcome = _verified_outcome(verified_outcome)
    context = _attribution_context(attribution_context)
    _validate_bindings(application, outcome, context)
    proposal = _evaluator_proposal(
        evaluator_proposal,
        application_receipt=application,
        verified_outcome=outcome,
        attribution_context=context,
    )
    observation = {
        "schema_version": MEMORY_UTILITY_OBSERVATION_SCHEMA_VERSION,
        "observation_id": "",
        "scope": context["scope"],
        "application_receipt_id": application_receipt_id,
        "memory_ref_digests": proposal["memory_ref_digests"],
        "retrieval_snapshot_ref": context["retrieval_snapshot_ref"],
        "policy_snapshot_ref": context["policy_snapshot_ref"],
        "outcome_ref": outcome["outcome_ref"],
        "utility_label": proposal["utility_label"],
        "attribution_level": proposal["attribution_level"],
        "evidence_basis": proposal["evidence_basis"],
        "confidence": proposal["confidence"],
        "reason_codes": proposal["reason_codes"],
        "evidence_refs": proposal["evidence_refs"],
        "evaluator_ref": proposal["evaluator_ref"],
        "evaluation_version": proposal["evaluation_version"],
        "created_at": _timestamp(created_at, "created_at"),
        "grants_new_action_authority": False,
        "provider_write_performed": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }
    observation["observation_id"] = _observation_id(observation)
    return validate_reward_memory_utility_observation(
        observation,
        application_receipt=application_receipt,
        verified_outcome=verified_outcome,
        attribution_context=attribution_context,
    )


def validate_reward_memory_utility_observation(
    observation: Mapping[str, Any],
    *,
    application_receipt: Mapping[str, Any] | None = None,
    verified_outcome: Mapping[str, Any] | None = None,
    attribution_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate structure alone, or also bind all supplied trusted facts."""

    normalized = _normalize_observation(observation)
    trusted_values = (application_receipt, verified_outcome, attribution_context)
    if all(value is None for value in trusted_values):
        return normalized
    if any(value is None for value in trusted_values):
        raise ValueError(
            "application_receipt, verified_outcome, and attribution_context "
            "must be supplied together"
        )

    application, application_receipt_id = _application_receipt(application_receipt)
    outcome = _verified_outcome(verified_outcome)
    context = _attribution_context(attribution_context)
    _validate_bindings(application, outcome, context)
    expected_bindings = {
        "scope": context["scope"],
        "application_receipt_id": application_receipt_id,
        "retrieval_snapshot_ref": context["retrieval_snapshot_ref"],
        "policy_snapshot_ref": context["policy_snapshot_ref"],
        "outcome_ref": outcome["outcome_ref"],
    }
    for key, expected in expected_bindings.items():
        if normalized[key] != expected:
            raise ValueError(f"observation.{key} does not match trusted context")
    _validate_attribution_subject(
        normalized["memory_ref_digests"],
        attribution_level=normalized["attribution_level"],
        application_memory_ref_digests=application["memory_ref_digests"],
    )
    return normalized
