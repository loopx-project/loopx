"""Compose configured capability registrations outside the control plane."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..control_plane.turn_driver.capability_context import dispatch_turn_context_hooks
from ..control_plane.turn_driver.post_settlement import (
    PostSettlement,
    registered_post_settlement,
)
from .reward_memory.turn_hooks import (
    memory_context_registration,
    memory_settlement_registration,
)


def attach_turn_context(
    payload: dict[str, Any],
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    decision: Mapping[str, Any],
    turn_instance_id: str,
) -> None:
    dispatch_turn_context_hooks(
        payload,
        (
            memory_context_registration(
                payload,
                registry_path=registry_path,
                goal_id=goal_id,
                agent_id=agent_id,
                decision=decision,
                turn_instance_id=turn_instance_id,
            ),
        ),
    )


def configured_post_settlement(
    payload: Mapping[str, Any],
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
) -> PostSettlement | None:
    registration = memory_settlement_registration(
        payload,
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    return registered_post_settlement(
        (registration,) if registration is not None else ()
    )
