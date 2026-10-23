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


def reject_retired_quota_format(payload: Mapping[str, Any]) -> None:
    """Route retired carriers to the typed rejection; modern rendering adds no RPC."""
    if "protocol_action_packet" in payload:
        quota_action_signature_document(payload)
    else:
        capsule = payload.get("contract_capsule")
        if isinstance(capsule, Mapping) and "protocol_action_packet" in capsule:
            turn_envelope_action_signature_document(payload)


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
    )


def turn_envelope_replan_settlement_identity(
    envelope: Mapping[str, Any], *, turn_instance_id: str | None,
) -> dict[str, Any] | None:
    result = _turn_envelope_result("replan_settlement_identity", envelope=dict(envelope),
                                   turn_instance_id=turn_instance_id)
    identity = result.get("identity")
    if identity is None:
        return None
    if not isinstance(identity, Mapping):
        raise RuntimeError("TypeScript Turn settlement identity must be an object")
    return dict(identity)


def build_turn_envelope(
    payload: Mapping[str, Any],
    *,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    captured_decision_path: str | None = None,
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
        scheduler_execution_args=scheduler_execution_args,
        **({"captured_decision_path": captured_decision_path}
           if captured_decision_path is not None else {}),
    )


def bind_turn_decision_capture(
    payload: Mapping[str, Any], envelope: Mapping[str, Any], *,
    captured_decision_path: str, turn_instance_id: str, turn_key: str,
) -> dict[str, Any]:
    """Validate and bind original native Turn evidence through the existing TS owner."""
    return _turn_envelope_result(
        "bind_capture", payload=dict(payload), envelope=dict(envelope),
        captured_decision_path=captured_decision_path,
        turn_instance_id=turn_instance_id, turn_key=turn_key,
    )
