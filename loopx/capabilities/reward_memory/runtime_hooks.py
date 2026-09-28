from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..context_providers.base import ContextProvider
from .application import (
    RewardMemoryApplier,
    RewardMemoryRecallSession,
    RewardMemoryRecallInputError,
    apply_reward_memory_recall,
    build_reward_memory_recall_request,
    execute_reward_memory_recall,
)
from .experiment import resolve_reward_memory_surface_config


AUTOMATIC_RECALL_HOOK_SCHEMA_VERSION = "reward_memory_automatic_recall_hook_v0"
AUTOMATIC_INGEST_HOOK_SCHEMA_VERSION = "reward_memory_automatic_ingest_hook_v0"

AutomaticIngestAdapter = Callable[..., dict[str, Any]]


def _automation(config: Mapping[str, Any]) -> Mapping[str, Any]:
    automation = config.get("automation")
    if not isinstance(automation, Mapping) or automation.get("fail_open") is not True:
        raise ValueError("reward-memory automation config is not normalized")
    return automation


def _recall_queries(
    queries: Sequence[Mapping[str, Any]], profile: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    if isinstance(queries, (str, bytes)):
        raise ValueError("automatic recall queries must be a bounded object list")
    result = list(queries)
    maximum = int(profile["max_queries"])
    mode = str(profile["mode"])
    if not 1 <= len(result) <= maximum:
        raise ValueError("automatic recall query count exceeds the surface profile")
    if mode == "function_boundary" and len(result) != 1:
        raise ValueError("function-boundary automatic recall requires one query")
    return result


def _recall_base(*, surface_id: str, enabled: bool, base_output: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": AUTOMATIC_RECALL_HOOK_SCHEMA_VERSION,
        "surface_id": surface_id,
        "status": "disabled" if not enabled else "not_available",
        "output": base_output,
        "automatic_recall": enabled,
        "fail_open_preserved_base": True,
        "provider_failure_is_user_gate": False,
        "grants_new_action_authority": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
        "telemetry": {
            "profile_id": None,
            "query_count": 0,
            "configured_corpus_count": 0,
            "attempted_corpus_count": 0,
            "provider_call_count": 0,
            "provider_call_cap": 0,
            "result_readback_verified": False,
        },
    }


def run_reward_memory_automatic_recall_hook(
    config: Mapping[str, Any],
    *,
    surface_id: str,
    base_output: Any,
    workspace_ref: str,
    project_ref: str,
    revision_ref: str,
    queries: Sequence[Mapping[str, Any]],
    observed_at: str,
    freshness_context: Mapping[str, Any],
    conflict_state: str,
    read_authority_checkpoints: Mapping[str, Mapping[str, Any]],
    application_id: str,
    artifact_ref: str | None = None,
    user_ref: str | None = None,
    peer_ref: str | None = None,
    session_ref: str | None = None,
    apply_memory: RewardMemoryApplier | None = None,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Run one opt-in module-owned recall boundary over explicit corpus routes.

    The caller owns the surface, queries, current-artifact checks, and semantic
    application callback. The hook only enforces the configured route/profile,
    stops after the first exact-corpus hit, and preserves the caller's base
    output on every guard or provider failure.
    """

    try:
        enabled = _automation(config).get("automatic_recall") is True
    except (TypeError, ValueError):
        return _recall_base(
            surface_id=surface_id,
            enabled=False,
            base_output=base_output,
        ) | {
            "status": "guard_rejected",
            "reason_code": "automation_config_invalid",
        }
    base = _recall_base(
        surface_id=surface_id,
        enabled=enabled,
        base_output=base_output,
    )
    if not enabled:
        return base
    try:
        route = resolve_reward_memory_surface_config(config, surface_id)
        profile = route["recall_profile"]
        bounded_queries = _recall_queries(queries, profile)
    except (KeyError, TypeError, ValueError):
        return base | {
            "status": "guard_rejected",
            "reason_code": "surface_profile_or_query_invalid",
        }

    routes = list(route["recall_corpora"])
    telemetry = {
        "profile_id": profile["profile_id"],
        "mode": profile["mode"],
        "query_count": len(bounded_queries),
        "max_queries_per_corpus": profile["max_queries"],
        "configured_corpus_count": len(routes),
        "attempted_corpus_count": 0,
        "provider_call_count": 0,
        "provider_call_cap": profile["max_queries"] * len(routes),
        "result_readback_verified": False,
    }
    attempts: list[dict[str, Any]] = []
    last_session: RewardMemoryRecallSession | None = None
    for corpus_route in routes:
        corpus = corpus_route["corpus"]
        corpus_id = str(corpus["corpus_id"])
        checkpoint = read_authority_checkpoints.get(corpus_id, {})
        try:
            request = build_reward_memory_recall_request(
                corpus,
                {
                    "workspace_ref": workspace_ref,
                    "project_ref": project_ref,
                    "user_ref": user_ref,
                    "peer_ref": peer_ref,
                    "session_ref": session_ref,
                    "surface_id": surface_id,
                    "revision_ref": revision_ref,
                    "mode": profile["mode"],
                    "queries": bounded_queries,
                    "limit": profile["limit"],
                    "observed_at": observed_at,
                    "freshness_context": dict(freshness_context),
                    "conflict_state": conflict_state,
                    "raw_content_captured": False,
                },
                read_authority_checkpoint=checkpoint,
            )
            session = execute_reward_memory_recall(
                request,
                provider_binding=corpus_route["provider_binding"],
                provider=provider,
            )
        except RewardMemoryRecallInputError as exc:
            return base | {
                "status": "guard_rejected",
                "reason_code": "exact_corpus_request_invalid",
                "boundary_detail_code": exc.reason_code,
                "recall_attempts": attempts,
                "telemetry": telemetry,
            }
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            return base | {
                "status": "guard_rejected",
                "reason_code": "exact_corpus_request_invalid",
                "recall_attempts": attempts,
                "telemetry": telemetry,
            }
        last_session = session
        attempts.append(session.public_packet)
        telemetry["attempted_corpus_count"] += 1
        telemetry["provider_call_count"] += int(
            session.public_packet.get("provider_call_count") or 0
        )
        if session.public_packet.get("status") != "empty":
            break

    if last_session is not None:
        telemetry["result_readback_verified"] = bool(
            last_session.public_packet.get("result_readback_verified")
        )

    if last_session is None:
        return base | {
            "status": "guard_rejected",
            "reason_code": "surface_has_no_recall_corpus",
            "telemetry": telemetry,
        }
    application = apply_reward_memory_recall(
        base_output,
        last_session,
        application_id=application_id,
        artifact_ref=artifact_ref,
        apply_memory=apply_memory,
    )
    status = str(application["status"])
    if last_session.public_packet.get("status") == "provider_unavailable":
        status = "provider_unavailable"
    return base | {
        "status": status,
        "output": application["output"],
        "application": {
            key: value for key, value in application.items() if key != "output"
        },
        "recall_attempts": attempts,
        "fail_open_preserved_base": application.get("output") == base_output,
        "telemetry": telemetry,
    }


def _ingest_base(*, surface_id: str, enabled: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": AUTOMATIC_INGEST_HOOK_SCHEMA_VERSION,
        "surface_id": surface_id,
        "status": "disabled" if not enabled else "not_available",
        "automatic_ingest": enabled,
        "fail_open": True,
        "provider_failure_is_user_gate": False,
        "grants_new_action_authority": False,
        "external_writes_performed": False,
        "raw_content_captured": False,
        "telemetry": {
            "provider_sync_count": 0,
            "exact_readback_verified": False,
            "deduplicated": False,
        },
    }


def run_reward_memory_automatic_ingest_hook(
    config: Mapping[str, Any],
    *,
    surface_id: str,
    adapter: str,
    event: Mapping[str, Any],
    observed_at: str,
    ingest_event: AutomaticIngestAdapter,
    execute: bool = True,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Run one opt-in compact event through the configured ingest corpus."""

    try:
        enabled = _automation(config).get("automatic_ingest") is True
    except (TypeError, ValueError):
        return _ingest_base(surface_id=surface_id, enabled=False) | {
            "status": "guard_rejected",
            "reason_code": "automation_config_invalid",
        }
    base = _ingest_base(surface_id=surface_id, enabled=enabled)
    if not enabled:
        return base
    source = event.get("source")
    if (
        event.get("surface_id") != surface_id
        or event.get("raw_content_captured") is not False
        or not isinstance(source, Mapping)
        or not str(source.get("actor_ref") or "").strip()
        or not str(source.get("actor_role") or "").strip()
    ):
        return base | {
            "status": "guard_rejected",
            "reason_code": "compact_actor_scoped_event_required",
        }
    try:
        route = resolve_reward_memory_surface_config(
            config,
            surface_id,
            adapter=adapter,
        )
        receipt = ingest_event(
            event,
            corpus=route["corpus"],
            standing_policy=route["standing_policy"],
            provider_binding=route["provider_binding"],
            observed_at=observed_at,
            execute=execute,
            provider=provider,
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return base | {
            "status": "guard_rejected",
            "reason_code": "compact_event_or_standing_policy_rejected",
        }
    status = str(receipt.get("status") or "not_available")
    provider_statuses = {"provider_unavailable", "committed_pending"}
    provider_sync_attempted = bool(
        status not in {"guard_blocked"} and isinstance(receipt.get("write"), Mapping)
    )
    return base | {
        "status": status,
        "receipt": receipt,
        "external_writes_performed": bool(receipt.get("external_writes_performed")),
        "telemetry": {
            "provider_sync_count": 1 if provider_sync_attempted else 0,
            "exact_readback_verified": bool(receipt.get("exact_readback_verified")),
            "deduplicated": bool(receipt.get("deduplicated")),
            "provider_available": status not in provider_statuses,
        },
    }
