from __future__ import annotations

from typing import Any, Callable, Optional

from ...boundary_authority import checkpointed_boundary_authority_summary
from ...orchestration import compact_orchestration_policy
from .run_context_retention import (
    compact_goal_semantic_history,
    latest_runs_with_agent_context,
)


LatestRun = Callable[[dict[str, Any]], Optional[dict[str, Any]]]
GoalLifecycleFields = Callable[[dict[str, Any], Optional[dict[str, Any]]], dict[str, Any]]
GoalProjection = Callable[[dict[str, Any]], Optional[dict[str, Any]]]
RunCompactor = Callable[[dict[str, Any]], dict[str, Any]]
QuotaStatus = Callable[[dict[str, Any]], dict[str, Any]]
StatusNeutralRun = Callable[[dict[str, Any]], bool]


def latest_run(
    goal: dict[str, Any],
    *,
    is_status_neutral_run: StatusNeutralRun,
) -> dict[str, Any] | None:
    status_run = goal.get("latest_status_run")
    if isinstance(status_run, dict) and not is_status_neutral_run(status_run):
        return status_run

    runs = goal.get("latest_runs")
    if not isinstance(runs, list) or not runs:
        return None
    for run in runs:
        if not isinstance(run, dict):
            continue
        if is_status_neutral_run(run):
            continue
        return run
    return None


def compact_goal_coordination(coordination: Any) -> dict[str, Any] | None:
    if not isinstance(coordination, dict):
        return None
    compact = dict(coordination)
    authority_summary = checkpointed_boundary_authority_summary(coordination)
    if authority_summary is not None:
        compact["checkpointed_boundary_authority"] = {
            key: value
            for key, value in authority_summary.items()
            if key != "entries"
        }
    elif "checkpointed_boundary_authority" in compact:
        compact.pop("checkpointed_boundary_authority", None)
    return compact


def build_run_history(
    history: dict[str, Any],
    *,
    latest_run: LatestRun,
    goal_lifecycle_fields: GoalLifecycleFields,
    subagent_activity_for_goal: GoalProjection,
    compact_run: RunCompactor,
    quota_status: QuotaStatus,
    display_limit: int | None = None,
    recent_run_limit: int | None = None,
    include_goal_subagent_configuration: bool = False,
) -> dict[str, Any]:
    display_limit = None if display_limit is None else max(0, display_limit)
    goals: list[dict[str, Any]] = []
    for goal in history.get("goals") or []:
        if not isinstance(goal, dict):
            continue
        current_run = latest_run(goal)
        lifecycle_fields = goal_lifecycle_fields(goal, current_run)
        subagent_activity = subagent_activity_for_goal(goal)
        latest_runs = [
            compact_run(run)
            for run in goal.get("latest_runs") or []
            if isinstance(run, dict)
        ]
        if display_limit is not None:
            latest_runs = latest_runs_with_agent_context(
                latest_runs,
                limit=display_limit,
            )
        semantic_history = compact_goal_semantic_history(
            goal.get("semantic_history"),
            compact_run=compact_run,
        )
        goals.append(
            {
                "id": goal.get("id"),
                "activation_state": goal.get("activation_state") or "active",
                "registry_goal_digest": goal.get("registry_goal_digest"),
                "display_name": goal.get("display_name"),
                "domain": goal.get("domain"),
                "status": goal.get("status"),
                "lifecycle_phase": lifecycle_fields["lifecycle_phase"],
                "lifecycle_flags": lifecycle_fields["lifecycle_flags"],
                "registry_member": goal.get("registry_member"),
                "legacy_runtime_goal": goal.get("legacy_runtime_goal"),
                "adapter_kind": goal.get("adapter_kind"),
                "adapter_status": goal.get("adapter_status"),
                "coordination": compact_goal_coordination(goal.get("coordination")),
                "explore_graph": goal.get("explore_graph")
                if isinstance(goal.get("explore_graph"), dict)
                else None,
                "guards": goal.get("guards") if isinstance(goal.get("guards"), list) else [],
                "next_probe": goal.get("next_probe"),
                "authority_registry": goal.get("authority_registry"),
                "quota": quota_status(goal) if goal.get("registry_member") else None,
                "index_digest": goal.get("index_digest"),
                "index_exists": goal.get("index_exists"),
                "raw_index_records": goal.get("raw_index_records"),
                "unique_runs": goal.get("unique_runs"),
                "subagent_activity": subagent_activity,
                "latest_status_run": compact_run(current_run) if current_run else None,
                "latest_runs": latest_runs,
            }
        )
        if include_goal_subagent_configuration:
            goals[-1]["spawn_policy"] = (
                compact_orchestration_policy(goal.get("spawn_policy"))
                if isinstance(goal.get("spawn_policy"), dict)
                else None
            )
        if semantic_history is not None:
            goals[-1]["semantic_history"] = semantic_history

    recent_runs = [
        compact_run(run)
        for run in history.get("runs") or []
        if isinstance(run, dict)
    ]
    recent_limit = recent_run_limit if recent_run_limit is not None else display_limit
    if recent_limit is not None:
        recent_runs = recent_runs[: max(0, recent_limit)]
    return {
        "available": True,
        "goal_count": history.get("goal_count"),
        "run_count": history.get("run_count"),
        "goals": goals,
        "recent_runs": recent_runs,
    }
