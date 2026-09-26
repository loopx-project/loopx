from __future__ import annotations

import re
from fnmatch import fnmatchcase
from typing import Any, Mapping

from ..runtime.public_safety import public_safe_compact_text
from ..todos.contract import (
    TODO_TASK_CLASS_VALUES,
    normalize_todo_action_kind,
)
from .runtime_model import PEER_AGENT_PROFILE_SCHEMA_VERSION


AGENT_PROFILE_FIELDS = {
    "schema_version",
    "agent_id",
    "profile_role",
    "scope_summary",
    "default_task_classes",
    "vision_requirement",
    "preferred_action_kinds",
    "avoid_action_kinds",
}
AGENT_PROFILE_ACTION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_*-]{0,79}$")
AGENT_PROFILE_ACTION_PATTERN_LIMIT = 16
AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS = 320
AGENT_PROFILE_HIERARCHY_ROLES = {
    "leader",
    "manager",
    "supervisor",
    "worker",
}
AGENT_PROFILE_HIERARCHY_AGENT_ROLE = re.compile(
    r"(?:^|-)(?:main|primary|side)-agent(?:-|$)"
)
AGENT_PROFILE_VISION_REQUIREMENTS = {"optional", "required"}


def _bounded_text(value: Any, *, field: str, limit: int) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"agent profile {field} must be at most {limit} characters")
    if public_safe_compact_text(text, limit=limit) != text:
        raise ValueError(f"agent profile {field} must be public-safe")
    return text


def _profile_role(value: Any) -> str | None:
    role = _bounded_text(value, field="profile_role", limit=80)
    if not role:
        return None
    normalized_role = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
    padded_role = f"-{normalized_role}-"
    if (
        any(
            f"-{forbidden}-" in padded_role
            for forbidden in AGENT_PROFILE_HIERARCHY_ROLES
        )
        or AGENT_PROFILE_HIERARCHY_AGENT_ROLE.search(normalized_role)
    ):
        raise ValueError(
            "agent profile_role must be functional and advisory, not a hierarchy role"
        )
    return role


def _task_classes(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("agent profile default_task_classes must be a list")
    task_classes: list[str] = []
    for item in value:
        task_class = str(item or "").strip().lower()
        if task_class not in TODO_TASK_CLASS_VALUES:
            raise ValueError(
                "agent profile default_task_classes must use known todo task classes"
            )
        if task_class not in task_classes:
            task_classes.append(task_class)
    return task_classes


def _action_patterns(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"agent profile {field} must be a list")
    patterns: list[str] = []
    for item in value:
        pattern = str(item or "").strip().lower()
        if not AGENT_PROFILE_ACTION_PATTERN.fullmatch(pattern):
            raise ValueError(
                f"agent profile {field} entries must be public-safe action_kind globs"
            )
        if pattern not in patterns:
            patterns.append(pattern)
        if len(patterns) > AGENT_PROFILE_ACTION_PATTERN_LIMIT:
            raise ValueError(
                f"agent profile {field} must contain at most "
                f"{AGENT_PROFILE_ACTION_PATTERN_LIMIT} entries"
            )
    return patterns


def _vision_requirement(value: Any, *, task_classes: list[str]) -> str | None:
    requirement = str(value or "").strip().lower()
    if requirement and requirement not in AGENT_PROFILE_VISION_REQUIREMENTS:
        raise ValueError(
            "agent profile vision_requirement must be optional or required"
        )
    if requirement:
        return requirement
    if {"advancement_task", "continuous_monitor"}.issubset(task_classes):
        return "required"
    return None


def agent_profile_requires_vision(profile: Mapping[str, Any] | None) -> bool:
    if not isinstance(profile, Mapping):
        return False
    requirement = str(profile.get("vision_requirement") or "").strip().lower()
    if requirement:
        return requirement == "required"
    task_classes = _task_classes(profile.get("default_task_classes"))
    return {"advancement_task", "continuous_monitor"}.issubset(task_classes)


def normalize_agent_profile(
    raw_profile: Mapping[str, Any],
    *,
    registered_agents: list[str],
    expected_agent_id: str | None = None,
    reject_unknown_fields: bool = True,
) -> dict[str, Any]:
    unknown = sorted(set(raw_profile) - AGENT_PROFILE_FIELDS)
    if reject_unknown_fields and unknown:
        raise ValueError(f"unknown agent profile field(s): {', '.join(unknown)}")
    schema_version = str(
        raw_profile.get("schema_version") or PEER_AGENT_PROFILE_SCHEMA_VERSION
    ).strip()
    if schema_version != PEER_AGENT_PROFILE_SCHEMA_VERSION:
        raise ValueError(
            f"agent profile schema_version must be {PEER_AGENT_PROFILE_SCHEMA_VERSION}"
        )
    agent_id = str(raw_profile.get("agent_id") or expected_agent_id or "").strip()
    if not agent_id or agent_id not in registered_agents:
        raise ValueError("agent profile agent_id must name a registered peer")
    if expected_agent_id and agent_id != expected_agent_id:
        raise ValueError("agent profile key and agent_id must match")

    preferred = _action_patterns(
        raw_profile.get("preferred_action_kinds"),
        field="preferred_action_kinds",
    )
    avoided = _action_patterns(
        raw_profile.get("avoid_action_kinds"),
        field="avoid_action_kinds",
    )
    overlap = sorted(set(preferred) & set(avoided))
    if overlap:
        raise ValueError(
            "agent profile action kind globs cannot be both preferred and avoided: "
            + ", ".join(overlap)
        )
    task_classes = _task_classes(raw_profile.get("default_task_classes"))
    profile = {
        "schema_version": PEER_AGENT_PROFILE_SCHEMA_VERSION,
        "agent_id": agent_id,
        "profile_role": _profile_role(raw_profile.get("profile_role")),
        "scope_summary": _bounded_text(
            raw_profile.get("scope_summary"),
            field="scope_summary",
            limit=AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS,
        ),
        "default_task_classes": task_classes,
        "vision_requirement": _vision_requirement(
            raw_profile.get("vision_requirement"),
            task_classes=task_classes,
        ),
        "preferred_action_kinds": preferred,
        "avoid_action_kinds": avoided,
    }
    return {
        key: value
        for key, value in profile.items()
        if value not in (None, [], "")
    }


def agent_profile_candidate_rank(
    item: Mapping[str, Any],
    *,
    agent_profile: Mapping[str, Any] | None,
) -> int:
    if not isinstance(agent_profile, Mapping):
        return 1
    action_kind = normalize_todo_action_kind(item.get("action_kind")) or ""
    avoided = agent_profile.get("avoid_action_kinds")
    if action_kind and isinstance(avoided, list) and any(
        fnmatchcase(action_kind, str(pattern)) for pattern in avoided
    ):
        return 2
    preferred = agent_profile.get("preferred_action_kinds")
    if action_kind and isinstance(preferred, list) and any(
        fnmatchcase(action_kind, str(pattern)) for pattern in preferred
    ):
        return 0
    default_task_classes = agent_profile.get("default_task_classes")
    if isinstance(default_task_classes, list):
        task_class = str(item.get("task_class") or "").strip().lower()
        if task_class and task_class in default_task_classes:
            return 0
    return 1
