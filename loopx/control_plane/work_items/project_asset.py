from __future__ import annotations

from functools import partial
from typing import Any, Callable

from ..runtime.public_safety import (
    LOCAL_PATH_SURFACE_PATTERN,
    SECRET_LIKE_SURFACE_PATTERN,
    compact_text as _compact_text,
    public_safe_compact_text as _runtime_public_safe_compact_text,
)
from ..todos.todo_semantics import todo_blocker_reason
from .autonomous_replan_obligation import run_history_agent_id


TODO_PROJECTION_VIEW_SCHEMA_VERSION = "todo_projection_view_v0"
TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION = "todo_projection_detail_pointer_v0"
PROJECT_ASSET_TODO_PROJECTION_GAP_SCHEMA_VERSION = "project_asset_todo_projection_gap_v0"
PROJECT_ASSET_HANDOFF_STATE_TRACE_CHECK_KEYS = (
    "project_asset_backed",
    "same_source_should_run",
    "handoff_has_next_action",
    "handoff_has_stop_condition",
    "handoff_sanitized_surface",
)
def project_asset_public_safe_compact_text(value: Any, *, limit: int = 220) -> str | None:
    return _runtime_public_safe_compact_text(value, limit=limit)


def project_asset_owner(
    waiting_on: str,
    *,
    monitor_signal_waiting_on: str,
) -> str:
    if waiting_on == "codex":
        return "codex"
    if waiting_on == "external_evidence":
        return "external_evidence"
    if waiting_on == monitor_signal_waiting_on:
        return monitor_signal_waiting_on
    if waiting_on == "controller":
        return "controller"
    if waiting_on == "user_or_controller":
        return "user_or_controller"
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
) -> str:
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


def project_asset_quota_summary(quota: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(quota, dict):
        return None
    summary: dict[str, Any] = {
        "compute": quota.get("compute"),
        "state": quota.get("state"),
        "spent_slots": quota.get("spent_slots"),
        "allowed_slots": quota.get("allowed_slots"),
    }
    if quota.get("reason"):
        summary["reason"] = _compact_text(str(quota.get("reason") or ""), limit=220)
    return summary


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def project_asset_quota_state(
    *,
    quota: dict[str, Any] | None,
    project_asset: dict[str, Any],
) -> str | None:
    quota_state = ""
    if isinstance(quota, dict):
        quota_state = str(quota.get("state") or "").strip()
    if not quota_state and isinstance(project_asset.get("quota"), dict):
        quota_state = str(project_asset["quota"].get("state") or "").strip()
    return quota_state or None


def project_asset_user_todo_open_count(
    *,
    user_todos: dict[str, Any] | None,
    project_asset: dict[str, Any],
) -> int | None:
    if isinstance(user_todos, dict):
        return _optional_int(user_todos.get("open_count"))
    if isinstance(project_asset.get("user_todos"), dict):
        return _optional_int(project_asset["user_todos"].get("open_count"))
    return None


def project_asset_latest_validation(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(run, dict):
        return None
    signal: dict[str, Any] = {}
    for field in ("generated_at", "classification"):
        value = run.get(field)
        if value:
            signal[field] = value
    summary = run.get("health_check") or run.get("recommended_action")
    if summary:
        signal["summary"] = _compact_text(str(summary), limit=260)
    return signal or None


def enrich_project_asset(
    item: dict[str, Any],
    *,
    user_todos: dict[str, Any] | None,
    agent_todos: dict[str, Any] | None,
    quota: dict[str, Any] | None,
    latest_validation: dict[str, Any] | None,
    latest_runs: list[dict[str, Any]] | None,
    execution_profile: dict[str, Any] | None,
    orchestration: dict[str, Any] | None,
    subagent_activity: dict[str, Any] | None,
    interface_budget_cadence: dict[str, Any] | None,
    project_asset_todo_summary: Callable[..., dict[str, Any] | None],
    project_asset_todo_projection_gap: Callable[..., dict[str, Any] | None],
    project_asset_quota_summary: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    compact_execution_profile: Callable[[dict[str, Any] | None], dict[str, Any]],
    compact_orchestration_policy: Callable[[dict[str, Any]], dict[str, Any]],
    project_asset_handoff_readiness: Callable[..., dict[str, Any] | None],
    project_asset_quota_state: Callable[..., str | None],
    project_asset_user_todo_open_count: Callable[..., int | None],
    build_long_task_cadence_hint: Callable[..., dict[str, Any]],
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
    cadence_hint = build_long_task_cadence_hint(
        execution_profile=(
            project_asset.get("execution_profile")
            if isinstance(project_asset.get("execution_profile"), dict)
            else None
        ),
        latest_runs=latest_runs,
        handoff_readiness=readiness,
        quota_state=quota_state,
        user_todo_open_count=user_todo_open_count,
    )
    project_asset["long_task_cadence_hint"] = cadence_hint
    item["long_task_cadence_hint"] = cadence_hint


def attach_active_state_project_asset_fields(
    item: dict[str, Any],
    *,
    latest_runs: list[dict[str, Any]] | None = None,
    next_action_projection_warning: Callable[..., dict[str, Any] | None] | None = None,
    autonomous_replan_obligation_from_runs: Callable[..., dict[str, Any] | None] | None = None,
    external_progress_review: dict[str, Any] | None = None,
    effective_turn_cadence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_asset = item.get("project_asset")
    if not isinstance(project_asset, dict):
        return {}

    attached: dict[str, Any] = {}
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

    issue_meta_surface = (
        item.get("issue_meta_surface") if isinstance(item.get("issue_meta_surface"), dict) else None
    )
    if issue_meta_surface:
        project_asset["issue_meta_surface"] = issue_meta_surface
        attached["issue_meta_surface"] = issue_meta_surface

    if next_action_projection_warning is not None:
        warning = next_action_projection_warning(
            active_state_next_action=active_next_action,
            latest_run_recommended_action=item.get("latest_run_recommended_action"),
        )
        if warning:
            item["next_action_projection_warning"] = warning
            project_asset["next_action_projection_warning"] = warning
            attached["next_action_projection_warning"] = warning

    for key in (
        "backlog_hygiene_warning",
        "state_projection_gap",
        "completed_todo_archive_warning",
    ):
        value = item.get(key) if isinstance(item.get(key), dict) else None
        if value:
            project_asset[key] = value
            attached[key] = value

    replan_obligation = (
        item.get("autonomous_replan_obligation")
        if isinstance(item.get("autonomous_replan_obligation"), dict)
        else None
    )
    if replan_obligation is None and autonomous_replan_obligation_from_runs is not None:
        agent_ids = sorted(
            {
                agent_id
                for run in latest_runs or []
                if isinstance(run, dict)
                if (agent_id := run_history_agent_id(run)) is not None
            }
        )
        obligations_by_agent = {
            agent_id: obligation
            for agent_id in agent_ids
            if (
                obligation := autonomous_replan_obligation_from_runs(
                    latest_runs or [],
                    # The shared todo summary is goal-scoped here. Quota derives
                    # todo-based replans later from its agent-filtered summary.
                    agent_todos=None,
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
            agent_todos=item.get("agent_todos") if isinstance(item.get("agent_todos"), dict) else None,
        )
        if replan_obligation:
            item["autonomous_replan_obligation"] = replan_obligation
    if replan_obligation:
        project_asset["autonomous_replan_obligation"] = replan_obligation
        attached["autonomous_replan_obligation"] = replan_obligation

    return attached


def project_asset_summary_is_public_safe(project_asset: dict[str, Any]) -> bool:
    text = repr(project_asset)
    return not LOCAL_PATH_SURFACE_PATTERN.search(text) and not SECRET_LIKE_SURFACE_PATTERN.search(text)


def project_asset_handoff_check_projection(item: dict[str, Any]) -> dict[str, Any] | None:
    project_asset = item.get("project_asset")
    if not isinstance(project_asset, dict):
        return None

    raw_quota = project_asset.get("quota")
    quota = raw_quota if isinstance(raw_quota, dict) else {}
    if not quota and isinstance(item.get("quota"), dict):
        quota = item["quota"]

    next_action = str(project_asset.get("next_action") or "").strip()
    item_action = str(item.get("recommended_action") or "").strip()
    stop_condition = str(project_asset.get("stop_condition") or "").strip()
    quota_state = str(quota.get("state") or "").strip()
    waiting_on = str(item.get("waiting_on") or "").strip()
    codex_ready = waiting_on == "codex" and quota_state == "eligible"
    checks = {
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
) -> dict[str, Any]:
    asset = {
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


def project_asset_todo_projection_metadata(
    *,
    role: str | None,
    item_limit: int,
    deferred_item_limit: int,
) -> dict[str, dict[str, Any]]:
    todo_role = str(role or "").strip().lower()
    if todo_role == "user":
        canonical_source = "attention_queue.items[].user_todos"
    elif todo_role == "agent":
        canonical_source = "attention_queue.items[].agent_todos"
    else:
        canonical_source = "attention_queue.items[].{user_todos,agent_todos}"
    return {
        "projection_view": {
            "schema_version": TODO_PROJECTION_VIEW_SCHEMA_VERSION,
            "view": "project_asset_overview",
            "truth": "derived",
            "canonical_source": canonical_source,
            "item_limit": item_limit,
            "deferred_item_limit": deferred_item_limit,
        },
        "detail_pointer": {
            "schema_version": TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION,
            "cold_path": "loopx status --format json",
            "active_state_source": "registry goal state_file",
            "full_list_included": False,
        },
    }


def project_asset_todo_projection_gap(
    *,
    user_todos: dict[str, Any] | None,
    agent_todos: dict[str, Any] | None,
) -> dict[str, Any] | None:
    missing_roles: list[str] = []
    if not isinstance(user_todos, dict):
        missing_roles.append("user")
    if not isinstance(agent_todos, dict):
        missing_roles.append("agent")
    if not missing_roles:
        return None
    return {
        "schema_version": PROJECT_ASSET_TODO_PROJECTION_GAP_SCHEMA_VERSION,
        "kind": "project_asset_todo_projection_gap",
        "missing_roles": missing_roles,
        "source": "active_state_todo_projection",
        "recommended_action": (
            "add parseable User Todo / Agent Todo sections or repair the active state_file "
            "before treating this project_asset as first-screen complete"
        ),
    }


PROJECT_ASSET_TODO_DISPLAY_FIELDS = (
    "index",
    "done",
    "schema_version",
    "todo_id",
    "role",
    "status",
    "priority",
    "archive_state",
    "source_section",
    "task_class",
    "action_kind",
    "task_domain",
    "task_repository",
    "required_write_scopes",
    "required_capabilities",
    "target_capabilities",
    "decision_scope",
    "required_decision_scopes",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "excluded_agents",
    "global_gate",
    "unblocks_todo_id",
    "resume_when",
    "resume_monitor_generation",
    "resume_condition",
    "resume_ready",
    "blocking_monitor_todo_id",
    "no_followup",
    "successor_todo_ids",
    "target_key",
    "cadence",
    "next_due_at",
    "expires_at",
    "last_checked_at",
    "result_hash",
    "consecutive_no_change",
    "material_change",
    "material_change_generation",
    "max_no_change_before_replan",
    "route_continuation_replan_required",
    "route_continuation_reason",
    "route_id",
    "route_key",
    "completed_at",
    "updated_at",
    "superseded_by",
)


def _project_asset_display_todo_item(item: dict[str, Any]) -> dict[str, Any]:
    display = {
        key: item.get(key)
        for key in PROJECT_ASSET_TODO_DISPLAY_FIELDS
        if item.get(key) is not None
    }
    text = _compact_text(str(item.get("text") or ""), limit=220)
    if text:
        display["text"] = text
    title = _compact_text(str(item.get("title") or ""), limit=220)
    if title:
        display["title"] = title
    reason = todo_blocker_reason(item)
    if reason:
        display["reason"] = reason
    return display


def _project_asset_display_todo_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _project_asset_display_todo_item(item)
        for item in items
        if isinstance(item, dict)
    ]


def build_project_asset_todo_summary(
    todos: dict[str, Any] | None,
    *,
    role: str | None = None,
    item_limit: int,
    deferred_item_limit: int,
    advancement_task_class: str,
    open_todo_items: Callable[..., list[dict[str, Any]]],
    compact_todo_item: Callable[[dict[str, Any]], dict[str, Any]],
    todo_lane_items: Callable[..., list[dict[str, Any]]],
    todo_item_is_actionable_open: Callable[[dict[str, Any]], bool],
    todo_item_task_class: Callable[[dict[str, Any]], str],
) -> dict[str, Any] | None:
    if not isinstance(todos, dict):
        return None

    def lane_count(lane: str) -> int:
        items = todos.get(lane)
        if not isinstance(items, list):
            return 0
        return sum(1 for item in items if isinstance(item, dict))

    open_count = todos.get("open_count", 0)
    done_count = todos.get("done_count", 0)
    total_count = todos.get("total_count", 0)
    todo_role = str(role or todos.get("role") or "").strip().lower()
    metadata = project_asset_todo_projection_metadata(
        role=todo_role,
        item_limit=item_limit,
        deferred_item_limit=deferred_item_limit,
    )
    summary: dict[str, Any] = {
        "schema_version": todos.get("schema_version") or "todo_summary_v0",
        "source_section": "project_asset",
        "open": open_count,
        "done": done_count,
        "total": total_count,
        **metadata,
    }
    if todos.get("advancement_done_count") is not None:
        summary["advancement_done_count"] = todos.get("advancement_done_count")
    open_items = open_todo_items(todos, limit=item_limit)
    claimed_open_count = todos.get("claimed_open_count")
    if claimed_open_count is None:
        claimed_open_count = lane_count("claimed_open_items") or sum(
            1 for item in open_items if item.get("claimed_by")
        )
    if claimed_open_count:
        summary["claimed_open_count"] = claimed_open_count
        summary["unclaimed_open_count"] = todos.get(
            "unclaimed_open_count",
            max(0, int(summary.get("open") or 0) - int(summary["claimed_open_count"] or 0)),
        )
    open_items = _project_asset_display_todo_items(open_items)
    if open_items:
        summary["items"] = open_items
        summary["next"] = open_items[0]["text"]
        if open_items[0].get("index") is not None:
            summary["next_index"] = open_items[0].get("index")
        if open_items[0].get("claimed_by"):
            summary["next_claimed_by"] = open_items[0].get("claimed_by")
    recent_completed_items = [
        _project_asset_display_todo_item(dict(item))
        for item in todos.get("recent_completed_advancement_items", [])
        if isinstance(item, dict)
    ][:item_limit]
    if recent_completed_items:
        summary["recent_completed_advancement_items"] = recent_completed_items
    monitor_writeback = todos.get("monitor_writeback")
    if isinstance(monitor_writeback, dict):
        summary["monitor_writeback"] = dict(monitor_writeback)
    deferred_items = [
        _project_asset_display_todo_item(compact_todo_item(item))
        for item in todos.get("deferred_items", [])
        if isinstance(item, dict)
    ][:deferred_item_limit]
    deferred_resume_candidates = [
        _project_asset_display_todo_item(compact_todo_item(item))
        for item in todos.get("deferred_resume_candidates", [])
        if isinstance(item, dict)
    ][:deferred_item_limit]
    if todos.get("deferred_count") is not None:
        summary["deferred_count"] = todos.get("deferred_count")
        summary["deferred_visibility_limit"] = deferred_item_limit
    if deferred_items:
        summary["deferred_items"] = deferred_items
    if deferred_resume_candidates:
        summary["deferred_resume_candidates"] = deferred_resume_candidates
    executable_items = [
        item
        for item in open_todo_items(
            todos,
            limit=item_limit,
            source_keys=("first_executable_items", "executable_backlog_items", "items"),
        )
        if todo_item_is_actionable_open(item)
        if todo_item_task_class(item) == advancement_task_class
    ]
    if executable_items:
        summary["first_executable_items"] = _project_asset_display_todo_items(
            executable_items[:item_limit]
        )
    for lane in (
        "gate_open_items",
        "current_agent_claimed_open_items",
        "active_next_action_items",
        "active_next_action_executable_items",
    ):
        lane_items = todo_lane_items(
            todos,
            lane,
            limit=item_limit,
        )
        if lane_items:
            summary[lane] = _project_asset_display_todo_items(lane_items)
    for count_key in (
        "claimed_advancement_open_count",
        "claimed_monitor_open_count",
    ):
        if todos.get(count_key) is not None:
            summary[count_key] = todos.get(count_key)
            continue
        lane = count_key.removesuffix("_count") + "_items"
        count = lane_count(lane)
        if count:
            summary[count_key] = count
    return summary
