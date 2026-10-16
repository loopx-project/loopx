from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...history import load_registry
from ...materials import find_registry_goal, goal_repo
from ..context_providers.base import ContextProvider
from .experiment import (
    resolve_reward_memory_experiment,
    resolve_reward_memory_surface_config,
)
from .experience_quality import normalize_procedural_experience
from .runtime_hooks import run_reward_memory_automatic_ingest_hook
from .scoped_feedback import (
    SCOPED_FEEDBACK_ADAPTER,
    SCOPED_FEEDBACK_EVENT_SCHEMA_VERSION,
    ingest_scoped_feedback_reward_memory_event,
)


TURN_REWARD_MEMORY_REFLECTION_SCHEMA_VERSION = "turn_reward_memory_reflection_v1"
LEGACY_TURN_REWARD_MEMORY_REFLECTION_SCHEMA_VERSION = "turn_reward_memory_reflection_v0"
TURN_REWARD_MEMORY_INGEST_SCHEMA_VERSION = "turn_reward_memory_ingest_v0"
TURN_REWARD_MEMORY_RECONCILIATION_SCHEMA_VERSION = (
    "turn_reward_memory_reconciliation_v0"
)
TURN_REWARD_MEMORY_SIDECAR_SCHEMA_VERSION = "turn_reward_memory_sidecar_v0"
LEGACY_PENDING_MIGRATION_SCHEMA_VERSION = (
    "turn_reward_memory_legacy_pending_migration_v0"
)
_SAFE_PATH_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")
_PENDING_PROVIDER_STATUSES = {
    "committed_pending",
    "provider_unavailable",
    "readback_unverified",
    "recall_unverified",
}
_OUTCOME_SOURCE_KINDS = {
    "research": "research_review",
    "simulation": "simulation_review",
    "real": "real_outcome_review",
    "engineering": "engineering_review",
}
_LEGACY_REFLECTION_FIELDS = {
    "schema_version",
    "status",
    "surface_id",
    "outcome_kind",
    "content_summary",
    "reasoning_summary",
    "confidence",
    "evidence_refs",
}
_REFLECTION_FIELDS = _LEGACY_REFLECTION_FIELDS | {"experience"}
_MAX_REFLECTION_BYTES = 16 * 1024


def _base(
    *,
    goal_id: str,
    agent_id: str,
    status: str,
    reason_code: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": TURN_REWARD_MEMORY_INGEST_SCHEMA_VERSION,
        "status": status,
        "reason_code": reason_code,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "automatic_ingest": False,
        "provider_sync_count": 0,
        "exact_readback_verified": False,
        "deduplicated": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
        "host_wiring": "codex_cli_turn_post_settlement",
        "fail_open": True,
        "provider_failure_is_user_gate": False,
        "grants_new_action_authority": False,
    }


def _identity_token(value: str, *, fallback: str) -> str:
    readable = _SAFE_PATH_TOKEN.sub("-", str(value or "")).strip("-") or fallback
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]
    return f"{readable[:60]}-{digest}"


def _goal_repo(registry_path: Path, goal_id: str) -> Path:
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    resolved = goal_repo(goal) if goal else None
    if resolved is None:
        raise ValueError(f"goal `{goal_id}` repository is unavailable")
    repo = Path(resolved)
    if not repo.is_dir():
        raise ValueError(f"goal `{goal_id}` repository is unavailable")
    return repo


def turn_outcome_ingest_sidecar_path(
    repo: Path,
    *,
    goal_id: str,
    agent_id: str,
    source_event_id: str,
) -> Path:
    """Return a collision-safe private retry journal for one outcome event."""

    return (
        repo
        / ".local"
        / "loopx"
        / "reward-memory-outcomes"
        / _identity_token(goal_id, fallback="goal")
        / _identity_token(agent_id, fallback="agent")
        / f"{_identity_token(source_event_id, fallback='event')}.json"
    )


def _load_sidecar(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("reward memory outcome sidecar is unreadable") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != TURN_REWARD_MEMORY_SIDECAR_SCHEMA_VERSION
    ):
        raise ValueError("reward memory outcome sidecar is invalid")
    return payload


def _write_sidecar(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _replayed_completed_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    receipt = value.get("public_receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("completed reward memory sidecar has no public receipt")
    return dict(receipt) | {
        "status": "activated",
        "provider_sync_count": 0,
        "exact_readback_verified": True,
        "deduplicated": True,
        "external_writes_performed": False,
        "reconciliation_state": "completed",
        "sidecar_receipt_reused": True,
    }


def _terminalize_legacy_pending_sidecar(
    path: Path,
    value: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    migrated_at: str,
) -> dict[str, Any] | None:
    reflection = value.get("reflection")
    if not isinstance(reflection, Mapping):
        return None
    normalized = _reflection(
        json.dumps(
            reflection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if normalized is None or normalized.get("status") != "legacy_no_write":
        return None
    migration_receipt = {
        "schema_version": LEGACY_PENDING_MIGRATION_SCHEMA_VERSION,
        "status": "terminal_rejected",
        "reason_code": "legacy_reflection_requires_transferable_experience",
        "provider_write_state": "unknown_may_have_committed",
        "provider_cleanup_performed": False,
        "migrated_at": migrated_at,
    }
    public_receipt = _base(
        goal_id=goal_id,
        agent_id=agent_id,
        status="no_eligible_evidence",
        reason_code="legacy_reflection_requires_transferable_experience",
    ) | {
        "automatic_ingest": True,
        "source_event_id": str(value.get("source_event_id") or "") or None,
        "surface_id": str(value.get("surface_id") or "") or None,
        "reconciliation_state": "rejected",
        "legacy_pending_migration": migration_receipt,
    }
    _write_sidecar(
        path,
        dict(value)
        | {
            "status": "rejected",
            "public_receipt": public_receipt,
            "legacy_pending_migration": migration_receipt,
        },
    )
    return public_receipt


def _reflection(value: object) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    # The v1 reflection embeds a bounded procedural-experience contract. Its
    # independently bounded fields can legitimately exceed the legacy 2,400
    # character envelope, especially for multi-byte text. Keep a whole-packet
    # denial-of-service guard, but measure the serialized transport in bytes
    # and leave semantic compaction to the field-level validators below.
    if len(text.encode("utf-8")) > _MAX_REFLECTION_BYTES:
        raise ValueError("reward memory reflection exceeds its bounded contract")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("reward memory reflection must be JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("reward memory reflection must decode to an object")
    schema_version = raw.get("schema_version")
    allowed_fields = (
        _LEGACY_REFLECTION_FIELDS
        if schema_version == LEGACY_TURN_REWARD_MEMORY_REFLECTION_SCHEMA_VERSION
        else _REFLECTION_FIELDS
    )
    if set(raw) - allowed_fields:
        raise ValueError("reward memory reflection contains unsupported fields")
    if schema_version not in {
        LEGACY_TURN_REWARD_MEMORY_REFLECTION_SCHEMA_VERSION,
        TURN_REWARD_MEMORY_REFLECTION_SCHEMA_VERSION,
    }:
        raise ValueError("reward memory reflection schema is unsupported")
    status = str(raw.get("status") or "")
    if status == "no_evidence":
        return {"schema_version": raw["schema_version"], "status": status}
    if status != "eligible":
        raise ValueError("reward memory reflection status is unsupported")
    if schema_version == LEGACY_TURN_REWARD_MEMORY_REFLECTION_SCHEMA_VERSION:
        return {
            "schema_version": schema_version,
            "status": "legacy_no_write",
        }
    surface_id = public_safe_compact_text(raw.get("surface_id"), limit=160)
    content_summary = public_safe_compact_text(raw.get("content_summary"), limit=500)
    reasoning_summary = public_safe_compact_text(
        raw.get("reasoning_summary"), limit=500
    )
    outcome_kind = str(raw.get("outcome_kind") or "")
    confidence = str(raw.get("confidence") or "")
    evidence_refs = raw.get("evidence_refs")
    if (
        not surface_id
        or not content_summary
        or not reasoning_summary
        or outcome_kind not in _OUTCOME_SOURCE_KINDS
        or confidence not in {"low", "medium", "high"}
        or not isinstance(evidence_refs, list)
        or not 1 <= len(evidence_refs) <= 5
    ):
        raise ValueError("eligible reward memory reflection is incomplete")
    compact_refs: list[str] = []
    for value in evidence_refs:
        ref = public_safe_compact_text(value, limit=180)
        if not ref or any(character.isspace() for character in ref):
            raise ValueError("reward memory evidence refs must be opaque tokens")
        compact_refs.append(ref)
    if len(set(compact_refs)) != len(compact_refs):
        raise ValueError("reward memory evidence refs must be unique")
    experience = normalize_procedural_experience(raw.get("experience"))
    if experience["evidence_refs"] != compact_refs:
        raise ValueError(
            "experience.evidence_refs must match the validated reflection evidence_refs"
        )
    return {
        "schema_version": raw["schema_version"],
        "status": status,
        "surface_id": surface_id,
        "outcome_kind": outcome_kind,
        "content_summary": content_summary,
        "reasoning_summary": reasoning_summary,
        "confidence": confidence,
        "evidence_refs": compact_refs,
        "experience": experience,
    }


def _reflection_digest(value: object) -> str:
    text = str(value or "").strip()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("reward memory reflection must be JSON") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("reward memory reflection must decode to an object")
    canonical = json.dumps(
        raw,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_reflection_evidence(
    *,
    reflection_json: object,
    reflection: Mapping[str, Any],
    settlement_evidence: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if (
        not isinstance(settlement_evidence, Mapping)
        or settlement_evidence.get("schema_version")
        != "turn_post_settlement_evidence_v0"
    ):
        return None
    task_validation = settlement_evidence.get("task_validation")
    writeback = settlement_evidence.get("writeback")
    quota_spend = settlement_evidence.get("quota_spend")
    if (
        not isinstance(task_validation, Mapping)
        or task_validation.get("ok") is not True
        or not isinstance(writeback, Mapping)
        or writeback.get("ok") is not True
        or writeback.get("appended") is not True
        or not isinstance(quota_spend, Mapping)
        or quota_spend.get("ok") is not True
        or quota_spend.get("appended") is not True
    ):
        return None
    validation = task_validation.get("reward_memory_reflection_validation")
    if not isinstance(validation, Mapping):
        return None
    if (
        validation.get("schema_version") != "reward_memory_reflection_validation_v0"
        or validation.get("status") != "validated"
        or validation.get("reflection_digest") != _reflection_digest(reflection_json)
        or validation.get("evidence_refs") != reflection.get("evidence_refs")
    ):
        return None
    return {
        "schema_version": "reward_memory_reflection_validation_v0",
        "status": "validated",
        "reflection_digest": str(validation["reflection_digest"]),
        "evidence_refs": list(validation["evidence_refs"]),
        "validator_kind": str(task_validation.get("validator_kind") or "unknown"),
    }


def _event_scope(
    config: Mapping[str, Any],
    *,
    surface_id: str,
    agent_id: str,
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    route = resolve_reward_memory_surface_config(
        config,
        surface_id,
        adapter=SCOPED_FEEDBACK_ADAPTER,
    )
    corpus = route["corpus"]
    scope = corpus["scope"]
    peer_ref = str(scope.get("peer_ref") or "") or None
    if peer_ref is not None and peer_ref != f"agent:{agent_id}":
        raise ValueError("reward memory ingest surface belongs to another Agent")
    return {
        "workspace_ref": scope["workspace_ref"],
        "project_ref": scope["project_ref"],
        "user_ref": scope.get("user_ref"),
        "peer_ref": peer_ref,
        "session_ref": scope.get("session_ref"),
    }, route


def run_configured_turn_outcome_ingest(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    host_result: Mapping[str, Any],
    settlement_evidence: Mapping[str, Any] | None = None,
    observed_at: str | None = None,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Ingest one independently validated, durably written Turn reflection."""

    experiment_status, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    if config is None:
        return _base(
            goal_id=goal_id,
            agent_id=agent_id,
            status=str(experiment_status.get("status") or "not_available"),
            reason_code=experiment_status.get("reason_code"),
        )
    if config["automation"]["automatic_ingest"] is not True:
        return _base(
            goal_id=goal_id,
            agent_id=agent_id,
            status="explicitly_disabled",
            reason_code="automatic_ingest_explicitly_disabled",
        )
    reflection = _reflection(host_result.get("reward_memory_reflection_json"))
    if reflection is None or reflection["status"] in {
        "no_evidence",
        "legacy_no_write",
    }:
        legacy = reflection is not None and reflection["status"] == "legacy_no_write"
        return _base(
            goal_id=goal_id,
            agent_id=agent_id,
            status="no_eligible_evidence",
            reason_code=(
                "legacy_reflection_requires_transferable_experience"
                if legacy
                else "validated_turn_declared_no_reward_evidence"
            ),
        ) | {"automatic_ingest": True}
    surface_id = str(reflection["surface_id"])
    scope, route = _event_scope(
        config,
        surface_id=surface_id,
        agent_id=agent_id,
    )
    policy_id = str(route["standing_policy"]["policy_id"])
    evidence_digest = hashlib.sha256(
        json.dumps(
            reflection["evidence_refs"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    source_event_id = hashlib.sha256(
        (
            f"{goal_id}\n{agent_id}\n{turn_key}\n{surface_id}\n{policy_id}\n"
            f"{evidence_digest}"
        ).encode("utf-8")
    ).hexdigest()[:20]
    source_ref = f"turn-reflection:{source_event_id}:{evidence_digest}"
    event = {
        "schema_version": SCOPED_FEEDBACK_EVENT_SCHEMA_VERSION,
        "feedback_ref": source_ref,
        **scope,
        "surface_id": surface_id,
        "revision_ref": turn_key,
        "target_class": str(route["corpus"]["class_id"]),
        "content_summary": reflection["content_summary"],
        "experience": reflection["experience"],
        "source": {
            "source_kind": _OUTCOME_SOURCE_KINDS[reflection["outcome_kind"]],
            "source_ref": source_ref,
            "actor_ref": f"agent:{agent_id}",
            "actor_role": "validated_goal_agent",
        },
        "reasoning": {
            "summary": reflection["reasoning_summary"],
            "confidence": reflection["confidence"],
        },
        "guard_context": {
            "source_freshness": "current",
            "conflict_state": "clear",
            "current_artifact_verified": True,
        },
        "requested_action_scopes": [],
        "raw_content_captured": False,
    }
    effective_observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    sidecar_path = turn_outcome_ingest_sidecar_path(
        _goal_repo(registry_path, goal_id),
        goal_id=goal_id,
        agent_id=agent_id,
        source_event_id=source_event_id,
    )
    previous = _load_sidecar(sidecar_path)
    if previous is not None:
        if any(
            previous.get(key) != expected
            for key, expected in {
                "goal_id": goal_id,
                "agent_id": agent_id,
                "source_event_id": source_event_id,
                "turn_key": turn_key,
                "binding_revision": experiment_status.get("binding_revision"),
            }.items()
        ):
            return _base(
                goal_id=goal_id,
                agent_id=agent_id,
                status="runtime_state_invalid",
                reason_code="outcome_sidecar_identity_mismatch",
            ) | {"automatic_ingest": True}
        stored_reflection = previous.get("reflection")
        if stored_reflection != reflection:
            return _base(
                goal_id=goal_id,
                agent_id=agent_id,
                status="runtime_state_invalid",
                reason_code="outcome_sidecar_payload_mismatch",
            ) | {"automatic_ingest": True}
        if previous.get("status") == "completed":
            return _replayed_completed_receipt(previous)
        if previous.get("status") != "pending":
            receipt = previous.get("public_receipt")
            return (
                dict(receipt)
                if isinstance(receipt, Mapping)
                else _base(
                    goal_id=goal_id,
                    agent_id=agent_id,
                    status="runtime_state_invalid",
                    reason_code="outcome_sidecar_terminal_receipt_missing",
                )
            ) | {
                "reconciliation_state": str(previous.get("status") or "rejected"),
                "sidecar_receipt_reused": True,
                "provider_sync_count": 0,
                "external_writes_performed": False,
            }
        effective_observed_at = str(previous.get("observed_at") or "").strip()
        if not effective_observed_at:
            return _base(
                goal_id=goal_id,
                agent_id=agent_id,
                status="runtime_state_invalid",
                reason_code="outcome_sidecar_observation_missing",
            ) | {"automatic_ingest": True}
    reflection_validation = _validated_reflection_evidence(
        reflection_json=host_result.get("reward_memory_reflection_json"),
        reflection=reflection,
        settlement_evidence=settlement_evidence,
    )
    if reflection_validation is None and previous is not None:
        stored_validation = previous.get("reflection_validation")
        if isinstance(stored_validation, Mapping):
            reflection_validation = dict(stored_validation)
    if reflection_validation is None:
        return _base(
            goal_id=goal_id,
            agent_id=agent_id,
            status="evidence_validation_required",
            reason_code="reflection_not_bound_to_independent_validation",
        ) | {
            "automatic_ingest": True,
            "surface_id": surface_id,
            "source_event_id": source_event_id,
            "candidate_state": "awaiting_evidence_validation",
        }
    settlement_validation = (
        settlement_evidence
        if isinstance(settlement_evidence, Mapping)
        else (previous or {}).get("settlement_validation")
    )
    settlement_validation = (
        settlement_validation if isinstance(settlement_validation, Mapping) else {}
    )
    attempt_count = int((previous or {}).get("attempt_count") or 0) + 1
    pending_sidecar = {
        "schema_version": TURN_REWARD_MEMORY_SIDECAR_SCHEMA_VERSION,
        "status": "pending",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "turn_key": turn_key,
        "source_event_id": source_event_id,
        **({"binding_revision": experiment_status["binding_revision"]} if experiment_status.get("binding_revision") else {}),
        "surface_id": surface_id,
        "reflection": reflection,
        "reflection_validation": reflection_validation,
        "settlement_validation": {
            "writeback": dict(settlement_validation.get("writeback") or {}),
            "quota_spend": dict(settlement_validation.get("quota_spend") or {}),
        },
        "observed_at": effective_observed_at,
        "attempt_count": attempt_count,
        "raw_content_captured": False,
    }
    try:
        _write_sidecar(sidecar_path, pending_sidecar)
    except OSError:
        return _base(
            goal_id=goal_id,
            agent_id=agent_id,
            status="runtime_state_unavailable",
            reason_code="outcome_sidecar_write_failed_before_provider",
        ) | {"automatic_ingest": True}
    result = run_reward_memory_automatic_ingest_hook(
        config,
        surface_id=surface_id,
        adapter=SCOPED_FEEDBACK_ADAPTER,
        event=event,
        observed_at=effective_observed_at,
        ingest_event=ingest_scoped_feedback_reward_memory_event,
        execute=True,
        provider=provider,
    )
    telemetry = result.get("telemetry")
    telemetry = telemetry if isinstance(telemetry, Mapping) else {}
    receipt = result.get("receipt")
    receipt = receipt if isinstance(receipt, Mapping) else {}
    reason_codes = receipt.get("reason_codes")
    guard = receipt.get("guard")
    guard = guard if isinstance(guard, Mapping) else {}
    guard_reason_codes = guard.get("reason_codes")
    destination_recall = receipt.get("destination_recall")
    destination_recall = (
        destination_recall if isinstance(destination_recall, Mapping) else {}
    )
    public_receipt = _base(
        goal_id=goal_id,
        agent_id=agent_id,
        status=str(result.get("status") or "not_available"),
        reason_code=(
            str(reason_codes[0])
            if isinstance(reason_codes, list) and reason_codes
            else str(guard_reason_codes[0])
            if isinstance(guard_reason_codes, list) and guard_reason_codes
            else result.get("reason_code")
        ),
    ) | {
        "automatic_ingest": True,
        "surface_id": surface_id,
        "source_event_id": source_event_id,
        "provider_sync_count": int(telemetry.get("provider_sync_count") or 0),
        "exact_readback_verified": bool(telemetry.get("exact_readback_verified")),
        "deduplicated": bool(telemetry.get("deduplicated")),
        "external_writes_performed": bool(result.get("external_writes_performed")),
        "experience_quality": dict(receipt.get("experience_quality") or {}),
        "destination_recall": {
            "required": bool(destination_recall.get("required")),
            "verified": bool(destination_recall.get("verified")),
            "query_kind": destination_recall.get("query_kind"),
            "experience_digest": destination_recall.get("experience_digest"),
        },
    }
    reconciliation_state = (
        "completed"
        if public_receipt["status"] == "activated"
        and public_receipt["exact_readback_verified"] is True
        else "pending"
        if public_receipt["status"] in _PENDING_PROVIDER_STATUSES
        else "rejected"
    )
    public_receipt["reconciliation_state"] = reconciliation_state
    final_sidecar = pending_sidecar | {
        "status": reconciliation_state,
        "public_receipt": public_receipt,
    }
    try:
        _write_sidecar(sidecar_path, final_sidecar)
    except OSError:
        public_receipt["reconciliation_state"] = "state_write_failed"
        public_receipt["reason_code"] = (
            public_receipt.get("reason_code")
            or "outcome_sidecar_write_failed_after_provider"
        )
    return public_receipt


def reconcile_pending_turn_outcome_ingests(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    observed_at: str | None = None,
    provider: ContextProvider | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    """Retry ambiguous writes with their original deterministic event identity."""

    if not 1 <= limit <= 3:
        raise ValueError("reward memory reconciliation limit must be between 1 and 3")
    experiment_status, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    base = {
        "ok": True,
        "schema_version": TURN_REWARD_MEMORY_RECONCILIATION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "status": "not_available",
        "pending_count": 0,
        "attempted_count": 0,
        "completed_count": 0,
        "provider_sync_count": 0,
        "external_writes_performed": False,
        "fail_open": True,
    }
    if config is None:
        return base | {
            "status": str(experiment_status.get("status") or "not_available"),
            "reason_code": experiment_status.get("reason_code"),
        }
    if config["automation"]["automatic_ingest"] is not True:
        return base | {
            "status": "explicitly_disabled",
            "reason_code": "automatic_ingest_explicitly_disabled",
        }
    directory = turn_outcome_ingest_sidecar_path(
        _goal_repo(registry_path, goal_id),
        goal_id=goal_id,
        agent_id=agent_id,
        source_event_id="placeholder",
    ).parent
    if not directory.is_dir():
        return base | {"status": "empty"}
    pending: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        value = _load_sidecar(path)
        if (
            value is not None
            and value.get("goal_id") == goal_id
            and value.get("agent_id") == agent_id
            and value.get("status") == "pending"
        ):
            pending.append((path, value))
    receipts: list[dict[str, Any]] = []
    effective_observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    for path, value in pending[:limit]:
        if value.get("binding_revision") != experiment_status.get("binding_revision"):
            # A pending write belongs to its original route. Reconfiguration
            # cannot authorize replay into a replacement provider/corpus.
            receipts.append(_base(
                goal_id=goal_id, agent_id=agent_id, status="runtime_state_invalid",
                reason_code="outcome_sidecar_identity_mismatch",
            ))
            continue
        legacy_receipt = _terminalize_legacy_pending_sidecar(
            path,
            value,
            goal_id=goal_id,
            agent_id=agent_id,
            migrated_at=effective_observed_at,
        )
        if legacy_receipt is not None:
            receipts.append(legacy_receipt)
            continue
        reflection = value.get("reflection")
        if not isinstance(reflection, Mapping):
            continue
        receipt = run_configured_turn_outcome_ingest(
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=str(value.get("turn_key") or ""),
            host_result={
                "reward_memory_reflection_json": json.dumps(
                    reflection,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            },
            observed_at=str(value.get("observed_at") or observed_at or "") or None,
            settlement_evidence={
                "schema_version": "turn_post_settlement_evidence_v0",
                "task_validation": {
                    "ok": True,
                    "validator_kind": str(
                        (value.get("reflection_validation") or {}).get("validator_kind")
                        or "reconciliation"
                    ),
                    "reward_memory_reflection_validation": dict(
                        value.get("reflection_validation") or {}
                    ),
                },
                "writeback": dict(
                    (value.get("settlement_validation") or {}).get("writeback") or {}
                ),
                "quota_spend": dict(
                    (value.get("settlement_validation") or {}).get("quota_spend") or {}
                ),
            },
            provider=provider,
        )
        receipts.append(receipt)
    return base | {
        "status": "completed" if receipts else "empty",
        "pending_count": len(pending),
        "attempted_count": len(receipts),
        "completed_count": sum(
            receipt.get("reconciliation_state") == "completed" for receipt in receipts
        ),
        "provider_sync_count": sum(
            int(receipt.get("provider_sync_count") or 0) for receipt in receipts
        ),
        "external_writes_performed": any(
            receipt.get("external_writes_performed") is True for receipt in receipts
        ),
        "receipts": receipts,
    }


def reconcile_pending_turn_outcome_ingests_fail_open(
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return reconcile_pending_turn_outcome_ingests(**kwargs)
    except (OSError, RuntimeError, TypeError, ValueError):
        return {
            "ok": True,
            "schema_version": TURN_REWARD_MEMORY_RECONCILIATION_SCHEMA_VERSION,
            "goal_id": str(kwargs.get("goal_id") or ""),
            "agent_id": str(kwargs.get("agent_id") or ""),
            "status": "runtime_unavailable",
            "reason_code": "automatic_ingest_reconciliation_failed",
            "pending_count": 0,
            "attempted_count": 0,
            "completed_count": 0,
            "provider_sync_count": 0,
            "external_writes_performed": False,
            "fail_open": True,
        }


def run_configured_turn_outcome_ingest_fail_open(**kwargs: Any) -> dict[str, Any]:
    try:
        return run_configured_turn_outcome_ingest(**kwargs)
    except (OSError, RuntimeError, TypeError, ValueError):
        return _base(
            goal_id=str(kwargs.get("goal_id") or ""),
            agent_id=str(kwargs.get("agent_id") or ""),
            status="runtime_unavailable",
            reason_code="automatic_ingest_runtime_failed",
        )


__all__ = [
    "TURN_REWARD_MEMORY_INGEST_SCHEMA_VERSION",
    "TURN_REWARD_MEMORY_RECONCILIATION_SCHEMA_VERSION",
    "TURN_REWARD_MEMORY_REFLECTION_SCHEMA_VERSION",
    "TURN_REWARD_MEMORY_SIDECAR_SCHEMA_VERSION",
    "reconcile_pending_turn_outcome_ingests",
    "reconcile_pending_turn_outcome_ingests_fail_open",
    "run_configured_turn_outcome_ingest",
    "run_configured_turn_outcome_ingest_fail_open",
    "turn_outcome_ingest_sidecar_path",
]
