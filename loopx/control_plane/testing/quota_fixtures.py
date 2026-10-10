from __future__ import annotations

from typing import Any

from ..todos.todo_summary import compact_todo_group


DEFAULT_FIXTURE_QUOTA = {
    "compute": 1.0,
    "window_hours": 24,
    "slot_minutes": 1,
    "allowed_slots": 10,
    "spent_slots": 0,
    "state": "eligible",
    "reason": "eligible fixture",
}


def quota_todo_item(
    *,
    todo_id: str,
    index: int = 1,
    role: str = "agent",
    status: str = "open",
    task_class: str = "advancement_task",
    priority: str = "P0",
    title: str | None = None,
    text: str | None = None,
    claimed_by: str | None = None,
    blocks_agent: str | None = None,
    action_kind: str | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    if text is None:
        if title is None:
            raise ValueError("quota_todo_item requires title or text")
        text = f"[{priority}] {title}"
    if title is None:
        title = text
    item: dict[str, Any] = {
        "schema_version": "todo_item_v0",
        "todo_id": todo_id,
        "index": index,
        "text": text,
        "title": title,
        "priority": priority,
        "role": role,
        "status": status,
        "done": status == "done",
        "task_class": task_class,
        "source_section": "Agent Todo" if role == "agent" else "User Todo",
    }
    if claimed_by:
        item["claimed_by"] = claimed_by
    if blocks_agent:
        item["blocks_agent"] = blocks_agent
    if action_kind:
        item["action_kind"] = action_kind
    item.update(metadata)
    return item


def quota_todo_summary(
    items: list[dict[str, Any]],
    *,
    role: str = "agent",
    claim_scope_agent_id: str | None = None,
    item_limit: int | None = None,
    include_task_orchestration_authority: bool = False,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    source_section = "Agent Todo" if role == "agent" else "User Todo"
    summary = compact_todo_group(
        items,
        source_section=source_section,
        role=role,
        item_limit=item_limit,
        include_task_orchestration_authority=include_task_orchestration_authority,
        evaluated_at=evaluated_at,
    )
    if summary is None:
        summary = {
            "schema_version": "todo_summary_v0",
            "source_section": source_section,
            "total_count": 0,
            "open_count": 0,
            "done_count": 0,
            "first_open_items": [],
            "first_executable_items": [],
            "executable_backlog_items": [],
            "monitor_open_items": [],
            "monitor_due_items": [],
            "monitor_due_count": 0,
        }
    if claim_scope_agent_id:
        from ..todos.quota_summary import summarize_project_asset_todos_for_quota

        scoped = summarize_project_asset_todos_for_quota(
            summary,
            agent_identity={"agent_id": claim_scope_agent_id},
        )
        if scoped is None:
            raise RuntimeError("agent-scoped Todo fixture projection is unavailable")
        summary["work_counts"] = scoped["work_counts"]
        summary["claim_scope"] = {"agent_id": claim_scope_agent_id}
    return summary


def quota_status_payload(
    *,
    goal_id: str,
    status: str,
    agent_todo_items: list[dict[str, Any]] | None = None,
    recommended_action: str,
    next_action: str | None = None,
    agent_todos: dict[str, Any] | None = None,
    user_todo_items: list[dict[str, Any]] | None = None,
    user_todos: dict[str, Any] | None = None,
    quota_state: str = "eligible",
    quota_extra: dict[str, Any] | None = None,
    safe_bypass: bool = False,
    source: str = "project_asset",
    waiting_on: str | None = None,
    active_state_next_action: str | None = None,
    project_asset_source: str | None = None,
    coordination: dict[str, Any] | None = None,
    latest_runs: list[dict[str, Any]] | None = None,
    post_handoff_latest_run: dict[str, Any] | None = None,
    claim_scope_agent_id: str | None = None,
    registry_status: str | None = None,
    goal_extra: dict[str, Any] | None = None,
    item_extra: dict[str, Any] | None = None,
    project_asset_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    agent_todo_items = agent_todo_items or []
    agent_todos = agent_todos or quota_todo_summary(
        agent_todo_items,
        role="agent",
        claim_scope_agent_id=claim_scope_agent_id,
    )
    user_todos = user_todos or quota_todo_summary(
        user_todo_items or [],
        role="user",
    )
    quota = dict(DEFAULT_FIXTURE_QUOTA)
    quota["state"] = quota_state
    if quota_state != "eligible":
        quota["reason"] = f"{quota_state} fixture"
    if quota_extra:
        quota.update(quota_extra)
    if safe_bypass:
        quota.update(
            {
                "safe_bypass_allowed": True,
                "safe_bypass_kind": "scoped_user_gate_fallback",
                "safe_bypass_policy": "fixture safe bypass",
            }
        )
    action = next_action if next_action is not None else recommended_action
    item: dict[str, Any] = {
        "goal_id": goal_id,
        "status": status,
        "waiting_on": waiting_on
        or ("controller" if quota_state == "operator_gate" else "codex"),
        "severity": "info",
        "source": source,
        "recommended_action": recommended_action,
        "quota": quota,
        "agent_todos": agent_todos,
        "user_todos": user_todos,
        "project_asset": {
            "next_action": action,
            "stop_condition": "stop on private material",
            "agent_todos": agent_todos,
            "user_todos": user_todos,
        },
    }
    if active_state_next_action:
        item["active_state_next_action"] = active_state_next_action
    if project_asset_source:
        item["project_asset_source"] = project_asset_source
    if project_asset_extra:
        item["project_asset"].update(project_asset_extra)
    if item_extra:
        item.update(item_extra)
    if post_handoff_latest_run:
        item["handoff_readiness"] = {
            "post_handoff_run_seen": True,
            "handoff_status": "post_handoff_run_seen",
            "post_handoff_latest_run": post_handoff_latest_run,
        }
    if coordination:
        item["coordination"] = coordination

    goal: dict[str, Any] = {
        "id": goal_id,
        "registry_member": True,
        "status": registry_status or status,
        "adapter_kind": "harness_self_improvement",
        "adapter_status": "connected-read-only",
        "quota": {
            "compute": quota.get("compute", 1.0),
            "window_hours": quota.get("window_hours", 24),
            "slot_minutes": quota.get("slot_minutes", 1),
            "allowed_slots": quota.get("allowed_slots", 10),
        },
    }
    if coordination:
        goal["coordination"] = coordination
    goal["latest_runs"] = latest_runs if latest_runs is not None else []
    if goal_extra:
        goal.update(goal_extra)

    return {
        "ok": True,
        "attention_queue": {"items": [item]},
        "run_history": {"goals": [goal]},
    }
