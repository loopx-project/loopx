from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .cadence import normalize_report_cadence


GoalPeriodicReportChange = tuple[bool, dict[str, Any] | None]


def managed_session_report_destination(configuration: Mapping[str, Any]) -> str | None:
    """Decode the reserved Core conversation route; this proves no authority.

    The native Chat owner still validates the exact Session, registered Agent,
    Goal lifetime, subscription, Task and original worker on every dispatch.
    """
    route = configuration.get("route_ref")
    if configuration.get("profile_preset") == "weekly-progress" and isinstance(route, str):
        match = re.fullmatch(r"managed-session:([a-f0-9]{32})", route)
        if match:
            return match[1]
    return None


def configuration_summary(goal: Mapping[str, Any]) -> dict[str, Any] | None:
    control_plane = goal.get("control_plane")
    periodic = (
        control_plane.get("periodic_report")
        if isinstance(control_plane, Mapping)
        else None
    )
    return deepcopy(dict(periodic)) if isinstance(periodic, Mapping) else None


def normalize_configuration(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"enabled", "profile_preset", "route_ref", "timezone", "schedule"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            "unknown periodic report configuration field(s): " + ", ".join(unknown)
        )
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise TypeError("periodic_report.enabled must be a boolean")
    timezone = str(value.get("timezone") or "UTC").strip()
    if timezone != "UTC":
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("periodic_report.timezone is unknown") from exc
    profile_preset = str(value.get("profile_preset") or "").strip()
    route_ref = str(value.get("route_ref") or "").strip()
    if enabled and (not profile_preset or not route_ref):
        raise ValueError(
            "enabled periodic_report requires profile_preset and route_ref"
        )
    if route_ref.startswith("managed-session:") and managed_session_report_destination(value) is None:
        raise ValueError("a managed Session report requires weekly-progress and its exact original Session route")
    normalized = {
        "enabled": enabled,
        "profile_preset": profile_preset or None,
        "route_ref": route_ref or None,
        "timezone": timezone,
    }
    schedule = normalize_report_cadence(value.get("schedule"))
    if schedule is not None:
        if schedule["timezone"] != timezone:
            raise ValueError("schedule.timezone must match periodic_report.timezone")
        normalized["schedule"] = schedule
    return normalized


def normalize_change(
    configuration: Mapping[str, Any] | None,
    *,
    clear: bool,
) -> GoalPeriodicReportChange:
    if clear and configuration is not None:
        raise ValueError(
            "clear_periodic_report_configuration cannot be combined with "
            "periodic_report_configuration"
        )
    return clear, normalize_configuration(configuration) if configuration is not None else None


def apply_change(
    goal: dict[str, Any],
    change: GoalPeriodicReportChange,
) -> None:
    clear, configuration = change
    if not clear and configuration is None:
        return
    control_plane = (
        goal.get("control_plane")
        if isinstance(goal.get("control_plane"), dict)
        else {}
    )
    if clear:
        control_plane.pop("periodic_report", None)
    else:
        control_plane["periodic_report"] = configuration
    goal["control_plane"] = control_plane


__all__ = [
    "GoalPeriodicReportChange",
    "apply_change",
    "configuration_summary",
    "normalize_change",
    "normalize_configuration",
]
