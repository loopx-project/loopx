from __future__ import annotations

from typing import Any, cast


from ..goal_vision_read_model import latest_agent_vision_from_runs as latest_agent_vision_from_runs

from ...work_items.autonomous_replan_ack import (
    latest_autonomous_replan_ack_for_projection,
)

VISION_CHECKPOINT_SATISFIED_DECISIONS = {
    "patched",
    "unchanged_with_reason",
}


def _run_history_goal(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
) -> dict[str, Any] | None:
    run_history = (
        cast(dict[str, Any], status_payload.get("run_history"))
        if isinstance(status_payload.get("run_history"), dict)
        else {}
    )
    goals = (
        cast(list[Any], run_history.get("goals")) if isinstance(run_history.get("goals"), list) else []
    )
    return next(
        (
            item
            for item in goals
            if isinstance(item, dict) and str(item.get("id") or "") == goal_id
        ),
        None,
    )


def _latest_runs_for_goal(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
) -> list[dict[str, Any]]:
    goal = _run_history_goal(status_payload, goal_id=goal_id)
    latest_runs = goal.get("latest_runs") if isinstance(goal, dict) else None
    return (
        [item for item in latest_runs if isinstance(item, dict)]
        if isinstance(latest_runs, list)
        else []
    )


def _semantic_agent_context_for_goal(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> tuple[bool, dict[str, Any] | None]:
    """Return whether semantic history is authoritative and its agent lane."""

    if not agent_id:
        return False, None
    goal = _run_history_goal(status_payload, goal_id=goal_id)
    semantic_history = goal.get("semantic_history") if isinstance(goal, dict) else None
    if not isinstance(semantic_history, dict):
        return False, None
    contexts = semantic_history.get("agents")
    if not isinstance(contexts, list):
        return True, None
    return True, next(
        (
            context
            for context in contexts
            if isinstance(context, dict)
            and str(context.get("agent_id") or "").strip() == agent_id
        ),
        None,
    )


def _run_agent_id_matches(run: dict[str, Any], *, agent_id: str | None) -> bool:
    if not agent_id:
        return True
    run_agent_id = str(run.get("agent_id") or "").strip()
    return not run_agent_id or run_agent_id == agent_id


def latest_agent_vision_from_status_payload(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Return the newest compact agent vision packet visible in run history."""

    semantic_history_present, context = _semantic_agent_context_for_goal(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    if semantic_history_present:
        if not isinstance(context, dict) or context.get("vision_retired_at"):
            return None
        vision_run = context.get("latest_agent_vision_run")
        return (
            latest_agent_vision_from_runs(
                [vision_run],
                goal_id=goal_id,
                agent_id=agent_id,
            )
            if isinstance(vision_run, dict)
            else None
        )
    return latest_agent_vision_from_runs(
        _latest_runs_for_goal(status_payload, goal_id=goal_id),
        goal_id=goal_id,
        agent_id=agent_id,
    )


def _latest_missing_vision_checkpoint_from_runs(
    runs: list[dict[str, Any]],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    for run in runs:
        checkpoint = run.get("vision_checkpoint")
        if not isinstance(checkpoint, dict):
            continue
        checkpoint_agent_id = str(
            checkpoint.get("agent_id") or run.get("agent_id") or ""
        ).strip()
        if agent_id and checkpoint_agent_id != agent_id:
            continue
        if not agent_id and checkpoint_agent_id:
            continue
        decision = str(checkpoint.get("decision") or "").strip()
        if (
            checkpoint.get("satisfied") is True
            and decision in VISION_CHECKPOINT_SATISFIED_DECISIONS
        ):
            return None
        if checkpoint.get("required") is not True:
            continue
        if checkpoint.get("satisfied") is not False:
            continue
        if decision != "missing_required":
            continue
        return {
            "schema_version": checkpoint.get("schema_version"),
            "goal_id": goal_id,
            "agent_id": checkpoint_agent_id or agent_id,
            "decision": checkpoint.get("decision"),
            "triggers": cast(list[Any], checkpoint.get("triggers"))
            if isinstance(checkpoint.get("triggers"), list)
            else [],
            "required_resolution": cast(list[Any], checkpoint.get("required_resolution"))
            if isinstance(checkpoint.get("required_resolution"), list)
            else [],
            "missing_baseline": checkpoint.get("missing_baseline") is True,
            "generated_at": run.get("generated_at"),
        }
    return None


def latest_missing_vision_checkpoint_from_status_payload(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Return the newest unsatisfied per-agent vision checkpoint in run history."""

    semantic_history_present, context = _semantic_agent_context_for_goal(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    if semantic_history_present:
        checkpoint_run = (
            context.get("latest_vision_checkpoint_run")
            if isinstance(context, dict)
            else None
        )
        return _latest_missing_vision_checkpoint_from_runs(
            [checkpoint_run] if isinstance(checkpoint_run, dict) else [],
            goal_id=goal_id,
            agent_id=agent_id,
        )
    return _latest_missing_vision_checkpoint_from_runs(
        _latest_runs_for_goal(status_payload, goal_id=goal_id),
        goal_id=goal_id,
        agent_id=agent_id,
    )


def latest_autonomous_replan_ack_from_status_payload(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    neutral_classifications: set[str],
) -> dict[str, Any] | None:
    """Return the newest agent-scoped durable replan ACK visible in run history."""

    if not agent_id:
        return None
    semantic_history_present, context = _semantic_agent_context_for_goal(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    if semantic_history_present:
        ack_run = (
            context.get("latest_autonomous_replan_ack_run")
            if isinstance(context, dict)
            else None
        )
        latest_runs = [ack_run] if isinstance(ack_run, dict) else []
    else:
        latest_runs = [
            run
            for run in _latest_runs_for_goal(status_payload, goal_id=goal_id)
            if _run_agent_id_matches(run, agent_id=agent_id)
        ]
    return latest_autonomous_replan_ack_for_projection(
        latest_runs,
        neutral_classifications=neutral_classifications,
    )


def latest_replan_ack_feedback_from_status_payload(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Return the newest rejected replan ACK reason for one agent lane."""

    semantic_history_present, context = _semantic_agent_context_for_goal(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    if semantic_history_present:
        run = (
            context.get("latest_replan_ack_feedback_run")
            if isinstance(context, dict)
            else None
        )
        candidates = [run] if isinstance(run, dict) else []
    else:
        candidates = _latest_runs_for_goal(status_payload, goal_id=goal_id)
    for run in candidates:
        if not _run_agent_id_matches(run, agent_id=agent_id):
            continue
        feedback = run.get("replan_ack_feedback")
        if isinstance(feedback, dict):
            return feedback
    return None


def latest_lane_recommendation_from_status(
    status_payload: dict[str, Any], *, goal_id: str, agent_id: str,
) -> dict[str, Any] | None:
    """Reuse the retained recommendation receipt beyond the recent-run window."""
    from ...work_items.recommendation_source_io import latest_bound_recommendation

    present, context = _semantic_agent_context_for_goal(
        status_payload, goal_id=goal_id, agent_id=agent_id)
    if present:
        row = context.get("latest_lane_step_run") if isinstance(context, dict) else None
        return latest_bound_recommendation([row], agent_id) if isinstance(row, dict) else None
    return latest_bound_recommendation(_latest_runs_for_goal(status_payload, goal_id=goal_id), agent_id)


def receipt_bound_vision_history_candidate(
    runs: list[dict[str, Any]], *, goal_id: str, agent_id: str,
    selected_obligation_id: str, guard_recorded_at: str | None,
    neutral_classifications: set[str], goal_status: str | None,
) -> dict[str, Any] | None:
    """Recover only a plain Vision source from canonical history at admission.

    This is a write-time query over the complete scoped history, not the
    current status window or its already folded semantic slots. A later
    checkpoint supplement is not an ACK of the admitted review. Reuse the
    ordinary source reducers, preserve their full triggers and stop, and leave
    fresh outcome qualification and once-only settlement to their owners.
    """
    from ...runtime.time import parse_timestamp
    from . import (
        acceptance_gaps_from_agent_vision, derive_goal_frontier_replan_obligation_from_summaries,
        outcome_continuity,
    )

    cutoff = parse_timestamp(guard_recorded_at)
    if not agent_id or cutoff is None or cutoff.tzinfo is None:
        return None
    historical = []
    for run in runs:
        timestamp = parse_timestamp(run.get("generated_at"))
        if (run.get("goal_id") == goal_id and run.get("agent_id") == agent_id
                and timestamp is not None and timestamp.tzinfo is not None and timestamp <= cutoff):
            historical.append((timestamp, run))
    if not historical:
        return None
    historical.sort(key=lambda item: item[0], reverse=True)
    status = {"run_history": {"goals": [{"id": goal_id,
        "latest_runs": [run for _, run in historical]}]}}
    vision = latest_agent_vision_from_status_payload(status, goal_id=goal_id, agent_id=agent_id)
    missing = latest_missing_vision_checkpoint_from_status_payload(status, goal_id=goal_id, agent_id=agent_id)
    checkpoint = outcome_continuity.latest_outcome_vision_checkpoint_from_status_payload(
        status, goal_id=goal_id, agent_id=agent_id)
    gaps = (acceptance_gaps_from_agent_vision(vision, goal_status=goal_status)
        + outcome_continuity.acceptance_gaps_from_vision_checkpoint(missing)
        + outcome_continuity.acceptance_gaps_from_outcome_checkpoint(vision, checkpoint))
    if not gaps or any(gap.get("kind") not in {
        "vision_acceptance_gap", "vision_checkpoint_missing", "vision_outcome_checkpoint_required",
    } for gap in gaps):
        return None
    ack = latest_autonomous_replan_ack_from_status_payload(status, goal_id=goal_id,
        agent_id=agent_id, neutral_classifications=neutral_classifications)
    source = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary=None, agent_todo_summary=None, work_lane_contract=None,
        agent_id=agent_id, existing_replan_obligation=None,
        latest_replan_ack=ack, acceptance_gaps=gaps,
        vision_settlement_query=True)
    if not source or source.get("obligation_id") != selected_obligation_id:
        return None
    return {"obligation": source, "ack": None, "reconstructed_vision_review": True}
