"""Read-only managed continuation over one complete journal and fresh decision.

The typed owner composes current journal qualification with the ordered
controller. Host scheduling, retries, dispatch and settlement keep their owners.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..quota.turn_envelope import TurnControllerInput
from .loop_controller import _controller_result

LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION = "loopx_turn_managed_step_v0"


def decide_managed_step(
    journal: Mapping[str, Any], fresh_decision: Mapping[str, Any] | TurnControllerInput, *,
    goal_id: str, agent_id: str, turn_key: str,
    predecessor_turn_key: str | None = None,
    observed_attempt: int | None = None, observed_max_attempts: int | None = None,
) -> dict[str, Any]:
    """Qualify and decide in one runtime crossing, without reconstructing execution."""
    return _controller_result(
        "managed", journal=dict(journal) if isinstance(journal, Mapping) else {},
        decision=fresh_decision,
        goal_id=goal_id, agent_id=agent_id, turn_key=turn_key,
        predecessor_turn_key=predecessor_turn_key, observed_attempt=observed_attempt,
        observed_max_attempts=observed_max_attempts,
    )
