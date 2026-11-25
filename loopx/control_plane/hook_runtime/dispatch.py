from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from .contracts import (
    INTERACTION_PROJECTION_HOOK_DISPATCH_SCHEMA_VERSION,
    TURN_START_HOOK_DISPATCH_SCHEMA_VERSION,
    EffectRuntimeResultFn,
    InteractionProjectionHookRegistration,
    TurnStartHookRegistration,
    as_string_dict,
    hook_failure,
)
from .request_scope import capability_hook_request_scope


def _evaluate_interaction_projection_hook(
    registration: InteractionProjectionHookRegistration,
    *,
    invoke: EffectRuntimeResultFn,
    projections: dict[str, object],
    failures: list[dict[str, str]],
    projected_hooks: list[str],
) -> None:
    try:
        invoke(
            "capability_hook.interaction_projection.validate_registration",
            {"registration": registration.contract()},
        )
    except (EffectRuntimeRejected, RuntimeError, TypeError, ValueError):
        failures.append(hook_failure(registration, error_code="registration_rejected"))
        return
    try:
        result = dict(registration.producer())
    except Exception:  # Capability failures are isolated by contract.
        failures.append(hook_failure(registration, error_code="producer_failed"))
        return
    try:
        normalized = invoke(
            "capability_hook.interaction_projection.validate",
            {
                "registration": registration.contract(),
                "result": result,
            },
        )
    except (EffectRuntimeRejected, RuntimeError, TypeError, ValueError):
        failures.append(hook_failure(registration, error_code="contract_rejected"))
        return
    if not isinstance(normalized, Mapping):
        failures.append(hook_failure(registration, error_code="runtime_result_invalid"))
        return
    if normalized.get("status") != "projected":
        return
    slot = normalized.get("projection_slot")
    projection = normalized.get("projection")
    if not isinstance(slot, str) or not isinstance(projection, Mapping):
        failures.append(hook_failure(registration, error_code="runtime_result_invalid"))
        return
    if slot in projections:
        failures.append(
            hook_failure(registration, error_code="projection_slot_conflict")
        )
        return
    projections[slot] = as_string_dict(
        projection,
        error_message="interaction projection slot payload must have string keys",
    )
    projected_hooks.append(registration.hook_id)


def dispatch_interaction_projection_hooks(
    registrations: Sequence[InteractionProjectionHookRegistration] | None,
    *,
    effect_runtime_result_fn: EffectRuntimeResultFn | None = None,
) -> dict[str, object]:
    """Validate and combine read-only projections without granting effects."""

    invoke = effect_runtime_result_fn or effect_runtime_result
    projections: dict[str, object] = {}
    failures: list[dict[str, str]] = []
    projected_hooks: list[str] = []
    ordered_registrations = registrations or ()
    with capability_hook_request_scope():
        for registration in ordered_registrations:
            _evaluate_interaction_projection_hook(
                registration,
                invoke=invoke,
                projections=projections,
                failures=failures,
                projected_hooks=projected_hooks,
            )
    return {
        "schema_version": INTERACTION_PROJECTION_HOOK_DISPATCH_SCHEMA_VERSION,
        "phase": "interaction_projection",
        "registered_count": len(ordered_registrations),
        "projected_hooks": projected_hooks,
        "projections": projections,
        "failures": failures,
    }


def _collect_turn_start_required_read(
    registration: TurnStartHookRegistration,
    admitted_registration: object,
    normalized: Mapping[str, object],
    *,
    seen_commands: set[str],
    required_reads: list[dict[str, object]],
    contexts: list[dict[str, object]],
) -> None:
    if normalized.get("agent_read_required") is not True or not isinstance(
        admitted_registration, Mapping
    ):
        return
    required_read = admitted_registration.get("required_read")
    if not isinstance(required_read, Mapping):
        return
    command = str(required_read.get("command") or "").strip()
    if not command or command in seen_commands:
        return
    required_reads.append(
        {
            **{str(k): v for k, v in required_read.items()},
            "source": "turn_start_capability_hook",
            "hook_id": registration.hook_id,
            "capability_id": registration.capability_id,
        }
    )
    seen_commands.add(command)
    if registration.context_reader is None:
        return
    # Invoke the registered read adapter, never its displayed shell command.
    # Discovery receipts remain content-free.
    try:
        content = dict(registration.context_reader())
        contexts.append({"command": command, "content": content})
    except Exception:  # noqa: BLE001 - preserve an actionable read on failure.
        contexts.append({"command": command, "error_code": "context_source_unavailable"})


def _evaluate_turn_start_hook(
    registration: TurnStartHookRegistration,
    *,
    invoke: EffectRuntimeResultFn,
    seen_hook_ids: set[str],
    seen_required_read_commands: set[str],
    results: list[dict[str, object]],
    required_reads: list[dict[str, object]],
    failures: list[dict[str, str]],
    contexts: list[dict[str, object]],
) -> bool:
    if registration.hook_id in seen_hook_ids:
        failures.append(hook_failure(registration, error_code="duplicate_hook_id"))
        return False
    seen_hook_ids.add(registration.hook_id)
    try:
        admitted_registration = invoke(
            "capability_hook.turn_start.validate_registration",
            {"registration": registration.contract()},
        )
    except (EffectRuntimeRejected, RuntimeError, TypeError, ValueError):
        failures.append(hook_failure(registration, error_code="registration_rejected"))
        return False
    try:
        result = dict(registration.producer())
    except Exception:  # noqa: BLE001 - capability failures are isolated.
        failures.append(hook_failure(registration, error_code="producer_failed"))
        return bool(registration.requested_write_scope)
    try:
        normalized = invoke(
            "capability_hook.turn_start.validate",
            {
                "registration": registration.contract(),
                "result": result,
            },
        )
    except (EffectRuntimeRejected, RuntimeError, TypeError, ValueError):
        failures.append(hook_failure(registration, error_code="contract_rejected"))
        return bool(registration.requested_write_scope)
    if not isinstance(normalized, Mapping):
        failures.append(
            hook_failure(registration, error_code="runtime_result_invalid")
        )
        return bool(registration.requested_write_scope)
    results.append(
        as_string_dict(
            normalized,
            error_message="turn start validation returned invalid key types",
        )
    )
    _collect_turn_start_required_read(
        registration,
        admitted_registration,
        normalized,
        seen_commands=seen_required_read_commands,
        required_reads=required_reads,
        contexts=contexts,
    )
    return False


def dispatch_turn_start_hooks(
    registrations: Sequence[TurnStartHookRegistration] | None,
    *,
    effect_runtime_result_fn: EffectRuntimeResultFn | None = None,
) -> dict[str, object]:
    """Run bounded pre-turn observations without exposing provider payloads."""

    invoke = effect_runtime_result_fn or effect_runtime_result
    results: list[dict[str, object]] = []
    required_reads: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    contexts: list[dict[str, object]] = []
    seen_hook_ids: set[str] = set()
    seen_required_read_commands: set[str] = set()
    uncertain_local_effects = False
    registered = registrations or ()
    ordered = sorted(registered, key=lambda item: item.hook_id)
    with capability_hook_request_scope():
        for registration in ordered:
            uncertain_local_effects |= _evaluate_turn_start_hook(
                registration,
                invoke=invoke,
                seen_hook_ids=seen_hook_ids,
                seen_required_read_commands=seen_required_read_commands,
                results=results,
                required_reads=required_reads,
                failures=failures,
                contexts=contexts,
            )
    dispatch: dict[str, object] = {
        "schema_version": TURN_START_HOOK_DISPATCH_SCHEMA_VERSION,
        "phase": "turn_start",
        "registered_count": len(registered),
        "invoked_count": len(results),
        "results": results,
        "required_reads": required_reads,
        "failures": failures,
    }
    if uncertain_local_effects:
        dispatch["uncertain_local_effects"] = True
    if contexts:
        dispatch["contexts"] = contexts
    return dispatch


def _object_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return list(value)
    return []


def _count_int(value: object) -> int:
    if isinstance(value, int):
        return int(value)
    if isinstance(value, (float, str, bytes)):
        return int(value)
    return 0


def merge_turn_start_dispatch(
    dispatch: Mapping[str, object] | None,
    extra: Mapping[str, object],
    *,
    include_contexts: bool = True,
) -> dict[str, object]:
    """Merge an additional turn-start dispatch packet without dynamic `Any` loops."""

    result = dict(dispatch or {})
    for key in ("results", "required_reads", "failures", "contexts"):
        if key == "contexts" and (not include_contexts or not extra.get(key)):
            continue
        result[key] = _object_list(result.get(key)) + _object_list(extra.get(key))
    for key in ("registered_count", "invoked_count"):
        result[key] = _count_int(result.get(key)) + _count_int(extra.get(key))
    return result
