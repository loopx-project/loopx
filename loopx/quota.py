from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shlex
from typing import Any

from .control_plane import compact_control_plane_policy
from .control_plane.goals.activation import goal_is_stopped
from .execution_profile import (
    execution_profile_outcome_floor,
    outcome_floor_threshold,
)
from .control_plane.quota.heartbeat_recommendation import (
    HEARTBEAT_HANDOFF_READINESS_COMPACT_FIELDS as HANDOFF_READINESS_COMPACT_FIELDS,
    HEARTBEAT_POST_HANDOFF_RUN_COMPACT_FIELDS as POST_HANDOFF_RUN_COMPACT_FIELDS,
)
from .control_plane.quota.decision_summary import (
    goal_status_health_ok as _goal_status_health_ok,
)
from .control_plane.effect_program import (
    ReceiptBoundMonitorPhase,
    ReceiptBoundReplayPhase,
    ReceiptBoundTerminalPhase,
)
from .control_plane.quota.goal_boundary import (
    registry_goal_by_id as _registry_goal_by_id,
)
from .control_plane.quota.policy_constants import (
    AUTONOMOUS_CANDIDATE_CONTEXT_FIELDS,  # noqa: F401
    MONITOR_DUE_ITEM_LIMIT,  # noqa: F401
)
from .control_plane.quota.monitor_poll import (
    QUOTA_MONITOR_POLL_CLASSIFICATION as QUOTA_MONITOR_POLL_CLASSIFICATION,
    build_quota_monitor_poll_event as build_quota_monitor_poll_event,
    record_quota_monitor_poll_for_decision,
    resolve_due_monitor_candidate,
)
from .control_plane.quota.recent_runs import (
    goal_latest_run as _goal_latest_run,
)
from .presentation.renderers.quota_event_markdown import (
    render_quota_monitor_poll_markdown as _render_quota_monitor_poll_markdown,
    render_quota_slot_preview_markdown as render_quota_slot_preview_markdown,
)
from .presentation.renderers.quota_markdown import (
    render_quota_markdown as render_quota_markdown,
    render_quota_scheduler_ack_markdown as render_quota_scheduler_ack_markdown,
    render_quota_should_run_markdown as render_quota_should_run_markdown,
)
from .control_plane.quota.scheduler_ack import (
    QUOTA_SCHEDULER_ACK_CLASSIFICATION,
    record_quota_scheduler_ack_for_decision,
)
from .control_plane.quota.settlement import (
    read_heartbeat_settlement,
    settlement_result_payload,
)
from .control_plane.quota.ledger_readback import (
    QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION,
    net_quota_slot_spend,
    quota_slot_contribution,
)
from .control_plane.quota.spend_sources import (
    DEFAULT_SLOT_SPEND_SOURCE,
    TURN_SCOPED_SLOT_SPEND_SOURCES,
    VISIBLE_GOAL_SLOT_SPEND_SOURCE,
)
from .control_plane.quota.states import AutomaticTurnPauseCause, QUOTA_STATE_ORDER
from .control_plane.quota.policy_constants import (
    DEFAULT_COMPUTE_QUOTA,
    DEFAULT_SLOT_MINUTES,
    DEFAULT_WINDOW_HOURS,
    FOCUS_WAIT_LIFECYCLE_MARKERS,
    FOCUS_WAIT_REASON,
    SELF_REPAIR_SPEND_ACTIONS,
)
from .control_plane.runtime.time import parse_timestamp as _parse_timestamp
from .control_plane.scheduler.execution_context import (
    SchedulerExecutionContextResolution,
)
from .control_plane.scheduler.state import (
    CODEX_APP_SURFACE,
)
from .control_plane.todos.contract import (
    normalize_todo_claimed_by,
    normalize_todo_id,
)
from .control_plane.todos.todo_semantics import (
    todo_index_rank as projection_todo_index_rank,
    todo_item_expires_at as projection_todo_item_expires_at,
    todo_item_is_due_monitor as projection_todo_item_is_due_monitor,
    todo_item_is_expired_monitor as projection_todo_item_is_expired_monitor,
    todo_item_missing_monitor_schedule as projection_todo_item_missing_monitor_schedule,
    todo_item_next_due_at as projection_todo_item_next_due_at,
    todo_priority_label as projection_todo_priority_label,
    todo_priority_rank as projection_todo_priority_rank,
    todo_projection_sort_key as projection_todo_projection_sort_key,
    todo_summary_claim_scope_agent_id as projection_todo_summary_claim_scope_agent_id,
)

_PUBLIC_COMPAT_REEXPORTS = {
    "AUTONOMOUS_CANDIDATE_CONTEXT_FIELDS": "loopx.control_plane.quota.policy_constants",
    "MONITOR_DUE_ITEM_LIMIT": "loopx.control_plane.quota.policy_constants",
    "QUOTA_MONITOR_POLL_CLASSIFICATION": "loopx.control_plane.quota.monitor_poll",
    "build_quota_monitor_poll_event": "loopx.control_plane.quota.monitor_poll",
    "render_quota_markdown": "loopx.presentation.renderers.quota_markdown",
    "render_quota_scheduler_ack_markdown": "loopx.presentation.renderers.quota_markdown",
    "render_quota_should_run_markdown": "loopx.presentation.renderers.quota_markdown",
    "build_quota_slot_void_event": "loopx.control_plane.quota.void_commit",
    "record_quota_slot_void_from_preview": "loopx.control_plane.quota.void_commit",
    "render_quota_slot_preview_markdown": "loopx.presentation.renderers.quota_event_markdown",
}
_LAZY_PUBLIC_COMPAT_REEXPORTS = frozenset({
    "build_quota_slot_void_event",
    "record_quota_slot_void_from_preview",
})


def __getattr__(name: str) -> Any:
    # Only legacy exports from execution adapters are lazy. Return their real
    # objects, not wrappers or a second implementation of quota transactions.
    if name not in _LAZY_PUBLIC_COMPAT_REEXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(_PUBLIC_COMPAT_REEXPORTS[name]), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PUBLIC_COMPAT_REEXPORTS))


AUTONOMOUS_REPLAN_ACK_NEUTRAL_CLASSIFICATIONS = {
    QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION,
    QUOTA_SCHEDULER_ACK_CLASSIFICATION,
    "delivery_completion_spend_accounted_v0",
}


def _validate_goal_id_path_segment(goal_id: str) -> str:
    value = goal_id.strip()
    if not value:
        raise ValueError("goal id is required")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("goal id must be a single path segment")
    if Path(value).name != value:
        raise ValueError("goal id must not include path traversal")
    return value


def _resolve_reward_memory_experiment_from_status(
    status_payload: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    from .capabilities.reward_memory.experiment import (
        resolve_reward_memory_experiment_from_status as _impl,
    )

    return _impl(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )


def _number(value: Any, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _int_number(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _clamp_compute(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 2)


def _text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_text_values(item))
        return values
    return [str(value)]


def _has_focus_wait_marker(*values: Any) -> bool:
    for value in values:
        for text in _text_values(value):
            marker = text.strip().lower()
            if marker in FOCUS_WAIT_LIFECYCLE_MARKERS:
                return True
    return False


def _focus_wait_quota(payload: dict[str, Any]) -> dict[str, Any]:
    quota = dict(payload)
    quota["state"] = "focus_wait"
    quota["reason"] = FOCUS_WAIT_REASON
    quota["blocked_action_scope"] = "delivery_focus"
    quota["focus_wait"] = True
    return quota


def quota_with_handoff_outcome_floor(
    quota: dict[str, Any],
    *,
    waiting_on: str | None = None,
    project_asset: dict[str, Any] | None = None,
    handoff_readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if waiting_on != "codex":
        return quota
    if not isinstance(handoff_readiness, dict) or not handoff_readiness:
        return quota
    profile = (
        project_asset.get("execution_profile")
        if isinstance(project_asset, dict)
        and isinstance(project_asset.get("execution_profile"), dict)
        else None
    )
    outcome_gap_streak = handoff_readiness.get("post_handoff_outcome_gap_streak")
    if not isinstance(outcome_gap_streak, int) or outcome_gap_streak <= 0:
        return quota
    threshold = outcome_floor_threshold(profile)
    if outcome_gap_streak < threshold:
        return quota
    from .control_plane.work_items.delivery_history import project_delivery_response

    latest = handoff_readiness.get("post_handoff_latest_run")
    if isinstance(latest, dict) and not project_delivery_response(
        latest, project_asset.get("agent_todos") if isinstance(project_asset, dict) else None,
    )["outcome_floor_applicable"]:
        return quota
    state = str(quota.get("state") or "eligible")
    if state in {"blocked_health", "operator_gate", "waiting", "paused", "throttled"}:
        return quota

    floor = execution_profile_outcome_floor(profile)
    must_advance = [
        str(value).strip()
        for value in (
            floor.get("must_advance")
            if isinstance(floor.get("must_advance"), list)
            else []
        )
        if str(value).strip()
    ]
    avoid = [
        str(value).strip()
        for value in (
            floor.get("avoid") if isinstance(floor.get("avoid"), list) else []
        )
        if str(value).strip()
    ]
    reason_parts = [
        f"handoff outcome floor not met: outcome_gap_streak={outcome_gap_streak}/{threshold}",
        "report blocker without spend or return with outcome-scale evidence",
    ]
    if must_advance:
        reason_parts.append(f"must_advance={'+'.join(must_advance)}")
    if avoid:
        reason_parts.append(f"avoid={'+'.join(avoid)}")

    blocked = dict(quota)
    blocked["state"] = "focus_wait"
    blocked["reason"] = "; ".join(reason_parts)
    blocked["blocked_action_scope"] = "delivery_outcome_floor"
    blocked["focus_wait"] = True
    blocked["handoff_outcome_floor_block"] = True
    blocked["post_handoff_outcome_gap_streak"] = outcome_gap_streak
    blocked["outcome_gap_threshold"] = threshold
    if must_advance:
        blocked["must_advance"] = must_advance
        blocked["safe_bypass_allowed"] = True
        blocked["safe_bypass_kind"] = "outcome_floor_recovery"
        blocked["safe_bypass_policy"] = (
            "Outcome-floor recovery only: attempt one bounded "
            f"{'+'.join(must_advance)} evidence segment or write back a concrete blocker; "
            "avoid surface-only work; spend only after validated evidence/blocker writeback."
        )
    if avoid:
        blocked["avoid"] = avoid
    return blocked


def _quota_with_focus_wait_override(
    quota: dict[str, Any],
    *,
    waiting_on: str | None = None,
    lifecycle_phase: Any = None,
    lifecycle_flags: Any = None,
    status: Any = None,
) -> dict[str, Any]:
    if waiting_on != "codex":
        return quota
    if not _has_focus_wait_marker(lifecycle_phase, lifecycle_flags, status):
        return quota
    state = str(quota.get("state") or "eligible")
    if state in {"blocked_health", "operator_gate", "waiting", "paused"}:
        return quota
    return _focus_wait_quota(quota)


def goal_quota_config(goal: dict[str, Any] | None) -> dict[str, Any]:
    raw = goal.get("quota") if goal and isinstance(goal.get("quota"), dict) else {}
    if goal and "compute_quota" in goal and "compute" not in raw:
        raw = {**raw, "compute": goal.get("compute_quota")}
    compute = _clamp_compute(_number(raw.get("compute"), default=DEFAULT_COMPUTE_QUOTA))
    window_hours = max(
        1, _int_number(raw.get("window_hours"), default=DEFAULT_WINDOW_HOURS)
    )
    slot_minutes = max(
        1, _int_number(raw.get("slot_minutes"), default=DEFAULT_SLOT_MINUTES)
    )
    spent_slots = max(0, _int_number(raw.get("spent_slots"), default=0))
    default_allowed_slots = round((window_hours * 60 / slot_minutes) * compute)
    allowed_slots = max(
        0, _int_number(raw.get("allowed_slots"), default=default_allowed_slots)
    )
    payload: dict[str, Any] = {
        "compute": compute,
        "window_hours": window_hours,
        "slot_minutes": slot_minutes,
        "allowed_slots": allowed_slots,
        "spent_slots": spent_slots,
    }
    if raw.get("next_eligible_at"):
        payload["next_eligible_at"] = str(raw.get("next_eligible_at"))
    return payload


def goal_quota_with_spend_ledger(
    goal: dict[str, Any] | None,
    runs: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = goal_quota_config(goal)
    goal_id = str(goal.get("id") or "") if goal else ""
    current_time = now or datetime.now(timezone.utc).astimezone()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    window_start = current_time - timedelta(hours=int(payload["window_hours"]))
    contributions: list[tuple[str, str, int]] = []
    spend_event_count = 0
    void_event_count = 0

    for run in runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("goal_id") or goal_id) != goal_id:
            continue
        goal_instance_id = (
            str(goal.get("goal_instance_id") or "").strip()
            if goal is not None
            else ""
        )
        run_goal_ref = (
            run.get("goal_ref")
            if isinstance(run.get("goal_ref"), Mapping)
            else None
        )
        if goal_instance_id:
            if (
                run_goal_ref is None
                or run_goal_ref.get("goal_id") != goal_id
                or run_goal_ref.get("goal_instance_id") != goal_instance_id
            ):
                continue
        elif run_goal_ref is not None:
            continue
        generated_at = _parse_timestamp(run.get("generated_at"))
        if (
            generated_at is None
            or generated_at < window_start
            or generated_at > current_time
        ):
            continue
        contribution = quota_slot_contribution(run)
        if contribution is None:
            continue
        kind, run_key, slots = contribution
        contributions.append((run_key, kind, slots))
        if kind == "spent":
            spend_event_count += 1
        else:
            void_event_count += 1

    payload["spent_slots"] = sum(net_quota_slot_spend(contributions).values())
    payload["spend_source"] = "runtime_events"
    payload["spend_event_count"] = spend_event_count
    if void_event_count:
        payload["void_event_count"] = void_event_count
    return payload


def quota_status(
    goal: dict[str, Any] | None,
    *,
    waiting_on: str | None = None,
    severity: str | None = None,
    lifecycle_phase: Any = None,
    lifecycle_flags: Any = None,
    status: Any = None,
) -> dict[str, Any]:
    payload = goal_quota_config(goal)
    compute = float(payload["compute"])
    spent_slots = int(payload["spent_slots"])
    allowed_slots = int(payload["allowed_slots"])

    if goal_is_stopped(goal):
        state = "paused"
        reason = "goal is stopped by owner; automatic agent turns are paused"
        # Stopping is an owner lifecycle decision, not a destructive quota edit.
        # Project zero *effective* allocation while retaining the configured
        # values so an explicit Goal resume restores the prior quota policy.
        payload["configured_compute"] = compute
        payload["configured_allowed_slots"] = allowed_slots
        payload["compute"] = 0.0
        payload["allowed_slots"] = 0
        payload["blocked_action_scope"] = "automatic_agent_turns"
        payload["goal_activation_state"] = "stopped"
        payload["pause_cause"] = AutomaticTurnPauseCause.GOAL_STOPPED.value
    elif compute <= 0:
        state = "paused"
        reason = "compute quota is 0; automatic agent turns are paused"
        payload["pause_cause"] = AutomaticTurnPauseCause.COMPUTE_QUOTA_ZERO.value
    elif severity == "high":
        state = "blocked_health"
        reason = "health or contract blocker must clear before compute is spent"
    elif waiting_on in {"user_or_controller", "controller"}:
        state = "operator_gate"
        reason = (
            "operator gate blocks gated delivery; safe non-gated steering may continue"
        )
        payload["blocked_action_scope"] = "gated_delivery"
        payload["safe_bypass_allowed"] = True
        payload["safe_bypass_policy"] = (
            "Do not execute agent_command, adapter work, write-control, production actions, "
            "or the gated path. A heartbeat may spend one bounded turn on read-only steering, "
            "analysis, documentation, or another priority-stack item that does not depend on this gate."
        )
    elif waiting_on == "external_evidence":
        state = "waiting"
        reason = "external evidence is still pending; do not spend delivery compute yet"
    elif waiting_on == "codex" and _has_focus_wait_marker(
        lifecycle_phase, lifecycle_flags, status
    ):
        state = "focus_wait"
        reason = FOCUS_WAIT_REASON
        payload["blocked_action_scope"] = "delivery_focus"
        payload["focus_wait"] = True
    elif waiting_on == "codex":
        if spent_slots >= allowed_slots:
            state = "throttled"
            reason = f"{compute:g} compute quota spent {spent_slots}/{allowed_slots} slots in this window"
        else:
            state = "eligible"
            reason = (
                f"{compute:g} compute quota; eligible for the next automatic agent turn"
            )
    else:
        state = "waiting"
        reason = "no active Codex-ready work is currently selected"

    payload["state"] = state
    payload["reason"] = reason
    return payload


def _quota_sort_key(item: dict[str, Any]) -> tuple[int, float, int, str]:
    quota = item.get("quota") if isinstance(item.get("quota"), dict) else {}
    state = str(quota.get("state") or "waiting")
    state_index = (
        QUOTA_STATE_ORDER.index(state)
        if state in QUOTA_STATE_ORDER
        else len(QUOTA_STATE_ORDER)
    )
    compute = _number(quota.get("compute"), default=DEFAULT_COMPUTE_QUOTA)
    spent_slots = _int_number(quota.get("spent_slots"), default=0)
    return (state_index, -compute, spent_slots, str(item.get("goal_id") or ""))


def _todo_priority_label(item: dict[str, Any]) -> str | None:
    return projection_todo_priority_label(item)


def _todo_priority_rank(item: dict[str, Any]) -> int:
    return projection_todo_priority_rank(item)


def _todo_index_rank(item: dict[str, Any]) -> int:
    return projection_todo_index_rank(item)


def _todo_projection_sort_key(item: dict[str, Any]) -> tuple[int, int]:
    return projection_todo_projection_sort_key(item)


def _compact_handoff_readiness(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = {
        field: value[field]
        for field in HANDOFF_READINESS_COMPACT_FIELDS
        if field in value
    }
    latest_run = (
        value.get("post_handoff_latest_run")
        if isinstance(value.get("post_handoff_latest_run"), dict)
        else {}
    )
    if latest_run:
        compact["post_handoff_latest_run"] = {
            field: latest_run[field]
            for field in POST_HANDOFF_RUN_COMPACT_FIELDS
            if field in latest_run
        }
    recent_runs = (
        value.get("post_handoff_recent_runs")
        if isinstance(value.get("post_handoff_recent_runs"), list)
        else []
    )
    compact_recent_runs: list[dict[str, Any]] = []
    for run in recent_runs:
        if not isinstance(run, dict):
            continue
        compact_run = {
            field: run[field]
            for field in POST_HANDOFF_RUN_COMPACT_FIELDS
            if field in run
        }
        if compact_run:
            compact_recent_runs.append(compact_run)
    if compact_recent_runs:
        compact["post_handoff_recent_runs"] = compact_recent_runs[:3]
    return compact or None


def _todo_item_next_due_at(item: dict[str, Any]) -> datetime | None:
    return projection_todo_item_next_due_at(item)


def _todo_item_expires_at(item: dict[str, Any]) -> datetime | None:
    return projection_todo_item_expires_at(item)


def _todo_item_is_expired_monitor(
    item: dict[str, Any], *, now: datetime | None = None
) -> bool:
    return projection_todo_item_is_expired_monitor(item, now=now)


def _todo_item_is_due_monitor(
    item: dict[str, Any], *, now: datetime | None = None
) -> bool:
    return projection_todo_item_is_due_monitor(item, now=now)


def _todo_item_missing_monitor_schedule(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    return projection_todo_item_missing_monitor_schedule(item, now=now)


def _todo_summary_claim_scope_agent_id(summary: dict[str, Any]) -> str | None:
    return projection_todo_summary_claim_scope_agent_id(summary)


def _quota_plan_goal_quota(
    *,
    attention: dict[str, Any],
    project_asset: dict[str, Any],
    goal: dict[str, Any],
    waiting_on: str,
    lifecycle_phase: Any,
    lifecycle_flags: Any,
    status: Any,
) -> dict[str, Any]:
    project_asset_quota = (
        project_asset.get("quota")
        if isinstance(project_asset.get("quota"), dict)
        else {}
    )
    raw_quota = (
        attention.get("quota")
        if isinstance(attention.get("quota"), dict)
        else goal.get("quota")
    )
    if goal_is_stopped(goal):
        # Stopped Goals intentionally have no attention-queue row. Re-derive
        # the hard pause from run-history state before any legacy quota fallback
        # can treat the Goal as waiting or eligible.
        quota = quota_status(
            goal,
            waiting_on=waiting_on,
            severity=str(attention.get("severity") or ""),
            lifecycle_phase=lifecycle_phase,
            lifecycle_flags=lifecycle_flags,
            status=status,
        )
    elif project_asset_quota:
        raw_quota_base = raw_quota if isinstance(raw_quota, dict) else {}
        quota = {**raw_quota_base, **project_asset_quota}
    elif isinstance(raw_quota, dict):
        quota = _quota_with_focus_wait_override(
            raw_quota,
            waiting_on=waiting_on,
            lifecycle_phase=lifecycle_phase,
            lifecycle_flags=lifecycle_flags,
            status=status,
        )
    else:
        quota = quota_status(
            goal,
            waiting_on=waiting_on,
            severity=str(attention.get("severity") or ""),
            lifecycle_phase=lifecycle_phase,
            lifecycle_flags=lifecycle_flags,
            status=status,
        )
    return quota_with_handoff_outcome_floor(
        quota,
        waiting_on=waiting_on,
        project_asset=project_asset,
        handoff_readiness=attention.get("handoff_readiness")
        if isinstance(attention.get("handoff_readiness"), dict)
        else None,
    )


def build_quota_plan(
    status_payload: dict[str, Any], *, mode: str = "status"
) -> dict[str, Any]:
    queue = (
        status_payload.get("attention_queue")
        if isinstance(status_payload.get("attention_queue"), dict)
        else {}
    )
    queue_items = queue.get("items") if isinstance(queue.get("items"), list) else []
    queue_by_goal = {
        str(item.get("goal_id")): item
        for item in queue_items
        if isinstance(item, dict) and item.get("goal_id")
    }
    health_items = [
        item
        for item in queue_items
        if isinstance(item, dict) and not isinstance(item.get("quota"), dict)
    ]

    run_history = (
        status_payload.get("run_history")
        if isinstance(status_payload.get("run_history"), dict)
        else {}
    )
    run_goals = (
        run_history.get("goals") if isinstance(run_history.get("goals"), list) else []
    )
    status_goals = (
        status_payload.get("goals")
        if isinstance(status_payload.get("goals"), list)
        else []
    )
    status_goal_by_id = {
        str(goal.get("id") or ""): goal
        for goal in status_goals
        if isinstance(goal, dict) and goal.get("id")
    }
    registry_goal_by_id = _registry_goal_by_id(status_payload)
    groups: dict[str, list[dict[str, Any]]] = {state: [] for state in QUOTA_STATE_ORDER}
    groups["unknown"] = []

    for goal in run_goals:
        if not isinstance(goal, dict) or not goal.get("registry_member"):
            continue
        goal_id = str(goal.get("id") or "")
        status_goal = (
            status_goal_by_id.get(goal_id) or registry_goal_by_id.get(goal_id) or {}
        )
        attention = queue_by_goal.get(goal_id, {})
        project_asset = (
            attention.get("project_asset")
            if isinstance(attention.get("project_asset"), dict)
            else {}
        )
        latest = _goal_latest_run(goal)
        waiting_on = attention.get("waiting_on") or "none"
        lifecycle_phase = attention.get("lifecycle_phase") or goal.get(
            "lifecycle_phase"
        )
        lifecycle_flags = attention.get("lifecycle_flags") or goal.get(
            "lifecycle_flags"
        )
        status = attention.get("status") or goal.get("status")
        control_plane = (
            compact_control_plane_policy(attention.get("control_plane"))
            or compact_control_plane_policy(project_asset.get("control_plane"))
            or compact_control_plane_policy(goal.get("control_plane"))
        )
        quota = _quota_plan_goal_quota(
            attention=attention,
            project_asset=project_asset,
            goal=goal,
            waiting_on=str(waiting_on or ""),
            lifecycle_phase=lifecycle_phase,
            lifecycle_flags=lifecycle_flags,
            status=status,
        )
        state = str(quota.get("state") or "waiting")
        item: dict[str, Any] = {
            "goal_id": goal_id,
            "status": status,
            "lifecycle_phase": lifecycle_phase,
            "lifecycle_flags": lifecycle_flags,
            "waiting_on": waiting_on,
            "severity": attention.get("severity") or "info",
            "source": attention.get("source") or "run_history",
            "recommended_action": project_asset.get("next_action")
            or attention.get("recommended_action")
            or latest.get("recommended_action"),
            "adapter_kind": goal.get("adapter_kind"),
            "adapter_status": goal.get("adapter_status"),
            "repo": (
                goal.get("repo")
                or goal.get("project")
                or goal.get("root")
                or status_goal.get("repo")
                or status_goal.get("project")
                or status_goal.get("root")
            ),
            "coordination": goal.get("coordination")
            if isinstance(goal.get("coordination"), dict)
            else None,
            "explore_graph": goal.get("explore_graph")
            if isinstance(goal.get("explore_graph"), dict)
            else None,
            "spawn_policy": goal.get("spawn_policy")
            if isinstance(goal.get("spawn_policy"), dict)
            else None,
            "guards": goal.get("guards")
            if isinstance(goal.get("guards"), list)
            else [],
            "next_probe": goal.get("next_probe"),
            "latest_run_generated_at": latest.get("generated_at"),
            "quota": quota,
        }
        workspace_guard_policy = (
            goal.get("workspace_guard_policy")
            if isinstance(goal.get("workspace_guard_policy"), dict)
            else status_goal.get("workspace_guard_policy")
            if isinstance(status_goal.get("workspace_guard_policy"), dict)
            else None
        )
        if workspace_guard_policy:
            item["workspace_guard_policy"] = workspace_guard_policy
        if control_plane:
            item["control_plane"] = control_plane
        if project_asset:
            item["project_asset"] = project_asset
            item["project_asset_source"] = "project_asset"
        else:
            item["project_asset_source"] = "legacy_raw_fallback"
        for optional_field in (
            "operator_question",
            "agent_command",
            "controller_stage",
            "missing_gates",
            "next_handoff_condition",
            "handoff_readiness",
            "user_todos",
            "agent_todos",
            "active_state_next_action",
            "active_state_next_action_entries",
            "recommendation_context",
            "standing_decision_authority",
            "long_task_cadence_hint",
            "stale_latest_run_warning",
            "backlog_hygiene_warning",
            "completed_todo_archive_warning",
            "dreaming_proposal",
            "dreaming_lane_badge",
        ):
            if optional_field in attention:
                if optional_field == "handoff_readiness":
                    compact_handoff = _compact_handoff_readiness(
                        attention[optional_field]
                    )
                    if compact_handoff:
                        item[optional_field] = compact_handoff
                else:
                    item[optional_field] = attention[optional_field]
        groups.setdefault(state, []).append(item)

    for state_items in groups.values():
        state_items.sort(key=_quota_sort_key)

    ordered_items = [
        item for state in QUOTA_STATE_ORDER for item in groups.get(state, [])
    ] + groups.get("unknown", [])
    next_automatic_turn = (groups.get("eligible") or [None])[0]
    summary = {
        "registered_goals": len(ordered_items),
        "health_blockers": len(health_items),
        "next_automatic_turn": next_automatic_turn.get("goal_id")
        if next_automatic_turn
        else None,
        "states": {state: len(groups.get(state, [])) for state in QUOTA_STATE_ORDER},
    }
    if groups.get("unknown"):
        summary["states"]["unknown"] = len(groups["unknown"])

    result = {
        "ok": status_payload.get("ok"),
        "mode": mode,
        "registry": status_payload.get("registry"),
        "runtime_root": status_payload.get("runtime_root"),
        "goal_count": status_payload.get("goal_count"),
        "run_count": status_payload.get("run_count"),
        "summary": summary,
        "next_automatic_turn": next_automatic_turn,
        "groups": groups,
        "health_items": health_items,
    }
    return _describe_quota_observation(result, status_payload, mode=mode)


def _describe_quota_observation(
    result: dict[str, Any], status_payload: dict[str, Any], *, mode: str
) -> dict[str, Any]:
    """Carry read-only selection and source proof without changing execution plans."""
    if mode not in {"status", "plan"}:
        return result
    goal_filter = status_payload.get("goal_filter")
    if goal_filter:
        result["goal_filter"] = goal_filter
        if not any(
            item["goal_id"] == goal_filter
            for group in result["groups"].values()
            for item in group
        ):
            result.update(
                ok=False,
                status="goal_not_found",
                reason="goal is not present in the registered quota plan",
                recommended_action="run `loopx registry` and connect or sync the selected goal",
            )
    if isinstance(status_payload.get("projection_envelope"), dict):
        # Freshness and coverage remain owned by the typed status envelope,
        # including when this observation was served from an explicit cache.
        result["status_projection_envelope"] = status_payload["projection_envelope"]
    return result


def _build_quota_plan_for_goal(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
) -> tuple[dict[str, Any], bool]:
    plan = build_quota_plan(status_payload, mode="should-run")
    return plan, _goal_status_health_ok(
        status_payload,
        goal_id=goal_id,
        fallback=bool(plan.get("ok")),
    )


def build_quota_should_run(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None = None,
    available_capabilities: Any = None,
    include_scheduler_detail: bool = False,
    include_agent_todo_detail: bool = False,
    codex_app_current_rrule: Any = None,
    codex_app_automation_id: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    receipt_bound_todo_id: str | None = None,
    requested_action_todo_id: str | None = None,
    receipt_bound_monitor_phase: ReceiptBoundMonitorPhase | None = None,
    receipt_bound_replay_phase: ReceiptBoundReplayPhase | None = None,
    receipt_bound_terminal_phase: ReceiptBoundTerminalPhase | None = None,
    receipt_bound_replan_obligation_id: str | None = None,
    receipt_bound_replan_guard_scoped: bool = False,
    turn_instance_id: str | None = None,
    runtime_root: str | Path | None = None,
    workspace_path: Path | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    from .control_plane.quota.should_run import (
        build_quota_should_run as _build_quota_should_run,
    )

    payload = _build_quota_should_run(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
        available_capabilities=available_capabilities,
        include_scheduler_detail=include_scheduler_detail,
        include_agent_todo_detail=include_agent_todo_detail,
        codex_app_current_rrule=codex_app_current_rrule,
        codex_app_automation_id=codex_app_automation_id,
        scheduler_execution_context=scheduler_execution_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        receipt_bound_todo_id=receipt_bound_todo_id,
        requested_action_todo_id=requested_action_todo_id,
        receipt_bound_monitor_phase=receipt_bound_monitor_phase,
        receipt_bound_replay_phase=receipt_bound_replay_phase,
        receipt_bound_terminal_phase=receipt_bound_terminal_phase,
        receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
        receipt_bound_replan_guard_scoped=receipt_bound_replan_guard_scoped,
        turn_instance_id=turn_instance_id,
        runtime_root=runtime_root,
        workspace_path=workspace_path,
        goal_ref=goal_ref,
    )
    observation_root = runtime_root or status_payload.get("runtime_root")
    if goal_ref is None and agent_id and observation_root:
        from .control_plane.goals.checkpoint_context_io import pending_first_delivery_progress

        delivery_progress = pending_first_delivery_progress(
            Path(observation_root), goal_id, agent_id,
        )
        if delivery_progress is not None:
            payload["first_delivery_progress"] = delivery_progress
            # Observation cannot grant work or override an owner pause/health hold.
            if payload.get("ok") and payload.get("state") != "paused":
                payload["recommended_action"] = delivery_progress["next_action"]
    return payload


def _quota_spend_index_basis(
    status_payload: Mapping[str, Any],
    *,
    goal_id: str,
) -> tuple[bool, str | None]:
    run_history = status_payload.get("run_history")
    if not isinstance(run_history, Mapping):
        return False, None
    goals = run_history.get("goals")
    if not isinstance(goals, list):
        return False, None
    for goal in goals:
        if not isinstance(goal, Mapping) or str(goal.get("id") or "") != goal_id:
            continue
        if "index_digest" not in goal:
            return False, None
        digest = goal.get("index_digest")
        if digest is not None and not isinstance(digest, str):
            raise ValueError("quota status index digest must be a string or null")
        return True, digest
    return False, None


def build_quota_slot_preview(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    slots: int = 1,
    agent_id: str | None = None,
    workspace_path: Path | None = None,
    available_capabilities: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    turn_instance_id: str | None = None,
    effect_ref: str | None = None,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from .control_plane.quota.slot_accounting import build_quota_slot_preview_for_decision

    safe_goal_id = _validate_goal_id_path_segment(str(goal_id or ""))
    basis_available, expected_index_digest = _quota_spend_index_basis(
        status_payload,
        goal_id=safe_goal_id,
    )
    before = build_quota_should_run(
        status_payload,
        goal_id=safe_goal_id,
        agent_id=agent_id,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
    )
    preview = build_quota_slot_preview_for_decision(
        status_payload,
        goal_id=safe_goal_id,
        slots=slots,
        agent_id=agent_id,
        workspace_path=workspace_path,
        before=before,
        after_decision=lambda after_status: build_quota_should_run(
            after_status,
            goal_id=safe_goal_id,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_execution_context,
            operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        ),
        quota_status_builder=quota_status,
        self_repair_spend_actions=SELF_REPAIR_SPEND_ACTIONS,
        todo_id=todo_id,
        replan_obligation_id=replan_obligation_id,
        turn_instance_id=turn_instance_id,
        source=source,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    if preview.get("ok") and basis_available:
        preview["expected_index_digest"] = expected_index_digest
    if not effect_ref:
        return preview
    return {**preview, "effect_ref": str(effect_ref).strip()}


def record_quota_scheduler_ack(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    execute: bool = False,
    agent_id: str | None = None,
    available_capabilities: Any = None,
    surface: str = CODEX_APP_SURFACE,
    state_key: str | None = None,
    applied_rrule: str | None = None,
    reset_token: str | None = None,
    identity_signature: str | None = None,
    reason_summary: str | None = None,
    use_current_hint: bool = False,
    host_match_observed: bool = False,
    scheduler_execution_context: Mapping[str, Any]
    | SchedulerExecutionContextResolution
    | None = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    before_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    safe_goal_id = _validate_goal_id_path_segment(str(goal_id or ""))
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    before = (
        dict(before_decision)
        if isinstance(before_decision, Mapping)
        else build_quota_should_run(
            status_payload,
            goal_id=safe_goal_id,
            agent_id=safe_agent_id,
            available_capabilities=available_capabilities,
            codex_app_current_rrule=(applied_rrule if host_match_observed else None),
            scheduler_execution_context=scheduler_execution_context,
            operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        )
    )
    raw_runtime_root = status_payload.get("runtime_root")
    if not raw_runtime_root:
        raise ValueError("status payload does not include runtime_root")
    runtime_root = Path(str(raw_runtime_root)).expanduser()
    return record_quota_scheduler_ack_for_decision(
        before,
        runtime_root=runtime_root,
        goal_id=safe_goal_id,
        agent_id=safe_agent_id,
        execute=execute,
        surface=str(surface or CODEX_APP_SURFACE).strip() or CODEX_APP_SURFACE,
        state_key=str(state_key).strip() if state_key is not None else None,
        applied_rrule=applied_rrule,
        reset_token=reset_token,
        identity_signature=identity_signature,
        reason_summary=reason_summary,
        use_current_hint=use_current_hint,
        host_match_observed=host_match_observed,
    )


def build_quota_slot_spend_event(
    preview: dict[str, Any],
    *,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    generated_at: str | None = None,
) -> dict[str, Any]:
    from .control_plane.quota.spend_commit import build_quota_slot_spend_event as _build_quota_slot_spend_event

    return _build_quota_slot_spend_event(
        preview,
        self_repair_spend_actions=SELF_REPAIR_SPEND_ACTIONS,
        source=source,
        generated_at=generated_at,
    )


def record_quota_monitor_poll(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    registry_path: Path | None = None,
    execute: bool = False,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    reason_summary: str | None = None,
    agent_id: str | None = None,
    available_capabilities: Any = None,
    todo_id: str | None = None,
    target_key: str | None = None,
    result_hash: str | None = None,
    material_change: bool = False,
    cadence: str | None = None,
    next_due_at: str | None = None,
    next_agent_todo: str | None = None,
    next_action_kind: str | None = None,
    next_task_repository: str | None = None,
    next_required_capabilities: list[str] | None = None,
    next_continuation_policy: str | None = None,
    next_target_key: str | None = None,
    next_user_todo: str | None = None,
    next_user_task_class: str | None = None,
    next_claimed_by: str | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    use_current_task_lease: bool = False,
    turn_instance_id: str | None = None,
    receipt_bound_todo_id: str | None = None,
    scheduler_execution_context: Mapping[str, Any]
    | SchedulerExecutionContextResolution
    | None = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    bounded_research_frontier_projector: (
        Callable[..., Mapping[str, Any] | None] | None
    ) = None,
    status_reloader: Callable[[], dict[str, Any]] | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    safe_goal_id = _validate_goal_id_path_segment(str(goal_id or ""))
    normalized_requested_todo_id = normalize_todo_id(todo_id) if todo_id else None
    normalized_receipt_todo_id = (
        normalize_todo_id(receipt_bound_todo_id) if receipt_bound_todo_id else None
    )
    raw_runtime_root = status_payload.get("runtime_root")
    runtime_root = (
        Path(str(raw_runtime_root)).expanduser() if raw_runtime_root else None
    )
    resolved_monitor = resolve_due_monitor_candidate(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=safe_goal_id,
        todo_id=normalized_requested_todo_id,
        target_key=target_key,
    )
    normalized_observation_todo_id = normalized_requested_todo_id or (
        normalize_todo_id(resolved_monitor.get("todo_id"))
        if resolved_monitor
        else None
    )

    def should_run(current_status: dict[str, Any]) -> dict[str, Any]:
        decision_status = current_status
        if bounded_research_frontier_projector is not None:
            raw_runtime_root = current_status.get("runtime_root")
            if raw_runtime_root:
                frontier = bounded_research_frontier_projector(
                    runtime_root=Path(str(raw_runtime_root)).expanduser(),
                    goal_id=safe_goal_id,
                    agent_id=agent_id,
                    status_payload=current_status,
                )
                if isinstance(frontier, Mapping):
                    decision_status = {
                        **current_status,
                        "bounded_research_frontier": dict(frontier),
                    }
        return build_quota_should_run(
            decision_status,
            goal_id=safe_goal_id,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_execution_context,
            operator_inbox_urgency_projector=operator_inbox_urgency_projector,
            receipt_bound_todo_id=normalized_receipt_todo_id,
            goal_ref=goal_ref,
        )

    before = should_run(status_payload)
    auxiliary_settlement_todo = None
    if (
        normalized_receipt_todo_id
        and normalized_observation_todo_id
        and normalized_observation_todo_id != normalized_receipt_todo_id
    ):
        # Discovery/selection is an open-work projection, not the committed
        # Turn's lifecycle authority. Read the exact bound record, including
        # completed history; TS monitor admission owns its interpretation.
        from .todos import list_goal_todos

        if registry_path is not None:
            bound_records = list_goal_todos(
                registry_path=registry_path,
                runtime_root_arg=str(runtime_root) if runtime_root else None,
                goal_id=safe_goal_id,
                todo_id=normalized_receipt_todo_id,
                role="agent",
            )
            items = bound_records.get("todos") or []
            auxiliary_settlement_todo = items[0] if len(items) == 1 else None
    effective_todo_id = normalized_observation_todo_id or (
        normalized_receipt_todo_id if not target_key else None
    )
    result = record_quota_monitor_poll_for_decision(
        before,
        status_payload,
        goal_id=safe_goal_id,
        render_markdown=_render_quota_monitor_poll_markdown,
        after_decision=should_run,
        registry_path=registry_path,
        execute=execute,
        source=source,
        reason_summary=reason_summary,
        agent_id=agent_id,
        settlement_todo_id=normalized_receipt_todo_id,
        auxiliary_settlement_todo=auxiliary_settlement_todo,
        todo_id=effective_todo_id,
        target_key=target_key,
        result_hash=result_hash,
        material_change=material_change,
        cadence=cadence,
        next_due_at=next_due_at,
        next_agent_todo=next_agent_todo,
        next_action_kind=next_action_kind,
        next_task_repository=next_task_repository,
        next_required_capabilities=next_required_capabilities,
        next_continuation_policy=next_continuation_policy,
        next_target_key=next_target_key,
        next_user_todo=next_user_todo,
        next_user_task_class=next_user_task_class,
        next_claimed_by=next_claimed_by,
        task_lease_idempotency_key=task_lease_idempotency_key,
        task_lease_expected_version=task_lease_expected_version,
        use_current_task_lease=use_current_task_lease,
        turn_instance_id=turn_instance_id,
        status_reloader=status_reloader,
        goal_ref=goal_ref,
    )
    continuation = result.get("turn_continuation") or {}
    if (
        result.get("ok") is True
        and continuation.get("settlement_binding_matches_observation") is False
    ):
        # The open-work readback may now select another Todo. Render the
        # original receipt's closeout separately; never borrow that selection
        # to construct this Turn's refresh/spend commands.
        from .control_plane.agents.capability_gate import (
            runtime_capabilities_for_cli_projection,
        )
        from .control_plane.quota.settlement import (
            attach_settlement_progress,
            build_turn_scoped_cli_settlement_plan,
        )

        readback = read_heartbeat_settlement(
            runtime_root,
            goal_id=safe_goal_id,
            agent_id=agent_id,
            todo_id=normalized_receipt_todo_id,
            turn_instance_id=turn_instance_id,
            registry_path=registry_path,
            goal_ref=goal_ref,
        )
        if readback is None or readback.identity.value is None:
            raise RuntimeError(
                "auxiliary monitor receipt lost its original settlement readback"
            )
        attach_settlement_progress(
            result, readback, registry_path=registry_path, runtime_root=runtime_root,
            goal_ref=goal_ref,
        )
        identity = readback.identity.value
        prefix = "loopx"
        if registry_path is not None:
            prefix += f" --registry {shlex.quote(str(registry_path))}"
        prefix += f" --runtime-root {shlex.quote(str(runtime_root))}"
        scoped_args = "".join(
            f" --available-capability {shlex.quote(capability)}"
            for capability in runtime_capabilities_for_cli_projection(
                available_capabilities
            )
        )
        plan = build_turn_scoped_cli_settlement_plan(
            goal_id=identity.goal_id,
            agent_id=identity.agent_id,
            todo_id=identity.todo_id,
            turn_instance_id=identity.turn_instance_id,
            replan_obligation_id=identity.replan_obligation_id,
            command_prefix=prefix,
            scoped_cli_args=scoped_args,
            lifecycle_actor_args="",
            quota_spend_source=readback.progress["quota_spend_source"],
            goal_ref=goal_ref,
        )
        result["settlement_resume"] = {
            "schema_version": "auxiliary_monitor_settlement_resume_v0",
            "identity": identity.as_dict(),
            "progress_ref": "$.settlement_progress",
            "next_step": next(
                (
                    step for step in plan.as_dict()["ordered_steps"]
                    if step["kind"] == readback.progress["next_step"]
                ),
                None,
            ),
            "grants_new_delivery": False,
        }
        # A later discovery projection is not a second settlement plan. The
        # top-level resume/readback above owns this original Turn's closeout.
        after = result.get("after") or {}
        cli = (after.get("interaction_contract") or {}).get("cli_channel")
        if (
            isinstance(cli, dict)
            and (after.get("selected_todo") or {}).get("todo_id") != identity.todo_id
        ):
            cli.pop("settlement_plan", None)
            cli.pop("next_cli_actions", None)
            cli.update(
                spend_allowed_now=False, spend_after_validation=False,
                settlement_resume_ref="$.settlement_resume",
            )
    return result


def build_quota_slot_void_preview(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    voided_run_generated_at: str,
    agent_id: str | None = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from .control_plane.quota.void_commit import (
        build_quota_slot_void_preview_for_decision,
        normalize_quota_void_goal_id as _normalize_quota_void_goal_id,
    )

    safe_goal_id = _normalize_quota_void_goal_id(goal_id)
    before = build_quota_should_run(
        status_payload,
        goal_id=safe_goal_id,
        agent_id=agent_id,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
    )
    return build_quota_slot_void_preview_for_decision(
        status_payload,
        goal_id=safe_goal_id,
        voided_run_generated_at=voided_run_generated_at,
        before=before,
    )


def void_quota_slot(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    voided_run_generated_at: str,
    execute: bool = False,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    reason_summary: str | None = None,
    agent_id: str | None = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from .control_plane.quota.void_commit import (
        commit_quota_slot_void,
        normalize_quota_void_goal_id as _normalize_quota_void_goal_id,
    )

    safe_goal_id = _normalize_quota_void_goal_id(goal_id)
    before = build_quota_should_run(
        status_payload,
        goal_id=safe_goal_id,
        agent_id=agent_id,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
    )
    return commit_quota_slot_void(
        status_payload,
        goal_id=safe_goal_id,
        voided_run_generated_at=voided_run_generated_at,
        before=before,
        execute=execute,
        source=source,
        reason_summary=reason_summary,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )


def spend_quota_slot(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    slots: int = 1,
    execute: bool = False,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    agent_id: str | None = None,
    workspace_path: Path | None = None,
    available_capabilities: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    turn_instance_id: str | None = None,
    effect_ref: str | None = None,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from .control_plane.quota.spend_commit import replay_quota_spend_by_effect_ref
    from .control_plane.quota.slot_accounting import record_quota_slot_spend_from_preview

    safe_goal_id = _validate_goal_id_path_segment(str(goal_id or ""))
    normalized_effect_ref = str(effect_ref or "").strip()
    raw_runtime_root = status_payload.get("runtime_root")
    if effect_ref is not None and not normalized_effect_ref:
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": not execute,
            "appended": False,
            "goal_id": safe_goal_id,
            "reason": "effect_ref must be a non-empty string when provided",
        }
    if normalized_effect_ref:
        if not raw_runtime_root:
            return {
                "ok": False,
                "mode": "spend-slot",
                "dry_run": not execute,
                "appended": False,
                "goal_id": safe_goal_id,
                "effect_ref": normalized_effect_ref,
                "reason": "effect-bound quota spend requires runtime_root",
            }
        replay = replay_quota_spend_by_effect_ref(
            Path(str(raw_runtime_root)).expanduser(),
            goal_id=safe_goal_id,
            effect_ref=normalized_effect_ref,
            agent_id=agent_id,
            read_only=not execute,
            registry_path=registry_path,
            goal_ref=goal_ref,
        )
        if replay.get("replay_found"):
            if not replay.get("ok"):
                return {
                    "ok": False,
                    "mode": "spend-slot",
                    "dry_run": not execute,
                    "appended": False,
                    "goal_id": safe_goal_id,
                    "effect_ref": normalized_effect_ref,
                    "reason": (
                        "effect_ref replay requires the same valid agent identity"
                    ),
                }
            return {
                "ok": True,
                "mode": "spend-slot",
                "dry_run": not execute,
                "appended": False,
                "idempotent_replay": True,
                "goal_id": safe_goal_id,
                "agent_id": replay.get("agent_id"),
                "effect_ref": normalized_effect_ref,
                "reason": "quota spend replayed for the same provider effect",
                **(
                    {"goal_ref": dict(goal_ref)}
                    if goal_ref is not None
                    else {}
                ),
            }
    if turn_instance_id and source not in TURN_SCOPED_SLOT_SPEND_SOURCES:
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": not execute,
            "appended": False,
            "goal_id": safe_goal_id,
            "reason": (
                "turn-scoped settlement is valid only for heartbeat or "
                "visible-goal spend"
            ),
        }
    recover_unbound_visible_goal = (
        source == VISIBLE_GOAL_SLOT_SPEND_SOURCE
        and not todo_id
        and not replan_obligation_id
    )
    settlement_readback = None
    if (
        not turn_instance_id
        and raw_runtime_root
        and (
            source == DEFAULT_SLOT_SPEND_SOURCE
            or recover_unbound_visible_goal
        )
    ):
        settlement_readback = read_heartbeat_settlement(
            Path(str(raw_runtime_root)).expanduser(),
            goal_id=safe_goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            turn_instance_id=None,
            infer_turn_instance_id=True,
            allow_unbound_binding=recover_unbound_visible_goal,
            registry_path=registry_path,
            goal_ref=goal_ref,
        )
        inferred_result = (
            settlement_readback.identity
            if settlement_readback is not None
            else None
        )
        if inferred_result is not None:
            if inferred_result.failure is not None or inferred_result.value is None:
                return {
                    "ok": False,
                    "mode": "spend-slot",
                    "dry_run": not execute,
                    "appended": False,
                    "goal_id": safe_goal_id,
                    "reason": (
                        inferred_result.failure.reason
                        if inferred_result.failure is not None
                        else "persisted heartbeat settlement has no identity"
                    ),
                    "settlement_result": settlement_result_payload(inferred_result),
                }
            identity = inferred_result.value
            turn_instance_id = identity.turn_instance_id
            if recover_unbound_visible_goal:
                todo_id = identity.todo_id
                replan_obligation_id = identity.replan_obligation_id
    if turn_instance_id and raw_runtime_root:
        runtime_root = Path(str(raw_runtime_root)).expanduser()
        if settlement_readback is None:
            settlement_readback = read_heartbeat_settlement(
                runtime_root,
                goal_id=safe_goal_id,
                agent_id=agent_id,
                todo_id=todo_id,
                turn_instance_id=turn_instance_id,
                replan_obligation_id=replan_obligation_id,
                registry_path=registry_path,
                goal_ref=goal_ref,
            )
        if settlement_readback is None:
            raise RuntimeError("exact settlement readback unexpectedly returned not-found")
        guard_result = settlement_readback.identity
        if guard_result.failure is not None or guard_result.value is None:
            return {
                "ok": False,
                "mode": "spend-slot",
                "dry_run": not execute,
                "appended": False,
                "goal_id": safe_goal_id,
                "reason": (
                    guard_result.failure.reason
                    if guard_result.failure is not None
                    else "turn-scoped spend has no settlement identity"
                ),
                "settlement_result": settlement_result_payload(guard_result),
            }
        identity = guard_result.value
        spent_result = settlement_readback.settlement
        if spent_result.failure is None:
            return {
                "ok": True,
                "mode": "spend-slot",
                "dry_run": not execute,
                "appended": False,
                "idempotent_replay": True,
                "goal_id": safe_goal_id,
                "agent_id": identity.agent_id,
                "todo_id": identity.todo_id,
                "replan_obligation_id": identity.replan_obligation_id,
                "turn_instance_id": identity.turn_instance_id,
                "settlement_identity": identity.as_dict(),
                "settlement_result": settlement_result_payload(spent_result),
                "reason": "quota spend receipt replayed for the same settlement identity",
                **(
                    {"goal_ref": dict(goal_ref)}
                    if goal_ref is not None
                    else {}
                ),
            }
        prior_spend_run = settlement_readback.spend_run
        if prior_spend_run is not None:
            return {
                "ok": True,
                "mode": "spend-slot",
                "dry_run": not execute,
                "appended": False,
                "receipt_repair_required": bool(execute),
                "goal_id": safe_goal_id,
                "agent_id": identity.agent_id,
                "todo_id": identity.todo_id,
                "replan_obligation_id": identity.replan_obligation_id,
                "turn_instance_id": identity.turn_instance_id,
                "settlement_identity": identity.as_dict(),
                "settlement_result": settlement_result_payload(spent_result),
                "reason": "quota spend run exists; repair its missing settlement receipt",
                **(
                    {"goal_ref": dict(goal_ref)}
                    if goal_ref is not None
                    else {}
                ),
            }
    preview = build_quota_slot_preview(
        status_payload,
        goal_id=safe_goal_id,
        slots=slots,
        agent_id=agent_id,
        workspace_path=workspace_path,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        todo_id=todo_id,
        replan_obligation_id=replan_obligation_id,
        turn_instance_id=turn_instance_id,
        effect_ref=normalized_effect_ref or None,
        source=source,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    if not preview.get("ok"):
        return preview

    return record_quota_slot_spend_from_preview(
        preview,
        status_payload,
        goal_id=safe_goal_id,
        execute=execute,
        source=source,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
