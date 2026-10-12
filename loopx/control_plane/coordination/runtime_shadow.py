"""Source snapshots and management adapters for bounded file-shadow evidence.

Retained configuration and source readers live in their independent owners.
This legacy command adapter keeps its import API during the migration window;
bootstrap binds a source snapshot and mutations use the durable outbox.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .local_authority_shadow_projection import source_effect_runtime_result as effect_runtime_result
from .coordination_state_contract_generated import (
    COORDINATION_RUNTIME_SHADOW_EXACT_BOOTSTRAP_REQUEST_SCHEMA,
    COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA as RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
    COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA as RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
    LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
    LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
)


# Retained import API while explicit shadow commands remain supported.
from .configuration import (  # noqa: F401
    CoordinationRuntimeShadowConfig,
    RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
    local_authority_shadow_summary,
    validate_local_authority_shadow_change,
    apply_local_authority_shadow_change,
    coordination_shadow_summaries,
    validate_coordination_shadow_changes,
    apply_coordination_shadow_changes,
    coordination_runtime_shadow_summary,
    validate_coordination_runtime_shadow_change,
    apply_coordination_runtime_shadow_change,
    resolve_coordination_runtime_shadow_config,
)
from .authority_source_capture import (  # noqa: F401
    build_todo_runtime_shadow_projection,
    capture_todo_archive_dependencies,
    todo_partition_projector,
    build_runtime_shadow_source_snapshot,
)


from .local_authority_shadow_readback import (  # noqa: F401
    RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA,
    RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA,
    RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA,
    inspect_coordination_runtime_shadow,
    qualify_coordination_runtime_shadow,
    read_coordination_runtime_shadow_todo_candidate,
    RUNTIME_SHADOW_INSPECT_METHOD,
    RUNTIME_SHADOW_QUALIFY_METHOD,
    RUNTIME_SHADOW_TODO_READ_METHOD,
)

RUNTIME_SHADOW_BOOTSTRAP_METHOD = "coordination.runtime_shadow.bootstrap"
RUNTIME_SHADOW_ROLLBACK_METHOD = "coordination.runtime_shadow.rollback"
LOCAL_AUTHORITY_PROMOTION_REVIEW_METHOD = "coordination.local_authority.promotion_review"



RuntimeInvoker = Callable[..., object]


def bootstrap_coordination_runtime_shadow(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    operation_id: str,
    source_version: str,
    projection: Mapping[str, Any],
    source_snapshot: Mapping[str, Any] | None = None,
    goal_ref: Mapping[str, Any] | None,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Import one legacy baseline into an empty shadow without promoting it."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
            "status": "disabled",
            "reason_code": config.reason_code,
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    exact_ref: dict[str, str] | None = None
    if goal_ref is not None:
        from ..goals.source_session_registry_state import exact_goal_ref

        exact_ref = exact_goal_ref(
            str(goal_ref.get("goal_id") or ""),
            str(goal_ref.get("goal_instance_id") or ""),
        )
        if (
            exact_ref["goal_id"] != goal_id
            or goal is None
            or goal.get("id") != goal_id
            or goal.get("goal_instance_id") != exact_ref["goal_instance_id"]
        ):
            raise ValueError("shadow bootstrap GoalRef does not match its Goal")
    request = {
        "schema_version": (
            COORDINATION_RUNTIME_SHADOW_EXACT_BOOTSTRAP_REQUEST_SCHEMA
            if exact_ref is not None
            else RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA_VERSION
        ),
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "source_snapshot": dict(source_snapshot or {}),
        "operation_id": operation_id,
        "source_version": source_version,
        "projection": dict(projection),
        **({"goal_ref": exact_ref} if exact_ref is not None else {}),
    }
    try:
        result = runtime_invoker(RUNTIME_SHADOW_BOOTSTRAP_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_bootstrap_runtime_unavailable",
            "reason": str(exc),
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_bootstrap_runtime_result_invalid",
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    return dict(result)


def rollback_coordination_runtime_shadow(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    operation_id: str,
    expected_provider_revision: str | None = None,
    expected_bootstrap_operation_id: str | None = None,
    projection: Mapping[str, Any] | None = None,
    source_snapshot: Mapping[str, Any] | None = None,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Quarantine one revision-fenced pre-promotion file shadow lineage."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
            "status": "disabled",
            "reason_code": config.reason_code,
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    request = {
        "schema_version": RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA_VERSION,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "source_snapshot": dict(source_snapshot or {}),
        "operation_id": operation_id,
        "expected_provider_revision": expected_provider_revision,
        "expected_bootstrap_operation_id": expected_bootstrap_operation_id,
        "projection": dict(projection or {}),
    }
    try:
        result = runtime_invoker(RUNTIME_SHADOW_ROLLBACK_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_rollback_runtime_unavailable",
            "reason": str(exc),
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_rollback_runtime_result_invalid",
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    return dict(result)


def review_local_coordination_authority_promotion(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    operation_id: str,
    projection: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    minimum_operations: int,
    required_event_kinds: list[str],
    handoff_mode_migration: str | None = None,
    registered_agents: list[str] | None = None,
    execute: bool,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Preview or atomically apply the reviewed whole-Goal coordination-authority cutover."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
            "status": "disabled",
            "executed": False,
            "reason_code": config.reason_code,
            "legacy_writer_fenced": False,
            "legacy_fallback_used": False,
        }
    request = {
        "schema_version": LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "operation_id": operation_id,
        "projection": dict(projection),
        "source_snapshot": dict(source_snapshot),
        "minimum_operations": minimum_operations,
        "required_event_kinds": list(required_event_kinds),
        **(
            {
                "handoff_mode_migration": handoff_mode_migration,
                "registered_agents": list(registered_agents or []),
            }
            if handoff_mode_migration is not None
            else {}
        ),
        "execute": execute,
    }
    try:
        result = runtime_invoker(LOCAL_AUTHORITY_PROMOTION_REVIEW_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
            "status": "failed",
            "executed": False,
            "reason_code": "promotion_review_runtime_unavailable",
            "reason": str(exc),
            "legacy_writer_fenced": False,
            "legacy_fallback_used": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
            "status": "failed",
            "executed": False,
            "reason_code": "promotion_review_runtime_result_invalid",
            "legacy_writer_fenced": False,
            "legacy_fallback_used": False,
        }
    return dict(result)
