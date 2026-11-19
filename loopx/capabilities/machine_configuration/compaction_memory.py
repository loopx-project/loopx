"""Strict decoder for a retired experiment; no activation or runtime owner."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COMPACTION_MEMORY_MACHINE_SCHEMA = "compaction_memory_machine_defaults_v0"


def normalize_compaction_memory(raw: Mapping[str, Any]) -> dict[str, Any]:
    if (set(raw) != {"schema_version", "enabled"}
            or raw.get("schema_version") != COMPACTION_MEMORY_MACHINE_SCHEMA
            or type(raw.get("enabled")) is not bool):
        raise ValueError("compaction_memory requires its schema_version and boolean enabled")
    return {"schema_version": COMPACTION_MEMORY_MACHINE_SCHEMA, "enabled": raw["enabled"]}
