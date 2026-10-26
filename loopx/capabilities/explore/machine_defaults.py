"""Machine preference IO and adapters to Explore's existing typed mode owner."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from ...control_plane.effect_runtime import effect_runtime_result
from ...explore_graph import plan_explore_configuration
from ..machine_configuration.contract import (
    MACHINE_CONFIGURATION_SCHEMA, MachineConfigurationNamespace,
)

EXPLORE_MACHINE_DEFAULTS_SCHEMA = "explore_harness_machine_defaults_v0"


def normalize_explore_machine_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    if set(raw) != {"schema_version", "mode"} or raw.get("schema_version") != EXPLORE_MACHINE_DEFAULTS_SCHEMA:
        raise ValueError("explore_harness requires its schema_version and mode")
    if not isinstance(raw["mode"], str):
        raise ValueError("Explore machine mode must be off, evidence or planning")
    return {"schema_version": EXPLORE_MACHINE_DEFAULTS_SCHEMA, "mode": _validated_mode(raw["mode"])}


@lru_cache(maxsize=3)
def _validated_mode(mode: str) -> str:
    return str(plan_explore_configuration({}, {"mode": mode})["mode"])


def explore_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace="explore_harness",
        schema_versions=frozenset({EXPLORE_MACHINE_DEFAULTS_SCHEMA}),
        normalize=normalize_explore_machine_defaults,
        project_public=lambda value: dict(value),
        apply_public_update=lambda _current, update: dict(update),
        title="Explore Harness",
        description=("Default exploration mode for existing and new Goals. Explicit Goal modes take precedence. "
                     "Planning includes evidence and read-only guidance; execution authority remains separate."),
        documentation={"path": "loopx/capabilities/explore/README.md#machine-default"},
        default_configuration={"schema_version": EXPLORE_MACHINE_DEFAULTS_SCHEMA, "mode": "off"},
    )


@lru_cache(maxsize=128)
def _resolve(encoded: str) -> dict[str, Any]:
    return effect_runtime_result("explore.configuration.goal", json.loads(encoded))


def resolve_explore_configuration(
    goal: Mapping[str, Any], machine_mode: str | None = None,
) -> dict[str, Any]:
    spawn = goal.get("spawn_policy") or {}
    # Only the capability configuration crosses the typed boundary, never the
    # Goal's private context or another capability's bindings.
    return deepcopy(_resolve(json.dumps({
        "graph": goal.get("explore_graph") or {},
        "harness": spawn.get("explore_harness") or {},
        "machine_mode": machine_mode,
    }, sort_keys=True)))


def configuration_summary(goal: Mapping[str, Any]) -> dict[str, Any] | None:
    return resolve_explore_configuration(goal)["goal_override"]


def apply_explore_machine_default(
    goal: Mapping[str, Any], machine_configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    machine_mode = None
    if machine_configuration is not None:
        if machine_configuration.get("schema_version") != MACHINE_CONFIGURATION_SCHEMA:
            raise ValueError(f"machine_configuration must use {MACHINE_CONFIGURATION_SCHEMA}")
        namespaces = machine_configuration.get("namespaces")
        if not isinstance(namespaces, Mapping):
            raise TypeError("machine_configuration.namespaces must be an object")
        raw = namespaces.get("explore_harness")
        if raw is not None:
            if not isinstance(raw, Mapping):
                raise TypeError("explore_harness machine configuration must be an object")
            machine_mode = normalize_explore_machine_defaults(raw)["mode"]
    resolved = resolve_explore_configuration(goal, machine_mode)
    projected = deepcopy(dict(goal))
    if resolved["mode_source"] == "machine_default":
        projected["explore_graph"] = {**(projected.get("explore_graph") or {}),
                                      "enabled": resolved["evidence_enabled"]}
        spawn = dict(projected.get("spawn_policy") or {})
        harness = dict(spawn.get("explore_harness") or {})
        harness["enabled"] = resolved["planning_enabled"]
        spawn["explore_harness"] = harness
        projected["spawn_policy"] = spawn
    return projected


def resolve_explore_goal(goal: Mapping[str, Any], runtime_root: Path) -> dict[str, Any]:
    """Resolve live settings for callers that read the raw registry, without writes."""
    if resolve_explore_configuration(goal)["mode_source"] == "goal_override":
        return deepcopy(dict(goal))
    from ..machine_configuration.builtins import build_builtin_machine_configuration_registry
    from ..machine_configuration.store import read_machine_configuration

    return apply_explore_machine_default(goal, read_machine_configuration(
        runtime_root, registry=build_builtin_machine_configuration_registry(),
    ))
