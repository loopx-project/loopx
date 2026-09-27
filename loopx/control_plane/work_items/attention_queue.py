from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Any, Callable, Optional

from ..goals.legacy_event_source import RetiredTodoEventSourceError


AttentionItemBuilder = Callable[..., dict[str, Any]]
GlobalRegistryShadowAttacher = Callable[[dict[str, Any], dict[str, Any]], None]


@dataclass(frozen=True)
class AttentionQueueContext:
    active_state_todo_fields: Callable[..., dict[str, Any]]
    active_state_todo_attention_item: Callable[..., dict[str, Any] | None]
    latest_run: Callable[[dict[str, Any]], dict[str, Any] | None]
    goal_attention: Callable[[dict[str, Any]], dict[str, Any] | None]
    compact_agent_lane_recommendation: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    latest_agent_lane_run: Callable[[dict[str, Any]], dict[str, Any] | None]
    latest_run_recommended_action_for_projection: Callable[..., tuple[str | None, str | None]]
    compact_autonomous_replan_ack: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    latest_autonomous_replan_ack_for_projection: Callable[[list[dict[str, Any]] | None], dict[str, Any] | None]
    compact_control_plane_policy: Callable[[Any], dict[str, Any]]
    subagent_activity_for_goal: Callable[[dict[str, Any]], dict[str, Any] | None]
    interface_budget_cadence_for_runs: Callable[[list[dict[str, Any]]], dict[str, Any]]
    active_state_projection_warning: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any] | None]
    enrich_project_asset: Callable[..., None]
    project_asset_latest_validation: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    attach_active_state_project_asset_fields: Callable[..., None]
    sync_connected_attention_action_from_todos: Callable[[dict[str, Any]], None]
    quota_status: Callable[..., dict[str, Any]]
    quota_with_handoff_outcome_floor: Callable[..., dict[str, Any]]
    normalize_monitor_quiet_attention_display: Callable[[dict[str, Any]], None]
    build_task_graph_projection: Callable[..., dict[str, Any] | None]
    attach_goal_channel_projection: Callable[..., None]
    attach_dependency_blockers: Callable[[list[dict[str, Any]]], None]
    autonomous_backlog_candidates: Callable[[list[dict[str, Any]]], dict[str, Any] | None]
    autonomous_monitor_candidates: Callable[[list[dict[str, Any]]], dict[str, Any] | None]
    attention_item: AttentionItemBuilder
    attach_global_registry_shadow_finding: GlobalRegistryShadowAttacher
    next_action_projection_warning: Callable[..., dict[str, Any] | None]
    autonomous_replan_obligation_from_runs: Callable[..., dict[str, Any] | None]
    source_registry_shadow_findings: AbstractSet[str]
    monitor_signal_waiting_on: str
    external_progress_review_context: Optional[Callable[..., dict[str, Any] | None]] = None


def merge_global_registry_findings(
    *,
    health_items: list[dict[str, Any]],
    history_items: list[dict[str, Any]],
    findings: list[Any],
    goal_id_filter: Optional[str],
    source_registry_shadow_findings: AbstractSet[str],
    attention_item: AttentionItemBuilder,
    attach_global_registry_shadow_finding: GlobalRegistryShadowAttacher,
) -> None:
    live_quota_items_by_goal: dict[str, list[dict[str, Any]]] = {}
    for item in history_items:
        if isinstance(item.get("quota"), dict):
            live_quota_items_by_goal.setdefault(str(item.get("goal_id") or ""), []).append(item)

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("severity") not in {"high", "action"}:
            continue
        goal_id = str(finding.get("goal_id") or "global-registry")
        if goal_id_filter:
            finding_goal_ids = [
                str(item)
                for item in (finding.get("goal_ids") or [])
                if str(item or "").strip()
            ]
            if goal_id != goal_id_filter and goal_id_filter not in finding_goal_ids:
                continue
        live_items = live_quota_items_by_goal.get(goal_id, [])
        if finding.get("kind") in source_registry_shadow_findings and live_items:
            for item in live_items:
                attach_global_registry_shadow_finding(item, finding)
            continue
        health_items.append(
            attention_item(
                goal_id=goal_id,
                status=str(finding.get("kind") or "global_registry_finding"),
                waiting_on="codex",
                severity=str(finding.get("severity") or "action"),
                recommended_action=str(finding.get("recommended_action") or "inspect global registry health"),
                source="global_registry",
            )
        )


def build_attention_queue_projection(
    *,
    items: list[dict[str, Any]],
    goal_id_filter: Optional[str],
    autonomous_backlog_candidates: Optional[dict[str, Any]],
    autonomous_monitor_candidates: Optional[dict[str, Any]],
    monitor_signal_waiting_on: str,
) -> dict[str, Any]:
    queue: dict[str, Any] = {
        "available": True,
        "item_count": len(items),
        "needs_user_or_controller": sum(
            1 for item in items if item["waiting_on"] in {"user_or_controller", "controller"}
        ),
        "needs_controller": sum(1 for item in items if item["waiting_on"] == "controller"),
        "needs_codex": sum(1 for item in items if item["waiting_on"] == "codex"),
        "watching_external_evidence": sum(1 for item in items if item["waiting_on"] == "external_evidence"),
        "watching_monitor": sum(1 for item in items if item["waiting_on"] == monitor_signal_waiting_on),
        "items": items,
    }
    if goal_id_filter:
        queue["goal_filter"] = goal_id_filter
        queue["goal_filter_applied"] = True
    if autonomous_backlog_candidates:
        queue["autonomous_backlog_candidates"] = autonomous_backlog_candidates
    if autonomous_monitor_candidates:
        queue["autonomous_monitor_candidates"] = autonomous_monitor_candidates
    return queue


def build_attention_queue(
    *,
    contract: dict[str, Any],
    history: dict[str, Any],
    global_registry: dict[str, Any],
    context: AttentionQueueContext,
    runtime_root: Path | None = None,
    include_task_graph: bool = False,
    goal_id_filter: str | None = None,
    include_stopped_goal_context: bool = False,
) -> dict[str, Any]:
    """Build the executable attention queue or a stopped-Goal archive view.

    Normal callers omit stopped Goals because they must never become runnable
    attention. The scoped Dashboard archive opts into their already-persisted
    Todo and run context so opening a stopped Goal remains informative; that
    opt-in does not change lifecycle authority or make the Goal executable.
    """
    health_items: list[dict[str, Any]] = []
    history_items: list[dict[str, Any]] = []
    if contract.get("ok") is False:
        health_items.append(
            context.attention_item(
                goal_id="loopx-contract",
                status="contract_check_failed",
                waiting_on="codex",
                severity="high",
                recommended_action="fix contract errors before advancing goal adapters",
                source="contract",
            )
        )

    for goal in history.get("goals") or []:
        if not isinstance(goal, dict):
            continue
        if (
            goal.get("activation_state") == "stopped"
            and not include_stopped_goal_context
        ):
            continue
        active_state_fields: dict[str, Any] | None = None
        active_state_item: dict[str, Any] | None = None
        current_status_run = context.latest_run(goal)
        goal_latest_runs = goal.get("latest_runs") if isinstance(goal.get("latest_runs"), list) else []
        if goal.get("registry_member"):
            try:
                active_state_fields = context.active_state_todo_fields(goal, runtime_root=runtime_root)
            except RetiredTodoEventSourceError as error:
                # Refuse this source without hiding independent Goals or turning
                # stale Todo/run projections into executable fallback work.
                item = context.attention_item(
                    goal_id=str(goal.get("id") or ""),
                    status=error.reason_code,
                    waiting_on="user_or_controller",
                    severity="high",
                    recommended_action=str(error),
                    source="active_state",
                )
                item["todo_source"] = "unavailable"
                item["activation_state"] = str(goal.get("activation_state") or "active")
                history_items.append(item)
                continue
            active_state_item = context.active_state_todo_attention_item(
                goal,
                active_state_fields,
                current_status_run,
            )
        if active_state_item and active_state_item.get("waiting_on") in {"controller", "user_or_controller"}:
            item = active_state_item
        else:
            item = context.goal_attention(goal)
            if not item:
                item = active_state_item
        if item:
            item["activation_state"] = str(
                goal.get("activation_state") or "active"
            )
            agent_lane_recommendation = context.compact_agent_lane_recommendation(
                context.latest_agent_lane_run(goal)
            )
            active_state_next_action = (
                active_state_fields.get("active_state_next_action")
                if isinstance(active_state_fields, dict)
                else None
            )
            latest_run_action, latest_run_action_source = (
                context.latest_run_recommended_action_for_projection(
                    current_status_run=current_status_run,
                    agent_lane_recommendation=agent_lane_recommendation,
                    active_state_next_action=active_state_next_action,
                    preferred_agent_id=None,
                )
            )
            if latest_run_action:
                item["latest_run_recommended_action"] = latest_run_action
                if latest_run_action_source:
                    item["latest_run_recommended_action_source"] = latest_run_action_source
                if isinstance(item.get("project_asset"), dict):
                    item["project_asset"]["latest_run_recommended_action"] = latest_run_action
                    if latest_run_action_source:
                        item["project_asset"][
                            "latest_run_recommended_action_source"
                        ] = latest_run_action_source
            replan_ack = context.compact_autonomous_replan_ack(
                current_status_run
            ) or context.latest_autonomous_replan_ack_for_projection(goal_latest_runs)
            if replan_ack:
                item["autonomous_replan_ack"] = replan_ack
                if isinstance(item.get("project_asset"), dict):
                    item["project_asset"]["autonomous_replan_ack"] = replan_ack
            control_plane = context.compact_control_plane_policy(goal.get("control_plane"))
            if control_plane:
                item["control_plane"] = control_plane
            if agent_lane_recommendation:
                item["agent_lane_recommendation"] = agent_lane_recommendation
            subagent_activity = context.subagent_activity_for_goal(goal)
            interface_budget_cadence = context.interface_budget_cadence_for_runs(goal_latest_runs)
            projection_warning = context.active_state_projection_warning(goal, context.latest_run(goal))
            context.enrich_project_asset(
                item,
                latest_validation=context.project_asset_latest_validation(context.latest_run(goal)),
                latest_runs=goal_latest_runs,
                execution_profile=(
                    goal.get("execution_profile")
                    if isinstance(goal.get("execution_profile"), dict)
                    else None
                ),
                orchestration=(
                    goal.get("spawn_policy")
                    if isinstance(goal.get("spawn_policy"), dict)
                    else None
                ),
                subagent_activity=subagent_activity,
                interface_budget_cadence=interface_budget_cadence,
            )
            if control_plane and isinstance(item.get("project_asset"), dict):
                item["project_asset"]["control_plane"] = control_plane
            if agent_lane_recommendation and isinstance(item.get("project_asset"), dict):
                item["project_asset"]["agent_lane_recommendation"] = agent_lane_recommendation
            if projection_warning:
                item["stale_latest_run_warning"] = projection_warning
                if isinstance(item.get("project_asset"), dict):
                    item["project_asset"]["stale_latest_run_warning"] = projection_warning
            if goal.get("registry_member"):
                if active_state_fields is None:
                    active_state_fields = context.active_state_todo_fields(goal, runtime_root=runtime_root)
                item.update(active_state_fields)
                context.sync_connected_attention_action_from_todos(item)
                external_progress_review = (
                    context.external_progress_review_context(goal, runtime_root)
                    if context.external_progress_review_context is not None
                    else None
                )
                context.attach_active_state_project_asset_fields(
                    item,
                    latest_runs=goal_latest_runs,
                    next_action_projection_warning=context.next_action_projection_warning,
                    autonomous_replan_obligation_from_runs=context.autonomous_replan_obligation_from_runs,
                    external_progress_review=external_progress_review,
                )
                item["quota"] = context.quota_status(
                    goal,
                    waiting_on=str(item.get("waiting_on") or ""),
                    severity=str(item.get("severity") or ""),
                    lifecycle_phase=item.get("lifecycle_phase"),
                    lifecycle_flags=item.get("lifecycle_flags"),
                    status=item.get("status"),
                )
                context.enrich_project_asset(
                    item,
                    user_todos=item.get("user_todos") if isinstance(item.get("user_todos"), dict) else None,
                    agent_todos=item.get("agent_todos") if isinstance(item.get("agent_todos"), dict) else None,
                    quota=item.get("quota") if isinstance(item.get("quota"), dict) else None,
                    latest_runs=goal_latest_runs,
                    subagent_activity=subagent_activity,
                    interface_budget_cadence=interface_budget_cadence,
                )
                guarded_quota = context.quota_with_handoff_outcome_floor(
                    item.get("quota") if isinstance(item.get("quota"), dict) else {},
                    waiting_on=str(item.get("waiting_on") or ""),
                    project_asset=item.get("project_asset")
                    if isinstance(item.get("project_asset"), dict)
                    else None,
                    handoff_readiness=item.get("handoff_readiness")
                    if isinstance(item.get("handoff_readiness"), dict)
                    else None,
                )
                if guarded_quota != item.get("quota"):
                    item["quota"] = guarded_quota
                    context.enrich_project_asset(
                        item,
                        user_todos=item.get("user_todos") if isinstance(item.get("user_todos"), dict) else None,
                        agent_todos=item.get("agent_todos") if isinstance(item.get("agent_todos"), dict) else None,
                        quota=guarded_quota,
                        latest_runs=goal_latest_runs,
                        subagent_activity=subagent_activity,
                        interface_budget_cadence=interface_budget_cadence,
                    )
                context.sync_connected_attention_action_from_todos(item)
                context.normalize_monitor_quiet_attention_display(item)
            if include_task_graph:
                task_graph_projection = context.build_task_graph_projection(
                    item,
                    goal=goal,
                    goal_latest_runs=goal_latest_runs,
                )
                if task_graph_projection:
                    item["task_graph_projection"] = task_graph_projection
            context.attach_goal_channel_projection(
                item,
                goal=goal,
                goal_latest_runs=goal_latest_runs,
                runtime_root=runtime_root,
            )
            history_items.append(item)

    merge_global_registry_findings(
        health_items=health_items,
        history_items=history_items,
        findings=global_registry.get("findings") or [],
        goal_id_filter=goal_id_filter,
        source_registry_shadow_findings=context.source_registry_shadow_findings,
        attention_item=context.attention_item,
        attach_global_registry_shadow_finding=context.attach_global_registry_shadow_finding,
    )

    items = [*health_items, *history_items]
    context.attach_dependency_blockers(items)
    backlog_candidates = context.autonomous_backlog_candidates(items)
    monitor_candidates = context.autonomous_monitor_candidates(items)

    return build_attention_queue_projection(
        items=items,
        goal_id_filter=goal_id_filter,
        autonomous_backlog_candidates=backlog_candidates,
        autonomous_monitor_candidates=monitor_candidates,
        monitor_signal_waiting_on=context.monitor_signal_waiting_on,
    )
