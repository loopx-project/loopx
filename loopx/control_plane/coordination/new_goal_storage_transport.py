"""Registry/source-snapshot transport for the typed new-Goal storage owner.

Python only observes registered source bytes and sends requests. Target policy,
empty-source admission, writer fences, provider selection and recovery are TS-owned.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..effect_runtime import effect_runtime_result

CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA = "loopx_goal_storage_defaults_v1"
NEW_GOAL_STORAGE_METHOD = "coordination.local_authority.new_goal_storage"


def resolve_canonical_creation_target(provider: str) -> dict[str, Any]:
    result: dict[str, Any] = effect_runtime_result(NEW_GOAL_STORAGE_METHOD, {"action": "resolve", "configuration": {
        "schema_version": CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA, "new_goal_provider": provider,
        "canonical_creation": True, "new_goal_handoff_mode": "hard_lease"}})
    return result


def initialize_goal_storage_target(runtime_root: Path, goal: Mapping[str, Any], *, registry_path: Path | None = None) -> dict[str, Any] | None:
    target = (goal.get("coordination") or {}).get("storage_target")
    if target is None:
        return None
    request = {"action": "initialize", "runtime_root": str(runtime_root), "goal_id": goal["id"], "target": target}
    if target.get("schema_version") == "loopx_new_goal_storage_target_v1":
        from ...registry import resolve_state_file
        from .authority_source_capture import authority_registry_source
        from ...agent_registry import registered_agent_ids_for_goal
        from ..projects.registry_codec import load_project_registry
        from ...registry import find_registry_goal
        if registry_path is None:
            raise ValueError("Canonical creation requires its registered source")
        state_path = resolve_state_file(Path(goal["repo"]), goal["state_file"])
        if state_path is None:
            raise ValueError("Canonical creation requires its registered state file")
        with authority_registry_source(registry_path) as witness:
            # The existing codec validates strict envelope bytes. The typed
            # creation owner rechecks this complete preimage, so it can use the
            # exact source-session registration without a second JSON codec.
            registry = load_project_registry(registry_path)
            if registry.get("profile_id") == "source_session_v1":
                if find_registry_goal(registry, str(goal["id"])) != dict(goal):
                    raise ValueError("Goal registration changed before authority initialization")
                from ..goals.source_session_registry_state import current_goal_ref
                request["source_goal_ref"] = current_goal_ref(registry, goal_id=str(goal["id"]))[0]
            request.update(creation_operation_id=goal.get("creation_operation_id"), source_snapshot={
                "state_path": str(state_path.resolve()),
                "registry_source": {**witness, "registered_agents": registered_agent_ids_for_goal(dict(goal))}})
            result: dict[str, Any] = effect_runtime_result(NEW_GOAL_STORAGE_METHOD, request)
        # Native receipt readback needs no Markdown parsing. Only the typed
        # owner can request a complete source capture for unfinished creation.
        if result.get("source_capture_required") is not True:
            return result
        from .authority_source_capture import build_runtime_shadow_source_snapshot
        projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime_root,
            state_path=state_path, registry_path=registry_path)
        request.update(creation_operation_id=goal.get("creation_operation_id"), projection=projection, source_snapshot=snapshot)
    result = effect_runtime_result(NEW_GOAL_STORAGE_METHOD, request)
    return result
