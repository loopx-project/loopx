"""Goal overrides for the existing pull-request-review configuration owner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .machine_defaults import (
    normalize_pull_request_review_machine_defaults,
    PULL_REQUEST_REVIEW_MACHINE_DEFAULTS_SCHEMA,
)
from .order import configuration as typed_configuration
from ...agent_registry import registered_agent_ids_for_goal

GOAL_CONFIGURATION_SCHEMA = "pull_request_review_goal_configuration_v0"


def configuration_summary(goal: Mapping[str, Any]) -> dict[str, Any] | None:
    control = goal.get("control_plane", {})
    raw = control.get("pull_request_review") if isinstance(control, Mapping) else None
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError("pull_request_review Goal configuration must be an object")
    if raw.get("schema_version") != GOAL_CONFIGURATION_SCHEMA:
        raise ValueError(
            "pull_request_review Goal configuration has an unsupported schema"
        )
    normalized = normalize_configuration({k: v for k, v in raw.items() if k != "schema_version"})
    if "wait_for_ci" in normalized or "review_order" in normalized:
        return {"wait_for_ci": True, "review_order": "forward", **normalized}
    return normalized


def normalize_configuration(raw: Mapping[str, Any]) -> dict[str, Any]:
    return typed_configuration({"action": "normalize", "configuration": dict(raw), "allow_agents": True})


def resolve_configuration(
    goal: Mapping[str, Any] | None = None,
    machine_configuration: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    raw = (machine_configuration or {}).get("namespaces", {}).get("pull_request_review")
    config = normalize_pull_request_review_machine_defaults(
        raw or {"schema_version": PULL_REQUEST_REVIEW_MACHINE_DEFAULTS_SCHEMA}
    )
    config.pop("schema_version")
    override = configuration_summary(goal or {})
    return typed_configuration({"action": "resolve", "machine": config if raw is not None else None,
                                "goal": override, "agent_id": agent_id,
                                "registered_agents": registered_agent_ids_for_goal(dict(goal or {}))})


def apply_change(
    goal: dict[str, Any], configuration: Mapping[str, Any] | None, *, clear: bool,
    agent_order_updates: Mapping[str, str | None] | None = None,
    reconcile_registered_agents: bool = False,
) -> None:
    if clear and (configuration is not None or agent_order_updates is not None):
        raise ValueError(
            "clear PR review configuration cannot be combined with settings"
        )
    if not clear and configuration is None and agent_order_updates is None and (
        not reconcile_registered_agents or configuration_summary(goal) is None
    ):
        return
    control = dict(goal.get("control_plane") or {})
    if clear:
        control.pop("pull_request_review", None)
    else:
        current = configuration_summary(goal) or {}
        current = typed_configuration({"action": "patch", "current": current,
                                       "patch": dict(configuration) if configuration is not None else None,
                                       "agent_order_updates": dict(agent_order_updates) if agent_order_updates is not None else None,
                                       "reconcile_agents": reconcile_registered_agents,
                                       "registered_agents": registered_agent_ids_for_goal(goal)})
        control["pull_request_review"] = {
            "schema_version": GOAL_CONFIGURATION_SCHEMA,
            **current,
        }
    if control:
        goal["control_plane"] = control
    else:
        goal.pop("control_plane", None)
