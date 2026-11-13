"""Compatibility transport for the TypeScript-owned Turn envelope transaction."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, cast
import json
import zlib

from ..effect_runtime import _encode_rpc_json, effect_runtime_projection_result, source_number_tokens
from ..scheduler.execution_context import (
    SchedulerExecutionContextResolution,
    render_scheduler_execution_args,
    resolve_scheduler_execution_context,
)

TURN_ENVELOPE_SCHEMA_VERSION = "loopx_turn_envelope_v0"
COMPLETE_TURN_DECISION_VERSION = "loopx_turn_envelope_v1"
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


def _apply_context_projection_changes(
    source: dict[str, Any], result: dict[str, Any], *, base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply typed display edits to the original Python facts, without policy."""
    if set(result) != {"changes"} or not isinstance(result["changes"], list):
        raise RuntimeError("TypeScript context projection must return changes")
    # JSON transport has no aliasing. Keep Python's exact numeric values while
    # separating repeated caller-owned objects before applying path edits.
    projected = json.loads(json.dumps(source if base is None else base, allow_nan=False))
    try:
        for change in result["changes"]:
            path = change["path"]
            if not isinstance(path, list):
                raise ValueError("projection path must be a list")
            if not path and "remove" in change:
                raise ValueError("projection root cannot be removed")
            parent: Any = projected
            for key in path[:-1]:
                parent = parent[key]
            if "remove" in change:
                del parent[path[-1]]
                continue
            if "from" in change:
                value: Any = source
                for part in change["from"]:
                    value = value[part]
            else:
                value = change["value"]
            if not path:
                if not isinstance(value, dict):
                    raise ValueError("projection root must be an object")
                projected = deepcopy(value)
                continue
            key = path[-1]
            if isinstance(parent, list) and key == len(parent):
                parent.append(deepcopy(value))
            else:
                parent[key] = deepcopy(value)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError("Invalid TypeScript context projection changes") from error
    return cast(dict[str, Any], projected)


def pack_turn_source(source: Mapping[str, Any]) -> dict[str, str] | None:
    """Use the lossless wire for bounded sources that compress well."""
    raw = _encode_rpc_json(source)
    if 16 * 1024 <= len(raw) <= 1024 * 1024:
        data = base64.b64encode(zlib.compress(raw, level=1)).decode("ascii")
        if len(data) + 128 < len(raw):
            return {"encoding": "deflate-base64-json-v0", "data": data}
    return None


def _turn_envelope_result(
    operation: str,
    **params: object,
) -> dict[str, Any]:
    """Call the coarse TS owner and fail closed on transport shape drift."""

    request = {"operation": operation, **params}
    payload = params.get("payload")
    complete = isinstance(payload, Mapping) and payload.get("schema_version") == COMPLETE_TURN_DECISION_VERSION
    staged_replan = (
        operation in {"build", "quota_signature", "replan_cli_view", "bind_capture",
                      "capability_automation_source"}
        and isinstance(payload, Mapping)
        and ((operation == "replan_cli_view" and params.get("source_edits") is True)
             or (payload.get("replan_action_packet") and (payload.get("replan_read_path") or params.get("reader_argv"))))
    )
    token_source = payload if isinstance(payload, Mapping) else params
    if complete or staged_replan or operation in {"replan_view", "reference_context",
            "compose_complete", "context_delivery", "context_recovery",
            "progressive_context_delivery", "context_read_sections"}:
        tokens = source_number_tokens(token_source)
        if tokens:
            request["source_number_tokens"] = tokens
    if operation == "capability_automation_source" and set(params) == {"payload"} and isinstance(payload, Mapping):
        transport = pack_turn_source(payload)
        if transport is not None:
            del request["payload"]
            request["source_transport"] = transport
    result = effect_runtime_projection_result("quota.turn_envelope.evaluate", request)
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript Turn envelope result must be an object")
    if operation == "replan_view" and "source_number_tokens" in request:
        return _apply_context_projection_changes(cast(dict[str, Any], payload), dict(result), base={})
    return dict(result)


LOOPX_TURN_PLAN_SCHEMA_VERSION = "loopx_turn_plan_v0"
COMPLETE_TURN_PLAN_SCHEMA_VERSION = "loopx_turn_plan_v1"
LOOPX_TURN_HOST_REQUEST_SCHEMA = "loopx_turn_host_request_v0"
COMPLETE_TURN_HOST_REQUEST_SCHEMA = "loopx_turn_host_request_v1"


def _wire_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def turn_plan_host_source(request: Mapping[str, Any]) -> Mapping[str, Any]:
    # Result validation and completion also read the private plan. Its schema
    # is not a model wire version; adapt only its existing source carriers.
    if request.get("schema_version") == COMPLETE_TURN_PLAN_SCHEMA_VERSION:
        if "work_contract" not in request or "turn_envelope" in request or "source_decision_hash" not in request:
            raise ValueError("complete Turn plan requires one source without an executor adapter")
        return {"schema_version": "loopx_turn_host_request_v1",
                "turn_key": _wire_mapping(request.get("transaction")).get("turn_key"),
                **{key: request[key] for key in ("work_contract", "source_decision_hash", "detail_ref")
                   if key in request}}
    return ({key: request[key] for key in ("work_contract", "turn_envelope") if key in request}
            if request.get("schema_version") == LOOPX_TURN_PLAN_SCHEMA_VERSION else request)


def captured_turn_host_input(request: Mapping[str, Any]) -> dict[str, Any]:
    """Transport original-reader metadata after complete-source validation."""
    envelope = _wire_mapping(request.get("turn_envelope"))
    model_input = {**{key: value for key, value in request.items() if key != "turn_envelope"},
                   "schema_version": COMPLETE_TURN_HOST_REQUEST_SCHEMA}
    detail = _wire_mapping(envelope.get("detail_ref"))
    captured = _wire_mapping(detail.get("captured_decision"))
    if captured:
        model_input["source_decision_hash"] = _wire_mapping(envelope.get("action_signature"))["source_decision_hash"]
        model_input["detail_ref"] = {
            "full_decision": detail["full_decision"],
            "captured_decision": {**captured, "source_hash_ref": "$.source_decision_hash"},
        }
        readers = _wire_mapping(detail.get("replan_action_packet"))
        if readers.get("review"):
            model_input["detail_ref"]["replan_action_packet"] = {
                key: value for key, value in readers.items() if key in {"review", "writeback"}
            }
    return model_input


def assert_turn_host_request_version(request: Mapping[str, Any]) -> None:
    """Decode the two published Host transports; neither grants authority."""
    version = request.get("schema_version")
    if version not in (LOOPX_TURN_HOST_REQUEST_SCHEMA, COMPLETE_TURN_HOST_REQUEST_SCHEMA):
        raise ValueError("unsupported LoopX Turn host request schema")
    if version == COMPLETE_TURN_HOST_REQUEST_SCHEMA and (
        "work_contract" not in request or "turn_envelope" in request
    ):
        raise ValueError("complete Host request requires work_contract without an executor adapter")


def complete_turn_host_source(
    request: Mapping[str, Any], *, operation: str = "host_context",
    include_session_lineage: bool = False,
    include_model_context: bool = False,
    recorded_capability_result: bool = False,
) -> dict[str, Any] | None:
    if "schema_version" in request:
        assert_turn_host_request_version(request)
    if "work_contract" not in request:
        return None
    header = ({"complete_host_request": {key: request[key] for key in (
        "schema_version", "turn_key", "source_decision_hash", "detail_ref",
    ) if key in request}} if request.get("schema_version") == COMPLETE_TURN_HOST_REQUEST_SCHEMA else {})
    # Identity-only current callers need no executor adapter. Keep an explicit
    # adapter, even a damaged one, on the typed owner's full Host validation.
    adapter = ({"envelope": _wire_mapping(request.get("turn_envelope"))}
               if "turn_envelope" in request or operation != "complete_host_lineage" else {})
    checked = _turn_envelope_result(operation, payload=request["work_contract"], **adapter, **header,
                                 **({"recorded_capability_result": True} if recorded_capability_result else {}),
                                 **({"include_model_context": True} if include_model_context else {}),
                                 **({"include_session_lineage": True} if include_session_lineage else {}))
    if include_model_context:
        changes = checked.pop("model_context_changes", None)
        if not isinstance(changes, dict):
            raise RuntimeError("TypeScript Host model-context projection is missing")
        checked["model_context"] = _apply_context_projection_changes(
            dict(request["work_contract"]), changes,
        )
    return checked


def projected_host_model_input(request: Mapping[str, Any], checked: Mapping[str, Any]) -> dict[str, Any]:
    """Render task data with original-reader provenance, never a new source.

    The full v1 wire/journal remains unchanged. Applying the TS owner's path
    edits above preserves original Python numbers and complete task text.
    """
    context = checked.get("model_context")
    if not isinstance(context, dict):
        raise RuntimeError("checked Host model-context projection is missing")
    original = captured_turn_host_input(request)
    return {**{key: value for key, value in original.items()
               if key not in {"work_contract", "schema_version"}},
            "schema_version": checked["model_context_schema_version"],
            "source_schema_version": original["schema_version"], "task_context": context}


def selected_turn_todo(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Transport historical selection to the shared work/settlement owner.

    Only Python's original scalar codec remains here. Pure source edits retain
    unknown fields and exact numbers without another JSON value decoder.
    """
    orchestration = _wire_mapping(envelope.get("task_orchestration_contract"))
    source = {"selected_todo": _wire_mapping(_wire_mapping(envelope.get("action")).get("selected_todo")),
              "task_orchestration_contract": orchestration}
    edits = _turn_envelope_result("selected_work_todo", payload=source,
        historical_primary_text=str(orchestration.get("primary_todo_id") or "").strip())
    selected: dict[str, Any] = _apply_context_projection_changes(source, edits)["selected_todo"]
    return selected


def read_turn_plan_work(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Read checked execution facts; current plans never regenerate a v0 view."""
    if plan.get("schema_version") == COMPLETE_TURN_PLAN_SCHEMA_VERSION or "work_contract" in plan:
        result = complete_turn_host_source(turn_plan_host_source(plan), operation="plan_work")
        assert result is not None
        return result
    if plan.get("schema_version") not in (None, LOOPX_TURN_PLAN_SCHEMA_VERSION):
        raise ValueError("unsupported Turn plan version")
    return _wire_mapping(plan.get("turn_envelope"))


def read_recorded_turn_plan_lineage(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Check a recorded plan with the same source reader as journal inspection."""
    tokens = source_number_tokens(plan.get("work_contract"))
    return _turn_envelope_result("recorded_plan_lineage", plan=plan,
        **({"source_number_tokens": tokens} if tokens else {}))


def turn_host_lineage(request: Mapping[str, Any]) -> dict[str, str]:
    """Read identity from the complete owner, with historical request decoding."""
    checked = complete_turn_host_source(turn_plan_host_source(request), operation="complete_host_lineage")
    if checked is not None:
        return checked
    envelope = _wire_mapping(request.get("turn_envelope"))
    todo = selected_turn_todo(envelope)
    return {
        "goal_id": str(envelope.get("goal_id") or "").strip(),
        "agent_id": str(envelope.get("agent_id") or "").strip(),
        "todo_id": str(todo.get("todo_id") or "").strip(),
    }


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


def quota_capability_automation_source(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read checked capability gates before the native quota source is sealed."""
    return _turn_envelope_result("capability_automation_source", payload=dict(payload))


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
    capability_context: Sequence[Mapping[str, Any]] = (),
    share_inline_diagnostics: bool = False,
) -> dict[str, Any]:
    """Transport complete source facts to the existing typed composition owner."""
    source, _ = _compose_turn_source(payload,
        scheduler_execution_context=scheduler_execution_context, capability_context=capability_context,
        share_inline_diagnostics=share_inline_diagnostics)
    return source


def compose_turn_decision_with_raw_envelope(
    payload: Mapping[str, Any], *, scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    captured_decision_path: str | None,
    capability_context: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return the complete capture and its bounded compatibility view in one call."""
    return _compose_turn_source(payload,
        scheduler_execution_context=scheduler_execution_context, capability_context=capability_context,
        legacy_output={"include_legacy_envelope": True,
                       **({"captured_decision_path": captured_decision_path}
                          if captured_decision_path is not None else {})})


def _compose_turn_source(
    payload: Mapping[str, Any], *, scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    legacy_output: Mapping[str, Any] | None = None,
    capability_context: Sequence[Mapping[str, Any]] = (),
    share_inline_diagnostics: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source, request = _complete_composition_input(payload, scheduler_execution_context, capability_context)
    result = _turn_envelope_result("compose_complete", payload=source, source_edits=True,
        **request, **(legacy_output or {}),
        **({"share_inline_diagnostics": True} if share_inline_diagnostics else {}))
    view = result.pop("legacy_envelope", None) if legacy_output is not None else None
    return _apply_context_projection_changes(source, result), view if isinstance(view, dict) else None


def _complete_composition_input(
    payload: Mapping[str, Any],
    scheduler_execution_context: Mapping[str, object] | SchedulerExecutionContextResolution | None,
    capability_context: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolution = resolve_scheduler_execution_context(scheduler_execution_context)
    args = render_scheduler_execution_args(scheduler_execution_context=resolution) if resolution.ok and resolution.context is not None else ""
    source = dict(payload)
    if capability_context:
        if "turn_context_contributions" in source:
            raise ValueError("New capability context cannot replace recorded contributions")
        source["turn_context_contributions"] = list(capability_context)
    return source, {"scheduler_execution_args": args,
                    **({"embed_in_complete_source": True} if capability_context else {})}


def compose_host_todo_settlement_guard(
    payload: Mapping[str, Any], *,
    scheduler_execution_context: Mapping[str, object] | SchedulerExecutionContextResolution | None,
    capability_context: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compose the current decision and return its compact MCP observation."""
    source, request = _complete_composition_input(payload, scheduler_execution_context, capability_context)
    return _turn_envelope_result("compose_complete", payload=source, host_todo_settlement=True, **request)


def is_legacy_saved_quota_decision(payload: Mapping[str, Any]) -> bool:
    """Recognize saved raw/v0 carriers that predate complete-source checking."""
    return (
        "contract_binding" not in payload
        and payload.get("schema_version", TURN_ENVELOPE_SCHEMA_VERSION) == TURN_ENVELOPE_SCHEMA_VERSION
    )


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
    if scheduler_execution_context is None and payload.get("schema_version") == COMPLETE_TURN_DECISION_VERSION:
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


def project_captured_replan_cli_view(
    payload: dict[str, Any], *, captured_decision_path: str | None = None,
) -> dict[str, Any]:
    """Stage replan reads against the verified capture without rewriting its source."""
    if not isinstance(payload.get("replan_read_path"), str):
        return payload
    result = _turn_envelope_result("replan_cli_view", payload=payload, source_edits=True,
        **({"captured_decision_path": captured_decision_path} if captured_decision_path is not None else {}))
    return _apply_context_projection_changes(payload, result)


def prepare_context_delivery(
    context: Mapping[str, Any], *, scope: Mapping[str, str], capture_id: str,
    baseline: Mapping[str, Any] | None = None,
    retained: Mapping[str, Any] | None = None,
    continuity: Mapping[str, Any] | None = None,
    surface: str = "work_context", native_projection: Mapping[str, Any] | None = None,
    consumed_pointers: Mapping[str, str] | None = None, observe_first_read: bool = False,
    include_initial_read_plan: bool = False,
) -> dict[str, Any]:
    """Prepare under the Host's explicit continuity basis, not a model assertion.

    Quota and Turn hosts pass their complete immutable projection (or the shared
    work_context) and its capture id. Surface binding prevents cross-projection reuse.
    Unsupported hosts omit retention and keep complete delivery. A candidate is
    not an ACK. Confirmed input and native delivery assumptions stay distinct.
    """
    source: dict[str, Any] = dict(context=dict(context), scope=dict(scope), capture_id=capture_id, surface=surface,
        baseline=dict(baseline) if baseline is not None else None,
        retained=dict(retained) if retained is not None else None,
        continuity=dict(continuity) if continuity is not None else None)
    if native_projection is not None:
        source["native_projection"] = dict(native_projection)
        if include_initial_read_plan:
            source["include_initial_read_plan"] = True
    if consumed_pointers is not None:
        source["consumed_pointers"] = dict(consumed_pointers)
        if consumed_pointers and observe_first_read:
            source["observe_first_read"] = True
    result = _turn_envelope_result("context_delivery", **source, source_edits=True)
    transaction = _apply_context_projection_changes(source, result)
    if native_projection is not None:
        delivery = transaction["delivery"]
        if delivery["mode"] == "full" and "context" not in delivery:
            # Preserve the separately owned delivery body for callers.
            delivery["context"] = deepcopy(transaction["context"])
    return transaction


def prepare_progressive_context(context: Mapping[str, Any], *, source_reference: Mapping[str, Any],
                                surface: str = "quota_packet") -> dict[str, Any]:
    """Stateless IO adapter to the same typed presentation owner; no Delta identity."""
    source = dict(context=dict(context), native_projection=dict(source_reference), surface=surface)
    return _apply_context_projection_changes(source,
        _turn_envelope_result("progressive_context_delivery", **source, source_edits=True))


def update_native_context_lifecycle(state: Mapping[str, Any], event: Mapping[str, Any], *,
                                    new_epoch: str, pages: list[dict[str, Any]],
                                    pages_verified: bool = False,
                                    resume_verified: bool = False,
                                    resume_sections: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Native IO adapter; the shared typed owner reduces continuity assumptions."""
    return _turn_envelope_result("native_context_lifecycle", state=dict(state),
        event=dict(event), new_epoch=new_epoch, pages=pages, pages_verified=pages_verified,
        resume_verified=resume_verified, **({"resume_sections": dict(resume_sections)} if resume_sections else {}))


def restore_context_delivery(
    delivery: Mapping[str, Any], *, baseline: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate/reconstruct or return the same-capture full body without effects."""
    source = dict(delivery=dict(delivery), baseline=dict(baseline) if baseline is not None else None,
        current=dict(current))
    result = _turn_envelope_result("context_recovery", **source, source_edits=True)
    return _apply_context_projection_changes(source, result)
