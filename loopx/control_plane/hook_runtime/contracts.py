from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias

from ..coordination.coordination_state_contract_generated import (
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
LEGACY_LOCK_SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
POST_WRITEBACK_DISPATCH_ID_RE = re.compile(r"pwh_[0-9a-f]{64}")
POST_WRITEBACK_RECEIPT_SNAPSHOT_RE = re.compile(r"(?:missing|sha256:[0-9a-f]{64})")
POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES = 1_750_000

HookPayload: TypeAlias = Mapping[str, object]
HookRecord: TypeAlias = dict[str, object]
InteractionProjectionProducer = Callable[[], Mapping[str, object]]
TurnStartProducer = Callable[[], Mapping[str, object]]
# Invocation-owned read composition after selection; no persisted policy or
# provider authority is created by supplying this adapter.
TurnStartContextProjector = Callable[
    [Mapping[str, object], Mapping[str, object] | None], dict[str, object] | None
]
PostWritebackProducer = Callable[[Mapping[str, object]], Mapping[str, object]]


class EffectRuntimeResultFn(Protocol):
    """Callable surface matching `effect_runtime_result(method, params)`."""

    def __call__(self, method: str, params: Mapping[str, object]) -> object: ...


class EffectRuntimeProjectionFn(Protocol):
    """Callable surface matching `effect_runtime_projection_result(method, params)`."""

    def __call__(self, method: str, params: Mapping[str, object]) -> object: ...


class TurnContextSourceFn(Protocol):
    """Callable surface resolving a hook payload's source metadata."""

    def __call__(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class ExclusiveFileLockFn(Protocol):
    """Callable surface matching `exclusive_file_lock` for legacy write guards."""

    def __call__(
        self,
        path: Path,
        *,
        operation: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AbstractContextManager[object]: ...


class MonotonicFn(Protocol):
    """Monotonic clock callback injected for deterministic deadline tests."""

    def __call__(self) -> float: ...


class PostWritebackSourceRejected(ValueError):
    """The typed runtime rejected deterministic source construction."""


@dataclass(frozen=True, slots=True)
class TurnContextHookRegistration:
    """Configured provider IO after governing selection, before host input.

    Registration bounds IO/results; it grants no provider or task authority.
    """

    hook_id: str
    capability_id: str
    readback_slots: tuple[str, ...]
    producer: Callable[[], Mapping[str, object]]
    max_result_bytes: int = 64 * 1024

    def contract(self) -> dict[str, object]:
        return {
            "hook_id": self.hook_id,
            "capability_id": self.capability_id,
            "readback_slots": list(self.readback_slots),
            "max_result_bytes": self.max_result_bytes,
        }


@dataclass(frozen=True, slots=True)
class InteractionProjectionHookRegistration:
    """One read-only capability contribution to an interaction contract."""

    hook_id: str
    capability_id: str
    projection_slots: tuple[str, ...]
    requested_read_scope: tuple[str, ...]
    producer: InteractionProjectionProducer
    max_result_bytes: int = 16 * 1024

    def contract(self) -> dict[str, object]:
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
    required_read: Mapping[str, object] | None = None
    context_reader: Callable[[], Mapping[str, object]] | None = None

    def contract(self) -> dict[str, object]:
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

    def contract(self) -> dict[str, object]:
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


def as_string_dict(value: object, *, error_message: str) -> dict[str, object]:
    """Narrow a runtime mapping payload to a concrete `dict[str, object]`."""

    if not isinstance(value, Mapping):
        raise ValueError(error_message)
    record: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(error_message)
        record[key] = item
    return record


def hook_failure(
    registration: (
        InteractionProjectionHookRegistration
        | TurnStartHookRegistration
        | PostWritebackHookRegistration
    ),
    *,
    error_code: str,
    durable_receipt_ref: str | None = None,
) -> dict[str, str]:
    """Build one normalized failure row for hook dispatch diagnostics."""

    failure = {
        "hook_id": registration.hook_id,
        "capability_id": registration.capability_id,
        "error_code": error_code,
    }
    if durable_receipt_ref is not None:
        failure["durable_receipt_ref"] = durable_receipt_ref
    return failure
