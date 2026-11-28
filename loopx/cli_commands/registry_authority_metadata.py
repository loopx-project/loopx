"""Explicit metadata patches over the existing project material registration owner."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import cast

from ..authority import (
    Payload, _apply_authority_entry, _dict_view, find_goal_index,
    normalize_project_materials, now_local, public_safe_optional, validate_authority_source_id,
)
from ..control_plane.projects.registry_codec import (
    load_project_registry, mutate_project_registry, require_runtime_compatible_project_registry,
)


def update_authority_source(
    *, registry_path: Path, goal_id: str, source_id: str,
    freshness: str | None = None, owner_status: str | None = None,
    gate_status: str | None = None, revision: str | None = None,
    conflict_rule: str | None = None, dry_run: bool = False,
) -> Payload:
    """Patch recorded metadata under the registry lock; never reopen the source."""
    registry_path = registry_path.expanduser()
    source_id = validate_authority_source_id(source_id)
    changes: Payload = {}
    for name, value in (("freshness", freshness), ("owner_status", owner_status),
                        ("gate_status", gate_status), ("revision", revision),
                        ("conflict_rule", conflict_rule)):
        if value is not None:
            cleaned = public_safe_optional(name, value)
            if not cleaned:
                raise ValueError(f"{name} must not be empty")
            changes[name] = cleaned
    if not changes:
        raise ValueError("specify at least one metadata field to update")

    def reduce(registry: Payload) -> tuple[Payload, Payload]:
        require_runtime_compatible_project_registry(registry, operation="authority metadata update")
        index = find_goal_index(registry, goal_id)
        goal = cast(list[Payload], registry["goals"])[index]
        authority = _dict_view(goal.get("authority_registry"))
        materials = normalize_project_materials(authority.get("project_materials"))
        if source_id not in materials:
            raise ValueError("source_id is not registered in this authoritative project registry; "
                             "select the project registry or register the source first")
        entry = dict(materials[source_id])
        entry.update(changes)
        entry["id"] = source_id
        summary, _previous = _apply_authority_entry(
            registry, goal_id=goal_id, entry=entry, registered_at=now_local(),
        )
        return entry, summary

    if dry_run:
        registry = load_project_registry(registry_path)
        entry, summary = reduce(copy.deepcopy(registry))
    else:
        entry, summary = mutate_project_registry(
            registry_path, operation="update_authority_source", reducer=reduce,
        )
    return {
        "ok": True, "operation": "update", "dry_run": dry_run, "written": not dry_run,
        "registry": str(registry_path), "goal_id": goal_id, "source_id": source_id,
        "entry": entry, "updated_fields": sorted(changes),
        "authority_registry_path": "authority_registry.project_materials",
        "authority_registry_summary": summary, "raw_source_ref_stored": False,
        "write_effect": "updated recorded metadata only; omitted fields, reference provenance and topics preserved; "
                        "no source access or permission change" if not dry_run else
                        "would update recorded metadata only; no registry writes or source access",
    }
