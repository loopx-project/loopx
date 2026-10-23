from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ...agents.agent_scope import agent_scope_item_claimed_by
from ...work_items.autonomous_replan_ack import accepted_blocked_replan_history_indexes
from ..goal_vision_policy import (
    COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD,
)
from ..goal_vision_state import goal_vision_state_is_closed

VISION_OUTCOME_CHECKPOINT_REQUIRED_TRIGGER = "vision_outcome_checkpoint_required"
VISION_CHECKPOINT_MISSING_TRIGGER = "vision_checkpoint_missing"
VISION_OUTCOME_CHECKPOINT_MATERIAL_OUTCOMES = {
    "outcome_gap",
    "outcome_progress",
    "primary_goal_outcome",
}
VISION_OUTCOME_CHECKPOINT_CONTINUATION_OUTCOMES = {
    "continue",
    "no_change",
    "replan",
}


class OutcomeCheckpointReason(StrEnum):
    CLAIM_MISSING = "final_outcome_claim_missing"
    INCOMPLETE = "final_outcome_checkpoint_incomplete"


def _compact_text(value: Any, *, limit: int) -> str | None:
    text = " ".join(str(value or "").strip().split())
    return text[:limit] if text else None


def _dict_field(value: dict[str, Any], key: str) -> dict[str, Any]:
    field = value.get(key)
    return field if isinstance(field, dict) else {}


def _run_history_goal(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
) -> dict[str, Any] | None:
    run_history = _dict_field(status_payload, "run_history")
    goals_value = run_history.get("goals")
    goals: list[Any] = goals_value if isinstance(goals_value, list) else []
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


def _outcome_checkpoint_runs_for_goal(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> list[dict[str, Any]]:
    """Prefer the per-agent semantic slot and fall back to legacy run lists."""

    goal = _run_history_goal(status_payload, goal_id=goal_id)
    semantic_history = goal.get("semantic_history") if isinstance(goal, dict) else None
    if agent_id and isinstance(semantic_history, dict):
        contexts = semantic_history.get("agents")
        context = (
            next(
                (
                    item
                    for item in contexts
                    if isinstance(item, dict)
                    and str(item.get("agent_id") or "").strip() == agent_id
                ),
                None,
            )
            if isinstance(contexts, list)
            else None
        )
        checkpoint_run = (
            context.get("latest_outcome_vision_checkpoint_run")
            if isinstance(context, dict)
            else None
        )
        return [checkpoint_run] if isinstance(checkpoint_run, dict) else []
    runs = _latest_runs_for_goal(status_payload, goal_id=goal_id)
    administrative = accepted_blocked_replan_history_indexes(runs)
    return [run for index, run in enumerate(runs) if index not in administrative]


def latest_outcome_vision_checkpoint_from_status_payload(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Return the newest outcome-relevant per-agent vision checkpoint."""

    for run in _outcome_checkpoint_runs_for_goal(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    ):
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
        trigger_kinds = {
            str(trigger.get("kind") or "").strip()
            for trigger in (checkpoint.get("triggers") or [])
            if isinstance(trigger, dict)
            and str(trigger.get("kind") or "").strip()
        }
        if "material_delivery_outcome" not in trigger_kinds:
            continue
        result: dict[str, Any] = {
            "schema_version": checkpoint.get("schema_version"),
            "goal_id": goal_id,
            "agent_id": checkpoint_agent_id or agent_id,
            "required": checkpoint.get("required") is True,
            "satisfied": checkpoint.get("satisfied") is True,
            "decision": checkpoint.get("decision"),
            "triggers": checkpoint.get("triggers")
            if isinstance(checkpoint.get("triggers"), list)
            else [],
            "generated_at": run.get("generated_at"),
        }
        continuity_basis = checkpoint.get("continuity_basis")
        if isinstance(continuity_basis, dict):
            result["continuity_basis"] = dict(continuity_basis)
        run_vision = run.get("agent_vision")
        if isinstance(run_vision, dict):
            result["qualification_agent_vision"] = {
                "agent_id": run_vision.get("agent_id") or checkpoint_agent_id,
                "state": run_vision.get("state"),
                "vision_patch": _dict_field(run_vision, "vision_patch"),
                "path_delta": _dict_field(run_vision, "path_delta"),
                "generated_at": run.get("generated_at"),
            }
        return result
    return None


def acceptance_gaps_from_vision_checkpoint(
    checkpoint: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Convert a missing per-agent vision checkpoint into a frontier gap."""

    if not isinstance(checkpoint, dict):
        return []
    trigger_kinds = [
        str(trigger.get("kind") or "").strip()
        for trigger in (checkpoint.get("triggers") or [])
        if isinstance(trigger, dict) and str(trigger.get("kind") or "").strip()
    ]
    trigger_text = ", ".join(trigger_kinds[:3]) or "required vision checkpoint"
    missing_baseline = checkpoint.get("missing_baseline") is True
    gap: dict[str, Any] = {
        "kind": VISION_CHECKPOINT_MISSING_TRIGGER,
        "source": "latest_vision_checkpoint",
        "agent_id": checkpoint.get("agent_id"),
        "decision": checkpoint.get("decision"),
        "replan_trigger_summary": (
            "refresh-state tried to keep vision unchanged without a persisted "
            f"per-agent baseline; triggers={trigger_text}"
            if missing_baseline
            else "refresh-state closed a material segment without a per-agent vision "
            f"decision; triggers={trigger_text}"
        ),
        "acceptance_summary": (
            "Write a bounded agent vision patch, or retire/supersede the frontier "
            "with an explicit rationale."
            if missing_baseline
            else "Write a bounded agent vision patch, record an unchanged reason, "
            "or retire/supersede the frontier with an explicit rationale."
        ),
    }
    if missing_baseline:
        gap["missing_baseline"] = True
    generated_at = _compact_text(checkpoint.get("generated_at"), limit=80)
    if generated_at:
        gap["generated_at"] = generated_at
    return [gap]


def acceptance_gaps_from_outcome_checkpoint(
    agent_vision: dict[str, Any] | None,
    checkpoint: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Require a fresh evidence-linked path decision after material closeout."""

    if not isinstance(agent_vision, dict) or not isinstance(checkpoint, dict):
        return []
    if goal_vision_state_is_closed(str(agent_vision.get("state") or "")):
        return []
    qualification_vision_value = checkpoint.get("qualification_agent_vision")
    qualification_vision = (
        qualification_vision_value
        if isinstance(qualification_vision_value, dict)
        else agent_vision
    )
    vision_agent_id = str(qualification_vision.get("agent_id") or "").strip()
    checkpoint_agent_id = str(checkpoint.get("agent_id") or "").strip()
    if (
        vision_agent_id
        and checkpoint_agent_id
        and vision_agent_id != checkpoint_agent_id
    ):
        return []

    triggers = [
        trigger
        for trigger in (checkpoint.get("triggers") or [])
        if isinstance(trigger, dict)
    ]
    delivery_outcomes = [
        outcome
        for trigger in triggers
        if trigger.get("kind") == "material_delivery_outcome"
        and (
            outcome := _compact_text(
                trigger.get("delivery_outcome"),
                limit=32,
            )
        )
        in VISION_OUTCOME_CHECKPOINT_MATERIAL_OUTCOMES
    ]
    if not delivery_outcomes:
        return []

    patch = _dict_field(qualification_vision, "vision_patch")
    current_patch = _dict_field(agent_vision, "vision_patch")
    claim = _compact_text(patch.get("acceptance_summary"), limit=420)
    path_delta = _dict_field(qualification_vision, "path_delta")
    path_outcome = _compact_text(path_delta.get("outcome"), limit=32)
    evidence_refs = [
        evidence_ref
        for value in (path_delta.get("evidence_refs") or [])
        if (evidence_ref := _compact_text(value, limit=140))
    ][:4]
    fresh_vision_patch = bool(
        checkpoint.get("decision") == "patched"
        and checkpoint.get("generated_at")
        and checkpoint.get("generated_at")
        == qualification_vision.get("generated_at")
    )
    continuity_basis = _dict_field(checkpoint, "continuity_basis")
    unchanged_vision_revision = bool(
        checkpoint.get("decision") == "unchanged_with_reason"
        and continuity_basis.get("kind") == "existing_vision_unchanged"
        and continuity_basis.get("vision_generated_at")
        == qualification_vision.get("generated_at")
    )
    outcome_gap_reported = "outcome_gap" in delivery_outcomes
    # The same component facts own both qualification and its explanation.
    component_checks = {
        "checkpoint_satisfied": checkpoint.get("satisfied") is True,
        "checkpoint_fresh": fresh_vision_patch or unchanged_vision_revision,
        "path_outcome_valid": path_outcome in VISION_OUTCOME_CHECKPOINT_CONTINUATION_OUTCOMES,
        "evidence_refs_present": bool(evidence_refs),
        "final_outcome_claim_present": bool(claim),
        "no_reported_outcome_gap": not outcome_gap_reported,
    }
    failed_components = [key for key, passed in component_checks.items() if not passed]
    if not failed_components:
        return []
    claim_only_missing = failed_components == ["final_outcome_claim_present"]
    reason = (
        OutcomeCheckpointReason.CLAIM_MISSING
        if claim_only_missing else OutcomeCheckpointReason.INCOMPLETE
    )

    gap: dict[str, Any] = {
        "kind": VISION_OUTCOME_CHECKPOINT_REQUIRED_TRIGGER,
        "source": "latest_vision_checkpoint",
        "agent_id": checkpoint.get("agent_id") or agent_vision.get("agent_id"),
        "reason_code": reason.value,
        "component_checks": component_checks,
        "resolution_hint": (
            "Add or restore a bounded vision_patch.acceptance_summary stating the "
            "final outcome supported by the existing evidence. Retain that route "
            "and its refs in the required path_delta.outcome=replan correction. "
            "A learning milestone alone is not completion."
            if claim_only_missing else
            "Repair the failed checkpoint components with a fresh evidence-linked "
            "final-outcome claim and a valid continuation path decision."
        ),
        "replan_trigger_summary": (
            "the checkpoint, path decision, and evidence are valid, but the "
            "final-outcome claim (vision_patch.acceptance_summary) is missing"
            if claim_only_missing else
            "a material milestone closed without a fresh evidence-linked final-outcome "
            "path decision"
        ),
        "acceptance_summary": _compact_text(
            current_patch.get("acceptance_summary"),
            limit=420,
        )
        or claim
        or "Name the active final-outcome claim and its authoritative evidence.",
        "advancement_policy": current_patch.get("advancement_policy"),
        "checkpoint_decision": checkpoint.get("decision"),
        "delivery_outcomes": delivery_outcomes[:3],
        "fresh_vision_patch": fresh_vision_patch,
        "unchanged_vision_revision": unchanged_vision_revision,
        "path_outcome": path_outcome,
        "evidence_ref_count": len(evidence_refs),
        "required_path_outcomes": ["continue", "no_change", "replan"],
    }
    generated_at = _compact_text(checkpoint.get("generated_at"), limit=80)
    if generated_at:
        gap["generated_at"] = generated_at
    return [gap]


def _iso_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _checkpoint_covers_completed_todo(
    agent_vision: dict[str, Any],
    checkpoint: dict[str, Any],
) -> bool:
    if checkpoint.get("satisfied") is not True:
        return False
    material_checkpoint = any(
        trigger.get("kind") == "material_delivery_outcome"
        and str(trigger.get("delivery_outcome") or "").strip()
        in VISION_OUTCOME_CHECKPOINT_MATERIAL_OUTCOMES
        for trigger in (checkpoint.get("triggers") or [])
        if isinstance(trigger, dict)
    )
    if material_checkpoint:
        return not acceptance_gaps_from_outcome_checkpoint(agent_vision, checkpoint)
    return False


def acceptance_gaps_from_todo_completion_checkpoint(
    agent_vision: dict[str, Any] | None,
    checkpoint: dict[str, Any] | None,
    *,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
    completed_todo_threshold: int = COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD,
) -> list[dict[str, Any]]:
    """Require a checkpoint after a bounded chain of scoped completions."""

    vision_present = isinstance(agent_vision, dict)
    if vision_present and goal_vision_state_is_closed(
        str(agent_vision.get("state") or "")
    ):
        return []
    if not agent_id:
        return []
    summary = agent_todo_summary if isinstance(agent_todo_summary, dict) else {}
    completed_items = [
        item
        for item in (summary.get("recent_completed_advancement_items") or [])
        if isinstance(item, dict)
        and agent_scope_item_claimed_by(item) == agent_id
    ]
    completed_items = [
        item
        for item in completed_items
        if _iso_timestamp(item.get("completed_at")) is not None
    ]
    completed_items.sort(
        key=lambda item: _iso_timestamp(item.get("completed_at")) or 0.0,
        reverse=True,
    )
    latest_item = completed_items[0] if completed_items else None
    if not isinstance(latest_item, dict):
        return []
    completed_at = _iso_timestamp(latest_item.get("completed_at"))
    if completed_at is None:
        return []
    checkpoint_generated_at = _iso_timestamp(
        checkpoint.get("generated_at") if isinstance(checkpoint, dict) else None
    )
    if (
        isinstance(checkpoint, dict)
        and checkpoint_generated_at is not None
        and checkpoint_generated_at >= completed_at
        and _checkpoint_covers_completed_todo(agent_vision or {}, checkpoint)
    ):
        return []

    qualified_checkpoint_at = (
        checkpoint_generated_at
        if isinstance(checkpoint, dict)
        and checkpoint_generated_at is not None
        and _checkpoint_covers_completed_todo(agent_vision or {}, checkpoint)
        else None
    )
    uncheckpointed_items = [
        item
        for item in completed_items
        if qualified_checkpoint_at is None
        or (_iso_timestamp(item.get("completed_at")) or 0.0)
        > qualified_checkpoint_at
    ]
    threshold = max(1, int(completed_todo_threshold))
    if len(uncheckpointed_items) < threshold:
        return []

    patch = _dict_field(agent_vision, "vision_patch") if vision_present else {}
    return [
        {
            "kind": VISION_OUTCOME_CHECKPOINT_REQUIRED_TRIGGER,
            "source": "recent_completed_advancement_todo",
            "agent_id": agent_id or agent_vision.get("agent_id"),
            "replan_trigger_summary": (
                "a completed advancement Todo chain has no later final-outcome "
                "checkpoint"
            ),
            "acceptance_summary": (
                _compact_text(patch.get("acceptance_summary"), limit=420)
                or (
                    "Write a bounded agent vision patch, then name the active "
                    "final-outcome claim and its authoritative evidence."
                    if not vision_present
                    else "Name the active final-outcome claim and its "
                    "authoritative evidence."
                )
            ),
            "advancement_policy": patch.get("advancement_policy"),
            "completed_todo_id": latest_item.get("todo_id"),
            "completed_at": latest_item.get("completed_at"),
            "completed_todo_count": len(uncheckpointed_items),
            "completed_todo_threshold": threshold,
            "completed_todo_ids": [
                item.get("todo_id")
                for item in uncheckpointed_items[
                    :threshold
                ]
                if item.get("todo_id")
            ],
            "checkpoint_generated_at": (
                checkpoint.get("generated_at")
                if isinstance(checkpoint, dict)
                else None
            ),
            "required_path_outcomes": ["continue", "no_change", "replan"],
        }
    ]
