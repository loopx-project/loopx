"""Public-safe validation for automation cadence owner results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

AutomationCadenceUnavailableReason = Literal[
    "agent_scope_required",
    "runtime_root_unavailable",
    "owner_read_failed",
]


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _identity(value: object, name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _eligibility(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("automation cadence eligibility must be an object")
    state = value.get("state")
    reason = value.get("reason")
    eligible_now = value.get("eligible_now")
    next_at = value.get("next_eligible_at_ms")
    if state == "unconfigured":
        if reason != "unconfigured" or eligible_now is not None or next_at is not None:
            raise ValueError("invalid unconfigured automation cadence eligibility")
    elif state == "eligible":
        if reason != "owner_minimum_interval" or eligible_now is not True:
            raise ValueError("invalid eligible automation cadence eligibility")
        if next_at is not None:
            next_at = _integer(
                next_at, "automation cadence eligibility next_eligible_at_ms"
            )
    elif state == "waiting":
        if reason != "minimum_interval_wait" or eligible_now is not False:
            raise ValueError("invalid waiting automation cadence eligibility")
        next_at = _integer(
            next_at, "automation cadence eligibility next_eligible_at_ms"
        )
    elif state == "unavailable":
        if reason not in {
            "agent_scope_required",
            "runtime_root_unavailable",
            "owner_read_failed",
        }:
            raise ValueError("invalid unavailable automation cadence reason")
        if eligible_now is not None or next_at is not None:
            raise ValueError("unavailable automation cadence cannot claim readiness")
    else:
        raise ValueError("unsupported automation cadence eligibility state")
    return {
        "state": state,
        "reason": reason,
        "eligible_now": eligible_now,
        "next_eligible_at_ms": next_at,
    }


def automation_cadence_readback(
    owner_result: Mapping[str, Any],
    *,
    expected_goal_id: str,
    expected_agent_id: str | None,
    expected_automation_id: str | None,
) -> dict[str, Any]:
    """Validate one owner result and retain only cross-surface public facts."""

    if (
        owner_result.get("schema_version") != "automation_cadence_result_v1"
        or owner_result.get("ok") is not True
    ):
        raise ValueError("automation cadence result schema mismatch")
    goal_id = _identity(owner_result.get("goal_id"), "goal_id")
    agent_id = _identity(owner_result.get("agent_id"), "agent_id", optional=True)
    automation_id = _identity(
        owner_result.get("automation_id"), "automation_id", optional=True
    )
    if (
        goal_id != expected_goal_id
        or agent_id != expected_agent_id
        or automation_id != expected_automation_id
    ):
        raise ValueError("automation cadence scope mismatch")
    return {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "automation_id": automation_id,
        "configuration_revision": _integer(
            owner_result.get("configuration_revision"),
            "automation cadence configuration_revision",
        ),
        "min_interval_minutes": _integer(
            owner_result.get("min_interval_minutes"),
            "automation cadence min_interval_minutes",
        ),
        "eligibility": _eligibility(owner_result.get("eligibility")),
    }


def unavailable_automation_cadence_readback(
    *,
    goal_id: str,
    agent_id: str | None,
    automation_id: str | None,
    reason: AutomationCadenceUnavailableReason,
) -> dict[str, Any]:
    """Represent a secondary read failure without turning unknown into ready."""

    return {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "automation_id": automation_id,
        "configuration_revision": None,
        "min_interval_minutes": None,
        "eligibility": {
            "state": "unavailable",
            "reason": reason,
            "eligible_now": None,
            "next_eligible_at_ms": None,
        },
    }
