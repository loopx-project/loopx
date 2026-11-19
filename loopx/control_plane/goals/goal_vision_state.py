from __future__ import annotations

from importlib.resources import files
import json
import re
from typing import Any


GOAL_VISION_DEFAULT_STATE = "vision_patch_proposed"
GOAL_VISION_CLOSED_STATES = frozenset(json.loads(
    files("loopx.control_plane").joinpath("goal_vision_lifecycle.json").read_text(encoding="utf-8")
)["closed_states"])
GOAL_VISION_SUCCESSOR_REQUIRED_STATES = frozenset({"vision_closed"})
GOAL_VISION_STATE_ALIASES = {
    "closed": "vision_closed",
    "satisfied": "vision_closed",
    "vision_satisfied": "vision_closed",
    "vision_retired": "retired",
    "vision_superseded": "superseded",
    "vision_no_followup": "no_followup",
    "closed_no_followup": "no_followup",
    "no_follow_up": "no_followup",
}
GOAL_VISION_STATE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


def normalize_goal_vision_state(value: Any) -> str:
    """Return a stable lifecycle token while preserving custom open states."""

    state = (
        str(value or GOAL_VISION_DEFAULT_STATE).strip().lower().replace("-", "_")
    )
    if not GOAL_VISION_STATE_PATTERN.fullmatch(state):
        raise ValueError(
            "agent_vision.state must be a lower snake_case lifecycle token "
            "such as vision_patch_proposed or vision_closed"
        )
    return GOAL_VISION_STATE_ALIASES.get(state, state)


def goal_vision_state_is_closed(value: Any) -> bool:
    """Recognize canonical and legacy closure tokens without trusting bad state."""

    try:
        state = normalize_goal_vision_state(value)
    except ValueError:
        return False
    return state in GOAL_VISION_CLOSED_STATES


def goal_vision_state_requires_successor(value: Any) -> bool:
    """Return whether the state closes one stage but not the active goal lane."""

    try:
        state = normalize_goal_vision_state(value)
    except ValueError:
        return False
    return state in GOAL_VISION_SUCCESSOR_REQUIRED_STATES
