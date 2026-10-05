"""Machine-configuration and bootstrap transport for the TS storage owner."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contract import MachineConfigurationNamespace
from ...control_plane.effect_runtime import effect_runtime_result

GOAL_STORAGE_DEFAULTS_SCHEMA = "loopx_goal_storage_defaults_v0"
CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA = "loopx_goal_storage_defaults_v1"
NEW_GOAL_STORAGE_METHOD = "coordination.local_authority.new_goal_storage"


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
        description=("Opt in to canonical creation and choose its execution policy. "
                     "Fixed for future Goals only; existing data requires a separate reviewed migration."),
        default_configuration={"schema_version": CANONICAL_GOAL_STORAGE_DEFAULTS_SCHEMA, "new_goal_provider": "file",
                               "canonical_creation": False, "new_goal_handoff_mode": "hard_lease"},
        documentation={"path": "docs/reference/local-authority-provider-selection.md",
                       "url": "https://github.com/loopx-project/loopx/blob/main/docs/reference/local-authority-provider-selection.md"},
    )


def new_goal_storage_target(runtime_root: Path) -> dict[str, Any] | None:
    from .builtins import build_builtin_machine_configuration_registry
    from .store import read_machine_configuration
    configuration = read_machine_configuration(runtime_root, registry=build_builtin_machine_configuration_registry())
    raw = (configuration or {}).get("namespaces", {}).get("goal_storage")
    if raw is None:
        return None
    return effect_runtime_result(NEW_GOAL_STORAGE_METHOD, {"action": "resolve", "configuration": raw})


def initialize_goal_storage_target(runtime_root: Path, goal: Mapping[str, Any], *, registry_path: Path | None = None) -> dict[str, Any] | None:
    target = (goal.get("coordination") or {}).get("storage_target")
    if target is None:
        return None
    request = {"action": "initialize", "runtime_root": str(runtime_root), "goal_id": goal["id"], "target": target}
    if target.get("schema_version") == "loopx_new_goal_storage_target_v1":
        from ...registry import resolve_state_file
        from ...control_plane.coordination.authority_source_capture import authority_registry_source
        from ...agent_registry import registered_agent_ids_for_goal
        if registry_path is None:
            raise ValueError("Canonical creation requires its registered source")
        state_path = resolve_state_file(Path(goal["repo"]), goal["state_file"])
        with authority_registry_source(registry_path) as witness:
            request.update(creation_operation_id=goal.get("creation_operation_id"), source_snapshot={
                "state_path": str(state_path.resolve()),
                "registry_source": {**witness, "registered_agents": registered_agent_ids_for_goal(dict(goal))}})
            result = effect_runtime_result(NEW_GOAL_STORAGE_METHOD, request)
        # Native receipt readback needs no Markdown parsing. Only the typed
        # owner can request a complete source capture for unfinished creation.
        if result.get("source_capture_required") is not True:
            return result
        from ...control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
        projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime_root,
            state_path=state_path, registry_path=registry_path)
        request.update(creation_operation_id=goal.get("creation_operation_id"), projection=projection, source_snapshot=snapshot)
    return effect_runtime_result(NEW_GOAL_STORAGE_METHOD, request)
