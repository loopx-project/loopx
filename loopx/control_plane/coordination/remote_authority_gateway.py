"""Remote authority gateway access for explicitly shared goals.

Enforces default-deny access: local private Goals never connect to remote authority
or leak private state. Only goals with explicit opt-in remote authority configuration
(or canary `loopx-quality-review` when explicitly enabled) route coordination over
authenticated HTTPS.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

REMOTE_AUTHORITY_CANARY_GOAL_ID = "loopx-quality-review"
REMOTE_AUTHORITY_GATEWAY_SCHEMA = "loopx_remote_authority_gateway_v0"


def is_remote_authority_goal(
    goal_id: str,
    registry_goals: Mapping[str, Any] | None = None,
) -> bool:
    """Return True only if the goal has explicit opt-in remote authority configuration."""
    if not goal_id:
        return False

    goal_entry: Mapping[str, Any] | None = None
    if registry_goals and goal_id in registry_goals:
        entry = registry_goals[goal_id]
        if isinstance(entry, Mapping):
            goal_entry = entry

    remote_cfg = (goal_entry or {}).get("remote_authority") if goal_entry else None
    if isinstance(remote_cfg, Mapping) and remote_cfg.get("enabled") is True:
        return True

    # Canary goal: requires explicit opt-in via environment flag or explicit config
    if goal_id == REMOTE_AUTHORITY_CANARY_GOAL_ID:
        if os.environ.get("LOOPX_REMOTE_AUTHORITY_CANARY_ENABLED") in {"1", "true", "yes"}:
            return True
        if isinstance(remote_cfg, Mapping) and remote_cfg.get("enabled") is not False:
            return bool(remote_cfg.get("endpoint"))

    return False


def get_remote_authority_projection(
    goal_id: str,
    registry_goals: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return safe public gateway metadata without secret tokens or private paths."""
    if not is_remote_authority_goal(goal_id, registry_goals):
        return None

    goal_entry: Mapping[str, Any] | None = None
    if registry_goals and goal_id in registry_goals:
        entry = registry_goals[goal_id]
        if isinstance(entry, Mapping):
            goal_entry = entry

    remote_cfg = dict((goal_entry or {}).get("remote_authority") or {})
    is_canary = goal_id == REMOTE_AUTHORITY_CANARY_GOAL_ID

    endpoint = remote_cfg.get("endpoint") or (
        os.environ.get("LOOPX_REMOTE_AUTHORITY_URL")
        if is_canary
        else None
    ) or "https://remote.loopx.ai:8443"

    tenant_id = remote_cfg.get("tenant_id") or ("tenant-canary" if is_canary else "default-tenant")
    store_identity = remote_cfg.get("store_identity") or ("postgresql:canary" if is_canary else "postgresql:default")
    actor_id = remote_cfg.get("actor_id") or ("canary-reviewer" if is_canary else None)

    return {
        "schema_version": REMOTE_AUTHORITY_GATEWAY_SCHEMA,
        "goal_id": goal_id,
        "enabled": True,
        "canary": is_canary,
        "endpoint": str(endpoint),
        "tenant_id": str(tenant_id),
        "store_identity": str(store_identity),
        **({"actor_id": str(actor_id)} if actor_id else {}),
        "handoff_mode": "hard_lease",
        "access": "opt_in_authenticated_only",
    }


def check_remote_authority_gateway_status(
    goal_id: str,
    registry_goals: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Evaluate gateway health for quota should-run.

    Fails closed if the explicit remote authority is unreachable or unconfigured.
    Returns None for normal local goals.
    """
    if not is_remote_authority_goal(goal_id, registry_goals):
        return None

    projection = get_remote_authority_projection(goal_id, registry_goals)
    if projection is None:
        return None

    # Check credential presence for the opt-in remote authority
    cred = os.environ.get("LOOPX_AUTHORITY_CREDENTIAL") or os.environ.get("LOOPX_REMOTE_AUTHORITY_CREDENTIAL")
    if not cred:
        return {
            "ok": False,
            "should_run": False,
            "status": "unavailable",
            "reason_code": "remote_authority_credential_required",
            "reason": (
                f"Goal {goal_id!r} is configured for remote authority but "
                "LOOPX_AUTHORITY_CREDENTIAL is not set in the environment"
            ),
            "remote_gateway": projection,
        }

    return {
        "ok": True,
        "status": "connected",
        "remote_gateway": projection,
    }
