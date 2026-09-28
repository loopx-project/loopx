"""Shared, read-only Goal settings projection for Dashboard and agent discovery.

This module does not decide enablement, readiness, selection or utility. Values
and revisions come from the existing configuration owners.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..configuration_transaction import goal_capability_configuration_revision
from .configuration_ui import build_capability_configuration_catalog
from .machine_configuration.builtins import build_builtin_machine_configuration_registry
from .machine_configuration.store import inspect_machine_configuration

GOAL_CONFIGURATION_INSPECTION_SCHEMA = "goal_configuration_inspection_v0"


def inspect_machine_namespaces(runtime_root: Path) -> list[Mapping[str, Any]]:
    registry = build_builtin_machine_configuration_registry()
    inspection = inspect_machine_configuration(runtime_root, registry=registry)
    current_namespaces = (
        inspection.get("machine_configuration", {}).get("namespaces", {})
        if isinstance(inspection.get("machine_configuration"), Mapping)
        else {}
    )
    return [
        {
            **descriptor,
            **(
                {"current": current_namespaces[descriptor["namespace"]]}
                if descriptor["namespace"] in current_namespaces
                else {}
            ),
        }
        for descriptor in registry.public_catalog()["namespaces"]
    ]


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is unavailable")
    return {str(key): item for key, item in value.items()}


def _goal_features_with_machine_context(
    payload: Mapping[str, Any],
    catalog: Mapping[str, Any],
    machine_namespaces: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    features = catalog.get("features")
    if not isinstance(features, list) or any(
        not isinstance(item, Mapping) for item in features
    ):
        raise ValueError("Goal feature catalog is invalid")
    goal_features = [dict(item) for item in features]
    machine_has_periodic_report = any(
        str(item.get("namespace") or "") == "periodic_report"
        for item in machine_namespaces
    )
    goal_has_periodic_report = any(
        str(item.get("feature_id") or "") == "periodic_report" for item in goal_features
    )
    if machine_has_periodic_report and not goal_has_periodic_report:
        after = payload.get("after")
        control_plane = (
            after.get("control_plane")
            if isinstance(after, Mapping)
            and isinstance(after.get("control_plane"), Mapping)
            else {}
        )
        current = control_plane.get("periodic_report")
        periodic_report = {
            "feature_id": "periodic_report",
            "display_name": "Periodic report",
            "availability": "supported_explicit_override",
            "default": {"enabled": False, "timezone": "UTC"},
            "effect": "Use a complete Goal-specific report route instead of the live machine default.",
        }
        if isinstance(current, Mapping):
            periodic_report["current"] = dict(current)
        goal_features.append(periodic_report)
    return goal_features


def project_goal_configuration(
    payload: Mapping[str, Any],
    *,
    machine_namespaces: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    goal_id = str(payload.get("goal_id") or "").strip()
    if not goal_id:
        raise ValueError("Goal configuration result is missing goal_id")
    catalog = _mapping(payload.get("configuration_catalog"), "configuration catalog")
    capability_catalog = _mapping(
        catalog.get("capability_catalog"), "capability catalog"
    )
    if machine_namespaces is not None:
        capability_catalog = build_capability_configuration_catalog(
            machine_namespaces=machine_namespaces,
            goal_features=_goal_features_with_machine_context(
                payload, catalog, machine_namespaces
            ),
        )
    capabilities = capability_catalog.get("capabilities")
    if not isinstance(capabilities, list) or any(
        not isinstance(item, Mapping) for item in capabilities
    ):
        raise ValueError("capability catalog is invalid")
    capability_ids = [
        str(item.get("capability_id") or "").strip() for item in capabilities
    ]
    if not all(capability_ids) or len(set(capability_ids)) != len(capability_ids):
        raise ValueError("capability catalog contains an invalid identity")
    return {
        "ok": True,
        "schema_version": GOAL_CONFIGURATION_INSPECTION_SCHEMA,
        "status": "configured",
        "goal_id": goal_id,
        "revision": goal_capability_configuration_revision(goal_id, capability_catalog),
        "available_capabilities": capability_ids,
        "capability_catalog": capability_catalog,
    }
