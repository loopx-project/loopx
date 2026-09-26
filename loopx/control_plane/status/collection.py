"""Status collection assembly inside the `status` bounded context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..goals.acceptance_observation import attach_goal_acceptance_observations
from ..goals.artifact_lifecycle import attach_goal_artifact_lifecycle_projections
from ..goals.contract_health import project_contract_health_for_goal
from ..goals.activation import (
    GoalActivationState,
    goal_activation_state,
    normalize_goal_activation_state,
)
from ..runtime.runtime_projection_route import (
    collect_runtime_projection_route_diagnostics,
)
from ..projection_envelope_facts import seal_projection_envelope, source_fact
from ..runtime.time import now_utc_iso, parse_timestamp, utc_isoformat
from ..todos.todo_index import MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL
from ...registry import registry_goals
from ...rollout_event_log import RolloutEventSnapshot


StatusCallback = Callable[..., Any]


def registry_activation_revision(registry: dict[str, Any]) -> str:
    """Stable fingerprint of the registry's goal activation partition.

    Two scoped snapshots (active vs stopped) can be merged safely only when
    this revision matches: it changes exactly when a goal is stopped or
    resumed between the two requests. A mismatched revision means the merge
    may duplicate or omit goals, so consumers should keep the projection
    incomplete and resync instead of trusting the concatenated view.
    """
    entries = sorted(
        (str(goal.get("id") or ""), goal_activation_state(goal).value)
        for goal in registry_goals(registry)
    )
    digest = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"registry_activation_v1:{digest[:16]}"


@dataclass(frozen=True)
class StatusCollectionContext:
    load_registry: StatusCallback
    resolve_runtime_root: StatusCallback
    collect_global_registry_health: StatusCallback
    collect_status_history: StatusCallback
    check_contract: StatusCallback
    build_attention_queue: StatusCallback
    build_runtime_summaries: StatusCallback
    build_promotion_gate: StatusCallback
    build_status_contract: StatusCallback
    build_contract_health_projection: StatusCallback
    build_agent_management_projection: StatusCallback
    build_goal_channel_notification_projection: StatusCallback
    status_control_plane_context_limit: int
    max_todo_index_items: int


def collect_status(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    scan_roots: list[Path],
    limit: int,
    context: StatusCollectionContext,
    include_task_graph: bool = False,
    goal_id: str | None = None,
    available_capabilities: Any = None,
    include_public_boundary_scan: bool = True,
    recent_run_limit: int | None = None,
    include_goal_subagent_configuration: bool = False,
    activation_state_filter: GoalActivationState | str | None = None,
    agent_lane_id: str | None = None,
) -> dict[str, Any]:
    display_limit = max(0, limit)
    control_plane_limit = max(
        display_limit,
        context.status_control_plane_context_limit,
        max(0, recent_run_limit) if recent_run_limit is not None else 0,
    )
    goal_filter = str(goal_id or "").strip() or None
    activation_filter = (
        normalize_goal_activation_state(activation_state_filter)
        if activation_state_filter is not None
        else None
    )
    registry = context.load_registry(registry_path)
    registry_read_at = now_utc_iso()
    runtime_root = context.resolve_runtime_root(
        registry,
        runtime_root_override,
        registry_path=registry_path,
    )
    rollout_events = RolloutEventSnapshot(
        runtime_root,
        limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
    )
    global_registry = context.collect_global_registry_health(
        registry_path=registry_path,
        runtime_root=runtime_root,
        current_registry=registry,
    )
    global_registry_read_at = now_utc_iso()
    include_runtime_goals = bool(global_registry.get("current_registry_is_global"))
    history_collection = context.collect_status_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_filter,
        limit=control_plane_limit,
        status_include_runtime_goals=include_runtime_goals,
        activation_state_filter=activation_filter,
        agent_lane_id=agent_lane_id,
        registry=registry,
    )
    history = history_collection.status_history
    history_read_at = now_utc_iso()
    contract = context.check_contract(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=scan_roots,
        limit=limit,
        goal_id_filter=goal_filter,
        include_public_boundary_scan=include_public_boundary_scan,
        activation_state_filter=activation_filter,
        history_audit=history_collection.contract_audit,
        registry=registry,
    )
    contract_read_at = now_utc_iso()
    contract = project_contract_health_for_goal(contract, goal_id=goal_filter)
    queue = context.build_attention_queue(
        contract=contract,
        history=history,
        global_registry=global_registry,
        runtime_root=runtime_root,
        include_task_graph=include_task_graph,
        goal_id_filter=goal_filter,
        include_stopped_goal_context=(
            activation_filter is GoalActivationState.STOPPED
        ),
        events_for_goal=rollout_events.events_for_goal,
    )
    runtime_summaries = context.build_runtime_summaries(
        history=history,
        queue=queue,
        runtime_root=runtime_root,
        goal_id_filter=goal_filter,
        display_limit=display_limit,
        todo_index_limit=max(context.max_todo_index_items, display_limit),
        recent_run_limit=recent_run_limit,
        include_goal_subagent_configuration=(
            include_goal_subagent_configuration
        ),
        events_for_goal=rollout_events.events_for_goal,
    )
    promotion_gate = context.build_promotion_gate(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        registry=registry,
    )
    runtime_projection_routes = collect_runtime_projection_route_diagnostics(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_filter,
        activation_state_filter=activation_filter,
        registry=registry,
    )
    routes_read_at = now_utc_iso()
    runtime_projection_route_health = {
        "healthy": (
            bool(runtime_projection_routes.get("healthy"))
            if runtime_projection_routes.get("available")
            else None
        ),
        # Registry, Goal and activation scope already live in the status envelope.
        "goal_count": int(runtime_projection_routes.get("goal_count") or 0),
    }
    contract_projection = {
        "ok": contract.get("ok"),
        "summary": contract.get("summary"),
        "errors": contract.get("errors") or [],
        "warnings": contract.get("warnings") or [],
        "checks": contract.get("checks") or [],
    }
    for key in (
        "error_diagnostics",
        "global_errors",
        "goal_errors",
    ):
        if contract.get(key):
            contract_projection[key] = contract[key]
    payload = {
        "ok": bool(contract.get("ok")) and bool(global_registry.get("ok", True)),
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "goal_count": history.get("goal_count"),
        "run_count": history.get("run_count"),
        "status_contract": context.build_status_contract(),
        "goal_filter": goal_filter,
        **context.build_contract_health_projection(contract),
        "contract": contract_projection,
        "global_registry": global_registry,
        "attention_queue": queue,
        **runtime_summaries,
        "promotion_gate": promotion_gate,
    }
    if activation_filter is not None:
        payload["goal_projection"] = {
            "schema_version": "loopx_goal_projection_scope_v0",
            "scope": activation_filter.value,
            "complete": False,
            "projected_goal_count": int(history.get("goal_count") or 0),
            "registry_goal_count": len(registry_goals(registry)),
            "registry_revision": registry_activation_revision(registry),
        }
    payload["runtime_projection_routes"] = runtime_projection_route_health
    agent_management_projection = context.build_agent_management_projection(
        payload,
        available_capabilities=available_capabilities,
    )
    if agent_management_projection.get("agents"):
        payload["agent_management_projection"] = agent_management_projection
    try:
        goal_channel_notification_projection = (
            context.build_goal_channel_notification_projection(
                registry_path=registry_path,
                registry=registry,
                goal_id=goal_filter,
            )
        )
        if activation_filter is not None and isinstance(
            goal_channel_notification_projection, dict
        ):
            projected_goal_ids = {
                str(goal.get("id") or "")
                for goal in registry_goals(registry)
                if goal_activation_state(goal) is activation_filter
            }
            goal_channel_notification_projection = {
                **goal_channel_notification_projection,
                "goals": [
                    row
                    for row in goal_channel_notification_projection.get("goals") or []
                    if isinstance(row, dict)
                    and str(row.get("goal_id") or "") in projected_goal_ids
                ],
            }
    except Exception:
        goal_channel_notification_projection = None
    notification_rows = (
        goal_channel_notification_projection.get("goals")
        if isinstance(goal_channel_notification_projection, dict)
        else None
    )
    if isinstance(notification_rows, list) and any(
        isinstance(row, dict) and row.get("configured") is True
        for row in notification_rows
    ):
        payload["goal_channel_notification_projection"] = (
            goal_channel_notification_projection
        )
    attach_goal_acceptance_observations(payload, history=history)
    attach_goal_artifact_lifecycle_projections(payload, history=history)
    payload["projection_envelope"] = seal_projection_envelope(
        projection="status",
        observed_at=now_utc_iso(),
        sources=[
            source_fact("registry", last_read_at=registry_read_at, item_count=len(registry_goals(registry))),
            source_fact(
                "global_registry",
                read_status="read" if global_registry.get("available") else global_registry.get("read_status", "missing"),
                last_read_at=global_registry_read_at if global_registry.get("available") else None,
                required=False,
                item_count=global_registry.get("global_goal_count"),
            ),
            _run_index_source(history, read_at=history_read_at),
            source_fact("goal_state_contract", last_read_at=contract_read_at),
            source_fact(
                "runtime_projection_routes",
                read_status="read" if runtime_projection_routes.get("available") else "missing",
                last_read_at=routes_read_at if runtime_projection_routes.get("available") else None,
                required=False,
                item_count=runtime_projection_route_health["goal_count"],
            ),
        ],
        coverage=_status_coverage(
            registry,
            history=history,
            queue=queue,
            goal_filter=goal_filter,
            activation_filter=activation_filter,
        ),
    )
    return payload


def _run_index_source(history: dict[str, Any], *, read_at: str) -> dict[str, Any]:
    goals = [goal for goal in history.get("goals") or [] if isinstance(goal, dict)]
    newest = None
    for goal in goals:
        for run in goal.get("latest_runs") or []:
            generated = parse_timestamp(run.get("generated_at")) if isinstance(run, dict) else None
            if generated is not None and generated.tzinfo is not None and (newest is None or generated > newest):
                newest = generated
    return source_fact(
        "goal_run_indexes",
        last_read_at=read_at,
        source_updated_at=utc_isoformat(newest) if newest is not None else None,
        item_count=len(goals),
        missing_count=sum(1 for goal in goals if goal.get("index_exists") is False),
    )


def _status_coverage(
    registry: dict[str, Any],
    *,
    history: dict[str, Any],
    queue: dict[str, Any],
    goal_filter: str | None,
    activation_filter: GoalActivationState | None,
) -> dict[str, Any]:
    """Count registry members in the requested scope; legacy runtime goals are extra."""
    members = registry_goals(registry)
    if goal_filter is not None:
        scope, expected = "goal", 1
        expected_ids = {goal_filter}
    elif activation_filter is not None:
        scope = f"activation.{activation_filter.value}"
        expected_ids = {
            str(goal.get("id") or "") for goal in members if goal_activation_state(goal) is activation_filter
        }
        expected = len(expected_ids)
    else:
        scope = "registry"
        expected_ids = {str(goal.get("id") or "") for goal in members}
        expected = len(expected_ids)
    projected = {
        str(goal.get("id") or "")
        for goal in history.get("goals") or []
        if isinstance(goal, dict)
        and (goal.get("registry_member") is True or (goal_filter is not None and goal.get("index_exists") is True))
    }
    missing = sorted(expected_ids - projected)
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    item_count = queue.get("item_count")
    return {
        "scope": scope,
        "expected_count": expected,
        "included_count": len(expected_ids & projected),
        "omitted": (
            [{"reason": "goal_not_found" if goal_filter else "not_projected", "count": len(missing), "refs": missing[:8]}]
            if missing
            else []
        ),
        "shown_count": len(items),
        "available_count": item_count if isinstance(item_count, int) and item_count >= 0 else len(items),
    }
