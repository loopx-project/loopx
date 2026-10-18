"""Read-only transport of existing registry organization, without authority inference."""

from collections.abc import Mapping

from ...public_safe_text import OPAQUE_ID_PATTERN


def goal_parent_fields(goal: Mapping[str, object]) -> dict[str, str]:
    parent = goal.get("parent_goal_id")
    # Malformed metadata must not expose a local path or become an executable
    # route. Absence keeps the legacy flat projection byte-for-byte unchanged.
    if isinstance(parent, str) and OPAQUE_ID_PATTERN.fullmatch(parent):
        return {"parent_goal_id": parent}
    return {}
