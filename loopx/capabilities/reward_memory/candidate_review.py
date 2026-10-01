from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...public_safe_text import (
    MODULE_QUALIFIED_SURFACE_PATTERN as SURFACE_RE,
    PUBLIC_SAFE_REFERENCE_PATTERN as TOKEN_RE,
)
from .experience_quality import (
    normalize_procedural_experience,
    procedural_experience_quality,
)
from .registry import IDENTITY_SCOPE_FIELDS


REWARD_MEMORY_CANDIDATE_SCHEMA_VERSION = "reward_memory_candidate_v0"
REWARD_MEMORY_REVIEW_SCHEMA_VERSION = "reward_memory_candidate_review_v0"
ISSUE_FIX_REWARD_MEMORY_ADAPTER_SCHEMA_VERSION = (
    "issue_fix_reward_memory_candidate_adapter_v0"
)

TARGET_CLASS_IDS = {
    "hard_policy",
    "soft_preference",
    "procedural_experience",
}
REVIEW_DECISIONS = {"accept", "edit", "reject", "retire", "no_write"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
SOURCE_FRESHNESS_STATES = {"current", "stale", "unknown"}
CONFLICT_STATES = {"clear", "unresolved"}
ELIGIBLE_POLICY_ACTOR_ROLES = {
    "verified_repository_core_contributor",
    "verified_project_owner_or_operator",
}
MAX_SURFACES = 12
MAX_ACTION_SCOPES = 12


def _token(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not TOKEN_RE.fullmatch(result):
        raise ValueError(f"{label} must be a compact public-safe token")
    return result


def _optional_token(value: object, label: str) -> str | None:
    if value in (None, ""):
        return None
    return _token(value, label)


def _compact(value: object, label: str, *, limit: int) -> str:
    result = public_safe_compact_text(value, limit=limit)
    if not result:
        raise ValueError(f"{label} must be compact and public-safe")
    return result


def _boolean(mapping: Mapping[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_timestamp(value: object, label: str) -> str | None:
    if value in (None, ""):
        return None
    result = str(value).strip()
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return result


def _tokens(value: object, label: str, *, maximum: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a bounded token list")
    if len(value) > maximum:
        raise ValueError(f"{label} must contain at most {maximum} items")
    result = sorted({_token(item, label) for item in value})
    if len(result) != len(value):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def _scope(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("scope must be an object")
    surfaces = _tokens(
        raw.get("surface_ids") or [],
        "scope.surface_ids",
        maximum=MAX_SURFACES,
    )
    if not surfaces or any(not SURFACE_RE.fullmatch(item) for item in surfaces):
        raise ValueError("scope.surface_ids must contain module-qualified tokens")
    scope = {
        "workspace_ref": _token(raw.get("workspace_ref"), "scope.workspace_ref"),
        "project_ref": _token(raw.get("project_ref"), "scope.project_ref"),
        "surface_ids": surfaces,
        "revision_ref": _optional_token(raw.get("revision_ref"), "scope.revision_ref"),
    }
    for field in IDENTITY_SCOPE_FIELDS:
        value = _optional_token(raw.get(field), f"scope.{field}")
        if value:
            scope[field] = value
    return scope


def _source(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError("source must be an object")
    return {
        "source_kind": _token(raw.get("source_kind"), "source.source_kind"),
        "source_ref": _compact(raw.get("source_ref"), "source.source_ref", limit=240),
        "actor_ref": _token(raw.get("actor_ref"), "source.actor_ref"),
        "actor_role": _token(raw.get("actor_role"), "source.actor_role"),
    }


def _reasoning(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError("reasoning must be an object")
    confidence = str(raw.get("confidence") or "").strip()
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError("reasoning.confidence must be low, medium, or high")
    return {
        "summary": _compact(raw.get("summary"), "reasoning.summary", limit=500),
        "confidence": confidence,
    }


def _guard_context(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("guard_context must be an object")
    source_freshness = str(raw.get("source_freshness") or "").strip()
    conflict_state = str(raw.get("conflict_state") or "").strip()
    if source_freshness not in SOURCE_FRESHNESS_STATES:
        raise ValueError("guard_context.source_freshness is invalid")
    if conflict_state not in CONFLICT_STATES:
        raise ValueError("guard_context.conflict_state is invalid")
    return {
        "source_freshness": source_freshness,
        "conflict_state": conflict_state,
        "current_artifact_verified": _boolean(raw, "current_artifact_verified"),
    }


def _authority_checkpoint(raw: object) -> dict[str, Any]:
    if raw is None:
        return {
            "verified": False,
            "source_ref": None,
            "actor_ref": None,
            "actor_role": None,
            "project_ref": None,
            "action_scopes": [],
        }
    if not isinstance(raw, Mapping):
        raise ValueError("authority_checkpoint must be an object")
    verified = _boolean(raw, "verified")
    return {
        "verified": verified,
        "source_ref": _optional_token(
            raw.get("source_ref"), "authority_checkpoint.source_ref"
        ),
        "actor_ref": _optional_token(
            raw.get("actor_ref"), "authority_checkpoint.actor_ref"
        ),
        "actor_role": _optional_token(
            raw.get("actor_role"), "authority_checkpoint.actor_role"
        ),
        "project_ref": _optional_token(
            raw.get("project_ref"), "authority_checkpoint.project_ref"
        ),
        "action_scopes": _tokens(
            raw.get("action_scopes") or [],
            "authority_checkpoint.action_scopes",
            maximum=MAX_ACTION_SCOPES,
        ),
    }


def _candidate_ref(candidate: Mapping[str, Any]) -> str:
    identity = {
        key: candidate.get(key)
        for key in (
            "target_class",
            "content_summary",
            "source",
            "scope",
            "guard_context",
            "experience",
            "requested_action_scopes",
        )
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    return f"candidate:{digest}"


def _guard(
    candidate: Mapping[str, Any], checkpoint: Mapping[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    requested = set(candidate["requested_action_scopes"])
    target_class = candidate["target_class"]
    guard_context = candidate["guard_context"]
    experience_quality = procedural_experience_quality(
        target_class=target_class,
        experience=candidate.get("experience"),
    )
    reasons.extend(experience_quality["reason_codes"])
    if guard_context["source_freshness"] != "current":
        reasons.append("source_freshness_not_current")
    if guard_context["conflict_state"] != "clear":
        reasons.append("unresolved_higher_authority_conflict")
    if (
        target_class == "procedural_experience"
        and guard_context["current_artifact_verified"] is not True
    ):
        reasons.append("procedural_experience_current_artifact_unverified")
    if target_class != "hard_policy" and requested:
        reasons.append("advisory_class_requested_action_authority")
    if target_class == "hard_policy":
        source = candidate["source"]
        scope = candidate["scope"]
        verified_scopes = set(checkpoint["action_scopes"])
        if not requested:
            reasons.append("hard_policy_missing_action_scope")
        if checkpoint["verified"] is not True or not checkpoint["source_ref"]:
            reasons.append("authority_checkpoint_unverified")
        if checkpoint["actor_ref"] != source["actor_ref"]:
            reasons.append("authority_actor_mismatch")
        if checkpoint["actor_role"] != source["actor_role"]:
            reasons.append("authority_actor_role_mismatch")
        if source["actor_role"] not in ELIGIBLE_POLICY_ACTOR_ROLES:
            reasons.append("actor_role_cannot_derive_hard_policy")
        if checkpoint["project_ref"] != scope["project_ref"]:
            reasons.append("authority_project_mismatch")
        if requested - verified_scopes:
            reasons.append("requested_action_scope_exceeds_verified_authority")
    return {
        "passed": not reasons,
        "reason_codes": reasons,
        "rule": (
            "model_proposes_meaning_deterministic_guards_verify_scope_privacy_"
            "and_no_authority_expansion"
        ),
        "semantic_reasoning_preserved": True,
        "experience_quality": experience_quality,
    }


def build_reward_memory_candidate(
    proposal: Mapping[str, Any],
    *,
    authority_checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one inspectable candidate without persisting memory or raw evidence."""

    if _boolean(proposal, "raw_content_captured"):
        raise ValueError("reward-memory candidates must not capture raw content")
    target_class = str(proposal.get("target_class") or "").strip()
    if target_class not in TARGET_CLASS_IDS:
        raise ValueError("target_class must be a durable reusable memory class")
    lifecycle: dict[str, Any] = {"state": "candidate", "supersedes_refs": []}
    expires_at = _optional_timestamp(proposal.get("expires_at"), "expires_at")
    if expires_at:
        lifecycle["expires_at"] = expires_at
    candidate: dict[str, Any] = {
        "schema_version": REWARD_MEMORY_CANDIDATE_SCHEMA_VERSION,
        "target_class": target_class,
        "content_summary": _compact(
            proposal.get("content_summary"), "content_summary", limit=500
        ),
        "source": _source(proposal.get("source")),
        "scope": _scope(proposal.get("scope")),
        "reasoning": _reasoning(proposal.get("reasoning")),
        "guard_context": _guard_context(proposal.get("guard_context")),
        "requested_action_scopes": _tokens(
            proposal.get("requested_action_scopes") or [],
            "requested_action_scopes",
            maximum=MAX_ACTION_SCOPES,
        ),
        "lifecycle": lifecycle,
        "privacy": {"raw_content_captured": False},
    }
    if proposal.get("experience") is not None:
        candidate["experience"] = normalize_procedural_experience(
            proposal.get("experience")
        )
    candidate["candidate_ref"] = _candidate_ref(candidate)
    checkpoint = _authority_checkpoint(authority_checkpoint)
    guard = _guard(candidate, checkpoint)
    return {
        "ok": True,
        "schema_version": REWARD_MEMORY_CANDIDATE_SCHEMA_VERSION,
        "status": "review_ready" if guard["passed"] else "guard_blocked",
        "candidate": candidate,
        "authority_checkpoint": checkpoint,
        "guard": guard,
        "available_review_decisions": sorted(REVIEW_DECISIONS),
        "candidate_persisted": False,
        "provider_write_performed": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }


def _target(candidate_or_review: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    schema = candidate_or_review.get("schema_version")
    if schema == REWARD_MEMORY_CANDIDATE_SCHEMA_VERSION:
        candidate = candidate_or_review.get("candidate")
        guard = candidate_or_review.get("guard")
        if not isinstance(candidate, Mapping) or not isinstance(guard, Mapping):
            raise ValueError("candidate packet is incomplete")
        return dict(candidate), guard.get("passed") is True
    if schema == REWARD_MEMORY_REVIEW_SCHEMA_VERSION:
        record = candidate_or_review.get("record")
        if not isinstance(record, Mapping):
            raise ValueError("review packet does not contain a record")
        return dict(record), candidate_or_review.get("guard_passed") is True
    raise ValueError("review target must be a candidate or review packet")


def review_reward_memory_candidate(
    candidate_or_review: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply one review decision while leaving persistence to the corpus owner."""

    record, guard_passed = _target(candidate_or_review)
    decision = str(review.get("decision") or "").strip()
    if decision not in REVIEW_DECISIONS:
        raise ValueError("decision must be accept, edit, reject, retire, or no_write")
    reviewer_ref = _token(review.get("reviewer_ref"), "review.reviewer_ref")
    review_ref = _token(review.get("review_ref"), "review.review_ref")
    reasoning_summary = _compact(
        review.get("reasoning_summary"), "review.reasoning_summary", limit=500
    )
    lifecycle = record.get("lifecycle")
    state = lifecycle.get("state") if isinstance(lifecycle, Mapping) else None
    supersedes_refs = (
        list(lifecycle.get("supersedes_refs") or [])
        if isinstance(lifecycle, Mapping)
        else []
    )
    expires_at = lifecycle.get("expires_at") if isinstance(lifecycle, Mapping) else None
    if decision == "retire" and state != "active":
        raise ValueError("retire requires an active reviewed record")
    if decision != "retire" and state != "candidate":
        raise ValueError("accept, edit, reject, and no_write require a candidate")

    effective_decision = decision
    status = "reviewed"
    if not guard_passed and decision in {"accept", "edit"}:
        effective_decision = "no_write"
        status = "guard_blocked"
    elif decision == "accept":
        record["lifecycle"] = {
            "state": "active",
            "supersedes_refs": supersedes_refs,
        }
        if expires_at:
            record["lifecycle"]["expires_at"] = expires_at
        status = "active"
    elif decision == "edit":
        old_ref = str(record.get("candidate_ref") or "")
        record["content_summary"] = _compact(
            review.get("edited_content_summary"),
            "review.edited_content_summary",
            limit=500,
        )
        record["candidate_ref"] = _candidate_ref(record)
        record["lifecycle"] = {
            "state": "candidate",
            "supersedes_refs": [old_ref],
        }
        if expires_at:
            record["lifecycle"]["expires_at"] = expires_at
        status = "review_ready"
    elif decision == "reject":
        record["lifecycle"] = {
            "state": "retired",
            "supersedes_refs": supersedes_refs,
            "retired_reason": "rejected_in_review",
        }
        status = "rejected"
    elif decision == "retire":
        record["lifecycle"] = {
            "state": "retired",
            "supersedes_refs": supersedes_refs,
            "retired_reason": "reviewed_retirement",
        }
        status = "retired"
    else:
        record["lifecycle"] = {
            "state": "retired",
            "supersedes_refs": supersedes_refs,
            "retired_reason": "no_write_rationale",
        }
        status = "no_write" if status != "guard_blocked" else status

    persistence_needed = effective_decision in {"accept", "retire"}
    return {
        "ok": True,
        "schema_version": REWARD_MEMORY_REVIEW_SCHEMA_VERSION,
        "status": status,
        "requested_decision": decision,
        "effective_decision": effective_decision,
        "guard_passed": guard_passed,
        "review": {
            "reviewer_ref": reviewer_ref,
            "review_ref": review_ref,
            "reasoning_summary": reasoning_summary,
        },
        "record": record,
        "persistence_next_step": (
            "caller_uses_declared_corpus_write_authority_then_readback"
            if persistence_needed
            else "none"
        ),
        "grants_new_action_authority": False,
        "provider_write_performed": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }


def build_issue_fix_reward_memory_candidate(
    event: Mapping[str, Any],
    *,
    authority_checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map compact Issue Fix evidence into the shared candidate contract."""

    issue_ref = _compact(event.get("issue_ref"), "issue_ref", limit=160)
    proposal = {
        "target_class": event.get("target_class"),
        "content_summary": event.get("content_summary"),
        "source": event.get("source"),
        "scope": {
            "workspace_ref": event.get("workspace_ref"),
            "project_ref": event.get("repository_ref"),
            "surface_ids": [event.get("surface_id")],
            "revision_ref": event.get("revision_ref"),
        },
        "reasoning": event.get("reasoning"),
        "guard_context": event.get("guard_context"),
        "requested_action_scopes": event.get("requested_action_scopes") or [],
        "raw_content_captured": event.get("raw_content_captured"),
    }
    candidate = build_reward_memory_candidate(
        proposal,
        authority_checkpoint=authority_checkpoint,
    )
    return {
        "ok": True,
        "schema_version": ISSUE_FIX_REWARD_MEMORY_ADAPTER_SCHEMA_VERSION,
        "issue_ref": issue_ref,
        "surface_id": candidate["candidate"]["scope"]["surface_ids"][0],
        "shared_candidate": candidate,
        "adapter_role": "field_mapping_only_shared_core_owns_semantics_and_lifecycle",
        "fresh_execution_context_source": "existing_loopx_control_plane",
        "provider_write_performed": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }


def issue_fix_verified_contributor_candidate_fixture() -> dict[str, Any]:
    """Return one public Issue Fix adapter fixture for CLI and smoke coverage."""

    return build_issue_fix_reward_memory_candidate(
        {
            "issue_ref": "github:example/repository#42",
            "workspace_ref": "workspace:example",
            "repository_ref": "repository:example",
            "surface_id": "issue_fix.patch_planning",
            "revision_ref": "revision:abc123",
            "target_class": "hard_policy",
            "content_summary": (
                "Keep focused fixes within the affected module unless broader "
                "evidence is present."
            ),
            "source": {
                "source_kind": "maintainer_correction",
                "source_ref": "github:example/repository#42:comment:1",
                "actor_ref": "github:user:maintainer",
                "actor_role": "verified_repository_core_contributor",
            },
            "reasoning": {
                "summary": (
                    "The correction consistently constrains issue-fix scope; "
                    "it does not grant repository write authority."
                ),
                "confidence": "high",
            },
            "guard_context": {
                "source_freshness": "current",
                "conflict_state": "clear",
                "current_artifact_verified": True,
            },
            "requested_action_scopes": ["issue_fix:scope_selection"],
            "raw_content_captured": False,
        },
        authority_checkpoint={
            "verified": True,
            "source_ref": "repository:authority-map",
            "actor_ref": "github:user:maintainer",
            "actor_role": "verified_repository_core_contributor",
            "project_ref": "repository:example",
            "action_scopes": ["issue_fix:scope_selection"],
        },
    )
