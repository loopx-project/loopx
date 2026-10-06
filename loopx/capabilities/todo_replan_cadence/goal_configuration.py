from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...control_plane.goals.goal_vision_policy import (
    completed_todo_replan_threshold,
)


def configuration_summary(goal: Mapping[str, Any]) -> dict[str, Any] | None:
    profile = goal.get("execution_profile")
    if not isinstance(profile, Mapping):
        return None
    if "replan_after_effective_turns" in profile:
        return {
            "count_unit": "effective_turns",
            "count": profile["replan_after_effective_turns"],
        }
    if "replan_after_completed_todos" in profile:
        return {
            "count_unit": "completed_todos",
            "count": completed_todo_replan_threshold(dict(profile)),
        }
    return None


__all__ = ["configuration_summary"]
