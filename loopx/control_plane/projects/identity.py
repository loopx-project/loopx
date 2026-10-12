"""Pure Goal naming and creation-conflict identity, shared by App and bootstrap."""
from __future__ import annotations

import re
from pathlib import Path

from ..runtime.public_safety import public_safe_compact_text


class GoalCreationConflictError(ValueError):
    """A create-only bootstrap cannot adopt another registry operation."""


def slugify_goal_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "project-goal"


def default_goal_id(project: Path) -> str:
    return f"{slugify_goal_id(project.name)}-goal"


def derive_goal_display_name(goal_text: str | None) -> str | None:
    """Derive a public-safe display title from user-supplied goal text."""

    return public_safe_compact_text(goal_text, limit=132)
