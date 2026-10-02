from __future__ import annotations

import re


GOAL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
GOAL_INSTANCE_ID = re.compile(r"^ginst_[0-9a-f]{32}$")


def require_goal_id(goal_id: str) -> None:
    if not GOAL_ID.fullmatch(goal_id):
        raise ValueError("source-session goal_id must be 1-200 safe characters")


def exact_goal_ref(goal_id: str, goal_instance_id: str) -> dict[str, str]:
    require_goal_id(goal_id)
    if not GOAL_INSTANCE_ID.fullmatch(goal_instance_id):
        raise ValueError("goal_instance_id must be a Goal instance identifier")
    return {
        "goal_id": goal_id,
        "goal_instance_id": goal_instance_id,
    }
