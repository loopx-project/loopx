"""Typed local-private binding storage, receipt transitions, and public packet helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import cast

from ...file_lock import LockAcquireTimeoutError, lock_timeout_error_fields

BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION = "loopx_goal_channel_botmux_binding_v0"
BOTMUX_RUNTIME_OPERATION_SCHEMA_VERSION = "loopx_goal_channel_runtime_operation_v0"
BOTMUX_PROVIDER = "botmux"
BOTMUX_DEFAULT_ENDPOINT = "http://127.0.0.1:7891"
BOTMUX_DEFAULT_TOKEN_ENV = "BOTMUX_DASHBOARD_TOKEN"
BOTMUX_HTTP_TIMEOUT_SECONDS = 4.0

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PRIVATE_PACKET_KEYS = frozenset(
    {
        "bot_id",
        "chat_id",
        "endpoint",
        "expected_working_dir",
        "message_id",
        "session_id",
        "token",
        "token_env",
        "trigger_id",
    }
)

Payload = dict[str, object]
PayloadView = Mapping[str, object]
JsonRequester = Callable[..., Mapping[str, object]]
AtomicJsonWriter = Callable[[Path, PayloadView], None]
NowIsoFn = Callable[[], str]


class BotmuxApiError(RuntimeError):
    """HTTP error raised when the botmux daemon rejects a request."""

    def __init__(self, status: int, payload: PayloadView | None = None) -> None:
        super().__init__(f"botmux API returned HTTP {status}")
        self.status = status
        self.payload: Payload = dict(payload or {})


class BotmuxTransportError(RuntimeError):
    """Transport or response-decoding failure talking to the botmux daemon."""


class BotmuxDispatchState(str, Enum):
    """Lifecycle states recorded in a local-private botmux dispatch receipt."""

    ATTEMPTING = "attempting"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


_TERMINAL_DISPATCH_STATES = frozenset(
    {
        BotmuxDispatchState.COMPLETED,
        BotmuxDispatchState.FAILED,
        BotmuxDispatchState.NOT_FOUND,
    }
)

_DISPATCH_TRANSITIONS: dict[
    BotmuxDispatchState | None,
    frozenset[BotmuxDispatchState],
] = {
    None: frozenset({BotmuxDispatchState.ATTEMPTING}),
    BotmuxDispatchState.ATTEMPTING: frozenset(
        {
            BotmuxDispatchState.QUEUED,
            BotmuxDispatchState.UNKNOWN,
            BotmuxDispatchState.REJECTED,
        }
    ),
    BotmuxDispatchState.QUEUED: frozenset(
        {
            BotmuxDispatchState.QUEUED,
            BotmuxDispatchState.RUNNING,
            BotmuxDispatchState.COMPLETED,
            BotmuxDispatchState.FAILED,
            BotmuxDispatchState.NOT_FOUND,
            BotmuxDispatchState.UNKNOWN,
        }
    ),
    BotmuxDispatchState.RUNNING: frozenset(
        {
            BotmuxDispatchState.RUNNING,
            BotmuxDispatchState.COMPLETED,
            BotmuxDispatchState.FAILED,
            BotmuxDispatchState.NOT_FOUND,
            BotmuxDispatchState.UNKNOWN,
        }
    ),
    BotmuxDispatchState.UNKNOWN: frozenset(
        {
            BotmuxDispatchState.UNKNOWN,
            BotmuxDispatchState.RUNNING,
            BotmuxDispatchState.COMPLETED,
            BotmuxDispatchState.FAILED,
            BotmuxDispatchState.NOT_FOUND,
        }
    ),
    BotmuxDispatchState.COMPLETED: frozenset({BotmuxDispatchState.COMPLETED}),
    BotmuxDispatchState.FAILED: frozenset({BotmuxDispatchState.FAILED}),
    BotmuxDispatchState.NOT_FOUND: frozenset({BotmuxDispatchState.NOT_FOUND}),
    BotmuxDispatchState.REJECTED: frozenset(
        {
            BotmuxDispatchState.REJECTED,
            BotmuxDispatchState.ATTEMPTING,
        }
    ),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_mapping(value: object) -> PayloadView | None:
    if isinstance(value, Mapping):
        return cast(PayloadView, value)
    return None


def _as_mutable_dict(value: object) -> Payload:
    mapping = _as_mapping(value)
    if mapping is None:
        return {}
    return {str(key): item for key, item in mapping.items()}


def _as_string_dict(value: object) -> Payload | None:
    if not isinstance(value, dict):
        return None
    mapping = cast(Mapping[object, object], value)
    normalized: Payload = {}
    for key, item in mapping.items():
        if not isinstance(key, str):
            return None
        normalized[key] = item
    return normalized


def default_botmux_runtime_binding_path(registry_path: Path) -> Path:
    """Return the local-private binding path beside a project source registry."""
    expanded = registry_path.expanduser().resolve()
    if expanded.parent.name != ".loopx":
        raise ValueError(
            "Botmux Goal Channel runtime binding requires a project source registry"
        )
    return expanded.parent / "goal-channel-runtime.json"


def read_botmux_runtime_binding(path: Path) -> Payload:
    """Read and validate the local-private botmux runtime binding file."""
    binding_path = path.expanduser()
    if not binding_path.exists():
        return {
            "schema_version": BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION,
            "bindings": {},
        }
    raw_payload = cast(object, json.loads(binding_path.read_text(encoding="utf-8")))
    payload = _as_string_dict(raw_payload)
    if (
        payload is None
        or payload.get("schema_version") != BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION
        or not isinstance(payload.get("bindings"), dict)
    ):
        raise ValueError("Botmux Goal Channel runtime binding is invalid")
    return payload


def botmux_binding_for_goal(
    payload: PayloadView,
    goal_id: str,
) -> Payload | None:
    """Return a shallow copy of the binding dictionary for ``goal_id`` if present."""
    bindings = _as_mapping(payload.get("bindings"))
    binding = _as_mapping(bindings.get(goal_id)) if bindings is not None else None
    return dict(binding) if binding is not None else None


def _save_binding(
    *,
    binding_path: Path,
    payload: PayloadView,
    goal_id: str,
    binding: PayloadView,
    writer: AtomicJsonWriter,
) -> None:
    bindings = _as_mapping(payload.get("bindings"))
    mutable: dict[str, object] = dict(bindings) if bindings is not None else {}
    mutable[goal_id] = dict(binding)
    writer(
        binding_path,
        {
            "schema_version": BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION,
            "bindings": mutable,
        },
    )


def _dispatch_state(value: object) -> BotmuxDispatchState | None:
    try:
        return BotmuxDispatchState(str(value or ""))
    except ValueError:
        return None


def _transition_dispatch_receipt(
    receipt: PayloadView | None,
    *,
    state: BotmuxDispatchState,
    timestamp_field: str,
    updates: PayloadView | None = None,
    now_iso: NowIsoFn = _now_iso,
) -> Payload:
    current = (
        _dispatch_state(receipt.get("state")) if receipt is not None else None
    )
    allowed = _DISPATCH_TRANSITIONS.get(current, frozenset())
    if state not in allowed:
        raise ValueError(
            f"invalid botmux dispatch transition: "
            f"{current.value if current else 'none'}->{state.value}"
        )
    transitioned: Payload = dict(receipt or {})
    transitioned["state"] = state.value
    transitioned[timestamp_field] = now_iso()
    if updates:
        transitioned.update(dict(updates))
    return transitioned


def _persist_dispatch_receipt(
    *,
    binding_path: Path,
    goal_id: str,
    binding: Payload,
    dispatch_key: str,
    state: BotmuxDispatchState,
    timestamp_field: str,
    updates: PayloadView | None = None,
    session: PayloadView | None = None,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn = _now_iso,
) -> Payload:
    payload = read_botmux_runtime_binding(binding_path)
    current_binding = botmux_binding_for_goal(payload, goal_id)
    if current_binding is None:
        raise ValueError("botmux runtime binding disappeared during dispatch")
    receipts = _as_mutable_dict(current_binding.get("receipts"))
    receipt = _transition_dispatch_receipt(
        _as_mapping(receipts.get(dispatch_key)),
        state=state,
        timestamp_field=timestamp_field,
        updates=updates,
        now_iso=now_iso,
    )
    receipts[dispatch_key] = receipt
    current_binding["receipts"] = receipts
    if session is not None:
        current_binding["session"] = dict(session)
    binding.clear()
    binding.update(current_binding)
    _save_binding(
        binding_path=binding_path,
        payload=payload,
        goal_id=goal_id,
        binding=current_binding,
        writer=writer,
    )
    return receipt


def _active_dispatch_receipt(
    binding: PayloadView,
) -> tuple[str, Payload] | None:
    receipts = _as_mapping(binding.get("receipts")) or {}
    session = _as_mapping(binding.get("session")) or {}
    active_key = str(session.get("active_dispatch_key") or "").strip()
    active = _as_mapping(receipts.get(active_key)) if active_key else None
    if active_key and active is not None:
        return active_key, dict(active)

    candidates: list[tuple[str, Payload]] = []
    for key, value in receipts.items():
        mapping = _as_mapping(value)
        if mapping is None:
            continue
        if _dispatch_state(mapping.get("state")) in {
            BotmuxDispatchState.ATTEMPTING,
            BotmuxDispatchState.UNKNOWN,
            BotmuxDispatchState.REJECTED,
        }:
            candidates.append((str(key), dict(mapping)))
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: str(
            item[1].get("unknown_at")
            or item[1].get("rejected_at")
            or item[1].get("attempted_at")
            or ""
        ),
    )


def _assert_public_packet(packet: PayloadView) -> None:
    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            mapping = cast(Mapping[object, object], value)
            for key, child in mapping.items():
                if key in _PRIVATE_PACKET_KEYS:
                    raise ValueError(
                        f"public botmux packet contains private key `{key}`"
                    )
                visit(child)
        elif isinstance(value, list):
            items = cast(list[object], value)
            for child in items:
                visit(child)

    visit(packet)


def _operation_packet(
    *,
    ok: bool,
    goal_id: str,
    operation: str,
    execute: bool,
    status: str,
    public_summary: str,
    blocker: str | None = None,
    external_write_performed: bool = False,
    readback_verified: bool = False,
    idempotency_key: str | None = None,
    receipt_id: str | None = None,
    details: PayloadView | None = None,
) -> Payload:
    packet: Payload = {
        "schema_version": BOTMUX_RUNTIME_OPERATION_SCHEMA_VERSION,
        "ok": ok,
        "goal_id": goal_id,
        "provider": BOTMUX_PROVIDER,
        "operation": operation,
        "execute": execute,
        "status": status,
        "external_write_performed": external_write_performed,
        "readback_verified": readback_verified,
        "idempotency_key": idempotency_key,
        "receipt_id": receipt_id,
        "public_summary": public_summary,
        "private_provider_payload_captured": False,
    }
    if blocker:
        packet["blocker"] = blocker
    if details:
        packet["details"] = dict(details)
    _assert_public_packet(packet)
    return packet


def _lock_timeout_packet(
    *,
    goal_id: str,
    operation: str,
    execute: bool,
    error: LockAcquireTimeoutError,
) -> Payload:
    packet = _operation_packet(
        ok=False,
        goal_id=goal_id,
        operation=operation,
        execute=execute,
        status="blocked",
        blocker="botmux_runtime_lock_timeout",
        public_summary=(
            "the botmux runtime binding is busy; inspect the lock holder and retry"
        ),
    )
    packet.update(lock_timeout_error_fields(error))
    _assert_public_packet(packet)
    return packet
