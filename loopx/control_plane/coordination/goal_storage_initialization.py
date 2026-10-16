"""Initialize a new Goal from its frozen canonical storage target."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ...registry import resolve_state_file
from ..effect_runtime import effect_runtime_result
from .authority_source_capture import (
    authority_registry_source,
    build_runtime_shadow_source_snapshot,
)
from .new_goal_storage_transport import NEW_GOAL_STORAGE_METHOD


def initialize_goal_storage_target(
    runtime_root: Path,
    goal: Mapping[str, Any],
    *,
    registry_path: Path | None = None,
    validate_source: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    target = (goal.get("coordination") or {}).get("storage_target")
    if target is None:
        return None
    request = {
        "action": "initialize",
        "runtime_root": str(runtime_root),
        "goal_id": goal["id"],
        "target": target,
    }
    if target.get("schema_version") == "loopx_new_goal_storage_target_v1":
        if registry_path is None:
            raise ValueError("Canonical creation requires its registered source")
        state_path = resolve_state_file(Path(goal["repo"]), goal["state_file"])
        with authority_registry_source(registry_path) as witness:
            request.update(
                creation_operation_id=goal.get("creation_operation_id"),
                source_snapshot={
                    "state_path": str(state_path.resolve()),
                    "registry_source": {
                        **witness,
                        "registered_agents": registered_agent_ids_for_goal(dict(goal)),
                    },
                },
            )
            result = effect_runtime_result(NEW_GOAL_STORAGE_METHOD, request)
        if result.get("source_capture_required") is not True:
            return result
        # Only the typed owner can request unfinished creation's source. A
        # completed receipt must remain readable without its display bytes.
        validated_bytes = None
        if validate_source is not None:
            validated_bytes = state_path.read_bytes()
            validate_source(validated_bytes.decode("utf-8"))
        projection, snapshot = build_runtime_shadow_source_snapshot(
            goal=goal,
            runtime_root=runtime_root,
            state_path=state_path,
            registry_path=registry_path,
        )
        if validated_bytes is not None and snapshot["state_bytes_sha256"] != (
            "sha256:" + hashlib.sha256(validated_bytes).hexdigest()
        ):
            raise ValueError("Goal creation source changed during validation; retry")
        request.update(
            creation_operation_id=goal.get("creation_operation_id"),
            projection=projection,
            source_snapshot=snapshot,
        )
    return effect_runtime_result(NEW_GOAL_STORAGE_METHOD, request)
