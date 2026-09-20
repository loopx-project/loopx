from __future__ import annotations

from enum import Enum
from typing import Any


class OwnerControllerActorKind(str, Enum):
    OWNER = "owner"
    CONTROLLER = "controller"


def normalize_owner_controller_actor(
    value: Any,
    *,
    required: bool,
) -> OwnerControllerActorKind | None:
    """Decode the explicit non-Agent actor for a durable local mutation."""

    normalized = str(value or "").strip().lower()
    if not normalized:
        if required:
            raise ValueError(
                "actor kind is required for state mutation; use owner or controller"
            )
        return None
    try:
        return OwnerControllerActorKind(normalized)
    except ValueError as exc:
        raise ValueError("actor kind must be owner or controller") from exc
