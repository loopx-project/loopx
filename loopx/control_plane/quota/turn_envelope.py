"""Compatibility transport for the TypeScript-owned Turn envelope transaction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import effect_runtime_projection_result, source_number_tokens
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

    request = {"operation": operation, **params}
    payload = params.get("payload")
    complete = isinstance(payload, Mapping) and payload.get("schema_version") == "loopx_turn_envelope_v1"
    token_source = payload if isinstance(payload, Mapping) else params
    if complete or operation in {"reference_context", "reference_replan_authoring",
            "compose_complete", "context_delivery", "context_recovery",
            "progressive_context_delivery", "context_read_sections"}:
        tokens = source_number_tokens(token_source)
        if tokens:
            request["source_number_tokens"] = tokens
    result = effect_runtime_projection_result("quota.turn_envelope.evaluate", request)
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript Turn envelope result must be an object")
    return dict(result)



def assert_turn_envelope_consumer_version(envelope: Mapping[str, Any]) -> None:
    """Use the typed wire-version gate before any Host consumes this packet."""
    _turn_envelope_result("consumer_version", envelope=dict(envelope))


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


def compose_turn_decision(
    payload: Mapping[str, Any], *, scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    composed_settlement: bool = False,
) -> dict[str, Any]:
    """Transport complete source facts to the existing typed composition owner."""
    resolution = resolve_scheduler_execution_context(scheduler_execution_context)
    args = render_scheduler_execution_args(scheduler_execution_context=resolution) if resolution.ok and resolution.context is not None else ""
    from .cli_projection import _apply_context_projection_changes

    source = dict(payload)
    result = _turn_envelope_result("compose_complete", payload=source,
        scheduler_execution_args=args, source_edits=True,
        **({"composed_settlement": True} if composed_settlement else {}))
    return _apply_context_projection_changes(source, result)


def assert_complete_turn_decision(payload: Mapping[str, Any]) -> None:
    """Validate the original contract; keep its host binding and caller-owned bytes."""
    _turn_envelope_result("complete_consumer", payload=dict(payload))


def turn_envelope_scoped_settlement_identity(
    envelope: Mapping[str, Any], *, turn_instance_id: str | None,
) -> dict[str, Any] | None:
    identity = _turn_envelope_result("scoped_settlement_identity", envelope=dict(envelope),
                                    turn_instance_id=turn_instance_id).get("identity")
    if identity is not None and not isinstance(identity, Mapping):
        raise RuntimeError("TypeScript Turn settlement identity must be an object")
    return dict(identity) if identity is not None else None


def build_turn_envelope(
    payload: Mapping[str, Any],
    *,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    captured_decision_path: str | None = None,
) -> dict[str, Any]:
    """Project one full quota decision through the canonical TS transaction."""

    return _build_turn_projection(payload,
        scheduler_execution_context=scheduler_execution_context,
        captured_decision_path=captured_decision_path)


def _build_turn_projection(
    payload: Mapping[str, Any], *, scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    captured_decision_path: str | None = None,
) -> dict[str, Any]:

    resolution = resolve_scheduler_execution_context(scheduler_execution_context)
    scheduler_execution_args: object = ""
    if resolution.ok and resolution.context is not None:
        scheduler_execution_args = render_scheduler_execution_args(
            scheduler_execution_context=resolution,
        )
    # A captured complete contract already records the original host binding.
    # Only transport that value when no caller override was supplied; TS checks
    # its integrity and rejects an explicit conflicting binding.
    if scheduler_execution_context is None and payload.get("schema_version") == "loopx_turn_envelope_v1":
        binding = payload.get("contract_binding")
        scheduler_execution_args = binding.get("scheduler_execution_args") if isinstance(binding, Mapping) else None
    return _turn_envelope_result(
        "build",
        payload=dict(payload),
        scheduler_execution_args=scheduler_execution_args,
        **({"captured_decision_path": captured_decision_path}
           if captured_decision_path is not None else {}),
    )


def bind_turn_decision_capture(
    payload: Mapping[str, Any], envelope: Mapping[str, Any] | None = None, *,
    captured_decision_path: str, turn_instance_id: str, turn_key: str,
    reader_argv: list[str] | None = None,
    complete_host_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and bind original native Turn evidence through the existing TS owner."""
    return _turn_envelope_result(
        "bind_capture", payload=dict(payload),
        **({"complete_host_request": dict(complete_host_request)} if complete_host_request is not None
           else {"envelope": dict(envelope or {})}),
        captured_decision_path=captured_decision_path,
        turn_instance_id=turn_instance_id, turn_key=turn_key,
        **({"reader_argv": reader_argv} if reader_argv is not None else {}),
    )


def prepare_context_delivery(
    context: Mapping[str, Any], *, scope: Mapping[str, str], capture_id: str,
    baseline: Mapping[str, Any] | None = None,
    retained: Mapping[str, Any] | None = None,
    continuity: Mapping[str, Any] | None = None,
    surface: str = "work_context", native_projection: Mapping[str, Any] | None = None,
    consumed_pointers: Mapping[str, str] | None = None,
    include_initial_read_plan: bool = False,
) -> dict[str, Any]:
    """Prepare under the Host's explicit continuity basis, not a model assertion.

    Quota and Turn hosts pass their complete immutable projection (or the shared
    work_context) and its capture id. Surface binding prevents cross-projection reuse.
    Unsupported hosts omit retention and keep complete delivery. A candidate is
    not an ACK. Confirmed input and native delivery assumptions stay distinct.
    """
    from .cli_projection import _apply_context_projection_changes

    source = dict(context=dict(context), scope=dict(scope), capture_id=capture_id, surface=surface,
        baseline=dict(baseline) if baseline is not None else None,
        retained=dict(retained) if retained is not None else None,
        continuity=dict(continuity) if continuity is not None else None)
    if native_projection is not None:
        source["native_projection"] = dict(native_projection)
        if include_initial_read_plan:
            source["include_initial_read_plan"] = True
    if consumed_pointers is not None:
        source["consumed_pointers"] = dict(consumed_pointers)
    result = _turn_envelope_result("context_delivery", **source, source_edits=True)
    return _apply_context_projection_changes(source, result)


def prepare_progressive_context(context: Mapping[str, Any], *, source_reference: Mapping[str, Any],
                                surface: str = "quota_packet") -> dict[str, Any]:
    """Stateless IO adapter to the same typed presentation owner; no Delta identity."""
    from .cli_projection import _apply_context_projection_changes

    source = dict(context=dict(context), native_projection=dict(source_reference), surface=surface)
    return _apply_context_projection_changes(source,
        _turn_envelope_result("progressive_context_delivery", **source, source_edits=True))


def update_native_context_lifecycle(state: Mapping[str, Any], event: Mapping[str, Any], *,
                                    new_epoch: str, pages: list[dict[str, Any]],
                                    pages_verified: bool = False,
                                    resume_verified: bool = False) -> dict[str, Any]:
    """Native IO adapter; the shared typed owner reduces continuity assumptions."""
    return _turn_envelope_result("native_context_lifecycle", state=dict(state),
        event=dict(event), new_epoch=new_epoch, pages=pages, pages_verified=pages_verified,
        resume_verified=resume_verified)


def restore_context_delivery(
    delivery: Mapping[str, Any], *, baseline: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate/reconstruct or return the same-capture full body without effects."""
    from .cli_projection import _apply_context_projection_changes

    source = dict(delivery=dict(delivery), baseline=dict(baseline) if baseline is not None else None,
        current=dict(current))
    result = _turn_envelope_result("context_recovery", **source, source_edits=True)
    return _apply_context_projection_changes(source, result)
