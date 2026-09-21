from __future__ import annotations

from typing import Any

from ..agents.agent_scope import _agent_scope_monitor_blocked_resume_candidates
from ..scheduler.external_evidence_observation import build_external_evidence_poll_signal
from ..todos.contract import next_action_requires_advancement_text
from ..todos.todo_semantics import (
    todo_summary_claim_scope_agent_id,
    todo_summary_first_executable_item,
    todo_summary_monitor_due_count,
    todo_summary_monitor_due_items,
    todo_summary_non_watch_only_monitor_due_items,
    todo_summary_monitor_schedule_gap_count,
    todo_summary_monitor_schedule_gap_items,
    todo_summary_open_task_counts,
    todo_summary_watch_only_monitor_due_items,
)
from .delivery_history import project_delivery_response
from .work_lane import (
    build_work_lane_contract,
    due_monitor_preempts_advancement,
)


DEPENDENCY_OBSERVATION_CLASSIFICATION_HINTS = (
    "dependency_observed",
    "dependency_observation",
    "dependency_monitor",
)
DEFAULT_MONITOR_DUE_ITEM_LIMIT = 1


def latest_run_progress_scope(run: dict[str, Any]) -> str:
    explicit = str(run.get("progress_scope") or "").strip()
    if explicit:
        return explicit
    classification = str(run.get("classification") or "").strip().lower()
    if any(hint in classification for hint in DEPENDENCY_OBSERVATION_CLASSIFICATION_HINTS):
        return "dependency_observation"
    return "primary_goal"


def item_progress_scope(item: dict[str, Any]) -> str:
    explicit = str(item.get("progress_scope") or "").strip()
    if explicit:
        return explicit
    project_asset = item.get("project_asset") if isinstance(item.get("project_asset"), dict) else {}
    project_explicit = str(project_asset.get("progress_scope") or "").strip()
    if project_explicit:
        return project_explicit
    latest_handoff_run = post_handoff_latest_run(item)
    if latest_handoff_run:
        return latest_run_progress_scope(latest_handoff_run)
    return latest_run_progress_scope(
        {
            "classification": item.get("status") or item.get("latest_run_classification"),
            "progress_scope": item.get("latest_run_progress_scope"),
        }
    )


def post_handoff_latest_run(item: dict[str, Any]) -> dict[str, Any]:
    handoff_readiness = (
        item.get("handoff_readiness")
        if isinstance(item.get("handoff_readiness"), dict)
        else {}
    )
    latest_run = (
        handoff_readiness.get("post_handoff_latest_run")
        if isinstance(handoff_readiness.get("post_handoff_latest_run"), dict)
        else {}
    )
    return latest_run


def outcome_followthrough_hint(
    item: dict[str, Any], summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    run = post_handoff_latest_run(item)
    return project_delivery_response(run, summary)["outcome_followthrough"] if run else None


def next_action_requires_advancement(item: dict[str, Any]) -> bool:
    project_asset = item.get("project_asset") if isinstance(item.get("project_asset"), dict) else {}
    values = (
        project_asset.get("next_action"),
        project_asset.get("recommended_action"),
        item.get("next_action"),
        item.get("recommended_action"),
    )
    text = " ".join(str(value or "") for value in values if str(value or "").strip())
    return next_action_requires_advancement_text(text)


def build_work_lane_context_contract(
    item: dict[str, Any],
    *,
    agent_todo_summary: dict[str, Any] | None,
    monitor_due_item_limit: int = DEFAULT_MONITOR_DUE_ITEM_LIMIT,
    monitor_attempt_already_recorded: bool = False,
    monitor_debt_backoff_active: bool = False,
    advancement_allowed: bool = True,
) -> dict[str, Any] | None:
    progress_scope = item_progress_scope(item)
    external_poll_signal = build_external_evidence_poll_signal(
        item,
        agent_todo_summary=agent_todo_summary,
    )
    todo_counts = todo_summary_open_task_counts(agent_todo_summary)
    if not advancement_allowed:
        todo_counts = {**todo_counts, "advancement": 0}
    due_monitor_items = todo_summary_monitor_due_items(agent_todo_summary)
    due_monitor_count = todo_summary_monitor_due_count(
        agent_todo_summary,
        due_items=due_monitor_items,
    )
    watch_only_due_monitor_items = todo_summary_watch_only_monitor_due_items(
        agent_todo_summary
    )
    non_watch_only_due_monitor_items = (
        todo_summary_non_watch_only_monitor_due_items(agent_todo_summary)
    )
    if not advancement_allowed and due_monitor_count <= 0:
        # In monitor-only mode, an action phrase such as "observe result" must
        # not bypass the monitor todo's explicit cadence window.
        external_poll_signal = None
    monitor_schedule_gap_items = todo_summary_monitor_schedule_gap_items(agent_todo_summary)
    monitor_schedule_gap_count = todo_summary_monitor_schedule_gap_count(
        agent_todo_summary,
        gap_items=monitor_schedule_gap_items,
    )
    first_preemptive_due_monitor = (
        non_watch_only_due_monitor_items[0]
        if non_watch_only_due_monitor_items
        else None
    )
    first_advancement = (
        todo_summary_first_executable_item(agent_todo_summary)
        if advancement_allowed
        else None
    )
    agent_id = todo_summary_claim_scope_agent_id(agent_todo_summary or {})
    monitor_blocked_resume_candidates = (
        _agent_scope_monitor_blocked_resume_candidates(
            agent_todo_summary,
            agent_id=agent_id,
        )
        if advancement_allowed
        else []
    )
    return build_work_lane_contract(
        progress_scope=progress_scope,
        external_poll_signal=bool(external_poll_signal),
        todo_counts=todo_counts,
        monitor_due_count=due_monitor_count,
        due_monitor_items=due_monitor_items,
        watch_only_due_monitor_items=watch_only_due_monitor_items,
        non_watch_only_due_monitor_items=non_watch_only_due_monitor_items,
        monitor_schedule_gap_count=monitor_schedule_gap_count,
        monitor_schedule_gap_items=monitor_schedule_gap_items,
        first_advancement=first_advancement,
        due_monitor_preempts_advancement=due_monitor_preempts_advancement(
            first_preemptive_due_monitor,
            first_advancement=first_advancement,
        ),
        outcome_followthrough=(
            outcome_followthrough_hint(item, agent_todo_summary) if advancement_allowed else None
        ),
        next_action_requires_advancement=(
            next_action_requires_advancement(item) if advancement_allowed else False
        ),
        monitor_due_item_limit=monitor_due_item_limit,
        resume_blocked_by_monitor_count=len(monitor_blocked_resume_candidates),
        resume_blocked_by_monitor_items=monitor_blocked_resume_candidates,
        monitor_attempt_already_recorded=monitor_attempt_already_recorded,
        monitor_debt_backoff_active=monitor_debt_backoff_active,
    )
