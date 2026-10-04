"""Source-owner capture and CLI/HTTP transport for the TS configuration checkpoint."""
from pathlib import Path
from typing import Any

from ..capabilities.machine_configuration.store import read_stored_machine_configuration
from ..control_plane.effect_runtime import effect_runtime_result
from ..control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ..history import load_registry
from ..registry import registry_goals


def capture_configuration_backup(
    *, registry_path: Path, runtime_root: Path, goal_ids: list[str] | None = None,
) -> dict[str, Any]:
    registry = load_registry(registry_path) if registry_path.exists() else {"goals": []}
    selected = goal_ids if goal_ids is not None else [str(goal["id"]) for goal in registry_goals(registry)]
    if len(selected) != len(set(selected)):
        raise ValueError("configuration backup Goal ids must be unique")
    snapshots = []
    for goal_id in selected:
        route = resolve_goal_source_runtime_route(registry_path=registry_path, goal_id=goal_id)
        source = Path(route["source_registry"])
        matches = [goal for goal in registry_goals(load_registry(source)) if goal.get("id") == goal_id]
        if len(matches) != 1:
            raise ValueError("configuration backup requires exactly one source-owned Goal")
        snapshots.append({"goal_id": goal_id, "goal_configuration": matches[0]})
    data = {
        "machine_configuration": read_stored_machine_configuration(runtime_root), "goals": snapshots,
    }
    backup = effect_runtime_result("configuration.backup", {"action": "capture", "data": data})
    if backup.get("data") != data:
        raise ValueError("configuration transport cannot preserve the complete source values")
    return backup


def verify_configuration_backup(backup: dict[str, Any]) -> dict[str, Any]:
    return effect_runtime_result("configuration.backup", {"action": "verify", "backup": backup})


def restore_configuration_backup(
    backup: dict[str, Any], *, destination: Path, expected_sha256: str, execute: bool = False,
) -> dict[str, Any]:
    return effect_runtime_result("configuration.backup", {"action": "restore", "backup": backup,
        "destination": str(destination.absolute()), "expected_sha256": expected_sha256, "execute": execute})
