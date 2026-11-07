from __future__ import annotations

from typing import Any, Mapping, cast

import hashlib
import json

from ..todos.contract import normalize_todo_claimed_by
from .runtime_model import (
    PEER_AGENT_PROFILE_SCHEMA_VERSION,
    normalized_peer_agent_ids,
)


PEER_AGENT_RUNTIME_MIGRATION = "peer_agent_runtime_v1"
LEGACY_AGENT_PROFILE_SCHEMA_VERSION = "agent_profile_v0"
LEGACY_HIERARCHY_ROLES = {"primary-agent", "side-agent"}


def legacy_agent_hierarchy_present(goal: Mapping[str, Any] | None) -> bool:
    """Detect v0.1 input only for the isolated migration path."""

    if not isinstance(goal, Mapping):
        return False
    coordination = goal.get("coordination")
    if not isinstance(coordination, Mapping):
        return False
    profiles = coordination.get("agent_profiles")
    profile_items = (
        profiles.values()
        if isinstance(profiles, Mapping)
        else profiles
        if isinstance(profiles, list)
        else []
    )
    legacy_profile_present = any(
        isinstance(profile, Mapping)
        and (
            profile.get("schema_version") == LEGACY_AGENT_PROFILE_SCHEMA_VERSION
            or "role" in profile
            or profile.get("primary_agent")
            or (
                isinstance(profile.get("review_policy"), Mapping)
                and (
                    profile["review_policy"].get("handoff_agent")
                    or profile["review_policy"].get("reviews_side_agent_work")
                )
            )
        )
        for profile in profile_items
    )
    return bool(
        coordination.get("primary_agent")
        or coordination.get("side_agent_handoff_agent")
        or coordination.get("agent_model") == "legacy_hierarchy"
        or legacy_profile_present
    )


def peer_agent_runtime_migration_id(
    goal_id: str,
    goal: Mapping[str, Any] | None,
) -> str:
    coordination = goal.get("coordination") if isinstance(goal, Mapping) else None
    registered_agents = (
        normalized_peer_agent_ids(coordination.get("registered_agents") or [])
        if isinstance(coordination, Mapping)
        else []
    )
    encoded = json.dumps(
        {
            "goal_id": str(goal_id),
            "migration": PEER_AGENT_RUNTIME_MIGRATION,
            "registered_agents": registered_agents,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"peer_runtime_v1_{digest}"


def completed_peer_agent_runtime_migration(
    goal: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if not isinstance(goal, Mapping):
        return None
    coordination = goal.get("coordination")
    if not isinstance(coordination, Mapping):
        return None
    completed = coordination.get("completed_migrations")
    if not isinstance(completed, Mapping):
        return None
    migration = completed.get(PEER_AGENT_RUNTIME_MIGRATION)
    return migration if isinstance(migration, Mapping) else None


def peer_agent_runtime_migration_completed(
    goal: Mapping[str, Any] | None,
) -> bool:
    migration = completed_peer_agent_runtime_migration(goal)
    return bool(migration and migration.get("status") == "completed")


def migrate_agent_profiles_to_peer_v1(raw_profiles: Any) -> dict[str, dict[str, Any]]:
    """Normalize advisory profiles while deleting v0.1 hierarchy policy."""

    if isinstance(raw_profiles, Mapping):
        candidates = [(key, value) for key, value in raw_profiles.items()]
    elif isinstance(raw_profiles, list):
        candidates = [
            (
                value.get("agent_id") or value.get("id") or value.get("name"),
                value,
            )
            for value in raw_profiles
            if isinstance(value, Mapping)
        ]
    else:
        return {}
    migrated: dict[str, dict[str, Any]] = {}
    for raw_agent_id, raw_profile in candidates:
        agent_id = normalize_todo_claimed_by(raw_agent_id)
        if not agent_id or not isinstance(raw_profile, Mapping):
            continue
        profile = dict(raw_profile)
        legacy_role = str(profile.pop("role", "") or "").strip()
        profile.pop("primary_agent", None)
        profile["schema_version"] = PEER_AGENT_PROFILE_SCHEMA_VERSION
        profile["agent_id"] = agent_id
        if legacy_role and legacy_role not in LEGACY_HIERARCHY_ROLES:
            profile.setdefault("profile_role", legacy_role)
        profile.pop("worktree_policy", None)
        profile.pop("review_policy", None)
        migrated[agent_id] = profile
    return migrated


def migrate_coordination_to_peer_v1(
    coordination: Mapping[str, Any],
    *,
    migration_id: str,
    completed_at: str | None,
) -> dict[str, Any]:
    """Apply the isolated hierarchy cleanup after host prompt migration."""

    migrated = dict(coordination)
    migrated.pop("primary_agent", None)
    migrated.pop("side_agent_handoff_agent", None)
    migrated["registered_agents"] = normalized_peer_agent_ids(
        migrated.get("registered_agents") or []
    )
    profiles = migrate_agent_profiles_to_peer_v1(migrated.get("agent_profiles"))
    if profiles:
        migrated["agent_profiles"] = profiles
    else:
        migrated.pop("agent_profiles", None)
    completed_migrations = (
        dict(cast(Mapping[str, Any], migrated.get("completed_migrations")))
        if isinstance(migrated.get("completed_migrations"), Mapping)
        else {}
    )
    completed_migrations[PEER_AGENT_RUNTIME_MIGRATION] = {
        "migration_id": migration_id,
        "status": "completed",
        "completed_at": completed_at,
    }
    migrated["completed_migrations"] = completed_migrations
    return migrated
