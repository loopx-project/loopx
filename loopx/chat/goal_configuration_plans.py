"""Typed request parsing, plan building, and response payload contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, TypeAlias

from ..capabilities.multi_subagent import public_codex_host_capacity
from ..configuration_transaction import (
    build_configuration_update_plan,
    configuration_payload_revision,
)


class GoalConfigurationServerProtocol(Protocol):
    """Minimal HTTP server attributes accessed by Goal configuration handlers."""

    @property
    def registry_path(self) -> Path: ...


GoalConfigurationReader: TypeAlias = Callable[..., dict[str, object]]
GoalConfigurationWriter: TypeAlias = Callable[..., dict[str, object]]


def require_mapping(value: object, label: str) -> dict[str, object]:
    """Require a mapping value and copy it to a string-keyed dict."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is unavailable")
    return {str(key): item for key, item in value.items()}


def capability_entry(payload: Mapping[str, object], capability_id: str) -> dict[str, object]:
    """Locate one unique capability descriptor in a public capability catalog."""
    catalog = require_mapping(payload.get("capability_catalog"), "capability catalog")
    capabilities = catalog.get("capabilities")
    if not isinstance(capabilities, list):
        raise ValueError("capability catalog is invalid")
    matches = [
        item
        for item in capabilities
        if isinstance(item, Mapping) and str(item.get("capability_id") or "") == capability_id
    ]
    if len(matches) != 1:
        raise ValueError(f"unknown Goal capability: {capability_id}")
    return dict(matches[0])


def parse_goal_configuration_update(
    body: Mapping[str, object],
    *,
    execute: bool,
) -> tuple[str, str, Mapping[str, object] | None, str]:
    """Parse and validate a Goal configuration preview or apply request payload."""
    allowed = {"goal_id", "capability_id", "configuration"}
    if execute:
        allowed.add("expected_plan_revision")
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ValueError("goal configuration request contains unknown fields: " + ", ".join(unknown))
    goal_id = str(body.get("goal_id") or "").strip()
    capability_id = str(body.get("capability_id") or "").strip()
    if "configuration" not in body:
        raise ValueError("configuration is required")
    configuration = body.get("configuration")
    if not goal_id or not capability_id:
        raise ValueError("goal_id and capability_id are required")
    if configuration is not None and not isinstance(configuration, Mapping):
        raise TypeError("configuration must be an object or null")
    expected_revision = str(body.get("expected_plan_revision") or "")
    return goal_id, capability_id, configuration, expected_revision


def configuration_revision(value: object) -> str:
    """Compute canonical configuration payload digest or absent sentinel."""
    if isinstance(value, Mapping):
        return configuration_payload_revision({str(key): item for key, item in value.items()})
    return "absent"


def changed_fields_list(payload: Mapping[str, object]) -> list[object]:
    """Extract bounded list of changed field names from configuration result."""
    raw = payload.get("changed_fields")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return list(raw)
    return []


def codex_home_override(server: object) -> Path | None:
    """Read optional codex_home path override from server.runtime_controller."""
    controller = getattr(server, "runtime_controller", None)
    value = getattr(controller, "codex_home", None)
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value:
        return Path(value)
    return None


def runtime_root_override_arg(server: object) -> str | None:
    """Read optional runtime_root_override string from server."""
    value = getattr(server, "runtime_root_override", None)
    if isinstance(value, Path):
        return str(value)
    return value if isinstance(value, str) else None


def goal_configuration_update_plan(
    *,
    goal_id: str,
    capability_id: str,
    current_public: Mapping[str, object],
    desired_public: Mapping[str, object],
    desired_result: Mapping[str, object],
) -> tuple[dict[str, object], object]:
    """Assemble the preview/apply Goal configuration update transaction plan."""
    current_configuration = capability_entry(current_public, capability_id).get("current")
    desired_configuration = capability_entry(desired_public, capability_id).get("current")
    host_capacity = (
        dict(public_codex_host_capacity(dict(desired_result)))
        if capability_id == "multi_subagent"
        else {}
    )
    plan = build_configuration_update_plan(
        schema_version="goal_configuration_update_plan_v0",
        current_present=isinstance(current_configuration, Mapping),
        desired_present=isinstance(desired_configuration, Mapping),
        current_revision=configuration_revision(current_configuration),
        desired_revision=configuration_revision(desired_configuration),
        target_identity={
            "goal_id": goal_id,
            "capability_id": capability_id,
            "base_revision": current_public["revision"],
        },
        changed_units={
            "changed_fields": changed_fields_list(desired_result),
            **({"codex_host_capacity": host_capacity} if host_capacity else {}),
        },
        projected_configuration=(
            {str(key): item for key, item in desired_configuration.items()}
            if isinstance(desired_configuration, Mapping)
            else None
        ),
        projection_field="goal_configuration",
        additional_write_required=bool(host_capacity.get("write_required")),
    )
    return dict(plan), desired_configuration


def build_partial_write_receipt_payload(
    *,
    applied: Mapping[str, object],
    capability_id: str,
    goal_id: str,
    plan: Mapping[str, object],
    readback_public: Mapping[str, object],
    readback_configuration: object,
    readback_verified: bool,
) -> dict[str, object]:
    """Format the 207 partial-write receipt without discarding write provenance."""
    global_sync = applied.get("global_sync")
    raw_readback = global_sync.get("readback") if isinstance(global_sync, Mapping) else None
    global_readback: Mapping[str, object] = raw_readback if isinstance(raw_readback, Mapping) else {}
    host_capacity = dict(public_codex_host_capacity(dict(applied)))
    return {
        "ok": False,
        "schema_version": "goal_configuration_transaction_v0",
        "status": "partial_write",
        "goal_id": goal_id,
        "capability_id": capability_id,
        "plan_revision": plan["plan_revision"],
        "applied_revision": readback_public["revision"] if readback_verified else None,
        "source_written": True,
        "shared_sync_pending": not bool(global_readback.get("verified")),
        "host_capacity_pending": host_capacity.get("status") == "apply_failed",
        "readback_verified": readback_verified,
        "changed_fields": changed_fields_list(applied),
        "goal_configuration": readback_configuration,
        "capability_catalog": readback_public["capability_catalog"],
        "codex_host_capacity": host_capacity,
        "error": str(applied.get("error") or "Goal configuration shared projection did not synchronize"),
        "recommended_action": str(
            applied.get("recommended_action") or f"rerun loopx sync-global --goal-id {goal_id}"
        ),
    }


def build_applied_goal_configuration_payload(
    *,
    applied: Mapping[str, object],
    capability_id: str,
    goal_id: str,
    plan: Mapping[str, object],
    readback_public: Mapping[str, object],
    readback_configuration: object,
) -> dict[str, object]:
    """Format the 200 applied/unchanged Goal configuration transaction payload."""
    return {
        "ok": True,
        "schema_version": "goal_configuration_transaction_v0",
        "status": "applied" if plan["writes_required"] else "unchanged",
        "goal_id": goal_id,
        "capability_id": capability_id,
        "plan_revision": plan["plan_revision"],
        "applied_revision": readback_public["revision"],
        "readback_verified": True,
        "changed_fields": changed_fields_list(applied),
        "goal_configuration": readback_configuration,
        "capability_catalog": readback_public["capability_catalog"],
        "codex_host_capacity": dict(public_codex_host_capacity(dict(applied))),
    }


__all__ = [
    "GoalConfigurationReader",
    "GoalConfigurationServerProtocol",
    "GoalConfigurationWriter",
    "build_applied_goal_configuration_payload",
    "build_partial_write_receipt_payload",
    "capability_entry",
    "changed_fields_list",
    "codex_home_override",
    "configuration_revision",
    "goal_configuration_update_plan",
    "parse_goal_configuration_update",
    "require_mapping",
    "runtime_root_override_arg",
]
