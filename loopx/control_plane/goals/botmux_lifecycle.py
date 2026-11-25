"""Goal lookup, binding setup/doctor/disable, and deterministic turn keys for botmux."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ...paths import registry_project_root
from ...registry import registry_goals
from .botmux_binding_store import (
    AtomicJsonWriter,
    BOTMUX_DEFAULT_TOKEN_ENV,
    BOTMUX_PROVIDER,
    BotmuxApiError,
    BotmuxTransportError,
    JsonRequester,
    NowIsoFn,
    Payload,
    PayloadView,
    _as_mapping,
    _now_iso,
    _operation_packet,
    _save_binding,
    botmux_binding_for_goal,
    read_botmux_runtime_binding,
)
from .botmux_transport import (
    _probe_botmux,
    _validated_endpoint,
    _validated_private_id,
    _validated_token_env,
)


def _goal_from_registry(
    registry: PayloadView,
    goal_id: str,
) -> Payload:
    raw_registry = {str(key): value for key, value in registry.items()}
    for goal in registry_goals(raw_registry):
        if str(goal.get("id") or "") == goal_id:
            normalized: Payload = {}
            for key, value in cast(Mapping[object, object], goal).items():
                normalized[str(key)] = value
            return normalized
    raise ValueError(f"Goal `{goal_id}` is not registered")


def _probe_error_packet(
    *,
    goal_id: str,
    operation: str,
    execute: bool,
    error: Exception,
    invalid_config_summary: str,
) -> Payload:
    if isinstance(error, ValueError):
        blocker = (
            "botmux_auth_unavailable"
            if "token is unavailable" in str(error)
            else "botmux_configuration_invalid"
        )
        summary = invalid_config_summary
    elif isinstance(error, BotmuxApiError):
        blocker = "botmux_api_rejected"
        summary = "botmux rejected the runtime readiness probe"
    else:
        blocker = "botmux_unreachable"
        summary = "the configured botmux daemon is unreachable"
    return _operation_packet(
        ok=False,
        goal_id=goal_id,
        operation=operation,
        execute=execute,
        status="blocked",
        blocker=blocker,
        public_summary=summary,
    )


def _setup_botmux_runtime(
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
    requester: JsonRequester,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn = _now_iso,
) -> Payload:
    _goal_from_registry(registry, goal_id)
    payload = read_botmux_runtime_binding(binding_path)
    prior: Payload = botmux_binding_for_goal(payload, goal_id) or {}
    endpoint = _validated_endpoint(endpoint)
    bot_id = _validated_private_id(bot_id, "bot id")
    chat_id = _validated_private_id(chat_id, "chat id")
    token_env = _validated_token_env(token_env)
    expected_working_dir = registry_project_root(registry_path)
    same_route = bool(
        prior.get("endpoint") == endpoint
        and prior.get("bot_id") == bot_id
        and prior.get("chat_id") == chat_id
        and prior.get("token_env") == token_env
        and prior.get("expected_working_dir") == str(expected_working_dir)
    )
    prior_session = _as_mapping(prior.get("session"))
    prior_receipts = _as_mapping(prior.get("receipts"))
    binding: Payload = {
        "goal_id": goal_id,
        "provider": BOTMUX_PROVIDER,
        "enabled": True,
        "endpoint": endpoint,
        "bot_id": bot_id,
        "chat_id": chat_id,
        "token_env": token_env,
        "expected_working_dir": str(expected_working_dir),
        "session": dict(prior_session) if same_route and prior_session else {},
        "receipts": dict(prior_receipts) if same_route and prior_receipts else {},
    }
    try:
        probe = _probe_botmux(binding=binding, requester=requester)
    except (ValueError, BotmuxApiError, BotmuxTransportError) as exc:
        return _probe_error_packet(
            goal_id=goal_id,
            operation="runtime_setup",
            execute=execute,
            error=exc,
            invalid_config_summary="the botmux runtime binding could not be verified",
        )
    if execute:
        binding["verified_at"] = now_iso()
        _save_binding(
            binding_path=binding_path,
            payload=payload,
            goal_id=goal_id,
            binding=binding,
            writer=writer,
        )
    return _operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="runtime_setup",
        execute=execute,
        status="configured" if execute else "preview_ready",
        public_summary=(
            "configured the verified botmux runtime binding"
            if execute
            else "the botmux runtime binding is ready to configure"
        ),
        readback_verified=bool(execute),
        details=probe,
    )


def _doctor_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    requester: JsonRequester,
) -> Payload:
    binding = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path),
        goal_id,
    )
    if binding is None or binding.get("enabled") is not True:
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_doctor",
            execute=False,
            status="blocked",
            blocker="botmux_binding_missing",
            public_summary="configure a botmux runtime binding for this goal",
        )
    try:
        probe = _probe_botmux(binding=binding, requester=requester)
    except (ValueError, BotmuxApiError, BotmuxTransportError) as exc:
        return _probe_error_packet(
            goal_id=goal_id,
            operation="runtime_doctor",
            execute=False,
            error=exc,
            invalid_config_summary="the botmux runtime binding is not ready",
        )
    return _operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="runtime_doctor",
        execute=False,
        status="ready",
        public_summary="the botmux runtime binding is ready",
        readback_verified=True,
        details=probe,
    )


def _disable_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    execute: bool = False,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn = _now_iso,
) -> Payload:
    payload = read_botmux_runtime_binding(binding_path)
    binding = botmux_binding_for_goal(payload, goal_id)
    if binding is None:
        return _operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="runtime_disable",
            execute=execute,
            status="not_configured",
            public_summary="no botmux runtime binding is configured for this goal",
        )
    if execute:
        binding["enabled"] = False
        binding["session"] = {}
        binding["disabled_at"] = now_iso()
        _save_binding(
            binding_path=binding_path,
            payload=payload,
            goal_id=goal_id,
            binding=binding,
            writer=writer,
        )
    return _operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="runtime_disable",
        execute=execute,
        status="disabled" if execute else "preview_ready",
        public_summary=(
            "disabled the botmux runtime binding for this goal"
            if execute
            else "the botmux runtime binding is ready to disable"
        ),
        readback_verified=bool(execute),
    )


def build_botmux_loopx_instruction(goal_id: str) -> str:
    """Build the default LoopX continuation prompt dispatched to botmux."""
    return (
        f"Continue the active LoopX goal `{goal_id}` in the current project using "
        "the installed LoopX CLI or managed skill. Read canonical goal state and "
        "execute the active next action. Complete a coherent artifact, targeted "
        "validation, and state writeback. Continue the next bounded segment while "
        "no explicit gate, quota, authority, or production access blocker applies. "
        "When stopping, report the exact stop reason and a pasteable resume command."
    )


def goal_runtime_revision(
    *,
    registry: PayloadView,
    registry_path: Path,
    goal_id: str,
) -> str:
    """Derive a short digest representing the current persisted Goal state file."""
    goal = _goal_from_registry(registry, goal_id)
    raw_state_path = str(goal.get("state_file") or "").strip()
    state_bytes = b""
    if raw_state_path:
        state_path = Path(raw_state_path).expanduser()
        if not state_path.is_absolute():
            state_path = registry_project_root(registry_path) / state_path
        try:
            state_bytes = state_path.read_bytes()
        except OSError:
            state_bytes = b""
    digest = hashlib.sha256(
        goal_id.encode("utf-8") + b"\0" + state_bytes
    ).hexdigest()
    return digest[:24]


def _dispatch_key(*, goal_id: str, revision: str, instruction: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            [goal_id, revision, instruction],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _provider_turn_key(dispatch_key: str) -> str:
    return f"loopx-{dispatch_key.removeprefix('sha256:')[:32]}"


def _receipt_id(dispatch_key: str) -> str:
    return f"runtime_{dispatch_key.removeprefix('sha256:')[:16]}"
