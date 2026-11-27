"""Public facade and lock-fenced entrypoints for the botmux Goal Channel runtime."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ...file_lock import LockAcquireTimeoutError, exclusive_file_lock
from ...public_safe_text import OPAQUE_ID_PATTERN
from .botmux_binding_store import (
    BOTMUX_DEFAULT_ENDPOINT as BOTMUX_DEFAULT_ENDPOINT,
    BOTMUX_DEFAULT_TOKEN_ENV as BOTMUX_DEFAULT_TOKEN_ENV,
    BOTMUX_HTTP_TIMEOUT_SECONDS as BOTMUX_HTTP_TIMEOUT_SECONDS,
    BOTMUX_PROVIDER as BOTMUX_PROVIDER,
    BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION as BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION,
    BOTMUX_RUNTIME_OPERATION_SCHEMA_VERSION as BOTMUX_RUNTIME_OPERATION_SCHEMA_VERSION,
    BotmuxApiError as BotmuxApiError,
    BotmuxDispatchState as BotmuxDispatchState,
    BotmuxTransportError as BotmuxTransportError,
    JsonRequester as JsonRequester,
    Payload as Payload,
    PayloadView as PayloadView,
    _DISPATCH_TRANSITIONS as _DISPATCH_TRANSITIONS,
    _ENV_NAME as _ENV_NAME,
    _PRIVATE_PACKET_KEYS as _PRIVATE_PACKET_KEYS,
    _TERMINAL_DISPATCH_STATES as _TERMINAL_DISPATCH_STATES,
    _active_dispatch_receipt as _active_dispatch_receipt,
    _assert_public_packet as _assert_public_packet,
    _dispatch_state as _dispatch_state,
    _lock_timeout_packet as _lock_timeout_packet,
    _now_iso as _store_now_iso,
    _operation_packet as _operation_packet,
    _persist_dispatch_receipt as _persist_dispatch_receipt,
    _save_binding as _save_binding,
    _transition_dispatch_receipt as _transition_dispatch_receipt,
    botmux_binding_for_goal as botmux_binding_for_goal,
    default_botmux_runtime_binding_path as default_botmux_runtime_binding_path,
    read_botmux_runtime_binding as read_botmux_runtime_binding,
)
from .botmux_lifecycle import (
    _disable_botmux_runtime as _disable_botmux_runtime,
    _dispatch_key as _dispatch_key,
    _doctor_botmux_runtime,
    _goal_from_registry as _goal_from_registry,
    _provider_turn_key as _provider_turn_key,
    _receipt_id as _receipt_id,
    _setup_botmux_runtime as _setup_botmux_runtime,
    build_botmux_loopx_instruction as build_botmux_loopx_instruction,
    goal_runtime_revision as goal_runtime_revision,
)
from .botmux_transport import (
    _NO_REDIRECT_OPENER as _NO_REDIRECT_OPENER,
    _NoRedirectHandler as _NoRedirectHandler,
    _dashboard_auth_for_binding as _dashboard_auth_for_binding,
    _normalized_working_dir as _normalized_working_dir,
    _probe_botmux as _probe_botmux,
    _request_json as _request_json,
    _validated_endpoint as _validated_endpoint,
    _validated_private_id as _validated_private_id,
    _validated_token_env as _validated_token_env,
)
from .botmux_status import _status_botmux_runtime as _status_botmux_runtime
from .botmux_trigger import _trigger_botmux_runtime as _trigger_botmux_runtime

__all__ = [
    "BOTMUX_DEFAULT_ENDPOINT",
    "BOTMUX_DEFAULT_TOKEN_ENV",
    "BOTMUX_HTTP_TIMEOUT_SECONDS",
    "BOTMUX_PROVIDER",
    "BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION",
    "BOTMUX_RUNTIME_OPERATION_SCHEMA_VERSION",
    "BotmuxApiError",
    "BotmuxDispatchState",
    "BotmuxTransportError",
    "JsonRequester",
    "Payload",
    "PayloadView",
    "botmux_binding_for_goal",
    "build_botmux_loopx_instruction",
    "default_botmux_runtime_binding_path",
    "disable_botmux_runtime",
    "doctor_botmux_runtime",
    "goal_runtime_revision",
    "read_botmux_runtime_binding",
    "setup_botmux_runtime",
    "status_botmux_runtime",
    "trigger_botmux_runtime",
]


_SAFE_TOKEN = OPAQUE_ID_PATTERN


def _now_iso() -> str:
    return _store_now_iso()


def _write_private_json_atomic(path: Path, payload: PayloadView) -> None:
    destination = path.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        # Windows has no fchmod; mkstemp already created the file for this user only.
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write(path: Path, payload: PayloadView) -> None:
    _write_private_json_atomic(path, payload)


def _current_iso() -> str:
    return _now_iso()


def _resolve_requester(requester: JsonRequester | None) -> JsonRequester:
    return _request_json if requester is None else requester


def doctor_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    requester: JsonRequester | None = None,
) -> Payload:
    """Verify the active botmux daemon, bot, project cwd, and chat route."""
    active_requester = _resolve_requester(requester)
    try:
        with exclusive_file_lock(binding_path, operation="botmux_runtime_doctor"):
            return _doctor_botmux_runtime(
                goal_id=goal_id,
                binding_path=binding_path,
                requester=active_requester,
            )
    except LockAcquireTimeoutError as exc:
        return _lock_timeout_packet(
            goal_id=goal_id, operation="runtime_doctor", execute=False, error=exc
        )


def setup_botmux_runtime(
    *,
    registry: PayloadView,
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    endpoint: str,
    bot_id: str,
    chat_id: str,
    token_env: str = BOTMUX_DEFAULT_TOKEN_ENV,
    execute: bool = False,
    requester: JsonRequester | None = None,
) -> Payload:
    """Verify and optionally bind a Goal to a botmux bot and chat route."""
    active_requester = _resolve_requester(requester)
    if not execute:
        return _setup_botmux_runtime(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            binding_path=binding_path,
            endpoint=endpoint,
            bot_id=bot_id,
            chat_id=chat_id,
            token_env=token_env,
            execute=False,
            requester=active_requester,
            writer=_atomic_write,
            now_iso=_current_iso,
        )
    with exclusive_file_lock(binding_path, operation="botmux_runtime_setup"):
        return _setup_botmux_runtime(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            binding_path=binding_path,
            endpoint=endpoint,
            bot_id=bot_id,
            chat_id=chat_id,
            token_env=token_env,
            execute=True,
            requester=active_requester,
            writer=_atomic_write,
            now_iso=_current_iso,
        )


def disable_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    execute: bool = False,
) -> Payload:
    """Disable one Goal's botmux runtime binding (preview unless ``execute=True``)."""
    if not execute:
        return _disable_botmux_runtime(
            goal_id=goal_id,
            binding_path=binding_path,
            execute=False,
            writer=_atomic_write,
            now_iso=_current_iso,
        )
    with exclusive_file_lock(binding_path, operation="botmux_runtime_disable"):
        return _disable_botmux_runtime(
            goal_id=goal_id,
            binding_path=binding_path,
            execute=True,
            writer=_atomic_write,
            now_iso=_current_iso,
        )


def trigger_botmux_runtime(
    *,
    registry: PayloadView,
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    instruction: str | None = None,
    turn_key: str | None = None,
    execute: bool = False,
    requester: JsonRequester | None = None,
) -> Payload:
    """Preview or queue one idempotent LoopX turn through the bound botmux runtime."""
    active_requester = _resolve_requester(requester)
    if not execute:
        return _trigger_botmux_runtime(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            binding_path=binding_path,
            instruction=instruction,
            turn_key=turn_key,
            execute=False,
            requester=active_requester,
            writer=_atomic_write,
            now_iso=_current_iso,
        )
    with exclusive_file_lock(binding_path, operation="botmux_runtime_trigger"):
        return _trigger_botmux_runtime(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            binding_path=binding_path,
            instruction=instruction,
            turn_key=turn_key,
            execute=True,
            requester=active_requester,
            writer=_atomic_write,
            now_iso=_current_iso,
        )


def status_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    execute: bool = False,
    requester: JsonRequester | None = None,
) -> Payload:
    """Inspect and optionally persist the current botmux runtime dispatch state."""
    active_requester = _resolve_requester(requester)
    if execute:
        with exclusive_file_lock(binding_path, operation="botmux_runtime_status"):
            return _status_botmux_runtime(
                goal_id=goal_id,
                binding_path=binding_path,
                execute=True,
                requester=active_requester,
                writer=_atomic_write,
                now_iso=_current_iso,
            )
    try:
        with exclusive_file_lock(binding_path, operation="botmux_runtime_status"):
            return _status_botmux_runtime(
                goal_id=goal_id,
                binding_path=binding_path,
                execute=False,
                requester=active_requester,
                writer=_atomic_write,
                now_iso=_current_iso,
            )
    except LockAcquireTimeoutError as exc:
        return _lock_timeout_packet(
            goal_id=goal_id, operation="runtime_status", execute=False, error=exc
        )
