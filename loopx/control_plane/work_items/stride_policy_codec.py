"""Configuration transport codec; stride decisions belong to the TS component.

The root package asset is shared with TypeScript and already included in wheels.
Reading these lexical options does not start Node or introduce a Python policy.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any


# The only live stride strategy. There is no live switch: Goal profiles,
# machine defaults and benchmark arms cannot select "off" for new decisions.
LIVE_STRIDE_STRATEGY = "evidence_conditioned_v1"
_LEGACY_CADENCE_V0_V1_SCHEMAS = frozenset(
    {
        "todo_replan_cadence_machine_defaults_v0",
        "todo_replan_cadence_machine_defaults_v1",
    }
)


@lru_cache(maxsize=1)
def stride_strategy_options() -> tuple[str, ...]:
    """Historically recorded strategy values, accepted only when decoding stored data."""
    contract = json.loads(files("loopx.control_plane").joinpath("stride_strategy_schema.json").read_text(encoding="utf-8"))
    return tuple(contract["enum"])


def normalize_stride_strategy(value: Any) -> str:
    """Validate a historically recorded strategy; live decisions use LIVE_STRIDE_STRATEGY."""
    if not isinstance(value, str) or value not in stride_strategy_options():
        raise ValueError("stride_strategy must be off or evidence_conditioned_v1")
    return value


def legacy_pre_upgrade_stride_strategy(
    raw_goal: Mapping[str, Any] | None,
    runtime_root: Path | None,
) -> str:
    """Inspect un-stripped Goal/machine config to preserve pre-upgrade `off` Turns."""
    profile = raw_goal.get("execution_profile") if isinstance(raw_goal, Mapping) else None
    if isinstance(profile, Mapping):
        if "stride_strategy" in profile:
            return normalize_stride_strategy(profile["stride_strategy"])
        if (
            "replan_after_completed_todos" in profile
            or "replan_after_effective_turns" in profile
        ):
            # Matches legacy inheritance where an explicit cadence override without
            # `stride_strategy` did not inherit the device default stride strategy.
            return "off"
    if runtime_root is not None:
        config_path = Path(runtime_root).expanduser() / "machine" / "configuration.json"
        if config_path.is_file():
            stored = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(stored, Mapping):
                raise ValueError("machine_configuration must be an object")
            namespaces = stored.get("namespaces")
            raw_ns = (
                namespaces.get("todo_replan_cadence")
                if isinstance(namespaces, Mapping)
                else None
            )
            if isinstance(raw_ns, Mapping):
                if "stride_strategy" in raw_ns:
                    return normalize_stride_strategy(raw_ns["stride_strategy"])
                if raw_ns.get("schema_version") in _LEGACY_CADENCE_V0_V1_SCHEMAS:
                    return "off"
    return LIVE_STRIDE_STRATEGY


def resolve_receipt_bound_stride_strategy(
    settlement_replan_guard: Mapping[str, Any] | None,
    *,
    raw_goal: Mapping[str, Any] | None,
    runtime_root: Path | None,
) -> str:
    """Return the stride strategy governing one receipt-bound replan Turn."""
    if (
        isinstance(settlement_replan_guard, Mapping)
        and settlement_replan_guard.get("scope") == "turn_guard"
        and settlement_replan_guard.get("selected_obligation_id")
    ):
        admitted = settlement_replan_guard.get("admitted_stride_strategy")
        if isinstance(admitted, str):
            return normalize_stride_strategy(admitted)
        return legacy_pre_upgrade_stride_strategy(raw_goal, runtime_root)
    return LIVE_STRIDE_STRATEGY
