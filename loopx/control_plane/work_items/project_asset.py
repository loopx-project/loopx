"""Project-asset normalization, active-state attachment, and handoff projection."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import Literal

from ..runtime.public_safety import (
    LOCAL_PATH_SURFACE_PATTERN,
    SECRET_LIKE_SURFACE_PATTERN,
    compact_text as _compact_text,
    public_safe_compact_text as _runtime_public_safe_compact_text,
)
from .autonomous_replan_obligation import run_history_agent_id
from .project_asset_todo_summary import (
    PROJECT_ASSET_TODO_DISPLAY_FIELDS as PROJECT_ASSET_TODO_DISPLAY_FIELDS,
    PROJECT_ASSET_TODO_PROJECTION_GAP_SCHEMA_VERSION as PROJECT_ASSET_TODO_PROJECTION_GAP_SCHEMA_VERSION,
    TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION as TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION,
    TODO_PROJECTION_VIEW_SCHEMA_VERSION as TODO_PROJECTION_VIEW_SCHEMA_VERSION,
    _optional_int as _optional_int,
    _project_asset_display_todo_item as _project_asset_display_todo_item,
    _project_asset_display_todo_items as _project_asset_display_todo_items,
    build_project_asset_todo_summary as build_project_asset_todo_summary,
    project_asset_todo_projection_gap as project_asset_todo_projection_gap,
    project_asset_todo_projection_metadata as project_asset_todo_projection_metadata,
)


PROJECT_ASSET_HANDOFF_STATE_TRACE_CHECK_KEYS: tuple[str, ...] = (
    "project_asset_backed",
    "same_source_should_run",
    "handoff_has_next_action",
    "handoff_has_stop_condition",
    "handoff_sanitized_surface",
)

SupportMode = Literal["reward_capture", "decision_support", "read_only_observer", "selective_assist"]

_ACTIVE_STATE_WARNING_KEYS: tuple[str, ...] = (
    "backlog_hygiene_warning",
    "state_projection_gap",
    "completed_todo_archive_warning",
    "state_incomplete",
)


def project_asset_public_safe_compact_text(value: object, *, limit: int = 220) -> str | None:
    return _runtime_public_safe_compact_text(value, limit=limit)


def project_asset_owner(
    waiting_on: str,
    *,
    monitor_signal_waiting_on: str,
) -> str:
    if waiting_on in {"codex", "external_evidence", "controller", "user_or_controller"}:
        return waiting_on
    if waiting_on == monitor_signal_waiting_on:
        return monitor_signal_waiting_on
    return waiting_on or "unknown"


def project_asset_gate(
    *,
    waiting_on: str,
    operator_question: str | None,
    missing_gates: list[str] | None,
    status: str,
    monitor_signal_waiting_on: str,
) -> str:
    if operator_question:
        return "operator_question"
    if missing_gates:
        return str(missing_gates[0])
    if waiting_on in {"user_or_controller", "controller"}:
        return status or waiting_on
    if waiting_on == "external_evidence":
        return "external_evidence"
    if waiting_on == monitor_signal_waiting_on:
        return "none"
    return "none"


def project_asset_stop_condition(
    *,
    waiting_on: str,
    next_handoff_condition: str | None,
    agent_command: str | None,
    monitor_signal_waiting_on: str,
    monitor_display_stop_condition: str,
) -> str:
    if next_handoff_condition:
        return next_handoff_condition
    if waiting_on == "user_or_controller":
        return "stop until the user or controller decision is recorded"
    if waiting_on == "controller":
        return "stop until the controller or owner resolves this gate"
    if waiting_on == "external_evidence":
        return "stop until external evidence changes"
    if waiting_on == monitor_signal_waiting_on:
        return monitor_display_stop_condition
    if agent_command:
        return "stop if the command fails or needs write, production, or additional approval"
    return "stop if the next action needs reward, gate approval, write control, or production access"


def project_asset_support_mode(
    *,
    waiting_on: str,
    operator_question: str | None,
    missing_gates: list[str] | None,
    status: str,
    recommended_action: str,
    agent_command: str | None,
    monitor_signal_waiting_on: str,
) -> SupportMode:
    surface = " ".join(
        str(value or "")
        for value in (status, recommended_action, agent_command, " ".join(missing_gates or []))
    ).lower()
    if "reward" in surface:
        return "reward_capture"
    if operator_question or missing_gates or waiting_on in {"user_or_controller", "controller"}:
        return "decision_support"
    if waiting_on in {"external_evidence", monitor_signal_waiting_on}:
        return "read_only_observer"
    if agent_command or waiting_on == "codex":
        return "selective_assist"
    return "read_only_observer"


def project_asset_quota_summary(quota: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(quota, dict):
        return None
    summary: dict[str, object] = {
        "compute": quota.get("compute"),
        "state": quota.get("state"),
        "spent_slots": quota.get("spent_slots"),
        "allowed_slots": quota.get("allowed_slots"),
    }
    if quota.get("reason"):
        summary["reason"] = _compact_text(str(quota.get("reason") or ""), limit=220)
    return summary


def project_asset_quota_state(
    *,
    quota: dict[str, object] | None,
    project_asset: dict[str, object],
) -> str | None:
    quota_state = ""
    if isinstance(quota, dict):
        quota_state = str(quota.get("state") or "").strip()
    if not quota_state:
        asset_quota = project_asset.get("quota")
        if isinstance(asset_quota, dict):
            quota_state = str(asset_quota.get("state") or "").strip()
    return quota_state or None


def project_asset_user_todo_open_count(
    *,
    user_todos: dict[str, object] | None,
    project_asset: dict[str, object],
) -> int | None:
    if isinstance(user_todos, dict):
        return _optional_int(user_todos.get("open_count"))
    asset_user_todos = project_asset.get("user_todos")
    if isinstance(asset_user_todos, dict):
        return _optional_int(asset_user_todos.get("open_count"))
    return None


def project_asset_latest_validation(run: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(run, dict):
        return None
    signal: dict[str, object] = {}
    for field in ("generated_at", "classification"):
        value = run.get(field)
        if value:
            signal[field] = value
    summary = run.get("health_check") or run.get("recommended_action")
    if summary:
        signal["summary"] = _compact_text(str(summary), limit=260)
    return signal or None


def enrich_project_asset(
    item: dict[str, object],
    *,
    user_todos: dict[str, object] | None,
    agent_todos: dict[str, object] | None,
    quota: dict[str, object] | None,
    latest_validation: dict[str, object] | None,
    latest_runs: list[dict[str, object]] | None,
    execution_profile: dict[str, object] | None,
    orchestration: dict[str, object] | None,
    subagent_activity: dict[str, object] | None,
    interface_budget_cadence: dict[str, object] | None,
    project_asset_todo_summary: Callable[..., dict[str, object] | None],
    project_asset_todo_projection_gap: Callable[..., dict[str, object] | None],
    project_asset_quota_summary: Callable[[dict[str, object] | None], dict[str, object] | None],
    compact_execution_profile: Callable[[dict[str, object] | None], dict[str, object]],
    compact_orchestration_policy: Callable[[dict[str, object]], dict[str, object]],
    project_asset_handoff_readiness: Callable[..., dict[str, object] | None],
    project_asset_quota_state: Callable[..., str | None],
    project_asset_user_todo_open_count: Callable[..., int | None],
    build_long_task_cadence_hint: Callable[..., dict[str, object]],
) -> None:
    project_asset = item.get("project_asset")
    if not isinstance(project_asset, dict):
        return
    user_summary = project_asset_todo_summary(user_todos, role="user")
    if user_summary:
        project_asset["user_todos"] = user_summary
    agent_summary = project_asset_todo_summary(agent_todos, role="agent")
    if agent_summary:
        project_asset["agent_todos"] = agent_summary
    todo_projection_gap = project_asset_todo_projection_gap(
        user_todos=user_todos,
        agent_todos=agent_todos,
    )
    if todo_projection_gap:
        project_asset["todo_projection_gap"] = todo_projection_gap
        item["todo_projection_gap"] = todo_projection_gap
    else:
        project_asset.pop("todo_projection_gap", None)
        item.pop("todo_projection_gap", None)
    quota_summary = project_asset_quota_summary(quota)
    if quota_summary:
        project_asset["quota"] = quota_summary
    if execution_profile is not None:
        project_asset["execution_profile"] = compact_execution_profile(execution_profile)
    if orchestration is not None:
        project_asset["orchestration"] = compact_orchestration_policy(orchestration)
    if subagent_activity:
        project_asset["subagent_activity"] = subagent_activity
    if interface_budget_cadence:
        project_asset["interface_budget_cadence"] = interface_budget_cadence
    if latest_validation:
        project_asset["latest_validation"] = latest_validation
    readiness = project_asset_handoff_readiness(item, latest_runs=latest_runs)
    if readiness:
        item["handoff_readiness"] = readiness
    quota_state = project_asset_quota_state(quota=quota, project_asset=project_asset)
    user_todo_open_count = project_asset_user_todo_open_count(
        user_todos=user_todos,
        project_asset=project_asset,
    )
    raw_profile = project_asset.get("execution_profile")
    cadence_hint = build_long_task_cadence_hint(
        execution_profile=raw_profile if isinstance(raw_profile, dict) else None,
        latest_runs=latest_runs,
        handoff_readiness=readiness,
        quota_state=quota_state,
        user_todo_open_count=user_todo_open_count,
    )
    project_asset["long_task_cadence_hint"] = cadence_hint
    item["long_task_cadence_hint"] = cadence_hint


def attach_active_state_project_asset_fields(
    item: dict[str, object],
    *,
    latest_runs: list[dict[str, object]] | None = None,
    next_action_projection_warning: Callable[..., dict[str, object] | None] | None = None,
    autonomous_replan_obligation_from_runs: Callable[..., dict[str, object] | None] | None = None,
    external_progress_review: dict[str, object] | None = None,
    effective_turn_cadence: dict[str, object] | None = None,
    agent_lane_id: str | None = None,
) -> dict[str, object]:
    project_asset = item.get("project_asset")
    if not isinstance(project_asset, dict):
        return {}

    attached: dict[str, object] = {}
    if effective_turn_cadence is not None and autonomous_replan_obligation_from_runs is not None:
        autonomous_replan_obligation_from_runs = partial(
            autonomous_replan_obligation_from_runs, effective_turn_cadence=effective_turn_cadence,
        )
    if isinstance(external_progress_review, dict):
        review_summary = external_progress_review.get("summary")
        if isinstance(review_summary, dict):
            item["external_progress_review"] = review_summary
            project_asset["external_progress_review"] = review_summary
            attached["external_progress_review"] = review_summary
        if autonomous_replan_obligation_from_runs is not None:
            autonomous_replan_obligation_from_runs = partial(
                autonomous_replan_obligation_from_runs,
                external_progress_review=external_progress_review,
            )
    active_next_action = item.get("active_state_next_action")
    if active_next_action:
        project_asset["active_state_next_action"] = active_next_action
        attached["active_state_next_action"] = active_next_action

    raw_issue_meta = item.get("issue_meta_surface")
    if isinstance(raw_issue_meta, dict) and raw_issue_meta:
        project_asset["issue_meta_surface"] = raw_issue_meta
        attached["issue_meta_surface"] = raw_issue_meta

    if next_action_projection_warning is not None:
        warning = next_action_projection_warning(
            active_state_next_action=active_next_action,
            latest_run_recommended_action=item.get("latest_run_recommended_action"),
        )
        if warning:
            item["next_action_projection_warning"] = warning
            project_asset["next_action_projection_warning"] = warning
            attached["next_action_projection_warning"] = warning

    for key in _ACTIVE_STATE_WARNING_KEYS:
        value = item.get(key)
        if isinstance(value, dict) and value:
            project_asset[key] = value
            attached[key] = value

    raw_replan = item.get("autonomous_replan_obligation")
    replan_obligation: dict[str, object] | None = raw_replan if isinstance(raw_replan, dict) else None
    if replan_obligation is None and autonomous_replan_obligation_from_runs is not None:
        shared_todos = item.get("agent_todos")
        monitor_todos: dict[str, object] = {
            "monitor_open_items": shared_todos.get("monitor_open_items", []) if isinstance(shared_todos, dict) else [],
        }
        # Status retains shared Goal history for ownership and health checks.
        # A lane caller only needs that Agent's semantic review obligations;
        # peer receipts must not become a prerequisite for its settlement.
        agent_ids = (
            [agent_lane_id]
            if agent_lane_id is not None
            else sorted(
                {
                    agent_id
                    for run in latest_runs or []
                    if isinstance(run, dict)
                    if (agent_id := run_history_agent_id(run)) is not None
                }
            )
        )
        obligations_by_agent = {
            agent_id: obligation
            for agent_id in agent_ids
            if (
                obligation := autonomous_replan_obligation_from_runs(
                    latest_runs or [],
                    # The TS history owner matches monitor intent by Todo,
                    # target and lane. Share those facts without importing
                    # goal-scoped advancement/resume decisions into this lane.
                    agent_todos=monitor_todos,
                    agent_id=agent_id,
                )
            )
        }
        if obligations_by_agent:
            item["autonomous_replan_obligations_by_agent"] = obligations_by_agent
            project_asset["autonomous_replan_obligations_by_agent"] = obligations_by_agent
            attached["autonomous_replan_obligations_by_agent"] = obligations_by_agent
        replan_obligation = autonomous_replan_obligation_from_runs(
            latest_runs or [],
            agent_todos=shared_todos if isinstance(shared_todos, dict) else None,
            agent_id=agent_lane_id,
        )
        if replan_obligation:
            item["autonomous_replan_obligation"] = replan_obligation
    if replan_obligation:
        project_asset["autonomous_replan_obligation"] = replan_obligation
        attached["autonomous_replan_obligation"] = replan_obligation

    return attached


def project_asset_summary_is_public_safe(project_asset: Mapping[str, object]) -> bool:
    text = repr(project_asset)
    return not LOCAL_PATH_SURFACE_PATTERN.search(text) and not SECRET_LIKE_SURFACE_PATTERN.search(text)


def project_asset_handoff_check_projection(item: dict[str, object]) -> dict[str, object] | None:
    project_asset = item.get("project_asset")
    if not isinstance(project_asset, dict):
        return None

    raw_quota = project_asset.get("quota")
    quota: dict[str, object] = raw_quota if isinstance(raw_quota, dict) else {}
    if not quota:
        item_quota = item.get("quota")
        if isinstance(item_quota, dict):
            quota = item_quota

    next_action = str(project_asset.get("next_action") or "").strip()
    item_action = str(item.get("recommended_action") or "").strip()
    stop_condition = str(project_asset.get("stop_condition") or "").strip()
    quota_state = str(quota.get("state") or "").strip()
    waiting_on = str(item.get("waiting_on") or "").strip()
    codex_ready = waiting_on == "codex" and quota_state == "eligible"
    checks: dict[str, bool] = {
        "project_asset_backed": True,
        "same_source_should_run": bool(
            quota and next_action and (not item_action or item_action == next_action)
        ),
        "codex_ready": codex_ready,
        "handoff_has_next_action": bool(next_action),
        "handoff_has_stop_condition": bool(stop_condition),
        "handoff_sanitized_surface": project_asset_summary_is_public_safe(project_asset),
    }
    return {
        "checks": checks,
        "codex_ready": codex_ready,
        "quota_state": quota_state or "unknown",
        "state_trace_ready": all(
            checks[key] for key in PROJECT_ASSET_HANDOFF_STATE_TRACE_CHECK_KEYS
        ),
    }


def project_asset_next_safe_command(agent_command: str | None) -> str | None:
    if not agent_command:
        return None
    return project_asset_public_safe_compact_text(agent_command, limit=320)


def build_project_asset(
    *,
    status: str,
    waiting_on: str,
    recommended_action: str,
    operator_question: str | None,
    agent_command: str | None,
    missing_gates: list[str] | None,
    next_handoff_condition: str | None,
    monitor_signal_waiting_on: str,
    monitor_display_stop_condition: str,
) -> dict[str, object]:
    asset: dict[str, object] = {
        "owner": project_asset_owner(
            waiting_on,
            monitor_signal_waiting_on=monitor_signal_waiting_on,
        ),
        "gate": project_asset_gate(
            waiting_on=waiting_on,
            operator_question=operator_question,
            missing_gates=missing_gates,
            status=status,
            monitor_signal_waiting_on=monitor_signal_waiting_on,
        ),
        "support_mode": project_asset_support_mode(
            waiting_on=waiting_on,
            operator_question=operator_question,
            missing_gates=missing_gates,
            status=status,
            recommended_action=recommended_action,
            agent_command=agent_command,
            monitor_signal_waiting_on=monitor_signal_waiting_on,
        ),
        "next_action": recommended_action,
        "stop_condition": project_asset_stop_condition(
            waiting_on=waiting_on,
            next_handoff_condition=next_handoff_condition,
            agent_command=agent_command,
            monitor_signal_waiting_on=monitor_signal_waiting_on,
            monitor_display_stop_condition=monitor_display_stop_condition,
        ),
    }
    next_safe_command = project_asset_next_safe_command(agent_command)
    if next_safe_command:
        asset["next_safe_command"] = next_safe_command
    return asset
