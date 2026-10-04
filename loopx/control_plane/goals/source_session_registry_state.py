from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...registry import atomic_write_json
from ..projects.registry_codec import SOURCE_SESSION_PROFILE_ID
from ..todos.active_state_editing import fsync_state_directory
from .goal_ref_validation import (
    GOAL_INSTANCE_ID,
    exact_goal_ref,
    require_goal_id as require_goal_id,
)


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def alias_digest(goal_id: str) -> str:
    return hashlib.sha256(goal_id.encode("utf-8")).hexdigest()


def lifetime_root(registry_path: Path) -> Path:
    return registry_path.parent / ".loopx" / "lifecycle" / "goal-instance"


def guard_path(registry_path: Path, goal_id: str) -> Path:
    return lifetime_root(registry_path) / "guards" / f"{alias_digest(goal_id)}.guard"


def write_journal(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload, preserve_mode=True)
    fsync_state_directory(path)


def required_list(
    registry: dict[str, Any],
    field: str,
) -> list[dict[str, Any]]:
    value = registry.get(field)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"source-session {field} must be a list of objects")
    return value


def current_goal_ref(
    registry: dict[str, Any],
    *,
    goal_id: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    if registry.get("profile_id") != SOURCE_SESSION_PROFILE_ID:
        raise ValueError("source-session registry profile is unsupported")
    goals = required_list(registry, "goals")
    matches = [goal for goal in goals if goal.get("id") == goal_id]
    if len(matches) != 1:
        raise ValueError(f"goal_id is not registered exactly once: {goal_id}")
    goal = matches[0]
    if goal.get("status") != "active":
        raise ValueError(f"foreground goal is not active: {goal_id}")
    instance_id = goal.get("goal_instance_id")
    if not isinstance(instance_id, str):
        raise ValueError("source-session Goal is missing goal_instance_id")
    return exact_goal_ref(goal_id, instance_id), goal


def session_binding_records(
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    bindings = required_list(registry, "session_bindings")
    seen: set[str] = set()
    for binding in bindings:
        session_id = binding.get("session_id")
        goal_ref = binding.get("foreground_goal_ref")
        if (
            not isinstance(session_id, str)
            or not session_id
            or session_id in seen
            or not isinstance(goal_ref, dict)
            or not isinstance(goal_ref.get("goal_id"), str)
            or not isinstance(goal_ref.get("goal_instance_id"), str)
            or not GOAL_INSTANCE_ID.fullmatch(goal_ref["goal_instance_id"])
        ):
            raise ValueError("source-session binding is invalid")
        seen.add(session_id)
    return bindings


def prior_operation_receipt(
    receipts: list[dict[str, Any]],
    *,
    operation_id: str,
) -> dict[str, Any] | None:
    matches = [
        receipt for receipt in receipts if receipt.get("operation_id") == operation_id
    ]
    if len(matches) > 1:
        raise ValueError("source-session operation has duplicate receipts")
    return matches[0] if matches else None
