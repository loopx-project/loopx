from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..context_providers import build_context_provider
from ...public_safe_text import (
    MODULE_QUALIFIED_SURFACE_PATTERN as SURFACE_RE,
    PUBLIC_SAFE_REFERENCE_PATTERN as TOKEN_RE,
)
from ..context_providers.base import (
    ContextProvider,
    canonical_context_text,
    opaque_provider_ref,
)
from .application import (
    build_active_reward_memory_record,
    build_reward_memory_recall_request,
    execute_reward_memory_recall,
    normalize_reward_memory_provider_binding,
)
from .candidate_review import (
    REWARD_MEMORY_CANDIDATE_SCHEMA_VERSION,
    TARGET_CLASS_IDS,
    review_reward_memory_candidate,
)
from .experience_quality import procedural_experience_digest
from .registry import IDENTITY_SCOPE_FIELDS, normalize_reward_memory_corpus


REWARD_MEMORY_STANDING_POLICY_SCHEMA_VERSION = "reward_memory_standing_policy_v0"
REWARD_MEMORY_INGEST_RECEIPT_SCHEMA_VERSION = "reward_memory_ingest_receipt_v0"

MAX_POLICY_VALUES = 12

_POLICY_FIELDS = {
    "schema_version",
    "policy_id",
    "enabled",
    "auto_activate",
    "owner_ref",
    "reviewer_ref",
    "authority_source_ref",
    "scope",
    "allowed_target_classes",
    "allowed_source_kinds",
    "allowed_actor_roles",
    "allowed_action_scopes",
    "raw_content_captured",
}
_POLICY_SCOPE_FIELDS = {
    "workspace_ref",
    "project_ref",
    "surface_ids",
    "user_ref",
    "peer_ref",
    "session_ref",
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


def _token(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not TOKEN_RE.fullmatch(result):
        raise ValueError(f"{label} must be a compact public-safe token")
    return result


def _optional_token(value: object, label: str) -> str | None:
    if value in (None, ""):
        return None
    return _token(value, label)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _tokens(
    value: object,
    label: str,
    *,
    surface: bool = False,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a bounded token list")
    minimum = 0 if allow_empty else 1
    if not minimum <= len(value) <= MAX_POLICY_VALUES:
        raise ValueError(f"{label} must contain {minimum}-{MAX_POLICY_VALUES} items")
    result = [_token(item, label) for item in value]
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicates")
    if surface and any(not SURFACE_RE.fullmatch(item) for item in result):
        raise ValueError(f"{label} must contain module-qualified surfaces")
    return sorted(result)


def normalize_reward_memory_standing_policy(
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one pre-approved exact-scope write policy without provider access."""

    raw = _strict_object(
        policy,
        label="standing_policy",
        allowed=_POLICY_FIELDS,
    )
    if raw.get("schema_version") != REWARD_MEMORY_STANDING_POLICY_SCHEMA_VERSION:
        raise ValueError("standing_policy must use reward_memory_standing_policy_v0")
    scope = _strict_object(
        raw.get("scope"),
        label="standing_policy.scope",
        allowed=_POLICY_SCOPE_FIELDS,
    )
    target_classes = _tokens(
        raw.get("allowed_target_classes"),
        "standing_policy.allowed_target_classes",
    )
    if any(item not in TARGET_CLASS_IDS for item in target_classes):
        raise ValueError("standing policy may only activate durable memory classes")
    raw_content = _boolean(
        raw.get("raw_content_captured"),
        "standing_policy.raw_content_captured",
    )
    if raw_content:
        raise ValueError("standing policy must not capture raw content")
    normalized_scope = {
        "workspace_ref": _token(
            scope.get("workspace_ref"), "standing_policy.scope.workspace_ref"
        ),
        "project_ref": _token(
            scope.get("project_ref"), "standing_policy.scope.project_ref"
        ),
        "surface_ids": _tokens(
            scope.get("surface_ids"),
            "standing_policy.scope.surface_ids",
            surface=True,
        ),
    }
    for field in IDENTITY_SCOPE_FIELDS:
        value = _optional_token(scope.get(field), f"standing_policy.scope.{field}")
        if value:
            normalized_scope[field] = value
    return {
        "schema_version": REWARD_MEMORY_STANDING_POLICY_SCHEMA_VERSION,
        "policy_id": _token(raw.get("policy_id"), "standing_policy.policy_id"),
        "enabled": _boolean(raw.get("enabled"), "standing_policy.enabled"),
        "auto_activate": _boolean(
            raw.get("auto_activate"), "standing_policy.auto_activate"
        ),
        "owner_ref": _token(raw.get("owner_ref"), "standing_policy.owner_ref"),
        "reviewer_ref": _token(raw.get("reviewer_ref"), "standing_policy.reviewer_ref"),
        "authority_source_ref": _token(
            raw.get("authority_source_ref"),
            "standing_policy.authority_source_ref",
        ),
        "scope": normalized_scope,
        "allowed_target_classes": target_classes,
        "allowed_source_kinds": _tokens(
            raw.get("allowed_source_kinds"),
            "standing_policy.allowed_source_kinds",
        ),
        "allowed_actor_roles": _tokens(
            raw.get("allowed_actor_roles"),
            "standing_policy.allowed_actor_roles",
        ),
        "allowed_action_scopes": _tokens(
            raw.get("allowed_action_scopes"),
            "standing_policy.allowed_action_scopes",
            allow_empty=True,
        ),
        "raw_content_captured": False,
    }


def _policy_guard(
    candidate_packet: Mapping[str, Any],
    *,
    corpus: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = candidate_packet.get("candidate")
    guard = candidate_packet.get("guard")
    if not isinstance(candidate, Mapping) or not isinstance(guard, Mapping):
        raise ValueError("candidate packet is incomplete")
    source = candidate.get("source")
    scope = candidate.get("scope")
    if not isinstance(source, Mapping) or not isinstance(scope, Mapping):
        raise ValueError("candidate source and scope must be objects")

    reasons = list(guard.get("reason_codes") or [])
    if guard.get("passed") is not True and not reasons:
        reasons.append("candidate_guard_not_passed")
    if policy["enabled"] is not True:
        reasons.append("standing_policy_disabled")
    if policy["auto_activate"] is not True:
        reasons.append("standing_policy_requires_manual_review")
    if policy["owner_ref"] != corpus["owner_ref"]:
        reasons.append("standing_policy_owner_mismatch")
    corpus_scope = corpus["scope"]
    policy_scope = policy["scope"]
    candidate_surfaces = set(scope.get("surface_ids") or [])
    if policy_scope["workspace_ref"] != corpus_scope["workspace_ref"]:
        reasons.append("standing_policy_workspace_corpus_mismatch")
    if policy_scope["project_ref"] != corpus_scope["project_ref"]:
        reasons.append("standing_policy_project_corpus_mismatch")
    for field in IDENTITY_SCOPE_FIELDS:
        if policy_scope.get(field) != corpus_scope.get(field):
            reasons.append(f"standing_policy_{field}_corpus_mismatch")
    if not set(policy_scope["surface_ids"]).issubset(set(corpus_scope["surface_ids"])):
        reasons.append("standing_policy_surface_exceeds_corpus")
    if corpus["lifecycle"]["state"] != "active":
        reasons.append("corpus_not_active")
    if corpus["write_authority"] in {"read_only", "ephemeral_runtime"}:
        reasons.append("corpus_write_authority_not_durable")
    if corpus["retrieval"]["readback_required"] is not True:
        reasons.append("corpus_exact_readback_not_required")
    if candidate.get("target_class") != corpus["class_id"]:
        reasons.append("candidate_class_corpus_mismatch")
    if policy["allowed_target_classes"] != [corpus["class_id"]]:
        reasons.append("standing_policy_class_not_exact_for_corpus")
    if candidate.get("target_class") not in policy["allowed_target_classes"]:
        reasons.append("candidate_class_not_allowed")
    if source.get("source_kind") not in policy["allowed_source_kinds"]:
        reasons.append("candidate_source_kind_not_allowed")
    if source.get("actor_role") not in policy["allowed_actor_roles"]:
        reasons.append("candidate_actor_role_not_allowed")
    if scope.get("workspace_ref") != policy_scope["workspace_ref"]:
        reasons.append("candidate_workspace_policy_mismatch")
    if scope.get("project_ref") != policy_scope["project_ref"]:
        reasons.append("candidate_project_policy_mismatch")
    for field in IDENTITY_SCOPE_FIELDS:
        if scope.get(field) != policy_scope.get(field):
            reasons.append(f"candidate_{field}_policy_mismatch")
    if len(candidate_surfaces) != 1:
        reasons.append("candidate_surface_count_not_one")
    if not candidate_surfaces.issubset(set(policy_scope["surface_ids"])):
        reasons.append("candidate_surface_not_allowed")
    if scope.get("workspace_ref") != corpus_scope["workspace_ref"]:
        reasons.append("candidate_workspace_corpus_mismatch")
    if scope.get("project_ref") != corpus_scope["project_ref"]:
        reasons.append("candidate_project_corpus_mismatch")
    for field in IDENTITY_SCOPE_FIELDS:
        if scope.get(field) != corpus_scope.get(field):
            reasons.append(f"candidate_{field}_corpus_mismatch")
    if not candidate_surfaces.issubset(set(corpus_scope["surface_ids"])):
        reasons.append("candidate_surface_corpus_mismatch")
    if corpus["freshness"]["mode"] == "revision_bound" and (
        scope.get("revision_ref") != corpus["freshness"]["source_revision"]
    ):
        reasons.append("candidate_revision_corpus_mismatch")
    if not set(candidate.get("requested_action_scopes") or []).issubset(
        set(policy["allowed_action_scopes"])
    ):
        reasons.append("candidate_action_scope_not_allowed")
    return {
        "passed": not reasons,
        "reason_codes": sorted(set(reasons)),
        "experience_quality": dict(guard.get("experience_quality") or {}),
        "semantic_reasoning_preserved": True,
        "rule": (
            "standing_policy_checks_exact_owner_class_source_actor_scope_and_"
            "authority_without_semantic_routing"
        ),
    }


def _freshness_context(
    corpus: Mapping[str, Any], revision_ref: object
) -> dict[str, Any]:
    return {
        "source_truth_current": True,
        "source_revision": revision_ref or corpus["freshness"].get("source_revision"),
        "age_seconds": 0,
    }


def ingest_reward_memory_candidate(
    candidate_packet: Mapping[str, Any],
    *,
    corpus: Mapping[str, Any],
    standing_policy: Mapping[str, Any],
    provider_binding: Mapping[str, Any],
    observed_at: str,
    execute: bool = False,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Activate, sync, and exactly read back one already-built compact candidate."""

    if candidate_packet.get("schema_version") != REWARD_MEMORY_CANDIDATE_SCHEMA_VERSION:
        raise ValueError("candidate_packet must use reward_memory_candidate_v0")
    normalized_corpus = normalize_reward_memory_corpus(corpus)
    policy = normalize_reward_memory_standing_policy(standing_policy)
    binding = normalize_reward_memory_provider_binding(
        provider_binding,
        normalized_corpus,
    )
    if binding["scope_ref"].rstrip("/").endswith(".json"):
        raise ValueError("provider_binding.scope_ref must identify a collection")
    candidate = candidate_packet.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate packet does not contain a candidate")
    candidate_ref = _token(candidate.get("candidate_ref"), "candidate_ref")
    candidate_scope = candidate.get("scope")
    if not isinstance(candidate_scope, Mapping):
        raise ValueError("candidate scope must be an object")
    surfaces = list(candidate_scope.get("surface_ids") or [])
    guard = _policy_guard(
        candidate_packet,
        corpus=normalized_corpus,
        policy=policy,
    )
    base: dict[str, Any] = {
        "ok": True,
        "schema_version": REWARD_MEMORY_INGEST_RECEIPT_SCHEMA_VERSION,
        "candidate_ref": candidate_ref,
        "corpus_id": normalized_corpus["corpus_id"],
        "policy_id": policy["policy_id"],
        "surface_ids": surfaces,
        "guard": guard,
        "experience_quality": dict(guard.get("experience_quality") or {}),
        "deduplicated": False,
        "exact_readback_verified": False,
        "memory_available_for_recall": False,
        "fail_open": True,
        "provider_failure_is_user_gate": False,
        "grants_new_action_authority": False,
        "raw_content_captured": False,
        "raw_provider_payload_captured": False,
        "external_writes_performed": False,
    }
    if guard["passed"] is not True:
        return base | {"status": "guard_blocked"}

    policy_digest = hashlib.sha256(policy["policy_id"].encode("utf-8")).hexdigest()[:12]
    candidate_digest = candidate_ref.split(":", 1)[-1]
    review = review_reward_memory_candidate(
        candidate_packet,
        {
            "decision": "accept",
            "reviewer_ref": policy["reviewer_ref"],
            "review_ref": f"review:{policy_digest}:{candidate_digest}",
            "reasoning_summary": (
                "Standing exact-scope policy accepted this compact candidate."
            ),
        },
    )
    active = build_active_reward_memory_record(
        review,
        normalized_corpus,
        activated_at=observed_at,
    )
    filename = f"reward-memory-{policy_digest}-{candidate_digest}.json"
    target_ref = f"{binding['scope_ref'].rstrip('/')}/{filename}"
    serialized = json.dumps(
        active,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_digest = hashlib.sha256(
        canonical_context_text(serialized).encode("utf-8")
    ).hexdigest()
    target_public_ref = opaque_provider_ref(
        provider=binding["provider_id"],
        namespace=binding["namespace"],
        resource_ref=target_ref,
    )
    prepared = base | {
        "activation_ref": active["activation_ref"],
        "provider_ref": target_public_ref,
        "next_recall": {
            "corpus_id": normalized_corpus["corpus_id"],
            "surface_ids": surfaces,
            "mode": "function_boundary",
            "automatic_recall": False,
        },
    }
    configured_provider: ContextProvider
    try:
        configured_provider = provider or build_context_provider(
            {
                "provider": binding["provider_id"],
                "provider_binary": binding.get("provider_binary"),
                "minimum_provider_version": binding.get("minimum_provider_version"),
                "actor_peer_id": binding.get("actor_peer_id"),
            }
        )
        with TemporaryDirectory(prefix="loopx-reward-memory-") as temporary_root:
            source_path = Path(temporary_root) / filename
            source_path.write_text(serialized + "\n", encoding="utf-8")
            sync = configured_provider.sync(
                namespace=binding["namespace"],
                resources=[(str(source_path), target_ref)],
                timeout_seconds=float(binding["timeout_seconds"]),
                observed_at=observed_at,
                execute=execute,
            )
    except Exception:  # noqa: BLE001 - provider execution is a fail-open boundary
        return prepared | {
            "status": "provider_unavailable",
            "reason_codes": ["provider_sync_execution_failed"],
        }

    sync_packet = sync.public_packet()
    synced = prepared | {
        "write": sync_packet,
        "deduplicated": sync.status == "completed" and sync.write_count == 0,
        "external_writes_performed": sync.write_count > 0,
    }
    if not execute:
        return synced | {
            "status": sync.status,
            "reason_codes": [sync.reason_code] if sync.reason_code else [],
        }
    if sync.status == "committed_pending":
        return synced | {
            "status": "committed_pending",
            "reason_codes": [sync.reason_code or "provider_commit_pending"],
        }
    if sync.status != "completed" or target_ref not in sync.result_refs:
        return synced | {
            "status": "provider_unavailable",
            "reason_codes": [sync.reason_code or "provider_sync_unverified"],
        }

    surface_id = str(surfaces[0])
    scope = candidate.get("scope")
    assert isinstance(scope, Mapping)
    identity_scope = {field: scope.get(field) for field in IDENTITY_SCOPE_FIELDS}
    recall_request = build_reward_memory_recall_request(
        normalized_corpus,
        {
            "workspace_ref": scope["workspace_ref"],
            "project_ref": scope["project_ref"],
            **identity_scope,
            "surface_id": surface_id,
            "revision_ref": scope.get("revision_ref"),
            "mode": "function_boundary",
            "query_kind": "ingest_verification",
            "queries": [
                {
                    "query": f"{candidate_ref} {candidate['content_summary']}",
                    "query_summary": "Verify the just-activated reward-memory record.",
                }
            ],
            "limit": 3,
            "observed_at": observed_at,
            "freshness_context": _freshness_context(
                normalized_corpus,
                scope.get("revision_ref"),
            ),
            "conflict_state": "clear",
            "raw_content_captured": False,
        },
        read_authority_checkpoint={
            "verified": True,
            "corpus_id": normalized_corpus["corpus_id"],
            "workspace_ref": scope["workspace_ref"],
            "project_ref": scope["project_ref"],
            **identity_scope,
            "surface_id": surface_id,
            "read_authority": normalized_corpus["read_authority"],
            "source_ref": policy["authority_source_ref"],
        },
    )
    recall = execute_reward_memory_recall(
        recall_request,
        provider_binding=binding,
        provider=configured_provider,
    )
    exact = any(
        (
            item.memory_ref == target_ref
            or item.memory_ref.startswith(target_ref.rstrip("/") + "/")
        )
        and item.candidate_ref == candidate_ref
        and item.content_digest == expected_digest
        for item in recall.items
    )
    qualified = (guard.get("experience_quality") or {}).get("passed") is True
    destination_required = candidate.get("target_class") == "procedural_experience"
    destination_verified = not destination_required
    destination_recall = None
    experience_digest = None
    if exact and destination_required:
        experience = candidate.get("experience")
        assert isinstance(experience, Mapping)
        experience_digest = procedural_experience_digest(experience)
        applicability = list(experience.get("applicability") or [])
        future_behavior = experience.get("future_behavior")
        assert isinstance(future_behavior, Mapping)
        destination_request = build_reward_memory_recall_request(
            normalized_corpus,
            {
                "workspace_ref": scope["workspace_ref"],
                "project_ref": scope["project_ref"],
                **identity_scope,
                "surface_id": surface_id,
                "revision_ref": scope.get("revision_ref"),
                "mode": "function_boundary",
                "query_kind": "business_recall",
                "queries": [
                    {
                        "query": " ".join(
                            [
                                str(applicability[0]),
                                str(future_behavior["trigger"]),
                                str(future_behavior["action"]),
                            ]
                        ),
                        "query_summary": (
                            "Verify semantic recall at the configured destination "
                            "surface."
                        ),
                    }
                ],
                "limit": 3,
                "observed_at": observed_at,
                "freshness_context": _freshness_context(
                    normalized_corpus,
                    scope.get("revision_ref"),
                ),
                "conflict_state": "clear",
                "raw_content_captured": False,
            },
            read_authority_checkpoint={
                "verified": True,
                "corpus_id": normalized_corpus["corpus_id"],
                "workspace_ref": scope["workspace_ref"],
                "project_ref": scope["project_ref"],
                **identity_scope,
                "surface_id": surface_id,
                "read_authority": normalized_corpus["read_authority"],
                "source_ref": policy["authority_source_ref"],
            },
        )
        destination_recall = execute_reward_memory_recall(
            destination_request,
            provider_binding=binding,
            provider=configured_provider,
        )
        destination_verified = any(
            item.candidate_ref == candidate_ref
            and item.experience_digest == experience_digest
            for item in destination_recall.items
        )
    available = exact and qualified and destination_verified
    status = (
        "activated"
        if available
        else "readback_unverified"
        if not exact
        else "recall_unverified"
    )
    reason_codes = (
        []
        if available
        else ["exact_provider_readback_unverified"]
        if not exact
        else ["destination_business_recall_unverified"]
    )
    return synced | {
        "status": status,
        "readback": recall.public_packet,
        "exact_readback_verified": exact,
        "destination_recall": {
            "required": destination_required,
            "verified": destination_verified,
            "query_kind": "business_recall" if destination_required else None,
            "experience_digest": experience_digest,
            "receipt": (
                destination_recall.public_packet
                if destination_recall is not None
                else None
            ),
        },
        "memory_available_for_recall": available,
        "reason_codes": reason_codes,
    }
