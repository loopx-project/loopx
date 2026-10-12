"""Machine-configuration and bootstrap transport for the TS storage owner."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contract import MachineConfigurationNamespace
from ...control_plane.effect_runtime import effect_runtime_result

from ...control_plane.coordination.new_goal_storage_transport import (
    CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA, NEW_GOAL_STORAGE_METHOD,
)

GOAL_STORAGE_DEFAULTS_SCHEMA = "loopx_goal_storage_defaults_v0"


def normalize_goal_storage_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    # Configuration-envelope validation only; target resolution/admission is TS-owned.
    canonical = raw.get("schema_version") == CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA
    fields = {"schema_version", "new_goal_provider", "canonical_creation", "new_goal_handoff_mode"} if canonical else {"schema_version", "new_goal_provider"}
    if set(raw) != fields or (not canonical and raw.get("schema_version") != GOAL_STORAGE_DEFAULTS_SCHEMA):
        raise ValueError("goal_storage requires schema_version and new_goal_provider")
    if raw.get("new_goal_provider") not in ("file", "sqlite"):
        raise ValueError("new_goal_provider must be file or sqlite")
    if canonical and (type(raw["canonical_creation"]) is not bool or raw["new_goal_handoff_mode"] not in ("soft_claim", "hard_lease")):
        raise ValueError("canonical_creation must be boolean; new_goal_handoff_mode must be soft_claim or hard_lease")
    return dict(raw)


def goal_storage_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace="goal_storage", schema_versions=frozenset({GOAL_STORAGE_DEFAULTS_SCHEMA, CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA}),
        normalize=normalize_goal_storage_defaults, project_public=dict,
        apply_public_update=lambda _current, update: dict(update),
        title="New Goal authority",
        description=("New Goals default to canonical SQLite with hard leases. Choose storage and execution policy. "
                     "Fixed for future Goals only; existing data requires a separate reviewed migration."),
        default_configuration={"schema_version": CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA, "new_goal_provider": "sqlite",
                               "canonical_creation": True, "new_goal_handoff_mode": "hard_lease"},
        documentation={"path": "docs/reference/local-authority-provider-selection.md",
                       "url": "https://github.com/loopx-project/loopx/blob/main/docs/reference/local-authority-provider-selection.md"},
    )


def new_goal_storage_target(runtime_root: Path) -> dict[str, Any]:
    from .builtins import build_builtin_machine_configuration_registry
    from .store import read_machine_configuration
    registry = build_builtin_machine_configuration_registry()
    configuration = read_machine_configuration(runtime_root, registry=registry)
    raw = (configuration or {}).get("namespaces", {}).get("goal_storage")
    if raw is None:
        # Reuse the registered configuration owner also projected by Settings.
        # Existing Goals do not enter this creation-time resolution path.
        raw = registry.resolve("goal_storage").default_configuration
    return effect_runtime_result(NEW_GOAL_STORAGE_METHOD, {"action": "resolve", "configuration": raw})
