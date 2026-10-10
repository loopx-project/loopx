from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..file_lock import (
    LOCK_POLICIES,
    LockAcquisitionPolicy,
    LockAcquireTimeoutError,
    exclusive_file_lock,
)
from .effect_runtime import (
    EffectRuntimeRejected,
    EffectRuntimeRemoteError,
    EffectRuntimeStartupError,
    effect_runtime_result,
)
from .coordination.coordination_state_contract_generated import (
    CAPABILITY_HOOK_INTERACTION_RESULT_SCHEMA,
    CAPABILITY_HOOK_POST_WRITEBACK_INPUT_SCHEMA,
    CAPABILITY_HOOK_POST_WRITEBACK_RECEIPT_SCHEMA,
    CAPABILITY_HOOK_POST_WRITEBACK_REGISTRATION_SCHEMA,
    CAPABILITY_HOOK_POST_WRITEBACK_RESULT_SCHEMA,
    CAPABILITY_HOOK_REGISTRATION_SCHEMA,
    CAPABILITY_HOOK_TURN_START_REGISTRATION_SCHEMA,
    CAPABILITY_HOOK_TURN_START_RESULT_SCHEMA,
)


CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION = CAPABILITY_HOOK_REGISTRATION_SCHEMA
INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION = (
    CAPABILITY_HOOK_INTERACTION_RESULT_SCHEMA
)
INTERACTION_PROJECTION_HOOK_DISPATCH_SCHEMA_VERSION = (
    "loopx_interaction_projection_hook_dispatch_v0"
)
TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION = (
    CAPABILITY_HOOK_TURN_START_REGISTRATION_SCHEMA
)
TURN_START_HOOK_RESULT_SCHEMA_VERSION = CAPABILITY_HOOK_TURN_START_RESULT_SCHEMA
TURN_START_HOOK_DISPATCH_SCHEMA_VERSION = "loopx_turn_start_capability_hook_dispatch_v1"
POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION = (
    CAPABILITY_HOOK_POST_WRITEBACK_REGISTRATION_SCHEMA
)
POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION = CAPABILITY_HOOK_POST_WRITEBACK_INPUT_SCHEMA
POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION = CAPABILITY_HOOK_POST_WRITEBACK_RESULT_SCHEMA
POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION = (
    "loopx_post_writeback_capability_hook_dispatch_v0"
)
POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION = (
    CAPABILITY_HOOK_POST_WRITEBACK_RECEIPT_SCHEMA
)
# Diagnostic code the TypeScript source decoder stamps on every source
# input-construction rejection. The decoder owns the field rules; this
# adapter only maps the typed category back to the legacy composition
# failure boundary and never re-validates the fields itself.
POST_WRITEBACK_SOURCE_REJECTION_DIAGNOSTIC_CODE = "post_writeback_source_invalid"
_LEGACY_LOCK_SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_POST_WRITEBACK_DISPATCH_ID_RE = re.compile(r"pwh_[0-9a-f]{64}")
_POST_WRITEBACK_RECEIPT_SNAPSHOT_RE = re.compile(r"(?:missing|sha256:[0-9a-f]{64})")
_POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES = 1_750_000

InteractionProjectionProducer = Callable[[], Mapping[str, Any]]
TurnStartProducer = Callable[[], Mapping[str, Any]]
PostWritebackProducer = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class PostWritebackSourceRejected(ValueError):
    """The typed runtime rejected deterministic source construction."""


@dataclass(frozen=True, slots=True)
class InteractionProjectionHookRegistration:
    """One read-only capability contribution to an interaction contract."""

    hook_id: str
    capability_id: str
    projection_slots: tuple[str, ...]
    requested_read_scope: tuple[str, ...]
    producer: InteractionProjectionProducer
    max_result_bytes: int = 16 * 1024

    def contract(self) -> dict[str, Any]:
        return {
            "schema_version": CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION,
            "hook_id": self.hook_id,
            "capability_id": self.capability_id,
            "phase": "interaction_projection",
            "projection_slots": list(self.projection_slots),
            "budget": {
                "max_invocations_per_dispatch": 1,
                "max_result_bytes": self.max_result_bytes,
            },
            "failure_policy": "isolate",
            "requested_read_scope": list(self.requested_read_scope),
            "requested_write_scope": [],
        }


@dataclass(frozen=True, slots=True)
class TurnStartHookRegistration:
    """One bounded provider observation before a LoopX turn is selected."""

    hook_id: str
    capability_id: str
    requested_read_scope: tuple[str, ...]
    requested_write_scope: tuple[str, ...]
    producer: TurnStartProducer
    max_result_bytes: int = 16 * 1024
    required_read: Mapping[str, Any] | None = None
    context_reader: Callable[[], Mapping[str, Any]] | None = None

    def contract(self) -> dict[str, Any]:
        return {
            "schema_version": TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION,
            "hook_id": self.hook_id,
            "capability_id": self.capability_id,
            "phase": "turn_start",
            "budget": {
                "max_invocations_per_dispatch": 1,
                "max_result_bytes": self.max_result_bytes,
            },
            "failure_policy": "isolate",
            "requested_read_scope": list(self.requested_read_scope),
            "requested_write_scope": list(self.requested_write_scope),
            "required_read": (
                dict(self.required_read) if self.required_read is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class PostWritebackHookRegistration:
    """One effect-free capability observer of a committed primary writeback."""

    hook_id: str
    capability_id: str
    event_kinds: tuple[str, ...]
    intent_kinds: tuple[str, ...]
    requested_read_scope: tuple[str, ...]
    producer: PostWritebackProducer
    policy_version: str = "v0"
    max_input_bytes: int = 64 * 1024
    max_result_bytes: int = 16 * 1024

    def contract(self) -> dict[str, Any]:
        return {
            "schema_version": POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION,
            "hook_id": self.hook_id,
            "capability_id": self.capability_id,
            "policy_version": self.policy_version,
            "phase": "post_writeback",
            "event_kinds": list(self.event_kinds),
            "intent_kinds": list(self.intent_kinds),
            "budget": {
                "max_invocations_per_dispatch": 1,
                "max_input_bytes": self.max_input_bytes,
                "max_result_bytes": self.max_result_bytes,
            },
            "failure_policy": "isolate",
            "requested_read_scope": list(self.requested_read_scope),
            "requested_write_scope": [],
        }


def dispatch_interaction_projection_hooks(
    registrations: Sequence[InteractionProjectionHookRegistration] | None,
) -> dict[str, Any]:
    """Validate and combine read-only projections without granting effects."""

    projections: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    projected_hooks: list[str] = []
    for registration in registrations or ():
        try:
            effect_runtime_result(
                "capability_hook.interaction_projection.validate_registration",
                {"registration": registration.contract()},
            )
        except (EffectRuntimeRejected, RuntimeError, TypeError, ValueError):
            failures.append(
                _hook_failure(registration, error_code="registration_rejected")
            )
            continue
        try:
            result = dict(registration.producer())
        except Exception:  # Capability failures are isolated by contract.
            failures.append(_hook_failure(registration, error_code="producer_failed"))
            continue
        try:
            normalized = effect_runtime_result(
                "capability_hook.interaction_projection.validate",
                {
                    "registration": registration.contract(),
                    "result": result,
                },
            )
        except (EffectRuntimeRejected, RuntimeError, TypeError, ValueError):
            failures.append(_hook_failure(registration, error_code="contract_rejected"))
            continue
        if not isinstance(normalized, Mapping):
            failures.append(
                _hook_failure(registration, error_code="runtime_result_invalid")
            )
            continue
        if normalized.get("status") != "projected":
            continue
        slot = normalized.get("projection_slot")
        projection = normalized.get("projection")
        if not isinstance(slot, str) or not isinstance(projection, Mapping):
            failures.append(
                _hook_failure(registration, error_code="runtime_result_invalid")
            )
            continue
        if slot in projections:
            failures.append(
                _hook_failure(registration, error_code="projection_slot_conflict")
            )
            continue
        projections[slot] = dict(projection)
        projected_hooks.append(registration.hook_id)
    return {
        "schema_version": INTERACTION_PROJECTION_HOOK_DISPATCH_SCHEMA_VERSION,
        "phase": "interaction_projection",
        "registered_count": len(registrations or ()),
        "projected_hooks": projected_hooks,
        "projections": projections,
        "failures": failures,
    }


def dispatch_turn_start_hooks(
    registrations: Sequence[TurnStartHookRegistration] | None,
) -> dict[str, Any]:
    """Run bounded pre-turn observations without exposing provider payloads."""

    results: list[dict[str, Any]] = []
    required_reads: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    contexts: list[dict[str, Any]] = []
    seen_hook_ids: set[str] = set()
    seen_required_read_commands: set[str] = set()
    ordered = sorted(registrations or (), key=lambda item: item.hook_id)
    for registration in ordered:
        if registration.hook_id in seen_hook_ids:
            failures.append(_hook_failure(registration, error_code="duplicate_hook_id"))
            continue
        seen_hook_ids.add(registration.hook_id)
        try:
            admitted_registration = effect_runtime_result(
                "capability_hook.turn_start.validate_registration",
                {"registration": registration.contract()},
            )
        except (EffectRuntimeRejected, RuntimeError, TypeError, ValueError):
            failures.append(
                _hook_failure(registration, error_code="registration_rejected")
            )
            continue
        try:
            result = dict(registration.producer())
        except Exception:  # noqa: BLE001 - capability failures are isolated.
            failures.append(_hook_failure(registration, error_code="producer_failed"))
            continue
        try:
            normalized = effect_runtime_result(
                "capability_hook.turn_start.validate",
                {
                    "registration": registration.contract(),
                    "result": result,
                },
            )
        except (EffectRuntimeRejected, RuntimeError, TypeError, ValueError):
            failures.append(_hook_failure(registration, error_code="contract_rejected"))
            continue
        if not isinstance(normalized, Mapping):
            failures.append(
                _hook_failure(registration, error_code="runtime_result_invalid")
            )
            continue
        results.append(dict(normalized))
        if normalized.get("agent_read_required") is True and isinstance(
            admitted_registration, Mapping
        ):
            required_read = admitted_registration.get("required_read")
            if isinstance(required_read, Mapping):
                command = str(required_read.get("command") or "").strip()
                if command and command not in seen_required_read_commands:
                    required_reads.append(
                        {
                            **dict(required_read),
                            "source": "turn_start_capability_hook",
                            "hook_id": registration.hook_id,
                            "capability_id": registration.capability_id,
                        }
                    )
                    seen_required_read_commands.add(command)
                    if registration.context_reader is not None:
                        # Invoke the registered read adapter, never its displayed
                        # shell command. Discovery receipts remain content-free.
                        try:
                            content = dict(registration.context_reader())
                            contexts.append({"command": command, "content": content})
                        except Exception:  # noqa: BLE001 - preserve an actionable read on failure.
                            contexts.append({"command": command, "error_code": "context_source_unavailable"})
    return {
        "schema_version": TURN_START_HOOK_DISPATCH_SCHEMA_VERSION,
        "phase": "turn_start",
        "registered_count": len(registrations or ()),
        "invoked_count": len(results),
        "results": results,
        "required_reads": required_reads,
        "failures": failures,
        **({"contexts": contexts} if contexts else {}),
    }


def _post_writeback_failure_dispatch(
    registrations: Sequence[PostWritebackHookRegistration],
    *,
    error_code: str,
    invoked_count: int = 0,
    runtime_failure: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    dispatch: dict[str, Any] = {
        "schema_version": POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION,
        "phase": "post_writeback",
        "registered_count": len(registrations),
        "invoked_count": invoked_count,
        "replayed_hooks": [],
        "retried_hooks": [],
        "intent_count": 0,
        "intents": [],
        "failures": [
            _hook_failure(registration, error_code=error_code)
            for registration in registrations
        ],
        "primary_writeback_preserved": True,
        "external_writes_performed": False,
    }
    if runtime_failure is not None:
        dispatch["runtime_failure"] = dict(runtime_failure)
    return dispatch


def _post_writeback_runtime_failure(
    error: BaseException,
    *,
    phase: str,
) -> dict[str, str]:
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


def _legacy_post_writeback_lock_target(
    runtime_root: Path,
    plan: Mapping[str, Any],
) -> Path:
    """Bridge the retired Python journal lock during rolling upgrades."""

    dispatch_id = str(plan.get("dispatch_id") or "")
    hook_input = plan.get("hook_input")
    identity = hook_input.get("identity") if isinstance(hook_input, Mapping) else None
    raw_goal_id = identity.get("goal_id") if isinstance(identity, Mapping) else None
    goal_id = raw_goal_id.strip() if isinstance(raw_goal_id, str) else ""
    if not _POST_WRITEBACK_DISPATCH_ID_RE.fullmatch(dispatch_id):
        raise ValueError("post-writeback provider dispatch_id is invalid")
    if not _LEGACY_LOCK_SAFE_SEGMENT_RE.fullmatch(goal_id):
        raise ValueError("post-writeback provider goal_id is invalid")
    return (
        runtime_root
        / "goals"
        / goal_id
        / "post_writeback_hooks"
        / f"{dispatch_id}.json"
    )


def _enter_legacy_post_writeback_guard(
    guards: ExitStack,
    *,
    runtime_root: Path,
    raw_plan: Mapping[str, Any],
    lease_timeout_seconds: float | None,
) -> str | None:
    """Acquire one legacy writer guard immediately before its provider."""

    try:
        target = _legacy_post_writeback_lock_target(runtime_root, raw_plan)
    except (TypeError, ValueError):
        return "lock_failed"
    plan_guard = ExitStack()
    try:
        plan_guard.enter_context(
            exclusive_file_lock(
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
    ) or not _POST_WRITEBACK_RECEIPT_SNAPSHOT_RE.fullmatch(expected_snapshot):
        plan_guard.close()
        return "lock_failed"
    try:
        current_snapshot = _post_writeback_receipt_snapshot(target)
    except OSError:
        plan_guard.close()
        return "lock_failed"
    if current_snapshot != expected_snapshot:
        plan_guard.close()
        return "receipt_changed"

    guards.callback(plan_guard.close)
    return None


def _post_writeback_receipt_snapshot(path: Path) -> str:
    try:
        encoded = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _post_writeback_finalize_params(
    base_request: Mapping[str, Any],
    *,
    transaction_id: object,
    outcomes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        **base_request,
        "phase": "finalize",
        "transaction_id": transaction_id,
        "provider_outcomes": [dict(outcome) for outcome in outcomes],
    }


def _post_writeback_transport_rejected_outcome(
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "dispatch_id": outcome.get("dispatch_id"),
        "hook_id": outcome.get("hook_id"),
        "capability_id": outcome.get("capability_id"),
        "attempt_count": outcome.get("attempt_count"),
        "status": "contract_rejected",
        "result": None,
    }


def _bounded_post_writeback_transport_outcomes(
    base_request: Mapping[str, Any],
    *,
    transaction_id: object,
    outcomes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Admit the smallest JSON-safe results into the real Python wire budget."""

    bounded: list[dict[str, Any]] = []
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, raw_outcome in enumerate(outcomes):
        outcome = dict(raw_outcome)
        if outcome.get("status") != "returned":
            bounded.append(outcome)
            continue
        rejected = _post_writeback_transport_rejected_outcome(outcome)
        bounded.append(rejected)
        try:
            rejected_size = len(json.dumps(rejected, separators=(",", ":")).encode())
            outcome_size = len(json.dumps(outcome, separators=(",", ":")).encode())
        except (OverflowError, RecursionError, TypeError, ValueError):
            continue
        candidates.append((outcome_size - rejected_size, index, outcome))

    baseline = _post_writeback_finalize_params(
        base_request,
        transaction_id=transaction_id,
        outcomes=bounded,
    )
    admitted_size = len(json.dumps(baseline, separators=(",", ":")).encode())
    if admitted_size > _POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES:
        raise ValueError("post-writeback finalize transport baseline is oversized")

    for delta, index, outcome in sorted(candidates):
        if admitted_size + delta > _POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES:
            continue
        bounded[index] = outcome
        admitted_size += delta

    finalized = _post_writeback_finalize_params(
        base_request,
        transaction_id=transaction_id,
        outcomes=bounded,
    )
    if (
        len(json.dumps(finalized, separators=(",", ":")).encode())
        > _POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES
    ):
        raise ValueError("post-writeback finalize transport admission drifted")
    return bounded


def _decode_post_writeback_preflight(
    value: object,
) -> tuple[dict[str, Any] | None, list[Mapping[str, Any]]]:
    if not isinstance(value, Mapping):
        raise ValueError("post-writeback transaction preflight is invalid")
    terminal_dispatch = value.get("dispatch")
    if isinstance(terminal_dispatch, Mapping):
        return dict(terminal_dispatch), []
    provider_plan = value.get("provider_plan")
    if not isinstance(provider_plan, list):
        raise ValueError("post-writeback provider plan is invalid")
    typed_provider_plan: list[Mapping[str, Any]] = []
    for raw_plan in provider_plan:
        if not isinstance(raw_plan, Mapping):
            raise ValueError("post-writeback provider step is invalid")
        typed_provider_plan.append(raw_plan)
    return None, typed_provider_plan


def _post_writeback_provider_outcome(
    raw_plan: Mapping[str, Any],
    *,
    registration: PostWritebackHookRegistration | None,
    unavailable_status: str | None,
) -> tuple[dict[str, Any], bool]:
    """Invoke one effect-free provider and isolate its JSON transport boundary."""

    admitted_input = raw_plan.get("hook_input")
    if registration is None or not isinstance(admitted_input, Mapping):
        raise ValueError("post-writeback provider step is unbound")
    invoked = unavailable_status is None
    if unavailable_status is not None:
        result = None
        status = unavailable_status
    else:
        try:
            produced = dict(registration.producer(dict(admitted_input)))
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


def _post_writeback_contract_placeholder(
    registration: PostWritebackHookRegistration,
) -> dict[str, object]:
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


def _bounded_post_writeback_transport_contracts(
    registrations: Sequence[PostWritebackHookRegistration],
    *,
    shared_request: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Keep one malformed registration from making the shared JSON wire unusable."""

    bounded: list[dict[str, Any]] = []
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, registration in enumerate(registrations):
        placeholder = _post_writeback_contract_placeholder(registration)
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
    if admitted_size > _POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES:
        raise ValueError("post-writeback preflight transport baseline is oversized")
    for delta, index, contract in sorted(candidates):
        if admitted_size + delta > _POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES:
            continue
        bounded[index] = contract
        admitted_size += delta
    return bounded


def dispatch_post_writeback_hooks(
    registrations: Sequence[PostWritebackHookRegistration] | None,
    *,
    source: Mapping[str, Any] | None = None,
    hook_input: Mapping[str, Any] | None = None,
    runtime_root: Path | None = None,
    lease_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run one TS-owned hook transaction around effect-free Python providers.

    The Python adapter exits when providers and the committed-writeback caller
    run in TypeScript. Its legacy-writer guard exits earlier only when TypeScript
    owns an execution claim spanning provider invocation and receipt commit.
    """

    ordered_registrations = tuple(registrations or ())
    if not ordered_registrations:
        return _post_writeback_failure_dispatch(
            ordered_registrations,
            error_code="registration_or_input_rejected",
        )
    if (source is None) == (hook_input is None):
        return _post_writeback_failure_dispatch(
            ordered_registrations,
            error_code="registration_or_input_rejected",
        )
    invoked_count = 0
    runtime_phase = "preflight"
    try:
        source_packet = dict(source) if source is not None else None
        legacy_hook_input = dict(hook_input) if hook_input is not None else None
        runtime_root_text = str(runtime_root.resolve()) if runtime_root else None
        shared_request = {
            "schema_version": "loopx_post_writeback_hook_transaction_request_v0",
            "runtime_root": runtime_root_text,
            "source": source_packet,
            "hook_input": legacy_hook_input,
        }
        contracts = _bounded_post_writeback_transport_contracts(
            ordered_registrations,
            shared_request={
                **shared_request,
                "phase": "preflight",
                "transaction_id": None,
                "provider_outcomes": [],
            },
        )
        base_request = {**shared_request, "registrations": contracts}
        providers: dict[tuple[str, str], PostWritebackHookRegistration] = {}
        for registration in ordered_registrations:
            if not isinstance(registration.hook_id, str) or not isinstance(
                registration.capability_id, str
            ):
                continue
            key = (registration.hook_id, registration.capability_id)
            providers.setdefault(key, registration)

        preflight_request = {
            **base_request,
            "phase": "preflight",
            "transaction_id": None,
            "provider_outcomes": [],
        }
        preflight = effect_runtime_result(
            "capability_hook.post_writeback.transaction",
            preflight_request,
        )
        terminal_dispatch, typed_provider_plan = _decode_post_writeback_preflight(
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
        _bounded_post_writeback_transport_outcomes(
            base_request,
            transaction_id=transaction_id,
            outcomes=sizing_outcomes,
        )

        runtime_phase = "provider"
        compatibility_timeout = (
            LOCK_POLICIES[LockAcquisitionPolicy.MUTATION].timeout_seconds
            if lease_timeout_seconds is None
            else max(0.0, lease_timeout_seconds)
        )
        compatibility_deadline = time.monotonic() + compatibility_timeout
        with ExitStack() as legacy_writer_guards:
            outcomes: list[dict[str, Any]] = []
            for raw_plan in typed_provider_plan:
                key = (
                    str(raw_plan.get("hook_id") or ""),
                    str(raw_plan.get("capability_id") or ""),
                )
                provider_registration = providers.get(key)
                unavailable_status = (
                    _enter_legacy_post_writeback_guard(
                        legacy_writer_guards,
                        runtime_root=runtime_root,
                        raw_plan=raw_plan,
                        lease_timeout_seconds=max(
                            0.0, compatibility_deadline - time.monotonic()
                        ),
                    )
                    if runtime_root is not None
                    else None
                )
                outcome, invoked = _post_writeback_provider_outcome(
                    raw_plan,
                    registration=provider_registration,
                    unavailable_status=unavailable_status,
                )
                invoked_count += int(invoked)
                outcomes.append(outcome)
            bounded_outcomes = _bounded_post_writeback_transport_outcomes(
                base_request,
                transaction_id=transaction_id,
                outcomes=outcomes,
            )
            runtime_phase = "finalize"
            finalized = effect_runtime_result(
                "capability_hook.post_writeback.transaction",
                _post_writeback_finalize_params(
                    base_request,
                    transaction_id=transaction_id,
                    outcomes=bounded_outcomes,
                ),
            )
            if not isinstance(finalized, Mapping) or not isinstance(
                finalized.get("dispatch"), Mapping
            ):
                raise ValueError("post-writeback transaction result is invalid")
            return dict(finalized["dispatch"])
    except (EffectRuntimeRejected, OSError, RuntimeError, TypeError, ValueError) as exc:
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
        return _post_writeback_failure_dispatch(
            ordered_registrations,
            error_code="runtime_result_invalid",
            invoked_count=invoked_count,
            runtime_failure=_post_writeback_runtime_failure(
                exc,
                phase=runtime_phase,
            ),
        )


def _hook_failure(
    registration: (
        InteractionProjectionHookRegistration
        | TurnStartHookRegistration
        | PostWritebackHookRegistration
    ),
    *,
    error_code: str,
    durable_receipt_ref: str | None = None,
) -> dict[str, str]:
    failure = {
        "hook_id": registration.hook_id,
        "capability_id": registration.capability_id,
        "error_code": error_code,
    }
    if durable_receipt_ref is not None:
        failure["durable_receipt_ref"] = durable_receipt_ref
    return failure
