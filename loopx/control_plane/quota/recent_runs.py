from __future__ import annotations

from typing import Any, cast


from ..todos.contract import normalize_todo_claimed_by, normalize_todo_id
from ..work_items.delivery_outcome import MATERIAL_DELIVERY_OUTCOMES
from .monitor_poll import QUOTA_MONITOR_POLL_CLASSIFICATION


MONITOR_DEBT_UNCHANGED_TURN_THRESHOLD = 2
NON_WORK_RUN_CLASSIFICATIONS = {"state_refreshed"}


def goal_latest_run(goal: dict[str, Any]) -> dict[str, Any]:
    """Return the latest run object for one goal, or an empty mapping."""

    latest_runs = (
        cast(list[Any], goal.get("latest_runs"))
        if isinstance(goal.get("latest_runs"), list)
        else []
    )
    if latest_runs and isinstance(latest_runs[0], dict):
        return latest_runs[0]
    return {}


def _run_is_unchanged_monitor_observation(run: dict[str, Any]) -> bool:
    if str(run.get("classification") or "") != QUOTA_MONITOR_POLL_CLASSIFICATION:
        return False
    monitor_event = (
        cast(dict[str, Any], run.get("monitor_event")) if isinstance(run.get("monitor_event"), dict) else {}
    )
    if monitor_event.get("material_change") is True or run.get("material_change") is True:
        return False
    monitor_mode = str(monitor_event.get("monitor_mode") or "").strip()
    health_check = str(run.get("health_check") or "").strip().lower()
    return bool(
        monitor_mode.endswith("_observed_without_material_transition")
        or "monitor observation unchanged" in health_check
        or "due monitor observation unchanged" in health_check
        or "external monitor observation unchanged" in health_check
        or "monitor-only poll unchanged" in health_check
    )


def _run_is_controller_bookkeeping(run: dict[str, Any]) -> bool:
    classification = str(run.get("classification") or "").strip()
    return bool(
        classification.startswith("quota_slot_")
        or classification.startswith("quota_scheduler_")
        or classification in NON_WORK_RUN_CLASSIFICATIONS
    )


def consecutive_unchanged_monitor_observations(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None = None,
    scan_limit: int = 12,
) -> list[dict[str, Any]]:
    """Return the latest consecutive unchanged monitor-only work turns.

    Scheduler ACKs, quota accounting, and state refreshes are controller
    bookkeeping rather than work-lane progress, so they do not break the
    streak. Any material monitor transition or non-monitor work event does.
    """

    safe_agent_id = normalize_todo_claimed_by(agent_id)
    observations: list[dict[str, Any]] = []
    for run in goal_latest_runs(status_payload, goal_id=goal_id)[: max(1, scan_limit)]:
        run_agent_id = normalize_todo_claimed_by(run.get("agent_id"))
        if safe_agent_id and run_agent_id and run_agent_id != safe_agent_id:
            continue
        if _run_is_controller_bookkeeping(run):
            continue
        if not _run_is_unchanged_monitor_observation(run):
            break
        monitor_event = (
            cast(dict[str, Any], run.get("monitor_event"))
            if isinstance(run.get("monitor_event"), dict)
            else {}
        )
        observations.append(
            {
                "classification": QUOTA_MONITOR_POLL_CLASSIFICATION,
                "generated_at": run.get("generated_at"),
                "agent_id": run_agent_id or None,
                "todo_id": monitor_event.get("todo_id") or run.get("todo_id"),
                "target_key": monitor_event.get("target_key") or run.get("target_key"),
                "monitor_mode": str(monitor_event.get("monitor_mode") or "").strip()
                or "monitor_observed_without_material_transition",
            }
        )
    return observations


def build_monitor_debt_arbitration(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None = None,
    threshold: int = MONITOR_DEBT_UNCHANGED_TURN_THRESHOLD,
) -> dict[str, Any]:
    safe_threshold = max(1, int(threshold))
    observations = consecutive_unchanged_monitor_observations(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    count = len(observations)
    return {
        "schema_version": "monitor_debt_arbitration_v0",
        "active": count >= safe_threshold,
        "consecutive_unchanged_monitor_turns": count,
        "threshold": safe_threshold,
        "policy": (
            "after the threshold, same-or-higher-priority runnable advancement "
            "wins over unchanged monitor catch-up; reply_due and material "
            "monitor transitions reset or preempt this backoff"
        ),
    }


def goal_latest_runs(status_payload: dict[str, Any], *, goal_id: str) -> list[dict[str, Any]]:
    run_history = (
        cast(dict[str, Any], status_payload.get("run_history"))
        if isinstance(status_payload.get("run_history"), dict)
        else {}
    )
    goals = cast(list[Any], run_history.get("goals")) if isinstance(run_history.get("goals"), list) else []
    goal = next(
        (
            candidate
            for candidate in goals
            if isinstance(candidate, dict) and str(candidate.get("id") or "") == goal_id
        ),
        None,
    )
    if not isinstance(goal, dict):
        return []
    runs = cast(list[Any], goal.get("latest_runs")) if isinstance(goal.get("latest_runs"), list) else []
    return [run for run in runs if isinstance(run, dict)]


def latest_accountable_agent_delivery(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    scan_limit: int = 12,
) -> dict[str, Any] | None:
    """Return the latest same-agent delivery anchor, ignoring controller ACKs.

    A newer same-agent work event without a typed Todo binding intentionally
    breaks continuity. Peer-agent events do not steal this agent's lane.
    """

    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if not safe_agent_id:
        return None
    for run in goal_latest_runs(status_payload, goal_id=goal_id)[: max(1, scan_limit)]:
        if _run_is_controller_bookkeeping(run):
            continue
        run_agent_id = normalize_todo_claimed_by(run.get("agent_id"))
        if run_agent_id and run_agent_id != safe_agent_id:
            continue
        if run_agent_id != safe_agent_id:
            continue
        todo_id = normalize_todo_id(run.get("todo_id"))
        delivery_outcome = str(run.get("delivery_outcome") or "").strip()
        if not todo_id or delivery_outcome not in MATERIAL_DELIVERY_OUTCOMES:
            return None
        checkpoint = run.get("vision_checkpoint")
        vision = run.get("agent_vision")
        return {
            "todo_id": todo_id,
            "delivery_outcome": delivery_outcome,
            "generated_at": run.get("generated_at"),
            # The run-history codec omits the long-standing default boundary.
            **({"delivery_boundary": checkpoint.get("delivery_boundary", "semantic_closeout")}
               if isinstance(checkpoint, dict) and checkpoint.get("agent_id") == safe_agent_id
               and checkpoint.get("schema_version") == "vision_checkpoint_v0" else {}),
            **({"path_delta": vision["path_delta"]}
               if isinstance(vision, dict) and vision.get("agent_id") == safe_agent_id
               and isinstance(vision.get("path_delta"), dict) else {}),
        }
    return None


def recent_external_monitor_observation_unchanged(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None = None,
    scan_limit: int = 8,
) -> dict[str, Any] | None:
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    for run in goal_latest_runs(status_payload, goal_id=goal_id)[: max(1, scan_limit)]:
        run_agent_id = normalize_todo_claimed_by(run.get("agent_id"))
        if safe_agent_id and run_agent_id and run_agent_id != safe_agent_id:
            continue
        if str(run.get("classification") or "") != QUOTA_MONITOR_POLL_CLASSIFICATION:
            continue
        monitor_event = cast(dict[str, Any], run.get("monitor_event")) if isinstance(run.get("monitor_event"), dict) else {}
        monitor_mode = str(monitor_event.get("monitor_mode") or "").strip()
        if monitor_event.get("material_change") is True:
            return None
        if _run_is_unchanged_monitor_observation(run):
            return {
                "classification": QUOTA_MONITOR_POLL_CLASSIFICATION,
                "generated_at": run.get("generated_at"),
                "agent_id": run_agent_id or None,
                "monitor_mode": monitor_mode or "monitor_observed_without_material_transition",
                "reason": "recent monitor observation was unchanged",
            }
    return None


def latest_unchanged_monitor_observation(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None = None,
    scan_limit: int = 8,
) -> dict[str, Any] | None:
    """Return an unchanged monitor poll only when it is the latest work event.

    Quota accounting, scheduler acknowledgements, and state refreshes do not
    start a new work lane. Any other current-agent or legacy-unscoped run does,
    so an older monitor poll cannot suppress monitor priority after later
    delivery.
    """

    safe_agent_id = normalize_todo_claimed_by(agent_id)
    for run in goal_latest_runs(status_payload, goal_id=goal_id)[: max(1, scan_limit)]:
        run_agent_id = normalize_todo_claimed_by(run.get("agent_id"))
        if safe_agent_id and run_agent_id and run_agent_id != safe_agent_id:
            continue
        classification = str(run.get("classification") or "").strip()
        if _run_is_controller_bookkeeping(run):
            continue
        if classification != QUOTA_MONITOR_POLL_CLASSIFICATION:
            return None
        monitor_event = cast(dict[str, Any], run.get("monitor_event")) if isinstance(run.get("monitor_event"), dict) else {}
        if not _run_is_unchanged_monitor_observation(run):
            return None
        monitor_mode = str(monitor_event.get("monitor_mode") or "").strip()
        return {
            "classification": QUOTA_MONITOR_POLL_CLASSIFICATION,
            "generated_at": run.get("generated_at"),
            "agent_id": run_agent_id or None,
            "monitor_mode": monitor_mode or "monitor_observed_without_material_transition",
            "reason": "latest work event was an unchanged monitor observation",
        }
    return None
