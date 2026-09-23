"""Provider routing and projection delivery, not a second Monitor planner."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from ...agent_registry import registered_agent_ids_from_registry
from ..coordination.authority_source_capture import authority_registry_source
from ..coordination.local_authority import (
    LOCAL_AUTHORITY_SOURCES,
    LocalCoordinationAuthorityUnavailable,
    local_authority_is_promoted,
    read_canonical_todos_if_promoted,
)
from ..effect_runtime import effect_runtime_result
from ..todos.provider_projection import settle_canonical_todo_projection


_MONITOR_POLL_RUNTIME_TIMEOUT_SECONDS = 45.0


def require_monitor_poll_source_available(*, runtime_root: Path, goal_id: str) -> None:
    """Fail closed on unavailable promoted authority before unrelated quota work."""
    read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)


def poll_canonical_monitor_if_promoted(
    *, registry_path: Path, runtime_root: Path, goal_id: str, execute: bool,
    monitor_effect_id: str | None, agent_id: str | None,
    observation: dict[str, Any], intent: dict[str, Any],
    lease_proof: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id):
        return None
    with authority_registry_source(registry_path) as registry_source:
        registered = registered_agent_ids_from_registry(registry_path, goal_id)
    result = effect_runtime_result("coordination.local_authority.monitor_poll", {
        "schema_version": "loopx_coordination_monitor_poll_request_v2",
        **({"lease_proof": lease_proof} if lease_proof is not None else {}),
        "runtime_root": str(runtime_root.expanduser().resolve()), "goal_id": goal_id,
        "operation_id": monitor_effect_id or f"monitor-poll:{goal_id}:{uuid4().hex}",
        "actor_agent_id": agent_id,
        "registered_agents": registered,
        "registry_source": registry_source,
        "dry_run": not execute, "observation": observation, "intent": intent,
    }, timeout=_MONITOR_POLL_RUNTIME_TIMEOUT_SECONDS)
    if (not isinstance(result, dict)
        or result.get("status") not in {"applied", "replayed", "recovered", "planned"}
        or result.get("source_authority") not in LOCAL_AUTHORITY_SOURCES
        or result.get("decision_read_from_provider") is not True
        or result.get("legacy_fallback_used") is not False
        or not isinstance(result.get("writeback"), dict)):
        payload = result if isinstance(result, dict) else {}
        raise LocalCoordinationAuthorityUnavailable(
            str(payload.get("reason") or "canonical Monitor transaction unavailable"),
            code=str(payload.get("reason_code") or "monitor_poll_unavailable"), payload=payload,
        )
    # Business commit remains successful even when the separate renderer fails.
    payload = {**result, "dry_run": not execute}
    settled = settle_canonical_todo_projection(payload=payload, registry_path=registry_path,
        runtime_root=runtime_root, goal_id=goal_id)
    return {**result["writeback"], "source_authority": result["source_authority"],
        "provider_revision": result.get("provider_revision"),
        "projection_delivery": settled.get("projection_delivery"),
        "projection_outbox": settled.get("projection_outbox")}
