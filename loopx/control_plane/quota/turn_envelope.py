"""Compatibility transport for the TypeScript-owned Turn envelope transaction."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from copy import deepcopy
import json
from typing import Any, cast
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


def pack_turn_source(
    source: Mapping[str, Any], *, max_encoded_fraction: float = 1.0,
) -> dict[str, str] | None:
    """Use the lossless wire for bounded sources that compress well."""
    raw = _encode_rpc_json(source)
    if 16 * 1024 <= len(raw) <= 16 * 1024 * 1024:
        data = base64.b64encode(zlib.compress(raw, level=1)).decode("ascii")
        if len(data) <= 2 * 1024 * 1024 and len(data) + 128 < len(raw) * max_encoded_fraction:
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
        and (
            (operation == "replan_cli_view" and params.get("source_edits") is True)
            or (payload.get("replan_action_packet") and (payload.get("replan_read_path") or params.get("reader_argv")))
        )
    )
    if complete or operation == "compose_complete" or operation == "replan_view" or staged_replan or operation == "reference_context":
        tokens = source_number_tokens(payload)
        if tokens:
            request["source_number_tokens"] = tokens
    elif operation in {"context_delivery", "context_recovery", "progressive_context_delivery", "context_read_sections"}:
        tokens = source_number_tokens(params)
        if tokens:
            request["source_number_tokens"] = tokens
    if operation == "attach_complete_turn_context" and isinstance(params.get("context_additions"), Mapping):
        addition_tokens = source_number_tokens(params["context_additions"])
        if addition_tokens:
            request["context_addition_tokens"] = addition_tokens
    source_field = "context" if operation == "context_delivery" else "payload"
    compact_source = (
        operation in {"compose_complete", "attach_complete_turn_context", "context_delivery"}
        or (operation == "capability_automation_source" and set(params) == {"payload"})
    )
    source = params.get(source_field)
    if compact_source and isinstance(source, Mapping):
        # Base64 of ordinary prose can cost more tokens than inline JSON.
        # Large repeated Goal bodies qualify only with a threefold byte reduction.
        fraction = 1.0 if operation == "capability_automation_source" else 1 / 3
        transport = pack_turn_source(source, max_encoded_fraction=fraction)
        if transport is not None:
            del request[source_field]
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
    """Use the checked wire or transfer original-reader metadata from v0."""
    if "turn_envelope" not in request:
        return {**request, "schema_version": COMPLETE_TURN_HOST_REQUEST_SCHEMA}
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
    """Decode the two published Host transports;"""
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
    wire = {}
    if request.get("schema_version") == COMPLETE_TURN_HOST_REQUEST_SCHEMA:
        wire["complete_host_request"] = {key: request[key] for key in (
            "schema_version", "turn_key", "source_decision_hash", "detail_ref",
        ) if key in request}
    elif "turn_envelope" in request or operation != "complete_host_lineage":
        # An explicit historical adapter still requires full validation.
        wire["envelope"] = _wire_mapping(request.get("turn_envelope"))
    checked = _turn_envelope_result(operation, payload=request["work_contract"], **wire,
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


def _turn_settlement_identity(
    operation: str, envelope: Mapping[str, Any], *, turn_instance_id: str | None,
) -> dict[str, Any] | None:
    identity = _turn_envelope_result(operation, envelope=dict(envelope), turn_instance_id=turn_instance_id).get("identity")
    if identity is None:
        return None
    if not isinstance(identity, Mapping):
        raise RuntimeError("TypeScript Turn settlement identity must be an object")
    return dict(identity)


def turn_envelope_replan_settlement_identity(
    envelope: Mapping[str, Any], *, turn_instance_id: str | None,
) -> dict[str, Any] | None:
    return _turn_settlement_identity("replan_settlement_identity", envelope, turn_instance_id=turn_instance_id)


def compose_turn_decision(
    payload: Mapping[str, Any], *, scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    composed_settlement: bool = False,
    include_vision_detail: bool = False,
    include_goal_acceptance_detail: bool = False,
    cli_argv: tuple[str, ...] | list[str] | None = None,
    capture_reference_file: str | None = None,
    cli_registry: str | None = None,
    cli_runtime_root: str | None = None,
) -> dict[str, Any]:
    """Transport complete source facts to the existing typed composition owner."""
    resolution = resolve_scheduler_execution_context(scheduler_execution_context)
    args = render_scheduler_execution_args(scheduler_execution_context=resolution) if resolution.ok and resolution.context is not None else ""
    source = dict(payload)
    result = _turn_envelope_result("compose_complete", payload=source,
        scheduler_execution_args=args, source_edits=True,
        **({"composed_settlement": composed_settlement} if composed_settlement is not False else {}),
        **({"include_vision_detail": True} if include_vision_detail else {}),
        **({"include_goal_acceptance_detail": True} if include_goal_acceptance_detail else {}),
        **({"cli_argv": list(cli_argv)} if cli_argv is not None else {}),
        **({"capture_reference_file": capture_reference_file} if capture_reference_file is not None else {}),
        **({"cli_registry": cli_registry} if cli_registry is not None else {}),
        **({"cli_runtime_root": cli_runtime_root} if cli_runtime_root is not None else {}))
    return _apply_context_projection_changes(source, result)


def attach_complete_turn_context(
    payload: Mapping[str, Any],
    context_additions: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal validated turn-context fields onto an already-composed decision."""
    source = dict(payload)
    additions = dict(context_additions)
    result = _turn_envelope_result(
        "attach_complete_turn_context",
        payload=source,
        context_additions=additions,
        source_edits=True,
    )
    return _apply_context_projection_changes({**source, **additions}, result)


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
    return _turn_settlement_identity("scoped_settlement_identity", envelope, turn_instance_id=turn_instance_id)


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
                                    resume_sections: Mapping[str, str] | None = None,
                                    semantic_unit_anchor: Mapping[str, Any] | None = None,
                                    semantic_unit_fold: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Native IO adapter; the shared typed owner reduces continuity assumptions."""
    source = dict(state)
    result = _turn_envelope_result("native_context_lifecycle", state=source, source_edits=True,
        event=dict(event), new_epoch=new_epoch, pages=pages, pages_verified=pages_verified,
        resume_verified=resume_verified, **({"resume_sections": dict(resume_sections)} if resume_sections else {}),
        **({"semantic_unit_anchor": dict(semantic_unit_anchor)} if semantic_unit_anchor is not None else {}),
        **({"semantic_unit_fold": dict(semantic_unit_fold)} if semantic_unit_fold is not None else {}))
    try:
        return _apply_context_projection_changes(source, result)
    except RuntimeError as error:
        # The native hook retires an invalid reduction under the journal lock.
        raise ValueError("Invalid native context journal edits") from error


def restore_context_delivery(
    delivery: Mapping[str, Any], *, baseline: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate/reconstruct or return the same-capture full body without effects."""
    source = dict(delivery=dict(delivery), baseline=dict(baseline) if baseline is not None else None,
        current=dict(current))
    result = _turn_envelope_result("context_recovery", **source, source_edits=True)
    return _apply_context_projection_changes(source, result)


def plan_stage_then_swap_prefetch(
    *,
    epoch_prefetch: Any = None,
) -> dict[str, Any]:
    """Validate default-off Stage-then-Swap EpochReset prefetch configuration via TS."""
    return _turn_envelope_result(
        "stage_then_swap_plan",
        epoch_prefetch=dict(epoch_prefetch) if isinstance(epoch_prefetch, Mapping) else epoch_prefetch,
    )


def evaluate_epoch_prefetch_swap(
    *,
    epoch_prefetch: Mapping[str, Any],
    context_epoch: str,
    reclaimed_turns: int = 0,
    dropped_failures: int = 0,
    failure_facts: list[str] | tuple[str, ...] = (),
    open_obligations: list[str] | tuple[str, ...] = (),
    source_ref: Mapping[str, str] | None = None,
    exit_code: int | None = None,
    timed_out: bool = False,
    truncated: bool = False,
    raw_bytes_read: int = 0,
    prefetched_output: str = "",
    baseline_bytes: int | None = None,
    baseline_tokens: int | None = None,
    hydrated_tokens: int | None = None,
    tokenizer: Mapping[str, Any] | None = None,
    require_token_gate: bool = False,
    latency_ms: int = 0,
    error_observation: str | None = None,
) -> dict[str, Any]:
    """Evaluate bounded prefetch output and anti-whitewashing receipt via TS."""
    payload: dict[str, Any] = {
        "epoch_prefetch": dict(epoch_prefetch),
        "context_epoch": context_epoch,
        "reclaimed_turns": reclaimed_turns,
        "dropped_failures": dropped_failures,
        "failure_facts": list(failure_facts),
        "open_obligations": list(open_obligations),
        "source_ref": dict(source_ref) if source_ref is not None else None,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "truncated": truncated,
        "raw_bytes_read": raw_bytes_read,
        "prefetched_output": prefetched_output,
        "baseline_bytes": baseline_bytes,
        "latency_ms": latency_ms,
        "require_token_gate": require_token_gate,
    }
    if baseline_tokens is not None:
        payload["baseline_tokens"] = baseline_tokens
    if hydrated_tokens is not None:
        payload["hydrated_tokens"] = hydrated_tokens
    if tokenizer is not None:
        payload["tokenizer"] = dict(tokenizer)
    if error_observation:
        payload["error_observation"] = error_observation
    return _turn_envelope_result("stage_then_swap_evaluate", **payload)


def evaluate_epoch_prefetch_prompt_gate(
    *,
    epoch_prefetch: Mapping[str, Any] | None = None,
    has_inline_exploratory: bool,
    replace_exploratory_context: bool,
    host_input_measurable: bool = True,
    rendered_prompt_baseline_bytes: int | None,
    rendered_prompt_delivered_bytes: int | None,
    rendered_prompt_baseline_tokens: int | None,
    rendered_prompt_delivered_tokens: int | None,
    external_baseline_bytes: int | None = None,
    external_baseline_tokens: int | None = None,
    potential_bytes_saved: int | None = None,
    tokenizer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adjudicate EpochReset vs KeepRaw on delivered prompt bytes, tokens, and cold prefill."""
    payload: dict[str, Any] = {
        "has_inline_exploratory": has_inline_exploratory,
        "replace_exploratory_context": replace_exploratory_context,
        "host_input_measurable": host_input_measurable,
        "rendered_prompt_baseline_bytes": rendered_prompt_baseline_bytes,
        "rendered_prompt_delivered_bytes": rendered_prompt_delivered_bytes,
        "rendered_prompt_baseline_tokens": rendered_prompt_baseline_tokens,
        "rendered_prompt_delivered_tokens": rendered_prompt_delivered_tokens,
        "external_baseline_bytes": external_baseline_bytes,
        "external_baseline_tokens": external_baseline_tokens,
        "potential_bytes_saved": potential_bytes_saved,
        "tokenizer": dict(tokenizer) if tokenizer is not None else None,
    }
    if epoch_prefetch is not None:
        payload["epoch_prefetch"] = dict(epoch_prefetch)
    return _turn_envelope_result("stage_then_swap_prompt_gate", **payload)


def compute_quiet_turn_state_digest(
    *,
    goal_id: str,
    agent_id: str,
    route: Mapping[str, Any] | None = None,
    work_contract: Mapping[str, Any] | None = None,
    turn_envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute canonical volatile-free Quiet-Turn state digest and contract eligibility via TS."""
    payload: dict[str, Any] = {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "route": dict(route) if isinstance(route, Mapping) else None,
        "work_contract": dict(work_contract) if isinstance(work_contract, Mapping) else None,
        "turn_envelope": dict(turn_envelope) if isinstance(turn_envelope, Mapping) else None,
    }
    return _turn_envelope_result("quiet_turn_state_digest", **payload)


def evaluate_quiet_turn(
    *,
    enabled: bool = True,
    quiet_noop_allowed: bool = True,
    must_attempt: bool = False,
    state_changed: bool | None = None,
    current_state_digest: str | None = None,
    previous_state_digest: str | None = None,
    work_contract: Mapping[str, Any] | None = None,
    turn_envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate Quiet-Turn zero-tax skip metadata via TS."""
    payload: dict[str, Any] = {
        "enabled": enabled,
        "quiet_noop_allowed": quiet_noop_allowed,
        "must_attempt": must_attempt,
    }
    if state_changed is not None:
        payload["state_changed"] = state_changed
    if current_state_digest is not None:
        payload["current_state_digest"] = current_state_digest
    if previous_state_digest is not None:
        payload["previous_state_digest"] = previous_state_digest
    if work_contract is not None:
        payload["work_contract"] = dict(work_contract)
    if turn_envelope is not None:
        payload["turn_envelope"] = dict(turn_envelope)
    return _turn_envelope_result("quiet_turn_evaluate", **payload)


def record_semantic_unit_anchor(
    *,
    todo_id: str,
    turn_instance_id: str,
    anchor_step_index: int,
    prefix_digest: str,
    context_epoch: str,
    anchor_surface_seq: int | None = None,
    goal_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Bind a Todo semantic-unit anchor to an exact parent KV prefix digest and epoch via TS."""
    payload: dict[str, Any] = {
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "anchor_step_index": anchor_step_index,
        "prefix_digest": prefix_digest,
        "context_epoch": context_epoch,
    }
    if anchor_surface_seq is not None:
        payload["anchor_surface_seq"] = anchor_surface_seq
    if goal_id is not None:
        payload["goal_id"] = goal_id
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return _turn_envelope_result("semantic_unit_anchor_record", **payload)


def build_semantic_unit_fold_swap(
    *,
    enabled: bool = False,
    anchor: Mapping[str, Any] | None = None,
    todo_id: str,
    turn_instance_id: str,
    context_epoch: str,
    prefix_digest: str | None = None,
    now_step_index: int | None = None,
    now_surface_seq: int | None = None,
    completion_receipt: Mapping[str, Any] | None = None,
    baseline_tokens: int | None = None,
    replacement_tokens: int | None = None,
    baseline_bytes: int | None = None,
    min_net_savings_tokens: int | None = None,
    cold_prefill_amortization_tokens: int | None = None,
    tokenizer_encoding: str | None = None,
    tokenizer: Mapping[str, Any] | None = None,
    obs_next_cmd: Mapping[str, Any] | None = None,
    surface_prefix_intact: bool = True,
    tool_pairing_valid: bool = True,
    quiescent_boundary: bool = True,
    failure_facts: list[str] | tuple[str, ...] = (),
    open_obligations: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Evaluate a receipt-gated Todo-unit suffix fold swap or fail closed to KeepRaw via TS."""
    payload: dict[str, Any] = {
        "enabled": enabled,
        "anchor": dict(anchor) if anchor is not None else None,
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "context_epoch": context_epoch,
        "prefix_digest": prefix_digest,
        "now_step_index": now_step_index,
        "completion_receipt": dict(completion_receipt) if completion_receipt is not None else None,
        "baseline_tokens": baseline_tokens,
        "replacement_tokens": replacement_tokens,
        "baseline_bytes": baseline_bytes,
        "tokenizer": dict(tokenizer) if tokenizer is not None else None,
        "obs_next_cmd": dict(obs_next_cmd) if obs_next_cmd is not None else None,
        "surface_prefix_intact": surface_prefix_intact,
        "tool_pairing_valid": tool_pairing_valid,
        "quiescent_boundary": quiescent_boundary,
        "failure_facts": list(failure_facts),
        "open_obligations": list(open_obligations),
    }
    if now_surface_seq is not None:
        payload["now_surface_seq"] = now_surface_seq
    if min_net_savings_tokens is not None:
        payload["min_net_savings_tokens"] = min_net_savings_tokens
    if cold_prefill_amortization_tokens is not None:
        payload["cold_prefill_amortization_tokens"] = cold_prefill_amortization_tokens
    if tokenizer_encoding is not None:
        payload["tokenizer_encoding"] = tokenizer_encoding
    return _turn_envelope_result("semantic_unit_fold_swap", **payload)
