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

from ...history import load_registry
from ...materials import find_registry_goal, goal_repo
from ..context_providers.base import ContextProvider
from ..reward_memory.experiment import (
    resolve_reward_memory_experiment,
    resolve_reward_memory_surface_config,
)
from ..reward_memory.read_authority import build_reward_memory_surface_read_authority_checkpoints
from .core import (
    AGENT_TURN_RECALL_SCHEMA_VERSION,
    AGENT_TURN_RECALL_SURFACE_ID,
    build_agent_turn_recall_preview,
    build_agent_turn_situation,
    run_agent_turn_recall,
)


AGENT_TURN_RECALL_RECEIPT_SCHEMA_VERSION = "agent_turn_recall_receipt_v1"
_SAFE_PATH_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_token(value: str, *, fallback: str) -> str:
    readable = _SAFE_PATH_TOKEN.sub("-", value).strip("-") or fallback
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
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


def agent_turn_recall_receipt_path(
    repo: Path,
    *,
    goal_id: str,
    agent_id: str,
) -> Path:
    """Return a Goal-and-Agent-scoped receipt path.

    Agent names are only Goal-local identities. Including both identity
    dimensions prevents two Goals that use the same local Agent name from
    sharing a recall receipt when they happen to use one repository.
    """

    goal_token = _safe_token(goal_id, fallback="goal")
    agent_token = _safe_token(agent_id, fallback="agent")
    return (
        repo
        / ".local"
        / "loopx"
        / "agent-turn-recall"
        / goal_token
        / f"{agent_token}.json"
    )


def load_agent_turn_recall_receipt(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != AGENT_TURN_RECALL_RECEIPT_SCHEMA_VERSION:
        return None
    return payload


def write_agent_turn_recall_receipt(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
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


def reward_memory_turn_identity_scope(
    config: Mapping[str, Any],
) -> dict[str, str | None]:
    route = resolve_reward_memory_surface_config(
        config,
        AGENT_TURN_RECALL_SURFACE_ID,
    )
    routes = route.get("recall_corpora")
    if not isinstance(routes, list) or not routes:
        raise ValueError("agent turn recall surface has no corpus")
    identity: dict[str, str | None] | None = None
    for item in routes:
        corpus = item.get("corpus") if isinstance(item, Mapping) else None
        scope = corpus.get("scope") if isinstance(corpus, Mapping) else None
        if not isinstance(scope, Mapping):
            raise ValueError("agent turn recall corpus scope is invalid")
        current = {
            key: str(scope.get(key) or "").strip() or None
            for key in (
                "workspace_ref",
                "project_ref",
                "user_ref",
                "peer_ref",
                "session_ref",
            )
        }
        if identity is not None and current != identity:
            raise ValueError("agent turn recall corpora must share one identity scope")
        identity = current
    assert identity is not None
    return identity


def resolve_reward_memory_turn_session_ref(
    identity: Mapping[str, str | None], requested: str | None
) -> str | None:
    configured = str(identity.get("session_ref") or "").strip() or None
    requested = str(requested or "").strip() or None
    if configured and requested and configured != requested:
        raise ValueError("session_ref does not match the configured corpus scope")
    return configured or requested


def reward_memory_turn_read_authority_checkpoints(
    config: Mapping[str, Any], goal_id: str
) -> dict[str, dict[str, Any]]:
    return build_reward_memory_surface_read_authority_checkpoints(
        config, AGENT_TURN_RECALL_SURFACE_ID,
        verified=True, source_ref=f"registry:{goal_id}:reward-memory",
    )


def deduplicated_agent_turn_recall_payload(
    receipt: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any] | None:
    context = receipt.get("context")
    if not isinstance(context, Mapping):
        return None
    if receipt.get("goal_id") != goal_id or receipt.get("agent_id") != agent_id:
        return None
    return {
        "ok": True,
        "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
        "status": "deduplicated",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "surface_id": AGENT_TURN_RECALL_SURFACE_ID,
        "turn_recall_id": receipt.get("turn_recall_id"),
        "situation_fingerprint": receipt.get("situation_fingerprint"),
        "context": dict(context),
        "provider_call_count": 0,
        "same_turn_receipt_reused": True,
        "source_status": receipt.get("source_status"),
        "fail_open": True,
        "grants_new_action_authority": False,
        "quota_spend_performed": False,
        "external_writes_performed": False,
        "suppress_external_sinks": True,
    }


def _unavailable_payload(
    *,
    goal_id: str,
    agent_id: str,
    status: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
        "status": str(status.get("status") or "not_available"),
        "reason_code": status.get("reason_code"),
        "goal_id": goal_id,
        "agent_id": agent_id,
        "surface_id": AGENT_TURN_RECALL_SURFACE_ID,
        "experiment": dict(status),
        "context": None,
        "provider_call_count": 0,
        "automatic_recall": bool(status.get("automatic_recall")),
        "host_wiring": "agent_turn_admission",
        "fail_open": True,
        "provider_failure_is_user_gate": False,
        "grants_new_action_authority": False,
        "quota_spend_performed": False,
        "external_writes_performed": False,
        "suppress_external_sinks": True,
    }


def run_configured_agent_turn_recall(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    quota_decision: Mapping[str, Any],
    turn_instance_id: str,
    execute: bool,
    session_ref: str | None = None,
    force_refresh: bool = False,
    observed_at: str | None = None,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Run the configured automatic recall at one real Turn admission boundary."""

    experiment_status, config = resolve_reward_memory_experiment(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    if config is None:
        return _unavailable_payload(
            goal_id=goal_id,
            agent_id=agent_id,
            status=experiment_status,
        )
    reconciliation: dict[str, Any] | None = None
    if execute and config["automation"]["automatic_ingest"] is True:
        from ..reward_memory.outcome_lifecycle import (
            reconcile_pending_turn_outcome_ingests_fail_open,
        )

        reconciliation = reconcile_pending_turn_outcome_ingests_fail_open(
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            observed_at=observed_at,
            provider=provider,
        )

    def with_reconciliation(payload: dict[str, Any]) -> dict[str, Any]:
        if reconciliation is not None:
            payload["outcome_ingest_reconciliation"] = reconciliation
        return payload

    if config["automation"]["automatic_recall"] is not True:
        return with_reconciliation(
            _unavailable_payload(
                goal_id=goal_id,
                agent_id=agent_id,
                status=experiment_status
                | {
                    "status": "explicitly_disabled",
                    "reason_code": "automatic_recall_explicitly_disabled",
                },
            )
        )

    identity = reward_memory_turn_identity_scope(config)
    situation = build_agent_turn_situation(
        quota_decision,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_instance_id,
        workspace_ref=str(identity["workspace_ref"] or ""),
        project_ref=str(identity["project_ref"] or ""),
        user_ref=identity["user_ref"],
        session_ref=resolve_reward_memory_turn_session_ref(identity, session_ref),
    )
    receipt_path = agent_turn_recall_receipt_path(
        _goal_repo(registry_path, goal_id),
        goal_id=goal_id,
        agent_id=agent_id,
    )
    previous = None if force_refresh else load_agent_turn_recall_receipt(receipt_path)
    deduplicated = (
        deduplicated_agent_turn_recall_payload(
            previous,
            goal_id=goal_id,
            agent_id=agent_id,
        )
        if previous and previous.get("turn_recall_id") == situation["turn_recall_id"]
        else None
    )
    if deduplicated is not None:
        return with_reconciliation(
            deduplicated
            | {
                "experiment": experiment_status,
                "host_wiring": "agent_turn_admission",
            }
        )
    if not execute:
        return with_reconciliation(
            build_agent_turn_recall_preview(situation)
            | {
                "goal_id": goal_id,
                "agent_id": agent_id,
                "experiment": experiment_status,
                "host_wiring": "agent_turn_admission",
            }
        )

    effective_observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    result = run_agent_turn_recall(
        config,
        situation,
        observed_at=effective_observed_at,
        read_authority_checkpoints=reward_memory_turn_read_authority_checkpoints(
            config, goal_id
        ),
        provider=provider,
    ) | {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "experiment": experiment_status,
        "host_wiring": "agent_turn_admission",
    }
    result = with_reconciliation(result)
    write_agent_turn_recall_receipt(
        receipt_path,
        {
            "schema_version": AGENT_TURN_RECALL_RECEIPT_SCHEMA_VERSION,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "turn_recall_id": result.get("turn_recall_id"),
            "situation_fingerprint": result.get("situation_fingerprint"),
            "source_status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "provider_call_count": result.get("provider_call_count"),
            "context": result.get("context"),
            "observed_at": effective_observed_at,
        },
    )
    result["same_turn_receipt_written"] = True
    return result


def run_configured_agent_turn_recall_fail_open(**kwargs: Any) -> dict[str, Any]:
    """Keep the base Turn runnable while exposing one typed recall failure."""

    try:
        return run_configured_agent_turn_recall(**kwargs)
    except (OSError, RuntimeError, TypeError, ValueError):
        return {
            "ok": True,
            "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
            "status": "runtime_unavailable",
            "reason_code": "automatic_recall_runtime_failed",
            "goal_id": str(kwargs.get("goal_id") or ""),
            "agent_id": str(kwargs.get("agent_id") or ""),
            "surface_id": AGENT_TURN_RECALL_SURFACE_ID,
            "context": None,
            "provider_call_count": 0,
            "host_wiring": "agent_turn_admission",
            "fail_open": True,
            "provider_failure_is_user_gate": False,
            "grants_new_action_authority": False,
            "quota_spend_performed": False,
            "external_writes_performed": False,
            "suppress_external_sinks": True,
        }


__all__ = [
    "AGENT_TURN_RECALL_RECEIPT_SCHEMA_VERSION",
    "agent_turn_recall_receipt_path",
    "deduplicated_agent_turn_recall_payload",
    "load_agent_turn_recall_receipt",
    "resolve_reward_memory_turn_session_ref",
    "reward_memory_turn_identity_scope",
    "reward_memory_turn_read_authority_checkpoints",
    "run_configured_agent_turn_recall",
    "run_configured_agent_turn_recall_fail_open",
    "write_agent_turn_recall_receipt",
]
