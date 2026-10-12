"""Agent-lane status projection inside the `status` bounded context."""

from __future__ import annotations

from typing import Any

AGENT_LANE_STATUS_PAYLOAD_COMPACTION_SCHEMA_VERSION = (
    "agent_lane_status_payload_compaction_v0"
)
AGENT_LANE_STATUS_FRONTIER_COMPACTION_SCHEMA_VERSION = (
    "agent_lane_goal_frontier_compaction_v0"
)
AGENT_LANE_STATUS_HISTORY_COMPACTION_SCHEMA_VERSION = (
    "agent_lane_run_history_compaction_v0"
)
AGENT_LANE_STATUS_MANAGEMENT_COMPACTION_SCHEMA_VERSION = (
    "agent_lane_management_compaction_v0"
)
AGENT_LANE_STATUS_GOAL_CHANNEL_COMPACTION_SCHEMA_VERSION = (
    "agent_lane_goal_channel_compaction_v0"
)

_PROJECT_ASSET_CANONICAL_FIELDS = (
    "active_state_next_action",
    "agent_interaction_summary",
    "agent_lane_frontier_hint",
    "agent_lane_next_action",
    "agent_lane_recommendation",
    "agent_member",
    "agent_reward_memory",
    "autonomous_replan_ack",
    "autonomous_replan_obligation",
    "bounded_research_frontier",
    "completed_todo_archive_warning",
    "control_plane",
    "external_progress_review",
    "goal_frontier_projection",
    "latest_run_recommended_action",
    "latest_run_recommended_action_source",
    "next_action_projection_warning",
    "stale_latest_run_warning",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _compact_run(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = {
        key: value[key]
        for key in (
            "goal_id",
            "agent_id",
            "classification",
            "generated_at",
            "lifecycle_phase",
            "lifecycle_flags",
        )
        if value.get(key) is not None
    }
    recommendation = str(value.get("recommended_action") or "").strip()
    if recommendation:
        compact["recommended_action"] = (
            recommendation
            if len(recommendation) <= 240
            else recommendation[:237].rstrip() + "..."
        )
    return compact or None


def _agent_semantic_goal(value: Any, *, agent_id: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    semantic_history = value.get("semantic_history")
    if not isinstance(semantic_history, dict):
        return None
    contexts = semantic_history.get("agents")
    current_context = (
        next(
            (
                context
                for context in contexts
                if isinstance(context, dict)
                and str(context.get("agent_id") or "").strip() == agent_id
            ),
            None,
        )
        if isinstance(contexts, list)
        else None
    )
    owner_correction = semantic_history.get("latest_owner_correction_run")
    if not isinstance(current_context, dict) and not isinstance(
        owner_correction,
        dict,
    ):
        return None
    compact_history: dict[str, Any] = {
        "schema_version": semantic_history.get("schema_version"),
        "agents": [current_context] if isinstance(current_context, dict) else [],
    }
    if isinstance(owner_correction, dict):
        compact_history["latest_owner_correction_run"] = owner_correction
    return {
        "id": value.get("id"),
        "semantic_history": compact_history,
    }


def _compact_run_history(payload: dict[str, object], *, agent_id: str) -> bool:
    history = payload.get("run_history")
    if not isinstance(history, dict):
        return False
    recent_runs = history.get("recent_runs")
    visible_runs = recent_runs if isinstance(recent_runs, list) else []
    agent_runs = [
        run
        for run in visible_runs
        if isinstance(run, dict) and str(run.get("agent_id") or "").strip() == agent_id
    ]
    goals = history.get("goals")
    semantic_goals = [
        compact_goal
        for compact_goal in (
            _agent_semantic_goal(goal, agent_id=agent_id)
            for goal in (goals if isinstance(goals, list) else [])
        )
        if compact_goal
    ]
    compact = {
        key: history[key]
        for key in ("available", "run_count", "goal_count")
        if history.get(key) is not None
    }
    latest_agent_run = _compact_run(agent_runs[0]) if agent_runs else None
    if latest_agent_run:
        compact["latest_agent_run"] = latest_agent_run
    if semantic_goals:
        compact["goals"] = semantic_goals
    compact["payload_compaction"] = {
        "schema_version": AGENT_LANE_STATUS_HISTORY_COMPACTION_SCHEMA_VERSION,
        "agent_id": agent_id,
        "visible_agent_run_count": len(agent_runs),
        "omitted_visible_run_count": max(
            0,
            len(visible_runs) - int(latest_agent_run is not None),
        ),
        "visible_semantic_goal_count": len(semantic_goals),
        "full_detail_cold_path": "status without --agent-id or history",
    }
    payload["run_history"] = compact
    return True


def _compact_agent_management(payload: dict[str, object], *, agent_id: str) -> bool:
    projection = payload.get("agent_management_projection")
    if not isinstance(projection, dict):
        return False
    agents = projection.get("agents")
    visible_agents = agents if isinstance(agents, list) else []
    current_agents = [
        agent
        for agent in visible_agents
        if isinstance(agent, dict)
        and str(agent.get("agent_id") or "").strip() == agent_id
    ]
    compact = {
        key: projection[key]
        for key in (
            "schema_version",
            "generated_at",
            "goal_id",
            "mode",
            "source_summary",
            "truth_contract",
        )
        if projection.get(key) is not None
    }
    compact["agents"] = current_agents[:1]
    compact["payload_compaction"] = {
        "schema_version": AGENT_LANE_STATUS_MANAGEMENT_COMPACTION_SCHEMA_VERSION,
        "agent_id": agent_id,
        "visible_agent_count": len(visible_agents),
        "omitted_other_agent_count": max(0, len(visible_agents) - len(current_agents)),
        "full_detail_cold_path": "status without --agent-id",
    }
    payload["agent_management_projection"] = compact
    return True


def _compact_vision_audit(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = {
        key: value[key]
        for key in (
            "schema_version",
            "agent_id",
            "required",
            "decision",
            "trigger_count",
            "trigger_kinds",
            "selected_todo_is_goal_completion",
            "closeout_allowed_without_evidence",
            "recommended_action",
            "outcome_checkpoint_diagnostics",
        )
        if value.get(key) is not None
    }
    judge = value.get("vision_gap_judge")
    if isinstance(judge, dict):
        compact["vision_gap_judge"] = {
            key: judge[key]
            for key in (
                "schema_version",
                "goal_id",
                "agent_id",
                "done",
                "decision",
                "reason",
            )
            if judge.get(key) is not None
        }
    for key in (
        "acceptance_requirements",
        "authoritative_evidence_kinds",
        "required_before_closeout",
    ):
        items = value.get(key)
        if isinstance(items, list):
            compact[f"{key}_count"] = len(items)
    return compact


def _compact_goal_frontier(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = {
        key: value[key]
        for key in (
            "schema_version",
            "goal_id",
            "agent_id",
            "source",
            "replan_required",
            "remaining_advancement_frontier",
            "deferred_successors",
            "monitor_only_lanes",
            "normalized_progress",
            "vision_wait_state",
            "autonomy_blockers",
        )
        if value.get(key) is not None
    }
    acceptance_gaps = value.get("acceptance_gaps")
    if isinstance(acceptance_gaps, list):
        compact["acceptance_gap_count"] = len(acceptance_gaps)
        compact["acceptance_gaps"] = [
            {
                key: gap[key]
                for key in (
                    "kind",
                    "state",
                    "source",
                    "agent_id",
                    "replan_trigger_source",
                    "reason_code",
                    "component_checks",
                    "resolution_hint",
                )
                if gap.get(key) is not None
            }
            for gap in acceptance_gaps[:1]
            if isinstance(gap, dict)
        ]
    vision_audit = _compact_vision_audit(value.get("vision_continuation_audit"))
    if vision_audit:
        compact["vision_continuation_audit"] = vision_audit
    compact["payload_compaction"] = {
        "schema_version": AGENT_LANE_STATUS_FRONTIER_COMPACTION_SCHEMA_VERSION,
        "full_detail_cold_path": "status without --agent-id or quota should-run",
    }
    return compact


def _compact_goal_channel(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    omitted_fields = (
        "agent_todos",
        "user_todos",
        "recent_events",
        "active_leases",
        "source_warnings",
    )
    compact = {key: child for key, child in value.items() if key not in omitted_fields}
    compact["payload_compaction"] = {
        "schema_version": AGENT_LANE_STATUS_GOAL_CHANNEL_COMPACTION_SCHEMA_VERSION,
        **{
            f"omitted_{key[:-1] if key.endswith('s') else key}_count": len(
                value.get(key) or []
            )
            for key in omitted_fields
        },
        "canonical_todo_path": "attention_queue.items[].{agent_todos,user_todos}",
        "full_detail_cold_path": "status without --agent-id",
    }
    return compact


def _compact_attention_item(item: dict[str, Any]) -> tuple[int, bool]:
    project_asset = _as_dict(item.get("project_asset"))
    removed_duplicates = 0
    if project_asset:
        item_frontier = item.get("goal_frontier_projection")
        asset_frontier = project_asset.get("goal_frontier_projection")
        if item_frontier is not None and item_frontier == asset_frontier:
            item.pop("goal_frontier_projection")
            removed_duplicates += 1
        frontier = _compact_goal_frontier(asset_frontier or item_frontier)
        if frontier:
            project_asset["goal_frontier_projection"] = frontier
        next_action = project_asset.get("next_action")
        for key in ("active_state_next_action", "latest_run_recommended_action"):
            if next_action and project_asset.get(key) == next_action:
                if item.get(key) == next_action:
                    item.pop(key)
                    removed_duplicates += 1
                project_asset.pop(key)
        for key in _PROJECT_ASSET_CANONICAL_FIELDS:
            if key in item and key in project_asset and item[key] == project_asset[key]:
                item.pop(key)
                removed_duplicates += 1
    goal_channel = _compact_goal_channel(item.get("goal_channel_projection"))
    if goal_channel:
        item["goal_channel_projection"] = goal_channel
    return removed_duplicates, bool(goal_channel)


def compact_agent_lane_status_payload_for_display(
    payload: dict[str, object],
    *,
    agent_id: str,
) -> None:
    safe_agent_id = str(agent_id or "").strip()
    if not safe_agent_id:
        return
    queue = payload.get("attention_queue")
    items = queue.get("items") if isinstance(queue, dict) else None
    compacted_items = 0
    removed_duplicates = 0
    compacted_goal_channels = 0
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            removed, channel_compacted = _compact_attention_item(item)
            removed_duplicates += removed
            compacted_goal_channels += int(channel_compacted)
            compacted_items += 1
    history_compacted = _compact_run_history(payload, agent_id=safe_agent_id)
    management_compacted = _compact_agent_management(
        payload,
        agent_id=safe_agent_id,
    )
    if compacted_items or history_compacted or management_compacted:
        payload["agent_lane_status_payload_compaction"] = {
            "schema_version": AGENT_LANE_STATUS_PAYLOAD_COMPACTION_SCHEMA_VERSION,
            "agent_id": safe_agent_id,
            "compacted_attention_item_count": compacted_items,
            "removed_project_asset_duplicate_count": removed_duplicates,
            "compacted_goal_channel_count": compacted_goal_channels,
            "run_history_compacted": history_compacted,
            "agent_management_compacted": management_compacted,
            "reason": (
                "status --agent-id keeps current-agent control semantics hot "
                "and moves whole-goal diagnostics to explicit cold paths"
            ),
        }
