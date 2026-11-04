"""Registered machine settings for the existing native Codex transport."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract import MachineConfigurationNamespace


CONTEXT_DELIVERY_MACHINE_SCHEMA = "context_delivery_machine_defaults_v0"


def normalize_context_delivery_machine_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    keys = set(raw)
    if not ({"schema_version", "mode"} <= keys <= {"schema_version", "mode", "delta_delivery", "antigravity_mode"}) or (
        raw.get("schema_version") != CONTEXT_DELIVERY_MACHINE_SCHEMA
    ):
        raise ValueError("context_delivery requires its schema_version and mode")
    antigravity_mode = raw.get("antigravity_mode", "on")
    delta = raw.get("delta_delivery", "auto")
    if (raw["mode"] not in ("off", "on") or antigravity_mode not in ("off", "on")
            or delta not in ("off", "auto")):
        raise ValueError("context_delivery mode and antigravity_mode must be off/on; delta_delivery off/auto")
    return {
        "schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA,
        "mode": raw["mode"],
        "delta_delivery": delta,
        **({"antigravity_mode": antigravity_mode} if "antigravity_mode" in raw else {}),
    }


def context_delivery_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace="context_delivery",
        schema_versions=frozenset({CONTEXT_DELIVERY_MACHINE_SCHEMA}),
        normalize=normalize_context_delivery_machine_defaults,
        project_public=lambda value: dict(value),
        apply_public_update=lambda current, update: {**dict(current or {}), **dict(update)},
        title="Progressive context",
        description=(
            "Read current requirements before expandable supporting material on every host (`mode`). "
            "Supported continuity hosts may reuse unchanged context across turns. "
            "Completed Codex reads use an explicit native-hook continuity assumption. "
            "A saved preference does not establish host readiness or actual delta use."
        ),
        documentation={"path": "docs/reference/required-work-context.md#machine-default"},
        default_configuration={
            "schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA,
            "mode": "on",
            "delta_delivery": "auto",
        },
    )
