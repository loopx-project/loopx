from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


RESOURCE_PORTFOLIO_SCHEMA_VERSION = "loopx_explore_resource_portfolio_v0"
RESOURCE_LANE_CAPABILITY_PREFIX = "resource_lane:"
RESOURCE_LANE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def normalize_resource_lane_key(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not RESOURCE_LANE_KEY_PATTERN.match(key):
        raise ValueError(
            f"resource lane {value!r} must start with a letter and contain only "
            "lowercase letters, digits, or underscores"
        )
    return key


def parse_resource_counts(values: Sequence[str] | None, *, flag_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"{flag_name} expects KEY=N, got: {value}")
        raw_key, raw_count = value.split("=", 1)
        key = normalize_resource_lane_key(raw_key)
        if key in counts:
            raise ValueError(f"{flag_name} repeats resource lane {key!r}")
        try:
            count = int(raw_count.strip())
        except ValueError as exc:
            raise ValueError(f"{flag_name} expects KEY=N with an integer N, got: {value}") from exc
        if count < 0:
            raise ValueError(f"{flag_name} expects a non-negative N, got: {value}")
        counts[key] = count
    return counts


def normalize_resource_counts(
    values: Mapping[str, Any] | None,
    *,
    field_name: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_key, raw_count in (values or {}).items():
        key = normalize_resource_lane_key(raw_key)
        if key in counts:
            raise ValueError(f"{field_name} repeats resource lane {key!r}")
        try:
            count = int(raw_count)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}[{key!r}] must be a non-negative integer") from exc
        if (
            isinstance(raw_count, bool)
            or count < 0
            or (not isinstance(raw_count, str) and raw_count != count)
        ):
            raise ValueError(f"{field_name}[{key!r}] must be a non-negative integer")
        counts[key] = count
    return dict(sorted(counts.items()))


def resolve_resource_portfolio(
    capacities: Mapping[str, Any] | None,
    usage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized_capacities = normalize_resource_counts(
        capacities,
        field_name="resource_capacities",
    )
    normalized_usage = normalize_resource_counts(
        usage,
        field_name="resource_usage",
    )
    unknown_usage = sorted(set(normalized_usage) - set(normalized_capacities))
    if unknown_usage:
        raise ValueError(
            "resource_usage requires matching resource_capacities for: "
            + ", ".join(unknown_usage)
        )
    lane_keys = sorted(normalized_capacities)
    lanes = {
        key: {
            "capacity": normalized_capacities[key],
            "current_usage": normalized_usage.get(key, 0),
            "available_slots": max(
                0,
                normalized_capacities[key] - normalized_usage.get(key, 0),
            ),
            "overcommitted_by": max(
                0,
                normalized_usage.get(key, 0) - normalized_capacities[key],
            ),
        }
        for key in lane_keys
    }
    return {
        "schema_version": RESOURCE_PORTFOLIO_SCHEMA_VERSION,
        "enabled": bool(normalized_capacities),
        "analysis_only": True,
        "score_delta": 0.0,
        "resource_capacities": normalized_capacities,
        "resource_usage": {key: normalized_usage.get(key, 0) for key in lane_keys},
        "available_slot_count": sum(lane["available_slots"] for lane in lanes.values()),
        "lanes": lanes,
        "continuous_monitor_consumes_capacity": False,
        "authority": {
            "writes_state": False,
            "claims_todos": False,
            "acquires_leases": False,
            "starts_workers": False,
            "changes_quota": False,
        },
    }


def resource_lane_from_capabilities(capabilities: Sequence[Any]) -> str:
    lanes = []
    for capability in capabilities:
        text = str(capability or "").strip().lower()
        if not text.startswith(RESOURCE_LANE_CAPABILITY_PREFIX):
            continue
        key = text.removeprefix(RESOURCE_LANE_CAPABILITY_PREFIX)
        if key and key not in lanes:
            lanes.append(normalize_resource_lane_key(key))
    if len(lanes) > 1:
        raise ValueError(
            "an advancement todo may declare only one resource_lane:<key> capability"
        )
    return lanes[0] if lanes else ""


def resource_assignment(
    portfolio: Mapping[str, Any],
    *,
    resource_lane: str,
    selected_count: int,
) -> dict[str, Any] | None:
    if not portfolio.get("enabled") or not resource_lane:
        return None
    lane = (portfolio.get("lanes") or {}).get(resource_lane)
    if not isinstance(lane, Mapping):
        return None
    return {
        "resource_lane": resource_lane,
        "slot_ordinal": int(lane.get("current_usage") or 0) + selected_count,
        "capacity": int(lane.get("capacity") or 0),
        "current_usage": int(lane.get("current_usage") or 0),
    }


def resource_portfolio_with_selection(
    portfolio: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result = {
        **dict(portfolio),
        "lanes": {
            key: dict(value)
            for key, value in (portfolio.get("lanes") or {}).items()
            if isinstance(value, Mapping)
        },
    }
    selected_by_lane = {
        key: sum(item.get("resource_lane") == key for item in selected)
        for key in result["lanes"]
    }
    for key, lane in result["lanes"].items():
        selected_count = selected_by_lane[key]
        lane["selected_slots"] = selected_count
        lane["remaining_slots"] = max(0, int(lane.get("available_slots") or 0) - selected_count)
    result["selected_slot_count"] = sum(selected_by_lane.values())
    result["remaining_slot_count"] = sum(
        int(lane.get("remaining_slots") or 0) for lane in result["lanes"].values()
    )
    result["unassigned_selected_count"] = sum(not item.get("resource_lane") for item in selected)
    return result
