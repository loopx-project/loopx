from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ..context_providers import build_context_provider
from ..context_providers.base import (
    ContextProvider,
    ContextProviderItem,
    canonical_context_text,
    opaque_provider_ref,
)
from ..context_providers.openviking import classify_openviking_scope
from .candidate_review import REWARD_MEMORY_REVIEW_SCHEMA_VERSION
from .experience_quality import (
    normalize_procedural_experience,
    procedural_experience_digest,
    procedural_experience_quality,
)
from .registry import IDENTITY_SCOPE_FIELDS, normalize_reward_memory_corpus


REWARD_MEMORY_ACTIVE_RECORD_SCHEMA_VERSION = "reward_memory_active_record_v0"
REWARD_MEMORY_RECALL_REQUEST_SCHEMA_VERSION = "reward_memory_recall_request_v0"
REWARD_MEMORY_RECALL_SCHEMA_VERSION = "reward_memory_recall_v0"
REWARD_MEMORY_APPLICATION_RECEIPT_SCHEMA_VERSION = (
    "reward_memory_application_receipt_v0"
)
REWARD_MEMORY_APPLICATION_SCHEMA_VERSION = "reward_memory_application_v0"

RECALL_MODES = {"function_boundary", "bounded_agentic_search"}
RECALL_QUERY_KINDS = {"business_recall", "ingest_verification"}
APPLICATION_OUTCOMES = {
    "applied",
    "ignored",
    "refuted",
    "failed",
    "not_available",
    "available_not_applied",
}
DURABLE_RECALL_CLASSES = {
    "hard_policy",
    "soft_preference",
    "procedural_experience",
}
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$")
SURFACE_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
MAX_QUERY_STEPS = 3
MAX_RESULTS = 8
MAX_SETUP_HINT = 500


@dataclass(frozen=True)
class RewardMemoryRecallItem:
    """One transient reviewed record exposed to the reasoning callback."""

    memory_ref: str
    candidate_ref: str
    target_class: str
    content_summary: str
    experience: Mapping[str, Any] | None = None
    experience_digest: str = ""
    content_digest: str = ""


@dataclass(frozen=True)
class RewardMemoryRecallSession:
    """Public-safe recall packet plus transient in-process reasoning inputs."""

    public_packet: dict[str, Any]
    items: tuple[RewardMemoryRecallItem, ...] = ()
    filtered_items: tuple["RewardMemoryFilteredRecallItem", ...] = ()


@dataclass(frozen=True)
class RewardMemoryFilteredRecallItem:
    """One private provider hit rejected by a typed recall qualification gate."""

    memory_ref: str
    reason_code: str


@dataclass(frozen=True)
class _ActiveItemDecision:
    item: RewardMemoryRecallItem | None
    reason_code: str | None


RewardMemoryApplier = Callable[
    [Any, tuple[RewardMemoryRecallItem, ...]], Mapping[str, Any]
]


RecallInputErrorCode = Literal[
    "freshness_age_invalid", "freshness_context_invalid",
    "read_authority_checkpoint_missing", "read_authority_checkpoint_invalid",
]


class RewardMemoryRecallInputError(ValueError):
    """An existing SDK input rejection with an allowlisted, non-content code."""

    def __init__(self, reason_code: RecallInputErrorCode, message: str) -> None:
        if reason_code not in get_args(RecallInputErrorCode):
            raise ValueError("unsupported recall input error code")
        super().__init__(message)
        self.reason_code = reason_code


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


def _observed_at(value: object) -> str:
    result = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    return result


def _scope_matches(record: Mapping[str, Any], corpus: Mapping[str, Any]) -> bool:
    scope = record.get("scope")
    corpus_scope = corpus["scope"]
    if not isinstance(scope, Mapping):
        return False
    return (
        scope.get("workspace_ref") == corpus_scope["workspace_ref"]
        and scope.get("project_ref") == corpus_scope["project_ref"]
        and all(
            scope.get(field) == corpus_scope.get(field)
            for field in IDENTITY_SCOPE_FIELDS
        )
        and set(scope.get("surface_ids") or []).issubset(
            set(corpus_scope["surface_ids"])
        )
    )


def build_active_reward_memory_record(
    reviewed_candidate: Mapping[str, Any],
    corpus: Mapping[str, Any],
    *,
    activated_at: str,
) -> dict[str, Any]:
    """Build the reviewed record envelope a declared corpus owner may persist."""

    normalized = normalize_reward_memory_corpus(corpus)
    if reviewed_candidate.get("schema_version") != REWARD_MEMORY_REVIEW_SCHEMA_VERSION:
        raise ValueError(
            "reviewed_candidate must use reward_memory_candidate_review_v0"
        )
    if (
        reviewed_candidate.get("effective_decision") != "accept"
        or reviewed_candidate.get("guard_passed") is not True
        or reviewed_candidate.get("status") != "active"
    ):
        raise ValueError("only a guard-passed accepted candidate can become active")
    record = reviewed_candidate.get("record")
    if not isinstance(record, Mapping):
        raise ValueError("reviewed candidate record is missing")
    lifecycle = record.get("lifecycle")
    if not isinstance(lifecycle, Mapping) or lifecycle.get("state") != "active":
        raise ValueError("reviewed candidate record must be active")
    if record.get("target_class") != normalized["class_id"]:
        raise ValueError("candidate class must match the selected corpus")
    if not _scope_matches(record, normalized):
        raise ValueError("candidate scope must stay inside the selected corpus")
    if normalized["freshness"]["mode"] == "revision_bound" and (
        record["scope"].get("revision_ref")
        != normalized["freshness"]["source_revision"]
    ):
        raise ValueError("candidate revision must match the selected corpus")
    activated = _observed_at(activated_at)
    candidate_ref = _token(record.get("candidate_ref"), "candidate_ref")
    identity = {
        "corpus_id": normalized["corpus_id"],
        "candidate_ref": candidate_ref,
        "activated_at": activated,
    }
    activation_ref = (
        "activation:"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
    )
    active_lifecycle: dict[str, Any] = {"state": "active"}
    expires_at = record["lifecycle"].get("expires_at")
    if expires_at:
        active_lifecycle["expires_at"] = expires_at
    experience = record.get("experience")
    quality = procedural_experience_quality(
        target_class=str(record.get("target_class") or ""),
        experience=experience,
    )
    active = {
        "schema_version": REWARD_MEMORY_ACTIVE_RECORD_SCHEMA_VERSION,
        "activation_ref": activation_ref,
        "activated_at": activated,
        "corpus_id": normalized["corpus_id"],
        "candidate_ref": candidate_ref,
        "target_class": normalized["class_id"],
        "content_summary": _compact(
            record.get("content_summary"), "content_summary", limit=500
        ),
        "experience_quality": quality,
        "scope": dict(record["scope"]),
        "source": dict(record["source"]),
        "review": dict(reviewed_candidate["review"]),
        "activation_lineage": {
            "effective_decision": "accept",
            "guard_passed": True,
            "review_ref": reviewed_candidate["review"]["review_ref"],
        },
        "lifecycle": active_lifecycle,
        "privacy": {"raw_content_captured": False},
        "provider_write_performed": False,
        "external_writes_performed": False,
    }
    if experience is not None:
        active["experience"] = normalize_procedural_experience(experience)
    return active


def _authority_checkpoint(
    raw: object, *, corpus: Mapping[str, Any], request: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw, Mapping):
        raise RewardMemoryRecallInputError(
            "read_authority_checkpoint_missing" if raw is None else "read_authority_checkpoint_invalid",
            "read_authority_checkpoint must be an object",
        )
    try:
        checkpoint = _normalize_authority_checkpoint(raw, corpus=corpus)
    except ValueError as exc:
        raise RewardMemoryRecallInputError(
            "read_authority_checkpoint_missing" if not raw else "read_authority_checkpoint_invalid",
            str(exc),
        ) from exc
    reasons: list[str] = []
    expected = {
        "corpus_id": corpus["corpus_id"],
        "workspace_ref": request["workspace_ref"],
        "project_ref": request["project_ref"],
        "surface_id": request["surface_id"],
        "read_authority": corpus["read_authority"],
    }
    for field in IDENTITY_SCOPE_FIELDS:
        expected_scope = corpus["scope"].get(field)
        if expected_scope:
            expected[field] = expected_scope
            if request.get(field) != expected_scope:
                reasons.append(f"read_authority_{field}_scope_mismatch")
    if checkpoint["verified"] is not True:
        reasons.append("read_authority_unverified")
    if not checkpoint["source_ref"]:
        reasons.append("read_authority_source_missing")
    for key, value in expected.items():
        if checkpoint[key] != value:
            reasons.append(f"read_authority_{key}_mismatch")
    return checkpoint, reasons


def _normalize_authority_checkpoint(
    raw: Mapping[str, Any], *, corpus: Mapping[str, Any],
) -> dict[str, Any]:
    checkpoint = {
        "verified": _boolean(raw, "verified"),
        "corpus_id": _token(raw.get("corpus_id"), "checkpoint.corpus_id"),
        "workspace_ref": _token(raw.get("workspace_ref"), "checkpoint.workspace_ref"),
        "project_ref": _token(raw.get("project_ref"), "checkpoint.project_ref"),
        "surface_id": _token(raw.get("surface_id"), "checkpoint.surface_id"),
        "read_authority": _token(
            raw.get("read_authority"), "checkpoint.read_authority"
        ),
        "source_ref": _optional_token(raw.get("source_ref"), "checkpoint.source_ref"),
    }
    for field in IDENTITY_SCOPE_FIELDS:
        expected_scope = corpus["scope"].get(field)
        if expected_scope:
            checkpoint[field] = _optional_token(raw.get(field), f"checkpoint.{field}")
    return checkpoint


def _freshness_reasons(
    corpus: Mapping[str, Any], freshness: Mapping[str, Any]
) -> list[str]:
    reasons: list[str] = []
    mode = corpus["freshness"]["mode"]
    try:
        source_truth_current = _boolean(freshness, "source_truth_current")
        source_revision = _optional_token(
            freshness.get("source_revision"), "freshness_context.source_revision"
        )
    except ValueError as exc:
        raise RewardMemoryRecallInputError("freshness_context_invalid", str(exc)) from exc
    age_seconds = freshness.get("age_seconds")
    if age_seconds is not None and (
        isinstance(age_seconds, bool)
        or not isinstance(age_seconds, int)
        or age_seconds < 0
    ):
        raise RewardMemoryRecallInputError(
            "freshness_age_invalid", "freshness_context.age_seconds must be a non-negative integer",
        )
    if mode in {"source_truth_bound", "execution_bound"} and not source_truth_current:
        reasons.append("source_truth_not_current")
    if mode in {"revision_bound", "session_archive_bound"} and (
        source_revision != corpus["freshness"]["source_revision"]
    ):
        reasons.append("source_revision_mismatch")
    if mode == "time_bound" and (
        age_seconds is None or age_seconds > int(corpus["freshness"]["max_age_seconds"])
    ):
        reasons.append("source_age_exceeded")
    return reasons


def _queries(raw: object, mode: str) -> list[dict[str, str]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("queries must be a bounded list")
    maximum = 1 if mode == "function_boundary" else MAX_QUERY_STEPS
    if not 1 <= len(raw) <= maximum:
        raise ValueError(f"{mode} requires between 1 and {maximum} queries")
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("queries must contain objects")
        result.append(
            {
                "query": _compact(item.get("query"), "query", limit=500),
                "query_summary": _compact(
                    item.get("query_summary"), "query_summary", limit=220
                ),
            }
        )
    return result


def _query_evidence(queries: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    """Expose bounded query identity without persisting provider query text."""

    return [
        {
            "query_digest": hashlib.sha256(item["query"].encode("utf-8")).hexdigest()[
                :16
            ],
            "query_summary": item["query_summary"],
            "exact_query_exposed": False,
        }
        for item in queries
    ]


def build_reward_memory_recall_request(
    corpus: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    read_authority_checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one explicit, exact-corpus recall request without running a provider."""

    normalized = normalize_reward_memory_corpus(corpus)
    mode = str(request.get("mode") or "").strip()
    if mode not in RECALL_MODES:
        raise ValueError("mode must be function_boundary or bounded_agentic_search")
    surface = str(request.get("surface_id") or "").strip()
    if not SURFACE_RE.fullmatch(surface):
        raise ValueError("surface_id must be module-qualified")
    recall_request = {
        "workspace_ref": _token(request.get("workspace_ref"), "workspace_ref"),
        "project_ref": _token(request.get("project_ref"), "project_ref"),
        "surface_id": surface,
        "revision_ref": _optional_token(request.get("revision_ref"), "revision_ref"),
        "mode": mode,
        "queries": _queries(request.get("queries"), mode),
        "limit": request.get("limit", 5),
        "observed_at": _observed_at(request.get("observed_at")),
        "conflict_state": str(request.get("conflict_state") or "").strip(),
    }
    for field in IDENTITY_SCOPE_FIELDS:
        value = _optional_token(request.get(field), field)
        if value:
            recall_request[field] = value
    query_kind = str(request.get("query_kind") or "business_recall").strip()
    if query_kind not in RECALL_QUERY_KINDS:
        raise ValueError("query_kind must be business_recall or ingest_verification")
    recall_request["query_kind"] = query_kind
    recall_request["query_evidence"] = _query_evidence(recall_request["queries"])
    limit = recall_request["limit"]
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_RESULTS
    ):
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
    if not isinstance(request.get("freshness_context"), Mapping):
        raise RewardMemoryRecallInputError("freshness_context_invalid", "freshness_context must be an object")
    if _boolean(request, "raw_content_captured"):
        raise ValueError("recall requests must not capture raw content")

    reasons: list[str] = []
    scope = normalized["scope"]
    if normalized["class_id"] not in DURABLE_RECALL_CLASSES:
        reasons.append("corpus_class_not_durable_recall_memory")
    if normalized["lifecycle"]["state"] != "active":
        reasons.append("corpus_not_active")
    if recall_request["workspace_ref"] != scope["workspace_ref"]:
        reasons.append("workspace_scope_mismatch")
    if recall_request["project_ref"] != scope["project_ref"]:
        reasons.append("project_scope_mismatch")
    for field in IDENTITY_SCOPE_FIELDS:
        expected_scope = scope.get(field)
        if expected_scope and recall_request.get(field) != expected_scope:
            reasons.append(f"{field}_scope_mismatch")
    if surface not in scope["surface_ids"]:
        reasons.append("surface_scope_mismatch")
    if recall_request["conflict_state"] != "clear":
        reasons.append("unresolved_conflict")
    reasons.extend(_freshness_reasons(normalized, request["freshness_context"]))
    if normalized["freshness"]["mode"] == "revision_bound" and (
        recall_request["revision_ref"] != normalized["freshness"]["source_revision"]
    ):
        reasons.append("request_revision_mismatch")
    checkpoint, authority_reasons = _authority_checkpoint(
        read_authority_checkpoint,
        corpus=normalized,
        request=recall_request,
    )
    reasons.extend(authority_reasons)
    guard = {
        "passed": not reasons,
        "reason_codes": reasons,
        "semantic_reasoning_preserved": True,
        "rule": (
            "caller_supplies_queries_and_reasoning_deterministic_code_checks_"
            "scope_authority_privacy_freshness_and_conflict"
        ),
    }
    return {
        "ok": True,
        "schema_version": REWARD_MEMORY_RECALL_REQUEST_SCHEMA_VERSION,
        "status": "ready" if guard["passed"] else "guard_blocked",
        "corpus": normalized,
        "request": recall_request,
        "read_authority_checkpoint": checkpoint,
        "guard": guard,
        "automatic_recall": False,
        "provider_call_performed": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }


def normalize_reward_memory_provider_binding(
    raw: Mapping[str, Any], corpus: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate one exact corpus/provider binding for recall or writeback."""

    setup = raw.get("setup_hints") or {}
    if not isinstance(setup, Mapping):
        raise ValueError("provider_binding.setup_hints must be an object")
    hints = {
        key: public_safe_compact_text(setup.get(key), limit=MAX_SETUP_HINT)
        for key in ("install", "configure")
        if public_safe_compact_text(setup.get(key), limit=MAX_SETUP_HINT)
    }
    binding = {
        "corpus_id": _token(raw.get("corpus_id"), "provider_binding.corpus_id"),
        "provider_id": _token(raw.get("provider_id"), "provider_binding.provider_id"),
        "namespace": _token(raw.get("namespace"), "provider_binding.namespace"),
        "scope_ref": _token(raw.get("scope_ref"), "provider_binding.scope_ref"),
        "timeout_seconds": raw.get("timeout_seconds", 30),
        "setup_hints": hints,
    }
    if binding["corpus_id"] != corpus["corpus_id"]:
        raise ValueError("provider binding must select the exact corpus_id")
    if binding["provider_id"] != corpus["provider_id"]:
        raise ValueError("provider binding provider_id must match the corpus")
    timeout = binding["timeout_seconds"]
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 1 <= float(timeout) <= 120
    ):
        raise ValueError("provider binding timeout_seconds must be between 1 and 120")
    expected_digest = corpus.get("provider_scope_ref_digest")
    if (
        expected_digest
        and hashlib.sha256(binding["scope_ref"].encode("utf-8")).hexdigest()[:16]
        != expected_digest
    ):
        raise ValueError("provider binding scope_ref does not match the corpus digest")
    for key in ("provider_binary", "minimum_provider_version"):
        if raw.get(key):
            binding[key] = str(raw[key])
    actor_peer_id = _optional_token(
        raw.get("actor_peer_id"), "provider_binding.actor_peer_id"
    )
    if "/peers/" in binding["scope_ref"]:
        peer_scope_id = binding["scope_ref"].split("/peers/", 1)[1].split("/", 1)[0]
        if not actor_peer_id:
            raise ValueError("peer-scoped provider binding requires actor_peer_id")
        if actor_peer_id != peer_scope_id:
            raise ValueError("actor_peer_id must match the peer-scoped provider URI")
    if actor_peer_id:
        binding["actor_peer_id"] = actor_peer_id
    if binding["provider_id"] == "openviking":
        provider_scope = classify_openviking_scope(binding["scope_ref"])
        visibility = str((corpus.get("privacy") or {}).get("visibility") or "")
        peer_ref = str((corpus.get("scope") or {}).get("peer_ref") or "")
        agent_ref = (
            peer_ref.removeprefix("agent:") if peer_ref.startswith("agent:") else ""
        )
        if visibility == "private" and provider_scope.visibility != "private":
            raise ValueError(
                "private Reward Memory cannot bind to public OpenViking resources"
            )
        if visibility == "private" and not provider_scope.actor_binding_required:
            raise ValueError(
                "private Reward Memory requires an actor-bound peer OpenViking scope"
            )
        if visibility == "private" and not agent_ref:
            raise ValueError(
                "private Reward Memory corpus requires scope.peer_ref=agent:<agent_id>"
            )
        if (
            agent_ref
            and provider_scope.actor_scope_id is not None
            and provider_scope.actor_scope_id != actor_peer_id
        ):
            raise ValueError(
                "private Reward Memory provider scope must match actor_peer_id"
            )
    return binding


def _active_item_decision(
    item: ContextProviderItem,
    corpus: Mapping[str, Any],
    *,
    surface_id: str,
    observed_at: str,
) -> _ActiveItemDecision:
    try:
        envelope = json.loads(item.content)
    except json.JSONDecodeError:
        return _ActiveItemDecision(None, "record_contract_filtered")
    if not isinstance(envelope, Mapping):
        return _ActiveItemDecision(None, "record_contract_filtered")
    scope = envelope.get("scope")
    lifecycle = envelope.get("lifecycle")
    if (
        envelope.get("schema_version") != REWARD_MEMORY_ACTIVE_RECORD_SCHEMA_VERSION
        or envelope.get("corpus_id") != corpus["corpus_id"]
        or envelope.get("target_class") != corpus["class_id"]
    ):
        return _ActiveItemDecision(None, "record_contract_filtered")
    if (
        not isinstance(scope, Mapping)
        or not _scope_matches({"scope": scope}, corpus)
        or surface_id not in set(scope.get("surface_ids") or [])
    ):
        return _ActiveItemDecision(None, "scope_filtered")
    if not isinstance(lifecycle, Mapping) or lifecycle.get("state") != "active":
        return _ActiveItemDecision(None, "lifecycle_filtered")
    expires_at = lifecycle.get("expires_at")
    if expires_at:
        try:
            expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            return _ActiveItemDecision(None, "expiry_filtered")
        if expires.tzinfo is None or observed.tzinfo is None or observed >= expires:
            return _ActiveItemDecision(None, "expiry_filtered")
    experience = envelope.get("experience")
    if envelope.get("target_class") == "procedural_experience" and experience is None:
        return _ActiveItemDecision(None, "legacy_contract_missing")
    try:
        quality = procedural_experience_quality(
            target_class=str(envelope.get("target_class") or ""),
            experience=experience,
        )
    except ValueError:
        return _ActiveItemDecision(None, "quality_filtered")
    if quality["passed"] is not True:
        return _ActiveItemDecision(None, "quality_filtered")
    try:
        normalized_experience = (
            normalize_procedural_experience(experience)
            if experience is not None
            else None
        )
        recall_item = RewardMemoryRecallItem(
            memory_ref=item.resource_ref,
            candidate_ref=_token(envelope.get("candidate_ref"), "candidate_ref"),
            target_class=str(envelope["target_class"]),
            content_summary=_compact(
                envelope.get("content_summary"), "content_summary", limit=500
            ),
            experience=normalized_experience,
            experience_digest=(
                procedural_experience_digest(normalized_experience)
                if normalized_experience is not None
                else ""
            ),
            content_digest=hashlib.sha256(
                canonical_context_text(item.content).encode("utf-8")
            ).hexdigest(),
        )
    except (TypeError, ValueError):
        return _ActiveItemDecision(None, "record_contract_filtered")
    return _ActiveItemDecision(recall_item, None)


def _active_item(
    item: ContextProviderItem,
    corpus: Mapping[str, Any],
    *,
    surface_id: str,
    observed_at: str,
) -> RewardMemoryRecallItem | None:
    """Compatibility wrapper for callers that only need the qualified item."""

    return _active_item_decision(
        item,
        corpus,
        surface_id=surface_id,
        observed_at=observed_at,
    ).item


def execute_reward_memory_recall(
    recall_request: Mapping[str, Any],
    *,
    provider_binding: Mapping[str, Any],
    provider: ContextProvider | None = None,
) -> RewardMemoryRecallSession:
    """Execute an explicit recall and retain provider content only in-process."""

    if (
        recall_request.get("schema_version")
        != REWARD_MEMORY_RECALL_REQUEST_SCHEMA_VERSION
    ):
        raise ValueError("recall_request must use reward_memory_recall_request_v0")
    corpus = recall_request.get("corpus")
    request = recall_request.get("request")
    guard = recall_request.get("guard")
    if not all(isinstance(value, Mapping) for value in (corpus, request, guard)):
        raise ValueError("recall_request is incomplete")
    binding = normalize_reward_memory_provider_binding(provider_binding, corpus)
    base_packet: dict[str, Any] = {
        "ok": True,
        "schema_version": REWARD_MEMORY_RECALL_SCHEMA_VERSION,
        "corpus_id": corpus["corpus_id"],
        "surface_id": request["surface_id"],
        "mode": request["mode"],
        "query_kind": request["query_kind"],
        "query_evidence": list(request["query_evidence"]),
        "provider_id": binding["provider_id"],
        "result_count": 0,
        "results": [],
        "provider_item_count": 0,
        "filtered_item_count": 0,
        "filtered_reason_counts": {},
        "duplicate_item_count": 0,
        "empty_cause": None,
        "legacy_record_maintenance": {
            "status": "not_required",
            "record_count": 0,
            "record_refs": [],
            "allowed_actions": [],
            "migration_path": None,
            "retirement_path": None,
            "write_authority_required": True,
            "provider_write_performed": False,
            "exact_readback_verified": False,
        },
        "result_readback_verified": False,
        "provider_call_count": 0,
        "automatic_recall": False,
        "fail_open": True,
        "provider_failure_is_user_gate": False,
        "external_writes_performed": False,
        "raw_provider_payload_captured": False,
        "raw_content_persisted": False,
    }
    if guard.get("passed") is not True:
        return RewardMemoryRecallSession(
            public_packet=base_packet
            | {
                "status": "guard_blocked",
                "reason_codes": list(guard.get("reason_codes") or []),
            }
        )

    configured_provider = provider or build_context_provider(
        {
            "provider": binding["provider_id"],
            "provider_binary": binding.get("provider_binary"),
            "minimum_provider_version": binding.get("minimum_provider_version"),
            "actor_peer_id": binding.get("actor_peer_id"),
        }
    )
    results: list[RewardMemoryRecallItem] = []
    filtered_items: list[RewardMemoryFilteredRecallItem] = []
    seen: set[str] = set()
    seen_provider_refs: set[str] = set()
    filtered_reason_counts: Counter[str] = Counter()
    duplicate_item_count = 0
    provider_calls = 0
    provider_status = "completed"
    reason_code: str | None = None

    def filter_projection() -> dict[str, Any]:
        legacy_refs = sorted(
            {
                opaque_provider_ref(
                    provider=binding["provider_id"],
                    namespace=binding["namespace"],
                    resource_ref=item.memory_ref,
                )
                for item in filtered_items
                if item.reason_code == "legacy_contract_missing"
            }
        )
        return {
            "provider_item_count": len(seen_provider_refs),
            "filtered_item_count": len(filtered_items),
            "filtered_reason_counts": {
                key: filtered_reason_counts[key]
                for key in sorted(filtered_reason_counts)
            },
            "duplicate_item_count": duplicate_item_count,
            "legacy_record_maintenance": {
                "status": "owner_action_required" if legacy_refs else "not_required",
                "record_count": len(legacy_refs),
                "record_refs": legacy_refs,
                "allowed_actions": ["migrate", "retire"] if legacy_refs else [],
                "migration_path": (
                    "ingest_validated_replacement_then_retire_legacy"
                    if legacy_refs
                    else None
                ),
                "retirement_path": (
                    "declared_retirement_authority_write_then_exact_readback"
                    if legacy_refs
                    else None
                ),
                "write_authority_required": True,
                "provider_write_performed": False,
                "exact_readback_verified": False,
            },
        }

    for query in request["queries"]:
        provider_calls += 1
        try:
            retrieval = configured_provider.retrieve(
                namespace=binding["namespace"],
                scope_ref=binding["scope_ref"],
                query=query["query"],
                query_summary=query["query_summary"],
                # Leave room for scope/lifecycle rejection and duplicate
                # candidates before applying the caller's final record limit.
                max_results=min(24, request["limit"] * 3),
                timeout_seconds=float(binding["timeout_seconds"]),
                observed_at=request["observed_at"],
            )
        except Exception:  # noqa: BLE001 - provider execution is a fail-open boundary
            provider_status = "provider_unavailable"
            reason_code = "provider_execution_failed"
            break
        if retrieval.status != "completed":
            provider_status = "provider_unavailable"
            reason_code = retrieval.reason_code or "provider_unavailable"
            break
        if retrieval.items and not retrieval.read_performed:
            provider_status = "provider_unavailable"
            reason_code = "provider_result_readback_unverified"
            break
        for provider_item in retrieval.items:
            if provider_item.resource_ref in seen_provider_refs:
                duplicate_item_count += 1
                continue
            seen_provider_refs.add(provider_item.resource_ref)
            decision = _active_item_decision(
                provider_item,
                corpus,
                surface_id=request["surface_id"],
                observed_at=request["observed_at"],
            )
            active = decision.item
            if active is None:
                filtered = RewardMemoryFilteredRecallItem(
                    memory_ref=provider_item.resource_ref,
                    reason_code=str(decision.reason_code or "quality_filtered"),
                )
                filtered_items.append(filtered)
                filtered_reason_counts[filtered.reason_code] += 1
                continue
            if active.candidate_ref in seen:
                continue
            seen.add(active.candidate_ref)
            results.append(active)
            if len(results) >= request["limit"]:
                break
        if len(results) >= request["limit"]:
            break

    if provider_status == "provider_unavailable":
        return RewardMemoryRecallSession(
            public_packet=base_packet
            | filter_projection()
            | {
                "status": provider_status,
                "reason_code": reason_code,
                "provider_call_count": provider_calls,
                "setup_hints": binding["setup_hints"],
            }
        )
    status = "completed" if results else "empty"
    empty_cause = None
    if not results:
        empty_cause = (
            "provider_returned_no_items"
            if not seen_provider_refs
            else "all_provider_items_filtered"
        )
    expose_summary = corpus["privacy"]["visibility"] == "public_safe"
    public_results = [
        {
            "memory_ref": opaque_provider_ref(
                provider=binding["provider_id"],
                namespace=binding["namespace"],
                resource_ref=item.memory_ref,
            ),
            "candidate_ref": item.candidate_ref,
            "target_class": item.target_class,
            "content_summary": item.content_summary if expose_summary else None,
            "experience_quality": (
                procedural_experience_quality(
                    target_class=item.target_class,
                    experience=item.experience,
                )
            ),
            "content_exposed": expose_summary,
        }
        for item in results
    ]
    return RewardMemoryRecallSession(
        public_packet=base_packet
        | filter_projection()
        | {
            "status": status,
            "reason_code": None if results else "no_active_exact_corpus_results",
            "empty_cause": empty_cause,
            "result_count": len(results),
            "results": public_results,
            "result_readback_verified": bool(results),
            "provider_call_count": provider_calls,
        },
        items=tuple(results),
        filtered_items=tuple(filtered_items),
    )


def _receipt(
    session: RewardMemoryRecallSession,
    *,
    application_id: str,
    artifact_ref: str | None,
    outcome: str,
    memory_refs: Sequence[str],
    reasoning_summary: str,
    current_artifact_verified: bool,
) -> dict[str, Any]:
    if outcome not in APPLICATION_OUTCOMES:
        raise ValueError("application outcome is invalid")
    return {
        "schema_version": REWARD_MEMORY_APPLICATION_RECEIPT_SCHEMA_VERSION,
        "application_id": _token(application_id, "application_id"),
        "artifact_ref": _optional_token(artifact_ref, "artifact_ref"),
        "corpus_id": session.public_packet["corpus_id"],
        "surface_id": session.public_packet["surface_id"],
        "mode": session.public_packet["mode"],
        "query_kind": str(session.public_packet.get("query_kind") or "business_recall"),
        "query_evidence": list(session.public_packet.get("query_evidence") or []),
        "outcome": outcome,
        "memory_ref_digests": sorted(
            {
                hashlib.sha256(str(ref).encode("utf-8")).hexdigest()[:16]
                for ref in memory_refs
            }
        ),
        "reasoning_summary": _compact(
            reasoning_summary, "reasoning_summary", limit=500
        ),
        "current_artifact_verified": current_artifact_verified,
        "result_readback_verified": bool(
            session.public_packet.get("result_readback_verified")
        ),
        "provider_call_count": int(
            session.public_packet.get("provider_call_count") or 0
        ),
        "model_reasoning_preserved": True,
        "grants_new_action_authority": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
    }


def apply_reward_memory_recall(
    base_output: Any,
    session: RewardMemoryRecallSession,
    *,
    application_id: str,
    artifact_ref: str | None = None,
    apply_memory: RewardMemoryApplier | None = None,
) -> dict[str, Any]:
    """Run caller-owned reasoning and fail open to the original output."""

    status = session.public_packet.get("status")
    if status != "completed" or not session.items:
        receipt = _receipt(
            session,
            application_id=application_id,
            artifact_ref=artifact_ref,
            outcome="not_available",
            memory_refs=[],
            reasoning_summary=f"recall_{status or 'invalid'}",
            current_artifact_verified=False,
        )
        return {
            "ok": True,
            "schema_version": REWARD_MEMORY_APPLICATION_SCHEMA_VERSION,
            "status": "not_available",
            "output": base_output,
            "receipt": receipt,
            "fail_open_preserved_base": True,
        }
    if apply_memory is None:
        receipt = _receipt(
            session,
            application_id=application_id,
            artifact_ref=artifact_ref,
            outcome="available_not_applied",
            memory_refs=[],
            reasoning_summary="model_application_callback_not_supplied",
            current_artifact_verified=False,
        )
        return {
            "ok": True,
            "schema_version": REWARD_MEMORY_APPLICATION_SCHEMA_VERSION,
            "status": "available_not_applied",
            "output": base_output,
            "receipt": receipt,
            "fail_open_preserved_base": True,
        }

    try:
        decision = apply_memory(base_output, session.items)
        if not isinstance(decision, Mapping):
            raise ValueError("application callback must return an object")
        if "output" not in decision:
            raise ValueError("application callback must return output")
        outcome = str(decision.get("outcome") or "").strip()
        if outcome not in {"applied", "ignored", "refuted"}:
            raise ValueError("application callback outcome is invalid")
        output = decision.get("output")
        refs = decision.get("memory_refs") or []
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)):
            raise ValueError("application memory_refs must be a list")
        memory_refs = [str(ref).strip() for ref in refs if str(ref).strip()]
        available_refs = {item.memory_ref for item in session.items}
        if any(ref not in available_refs for ref in memory_refs):
            raise ValueError("application memory_refs must come from this recall")
        current_verified = _boolean(decision, "current_artifact_verified")
        if outcome == "applied" and (not memory_refs or not current_verified):
            raise ValueError(
                "applied memory requires attribution and current artifact verification"
            )
        if outcome != "applied" and output != base_output:
            raise ValueError("ignored or refuted memory must preserve the base output")
        reasoning_summary = _compact(
            decision.get("reasoning_summary"), "reasoning_summary", limit=500
        )
        receipt = _receipt(
            session,
            application_id=application_id,
            artifact_ref=artifact_ref,
            outcome=outcome,
            memory_refs=memory_refs,
            reasoning_summary=reasoning_summary,
            current_artifact_verified=current_verified,
        )
        return {
            "ok": True,
            "schema_version": REWARD_MEMORY_APPLICATION_SCHEMA_VERSION,
            "status": outcome,
            "output": output,
            "receipt": receipt,
            "fail_open_preserved_base": output == base_output,
        }
    except Exception:  # noqa: BLE001 - model/caller application is fail-open
        receipt = _receipt(
            session,
            application_id=application_id,
            artifact_ref=artifact_ref,
            outcome="failed",
            memory_refs=[],
            reasoning_summary="application_boundary_failed",
            current_artifact_verified=False,
        )
        return {
            "ok": True,
            "schema_version": REWARD_MEMORY_APPLICATION_SCHEMA_VERSION,
            "status": "failed",
            "output": base_output,
            "receipt": receipt,
            "fail_open_preserved_base": True,
        }
