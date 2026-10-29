from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...public_safe_text import COMPACT_LABEL_PATTERN as _SOURCE_ID_PATTERN
from .repository_memory import (
    SUPPORT_ASPECTS,
    build_issue_fix_repository_memory_hook,
)

ISSUE_FIX_REPOSITORY_CONTEXT_INPUT_SCHEMA_VERSION = (
    "issue_fix_repository_context_input_v0"
)
ISSUE_FIX_REPOSITORY_CONTEXT_SCHEMA_VERSION = "issue_fix_repository_context_v0"

SOURCE_KINDS = {
    "repository_policy",
    "architecture_doc",
    "maintainer_map",
    "test_surface",
    "source_code",
    "prior_fix",
    "memory_retrieval",
    "external_expert",
    "knowledge_bundle",
}
TRUST_LEVELS = {"authoritative", "verified", "advisory"}
FRESHNESS_STATES = {"current", "stale", "unknown"}
CONSULTATION_STATES = {"not_applicable", "available", "queried", "unavailable"}
REQUIRED_FIX_ASPECTS = ("change_scope", "reproduction", "validation")
MAX_SOURCES = 16

_INPUT_FIELDS = {"schema_version", "repository_revision", "sources"}
_SOURCE_FIELDS = {
    "source_id",
    "source_kind",
    "reference",
    "trust",
    "freshness",
    "supports",
    "summary",
    "consultation_state",
}
_EXPERT_QUESTIONS = {
    "change_scope": "Which modules and invariants bound this issue?",
    "reproduction": "What is the smallest repository-native reproduction path?",
    "validation": "Which focused checks are authoritative for this change?",
}


def repository_context_input_contract() -> dict[str, Any]:
    """Describe the JSON object consumed by workflow-plan and feasibility."""

    return {
        "schema_version": ISSUE_FIX_REPOSITORY_CONTEXT_INPUT_SCHEMA_VERSION,
        "allowed_fields": sorted(_INPUT_FIELDS),
        "source_fields": sorted(_SOURCE_FIELDS),
        "source_kind_values": sorted(SOURCE_KINDS),
        "trust_values": sorted(TRUST_LEVELS),
        "freshness_values": sorted(FRESHNESS_STATES),
        "supports_values": sorted(SUPPORT_ASPECTS),
        "minimal_example": {
            "schema_version": ISSUE_FIX_REPOSITORY_CONTEXT_INPUT_SCHEMA_VERSION,
            "repository_revision": "<git-revision>",
            "sources": [
                {
                    "source_id": "focused-source",
                    "source_kind": "source_code",
                    "reference": "repo/relative/path.py",
                    "trust": "verified",
                    "freshness": "current",
                    "supports": ["change_scope", "reproduction", "validation"],
                }
            ],
        },
    }


def _safe_text(value: Any, *, field: str, limit: int = 220) -> str:
    text = public_safe_compact_text(value, limit=limit)
    if not text:
        raise ValueError(f"{field} must be compact and public-safe")
    return text


def _safe_reference(value: Any, *, field: str) -> str:
    raw_reference = str(value or "").strip()
    if raw_reference.startswith(("/", "~")) or re.match(
        r"^[A-Za-z]:[\\/]", raw_reference
    ):
        raise ValueError(f"{field} must not be an absolute or home-relative path")
    reference = _safe_text(value, field=field, limit=260)
    if "://" in reference:
        parsed = urlsplit(reference)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username:
            raise ValueError(f"{field} URL must be public https without user info")
        if parsed.query:
            raise ValueError(f"{field} URL must not contain query parameters")
        return reference
    path = PurePosixPath(reference)
    if ".." in path.parts:
        raise ValueError(f"{field} must not traverse outside the repository")
    return reference


def _normalise_source(raw: Any, *, index: int, has_revision: bool) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"sources[{index}] must be an object")
    unknown = sorted(set(raw) - _SOURCE_FIELDS)
    if unknown:
        raise ValueError(f"sources[{index}] has unsupported fields: {unknown}")

    source_id = _safe_text(raw.get("source_id"), field=f"sources[{index}].source_id")
    if not _SOURCE_ID_PATTERN.fullmatch(source_id):
        raise ValueError(f"sources[{index}].source_id has an invalid shape")
    source_kind = str(raw.get("source_kind") or "").strip()
    if source_kind not in SOURCE_KINDS:
        raise ValueError(
            f"sources[{index}].source_kind must be one of {sorted(SOURCE_KINDS)}"
        )
    trust = str(raw.get("trust") or "").strip()
    if trust not in TRUST_LEVELS:
        raise ValueError(f"sources[{index}].trust must be one of {sorted(TRUST_LEVELS)}")
    if source_kind == "external_expert" and trust != "advisory":
        raise ValueError("external_expert sources must remain advisory")
    if source_kind == "memory_retrieval" and trust != "advisory":
        raise ValueError("memory_retrieval sources must remain advisory")
    freshness = str(raw.get("freshness") or "unknown").strip()
    if freshness not in FRESHNESS_STATES:
        raise ValueError(
            f"sources[{index}].freshness must be one of {sorted(FRESHNESS_STATES)}"
        )
    if freshness == "current" and not has_revision:
        raise ValueError("current repository context sources require repository_revision")

    raw_supports = raw.get("supports")
    if not isinstance(raw_supports, Sequence) or isinstance(raw_supports, (str, bytes)):
        raise ValueError(f"sources[{index}].supports must be a list")
    supports = sorted({str(value).strip() for value in raw_supports if str(value).strip()})
    if not supports or any(value not in SUPPORT_ASPECTS for value in supports):
        raise ValueError(
            f"sources[{index}].supports must use {sorted(SUPPORT_ASPECTS)}"
        )

    consultation_state = str(
        raw.get("consultation_state")
        or ("available" if source_kind == "external_expert" else "not_applicable")
    ).strip()
    if consultation_state not in CONSULTATION_STATES:
        raise ValueError(
            f"sources[{index}].consultation_state must be one of "
            f"{sorted(CONSULTATION_STATES)}"
        )
    if source_kind != "external_expert" and consultation_state != "not_applicable":
        raise ValueError("consultation_state only applies to external_expert sources")

    source: dict[str, Any] = {
        "schema_version": "issue_fix_repository_context_source_v0",
        "source_id": source_id,
        "source_kind": source_kind,
        "reference": _safe_reference(
            raw.get("reference"), field=f"sources[{index}].reference"
        ),
        "trust": trust,
        "freshness": freshness,
        "supports": supports,
        "consultation_state": consultation_state,
        "raw_content_captured": False,
        "raw_response_captured": False,
    }
    if raw.get("summary") is not None:
        source["summary"] = _safe_text(
            raw.get("summary"), field=f"sources[{index}].summary"
        )
    return source


def _coverage(sources: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for aspect in sorted(SUPPORT_ASPECTS):
        matching = [source for source in sources if aspect in source.get("supports", [])]
        grounded = [
            source
            for source in matching
            if source.get("freshness") == "current"
            and source.get("trust") in {"authoritative", "verified"}
            and source.get("source_kind") != "external_expert"
        ]
        status = "grounded" if grounded else "advisory" if matching else "missing"
        result[aspect] = {
            "status": status,
            "source_refs": [source["source_id"] for source in grounded or matching],
        }
    return result


def _context_status(
    *, provided: bool, coverage: Mapping[str, Mapping[str, Any]]
) -> str:
    if not provided:
        return "not_provided"
    statuses = [coverage[aspect]["status"] for aspect in REQUIRED_FIX_ASPECTS]
    if all(status == "grounded" for status in statuses):
        return "grounded"
    if any(status == "grounded" for status in statuses):
        return "partial"
    return "ungrounded"


def _expert_projection(
    sources: Sequence[Mapping[str, Any]],
    unresolved_aspects: Sequence[str],
) -> dict[str, Any]:
    experts = [source for source in sources if source.get("source_kind") == "external_expert"]
    available = [
        source
        for source in experts
        if source.get("consultation_state") in {"available", "queried"}
    ]
    queried = [
        source["source_id"]
        for source in experts
        if source.get("consultation_state") == "queried"
    ]
    unqueried = [
        source["source_id"]
        for source in experts
        if source.get("consultation_state") == "available"
    ]
    if unresolved_aspects and queried:
        next_action = "verify_queried_answer_against_repository"
    elif unresolved_aspects and unqueried:
        next_action = "consult_then_verify_against_repository"
    elif unresolved_aspects:
        next_action = "read_repository_sources"
    else:
        next_action = "none"
    return {
        "schema_version": "issue_fix_repository_expert_consultation_v0",
        "available": bool(available),
        "recommended": bool(unresolved_aspects and unqueried),
        "queried_source_refs": queried,
        "unqueried_source_refs": unqueried,
        "questions": [
            {"aspect": aspect, "question": _EXPERT_QUESTIONS[aspect]}
            for aspect in unresolved_aspects
        ],
        "next_action": next_action,
        "answer_is_authority": False,
        "external_write_authorized": False,
        "raw_response_captured": False,
    }


def build_issue_fix_repository_context_packet(
    *,
    repo: str,
    issue_ref: str,
    context_input: Mapping[str, Any] | None = None,
    memory_retrieval_input: Mapping[str, Any] | None = None,
    generated_at: str | None = "2026-06-23T00:00:00Z",
) -> dict[str, Any]:
    """Compose compact repository and host-retrieved memory evidence."""

    provided = context_input is not None or memory_retrieval_input is not None
    raw_input: Mapping[str, Any] = context_input or {}
    if context_input is not None:
        if raw_input.get("schema_version") != ISSUE_FIX_REPOSITORY_CONTEXT_INPUT_SCHEMA_VERSION:
            raise ValueError(
                "repository context schema_version must be "
                "issue_fix_repository_context_input_v0"
            )
        unknown = sorted(set(raw_input) - _INPUT_FIELDS)
        if unknown:
            raise ValueError(f"repository context has unsupported fields: {unknown}")

    revision_value = raw_input.get("repository_revision")
    repository_revision = (
        _safe_text(revision_value, field="repository_revision", limit=120)
        if revision_value is not None
        else None
    )
    raw_sources = raw_input.get("sources") or []
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        raise ValueError("repository context sources must be a list")
    memory_hook = build_issue_fix_repository_memory_hook(
        memory_input=memory_retrieval_input,
        repository_revision=repository_revision,
    )
    composed_sources = [*raw_sources, *memory_hook["sources"]]
    if len(composed_sources) > MAX_SOURCES:
        raise ValueError(f"repository context supports at most {MAX_SOURCES} sources")
    sources = [
        _normalise_source(raw, index=index, has_revision=bool(repository_revision))
        for index, raw in enumerate(composed_sources)
    ]
    source_ids = [source["source_id"] for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("repository context source_id values must be unique")

    coverage = _coverage(sources)
    unresolved = [
        aspect
        for aspect in REQUIRED_FIX_ASPECTS
        if coverage[aspect]["status"] != "grounded"
    ]
    advisory = [
        aspect
        for aspect in REQUIRED_FIX_ASPECTS
        if coverage[aspect]["status"] == "advisory"
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "repo": repo,
                "issue_ref": issue_ref,
                "repository_revision": repository_revision,
                "sources": sources,
                "memory_hook": memory_hook["projection"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    memory_refs = [
        source["source_id"]
        for source in sources
        if source["source_kind"] == "memory_retrieval"
    ]
    bundle_refs = [
        source["source_id"]
        for source in sources
        if source["source_kind"] == "knowledge_bundle"
    ]
    packet: dict[str, Any] = {
        "ok": True,
        "schema_version": ISSUE_FIX_REPOSITORY_CONTEXT_SCHEMA_VERSION,
        "mode": "issue-fix-repository-context",
        "generated_at": generated_at,
        "repo": _safe_text(repo, field="repo", limit=160),
        "issue_ref": _safe_text(issue_ref, field="issue_ref", limit=160),
        "provided": provided,
        "repository_revision": repository_revision,
        "context_fingerprint": fingerprint,
        "context_status": _context_status(provided=provided, coverage=coverage),
        "sources": sources,
        "coverage": coverage,
        "required_fix_aspects": list(REQUIRED_FIX_ASPECTS),
        "unresolved_required_aspects": unresolved,
        "advisory_required_aspects": advisory,
        "recommended_reads": [
            {
                "aspect": aspect,
                "action": f"Ground {aspect} with current repository evidence.",
            }
            for aspect in unresolved
        ],
        "expert_consultation": _expert_projection(sources, unresolved),
        "memory_projection": {
            "schema_version": "issue_fix_repository_memory_projection_v0",
            "source_refs": memory_refs,
            "knowledge_bundle_refs": bundle_refs,
            "live_retrieval_performed": bool(
                memory_hook["projection"].get("search_performed")
                or memory_hook["projection"].get("read_performed")
            ),
            "writeback_performed": False,
            "raw_memory_captured": False,
            "retrieval_hook": memory_hook["projection"],
            "writeback_policy": (
                "After a validated outcome, write only distilled reusable facts with "
                "repository revision, provenance, freshness, and supersession metadata."
            ),
        },
        "truth_contract": {
            "repository_at_revision_is_primary": True,
            "memory_requires_repository_verification": True,
            "external_expert_is_advisory": True,
            "context_cannot_authorize_external_writes": True,
        },
        "external_reads_performed": bool(
            memory_hook["projection"].get("search_performed")
            or memory_hook["projection"].get("read_performed")
        ),
        "external_writes_performed": False,
        "raw_content_captured": False,
        "raw_responses_captured": False,
        "raw_logs_captured": False,
        "local_paths_captured": False,
        "credentials_captured": False,
    }
    validation = validate_issue_fix_repository_context_packet(packet)
    packet["ok"] = validation["ok"]
    packet["validation"] = validation
    return packet


def validate_issue_fix_repository_context_packet(
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if packet.get("schema_version") != ISSUE_FIX_REPOSITORY_CONTEXT_SCHEMA_VERSION:
        errors.append("packet schema_version must be issue_fix_repository_context_v0")
    if not isinstance(packet.get("external_reads_performed"), bool):
        errors.append("packet external_reads_performed must be a boolean")
    for key in (
        "external_writes_performed",
        "raw_content_captured",
        "raw_responses_captured",
        "raw_logs_captured",
        "local_paths_captured",
        "credentials_captured",
    ):
        if packet.get(key) is not False:
            errors.append(f"packet {key} must be false")
    if packet.get("context_status") not in {
        "not_provided",
        "grounded",
        "partial",
        "ungrounded",
    }:
        errors.append("packet context_status is invalid")
    sources = packet.get("sources")
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        errors.append("packet sources must be a list")
        sources = []
    for source in sources:
        if not isinstance(source, Mapping):
            errors.append("packet source entries must be objects")
            continue
        if source.get("raw_content_captured") is not False:
            errors.append("packet sources must not capture raw content")
        if source.get("raw_response_captured") is not False:
            errors.append("packet sources must not capture raw responses")
        if source.get("source_kind") == "external_expert" and source.get("trust") != "advisory":
            errors.append("external expert sources must remain advisory")
        if source.get("source_kind") == "memory_retrieval" and source.get("trust") != "advisory":
            errors.append("memory retrieval sources must remain advisory")
    truth = packet.get("truth_contract")
    if not isinstance(truth, Mapping) or truth.get(
        "context_cannot_authorize_external_writes"
    ) is not True:
        errors.append("truth contract must deny context-based external write authority")
    return {
        "ok": not errors,
        "schema_version": "issue_fix_repository_context_validation_v0",
        "errors": errors,
    }
