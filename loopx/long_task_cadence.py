from __future__ import annotations

from typing import Any, cast


from .control_plane.work_items.delivery_batch_scale import DeliveryBatchScale, normalize_delivery_batch_scale
from .control_plane.work_items.delivery_outcome import (
    DeliveryOutcome,
    DeliveryTurnKind,
    normalize_delivery_outcome,
    normalize_delivery_turn_kind,
)
from .execution_profile import (
    compact_execution_profile,
    execution_profile_is_fine_grained,
    execution_profile_threshold,
)


LONG_TASK_CADENCE_HINT_SCHEMA_VERSION = "cadence_hint_v0"
FINAL_AGENT_SCOPED_CADENCE_AUTHORITY = (
    "final_agent_scoped_interaction_and_scheduler"
)

BLOCKED_QUOTA_STATES = frozenset(
    {
        "blocked",
        "blocked_health",
        "focus_wait",
        "monitor_only",
        "operator_gate",
        "paused",
        "throttled",
        "user_gate",
        "waiting",
    }
)
SMALL_PROGRESS_GRANULARITIES = frozenset({"status_only", "single_surface"})
MATERIAL_PROGRESS_GRANULARITIES = frozenset(
    {"multi_surface", "implementation_plus_validation", "milestone"}
)


def _progress_granularity(run: dict[str, Any] | None) -> str:
    if not isinstance(run, dict) or not run:
        return "status_only"

    outcome = normalize_delivery_outcome(run.get("delivery_outcome"))
    turn_kind = normalize_delivery_turn_kind(run.get("delivery_turn_kind"))
    scale = normalize_delivery_batch_scale(run.get("delivery_batch_scale"))

    if outcome == DeliveryOutcome.PRIMARY_GOAL_OUTCOME:
        return "milestone"
    if outcome == DeliveryOutcome.OUTCOME_PROGRESS:
        return "implementation_plus_validation"
    if turn_kind == DeliveryTurnKind.PRODUCT_PATH_EXECUTION:
        return "implementation_plus_validation"
    if scale == DeliveryBatchScale.IMPLEMENTATION:
        return "implementation_plus_validation"
    if scale == DeliveryBatchScale.MULTI_SURFACE or turn_kind == DeliveryTurnKind.COMPACT_EVIDENCE:
        return "multi_surface"
    if scale in {DeliveryBatchScale.TEST_ONLY, DeliveryBatchScale.SINGLE_SURFACE}:
        return "single_surface"
    if turn_kind == DeliveryTurnKind.CONTRACT_ONLY_PREPARATION:
        return "single_surface"
    return "status_only"


def _small_step_streak(runs: list[dict[str, Any]]) -> int:
    streak = 0
    for run in runs:
        turn_kind = normalize_delivery_turn_kind(run.get("delivery_turn_kind"))
        if turn_kind == DeliveryTurnKind.BLOCKER_WRITEBACK:
            break
        if _progress_granularity(run) not in SMALL_PROGRESS_GRANULARITIES:
            break
        streak += 1
    return streak


def _recent_runs(
    *,
    latest_runs: list[dict[str, Any]] | None,
    handoff_readiness: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    readiness = handoff_readiness if isinstance(handoff_readiness, dict) else {}
    compact_runs = (
        cast(list[Any], readiness.get("post_handoff_recent_runs"))
        if isinstance(readiness.get("post_handoff_recent_runs"), list)
        else []
    )
    if compact_runs:
        return [run for run in compact_runs if isinstance(run, dict)]
    latest_run = readiness.get("post_handoff_latest_run")
    if isinstance(latest_run, dict) and latest_run:
        return [latest_run]
    runs = [run for run in latest_runs or [] if isinstance(run, dict)]
    if runs:
        return runs
    return []


def _reason_from_granularity(granularity: str) -> str:
    return f"{granularity}_latest_turn"


def _blocked_reason(quota_state: str, user_todo_open_count: int | None) -> list[str]:
    reasons = [f"quota_state_{quota_state}"]
    if user_todo_open_count and user_todo_open_count > 0:
        reasons.append("open_user_todos_visible")
    return reasons


def build_long_task_cadence_hint(
    *,
    execution_profile: dict[str, Any] | None = None,
    latest_runs: list[dict[str, Any]] | None = None,
    handoff_readiness: dict[str, Any] | None = None,
    quota_state: str | None = None,
    user_todo_open_count: int | None = None,
) -> dict[str, Any]:
    """Build a small, public-safe cadence hint from existing control signals."""

    profile = compact_execution_profile(execution_profile)
    runs = _recent_runs(latest_runs=latest_runs, handoff_readiness=handoff_readiness)
    latest_run = runs[0] if runs else {}
    threshold = execution_profile_threshold(profile)
    small_step_streak = _small_step_streak(runs)
    normalized_quota_state = str(quota_state or "").strip()

    if normalized_quota_state in BLOCKED_QUOTA_STATES:
        return {
            "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
            "signal": "blocked",
            "recommendation": "wait",
            "reason_codes": _blocked_reason(normalized_quota_state, user_todo_open_count),
        }

    if not runs:
        return {
            "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
            "signal": "unknown",
            "recommendation": "keep",
            "reason_codes": ["missing_recent_runs"],
        }

    granularity = _progress_granularity(latest_run)
    if granularity in SMALL_PROGRESS_GRANULARITIES:
        threshold_reached = small_step_streak >= threshold
        fine_grained = execution_profile_is_fine_grained(profile)
        recommendation = (
            "replan"
            if fine_grained and threshold_reached
            else "widen"
            if threshold_reached
            else "keep"
        )
        reason = (
            "fine_direction_review_due"
            if recommendation == "replan"
            else "repeated_surface_only"
            if recommendation == "widen"
            else "fine_checkpoint_within_slice"
            if fine_grained
            else _reason_from_granularity(granularity)
        )
        return {
            "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
            "signal": "thin_progress",
            "recommendation": recommendation,
            "reason_codes": [reason],
        }

    if granularity in MATERIAL_PROGRESS_GRANULARITIES:
        return {
            "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
            "signal": "material_progress",
            "recommendation": "keep",
            "reason_codes": [_reason_from_granularity(granularity)],
        }

    return {
        "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
        "signal": "unknown",
        "recommendation": "keep",
        "reason_codes": [_reason_from_granularity(granularity)],
    }


def build_long_task_cadence_policy(**kwargs: Any) -> dict[str, Any]:
    """Compatibility wrapper for older callers while this projection rolls out."""

    return build_long_task_cadence_hint(**kwargs)


def reconcile_long_task_cadence_hint(
    value: Any,
    *,
    interaction_contract: Any,
    scheduler_hint: Any,
) -> dict[str, Any] | None:
    """Align a legacy wait advisory with final agent-scoped run authority."""

    if not isinstance(value, dict):
        return None
    hint = dict(value)
    if hint.get("recommendation") != "wait":
        return hint

    contract = interaction_contract if isinstance(interaction_contract, dict) else {}
    agent_channel = (
        cast(dict[str, Any], contract.get("agent_channel"))
        if isinstance(contract.get("agent_channel"), dict)
        else {}
    )
    scheduler = scheduler_hint if isinstance(scheduler_hint, dict) else {}
    final_authority_requires_work = (
        agent_channel.get("must_attempt") is True
        and agent_channel.get("quiet_noop_allowed") is False
        and scheduler.get("action") == "run_now"
        and scheduler.get("cadence_class") == "active_work"
    )
    if not final_authority_requires_work:
        return hint

    prior_reason_codes = hint.get("reason_codes")
    superseded = {
        "signal": hint.get("signal"),
        "recommendation": hint.get("recommendation"),
        "reason_codes": (
            list(prior_reason_codes) if isinstance(prior_reason_codes, list) else []
        ),
    }
    return {
        "schema_version": hint.get(
            "schema_version", LONG_TASK_CADENCE_HINT_SCHEMA_VERSION
        ),
        "signal": "active_work",
        "recommendation": "keep",
        "reason_codes": ["final_agent_scoped_active_work"],
        "authority": FINAL_AGENT_SCOPED_CADENCE_AUTHORITY,
        "superseded": superseded,
    }


def long_task_cadence_hint_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    reason_codes = value.get("reason_codes")
    if isinstance(reason_codes, list):
        reason_text = ",".join(str(reason) for reason in reason_codes)
    else:
        reason_text = ""
    return (
        f"signal={value.get('signal')} "
        f"recommendation={value.get('recommendation')} "
        f"reasons={reason_text}"
    )


def long_task_cadence_summary(value: Any) -> str:
    return long_task_cadence_hint_summary(value)
