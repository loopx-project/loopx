from __future__ import annotations

import hashlib
import json
from typing import Any

from ..effect_runtime import effect_runtime_result
from ..todos.contract import normalize_todo_id
from ..todos.resume_planning import build_todo_resume_planning_request

GOAL_VISION_WAIT_STATE_SCHEMA_VERSION = "goal_vision_wait_state_v0"


def _vision_wait_blocker_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ("todo_id", "task_class", "status", "priority", "claimed_by", "excluded_agents", "reason")
    return [{key: item[key] for key in fields if key in item} for item in items if isinstance(item, dict)]


def _vision_wait_lineage_rows(items: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if items is None:
        return None
    fields = ("todo_id", "successor_todo_ids", "resume_condition")
    return [{key: item[key] for key in fields if key in item} for item in items if isinstance(item, dict)]


def exact_blocked_successor_wait_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidate = value
    if value.get("schema_version") != GOAL_VISION_WAIT_STATE_SCHEMA_VERSION:
        raw_candidate = value.get("vision_wait_state")
        candidate = raw_candidate if isinstance(raw_candidate, dict) else {}
        if not candidate:
            raw_projection = value.get("goal_frontier_projection")
            projection = raw_projection if isinstance(raw_projection, dict) else {}
            raw_candidate = projection.get("vision_wait_state")
            candidate = raw_candidate if isinstance(raw_candidate, dict) else {}
    if (
        candidate.get("schema_version") != GOAL_VISION_WAIT_STATE_SCHEMA_VERSION
        or candidate.get("state") != "waiting"
        or candidate.get("reason_code") != "exact_blocked_successor"
        or candidate.get("automatic_resume") is not True
        or not normalize_todo_id(candidate.get("selected_todo_id"))
        or not str(candidate.get("resume_when") or "").strip()
    ):
        return {}
    return candidate


def exact_blocked_successor_frontier_identity(value: Any) -> str | None:
    wait = exact_blocked_successor_wait_state(value)
    if not wait:
        return None
    parts = {
        "agent_id": str(wait.get("agent_id") or "").strip(),
        "reason_code": "exact_blocked_successor",
        "selected_todo_id": normalize_todo_id(wait.get("selected_todo_id")),
        "resume_when": str(wait.get("resume_when") or "").strip(),
    }
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def build_goal_vision_wait_state(
    *,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
    acceptance_gaps: list[dict[str, Any]] | None,
    selectable_advancement_count: int,
    source_items: list[dict[str, Any]] | None = None,
    lineage_source_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Project a temporary vision wait over an authoritative blocked frontier.

    This is deliberately a read model, not a new todo or vision lifecycle
    state. It may defer only ordinary open-vision acceptance gaps. Missing
    checkpoints and closed-stage successor requirements remain strict.
    """

    summary = agent_todo_summary if isinstance(agent_todo_summary, dict) else {}
    planning_input = summary
    if source_items is not None:
        planning_input = {
            "items": source_items,
            "deferred_items": [item for item in source_items if item.get("status") == "deferred"],
            "resume_blocked_items": [
                item for item in source_items
                if item.get("resume_ready") is False and item.get("status") == "open"
            ],
            "current_agent_blocker_items": source_items,
        }
    blocker_items = source_items if source_items is not None else summary.get("current_agent_blocker_items")
    wait_summary = {"vision_wait_states": summary.get("vision_wait_states"),
                    "current_agent_blocker_items": _vision_wait_blocker_rows(blocker_items or [])}
    lineage_items = lineage_source_items if lineage_source_items is not None else source_items
    result = effect_runtime_result("goal.vision_wait.project", {
        "agent_todo_summary": wait_summary,
        "agent_id": agent_id,
        "acceptance_gaps": acceptance_gaps or [],
        "selectable_advancement_count": selectable_advancement_count,
        "lineage_source_items": _vision_wait_lineage_rows(lineage_items),
        "resume_planning_request": build_todo_resume_planning_request(planning_input, agent_id=agent_id),
    })
    if result is not None and (
        not isinstance(result, dict)
        or result.get("schema_version") != GOAL_VISION_WAIT_STATE_SCHEMA_VERSION
        or result.get("state") != "waiting"
    ):
        raise RuntimeError("TypeScript vision wait state shape mismatch")
    return result
