from __future__ import annotations

from typing import AbstractSet, Any, Callable, Optional

from ..runtime.session_runtime import session_runtime_projection_attention


AttentionItemBuilder = Callable[..., dict[str, Any]]
AttentionFieldsBuilder = Callable[[Optional[dict[str, Any]]], dict[str, Any]]
GoalLifecycleFields = Callable[[dict[str, Any], Optional[dict[str, Any]]], dict[str, Any]]
LatestRun = Callable[[dict[str, Any]], Optional[dict[str, Any]]]
LegacyRuntimeGoalAttention = Callable[
    [dict[str, Any], Optional[dict[str, Any]], dict[str, Any]],
    Optional[dict[str, Any]],
]
OperatorQuestionBuilder = Callable[[str, str], str]
OperatorQuestionNormalizer = Callable[..., str]
RunHasExternalEvidenceWatchSignal = Callable[[dict[str, Any]], bool]
SessionRuntimeProjectionFromRun = Callable[[Optional[dict[str, Any]]], Optional[dict[str, Any]]]
PublicSafeText = Callable[..., Optional[str]]


def goal_attention(
    goal: dict[str, Any],
    *,
    latest_run: LatestRun,
    readiness_attention_fields: AttentionFieldsBuilder,
    operator_gate_attention_fields: AttentionFieldsBuilder,
    dreaming_attention_fields: AttentionFieldsBuilder,
    goal_lifecycle_fields: GoalLifecycleFields,
    legacy_runtime_goal_attention: LegacyRuntimeGoalAttention,
    compact_session_runtime_projection_from_run: SessionRuntimeProjectionFromRun,
    public_safe_compact_text: PublicSafeText,
    attention_item: AttentionItemBuilder,
    run_has_external_evidence_watch_signal: RunHasExternalEvidenceWatchSignal,
    default_operator_question: OperatorQuestionBuilder,
    normalize_operator_question: OperatorQuestionNormalizer,
    monitor_signal_waiting_on: str,
    default_operator_gate: str,
    planned_controller_opt_in_recommended_action: str,
    connected_adapter_statuses: AbstractSet[str],
    connected_delivery_adapter_statuses: AbstractSet[str],
    registry_waiting_on_overrides: AbstractSet[str],
    blocking_classifications: AbstractSet[str],
    user_or_controller_classifications: AbstractSet[str],
    codex_ready_classifications: AbstractSet[str],
) -> dict[str, Any] | None:
    goal_id = str(goal.get("id") or "unknown-goal")
    adapter_status = str(goal.get("adapter_status") or "")
    adapter_kind = str(goal.get("adapter_kind") or "")
    current_run = latest_run(goal)
    readiness_fields = readiness_attention_fields(current_run)
    operator_gate_fields = operator_gate_attention_fields(current_run)
    dreaming_fields = dreaming_attention_fields(current_run)
    attention_fields = {**readiness_fields, **operator_gate_fields, **dreaming_fields}
    lifecycle_fields = goal_lifecycle_fields(goal, current_run)

    if goal.get("legacy_runtime_goal"):
        return legacy_runtime_goal_attention(goal, current_run, readiness_fields)

    if not current_run:
        if (
            adapter_status in connected_adapter_statuses
            or adapter_status in connected_delivery_adapter_statuses
        ):
            # Lane notes and neutral runs do not establish Goal status, but are history.
            has_history = bool(goal.get("unique_runs") or goal.get("latest_runs"))
            return attention_item(
                goal_id=goal_id,
                status="unknown" if has_history else "connected_without_run",
                waiting_on="codex",
                severity="action",
                recommended_action=(
                    "inspect the active goal state; existing history has no goal-level status run"
                    if has_history
                    else "run the first read-only adapter tick and save a compact run record"
                ),
                source="run_history",
                **lifecycle_fields,
            )
        if adapter_status == "planned" and adapter_kind.endswith("_read_only_map_v0"):
            command = f"loopx read-only-map --goal-id {goal_id} --dry-run"
            return attention_item(
                goal_id=goal_id,
                status=str(goal.get("status") or "planned"),
                waiting_on="user_or_controller",
                severity="action",
                recommended_action=planned_controller_opt_in_recommended_action,
                operator_question=default_operator_question(goal_id, default_operator_gate),
                agent_command=command,
                source="registry",
                **lifecycle_fields,
            )
        return attention_item(
            goal_id=goal_id,
            status=str(goal.get("status") or "no_run"),
            waiting_on="controller",
            severity="action",
            recommended_action="connect an adapter or run a read-only map before expecting runtime status",
            source="registry",
            **lifecycle_fields,
        )

    json_exists = bool(current_run.get("json_exists"))
    markdown_exists = bool(current_run.get("markdown_exists"))
    if not json_exists or not markdown_exists:
        return attention_item(
            goal_id=goal_id,
            status="run_artifact_missing",
            waiting_on="codex",
            severity="high",
            recommended_action="repair or regenerate the latest run artifacts before trusting status",
            source="run_history",
            **attention_fields,
            **lifecycle_fields,
        )

    session_projection = compact_session_runtime_projection_from_run(current_run)
    if session_projection:
        return session_runtime_projection_attention(
            goal,
            current_run,
            session_projection,
            public_safe_compact_text=public_safe_compact_text,
            attention_item=attention_item,
            goal_lifecycle_fields=goal_lifecycle_fields,
            monitor_signal_waiting_on=monitor_signal_waiting_on,
        )

    classification = str(current_run.get("classification") or "unknown")
    action = str(current_run.get("recommended_action") or "inspect the latest run and choose one next action")
    registry_waiting_on = str(goal.get("waiting_on") or "")
    if registry_waiting_on in registry_waiting_on_overrides:
        registry_attention_fields = dict(attention_fields)
        if goal.get("operator_question"):
            registry_attention_fields["operator_question"] = normalize_operator_question(
                str(goal.get("operator_question") or ""),
                goal_id=goal_id,
                gate=str(goal.get("operator_gate") or default_operator_gate),
            )
        if goal.get("next_handoff_condition"):
            registry_attention_fields["next_handoff_condition"] = str(goal.get("next_handoff_condition") or "")
        return attention_item(
            goal_id=goal_id,
            status=str(goal.get("attention_status") or classification),
            waiting_on=registry_waiting_on,
            severity="watch" if registry_waiting_on == "external_evidence" else "action",
            recommended_action=str(goal.get("recommended_action") or action),
            source="registry",
            **registry_attention_fields,
            **lifecycle_fields,
        )
    if classification in blocking_classifications:
        return attention_item(
            goal_id=goal_id,
            status=classification,
            waiting_on="user_or_controller",
            severity="high",
            recommended_action=action,
            source="latest_run",
            **attention_fields,
            **lifecycle_fields,
        )
    if classification in user_or_controller_classifications:
        return attention_item(
            goal_id=goal_id,
            status=classification,
            waiting_on="user_or_controller",
            severity="action",
            recommended_action=action,
            source="latest_run",
            **attention_fields,
            **lifecycle_fields,
        )
    if classification in codex_ready_classifications:
        return attention_item(
            goal_id=goal_id,
            status=classification,
            waiting_on="codex",
            severity="action",
            recommended_action=action,
            source="latest_run",
            **attention_fields,
            **lifecycle_fields,
        )
    if adapter_status in connected_delivery_adapter_statuses:
        return attention_item(
            goal_id=goal_id,
            status=classification,
            waiting_on="codex",
            severity="action",
            recommended_action=action,
            source="latest_run",
            **attention_fields,
            **lifecycle_fields,
        )
    if run_has_external_evidence_watch_signal(current_run):
        return attention_item(
            goal_id=goal_id,
            status=classification,
            waiting_on="external_evidence",
            severity="watch",
            recommended_action=action,
            source="latest_run",
            **attention_fields,
            **lifecycle_fields,
        )
    if adapter_status in connected_adapter_statuses:
        return attention_item(
            goal_id=goal_id,
            status=classification,
            waiting_on="codex",
            severity="action",
            recommended_action=action,
            source="latest_run",
            **attention_fields,
            **lifecycle_fields,
        )
    return None
