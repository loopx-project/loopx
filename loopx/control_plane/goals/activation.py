from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from ..actor_identity import normalize_owner_controller_actor


GOAL_ACTIVATION_SCHEMA_VERSION = "loopx_goal_activation_v1"


class GoalActivationState(str, Enum):
    ACTIVE = "active"
    STOPPED = "stopped"


def normalize_goal_activation_state(value: Any) -> GoalActivationState:
    if isinstance(value, GoalActivationState):
        return value
    normalized = str(value or "").strip().lower()
    try:
        return GoalActivationState(normalized)
    except ValueError as exc:
        raise ValueError("goal activation state must be active or stopped") from exc


def require_current_registry_goal_activation(goal: Mapping[str, Any]) -> None:
    """Reject retired storage fields, without interpreting them as live authority."""
    if "activation_state" in goal or goal.get("status") in ("paused", "stopped"):
        raise ValueError(
            "Goal activation metadata requires upgrade; run "
            "loopx authority-archive upgrade --all-known --execute "
            "to back up and migrate the registry before activation"
        )


def goal_activation_state(goal: Mapping[str, Any] | None) -> GoalActivationState:
    """Read canonical activation or its derived read-model projection.

    Registry IO rejects stored activation_state aliases and retired pause values;
    only the explicit upgrade decoder may consume them. Omitted activation is active.
    """

    if not isinstance(goal, Mapping):
        return GoalActivationState.ACTIVE
    projected = goal.get("activation_state")
    if projected is not None:
        return normalize_goal_activation_state(projected)
    require_current_registry_goal_activation(goal)
    activation = goal.get("activation")
    if activation is None:
        return GoalActivationState.ACTIVE
    if not isinstance(activation, Mapping):
        raise ValueError("goal activation must be an object")
    schema_version = str(activation.get("schema_version") or "").strip()
    if schema_version and schema_version != GOAL_ACTIVATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported goal activation schema: {schema_version}")
    return normalize_goal_activation_state(activation.get("state"))


def build_goal_activation(
    *,
    state: GoalActivationState | str,
    updated_at: str,
    reason: str,
    actor_kind: str | None = None,
) -> dict[str, str]:
    normalized_state = normalize_goal_activation_state(state)
    timestamp = str(updated_at or "").strip()
    if not timestamp:
        raise ValueError("goal activation updated_at is required")
    compact_reason = " ".join(str(reason or "").split()).strip()
    if not compact_reason or len(compact_reason) > 600:
        raise ValueError("goal activation reason must be bounded visible text")
    actor = normalize_owner_controller_actor(actor_kind, required=False)
    activation = {
        "schema_version": GOAL_ACTIVATION_SCHEMA_VERSION,
        "state": normalized_state.value,
        "updated_at": timestamp,
        "reason": compact_reason,
    }
    if actor is not None:
        activation["actor_kind"] = actor.value
    return activation


def goal_is_stopped(goal: Mapping[str, Any] | None) -> bool:
    return goal_activation_state(goal) is GoalActivationState.STOPPED
