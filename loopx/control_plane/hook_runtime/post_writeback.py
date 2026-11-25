from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path

from ...file_lock import (
    LOCK_POLICIES,
    LockAcquisitionPolicy,
    LockAcquireTimeoutError,
    exclusive_file_lock,
)
from ..effect_runtime import (
    EffectRuntimeRejected,
    EffectRuntimeRemoteError,
    EffectRuntimeStartupError,
    effect_runtime_result,
)
from .contracts import (
    LEGACY_LOCK_SAFE_SEGMENT_RE,
    POST_WRITEBACK_DISPATCH_ID_RE,
    POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION,
    POST_WRITEBACK_RECEIPT_SNAPSHOT_RE,
    POST_WRITEBACK_SOURCE_REJECTION_DIAGNOSTIC_CODE,
    POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES,
    EffectRuntimeResultFn,
    ExclusiveFileLockFn,
    MonotonicFn,
    PostWritebackHookRegistration,
    PostWritebackSourceRejected,
    as_string_dict,
    hook_failure,
)
from .request_scope import capability_hook_request_scope


def post_writeback_failure_dispatch(
    registrations: Sequence[PostWritebackHookRegistration],
    *,
    error_code: str,
    invoked_count: int = 0,
    runtime_failure: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Construct the closed failure envelope when transaction dispatch fails."""

    dispatch: dict[str, object] = {
        "schema_version": POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION,
        "phase": "post_writeback",
        "registered_count": len(registrations),
        "invoked_count": invoked_count,
        "replayed_hooks": [],
        "retried_hooks": [],
        "intent_count": 0,
        "intents": [],
        "failures": [
            hook_failure(registration, error_code=error_code)
            for registration in registrations
        ],
        "primary_writeback_preserved": True,
        "external_writes_performed": False,
    }
    if runtime_failure is not None:
        dispatch["runtime_failure"] = dict(runtime_failure)
    return dispatch


def post_writeback_runtime_failure(
    error: BaseException,
    *,
    phase: str,
) -> dict[str, str]:
    """Classify a post-writeback runtime failure by transport/IO phase."""

    if isinstance(error, EffectRuntimeRemoteError):
        error_kind = error.error_kind
        diagnostic_code = error.diagnostic_code
    elif isinstance(error, EffectRuntimeStartupError):
        error_kind = "startup_failed"
        diagnostic_code = error.diagnostic_code
    elif isinstance(error, OSError):
        error_kind = "local_io_failed"
        diagnostic_code = "runtime_transport_io_failed"
    else:
        error_kind = "local_contract_invalid"
        diagnostic_code = "runtime_result_shape_invalid"
    return {
        "schema_version": "loopx_post_writeback_runtime_failure_v0",
        "phase": phase,
        "error_kind": error_kind,
        "diagnostic_code": diagnostic_code,
    }


def legacy_post_writeback_lock_target(
    runtime_root: Path,
    plan: Mapping[str, object],
) -> Path:
    """Bridge the retired Python journal lock during rolling upgrades."""

    dispatch_id = str(plan.get("dispatch_id") or "")
    hook_input = plan.get("hook_input")
    identity = hook_input.get("identity") if isinstance(hook_input, Mapping) else None
    raw_goal_id = identity.get("goal_id") if isinstance(identity, Mapping) else None
    goal_id = raw_goal_id.strip() if isinstance(raw_goal_id, str) else ""
    if not POST_WRITEBACK_DISPATCH_ID_RE.fullmatch(dispatch_id):
        raise ValueError("post-writeback provider dispatch_id is invalid")
    if not LEGACY_LOCK_SAFE_SEGMENT_RE.fullmatch(goal_id):
        raise ValueError("post-writeback provider goal_id is invalid")
    return (
        runtime_root
        / "goals"
        / goal_id
        / "post_writeback_hooks"
        / f"{dispatch_id}.json"
    )


def post_writeback_receipt_snapshot(path: Path) -> str:
    """Return the content-addressed digest of an existing receipt or `'missing'`."""

    try:
        encoded = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def enter_legacy_post_writeback_guard(
    guards: ExitStack,
    *,
    runtime_root: Path,
    raw_plan: Mapping[str, object],
    lease_timeout_seconds: float | None,
    exclusive_file_lock_fn: ExclusiveFileLockFn | None = None,
) -> str | None:
    """Acquire one legacy writer guard immediately before its provider."""

    lock_fn = exclusive_file_lock_fn or exclusive_file_lock
    try:
        target = legacy_post_writeback_lock_target(runtime_root, raw_plan)
    except (TypeError, ValueError):
        return "lock_failed"
    plan_guard = ExitStack()
    try:
        plan_guard.enter_context(
            lock_fn(
                target,
                operation="post_writeback_hook_dispatch",
                timeout_seconds=lease_timeout_seconds,
            )
        )
    except LockAcquireTimeoutError:
        plan_guard.close()
        return "lock_unavailable"
    except OSError:
        plan_guard.close()
        return "lock_failed"

    expected_snapshot = raw_plan.get("receipt_snapshot")
    if not isinstance(
        expected_snapshot, str
    ) or not POST_WRITEBACK_RECEIPT_SNAPSHOT_RE.fullmatch(expected_snapshot):
        plan_guard.close()
        return "lock_failed"
    try:
        current_snapshot = post_writeback_receipt_snapshot(target)
    except OSError:
        plan_guard.close()
        return "lock_failed"
    if current_snapshot != expected_snapshot:
        plan_guard.close()
        return "receipt_changed"

    guards.callback(plan_guard.close)
    return None


def post_writeback_finalize_params(
    base_request: Mapping[str, object],
    *,
    transaction_id: object,
    outcomes: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Assemble the finalize-phase RPC payload for post-writeback hooks."""

    return {
        **base_request,
        "phase": "finalize",
        "transaction_id": transaction_id,
        "provider_outcomes": [dict(outcome) for outcome in outcomes],
    }


def post_writeback_transport_rejected_outcome(
    outcome: Mapping[str, object],
) -> dict[str, object]:
    """Strip an oversized or unserializable result down to a rejected stub."""

    return {
        "dispatch_id": outcome.get("dispatch_id"),
        "hook_id": outcome.get("hook_id"),
        "capability_id": outcome.get("capability_id"),
        "attempt_count": outcome.get("attempt_count"),
        "status": "contract_rejected",
        "result": None,
    }


def bounded_post_writeback_transport_outcomes(
    base_request: Mapping[str, object],
    *,
    transaction_id: object,
    outcomes: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Admit the smallest JSON-safe results into the real Python wire budget."""

    bounded: list[dict[str, object]] = []
    candidates: list[tuple[int, int, dict[str, object]]] = []
    for index, raw_outcome in enumerate(outcomes):
        outcome = dict(raw_outcome)
        if outcome.get("status") != "returned":
            bounded.append(outcome)
            continue
        rejected = post_writeback_transport_rejected_outcome(outcome)
        bounded.append(rejected)
        try:
            rejected_size = len(json.dumps(rejected, separators=(",", ":")).encode())
            outcome_size = len(json.dumps(outcome, separators=(",", ":")).encode())
        except (OverflowError, RecursionError, TypeError, ValueError):
            continue
        candidates.append((outcome_size - rejected_size, index, outcome))

    baseline = post_writeback_finalize_params(
        base_request,
        transaction_id=transaction_id,
        outcomes=bounded,
    )
    admitted_size = len(json.dumps(baseline, separators=(",", ":")).encode())
    if admitted_size > POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES:
        raise ValueError("post-writeback finalize transport baseline is oversized")

    for delta, index, outcome in sorted(candidates):
        if admitted_size + delta > POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES:
            continue
        bounded[index] = outcome
        admitted_size += delta

    finalized = post_writeback_finalize_params(
        base_request,
        transaction_id=transaction_id,
        outcomes=bounded,
    )
    if (
        len(json.dumps(finalized, separators=(",", ":")).encode())
        > POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES
    ):
        raise ValueError("post-writeback finalize transport admission drifted")
    return bounded


def decode_post_writeback_preflight(
    value: object,
) -> tuple[dict[str, object] | None, list[Mapping[str, object]]]:
    """Decode either a terminal dispatch or a provider execution plan."""

    if not isinstance(value, Mapping):
        raise ValueError("post-writeback transaction preflight is invalid")
    terminal_dispatch = value.get("dispatch")
    if isinstance(terminal_dispatch, Mapping):
        return (
            as_string_dict(
                terminal_dispatch,
                error_message="post-writeback terminal dispatch is invalid",
            ),
            [],
        )
    provider_plan = value.get("provider_plan")
    if not isinstance(provider_plan, list):
        raise ValueError("post-writeback provider plan is invalid")
    typed_provider_plan: list[Mapping[str, object]] = []
    for raw_plan in provider_plan:
        if not isinstance(raw_plan, Mapping):
            raise ValueError("post-writeback provider step is invalid")
        typed_provider_plan.append(raw_plan)
    return None, typed_provider_plan


def post_writeback_provider_outcome(
    raw_plan: Mapping[str, object],
    *,
    registration: PostWritebackHookRegistration | None,
    unavailable_status: str | None,
) -> tuple[dict[str, object], bool]:
    """Invoke one effect-free provider and isolate its JSON transport boundary."""

    admitted_input = raw_plan.get("hook_input")
    if registration is None or not isinstance(admitted_input, Mapping):
        raise ValueError("post-writeback provider step is unbound")
    invoked = unavailable_status is None
    if unavailable_status is not None:
        result: object = None
        status = unavailable_status
    else:
        try:
            produced = dict(
                registration.producer(
                    as_string_dict(
                        admitted_input,
                        error_message="post-writeback hook input is invalid",
                    )
                )
            )
        except Exception:  # noqa: BLE001 - provider failures isolate.
            result = None
            status = "producer_failed"
        else:
            try:
                encoded_result = json.dumps(
                    produced,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                result = json.loads(encoded_result)
            except (OverflowError, RecursionError, TypeError, ValueError):
                result = None
                status = "contract_rejected"
            else:
                status = "returned"
    return (
        {
            "dispatch_id": raw_plan.get("dispatch_id"),
            "hook_id": raw_plan.get("hook_id"),
            "capability_id": raw_plan.get("capability_id"),
            "attempt_count": raw_plan.get("attempt_count"),
            "status": status,
            "result": result,
        },
        invoked,
    )


def post_writeback_contract_placeholder(
    registration: PostWritebackHookRegistration,
) -> dict[str, object]:
    """Build a bounded placeholder contract for oversized/unserializable hooks."""

    hook_id = registration.hook_id
    capability_id = registration.capability_id
    return {
        "hook_id": (
            hook_id if isinstance(hook_id, str) and len(hook_id) <= 200 else ""
        ),
        "capability_id": (
            capability_id
            if isinstance(capability_id, str) and len(capability_id) <= 200
            else ""
        ),
    }


def bounded_post_writeback_transport_contracts(
    registrations: Sequence[PostWritebackHookRegistration],
    *,
    shared_request: Mapping[str, object],
) -> list[dict[str, object]]:
    """Keep one malformed registration from making the shared JSON wire unusable."""

    bounded: list[dict[str, object]] = []
    candidates: list[tuple[int, int, dict[str, object]]] = []
    for index, registration in enumerate(registrations):
        placeholder = post_writeback_contract_placeholder(registration)
        bounded.append(placeholder)
        try:
            encoded = json.dumps(
                registration.contract(),
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
            contract = json.loads(encoded)
            if not isinstance(contract, dict):
                continue
            placeholder_size = len(
                json.dumps(placeholder, separators=(",", ":")).encode()
            )
        except (
            AttributeError,
            OverflowError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            continue
        candidates.append((len(encoded) - placeholder_size, index, contract))

    request = {**shared_request, "registrations": bounded}
    admitted_size = len(json.dumps(request, separators=(",", ":")).encode())
    if admitted_size > POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES:
        raise ValueError("post-writeback preflight transport baseline is oversized")
    for delta, index, contract in sorted(candidates):
        if admitted_size + delta > POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES:
            continue
        bounded[index] = contract
        admitted_size += delta
    return bounded


def _build_post_writeback_base_request(
    ordered_registrations: Sequence[PostWritebackHookRegistration],
    *,
    source: Mapping[str, object] | None,
    hook_input: Mapping[str, object] | None,
    runtime_root: Path | None,
) -> tuple[dict[str, object], dict[tuple[str, str], PostWritebackHookRegistration]]:
    source_packet = dict(source) if source is not None else None
    legacy_hook_input = dict(hook_input) if hook_input is not None else None
    runtime_root_text = str(runtime_root.resolve()) if runtime_root else None
    shared_request: dict[str, object] = {
        "schema_version": "loopx_post_writeback_hook_transaction_request_v0",
        "runtime_root": runtime_root_text,
        "source": source_packet,
        "hook_input": legacy_hook_input,
    }
    contracts = bounded_post_writeback_transport_contracts(
        ordered_registrations,
        shared_request={
            **shared_request,
            "phase": "preflight",
            "transaction_id": None,
            "provider_outcomes": [],
        },
    )
    providers: dict[tuple[str, str], PostWritebackHookRegistration] = {}
    for registration in ordered_registrations:
        if not isinstance(registration.hook_id, str) or not isinstance(
            registration.capability_id, str
        ):
            continue
        providers.setdefault((registration.hook_id, registration.capability_id), registration)
    return {**shared_request, "registrations": contracts}, providers


def _run_post_writeback_plan_steps(
    typed_provider_plan: Sequence[Mapping[str, object]],
    *,
    legacy_writer_guards: ExitStack,
    providers: Mapping[tuple[str, str], PostWritebackHookRegistration],
    runtime_root: Path | None,
    lease_timeout_seconds: float | None,
    exclusive_file_lock_fn: ExclusiveFileLockFn | None,
    monotonic_fn: MonotonicFn,
) -> tuple[list[dict[str, object]], int]:
    compatibility_timeout = (
        LOCK_POLICIES[LockAcquisitionPolicy.MUTATION].timeout_seconds
        if lease_timeout_seconds is None
        else max(0.0, lease_timeout_seconds)
    )
    compatibility_deadline = monotonic_fn() + compatibility_timeout
    invoked_count = 0
    outcomes: list[dict[str, object]] = []
    for raw_plan in typed_provider_plan:
        key = (
            str(raw_plan.get("hook_id") or ""),
            str(raw_plan.get("capability_id") or ""),
        )
        provider_registration = providers.get(key)
        unavailable_status = (
            enter_legacy_post_writeback_guard(
                legacy_writer_guards,
                runtime_root=runtime_root,
                raw_plan=raw_plan,
                lease_timeout_seconds=max(
                    0.0, compatibility_deadline - monotonic_fn()
                ),
                exclusive_file_lock_fn=exclusive_file_lock_fn,
            )
            if runtime_root is not None
            else None
        )
        outcome, invoked = post_writeback_provider_outcome(
            raw_plan,
            registration=provider_registration,
            unavailable_status=unavailable_status,
        )
        invoked_count += int(invoked)
        outcomes.append(outcome)
    return outcomes, invoked_count


def dispatch_post_writeback_hooks(
    registrations: Sequence[PostWritebackHookRegistration] | None,
    *,
    source: Mapping[str, object] | None = None,
    hook_input: Mapping[str, object] | None = None,
    runtime_root: Path | None = None,
    lease_timeout_seconds: float | None = None,
    effect_runtime_result_fn: EffectRuntimeResultFn | None = None,
    exclusive_file_lock_fn: ExclusiveFileLockFn | None = None,
    monotonic_fn: MonotonicFn | None = None,
) -> dict[str, object]:
    """Run one TS-owned hook transaction around effect-free Python providers.

    The Python adapter exits when providers and the committed-writeback caller
    run in TypeScript. Its legacy-writer guard exits earlier only when TypeScript
    owns an execution claim spanning provider invocation and receipt commit.
    """

    ordered_registrations = tuple(registrations or ())
    if not ordered_registrations or (source is None) == (hook_input is None):
        return post_writeback_failure_dispatch(
            ordered_registrations,
            error_code="registration_or_input_rejected",
        )
    invoke = effect_runtime_result_fn or effect_runtime_result
    clock = monotonic_fn or time.monotonic
    invoked_count = 0
    runtime_phase = "preflight"
    with capability_hook_request_scope():
        try:
            base_request, providers = _build_post_writeback_base_request(
                ordered_registrations,
                source=source,
                hook_input=hook_input,
                runtime_root=runtime_root,
            )
            preflight_request: dict[str, object] = {
                **base_request,
                "phase": "preflight",
                "transaction_id": None,
                "provider_outcomes": [],
            }
            preflight = invoke(
                "capability_hook.post_writeback.transaction",
                preflight_request,
            )
            if not isinstance(preflight, Mapping):
                raise ValueError("post-writeback transaction preflight is invalid")
            terminal_dispatch, typed_provider_plan = decode_post_writeback_preflight(
                preflight
            )
            if terminal_dispatch is not None:
                return terminal_dispatch
            transaction_id = preflight.get("transaction_id")
            if not isinstance(transaction_id, str):
                raise ValueError("post-writeback transaction_id is invalid")

            sizing_outcomes = [
                {
                    "dispatch_id": raw_plan.get("dispatch_id"),
                    "hook_id": raw_plan.get("hook_id"),
                    "capability_id": raw_plan.get("capability_id"),
                    "attempt_count": raw_plan.get("attempt_count"),
                    "status": "contract_rejected",
                    "result": None,
                }
                for raw_plan in typed_provider_plan
            ]
            bounded_post_writeback_transport_outcomes(
                base_request,
                transaction_id=transaction_id,
                outcomes=sizing_outcomes,
            )

            runtime_phase = "provider"
            with ExitStack() as legacy_writer_guards:
                outcomes, invoked_count = _run_post_writeback_plan_steps(
                    typed_provider_plan,
                    legacy_writer_guards=legacy_writer_guards,
                    providers=providers,
                    runtime_root=runtime_root,
                    lease_timeout_seconds=lease_timeout_seconds,
                    exclusive_file_lock_fn=exclusive_file_lock_fn,
                    monotonic_fn=clock,
                )
                bounded_outcomes = bounded_post_writeback_transport_outcomes(
                    base_request,
                    transaction_id=transaction_id,
                    outcomes=outcomes,
                )
                runtime_phase = "finalize"
                finalized = invoke(
                    "capability_hook.post_writeback.transaction",
                    post_writeback_finalize_params(
                        base_request,
                        transaction_id=transaction_id,
                        outcomes=bounded_outcomes,
                    ),
                )
            if not isinstance(finalized, Mapping) or not isinstance(
                finalized.get("dispatch"), Mapping
            ):
                raise ValueError("post-writeback transaction result is invalid")
            return as_string_dict(
                finalized["dispatch"],
                error_message="post-writeback transaction result is invalid",
            )
        except (
            EffectRuntimeRejected,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            if (
                isinstance(exc, EffectRuntimeRejected)
                and exc.diagnostic_code == POST_WRITEBACK_SOURCE_REJECTION_DIAGNOSTIC_CODE
            ):
                # The TypeScript decoder owns source legality. Its typed
                # input-construction rejection crosses this boundary as the
                # legacy composition ValueError, so callers keep reporting one
                # source_projection_failed failure instead of per-hook
                # runtime_result_invalid noise; every other rejection stays a
                # runtime failure without string matching or field copies.
                raise PostWritebackSourceRejected(
                    "post-writeback hooks require committed source identity fields"
                ) from exc
            return post_writeback_failure_dispatch(
                ordered_registrations,
                error_code="runtime_result_invalid",
                invoked_count=invoked_count,
                runtime_failure=post_writeback_runtime_failure(
                    exc,
                    phase=runtime_phase,
                ),
            )
