"""Registered machine settings for the existing native Codex transport."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract import MachineConfigurationNamespace
from ...control_plane.turn_driver.codex_context import CONTEXT_DELIVERY_SETTINGS


CONTEXT_DELIVERY_MACHINE_SCHEMA = "context_delivery_machine_defaults_v0"


def normalize_context_delivery_machine_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    keys = set(raw)
    if not ({"schema_version", "mode"} <= keys <= {"schema_version", "mode", "antigravity_mode"}) or (
        raw.get("schema_version") != CONTEXT_DELIVERY_MACHINE_SCHEMA
    ):
        raise ValueError("context_delivery requires its schema_version and mode")
    antigravity_mode = raw.get("antigravity_mode", "on")
    if raw["mode"] not in CONTEXT_DELIVERY_SETTINGS or antigravity_mode not in CONTEXT_DELIVERY_SETTINGS:
        raise ValueError("context_delivery mode must be off or on")
    return {
        "schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA,
        "mode": raw["mode"],
        "antigravity_mode": antigravity_mode,
    }


def context_delivery_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace="context_delivery",
        schema_versions=frozenset({CONTEXT_DELIVERY_MACHINE_SCHEMA}),
        normalize=normalize_context_delivery_machine_defaults,
        project_public=lambda value: dict(value),
        apply_public_update=lambda current, update: {**dict(current or {}), **dict(update)},
        title="Differential context",
        description=(
            "Reuse unchanged context for Codex App heartbeats, SSH Goals and benchmark heartbeats (`mode`), "
            "and read trusted-owner Antigravity manager delivery histories progressively from private captures "
            "(`antigravity_mode`). Both default on independently; explicit unsupported Codex activation fails "
            "and known context loss or disabled provider preferences keep full context. "
            "Completed Codex reads use an explicit native-hook continuity assumption. "
            "A saved preference does not establish host readiness or actual delta use."
        ),
        documentation={"path": "docs/reference/required-work-context.md#machine-default"},
        default_configuration={
            "schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA,
            "mode": "on",
            "antigravity_mode": "on",
        },
    )
