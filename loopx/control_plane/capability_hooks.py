from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path

from ..file_lock import (
    LOCK_POLICIES as LOCK_POLICIES,
    LockAcquisitionPolicy as LockAcquisitionPolicy,
    LockAcquireTimeoutError as LockAcquireTimeoutError,
    exclusive_file_lock,
)
from .effect_runtime import (
    EffectRuntimeRejected as EffectRuntimeRejected,
    EffectRuntimeRemoteError as EffectRuntimeRemoteError,
    EffectRuntimeStartupError as EffectRuntimeStartupError,
    effect_runtime_projection_result,
    effect_runtime_result,
)
from .hook_runtime.contracts import (
    CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION as CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION,
    INTERACTION_PROJECTION_HOOK_DISPATCH_SCHEMA_VERSION as INTERACTION_PROJECTION_HOOK_DISPATCH_SCHEMA_VERSION,
    INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION as INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
    LEGACY_LOCK_SAFE_SEGMENT_RE as _LEGACY_LOCK_SAFE_SEGMENT_RE,
    POST_WRITEBACK_DISPATCH_ID_RE as _POST_WRITEBACK_DISPATCH_ID_RE,
    POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION as POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION,
    POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION as POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION,
    POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION as POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION,
    POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION as POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION,
    POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION as POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
    POST_WRITEBACK_RECEIPT_SNAPSHOT_RE as _POST_WRITEBACK_RECEIPT_SNAPSHOT_RE,
    POST_WRITEBACK_SOURCE_REJECTION_DIAGNOSTIC_CODE as POST_WRITEBACK_SOURCE_REJECTION_DIAGNOSTIC_CODE,
    POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES as _POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES,
    TURN_START_HOOK_DISPATCH_SCHEMA_VERSION as TURN_START_HOOK_DISPATCH_SCHEMA_VERSION,
    TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION as TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION,
    TURN_START_HOOK_RESULT_SCHEMA_VERSION as TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    InteractionProjectionHookRegistration as InteractionProjectionHookRegistration,
    InteractionProjectionProducer as InteractionProjectionProducer,
    PostWritebackHookRegistration as PostWritebackHookRegistration,
    PostWritebackProducer as PostWritebackProducer,
    PostWritebackSourceRejected as PostWritebackSourceRejected,
    TurnContextHookRegistration as TurnContextHookRegistration,
    TurnStartContextProjector as TurnStartContextProjector,
    TurnStartHookRegistration as TurnStartHookRegistration,
    TurnStartProducer as TurnStartProducer,
    hook_failure as _hook_failure,
)
from .hook_runtime.dispatch import (
    dispatch_interaction_projection_hooks as _dispatch_interaction_projection_hooks_impl,
    dispatch_turn_start_hooks as _dispatch_turn_start_hooks_impl,
    merge_turn_start_dispatch as merge_turn_start_dispatch,
)
from .hook_runtime.post_writeback import (
    bounded_post_writeback_transport_contracts as _bounded_post_writeback_transport_contracts,
    bounded_post_writeback_transport_outcomes as _bounded_post_writeback_transport_outcomes,
    decode_post_writeback_preflight as _decode_post_writeback_preflight,
    dispatch_post_writeback_hooks as _dispatch_post_writeback_hooks_impl,
    enter_legacy_post_writeback_guard as _enter_legacy_post_writeback_guard_impl,
    legacy_post_writeback_lock_target as _legacy_post_writeback_lock_target,
    post_writeback_contract_placeholder as _post_writeback_contract_placeholder,
    post_writeback_failure_dispatch as _post_writeback_failure_dispatch,
    post_writeback_finalize_params as _post_writeback_finalize_params,
    post_writeback_provider_outcome as _post_writeback_provider_outcome,
    post_writeback_receipt_snapshot as _post_writeback_receipt_snapshot,
    post_writeback_runtime_failure as _post_writeback_runtime_failure,
    post_writeback_transport_rejected_outcome as _post_writeback_transport_rejected_outcome,
)
from .hook_runtime.request_scope import (
    CapabilityHookRequestScope as CapabilityHookRequestScope,
    capability_hook_request_scope as capability_hook_request_scope,
    current_hook_registry_snapshots as current_hook_registry_snapshots,
    current_hook_request_scope as current_hook_request_scope,
    invalidate_hook_registry_snapshot as invalidate_hook_registry_snapshot,
    load_scoped_registry as load_scoped_registry,
)
from .hook_runtime.turn_context import (
    capability_result_fields as capability_result_fields,
    dispatch_turn_context_hooks as _dispatch_turn_context_hooks_impl,
    host_context_projection as host_context_projection,
    project_turn_context as _project_turn_context_impl,
    turn_context_binding_source as _turn_context_binding_source_impl,
    turn_context_source as turn_context_source,
)


def _call_effect_runtime_result(method: str, params: Mapping[str, object]) -> object:
    return effect_runtime_result(method, params)


def _call_effect_runtime_projection_result(
    method: str,
    params: Mapping[str, object],
) -> object:
    return effect_runtime_projection_result(method, params)


def _call_turn_context_source(payload: Mapping[str, object]) -> Mapping[str, object]:
    return turn_context_source(payload)


def _acquire_exclusive_file_lock(
    path: Path,
    *,
    operation: str | None = None,
    timeout_seconds: float | None = None,
) -> AbstractContextManager[object]:
    return exclusive_file_lock(
        path,
        operation=operation,
        timeout_seconds=timeout_seconds,
    )


def _read_monotonic() -> float:
    return time.monotonic()


def _turn_context_binding_source(
    payload: Mapping[str, object],
    *,
    from_journal: bool = False,
) -> Mapping[str, object]:
    return _turn_context_binding_source_impl(
        payload,
        from_journal=from_journal,
        turn_context_source_fn=_call_turn_context_source,
    )


def dispatch_turn_context_hooks(
    payload: dict[str, object],
    registrations: Sequence[TurnContextHookRegistration],
) -> None:
    _dispatch_turn_context_hooks_impl(
        payload,
        registrations,
        effect_runtime_result_fn=_call_effect_runtime_result,
        turn_context_source_fn=_call_turn_context_source,
    )


def project_turn_context(
    payload: Mapping[str, object],
    *,
    from_journal: bool = False,
) -> dict[str, object]:
    return _project_turn_context_impl(
        payload,
        from_journal=from_journal,
        effect_runtime_projection_result_fn=_call_effect_runtime_projection_result,
        turn_context_source_fn=_call_turn_context_source,
    )


def dispatch_interaction_projection_hooks(
    registrations: Sequence[InteractionProjectionHookRegistration] | None,
) -> dict[str, object]:
    return _dispatch_interaction_projection_hooks_impl(
        registrations,
        effect_runtime_result_fn=_call_effect_runtime_result,
    )


def dispatch_turn_start_hooks(
    registrations: Sequence[TurnStartHookRegistration] | None,
) -> dict[str, object]:
    return _dispatch_turn_start_hooks_impl(
        registrations,
        effect_runtime_result_fn=_call_effect_runtime_result,
    )


def _enter_legacy_post_writeback_guard(
    guards: ExitStack,
    *,
    runtime_root: Path,
    raw_plan: Mapping[str, object],
    lease_timeout_seconds: float | None,
) -> str | None:
    return _enter_legacy_post_writeback_guard_impl(
        guards,
        runtime_root=runtime_root,
        raw_plan=raw_plan,
        lease_timeout_seconds=lease_timeout_seconds,
        exclusive_file_lock_fn=_acquire_exclusive_file_lock,
    )


def dispatch_post_writeback_hooks(
    registrations: Sequence[PostWritebackHookRegistration] | None,
    *,
    source: Mapping[str, object] | None = None,
    hook_input: Mapping[str, object] | None = None,
    runtime_root: Path | None = None,
    lease_timeout_seconds: float | None = None,
) -> dict[str, object]:
    """Run one TS-owned hook transaction around effect-free Python providers."""

    return _dispatch_post_writeback_hooks_impl(
        registrations,
        source=source,
        hook_input=hook_input,
        runtime_root=runtime_root,
        lease_timeout_seconds=lease_timeout_seconds,
        effect_runtime_result_fn=_call_effect_runtime_result,
        exclusive_file_lock_fn=_acquire_exclusive_file_lock,
        monotonic_fn=_read_monotonic,
    )


__all__ = [
    "CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION",
    "CapabilityHookRequestScope",
    "EffectRuntimeRejected",
    "EffectRuntimeRemoteError",
    "EffectRuntimeStartupError",
    "INTERACTION_PROJECTION_HOOK_DISPATCH_SCHEMA_VERSION",
    "INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION",
    "InteractionProjectionHookRegistration",
    "InteractionProjectionProducer",
    "LOCK_POLICIES",
    "LockAcquireTimeoutError",
    "LockAcquisitionPolicy",
    "POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION",
    "POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION",
    "POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION",
    "POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION",
    "POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION",
    "POST_WRITEBACK_SOURCE_REJECTION_DIAGNOSTIC_CODE",
    "PostWritebackHookRegistration",
    "PostWritebackProducer",
    "PostWritebackSourceRejected",
    "TURN_START_HOOK_DISPATCH_SCHEMA_VERSION",
    "TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION",
    "TURN_START_HOOK_RESULT_SCHEMA_VERSION",
    "TurnContextHookRegistration",
    "TurnStartContextProjector",
    "TurnStartHookRegistration",
    "TurnStartProducer",
    "_LEGACY_LOCK_SAFE_SEGMENT_RE",
    "_POST_WRITEBACK_DISPATCH_ID_RE",
    "_POST_WRITEBACK_RECEIPT_SNAPSHOT_RE",
    "_POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES",
    "_bounded_post_writeback_transport_contracts",
    "_bounded_post_writeback_transport_outcomes",
    "_decode_post_writeback_preflight",
    "_enter_legacy_post_writeback_guard",
    "_hook_failure",
    "_legacy_post_writeback_lock_target",
    "_post_writeback_contract_placeholder",
    "_post_writeback_failure_dispatch",
    "_post_writeback_finalize_params",
    "_post_writeback_provider_outcome",
    "_post_writeback_receipt_snapshot",
    "_post_writeback_runtime_failure",
    "_post_writeback_transport_rejected_outcome",
    "_turn_context_binding_source",
    "capability_hook_request_scope",
    "capability_result_fields",
    "current_hook_registry_snapshots",
    "current_hook_request_scope",
    "dispatch_interaction_projection_hooks",
    "dispatch_post_writeback_hooks",
    "dispatch_turn_context_hooks",
    "dispatch_turn_start_hooks",
    "effect_runtime_projection_result",
    "effect_runtime_result",
    "exclusive_file_lock",
    "host_context_projection",
    "invalidate_hook_registry_snapshot",
    "load_scoped_registry",
    "merge_turn_start_dispatch",
    "project_turn_context",
    "time",
    "turn_context_source",
]
