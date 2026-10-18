"""Cheap Workspace discovery; never infer execution state from registry metadata."""

from __future__ import annotations

from typing import Any

from .chat import redact_local_paths
from .control_plane.goals.activation import goal_activation_state
from .control_plane.status.collection import registry_activation_revision
from .registry import registry_goals
from .control_plane.presentation.goal_metadata import goal_parent_fields


def workspace_goal_directory(
    registry: dict[str, Any], *, selected_goal_id: str | None = None
) -> dict[str, Any]:
    goals = []
    for goal in registry_goals(registry):
        goal_id = str(goal.get("id") or "")
        if selected_goal_id and goal_id != selected_goal_id:
            continue
        # Do not open active state, history, an authority store, or a host session.
        title = str(goal.get("display_name") or goal.get("name") or goal_id)
        title = redact_local_paths(title, protected_paths=[str(goal["repo"])] if goal.get("repo") else [])
        goals.append(
            {
                "id": goal_id,
                **goal_parent_fields(goal),
                "display_name": title[:200],
                "activation_state": goal_activation_state(goal).value,
                "registry_member": True,
            }
        )
    return {
        "ok": True,
        "schema_version": "loopx_workspace_directory_v1",
        "registry_revision": registry_activation_revision(registry),
        "goals": goals,
    }
