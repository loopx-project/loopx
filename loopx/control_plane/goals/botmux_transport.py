"""HTTP transport, SSRF/config validation, and readiness probing for botmux."""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Mapping
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ...public_safe_text import OPAQUE_ID_PATTERN as _SAFE_TOKEN
from .botmux_binding_store import (
    BOTMUX_HTTP_TIMEOUT_SECONDS,
    BotmuxApiError,
    BotmuxTransportError,
    JsonRequester,
    Payload,
    PayloadView,
    _ENV_NAME,
    _as_mapping,
    _as_string_dict,
)


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse HTTP redirects so loopback/HTTPS trust stays pinned."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


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


def _dashboard_auth_for_binding(binding: PayloadView) -> str:
    token_env = _validated_token_env(str(binding.get("token_env") or ""))
    token = str(os.environ.get(token_env) or "").strip()
    if not token or any(ord(character) <= 32 for character in token) or ";" in token:
        raise ValueError("botmux dashboard token is unavailable")
    return token


def _decode_json_object(raw: bytes) -> Payload:
    try:
        parsed = cast(object, json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BotmuxTransportError("botmux returned a non-JSON response") from exc
    payload = _as_string_dict(parsed)
    if payload is None:
        raise BotmuxTransportError("botmux returned an invalid JSON response")
    return payload


def _request_json(
    *,
    endpoint: str,
    path: str,
    method: str = "GET",
    dashboard_auth: str | None = None,
    payload: PayloadView | None = None,
    timeout_seconds: float = BOTMUX_HTTP_TIMEOUT_SECONDS,
) -> Payload:
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
            raw = cast(bytes, response.read())
    except HTTPError as exc:
        try:
            raw_error = cast(object, json.loads(exc.read().decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raw_error = {}
        error_payload = (
            cast(PayloadView, raw_error) if isinstance(raw_error, Mapping) else {}
        )
        raise BotmuxApiError(exc.code, error_payload) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BotmuxTransportError("botmux transport failed") from exc
    return _decode_json_object(raw)


def _select_online_bot(
    *,
    endpoint: str,
    dashboard_auth: str,
    bot_id: str,
    requester: JsonRequester,
) -> Payload:
    bots_payload = requester(
        endpoint=endpoint,
        path="/api/bots",
        dashboard_auth=dashboard_auth,
    )
    raw_bots = bots_payload.get("bots")
    bots = cast(list[object], raw_bots) if isinstance(raw_bots, list) else []
    selected: Payload | None = None
    for item in bots:
        mapping = _as_mapping(item)
        if mapping is not None and str(mapping.get("larkAppId") or "") == bot_id:
            selected = dict(mapping)
            break
    if selected is None:
        raise ValueError("configured botmux bot is not online")
    if selected.get("online") is not True:
        raise ValueError("configured botmux bot is offline")
    return selected


def _verify_chat_membership_and_cwd(
    *,
    endpoint: str,
    dashboard_auth: str,
    bot_id: str,
    chat_id: str,
    expected_working_dir: Path,
    selected_bot: PayloadView,
    requester: JsonRequester,
) -> None:
    groups_payload = requester(
        endpoint=endpoint,
        path="/api/groups",
        dashboard_auth=dashboard_auth,
    )
    raw_chats = groups_payload.get("chats")
    chats = cast(list[object], raw_chats) if isinstance(raw_chats, list) else []
    selected_chat: Payload | None = None
    for item in chats:
        mapping = _as_mapping(item)
        if mapping is not None and str(mapping.get("chatId") or "") == chat_id:
            selected_chat = dict(mapping)
            break
    if selected_chat is None:
        raise ValueError("configured botmux chat is not visible")
    raw_members = selected_chat.get("memberBots")
    members = cast(list[object], raw_members) if isinstance(raw_members, list) else []
    selected_member: Payload | None = None
    for item in members:
        mapping = _as_mapping(item)
        if (
            mapping is not None
            and str(mapping.get("larkAppId") or "") == bot_id
            and mapping.get("inChat") is True
        ):
            selected_member = dict(mapping)
            break
    if selected_member is None:
        raise ValueError("configured botmux bot is not in the selected chat")
    oncall = _as_mapping(selected_member.get("oncallChat")) or {}
    configured_working_dirs = {
        _normalized_working_dir(value)
        for value in (
            str(selected_bot.get("defaultWorkingDir") or ""),
            str(oncall.get("workingDir") or ""),
        )
        if value
    }
    if expected_working_dir not in configured_working_dirs:
        raise ValueError(
            "configured botmux bot or chat does not bind the LoopX project directory"
        )


def _probe_botmux(
    *,
    binding: PayloadView,
    requester: JsonRequester,
) -> Payload:
    """Verify botmux liveness, bot online state, project cwd, and dry-run route."""
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
    selected_bot = _select_online_bot(
        endpoint=endpoint,
        dashboard_auth=dashboard_auth,
        bot_id=bot_id,
        requester=requester,
    )
    _verify_chat_membership_and_cwd(
        endpoint=endpoint,
        dashboard_auth=dashboard_auth,
        bot_id=bot_id,
        chat_id=chat_id,
        expected_working_dir=expected_working_dir,
        selected_bot=selected_bot,
        requester=requester,
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
