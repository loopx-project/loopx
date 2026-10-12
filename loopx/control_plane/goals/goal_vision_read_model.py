from __future__ import annotations

from typing import Any

from .goal_vision_state import (
    goal_vision_state_is_closed,
    goal_vision_state_requires_successor,
)

# Shared read contract for both Todo wait projection and frontier decisions.
VISION_FRONTIER_TODO_DELTA_ACTIONS = frozenset(
    {"activate", "create", "reopen", "resume", "retain"}
)
VISION_TODO_DELTA_ID_LIMIT = 120
VISION_ACCEPTANCE_GAP_TRIGGER = "vision_acceptance_gap"
VISION_SUCCESSOR_GAP_TRIGGER = "vision_successor_required"


def _compact_projection_text(value: Any, *, limit: int = 360) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    return text[:limit]


def parse_vision_todo_delta_entries(entries: Any) -> list[tuple[str, str]]:
    """Parse ``action:todo_id`` vision todo_delta entries once for consumers."""

    parsed: list[tuple[str, str]] = []
    for value in entries or []:
        if not isinstance(value, str):
            continue
        action, separator, raw_todo_id = value.strip().partition(":")
        todo_id = (
            _compact_projection_text(raw_todo_id, limit=VISION_TODO_DELTA_ID_LIMIT)
            or ""
        )
        normalized_action = action.strip().lower()
        if (
            separator
            and todo_id
            and normalized_action in (VISION_FRONTIER_TODO_DELTA_ACTIONS)
        ):
            parsed.append((normalized_action, todo_id))
    return parsed


def latest_agent_vision_from_runs(
    runs: list[dict[str, Any]],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Return the newest active vision from newest-first compact run records."""

    for run in runs:
        vision = run.get("agent_vision")
        if not isinstance(vision, dict):
            continue
        vision_agent_id = str(
            vision.get("agent_id") or run.get("agent_id") or ""
        ).strip()
        if agent_id and vision_agent_id and vision_agent_id != agent_id:
            continue
        patch = (
            vision.get("vision_patch")
            if isinstance(vision.get("vision_patch"), dict)
            else {}
        )
        if not patch:
            continue
        result: dict[str, Any] = {
            "schema_version": vision.get("schema_version"),
            "goal_id": goal_id,
            "agent_id": vision_agent_id or agent_id,
            "state": vision.get("state"),
            "vision_patch": patch,
            "todo_delta": vision.get("todo_delta")
            if isinstance(vision.get("todo_delta"), list)
            else [],
            "vision_budget": vision.get("vision_budget")
            if isinstance(vision.get("vision_budget"), dict)
            else None,
            "generated_at": run.get("generated_at"),
        }
        if isinstance(vision.get("stride_decision"), dict):
            result["stride_decision"] = vision["stride_decision"]
        if isinstance(vision.get("path_delta"), dict):
            result["path_delta"] = vision["path_delta"]
        if isinstance(vision.get("fallback_declarations"), list):
            result["fallback_declarations"] = vision["fallback_declarations"]
        return result
    return None


def acceptance_gaps_from_agent_vision(
    agent_vision: dict[str, Any] | None,
    *,
    goal_status: str | None = None,
) -> list[dict[str, Any]]:
    """Convert bounded vision replan triggers into goal-frontier gap records."""

    if not isinstance(agent_vision, dict):
        return []
    raw_patch = agent_vision.get("vision_patch")
    patch = raw_patch if isinstance(raw_patch, dict) else {}
    state = str(agent_vision.get("state") or "").strip()
    if goal_vision_state_is_closed(state):
        normalized_goal_status = str(goal_status or "").strip().lower()
        active_goal = (
            normalized_goal_status == "active"
            or normalized_goal_status.startswith("active-")
        )
        if goal_vision_state_requires_successor(state) and active_goal:
            return [
                {
                    "kind": VISION_SUCCESSOR_GAP_TRIGGER,
                    "source": "latest_agent_vision",
                    "agent_id": agent_vision.get("agent_id"),
                    "state": agent_vision.get("state"),
                    "goal_status": normalized_goal_status,
                    "replan_trigger_summary": (
                        "the current stage vision is closed while the registry goal "
                        "remains active; establish a successor vision before continuing"
                    ),
                    "acceptance_summary": (
                        "Write the next bounded agent vision, or explicitly retire, "
                        "supersede, or close the lane with no_followup."
                    ),
                    "advancement_policy": "repeat_until_closed",
                    "generated_at": agent_vision.get("generated_at"),
                }
            ]
        return []
    acceptance = _compact_projection_text(patch.get("acceptance_summary"), limit=420)
    explicit_trigger = _compact_projection_text(
        patch.get("replan_trigger_summary"),
        limit=240,
    )
    trigger = explicit_trigger
    if not trigger and acceptance:
        trigger = (
            "active agent vision remains open with acceptance evidence still required"
        )
    if not trigger:
        return []
    gap: dict[str, Any] = {
        "kind": VISION_ACCEPTANCE_GAP_TRIGGER,
        "source": "latest_agent_vision",
        "agent_id": agent_vision.get("agent_id"),
        "state": agent_vision.get("state"),
        "replan_trigger_summary": trigger,
        "replan_trigger_source": (
            "explicit_vision_trigger"
            if explicit_trigger
            else "implicit_open_acceptance"
        ),
    }
    if acceptance:
        gap["acceptance_summary"] = acceptance
    vision_todo_ids = [
        todo_id
        for _, todo_id in parse_vision_todo_delta_entries(
            agent_vision.get("todo_delta")
        )
    ]
    if vision_todo_ids:
        gap["vision_todo_ids"] = list(dict.fromkeys(vision_todo_ids))
    advancement_policy = _compact_projection_text(
        patch.get("advancement_policy"),
        limit=32,
    )
    if advancement_policy:
        gap["advancement_policy"] = advancement_policy
    generated_at = _compact_projection_text(agent_vision.get("generated_at"), limit=80)
    if generated_at:
        gap["generated_at"] = generated_at
    return [gap]
