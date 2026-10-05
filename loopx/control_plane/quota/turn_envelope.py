"""Compatibility transport for the TypeScript-owned Turn envelope transaction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import effect_runtime_result
from ..scheduler.execution_context import (
    SchedulerExecutionContextResolution,
    render_scheduler_execution_args,
    resolve_scheduler_execution_context,
)
from ..work_items.interaction_contract import protocol_action_packet_fields

TURN_ENVELOPE_SCHEMA_VERSION = "loopx_turn_envelope_v0"
TURN_ENVELOPE_BUDGET_BYTES = 8_192
CONTRACT_CAPSULE_SCHEMA_VERSION = "loopx_contract_capsule_v0"
ACTION_SIGNATURE_SCHEMA_VERSION = "loopx_action_signature_v0"
ACTION_SIGNATURE_COVERAGE_V0 = "turn_envelope_action_dimensions_v0"
ACTION_SIGNATURE_COVERAGE_V1 = "turn_envelope_action_dimensions_v1"
ACTION_SIGNATURE_COVERAGE_V2 = "turn_envelope_action_dimensions_v2"
ACTION_SIGNATURE_COVERAGE_V3 = "turn_envelope_action_dimensions_v3"
ACTION_SIGNATURE_COVERAGE_V4 = "turn_envelope_action_dimensions_v4"
ACTION_SIGNATURE_COVERAGE_V5 = "turn_envelope_action_dimensions_v5"
ACTION_SIGNATURE_COVERAGE = ACTION_SIGNATURE_COVERAGE_V0
PLANNING_HORIZON_DETAIL_REFS_REF = "$.detail_ref"


def _turn_envelope_result(
    operation: str,
    **params: object,
) -> dict[str, Any]:
    """Call the coarse TS owner and fail closed on transport shape drift."""

    result = effect_runtime_result(
        "quota.turn_envelope.evaluate",
        {"operation": operation, **params},
    )
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript Turn envelope result must be an object")
    return dict(result)


def _protocol_action_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Prepare the separately versioned protocol-packet projection."""

    return protocol_action_packet_fields(dict(payload))


def turn_envelope_action_signature_document(
    envelope: Mapping[str, Any],
) -> dict[str, Any]:
    return _turn_envelope_result(
        "envelope_signature",
        envelope=dict(envelope),
    )


def quota_action_signature_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _turn_envelope_result(
        "quota_signature",
        payload=dict(payload),
        protocol_action_fields=_protocol_action_fields(payload),
    )


def build_turn_envelope(
    payload: Mapping[str, Any],
    *,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
) -> dict[str, Any]:
    """Project one full quota decision through the canonical TS transaction."""

    resolution = resolve_scheduler_execution_context(scheduler_execution_context)
    scheduler_execution_args = ""
    if resolution.ok and resolution.context is not None:
        scheduler_execution_args = render_scheduler_execution_args(
            scheduler_execution_context=resolution,
        )
    return _turn_envelope_result(
        "build",
        payload=dict(payload),
        protocol_action_fields=_protocol_action_fields(payload),
        scheduler_execution_args=scheduler_execution_args,
    )
