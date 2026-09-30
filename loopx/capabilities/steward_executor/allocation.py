"""Typed persisted allocation for one steward Chat Session."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...reasoning_effort import REASONING_EFFORTS
from .machine_defaults import (
    FLEXIBLE_SELECTION_POLICY,
    STEWARD_SELECTION_POLICIES,
)

MANAGER_EXECUTOR_ALLOCATION_SCHEMA_VERSION = "manager_executor_allocation_v0"
MANAGER_ALLOCATION_REASON_USER_EXPLICIT = "user_explicit"
MANAGER_ALLOCATION_REASON_PINNED = "pinned_configuration"
MANAGER_ALLOCATION_REASON_CONFIGURED_PREFERENCE = "configured_preference"
MANAGER_ALLOCATION_REASON_PRODUCT_DEFAULT = "product_default"
MANAGER_ALLOCATION_REASON_SERVICE_OVERRIDE = "service_override"
MANAGER_ALLOCATION_REASON_FLEXIBLE_PRIMARY = "flexible_primary_available"
MANAGER_ALLOCATION_REASON_FLEXIBLE_FALLBACK = "flexible_availability_fallback"
MANAGER_ALLOCATION_REASON_FLEXIBLE_UNAVAILABLE = "flexible_pool_unavailable"
MANAGER_ALLOCATION_REASONS = frozenset(
    {
        MANAGER_ALLOCATION_REASON_USER_EXPLICIT,
        MANAGER_ALLOCATION_REASON_PINNED,
        MANAGER_ALLOCATION_REASON_CONFIGURED_PREFERENCE,
        MANAGER_ALLOCATION_REASON_PRODUCT_DEFAULT,
        MANAGER_ALLOCATION_REASON_SERVICE_OVERRIDE,
        MANAGER_ALLOCATION_REASON_FLEXIBLE_PRIMARY,
        MANAGER_ALLOCATION_REASON_FLEXIBLE_FALLBACK,
        MANAGER_ALLOCATION_REASON_FLEXIBLE_UNAVAILABLE,
    }
)

_FIELDS = frozenset(
    {
        "schema_version",
        "selection_policy",
        "allocation_reason",
        "executor_endpoint",
        "executor_endpoint_source",
        "executor_endpoint_default_reason",
        "configured_endpoint",
        "eligible_endpoints",
        "configuration_revision",
        "available",
        "model",
        "model_source",
        "reasoning_effort",
    }
)
# These are the machine inputs accepted with this allocation, not new defaults.
# A namespace revision also changes for policy edits; its hash cannot recover
# whether an unset model/effort later became an explicit selection or was cleared.
# Older persisted allocations lack this historical fact and remain readable.
_CONFIGURED_FIELDS = frozenset({"configured_model", "configured_reasoning_effort"})
_NONEMPTY_TEXT_FIELDS = (
    "allocation_reason",
    "executor_endpoint",
    "executor_endpoint_source",
    "model",
    "model_source",
    "reasoning_effort",
)


def normalize_manager_executor_allocation(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the safe, restart-stable allocation stored on a Session."""

    unknown = sorted(set(raw) - _FIELDS - _CONFIGURED_FIELDS)
    missing = sorted(_FIELDS - set(raw))
    if unknown:
        raise ValueError(
            "unsupported manager executor allocation fields: " + ", ".join(unknown)
        )
    if missing:
        raise ValueError(
            "missing manager executor allocation fields: " + ", ".join(missing)
        )
    if raw.get("schema_version") != MANAGER_EXECUTOR_ALLOCATION_SCHEMA_VERSION:
        raise ValueError(
            "manager_executor_allocation must use "
            f"{MANAGER_EXECUTOR_ALLOCATION_SCHEMA_VERSION}"
        )

    normalized = dict(raw)
    configured_fields = set(raw) & _CONFIGURED_FIELDS
    if configured_fields and configured_fields != _CONFIGURED_FIELDS:
        raise ValueError("manager_executor_allocation must capture both configured model fields")
    for field in configured_fields:
        value = raw[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"manager_executor_allocation.{field} must be null or non-empty")
        normalized[field] = value.strip() if isinstance(value, str) else None
    if (normalized.get("configured_reasoning_effort") is not None
            and normalized["configured_reasoning_effort"] not in REASONING_EFFORTS):
        raise ValueError("manager_executor_allocation.configured_reasoning_effort is unsupported")
    for field in _NONEMPTY_TEXT_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"manager_executor_allocation.{field} must be non-empty")
        normalized[field] = value.strip()
    for field in (
        "executor_endpoint_default_reason",
        "configuration_revision",
    ):
        value = raw.get(field)
        if not isinstance(value, str):
            raise TypeError(f"manager_executor_allocation.{field} must be text")
        normalized[field] = value.strip()

    configured = raw.get("configured_endpoint")
    if configured is not None and (
        not isinstance(configured, str) or not configured.strip()
    ):
        raise ValueError(
            "manager_executor_allocation.configured_endpoint must be null or non-empty"
        )
    normalized["configured_endpoint"] = (
        configured.strip() if isinstance(configured, str) else None
    )

    policy = str(raw.get("selection_policy") or "")
    if policy not in STEWARD_SELECTION_POLICIES:
        raise ValueError(
            "manager_executor_allocation.selection_policy must be preferred, pinned, or flexible"
        )
    reason = str(raw.get("allocation_reason") or "")
    if reason not in MANAGER_ALLOCATION_REASONS:
        raise ValueError("manager_executor_allocation.allocation_reason is unsupported")
    effort = str(raw.get("reasoning_effort") or "")
    if effort not in REASONING_EFFORTS:
        raise ValueError("manager_executor_allocation.reasoning_effort is unsupported")

    eligible = raw.get("eligible_endpoints")
    if not isinstance(eligible, list) or any(
        not isinstance(item, str) or not item.strip() for item in eligible
    ):
        raise TypeError(
            "manager_executor_allocation.eligible_endpoints must be a text list"
        )
    normalized_eligible = [item.strip() for item in eligible]
    if len(set(normalized_eligible)) != len(normalized_eligible):
        raise ValueError(
            "manager_executor_allocation.eligible_endpoints must not contain duplicates"
        )
    if policy == FLEXIBLE_SELECTION_POLICY:
        if normalized["executor_endpoint"] not in normalized_eligible:
            raise ValueError(
                "manager_executor_allocation.executor_endpoint must belong to the flexible pool"
            )
    elif normalized_eligible:
        raise ValueError(
            "manager_executor_allocation.eligible_endpoints is only valid for flexible selection"
        )
    normalized["eligible_endpoints"] = normalized_eligible

    available = raw.get("available")
    if available is not None and not isinstance(available, bool):
        raise TypeError("manager_executor_allocation.available must be boolean or null")
    return normalized


def manager_executor_model(
    raw: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """Project the provider model fields from one persisted allocation."""

    if raw is None:
        return None
    allocation = normalize_manager_executor_allocation(raw)
    return {
        "model": str(allocation["model"]),
        "reasoning_effort": str(allocation["reasoning_effort"]),
    }


def manager_executor_session_fields(
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Persist a validated allocation without leaking its shape into runtime code."""

    return (
        {"manager_executor_allocation": normalize_manager_executor_allocation(raw)}
        if raw is not None
        else {}
    )


def restored_executor_model(session: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Restore the model binding for manager and legacy LoopX-tool Sessions."""

    allocation = session.get("manager_executor_allocation")
    if isinstance(allocation, Mapping):
        return manager_executor_model(allocation)
    return session.get("loopx_executor") if session.get("loopx_tools") else None
