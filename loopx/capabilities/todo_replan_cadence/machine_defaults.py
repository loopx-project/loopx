from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from enum import Enum
from pathlib import Path

from ...control_plane.goals.goal_vision_policy import (
    DEFAULT_EFFECTIVE_TURN_REPLAN_THRESHOLD,
    normalize_completed_todo_replan_threshold,
    normalize_effective_turn_replan_threshold,
)
from ..machine_configuration.contract import (
    MACHINE_CONFIGURATION_SCHEMA,
    MachineConfigurationNamespace,
)

TODO_REPLAN_CADENCE_MACHINE_DEFAULTS_SCHEMA = "todo_replan_cadence_machine_defaults_v0"


# Extends the existing cadence capability configuration (machine schema v1).
# The work-item TS owner decides which history and receipts count.
class ReplanCadenceUnit(str, Enum):
    COMPLETED_TODOS = "completed_todos"
    EFFECTIVE_TURNS = "effective_turns"


TODO_REPLAN_CADENCE_MACHINE_DEFAULTS_V1 = "todo_replan_cadence_machine_defaults_v1"


def normalize_replan_cadence_configuration(raw: Mapping[str, Any]) -> dict[str, Any]:
    if set(raw) == {"completed_todos"}:
        return {
            "count_unit": ReplanCadenceUnit.COMPLETED_TODOS.value,
            "count": normalize_completed_todo_replan_threshold(raw["completed_todos"]),
        }
    if set(raw) != {"count_unit", "count"}:
        raise ValueError("review cadence requires count_unit and count")
    try:
        unit = ReplanCadenceUnit(raw["count_unit"])
    except (ValueError, TypeError):
        raise ValueError(
            "count_unit must be completed_todos or effective_turns"
        ) from None
    count = raw["count"]
    if unit is ReplanCadenceUnit.COMPLETED_TODOS:
        count = normalize_completed_todo_replan_threshold(count)
    else:
        count = normalize_effective_turn_replan_threshold(count)
    return {"count_unit": unit.value, "count": count}


def normalize_todo_replan_cadence_machine_defaults(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    version = raw.get("schema_version")
    if version == TODO_REPLAN_CADENCE_MACHINE_DEFAULTS_SCHEMA:
        if set(raw) - {"schema_version", "completed_todos"}:
            raise ValueError("todo_replan_cadence contains unsupported fields")
        return {
            "schema_version": version,
            "completed_todos": normalize_completed_todo_replan_threshold(
                raw.get("completed_todos")
            ),
        }
    if version != TODO_REPLAN_CADENCE_MACHINE_DEFAULTS_V1:
        raise ValueError("todo_replan_cadence must use a supported v0 or v1 schema")
    if set(raw) != {"schema_version", "count_unit", "count"}:
        raise ValueError("v1 review cadence requires count_unit and count")
    return {
        "schema_version": version,
        **normalize_replan_cadence_configuration(
            {key: value for key, value in raw.items() if key != "schema_version"}
        ),
    }


def cadence_configuration_value(raw: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_todo_replan_cadence_machine_defaults(raw)
    return normalize_replan_cadence_configuration(
        {key: value for key, value in normalized.items() if key != "schema_version"}
    )


def default_replan_cadence_configuration() -> dict[str, Any]:
    """One capability default for configuration editors and runtime inheritance."""
    return {
        "count_unit": ReplanCadenceUnit.EFFECTIVE_TURNS.value,
        "count": DEFAULT_EFFECTIVE_TURN_REPLAN_THRESHOLD,
    }


def todo_replan_cadence_machine_configuration_namespace() -> (
    MachineConfigurationNamespace
):
    return MachineConfigurationNamespace(
        namespace="todo_replan_cadence",
        schema_versions=frozenset(
            {
                TODO_REPLAN_CADENCE_MACHINE_DEFAULTS_SCHEMA,
                TODO_REPLAN_CADENCE_MACHINE_DEFAULTS_V1,
            }
        ),
        normalize=normalize_todo_replan_cadence_machine_defaults,
        project_public=lambda value: dict(value),
        apply_public_update=lambda _current, update: dict(update),
        title="Goal review cadence",
        description=(
            "Live review threshold for Goals without an explicit cadence override. "
            "Defaults to six settled work Turns; completed Todos remain an explicit option. "
            "It does not create "
            "turns, spend quota, or grant authority."
        ),
        default_configuration={
            "schema_version": TODO_REPLAN_CADENCE_MACHINE_DEFAULTS_V1,
            **default_replan_cadence_configuration(),
        },
    )


def _machine_default(
    machine_configuration: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if machine_configuration is None:
        return None
    if machine_configuration.get("schema_version") != MACHINE_CONFIGURATION_SCHEMA:
        raise ValueError(
            f"machine_configuration must use {MACHINE_CONFIGURATION_SCHEMA}"
        )
    namespaces = machine_configuration.get("namespaces")
    if not isinstance(namespaces, Mapping):
        raise TypeError("machine_configuration.namespaces must be an object")
    raw = namespaces.get("todo_replan_cadence")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError(
            "machine_configuration.namespaces.todo_replan_cadence must be an object"
        )
    return cadence_configuration_value(raw)


def apply_todo_replan_cadence_machine_default(
    goal: Mapping[str, Any],
    machine_configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project the live machine default without overwriting a Goal override."""

    projected = deepcopy(dict(goal))
    raw_profile = goal.get("execution_profile")
    if isinstance(raw_profile, Mapping) and (
        "replan_after_completed_todos" in raw_profile
        or "replan_after_effective_turns" in raw_profile
    ):
        return projected
    cadence = _machine_default(machine_configuration)
    if cadence is None:
        cadence = default_replan_cadence_configuration()
    profile = dict(raw_profile) if isinstance(raw_profile, Mapping) else {}
    field = (
        "replan_after_effective_turns"
        if cadence["count_unit"] == ReplanCadenceUnit.EFFECTIVE_TURNS.value
        else "replan_after_completed_todos"
    )
    profile[field] = cadence["count"]
    projected["execution_profile"] = profile
    return projected


def resolve_todo_replan_cadence_goal(
    goal: Mapping[str, Any], runtime_root: Path | None,
) -> dict[str, Any]:
    """Resolve live cadence for raw-registry writeback callers, without writes.

    History already composes machine defaults before calling the history codec.
    Explicit Goal cadence remains independent of device configuration IO.
    """
    profile = goal.get("execution_profile")
    if isinstance(profile, Mapping) and (
        "replan_after_effective_turns" in profile
        or "replan_after_completed_todos" in profile
    ):
        return apply_todo_replan_cadence_machine_default(goal, None)
    from ..machine_configuration.builtins import build_builtin_machine_configuration_registry
    from ..machine_configuration.store import read_machine_configuration

    machine = (
        read_machine_configuration(
            runtime_root, registry=build_builtin_machine_configuration_registry(),
        ) if runtime_root is not None else None
    )
    return apply_todo_replan_cadence_machine_default(goal, machine)
