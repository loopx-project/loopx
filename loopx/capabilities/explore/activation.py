from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...agent_registry import load_goal_from_registry
from ...explore_graph import compact_explore_graph_policy


EXPLORE_GRAPH_ACTIVATION_SCHEMA_VERSION = "loopx_explore_graph_activation_v0"

EXPLORE_GRAPH_DELIVERY_POSTCONDITION_SCHEMA_VERSION = (
    "loopx_explore_graph_delivery_postcondition_v0"
)


def explore_graph_delivery_postcondition(
    *,
    enabled: bool,
    status: str,
    ok: bool,
    external_sink_delivery_authorized: bool,
    row_readback_verified: bool | None = None,
) -> dict[str, Any]:
    """Return the material-delivery contract for one Graph activation."""

    normalized_status = str(status or "unknown")
    if not enabled:
        satisfied = normalized_status == "disabled" and ok
        disposition = "not_required" if satisfied else "activation_failed"
    elif normalized_status in {"not_applicable", "not_configured"} and ok:
        satisfied = True
        disposition = "local_projection_only"
    elif normalized_status == "unchanged" and ok and row_readback_verified is True:
        satisfied = True
        disposition = "unchanged_verified"
    elif normalized_status == "synced" and ok and row_readback_verified is True:
        satisfied = True
        disposition = "synced_and_verified"
    elif normalized_status == "external_sink_suppressed":
        satisfied = False
        disposition = "delivery_deferred_by_authority"
    else:
        satisfied = False
        disposition = "delivery_failed"

    required = enabled
    retry_required = required and not satisfied
    blocks_delivery = retry_required and external_sink_delivery_authorized
    if satisfied:
        required_action = None
    elif normalized_status == "external_sink_suppressed":
        required_action = (
            "record a concrete authorized Explore Graph sync successor; "
            "do not claim the configured sink is current"
        )
    else:
        required_action = (
            "retry configured Explore Graph sync and row/result-id readback "
            "before material delivery"
        )
    return {
        "schema_version": EXPLORE_GRAPH_DELIVERY_POSTCONDITION_SCHEMA_VERSION,
        "required": required,
        "satisfied": satisfied,
        "disposition": disposition,
        "retry_required": retry_required,
        "blocks_delivery": blocks_delivery,
        "required_action": required_action,
    }

def sync_explore_graph_after_material_refresh(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    external_sink_delivery_authorized: bool = True,
    syncer: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Flush an enabled graph after the goal-level refresh transaction.

    Explore Harness planning includes its evidence graph. A
    configured graph reuses the existing idempotent projection/sink adapter;
    disabled or absent policy performs no graph reads or writes.
    """

    goal = load_goal_from_registry(registry_path, goal_id)
    policy = compact_explore_graph_policy(
        (goal or {}).get("explore_graph"), ((goal or {}).get("spawn_policy") or {}).get("explore_harness")
    )
    base = {
        "ok": True,
        "schema_version": EXPLORE_GRAPH_ACTIVATION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "enabled": policy["enabled"],
        "trigger": "material_refresh",
        "external_sink_delivery_authorized": external_sink_delivery_authorized,
    }
    if goal is None:
        status = "goal_not_found"
        return {
            **base,
            "ok": False,
            "status": status,
            "delivery_postcondition": explore_graph_delivery_postcondition(
                enabled=bool(policy["enabled"]),
                status=status,
                ok=False,
                external_sink_delivery_authorized=external_sink_delivery_authorized,
            ),
        }
    if not policy["enabled"]:
        status = "disabled"
        return {
            **base,
            "status": status,
            "delivery_postcondition": explore_graph_delivery_postcondition(
                enabled=False,
                status=status,
                ok=True,
                external_sink_delivery_authorized=external_sink_delivery_authorized,
            ),
        }

    # Disabled activation preserves the caller's authorization observation without
    # reading or publishing anything. An enabled legacy Harness does not grant a
    # sink permission merely by supplying its local evidence graph.
    external_sink_delivery_authorized = external_sink_delivery_authorized and (
        ((goal or {}).get("explore_graph") or {}).get("enabled") is True
    )
    base["external_sink_delivery_authorized"] = external_sink_delivery_authorized

    if syncer is None:
        status = "projection_sink_provider_unavailable"
        return {
            **base,
            "ok": False,
            "status": status,
            "delivery_postcondition": explore_graph_delivery_postcondition(
                enabled=True,
                status=status,
                ok=False,
                external_sink_delivery_authorized=external_sink_delivery_authorized,
            ),
        }

    try:
        result = syncer(
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            project=project,
            state_file=state_file,
            execute=True,
            external_sink_delivery_authorized=external_sink_delivery_authorized,
        )
    except Exception as exc:
        status = "sync_failed"
        return {
            **base,
            "ok": False,
            "status": status,
            "error_type": type(exc).__name__,
            "delivery_postcondition": explore_graph_delivery_postcondition(
                enabled=True,
                status=status,
                ok=False,
                external_sink_delivery_authorized=external_sink_delivery_authorized,
            ),
        }

    projection = (
        result.get("projection")
        if isinstance(result.get("projection"), Mapping)
        else {}
    )
    result_ok = result.get("ok") is True
    status = str(result.get("status") or "unknown")
    row_readback_verified = result.get("row_readback_verified")
    return {
        **base,
        "ok": result_ok,
        "status": status,
        "applicable": projection.get("applicable"),
        "material_change": projection.get("material_change"),
        "material_event_count": projection.get("material_event_count"),
        "appended_event_count": projection.get("appended_event_count"),
        "needs_row_sync": result.get("needs_row_sync"),
        "needs_visual_sync": result.get("needs_visual_sync"),
        "row_readback_verified": row_readback_verified,
        "semantic_digest": result.get("semantic_digest"),
        "source_runtime_route": projection.get("source_runtime_route"),
        "extension_activation": result.get("extension_activation"),
        "delivery_postcondition": explore_graph_delivery_postcondition(
            enabled=True,
            status=status,
            ok=result_ok,
            external_sink_delivery_authorized=external_sink_delivery_authorized,
            row_readback_verified=(
                row_readback_verified if isinstance(row_readback_verified, bool) else None
            ),
        ),
    }
