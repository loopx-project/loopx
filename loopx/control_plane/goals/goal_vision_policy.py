from __future__ import annotations

from enum import Enum
from typing import Any


class GoalVisionAdvancementPolicy(str, Enum):
    AS_NEEDED = "as_needed"
    REPEAT_UNTIL_CLOSED = "repeat_until_closed"


GOAL_VISION_ADVANCEMENT_POLICY_CHOICES = tuple(
    policy.value for policy in GoalVisionAdvancementPolicy
)

# Default review cadence counts settled work Turns, not Todo size.
DEFAULT_EFFECTIVE_TURN_REPLAN_THRESHOLD = 5

# Explicit completed-Todo cadence remains bounded by the retained evidence
# window. This is a legacy-unit limit, not the product default.
COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD = 5


def normalize_completed_todo_replan_threshold(value: Any) -> int:
    # The durable agent projection retains five completions. Larger thresholds
    # would be unreachable without widening that evidence window.
    if type(value) is not int or not 1 <= value <= COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD:
        raise ValueError("replan_after_completed_todos must be an integer from 1 to 5")
    return value


def completed_todo_replan_threshold(profile: Any) -> int:
    if not isinstance(profile, dict) or "replan_after_completed_todos" not in profile:
        return COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD
    return normalize_completed_todo_replan_threshold(profile["replan_after_completed_todos"])


def normalize_goal_vision_advancement_policy(value: Any) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_")
    try:
        return GoalVisionAdvancementPolicy(candidate).value
    except ValueError as exc:
        raise ValueError(
            "agent_vision.advancement_policy must be one of: "
            + ", ".join(GOAL_VISION_ADVANCEMENT_POLICY_CHOICES)
        ) from exc


def goal_vision_repeats_advancement_until_closed(value: Any) -> bool:
    return (
        str(value or "").strip()
        == GoalVisionAdvancementPolicy.REPEAT_UNTIL_CLOSED.value
    )
