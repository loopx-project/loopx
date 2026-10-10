from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ...file_lock import LockAcquireTimeoutError, exclusive_file_lock
from ...file_lock import lock_timeout_error_fields
from ...paths import registry_project_root
from ...public_safe_text import OPAQUE_ID_PATTERN as _SAFE_TOKEN
from ...registry import registry_goals


BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION = "loopx_goal_channel_botmux_binding_v0"
BOTMUX_RUNTIME_OPERATION_SCHEMA_VERSION = "loopx_goal_channel_runtime_operation_v0"
BOTMUX_PROVIDER = "botmux"
BOTMUX_DEFAULT_ENDPOINT = "http://127.0.0.1:7891"
BOTMUX_DEFAULT_TOKEN_ENV = "BOTMUX_DASHBOARD_TOKEN"
BOTMUX_HTTP_TIMEOUT_SECONDS = 4.0

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PRIVATE_PACKET_KEYS = {
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

JsonRequester = Callable[..., dict[str, Any]]


class BotmuxApiError(RuntimeError):
    def __init__(self, status: int, payload: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"botmux API returned HTTP {status}")
        self.status = status
        self.payload = dict(payload or {})


class BotmuxTransportError(RuntimeError):
    pass


class BotmuxDispatchState(str, Enum):
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


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def default_botmux_runtime_binding_path(registry_path: Path) -> Path:
    expanded = registry_path.expanduser().resolve()
    if expanded.parent.name != ".loopx":
        raise ValueError(
            "Botmux Goal Channel runtime binding requires a project source registry"
        )
    return expanded.parent / "goal-channel-runtime.json"


def _write_private_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
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


def read_botmux_runtime_binding(path: Path) -> dict[str, Any]:
    binding_path = path.expanduser()
    if not binding_path.exists():
        return {
            "schema_version": BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION,
            "bindings": {},
        }
    payload = json.loads(binding_path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION
        or not isinstance(payload.get("bindings"), dict)
    ):
        raise ValueError("Botmux Goal Channel runtime binding is invalid")
    return payload


def botmux_binding_for_goal(
    payload: Mapping[str, Any],
    goal_id: str,
) -> dict[str, Any] | None:
    bindings = payload.get("bindings")
    binding = bindings.get(goal_id) if isinstance(bindings, Mapping) else None
    return dict(binding) if isinstance(binding, Mapping) else None


def _save_binding(
    *,
    binding_path: Path,
    payload: Mapping[str, Any],
    goal_id: str,
    binding: Mapping[str, Any],
) -> None:
    bindings = payload.get("bindings")
    mutable = dict(bindings) if isinstance(bindings, Mapping) else {}
    mutable[goal_id] = dict(binding)
    _write_private_json_atomic(
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
    receipt: Mapping[str, Any] | None,
    *,
    state: BotmuxDispatchState,
    timestamp_field: str,
    updates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current = _dispatch_state(receipt.get("state")) if isinstance(receipt, Mapping) else None
    allowed = _DISPATCH_TRANSITIONS.get(current, frozenset())
    if state not in allowed:
        raise ValueError(
            f"invalid botmux dispatch transition: "
            f"{current.value if current else 'none'}->{state.value}"
        )
    transitioned = dict(receipt or {})
    transitioned["state"] = state.value
    transitioned[timestamp_field] = _now_iso()
    if updates:
        transitioned.update(dict(updates))
    return transitioned


def _persist_dispatch_receipt(
    *,
    binding_path: Path,
    goal_id: str,
    binding: dict[str, Any],
    dispatch_key: str,
    state: BotmuxDispatchState,
    timestamp_field: str,
    updates: Mapping[str, Any] | None = None,
    session: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = read_botmux_runtime_binding(binding_path)
    current_binding = botmux_binding_for_goal(payload, goal_id)
    if current_binding is None:
        raise ValueError("botmux runtime binding disappeared during dispatch")
    receipts = dict(current_binding.get("receipts") or {})
    receipt = _transition_dispatch_receipt(
        receipts.get(dispatch_key)
        if isinstance(receipts.get(dispatch_key), Mapping)
        else None,
        state=state,
        timestamp_field=timestamp_field,
        updates=updates,
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
    )
    return receipt


def _active_dispatch_receipt(
    binding: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    receipts = binding.get("receipts")
    receipts = receipts if isinstance(receipts, Mapping) else {}
    session = binding.get("session")
    session = session if isinstance(session, Mapping) else {}
    active_key = str(session.get("active_dispatch_key") or "").strip()
    active = receipts.get(active_key) if active_key else None
    if active_key and isinstance(active, Mapping):
        return active_key, dict(active)

    candidates = [
        (str(key), dict(value))
        for key, value in receipts.items()
        if isinstance(value, Mapping)
        and _dispatch_state(value.get("state"))
        in {
            BotmuxDispatchState.ATTEMPTING,
            BotmuxDispatchState.UNKNOWN,
            BotmuxDispatchState.REJECTED,
        }
    ]
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


def _goal_from_registry(
    registry: Mapping[str, Any],
    goal_id: str,
) -> dict[str, Any]:
    match = next(
        (
            dict(goal)
            for goal in registry_goals(dict(registry))
            if str(goal.get("id") or "") == goal_id
        ),
        None,
    )
    if match is None:
        raise ValueError(f"Goal `{goal_id}` is not registered")
    return match


def _validated_endpoint(value: str) -> str:
    endpoint = str(value or "").strip().rstrip("/")
    parsed = urlparse(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("botmux endpoint must be one HTTP(S) origin")
    if parsed.scheme == "http":
        hostname = str(parsed.hostname or "").lower()
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname == "localhost"
        if not is_loopback:
            raise ValueError("remote botmux endpoint must use HTTPS")
    return endpoint


def _validated_token_env(value: str) -> str:
    token_env = str(value or "").strip()
    if not _ENV_NAME.fullmatch(token_env):
        raise ValueError("botmux token env must be one environment variable name")
    return token_env


def _validated_private_id(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_TOKEN.fullmatch(normalized):
        raise ValueError(f"botmux {label} is invalid")
    return normalized


def _normalized_working_dir(value: str) -> Path:
    working_dir = Path(str(value or "").strip()).expanduser()
    if not str(value or "").strip() or not working_dir.is_absolute():
        raise ValueError("botmux working directory must be absolute")
    return working_dir.resolve()


def _dashboard_auth_for_binding(binding: Mapping[str, Any]) -> str:
    token_env = _validated_token_env(str(binding.get("token_env") or ""))
    token = str(os.environ.get(token_env) or "").strip()
    if not token or any(ord(character) <= 32 for character in token) or ";" in token:
        raise ValueError("botmux dashboard token is unavailable")
    return token


def _request_json(
    *,
    endpoint: str,
    path: str,
    method: str = "GET",
    dashboard_auth: str | None = None,
    payload: Mapping[str, Any] | None = None,
    timeout_seconds: float = BOTMUX_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    body = (
        json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if payload is not None
        else None
    )
    headers = {"accept": "application/json"}
    if body is not None:
        headers["content-type"] = "application/json"
    if dashboard_auth:
        headers["cookie"] = f"botmux_dashboard_token={dashboard_auth}"
    request = Request(
        f"{endpoint}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with _NO_REDIRECT_OPENER.open(  # noqa: S310 - endpoint is explicit local-private operator configuration and redirects are disabled.
            request,
            timeout=timeout_seconds,
        ) as response:
            raw = response.read()
    except HTTPError as exc:
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_payload = {}
        raise BotmuxApiError(
            exc.code,
            error_payload if isinstance(error_payload, Mapping) else {},
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BotmuxTransportError("botmux transport failed") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BotmuxTransportError("botmux returned a non-JSON response") from exc
    if not isinstance(parsed, dict):
        raise BotmuxTransportError("botmux returned an invalid JSON response")
    return parsed


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
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    packet: dict[str, Any] = {
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


def _assert_public_packet(packet: Mapping[str, Any]) -> None:
    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in _PRIVATE_PACKET_KEYS:
                    raise ValueError(
                        f"public botmux packet contains private key `{key}`"
                    )
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(packet)


def _lock_timeout_packet(
    *, goal_id: str, operation: str, execute: bool, error: LockAcquireTimeoutError
) -> dict[str, Any]:
    packet = _operation_packet(
        ok=False,
        goal_id=goal_id,
        operation=operation,
        execute=execute,
        status="blocked",
        blocker="botmux_runtime_lock_timeout",
        public_summary="the botmux runtime binding is busy; inspect the lock holder and retry",
    )
    packet.update(lock_timeout_error_fields(error))
    _assert_public_packet(packet)
    return packet


def _probe_botmux(
    *,
    binding: Mapping[str, Any],
    requester: JsonRequester,
) -> dict[str, Any]:
    endpoint = _validated_endpoint(str(binding.get("endpoint") or ""))
    bot_id = _validated_private_id(str(binding.get("bot_id") or ""), "bot id")
    chat_id = _validated_private_id(str(binding.get("chat_id") or ""), "chat id")
    expected_working_dir = _normalized_working_dir(
        str(binding.get("expected_working_dir") or "")
    )
    dashboard_auth = _dashboard_auth_for_binding(binding)
    health = requester(endpoint=endpoint, path="/__health")
    if health.get("ok") is not True:
        raise ValueError("botmux health probe failed")
    bots_payload = requester(
        endpoint=endpoint,
        path="/api/bots",
        dashboard_auth=dashboard_auth,
    )
    bots = bots_payload.get("bots")
    bots = bots if isinstance(bots, list) else []
    selected = next(
        (
            dict(item)
            for item in bots
            if isinstance(item, Mapping)
            and str(item.get("larkAppId") or "") == bot_id
        ),
        None,
    )
    if selected is None:
        raise ValueError("configured botmux bot is not online")
    if selected.get("online") is not True:
        raise ValueError("configured botmux bot is offline")
    groups_payload = requester(
        endpoint=endpoint,
        path="/api/groups",
        dashboard_auth=dashboard_auth,
    )
    chats = groups_payload.get("chats")
    chats = chats if isinstance(chats, list) else []
    selected_chat = next(
        (
            dict(item)
            for item in chats
            if isinstance(item, Mapping)
            and str(item.get("chatId") or "") == chat_id
        ),
        None,
    )
    if selected_chat is None:
        raise ValueError("configured botmux chat is not visible")
    member_bots = selected_chat.get("memberBots")
    member_bots = member_bots if isinstance(member_bots, list) else []
    selected_member = next(
        (
            dict(item)
            for item in member_bots
            if isinstance(item, Mapping)
            and str(item.get("larkAppId") or "") == bot_id
            and item.get("inChat") is True
        ),
        None,
    )
    if selected_member is None:
        raise ValueError("configured botmux bot is not in the selected chat")
    oncall = selected_member.get("oncallChat")
    oncall = oncall if isinstance(oncall, Mapping) else {}
    configured_working_dirs = {
        _normalized_working_dir(value)
        for value in (
            str(selected.get("defaultWorkingDir") or ""),
            str(oncall.get("workingDir") or ""),
        )
        if value
    }
    if expected_working_dir not in configured_working_dirs:
        raise ValueError(
            "configured botmux bot or chat does not bind the LoopX project directory"
        )
    route = requester(
        endpoint=endpoint,
        path="/api/trigger",
        method="POST",
        dashboard_auth=dashboard_auth,
        payload={
            "source": {"type": "webhook"},
            "target": {
                "kind": "turn",
                "botId": bot_id,
                "chatId": chat_id,
            },
            "instruction": "LoopX botmux route readiness probe.",
            "envelope": {
                "format": "json",
                "sourceName": "loopx",
                "trusted": False,
            },
            "options": {"dryRun": True},
        },
    )
    route_verified = bool(
        route.get("ok") is True and str(route.get("action") or "") == "dry_run"
    )
    if not route_verified:
        raise ValueError("botmux did not verify the configured chat route")
    return {
        "health_verified": True,
        "bot_online": True,
        "project_working_dir_verified": True,
        "chat_route_verified": True,
    }


def _setup_botmux_runtime(
    *,
    registry: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    endpoint: str,
    bot_id: str,
    chat_id: str,
    token_env: str = BOTMUX_DEFAULT_TOKEN_ENV,
    execute: bool = False,
    requester: JsonRequester = _request_json,
) -> dict[str, Any]:
    _goal_from_registry(registry, goal_id)
    payload = read_botmux_runtime_binding(binding_path)
    prior = botmux_binding_for_goal(payload, goal_id) or {}
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
    binding = {
        "goal_id": goal_id,
        "provider": BOTMUX_PROVIDER,
        "enabled": True,
        "endpoint": endpoint,
        "bot_id": bot_id,
        "chat_id": chat_id,
        "token_env": token_env,
        "expected_working_dir": str(expected_working_dir),
        "session": dict(prior.get("session") or {}) if same_route else {},
        "receipts": dict(prior.get("receipts") or {}) if same_route else {},
    }
    try:
        probe = _probe_botmux(binding=binding, requester=requester)
    except ValueError as exc:
        blocker = (
            "botmux_auth_unavailable"
            if "token is unavailable" in str(exc)
            else "botmux_configuration_invalid"
        )
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_setup",
            execute=execute,
            status="blocked",
            blocker=blocker,
            public_summary="the botmux runtime binding could not be verified",
        )
    except BotmuxApiError:
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_setup",
            execute=execute,
            status="blocked",
            blocker="botmux_api_rejected",
            public_summary="botmux rejected the runtime readiness probe",
        )
    except BotmuxTransportError:
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_setup",
            execute=execute,
            status="blocked",
            blocker="botmux_unreachable",
            public_summary="the configured botmux daemon is unreachable",
        )
    if execute:
        binding["verified_at"] = _now_iso()
        _save_binding(
            binding_path=binding_path,
            payload=payload,
            goal_id=goal_id,
            binding=binding,
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


def doctor_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    requester: JsonRequester = _request_json,
) -> dict[str, Any]:
    try:
        with exclusive_file_lock(binding_path, operation="botmux_runtime_doctor"):
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
                if isinstance(exc, ValueError):
                    blocker = (
                        "botmux_auth_unavailable"
                        if "token is unavailable" in str(exc)
                        else "botmux_configuration_invalid"
                    )
                    summary = "the botmux runtime binding is not ready"
                elif isinstance(exc, BotmuxApiError):
                    blocker = "botmux_api_rejected"
                    summary = "botmux rejected the runtime readiness probe"
                else:
                    blocker = "botmux_unreachable"
                    summary = "the configured botmux daemon is unreachable"
                return _operation_packet(
                    ok=False,
                    goal_id=goal_id,
                    operation="runtime_doctor",
                    execute=False,
                    status="blocked",
                    blocker=blocker,
                    public_summary=summary,
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
    except LockAcquireTimeoutError as exc:
        return _lock_timeout_packet(
            goal_id=goal_id, operation="runtime_doctor", execute=False, error=exc
        )


def _disable_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    execute: bool = False,
) -> dict[str, Any]:
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
        binding["disabled_at"] = _now_iso()
        _save_binding(
            binding_path=binding_path,
            payload=payload,
            goal_id=goal_id,
            binding=binding,
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
    registry: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
) -> str:
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


def _trigger_botmux_runtime(
    *,
    registry: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    instruction: str | None = None,
    turn_key: str | None = None,
    execute: bool = False,
    requester: JsonRequester = _request_json,
) -> dict[str, Any]:
    payload = read_botmux_runtime_binding(binding_path)
    binding = botmux_binding_for_goal(payload, goal_id)
    if binding is None or binding.get("enabled") is not True:
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=execute,
            status="blocked",
            blocker="botmux_binding_missing",
            public_summary="configure a botmux runtime binding for this goal",
        )
    selected_instruction = str(
        instruction or build_botmux_loopx_instruction(goal_id)
    ).strip()
    if not selected_instruction:
        raise ValueError("botmux runtime instruction must not be empty")
    revision = str(
        turn_key
        or goal_runtime_revision(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
        )
    )
    dispatch_key = _dispatch_key(
        goal_id=goal_id,
        revision=revision,
        instruction=selected_instruction,
    )
    receipts = dict(binding.get("receipts") or {})
    prior = receipts.get(dispatch_key)
    prior_state = (
        _dispatch_state(prior.get("state"))
        if isinstance(prior, Mapping)
        else None
    )
    if prior_state in {
        BotmuxDispatchState.ATTEMPTING,
        BotmuxDispatchState.QUEUED,
        BotmuxDispatchState.RUNNING,
        BotmuxDispatchState.COMPLETED,
        BotmuxDispatchState.FAILED,
        BotmuxDispatchState.NOT_FOUND,
        BotmuxDispatchState.UNKNOWN,
    }:
        return _operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=execute,
            status="already_dispatched",
            public_summary="the same botmux runtime turn was already dispatched",
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
            details={
                "dispatch_state": prior_state.value,
                "session_reused": True,
            },
        )
    session = dict(binding.get("session") or {})
    existing_session_id = str(session.get("session_id") or "").strip()
    if not execute:
        return _operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=False,
            status="preview_ready",
            public_summary="the next LoopX turn is ready for botmux dispatch",
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
            details={
                "dispatch_state": "preview",
                "session_reused": bool(existing_session_id),
            },
        )
    endpoint = _validated_endpoint(str(binding.get("endpoint") or ""))
    bot_id = _validated_private_id(str(binding.get("bot_id") or ""), "bot id")
    dashboard_auth = _dashboard_auth_for_binding(binding)
    target: dict[str, Any] = {"kind": "turn", "botId": bot_id}
    options: dict[str, Any] = {"asyncReturnSessionId": True}
    if existing_session_id:
        target["sessionId"] = _validated_private_id(
            existing_session_id,
            "session id",
        )
        options["turnIdempotencyKey"] = _provider_turn_key(dispatch_key)
    else:
        target["chatId"] = _validated_private_id(
            str(binding.get("chat_id") or ""),
            "chat id",
        )
    _persist_dispatch_receipt(
        binding_path=binding_path,
        goal_id=goal_id,
        binding=binding,
        dispatch_key=dispatch_key,
        state=BotmuxDispatchState.ATTEMPTING,
        timestamp_field="attempted_at",
        updates={"session_reused": bool(existing_session_id)},
        session={
            **(
                {"session_id": existing_session_id}
                if existing_session_id
                else {}
            ),
            "active_dispatch_key": dispatch_key,
            "updated_at": _now_iso(),
        },
    )
    try:
        response = requester(
            endpoint=endpoint,
            path="/api/trigger",
            method="POST",
            dashboard_auth=dashboard_auth,
            payload={
                "source": {"type": "webhook"},
                "target": target,
                "instruction": selected_instruction,
                "envelope": {
                    "format": "json",
                    "sourceName": "loopx",
                    "trusted": False,
                },
                "options": options,
            },
        )
    except BotmuxTransportError:
        _persist_dispatch_receipt(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            dispatch_key=dispatch_key,
            state=BotmuxDispatchState.UNKNOWN,
            timestamp_field="unknown_at",
            updates={"unknown_reason": "transport_failure"},
        )
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=True,
            status="dispatch_unknown",
            blocker="botmux_dispatch_outcome_unknown",
            public_summary=(
                "botmux dispatch outcome is unknown; inspect the existing receipt "
                "before retrying"
            ),
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
        )
    except BotmuxApiError as exc:
        _persist_dispatch_receipt(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            dispatch_key=dispatch_key,
            state=BotmuxDispatchState.REJECTED,
            timestamp_field="rejected_at",
            updates={"http_status": exc.status},
        )
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=True,
            status="rejected",
            blocker="botmux_api_rejected",
            public_summary="botmux rejected the LoopX runtime turn",
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
        )
    response_target = response.get("target")
    response_target = (
        response_target if isinstance(response_target, Mapping) else {}
    )
    async_payload = response.get("async")
    async_payload = async_payload if isinstance(async_payload, Mapping) else {}
    session_id = str(
        response_target.get("sessionId") or async_payload.get("sessionId") or ""
    ).strip()
    if response.get("ok") is not True or not session_id:
        _persist_dispatch_receipt(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            dispatch_key=dispatch_key,
            state=BotmuxDispatchState.UNKNOWN,
            timestamp_field="unknown_at",
            updates={"unknown_reason": "invalid_provider_receipt"},
        )
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=True,
            status="dispatch_unknown",
            blocker="botmux_dispatch_receipt_invalid",
            public_summary="botmux did not return a verifiable runtime session receipt",
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
        )
    _persist_dispatch_receipt(
        binding_path=binding_path,
        goal_id=goal_id,
        binding=binding,
        dispatch_key=dispatch_key,
        state=BotmuxDispatchState.QUEUED,
        timestamp_field="queued_at",
        updates={
            "session_id": session_id,
            "trigger_id": str(response.get("triggerId") or ""),
        },
        session={
            "session_id": session_id,
            "active_dispatch_key": dispatch_key,
            "updated_at": _now_iso(),
        },
    )
    return _operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="runtime_trigger",
        execute=True,
        status="queued",
        public_summary="queued one LoopX turn through the botmux runtime",
        external_write_performed=True,
        idempotency_key=dispatch_key,
        receipt_id=_receipt_id(dispatch_key),
        details={"dispatch_state": "queued", "session_reused": bool(existing_session_id)},
    )


def _status_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    execute: bool = False,
    requester: JsonRequester = _request_json,
) -> dict[str, Any]:
    payload = read_botmux_runtime_binding(binding_path)
    binding = botmux_binding_for_goal(payload, goal_id)
    if binding is None:
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_status",
            execute=execute,
            status="blocked",
            blocker="botmux_binding_missing",
            public_summary="configure a botmux runtime binding for this goal",
        )
    session = dict(binding.get("session") or {})
    session_id = str(session.get("session_id") or "").strip()
    dispatch_key = str(session.get("active_dispatch_key") or "").strip()
    if not session_id or not dispatch_key:
        active = _active_dispatch_receipt(binding)
        if active is not None:
            active_key, receipt = active
            receipt_state = _dispatch_state(receipt.get("state"))
            if receipt_state == BotmuxDispatchState.ATTEMPTING:
                receipt_state = BotmuxDispatchState.UNKNOWN
                if execute:
                    _persist_dispatch_receipt(
                        binding_path=binding_path,
                        goal_id=goal_id,
                        binding=binding,
                        dispatch_key=active_key,
                        state=BotmuxDispatchState.UNKNOWN,
                        timestamp_field="unknown_at",
                        updates={"unknown_reason": "unresolved_attempt"},
                    )
            if receipt_state in {
                BotmuxDispatchState.UNKNOWN,
                BotmuxDispatchState.REJECTED,
            }:
                public_state = receipt_state.value
                return _operation_packet(
                    ok=False,
                    goal_id=goal_id,
                    operation="runtime_status",
                    execute=execute,
                    status=public_state,
                    blocker=(
                        "botmux_dispatch_outcome_unknown"
                        if receipt_state == BotmuxDispatchState.UNKNOWN
                        else "botmux_api_rejected"
                    ),
                    public_summary=(
                        "the botmux runtime dispatch outcome is unknown"
                        if receipt_state == BotmuxDispatchState.UNKNOWN
                        else "the botmux runtime dispatch was rejected"
                    ),
                    idempotency_key=active_key,
                    receipt_id=_receipt_id(active_key),
                    details={
                        "dispatch_state": public_state,
                        "output_available": False,
                        "local_receipt_updated": bool(
                            execute
                            and _dispatch_state(receipt.get("state"))
                            == BotmuxDispatchState.ATTEMPTING
                        ),
                    },
                )
        return _operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="runtime_status",
            execute=execute,
            status="idle",
            public_summary="no botmux runtime turn has been dispatched for this goal",
            details={"dispatch_state": "idle", "output_available": False},
        )
    try:
        result = requester(
            endpoint=_validated_endpoint(str(binding.get("endpoint") or "")),
            path=(
                "/api/sessions/"
                f"{quote(_validated_private_id(session_id, 'session id'), safe='')}"
                "/trigger-result"
            ),
            dashboard_auth=_dashboard_auth_for_binding(binding),
        )
    except ValueError:
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_status",
            execute=execute,
            status="blocked",
            blocker="botmux_auth_unavailable",
            public_summary="the botmux runtime status cannot be read without auth",
        )
    except BotmuxApiError as exc:
        if exc.status == 404 and (
            exc.payload.get("error") == "unknown_session"
            or exc.payload.get("state") == "not_found"
        ):
            result = {"state": "not_found"}
        else:
            return _operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="runtime_status",
                execute=execute,
                status="blocked",
                blocker="botmux_api_rejected",
                public_summary="botmux rejected the runtime status read",
            )
    except BotmuxTransportError:
        if execute:
            receipts = binding.get("receipts")
            receipts = receipts if isinstance(receipts, Mapping) else {}
            receipt = receipts.get(dispatch_key)
            current_state = (
                _dispatch_state(receipt.get("state"))
                if isinstance(receipt, Mapping)
                else None
            )
            if current_state in {
                BotmuxDispatchState.QUEUED,
                BotmuxDispatchState.RUNNING,
                BotmuxDispatchState.UNKNOWN,
            }:
                _persist_dispatch_receipt(
                    binding_path=binding_path,
                    goal_id=goal_id,
                    binding=binding,
                    dispatch_key=dispatch_key,
                    state=BotmuxDispatchState.UNKNOWN,
                    timestamp_field="unknown_at",
                    updates={"unknown_reason": "status_transport_failure"},
                )
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_status",
            execute=execute,
            status="unknown",
            blocker="botmux_unreachable",
            public_summary="the botmux runtime status is temporarily unknown",
        )
    provider_state = _dispatch_state(result.get("state"))
    if provider_state not in {
        BotmuxDispatchState.RUNNING,
        BotmuxDispatchState.COMPLETED,
        BotmuxDispatchState.FAILED,
        BotmuxDispatchState.NOT_FOUND,
    }:
        provider_state = BotmuxDispatchState.UNKNOWN
    latest_payload = read_botmux_runtime_binding(binding_path)
    latest_binding = botmux_binding_for_goal(latest_payload, goal_id) or binding
    latest_receipts = latest_binding.get("receipts")
    latest_receipts = (
        latest_receipts if isinstance(latest_receipts, Mapping) else {}
    )
    latest_receipt = latest_receipts.get(dispatch_key)
    local_state = (
        _dispatch_state(latest_receipt.get("state"))
        if isinstance(latest_receipt, Mapping)
        else None
    )
    provider_observation_ignored = (
        local_state in _TERMINAL_DISPATCH_STATES
        and provider_state != local_state
    )
    state = local_state if provider_observation_ignored else provider_state
    output = result.get("output")
    output_available = bool(
        isinstance(output, Mapping) and str(output.get("content") or "").strip()
    )
    local_receipt_updated = execute
    if execute:
        updates = None
        if provider_observation_ignored:
            updates = {
                "provider_observation": provider_state.value,
                "provider_observation_ignored": True,
            }
        _persist_dispatch_receipt(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            dispatch_key=dispatch_key,
            state=state,
            timestamp_field="status_checked_at",
            updates=updates,
        )
    details: dict[str, Any] = {
        "dispatch_state": state.value,
        "output_available": output_available,
        "local_receipt_updated": local_receipt_updated,
    }
    if provider_observation_ignored:
        details.update(
            {
                "provider_observation": provider_state.value,
                "provider_observation_ignored": True,
            }
        )
    return _operation_packet(
        ok=state != BotmuxDispatchState.UNKNOWN,
        goal_id=goal_id,
        operation="runtime_status",
        execute=execute,
        status=state.value,
        blocker=(
            "botmux_status_unknown"
            if state == BotmuxDispatchState.UNKNOWN
            else None
        ),
        public_summary=f"the botmux runtime turn is {state.value}",
        readback_verified=state == BotmuxDispatchState.COMPLETED,
        idempotency_key=dispatch_key,
        receipt_id=_receipt_id(dispatch_key),
        details=details,
    )


def setup_botmux_runtime(
    *,
    registry: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    endpoint: str,
    bot_id: str,
    chat_id: str,
    token_env: str = BOTMUX_DEFAULT_TOKEN_ENV,
    execute: bool = False,
    requester: JsonRequester = _request_json,
) -> dict[str, Any]:
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
            requester=requester,
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
            requester=requester,
        )


def disable_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    execute: bool = False,
) -> dict[str, Any]:
    if not execute:
        return _disable_botmux_runtime(
            goal_id=goal_id,
            binding_path=binding_path,
            execute=False,
        )
    with exclusive_file_lock(binding_path, operation="botmux_runtime_disable"):
        return _disable_botmux_runtime(
            goal_id=goal_id,
            binding_path=binding_path,
            execute=True,
        )


def trigger_botmux_runtime(
    *,
    registry: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    instruction: str | None = None,
    turn_key: str | None = None,
    execute: bool = False,
    requester: JsonRequester = _request_json,
) -> dict[str, Any]:
    if not execute:
        return _trigger_botmux_runtime(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            binding_path=binding_path,
            instruction=instruction,
            turn_key=turn_key,
            execute=False,
            requester=requester,
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
            requester=requester,
        )


def status_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    execute: bool = False,
    requester: JsonRequester = _request_json,
) -> dict[str, Any]:
    if execute:
        with exclusive_file_lock(binding_path, operation="botmux_runtime_status"):
            return _status_botmux_runtime(
                goal_id=goal_id,
                binding_path=binding_path,
                execute=True,
                requester=requester,
            )
    try:
        with exclusive_file_lock(binding_path, operation="botmux_runtime_status"):
            return _status_botmux_runtime(
                goal_id=goal_id,
                binding_path=binding_path,
                execute=False,
                requester=requester,
            )
    except LockAcquireTimeoutError as exc:
        return _lock_timeout_packet(
            goal_id=goal_id, operation="runtime_status", execute=False, error=exc
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
