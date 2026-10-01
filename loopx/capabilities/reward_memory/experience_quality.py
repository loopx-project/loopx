from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...public_safe_text import PUBLIC_SAFE_REFERENCE_PATTERN as OPAQUE_REF_RE


PROCEDURAL_EXPERIENCE_SCHEMA_VERSION = "procedural_experience_contract_v0"
PROCEDURAL_EXPERIENCE_FIELDS = {
    "schema_version",
    "applicability",
    "observed_outcome",
    "attribution",
    "future_behavior",
    "limitations",
    "evidence_refs",
}
FUTURE_BEHAVIOR_FIELDS = {"trigger", "action", "validation", "stop_condition"}
MAX_CONTEXT_ITEMS = 5
MAX_EVIDENCE_REFS = 8


def _strict_object(
    value: object,
    *,
    label: str,
    fields: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing or unknown:
        raise ValueError(
            f"{label} has invalid fields: missing={missing}, unknown={unknown}"
        )
    return value


def _compact(value: object, label: str, *, limit: int = 500) -> str:
    result = public_safe_compact_text(value, limit=limit)
    if not result:
        raise ValueError(f"{label} must be compact and public-safe")
    return result


def _compact_list(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not 1 <= len(value) <= MAX_CONTEXT_ITEMS
    ):
        raise ValueError(
            f"{label} must contain between 1 and {MAX_CONTEXT_ITEMS} items"
        )
    result = [_compact(item, label) for item in value]
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def _evidence_refs(value: object) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not 1 <= len(value) <= MAX_EVIDENCE_REFS
    ):
        raise ValueError(
            "experience.evidence_refs must contain between 1 and "
            f"{MAX_EVIDENCE_REFS} opaque references"
        )
    result = [str(item or "").strip() for item in value]
    if any(not OPAQUE_REF_RE.fullmatch(item) for item in result):
        raise ValueError(
            "experience.evidence_refs must contain compact opaque references"
        )
    if len(set(result)) != len(result):
        raise ValueError("experience.evidence_refs must not contain duplicates")
    return result


def normalize_procedural_experience(value: object) -> dict[str, Any]:
    """Normalize the transferable lesson required by procedural memory.

    The contract is structural rather than keyword based. It separates the
    situation, independently observed result, attribution, future behavior,
    and non-generalization boundary so a fact-only summary cannot become an
    active procedural experience merely because persistence succeeded.
    """

    raw = _strict_object(
        value,
        label="experience",
        fields=PROCEDURAL_EXPERIENCE_FIELDS,
    )
    if raw.get("schema_version") != PROCEDURAL_EXPERIENCE_SCHEMA_VERSION:
        raise ValueError(f"experience must use {PROCEDURAL_EXPERIENCE_SCHEMA_VERSION}")
    future = _strict_object(
        raw.get("future_behavior"),
        label="experience.future_behavior",
        fields=FUTURE_BEHAVIOR_FIELDS,
    )
    return {
        "schema_version": PROCEDURAL_EXPERIENCE_SCHEMA_VERSION,
        "applicability": _compact_list(
            raw.get("applicability"), "experience.applicability"
        ),
        "observed_outcome": _compact(
            raw.get("observed_outcome"), "experience.observed_outcome"
        ),
        "attribution": _compact(raw.get("attribution"), "experience.attribution"),
        "future_behavior": {
            key: _compact(future.get(key), f"experience.future_behavior.{key}")
            for key in ("trigger", "action", "validation", "stop_condition")
        },
        "limitations": _compact_list(raw.get("limitations"), "experience.limitations"),
        "evidence_refs": _evidence_refs(raw.get("evidence_refs")),
    }


def procedural_experience_quality(
    *,
    target_class: str,
    experience: object,
) -> dict[str, Any]:
    """Return a deterministic qualification receipt for one memory class."""

    required = target_class == "procedural_experience"
    if experience is None:
        return {
            "required": required,
            "passed": not required,
            "status": "missing" if required else "not_applicable",
            "contract_version": PROCEDURAL_EXPERIENCE_SCHEMA_VERSION,
            "reason_codes": (
                ["procedural_experience_quality_contract_missing"] if required else []
            ),
            "value_status": "unproven",
        }
    normalized = normalize_procedural_experience(experience)
    return {
        "required": required,
        "passed": True,
        "status": "qualified_for_activation" if required else "supporting_context",
        "contract_version": PROCEDURAL_EXPERIENCE_SCHEMA_VERSION,
        "experience_digest": procedural_experience_digest(normalized),
        "reason_codes": [],
        "value_status": "unproven_until_application_evidence",
    }


def procedural_experience_digest(value: object) -> str:
    normalized = normalize_procedural_experience(value)
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "PROCEDURAL_EXPERIENCE_SCHEMA_VERSION",
    "normalize_procedural_experience",
    "procedural_experience_digest",
    "procedural_experience_quality",
]
