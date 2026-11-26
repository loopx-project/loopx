"""Attention queue projection builder for global Goal and health work items."""
from __future__ import annotations

from collections.abc import Sequence, Set as AbstractSet
from pathlib import Path
from typing import cast

from ..coordination.local_authority import LocalCoordinationAuthorityUnavailable
from ..effect_runtime import effect_runtime_result
from ..goals.legacy_event_source import RetiredTodoEventSourceError
from .attention_queue_types import (
    ACTIONABLE_REGISTRY_SEVERITIES as ACTIONABLE_REGISTRY_SEVERITIES,
    ATTENTION_WAITING_ON_VALUES as ATTENTION_WAITING_ON_VALUES,
    USER_OR_CONTROLLER_WAIT_STATES as USER_OR_CONTROLLER_WAIT_STATES,
    ActiveStateProjectAssetAttacher as ActiveStateProjectAssetAttacher,
    ActiveStateTodoAttentionBuilder as ActiveStateTodoAttentionBuilder,
    ActiveStateTodoFieldsBuilder as ActiveStateTodoFieldsBuilder,
    AttentionItemBuilder as AttentionItemBuilder,
    AttentionQueueContext as AttentionQueueContext,
    AttentionQueueItem as AttentionQueueItem,
    AttentionQueueProjection as AttentionQueueProjection,
    AttentionWaitingOn as AttentionWaitingOn,
    EnrichProjectAssetFn as EnrichProjectAssetFn,
    ExternalProgressReviewLoader as ExternalProgressReviewLoader,
    GlobalRegistryShadowAttacher as GlobalRegistryShadowAttacher,
    GoalChannelProjectionAttacher as GoalChannelProjectionAttacher,
    LatestRunRecommendedActionBuilder as LatestRunRecommendedActionBuilder,
    QuotaHandoffFloorBuilder as QuotaHandoffFloorBuilder,
    QuotaStatusBuilder as QuotaStatusBuilder,
    TaskGraphProjectionBuilder as TaskGraphProjectionBuilder,
)
from .replan_history_codec import effective_turn_cadence_context


def _sequence_or_empty(raw: object, *, field_name: str) -> Sequence[object]:
    """Accept valid sequences and legacy empty containers; fail closed on bad scalars."""

    if raw is None or raw == [] or raw == {} or raw == ():
        return ()
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return raw
    raise TypeError(f"{field_name} must be a sequence")


def _optional_dict(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def merge_global_registry_findings(
    *,
    health_items: list[dict[str, object]],
    history_items: list[dict[str, object]],
    findings: Sequence[object],
    goal_id_filter: str | None,
    source_registry_shadow_findings: AbstractSet[str],
    attention_item: AttentionItemBuilder,
    attach_global_registry_shadow_finding: GlobalRegistryShadowAttacher,
) -> None:
    live_quota_items_by_goal: dict[str, list[dict[str, object]]] = {}
    for item in history_items:
        if isinstance(item.get("quota"), dict):
            live_quota_items_by_goal.setdefault(
                str(item.get("goal_id") or ""), []
            ).append(item)

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("severity") not in ACTIONABLE_REGISTRY_SEVERITIES:
            continue
        goal_id = str(finding.get("goal_id") or "global-registry")
        if goal_id_filter:
            raw_goal_ids = _sequence_or_empty(
                finding.get("goal_ids"), field_name="finding.goal_ids"
            )
            finding_goal_ids = [
                str(item) for item in raw_goal_ids if str(item or "").strip()
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
                recommended_action=str(
                    finding.get("recommended_action") or "inspect global registry health"
                ),
                source="global_registry",
            )
        )


def build_attention_queue_projection(
    *,
    items: Sequence[AttentionQueueItem],
    goal_id_filter: str | None,
    autonomous_backlog_candidates: dict[str, object] | None,
    autonomous_monitor_candidates: dict[str, object] | None,
    monitor_signal_waiting_on: str,
) -> AttentionQueueProjection:
    needs_user_or_controller = 0
    needs_controller = 0
    needs_codex = 0
    watching_external_evidence = 0
    watching_monitor = 0
    for item in items:
        waiting_on = item["waiting_on"]
        if waiting_on in USER_OR_CONTROLLER_WAIT_STATES:
            needs_user_or_controller += 1
        if waiting_on == "controller":
            needs_controller += 1
        elif waiting_on == "codex":
            needs_codex += 1
        elif waiting_on == "external_evidence":
            watching_external_evidence += 1
        if waiting_on == monitor_signal_waiting_on:
            watching_monitor += 1

    queue_items = items if isinstance(items, list) else list(items)
    queue: AttentionQueueProjection = {
        "available": True,
        "item_count": len(items),
        "needs_user_or_controller": needs_user_or_controller,
        "needs_controller": needs_controller,
        "needs_codex": needs_codex,
        "watching_external_evidence": watching_external_evidence,
        "watching_monitor": watching_monitor,
        "items": queue_items,
    }
    if goal_id_filter:
        queue["goal_filter"] = goal_id_filter
        queue["goal_filter_applied"] = True
    if autonomous_backlog_candidates:
        queue["autonomous_backlog_candidates"] = autonomous_backlog_candidates
    if autonomous_monitor_candidates:
        queue["autonomous_monitor_candidates"] = autonomous_monitor_candidates
    return queue


def _reconcile_active_state_item(
    goal: dict[str, object],
    current_run: dict[str, object] | None,
    item: dict[str, object],
    active_state_item: dict[str, object] | None,
    context: AttentionQueueContext,
) -> tuple[dict[str, object], dict[str, object] | None]:
    warning = context.active_state_projection_warning(goal, current_run)
    if not warning:
        return item, None
    reconciled = effect_runtime_result(
        "presentation.goal_attention.reconcile",
        {
            "item": item,
            "active_state_item": active_state_item,
            "warning": warning,
            "historical_run": current_run,
        },
    )
    reconciled_item = reconciled["item"]
    reconciled_warning = reconciled["warning"]
    if not isinstance(reconciled_item, dict):
        raise TypeError("presentation.goal_attention.reconcile item must be a dict")
    if reconciled_warning is not None and not isinstance(reconciled_warning, dict):
        raise TypeError(
            "presentation.goal_attention.reconcile warning must be a dict or None"
        )
    return (
        cast(dict[str, object], reconciled_item),
        cast(dict[str, object] | None, reconciled_warning),
    )


def _active_state_goal_context(
    goal: dict[str, object],
    current_run: dict[str, object] | None,
    context: AttentionQueueContext,
    runtime_root: Path | None,
) -> tuple[dict[str, object], dict[str, object] | None]:
    fields = context.active_state_todo_fields(goal, runtime_root=runtime_root)
    # Goal content survives even when no work creates an attention item.
    goal["goal_objective"] = fields.get("goal_objective")
    return fields, context.active_state_todo_attention_item(goal, fields, current_run)


def _unavailable_todo_source_item(
    goal: dict[str, object],
    error: RetiredTodoEventSourceError | LocalCoordinationAuthorityUnavailable,
    context: AttentionQueueContext,
) -> dict[str, object]:
    item = context.attention_item(
        goal_id=str(goal.get("id") or ""),
        status=(
            error.code
            if isinstance(error, LocalCoordinationAuthorityUnavailable)
            else error.reason_code
        ),
        waiting_on="user_or_controller",
        severity="high",
        recommended_action=str(error),
        source="active_state",
    )
    item["todo_source"] = "unavailable"
    if isinstance(error, LocalCoordinationAuthorityUnavailable):
        item["error_code"] = error.code
        item["authority_read"] = error.payload
    item["activation_state"] = str(goal.get("activation_state") or "active")
    return item


def _select_goal_attention_item(
    goal: dict[str, object],
    current_status_run: dict[str, object] | None,
    active_state_item: dict[str, object] | None,
    context: AttentionQueueContext,
) -> dict[str, object] | None:
    if (
        active_state_item
        and active_state_item.get("waiting_on") in USER_OR_CONTROLLER_WAIT_STATES
    ):
        return active_state_item
    item = context.goal_attention(goal)
    if not item or (
        current_status_run is None
        and item.get("status") == "unknown"
        and active_state_item
    ):
        return active_state_item
    return item


def _enrich_registry_member_item(
    item: dict[str, object],
    *,
    goal: dict[str, object],
    active_state_fields: dict[str, object] | None,
    goal_latest_runs: list[dict[str, object]],
    subagent_activity: dict[str, object] | None,
    interface_budget_cadence: dict[str, object] | None,
    context: AttentionQueueContext,
    runtime_root: Path | None,
    agent_lane_id: str | None,
) -> None:
    if active_state_fields is None:
        active_state_fields = context.active_state_todo_fields(
            goal, runtime_root=runtime_root
        )
    item.update(
        {
            key: value
            for key, value in active_state_fields.items()
            if key != "goal_objective"
        }
    )
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
        autonomous_replan_obligation_from_runs=(
            context.autonomous_replan_obligation_from_runs
        ),
        external_progress_review=external_progress_review,
        effective_turn_cadence=effective_turn_cadence_context(
            goal,
            runtime_root,
            registry_path=context.registry_path,
            tolerate_malformed_history=context.tolerate_malformed_settlement_history,
        ),
        agent_lane_id=agent_lane_id,
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
        user_todos=_optional_dict(item.get("user_todos")),
        agent_todos=_optional_dict(item.get("agent_todos")),
        quota=_optional_dict(item.get("quota")),
        latest_runs=goal_latest_runs,
        subagent_activity=subagent_activity,
        interface_budget_cadence=interface_budget_cadence,
    )
    guarded_quota = context.quota_with_handoff_outcome_floor(
        _optional_dict(item.get("quota")) or {},
        waiting_on=str(item.get("waiting_on") or ""),
        project_asset=_optional_dict(item.get("project_asset")),
        handoff_readiness=_optional_dict(item.get("handoff_readiness")),
    )
    if guarded_quota != item.get("quota"):
        item["quota"] = guarded_quota
        context.enrich_project_asset(
            item,
            user_todos=_optional_dict(item.get("user_todos")),
            agent_todos=_optional_dict(item.get("agent_todos")),
            quota=guarded_quota,
            latest_runs=goal_latest_runs,
            subagent_activity=subagent_activity,
            interface_budget_cadence=interface_budget_cadence,
        )
    context.sync_connected_attention_action_from_todos(item)
    context.normalize_monitor_quiet_attention_display(item)


def _build_goal_attention_item(
    goal: dict[str, object],
    *,
    context: AttentionQueueContext,
    runtime_root: Path | None,
    include_task_graph: bool,
    goal_id_filter: str | None,
    include_goal_channel_projection: bool,
    agent_lane_id: str | None,
) -> dict[str, object] | None:
    active_state_fields: dict[str, object] | None = None
    active_state_item: dict[str, object] | None = None
    current_status_run = context.latest_run(goal)
    raw_latest_runs = goal.get("latest_runs")
    goal_latest_runs: list[dict[str, object]] = (
        cast(list[dict[str, object]], raw_latest_runs)
        if isinstance(raw_latest_runs, list)
        else []
    )
    if goal.get("registry_member"):
        try:
            active_state_fields, active_state_item = _active_state_goal_context(
                goal, current_status_run, context, runtime_root
            )
        except (RetiredTodoEventSourceError, LocalCoordinationAuthorityUnavailable) as error:
            if isinstance(error, LocalCoordinationAuthorityUnavailable) and goal_id_filter:
                raise
            # Refuse this source without hiding independent Goals or turning
            # stale Todo/run projections into executable fallback work.
            return _unavailable_todo_source_item(goal, error, context)

    selected = _select_goal_attention_item(
        goal, current_status_run, active_state_item, context
    )
    if not selected:
        return None
    item, projection_warning = _reconcile_active_state_item(
        goal, current_status_run, selected, active_state_item, context
    )
    item["activation_state"] = str(goal.get("activation_state") or "active")
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
    pre_enrich_asset = _optional_dict(item.get("project_asset"))
    if latest_run_action:
        item["latest_run_recommended_action"] = latest_run_action
        if latest_run_action_source:
            item["latest_run_recommended_action_source"] = latest_run_action_source
        if pre_enrich_asset is not None:
            pre_enrich_asset["latest_run_recommended_action"] = latest_run_action
            if latest_run_action_source:
                pre_enrich_asset[
                    "latest_run_recommended_action_source"
                ] = latest_run_action_source
    replan_ack = context.compact_autonomous_replan_ack(
        current_status_run
    ) or context.latest_autonomous_replan_ack_for_projection(goal_latest_runs)
    if replan_ack:
        item["autonomous_replan_ack"] = replan_ack
        if pre_enrich_asset is not None:
            pre_enrich_asset["autonomous_replan_ack"] = replan_ack
    control_plane = context.compact_control_plane_policy(goal.get("control_plane"))
    if control_plane:
        item["control_plane"] = control_plane
    if agent_lane_recommendation:
        item["agent_lane_recommendation"] = agent_lane_recommendation
    subagent_activity = context.subagent_activity_for_goal(goal)
    interface_budget_cadence = context.interface_budget_cadence_for_runs(
        goal_latest_runs
    )
    context.enrich_project_asset(
        item,
        latest_validation=context.project_asset_latest_validation(current_status_run),
        latest_runs=goal_latest_runs,
        execution_profile=_optional_dict(goal.get("execution_profile")),
        orchestration=_optional_dict(goal.get("spawn_policy")),
        subagent_activity=subagent_activity,
        interface_budget_cadence=interface_budget_cadence,
    )
    post_enrich_asset = _optional_dict(item.get("project_asset"))
    if post_enrich_asset is not None:
        if control_plane:
            post_enrich_asset["control_plane"] = control_plane
        if agent_lane_recommendation:
            post_enrich_asset["agent_lane_recommendation"] = agent_lane_recommendation
    if projection_warning:
        item["stale_latest_run_warning"] = projection_warning
        if post_enrich_asset is not None:
            post_enrich_asset["stale_latest_run_warning"] = projection_warning
    if goal.get("registry_member"):
        _enrich_registry_member_item(
            item,
            goal=goal,
            active_state_fields=active_state_fields,
            goal_latest_runs=goal_latest_runs,
            subagent_activity=subagent_activity,
            interface_budget_cadence=interface_budget_cadence,
            context=context,
            runtime_root=runtime_root,
            agent_lane_id=agent_lane_id,
        )
    if include_task_graph:
        task_graph_projection = context.build_task_graph_projection(
            item,
            goal=goal,
            goal_latest_runs=goal_latest_runs,
        )
        if task_graph_projection:
            item["task_graph_projection"] = task_graph_projection
    if include_goal_channel_projection:
        context.attach_goal_channel_projection(
            item,
            goal=goal,
            goal_latest_runs=goal_latest_runs,
            runtime_root=runtime_root,
        )
    return item


def _validated_queue_items(
    items: Sequence[dict[str, object]],
    *,
    monitor_signal_waiting_on: str,
) -> list[AttentionQueueItem]:
    validated: list[AttentionQueueItem] = []
    allowed = ATTENTION_WAITING_ON_VALUES | {monitor_signal_waiting_on}
    for item in items:
        waiting_on = item["waiting_on"]
        if not isinstance(waiting_on, str) or waiting_on not in allowed:
            raise ValueError(f"unsupported attention queue waiting_on: {waiting_on!r}")
        validated.append(cast(AttentionQueueItem, item))
    return validated


def build_attention_queue(
    *,
    contract: dict[str, object],
    history: dict[str, object],
    global_registry: dict[str, object],
    context: AttentionQueueContext,
    runtime_root: Path | None = None,
    include_task_graph: bool = False,
    goal_id_filter: str | None = None,
    include_stopped_goal_context: bool = False,
    include_goal_channel_projection: bool = True,
    agent_lane_id: str | None = None,
) -> AttentionQueueProjection:
    """Build the executable attention queue or a stopped-Goal archive view.

    Normal callers omit stopped Goals because they must never become runnable
    attention. The scoped Dashboard archive opts into their already-persisted
    Todo and run context so opening a stopped Goal remains informative; that
    opt-in does not change lifecycle authority or make the Goal executable.
    """
    health_items: list[dict[str, object]] = []
    history_items: list[dict[str, object]] = []
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

    for goal in _sequence_or_empty(history.get("goals"), field_name="history.goals"):
        if not isinstance(goal, dict):
            continue
        if (
            goal.get("activation_state") == "stopped"
            and not include_stopped_goal_context
        ):
            continue
        typed_goal = cast(dict[str, object], goal)
        built_item = _build_goal_attention_item(
            typed_goal,
            context=context,
            runtime_root=runtime_root,
            include_task_graph=include_task_graph,
            goal_id_filter=goal_id_filter,
            include_goal_channel_projection=include_goal_channel_projection,
            agent_lane_id=agent_lane_id,
        )
        if built_item is not None:
            history_items.append(built_item)

    merge_global_registry_findings(
        health_items=health_items,
        history_items=history_items,
        findings=_sequence_or_empty(
            global_registry.get("findings"), field_name="global_registry.findings"
        ),
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
        items=_validated_queue_items(
            items, monitor_signal_waiting_on=context.monitor_signal_waiting_on
        ),
        goal_id_filter=goal_id_filter,
        autonomous_backlog_candidates=backlog_candidates,
        autonomous_monitor_candidates=monitor_candidates,
        monitor_signal_waiting_on=context.monitor_signal_waiting_on,
    )
