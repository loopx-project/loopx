"""Independent, default-off preference for native compaction working memory."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contract import MachineConfigurationNamespace

COMPACTION_MEMORY_MACHINE_SCHEMA = "compaction_memory_machine_defaults_v0"


def normalize_compaction_memory(raw: Mapping[str, Any]) -> dict[str, Any]:
    if (set(raw) != {"schema_version", "enabled"}
            or raw.get("schema_version") != COMPACTION_MEMORY_MACHINE_SCHEMA
            or type(raw.get("enabled")) is not bool):
        raise ValueError("compaction_memory requires its schema_version and boolean enabled")
    return {"schema_version": COMPACTION_MEMORY_MACHINE_SCHEMA, "enabled": raw["enabled"]}


def compaction_memory_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace="compaction_memory",
        schema_versions=frozenset({COMPACTION_MEMORY_MACHINE_SCHEMA}),
        normalize=normalize_compaction_memory,
        project_public=dict,
        apply_public_update=lambda current, update: {**dict(current or {}), **dict(update)},
        title="Compaction working memory (experimental)",
        description="Preserve a small source-bound working view at compaction on supported native sessions. No extra model calls.",
        documentation={"path": "docs/reference/required-work-context.md#experimental-compaction-working-memory"},
        default_configuration={"schema_version": COMPACTION_MEMORY_MACHINE_SCHEMA, "enabled": False},
    )


def compaction_memory_enabled(runtime_root: Path) -> bool:
    """Read only this preference; an optional invalid setting cannot break recovery."""
    from .builtins import build_builtin_machine_configuration_registry
    from .store import read_machine_configuration

    try:
        value = read_machine_configuration(
            runtime_root, registry=build_builtin_machine_configuration_registry(),
            namespace_ids=("compaction_memory",),
        )
        return bool(value and value["namespaces"].get("compaction_memory", {}).get("enabled") is True)
    except (OSError, TypeError, ValueError):
        return False
