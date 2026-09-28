"""Machine-configuration and bootstrap transport for the TS storage owner."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...capabilities.machine_configuration.contract import MachineConfigurationNamespace
from ..effect_runtime import effect_runtime_result

SCHEMA = "loopx_goal_storage_defaults_v0"
METHOD = "coordination.local_authority.new_goal_storage"


def normalize_goal_storage_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    # Configuration-envelope validation only; target resolution/admission is TS-owned.
    if set(raw) != {"schema_version", "new_goal_provider"} or raw.get("schema_version") != SCHEMA:
        raise ValueError("goal_storage requires schema_version and new_goal_provider")
    if raw.get("new_goal_provider") not in ("file", "sqlite"):
        raise ValueError("new_goal_provider must be file or sqlite")
    return dict(raw)


def goal_storage_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace="goal_storage", schema_versions=frozenset({SCHEMA}),
        normalize=normalize_goal_storage_defaults, project_public=dict,
        apply_public_update=lambda _current, update: dict(update),
        title="New Goal storage target",
        description=("Fixed when a new Goal is created; used after reviewed promotion. "
                     "Does not promote Goals or migrate existing data. Existing Goals keep their selection."),
        default_configuration={"schema_version": SCHEMA, "new_goal_provider": "file"},
        documentation={"path": "docs/reference/local-authority-provider-selection.md",
                       "url": "https://github.com/loopx-project/loopx/blob/main/docs/reference/local-authority-provider-selection.md"},
    )


def new_goal_storage_target(runtime_root: Path) -> dict[str, Any] | None:
    from ...capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
    from ...capabilities.machine_configuration.store import read_machine_configuration
    configuration = read_machine_configuration(runtime_root, registry=build_builtin_machine_configuration_registry())
    raw = (configuration or {}).get("namespaces", {}).get("goal_storage")
    if raw is None:
        return None
    return effect_runtime_result(METHOD, {"action": "resolve", "configuration": raw})


def initialize_goal_storage_target(runtime_root: Path, goal: Mapping[str, Any]) -> dict[str, Any] | None:
    target = (goal.get("coordination") or {}).get("storage_target")
    if target is None:
        return None
    return effect_runtime_result(METHOD, {"action": "initialize", "runtime_root": str(runtime_root),
                                          "goal_id": goal["id"], "target": target})
