from __future__ import annotations

from typing import Any, cast

import hashlib
from collections.abc import Mapping

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ..context_providers.base import ContextProvider
from .candidate_review import build_reward_memory_candidate
from .ingestion import (
    ingest_reward_memory_candidate,
    normalize_reward_memory_standing_policy,
)


SCOPED_FEEDBACK_ADAPTER = "scoped_feedback"
SCOPED_FEEDBACK_EVENT_SCHEMA_VERSION = "scoped_feedback_reward_memory_event_v0"
SCOPED_FEEDBACK_ADAPTER_SCHEMA_VERSION = (
    "scoped_feedback_reward_memory_candidate_adapter_v0"
)

_EVENT_FIELDS = {
    "schema_version",
    "feedback_ref",
    "workspace_ref",
    "project_ref",
    "user_ref",
    "peer_ref",
    "session_ref",
    "surface_id",
    "revision_ref",
    "target_class",
    "content_summary",
    "experience",
    "source",
    "reasoning",
    "guard_context",
    "requested_action_scopes",
    "raw_content_captured",
}
_SOURCE_FIELDS = {"source_kind", "source_ref", "actor_ref", "actor_role"}
_REASONING_FIELDS = {"summary", "confidence"}
_GUARD_FIELDS = {
    "source_freshness",
    "conflict_state",
    "current_artifact_verified",
}


def _strict_object(
    value: object,
    *,
    label: str,
    allowed: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(
            f"{label} contains unsupported fields: {', '.join(unexpected)}"
        )
    return value


def build_scoped_feedback_reward_memory_candidate(
    event: Mapping[str, Any],
    *,
    authority_checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map one module-scoped compact feedback event into the shared candidate."""

    raw = _strict_object(event, label="scoped_feedback_event", allowed=_EVENT_FIELDS)
    if raw.get("schema_version") != SCOPED_FEEDBACK_EVENT_SCHEMA_VERSION:
        raise ValueError(f"event must use {SCOPED_FEEDBACK_EVENT_SCHEMA_VERSION}")
    feedback_ref = public_safe_compact_text(raw.get("feedback_ref"), limit=160)
    if not feedback_ref:
        raise ValueError("feedback_ref must be compact and public-safe")
    _strict_object(
        raw.get("source"), label="scoped_feedback_event.source", allowed=_SOURCE_FIELDS
    )
    _strict_object(
        raw.get("reasoning"),
        label="scoped_feedback_event.reasoning",
        allowed=_REASONING_FIELDS,
    )
    _strict_object(
        raw.get("guard_context"),
        label="scoped_feedback_event.guard_context",
        allowed=_GUARD_FIELDS,
    )
    candidate = build_reward_memory_candidate(
        {
            "target_class": raw.get("target_class"),
            "content_summary": raw.get("content_summary"),
            "experience": raw.get("experience"),
            "source": raw.get("source"),
            "scope": {
                "workspace_ref": raw.get("workspace_ref"),
                "project_ref": raw.get("project_ref"),
                "user_ref": raw.get("user_ref"),
                "peer_ref": raw.get("peer_ref"),
                "session_ref": raw.get("session_ref"),
                "surface_ids": [raw.get("surface_id")],
                "revision_ref": raw.get("revision_ref"),
            },
            "reasoning": raw.get("reasoning"),
            "guard_context": raw.get("guard_context"),
            "requested_action_scopes": raw.get("requested_action_scopes") or [],
            "raw_content_captured": raw.get("raw_content_captured"),
        },
        authority_checkpoint=authority_checkpoint,
    )
    return {
        "ok": True,
        "schema_version": SCOPED_FEEDBACK_ADAPTER_SCHEMA_VERSION,
        "feedback_ref": feedback_ref,
        "surface_id": candidate["candidate"]["scope"]["surface_ids"][0],
        "shared_candidate": candidate,
        "adapter_role": "strict_field_mapping_only_shared_core_owns_lifecycle",
        "provider_write_performed": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }


def ingest_scoped_feedback_reward_memory_event(
    event: Mapping[str, Any],
    *,
    corpus: Mapping[str, Any],
    standing_policy: Mapping[str, Any],
    provider_binding: Mapping[str, Any],
    observed_at: str,
    execute: bool = False,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Ingest one compact module-scoped feedback event through the shared core."""

    policy = normalize_reward_memory_standing_policy(standing_policy)
    source = event.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("scoped_feedback_event.source must be an object")
    authority_checkpoint = None
    if event.get("target_class") == "hard_policy":
        authority_checkpoint = {
            "verified": policy["enabled"] is True,
            "source_ref": policy["authority_source_ref"],
            "actor_ref": source.get("actor_ref"),
            "actor_role": source.get("actor_role"),
            "project_ref": event.get("project_ref"),
            "action_scopes": event.get("requested_action_scopes") or [],
        }
    adapter = build_scoped_feedback_reward_memory_candidate(
        event,
        authority_checkpoint=authority_checkpoint,
    )
    result = ingest_reward_memory_candidate(
        adapter["shared_candidate"],
        corpus=corpus,
        standing_policy=policy,
        provider_binding=provider_binding,
        observed_at=observed_at,
        execute=execute,
        provider=provider,
    )
    summary = public_safe_compact_text(event.get("content_summary"), limit=500)
    return result | {
        "adapter_schema_version": SCOPED_FEEDBACK_ADAPTER_SCHEMA_VERSION,
        "event_schema_version": SCOPED_FEEDBACK_EVENT_SCHEMA_VERSION,
        "feedback_ref": adapter["feedback_ref"],
        "event_summary_digest": hashlib.sha256(cast(str, summary).encode("utf-8")).hexdigest()[
            :16
        ],
        "next_reward_memory_call": "explicit_function_boundary_recall",
    }
