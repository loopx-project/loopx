"""IO adaptation only. The original Goal registry owns the configuration."""

from collections.abc import Mapping
from typing import Any

from ...control_plane.effect_runtime import EffectRuntimeRemoteError, effect_runtime_result


def raw_configuration(goal: Mapping[str, Any]) -> object:
    control_plane = goal.get("control_plane")
    return control_plane.get("capability_improvement") if isinstance(control_plane, Mapping) else None


def configuration_summary(goal: Mapping[str, Any]) -> dict[str, Any] | None:
    value = raw_configuration(goal)
    if value is None:
        return None
    return effect_runtime_result("capability.improvement.inspect", {"policy": value})


def apply_change(goal: dict[str, Any], patch: Mapping[str, Any] | None, *, clear: bool) -> None:
    if patch is None and not clear:
        return
    try:
        result = effect_runtime_result("capability.improvement.configuration", {
            "current": raw_configuration(goal), "patch": dict(patch) if patch is not None else None,
            "clear": clear,
        })
    except EffectRuntimeRemoteError as exc:
        raise ValueError(str(exc)) from exc
    control_plane = dict(goal.get("control_plane") or {})
    if result["configuration"] is None:
        control_plane.pop("capability_improvement", None)
    else:
        control_plane["capability_improvement"] = result["configuration"]
    if control_plane:
        goal["control_plane"] = control_plane
    else:
        goal.pop("control_plane", None)
