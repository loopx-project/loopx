from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...public_safe_text import (
    MODULE_QUALIFIED_SURFACE_PATTERN as SURFACE_RE,
    PUBLIC_SAFE_REFERENCE_PATTERN as TOKEN_RE,
)
from .architecture import MEMORY_CLASS_IDS


REWARD_MEMORY_CORPUS_REGISTRY_SCHEMA_VERSION = "reward_memory_corpus_registry_v0"
REWARD_MEMORY_SEMANTIC_PREFERENCE_BRIDGE_SCHEMA_VERSION = (
    "reward_memory_semantic_preference_registry_bridge_v0"
)

MAX_CORPORA = 50
MAX_SURFACES = 20
CORPUS_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

READ_AUTHORITIES = {
    "goal_run_scoped",
    "authority_scoped",
    "module_scoped",
    "actor_scoped",
    "session_scoped",
}
WRITE_AUTHORITIES = {
    "append_only_overlay",
    "authorized_policy_source",
    "provider_managed",
    "read_only",
    "ephemeral_runtime",
}
FRESHNESS_MODES = {
    "source_truth_bound",
    "revision_bound",
    "time_bound",
    "session_archive_bound",
    "execution_bound",
}
LIFECYCLE_STATES = {"active", "superseded", "retired"}
VISIBILITIES = {"private", "workspace", "public_safe"}
IDENTITY_SCOPE_FIELDS = ("user_ref", "peer_ref", "session_ref")


def _token(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not TOKEN_RE.fullmatch(result):
        raise ValueError(f"{label} must be a compact public-safe token")
    return result


def _optional_token(value: object, label: str) -> str | None:
    if value in (None, ""):
        return None
    return _token(value, label)


def _boolean(mapping: Mapping[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _tokens(
    values: object,
    label: str,
    *,
    maximum: int,
    surface: bool = False,
) -> list[str]:
    if not isinstance(values, list) or len(values) > maximum:
        raise ValueError(f"{label} must be a bounded list")
    normalized: list[str] = []
    for value in values:
        item = str(value or "").strip()
        pattern = SURFACE_RE if surface else TOKEN_RE
        if not pattern.fullmatch(item):
            raise ValueError(f"{label} contains an invalid token")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must not contain duplicates")
    return sorted(normalized)


def _normalize_scope(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("corpus scope must be an object")
    surfaces = _tokens(
        raw.get("surface_ids"),
        "scope.surface_ids",
        maximum=MAX_SURFACES,
        surface=True,
    )
    if not surfaces:
        raise ValueError("scope.surface_ids must not be empty")
    scope = {
        "workspace_ref": _token(raw.get("workspace_ref"), "scope.workspace_ref"),
        "project_ref": _token(raw.get("project_ref"), "scope.project_ref"),
        "surface_ids": surfaces,
    }
    for field in IDENTITY_SCOPE_FIELDS:
        scope[field] = _optional_token(raw.get(field), f"scope.{field}")
    return scope


def _normalize_freshness(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("corpus freshness must be an object")
    mode = str(raw.get("mode") or "").strip()
    if mode not in FRESHNESS_MODES:
        raise ValueError("freshness.mode is invalid")
    max_age = raw.get("max_age_seconds")
    if max_age is not None and (
        isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 1
    ):
        raise ValueError("freshness.max_age_seconds must be a positive integer")
    source_revision = _optional_token(
        raw.get("source_revision"), "freshness.source_revision"
    )
    if mode in {"revision_bound", "session_archive_bound"} and not source_revision:
        raise ValueError(f"freshness mode {mode} requires source_revision")
    if mode == "time_bound" and max_age is None:
        raise ValueError("time_bound freshness requires max_age_seconds")
    return {
        "mode": mode,
        "source_revision": source_revision,
        "max_age_seconds": max_age,
    }


def _normalize_lifecycle(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("corpus lifecycle must be an object")
    state = str(raw.get("state") or "").strip()
    if state not in LIFECYCLE_STATES:
        raise ValueError("lifecycle.state is invalid")
    supersedes = _tokens(
        raw.get("supersedes") or [],
        "lifecycle.supersedes",
        maximum=MAX_CORPORA,
    )
    if any(not CORPUS_ID_RE.fullmatch(item) for item in supersedes):
        raise ValueError("lifecycle.supersedes must contain corpus ids")
    superseded_by = _optional_token(raw.get("superseded_by"), "lifecycle.superseded_by")
    if superseded_by and not CORPUS_ID_RE.fullmatch(superseded_by):
        raise ValueError("lifecycle.superseded_by must be a corpus id")
    retirement_reason = _optional_token(
        raw.get("retirement_reason"), "lifecycle.retirement_reason"
    )
    if state == "superseded" and not superseded_by:
        raise ValueError("superseded corpus requires lifecycle.superseded_by")
    if state == "retired" and not retirement_reason:
        raise ValueError("retired corpus requires lifecycle.retirement_reason")
    return {
        "state": state,
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "retirement_reason": retirement_reason,
    }


def _normalize_retrieval(raw: object) -> dict[str, bool]:
    if not isinstance(raw, Mapping):
        raise ValueError("corpus retrieval must be an object")
    return {
        key: _boolean(raw, key)
        for key in (
            "index_required",
            "readback_required",
            "application_receipt_required",
        )
    }


def _normalize_maintenance(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("corpus maintenance must be an object")
    triggers = _tokens(
        raw.get("writeback_triggers") or [],
        "maintenance.writeback_triggers",
        maximum=10,
    )
    closure_policy = _token(raw.get("closure_policy"), "maintenance.closure_policy")
    retirement_authority = _token(
        raw.get("retirement_authority"), "maintenance.retirement_authority"
    )
    return {
        "writeback_triggers": triggers,
        "closure_policy": closure_policy,
        "retirement_authority": retirement_authority,
    }


def _normalize_privacy(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("corpus privacy must be an object")
    visibility = str(raw.get("visibility") or "").strip()
    if visibility not in VISIBILITIES:
        raise ValueError("privacy.visibility is invalid")
    raw_content = _boolean(raw, "raw_content_in_registry")
    if raw_content:
        raise ValueError("reward-memory registry must not contain raw memory content")
    return {
        "visibility": visibility,
        "raw_content_in_registry": False,
    }


def normalize_reward_memory_corpus(corpus: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one compact corpus declaration without reading provider content."""

    corpus_id = str(corpus.get("corpus_id") or "").strip()
    if not CORPUS_ID_RE.fullmatch(corpus_id):
        raise ValueError("corpus_id must be a lower-snake token")
    class_id = str(corpus.get("class_id") or "").strip()
    if class_id not in MEMORY_CLASS_IDS:
        raise ValueError("class_id is not a Stage-0 reward-memory class")
    read_authority = str(corpus.get("read_authority") or "").strip()
    write_authority = str(corpus.get("write_authority") or "").strip()
    if read_authority not in READ_AUTHORITIES:
        raise ValueError("read_authority is invalid")
    if write_authority not in WRITE_AUTHORITIES:
        raise ValueError("write_authority is invalid")
    entry = {
        "corpus_id": corpus_id,
        "class_id": class_id,
        "provider_id": _token(corpus.get("provider_id"), "provider_id"),
        "owner_ref": _token(corpus.get("owner_ref"), "owner_ref"),
        "source_of_truth": _token(corpus.get("source_of_truth"), "source_of_truth"),
        "read_authority": read_authority,
        "write_authority": write_authority,
        "scope": _normalize_scope(corpus.get("scope")),
        "freshness": _normalize_freshness(corpus.get("freshness")),
        "lifecycle": _normalize_lifecycle(corpus.get("lifecycle")),
        "retrieval": _normalize_retrieval(corpus.get("retrieval")),
        "maintenance": _normalize_maintenance(corpus.get("maintenance")),
        "privacy": _normalize_privacy(corpus.get("privacy")),
    }
    scope_digest = _optional_token(
        corpus.get("provider_scope_ref_digest"), "provider_scope_ref_digest"
    )
    if scope_digest:
        entry["provider_scope_ref_digest"] = scope_digest
    read_role = _optional_token(corpus.get("read_role"), "read_role")
    if read_role:
        entry["read_role"] = read_role
    return entry


def _reference_corpora() -> list[dict[str, Any]]:
    base_scope = {
        "workspace_ref": "workspace:exact",
        "project_ref": "project:exact",
        "surface_ids": ["control_plane.reward_review"],
    }
    private = {"visibility": "private", "raw_content_in_registry": False}
    active = {"state": "active", "supersedes": []}
    return [
        {
            "corpus_id": "run_reward_overlays",
            "class_id": "run_bound_reward",
            "provider_id": "loopx_control_plane",
            "owner_ref": "loopx_reward_ledger",
            "source_of_truth": "human_reward_event_ledger",
            "read_authority": "goal_run_scoped",
            "write_authority": "append_only_overlay",
            "scope": base_scope,
            "freshness": {"mode": "source_truth_bound"},
            "lifecycle": active,
            "retrieval": {
                "index_required": False,
                "readback_required": True,
                "application_receipt_required": False,
            },
            "maintenance": {
                "writeback_triggers": [
                    "explicit_human_reward",
                    "correction_or_revocation",
                ],
                "closure_policy": "append_event_then_readback",
                "retirement_authority": "loopx_reward_ledger",
            },
            "privacy": private,
        },
        {
            "corpus_id": "authority_policy_sources",
            "class_id": "hard_policy",
            "provider_id": "loopx_control_plane",
            "owner_ref": "canonical_authority_sources",
            "source_of_truth": "user_repository_operator_authority",
            "read_authority": "authority_scoped",
            "write_authority": "authorized_policy_source",
            "scope": base_scope | {"surface_ids": ["control_plane.action_gate"]},
            "freshness": {"mode": "source_truth_bound"},
            "lifecycle": active,
            "retrieval": {
                "index_required": False,
                "readback_required": True,
                "application_receipt_required": False,
            },
            "maintenance": {
                "writeback_triggers": [
                    "authority_source_changed",
                    "verified_contributor_policy_derived",
                    "expiry_reached",
                ],
                "closure_policy": (
                    "verified_actor_scope_then_canonical_policy_readback"
                ),
                "retirement_authority": "canonical_authority_sources",
            },
            "privacy": private,
        },
        {
            "corpus_id": "scoped_preferences",
            "class_id": "soft_preference",
            "provider_id": "configured_memory_provider",
            "owner_ref": "provider_scope_owner",
            "source_of_truth": "explicit_reviewed_feedback",
            "read_authority": "module_scoped",
            "write_authority": "provider_managed",
            "scope": base_scope | {"surface_ids": ["module.owned_surface"]},
            "freshness": {"mode": "source_truth_bound"},
            "lifecycle": active,
            "retrieval": {
                "index_required": True,
                "readback_required": True,
                "application_receipt_required": True,
            },
            "maintenance": {
                "writeback_triggers": [
                    "explicit_feedback",
                    "source_truth_changed",
                ],
                "closure_policy": "provider_write_index_read_and_scoped_recall",
                "retirement_authority": "provider_scope_owner",
            },
            "privacy": private,
        },
        {
            "corpus_id": "execution_trajectories",
            "class_id": "procedural_experience",
            "provider_id": "configured_memory_provider",
            "owner_ref": "provider_scope_owner",
            "source_of_truth": "revision_stamped_execution",
            "read_authority": "module_scoped",
            "write_authority": "provider_managed",
            "scope": base_scope | {"surface_ids": ["issue_fix.diagnosis"]},
            "freshness": {
                "mode": "revision_bound",
                "source_revision": "revision:observed",
            },
            "lifecycle": active,
            "retrieval": {
                "index_required": True,
                "readback_required": True,
                "application_receipt_required": True,
            },
            "maintenance": {
                "writeback_triggers": ["validated_execution"],
                "closure_policy": "provider_write_then_revision_verified_read",
                "retirement_authority": "provider_scope_owner",
            },
            "privacy": private,
        },
        {
            "corpus_id": "distilled_experiences",
            "class_id": "procedural_experience",
            "provider_id": "configured_memory_provider",
            "owner_ref": "provider_scope_owner",
            "source_of_truth": "reviewed_revision_lineage",
            "read_authority": "module_scoped",
            "write_authority": "provider_managed",
            "scope": base_scope | {"surface_ids": ["issue_fix.routing"]},
            "freshness": {
                "mode": "revision_bound",
                "source_revision": "revision:observed",
            },
            "lifecycle": active,
            "retrieval": {
                "index_required": True,
                "readback_required": True,
                "application_receipt_required": True,
            },
            "maintenance": {
                "writeback_triggers": [
                    "reviewed_candidate",
                    "source_truth_changed",
                ],
                "closure_policy": "provider_write_then_revision_verified_read",
                "retirement_authority": "provider_scope_owner",
            },
            "privacy": private,
        },
        {
            "corpus_id": "session_working_memory",
            "class_id": "working_context",
            "provider_id": "configured_session_provider",
            "owner_ref": "session_owner",
            "source_of_truth": "completed_session_archive",
            "read_authority": "session_scoped",
            "write_authority": "provider_managed",
            "scope": base_scope
            | {
                "surface_ids": ["session.continuation"],
                "session_ref": "session:exact",
            },
            "freshness": {
                "mode": "session_archive_bound",
                "source_revision": "archive:completed",
            },
            "lifecycle": active,
            "retrieval": {
                "index_required": False,
                "readback_required": True,
                "application_receipt_required": False,
            },
            "maintenance": {
                "writeback_triggers": ["completed_session_archive"],
                "closure_policy": "completed_archive_then_direct_read",
                "retirement_authority": "session_owner",
            },
            "privacy": private,
        },
        {
            "corpus_id": "fresh_execution_context",
            "class_id": "working_context",
            "provider_id": "loopx_runtime",
            "owner_ref": "current_execution",
            "source_of_truth": "registry_todo_checkout_observation",
            "read_authority": "actor_scoped",
            "write_authority": "ephemeral_runtime",
            "scope": base_scope | {"surface_ids": ["control_plane.current_execution"]},
            "freshness": {"mode": "execution_bound"},
            "lifecycle": active,
            "retrieval": {
                "index_required": False,
                "readback_required": True,
                "application_receipt_required": False,
            },
            "maintenance": {
                "writeback_triggers": ["fresh_source_observed"],
                "closure_policy": "fresh_read_replaces_prior_context",
                "retirement_authority": "current_execution",
            },
            "privacy": private,
        },
    ]


def build_reward_memory_corpus_registry_packet(
    corpora: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a stateless registry read model over provider-owned corpora."""

    raw_corpora = list(corpora) if corpora is not None else _reference_corpora()
    if not raw_corpora or len(raw_corpora) > MAX_CORPORA:
        raise ValueError(f"corpora must contain between 1 and {MAX_CORPORA} items")
    normalized = [normalize_reward_memory_corpus(item) for item in raw_corpora]
    ids = [item["corpus_id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("corpus_id values must be unique")
    known_ids = set(ids)
    for item in normalized:
        lifecycle = item["lifecycle"]
        refs = set(lifecycle["supersedes"])
        if lifecycle["superseded_by"]:
            refs.add(lifecycle["superseded_by"])
        if item["corpus_id"] in refs:
            raise ValueError("corpus lifecycle must not reference itself")
        if refs - known_ids:
            raise ValueError("corpus lifecycle references an unknown corpus_id")
    coverage = {
        class_id: sorted(
            item["corpus_id"] for item in normalized if item["class_id"] == class_id
        )
        for class_id in MEMORY_CLASS_IDS
    }
    return {
        "ok": True,
        "schema_version": REWARD_MEMORY_CORPUS_REGISTRY_SCHEMA_VERSION,
        "status": "reference_registry" if corpora is None else "registered",
        "registry_role": "stateless_read_model_not_memory_source_of_truth",
        "corpus_count": len(normalized),
        "corpora": normalized,
        "class_coverage": coverage,
        "maintenance_contract": {
            "inventory_owner": "provider_or_canonical_source_owner",
            "write_rule": "write_only_through_declared_write_authority",
            "freshness_rule": "verify_source_truth_revision_or_archive_before_use",
            "supersession_rule": "preserve_lineage_and_stop_recalling_inactive_items",
            "retirement_rule": "retain_compact_reason_without_raw_content",
            "scope_rule": "fail_closed_on_project_or_surface_mismatch",
        },
        "provider_alignment": {
            "openviking": {
                "content_source_of_truth": "agfs_content",
                "index_role": "derived_retrieval_reference",
                "preferences": "scoped_preferences",
                "trajectories": "execution_trajectories",
                "experiences": "distilled_experiences",
                "working_memory": "session_working_memory",
                "cases": "evaluation_fixture_not_registered_as_executable_memory",
            }
        },
        "raw_memory_captured": False,
        "registry_persisted": False,
        "provider_write_performed": False,
        "external_writes_performed": False,
    }


def semantic_preference_inventory_to_reward_corpora(
    inventory: Sequence[Mapping[str, Any]],
    *,
    provider_id: str,
    workspace_ref: str,
    project_ref: str,
    surface: str,
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Bridge the shipped semantic-preference inventory into the Stage-1 registry."""

    provider = _token(provider_id, "provider_id")
    workspace = _token(workspace_ref, "workspace_ref")
    project = _token(project_ref, "project_ref")
    if not SURFACE_RE.fullmatch(surface):
        raise ValueError("surface must be a module-qualified token")
    if not isinstance(inventory, Sequence) or isinstance(inventory, (str, bytes)):
        raise ValueError("inventory must be a bounded sequence")
    if not inventory or len(inventory) > MAX_CORPORA:
        raise ValueError("inventory must contain a bounded non-empty corpus list")
    corpora: list[dict[str, Any]] = []
    for item in inventory:
        if not isinstance(item, Mapping):
            raise ValueError("inventory items must be objects")
        corpus_id = str(item.get("corpus_id") or "").strip()
        if not CORPUS_ID_RE.fullmatch(corpus_id):
            raise ValueError("inventory corpus_id is invalid")
        scope_ref = _token(item.get("scope_ref"), "inventory.scope_ref")
        write_mode = str(item.get("write_mode") or "").strip()
        if write_mode not in {"read_only", "provider_managed"}:
            raise ValueError("inventory write_mode is invalid")
        owner_ref = str(item.get("write_actor_ref") or provider).strip()
        freshness: dict[str, Any] = {"mode": "source_truth_bound"}
        if source_revision:
            freshness = {
                "mode": "revision_bound",
                "source_revision": _token(source_revision, "source_revision"),
            }
        corpora.append(
            {
                "corpus_id": corpus_id,
                "class_id": "soft_preference",
                "provider_id": provider,
                "owner_ref": owner_ref,
                "source_of_truth": _token(
                    item.get("source_of_truth"), "inventory.source_of_truth"
                ),
                "read_authority": "module_scoped",
                "write_authority": write_mode,
                "scope": {
                    "workspace_ref": workspace,
                    "project_ref": project,
                    "surface_ids": [surface],
                },
                "freshness": freshness,
                "lifecycle": {"state": "active", "supersedes": []},
                "retrieval": {
                    "index_required": True,
                    "readback_required": True,
                    "application_receipt_required": True,
                },
                "maintenance": {
                    "writeback_triggers": list(item.get("writeback_triggers") or []),
                    "closure_policy": _token(
                        item.get("closure_policy"), "inventory.closure_policy"
                    ),
                    "retirement_authority": owner_ref,
                },
                "privacy": {
                    "visibility": "private",
                    "raw_content_in_registry": False,
                },
                "provider_scope_ref_digest": hashlib.sha256(
                    scope_ref.encode("utf-8")
                ).hexdigest()[:16],
                "read_role": _token(item.get("read_role"), "inventory.read_role"),
            }
        )
    packet = build_reward_memory_corpus_registry_packet(corpora)
    packet["bridge_schema_version"] = (
        REWARD_MEMORY_SEMANTIC_PREFERENCE_BRIDGE_SCHEMA_VERSION
    )
    packet["source_inventory_schema"] = "semantic_preference_provider_response_v0"
    return packet
