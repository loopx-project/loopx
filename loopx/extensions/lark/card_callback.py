"""Shared authenticated Lark card callback transport helpers.

The operation and team-plan domains own different proposal state machines.
They share only the provider boundary that authenticates one callback and
updates the exact originating Lark message with verified readback.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import json

from .goal_channel_message_delivery import (
    card_projection_matches,
    message_card_matches,
    normalized_card_text,
)
from .goal_channel_transport import call, json_payload, lark_args
from .presentation.kanban import CommandRunner


def callback_timestamp(value: object, *, subject: str) -> str:
    token = str(value or "").strip()
    precision = {
        13: 1_000,
        16: 1_000_000,
    }.get(len(token))
    if not token.isdigit() or precision is None:
        raise ValueError(f"{subject} callback timestamp is invalid")
    seconds, remainder = divmod(int(token), precision)
    return (
        (
            datetime.fromtimestamp(seconds, tz=timezone.utc)
            + timedelta(microseconds=remainder * (1_000_000 // precision))
        )
        .isoformat()
        .replace("+00:00", "Z")
    )


def _lark_card_v2_fallback_matches(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    """Recognize Lark's message-get fallback for a Card 2.0 payload."""

    if expected.get("schema") != "2.0":
        return False
    header = expected.get("header")
    if not isinstance(header, Mapping):
        return False
    title_value = header.get("title")
    subtitle_value = header.get("subtitle")
    title = title_value.get("content") if isinstance(title_value, Mapping) else None
    subtitle = (
        subtitle_value.get("content") if isinstance(subtitle_value, Mapping) else None
    )
    expected_title = "\n".join(
        item for item in (title, subtitle) if isinstance(item, str) and item
    )
    return _lark_card_v2_fallback_matches_title(observed, expected_title)


def _lark_card_v2_fallback_matches_title(
    observed: Mapping[str, object], expected_title: str
) -> bool:
    if set(observed) != {"title", "elements"}:
        return False
    elements = observed.get("elements")
    if observed.get("title") != expected_title or not isinstance(elements, list):
        return False
    leaves: list[Mapping[str, object]] = []

    def collect(value: object) -> bool:
        if isinstance(value, list):
            return bool(value) and all(collect(item) for item in value)
        if not isinstance(value, Mapping) or value.get("tag") not in {"img", "text"}:
            return False
        leaves.append(value)
        return True

    return collect(elements) and any(item.get("tag") == "img" for item in leaves)


def callback_card_content_matches(
    value: object, expected: Mapping[str, object]
) -> bool:
    """Verify either provider JSON or the documented userDSL callback shape."""

    observed: object = value
    if isinstance(value, str):
        if not value:
            return False
        try:
            observed = json.loads(value)
        except json.JSONDecodeError:
            return value == normalized_card_text(expected)
    return bool(
        isinstance(observed, Mapping)
        and (
            card_projection_matches(observed, expected)
            or _lark_card_v2_fallback_matches(observed, expected)
        )
    )


def _find_message(value: object, message_id: str) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        if str(value.get("message_id") or "") == message_id:
            return value
        for child in value.values():
            if found := _find_message(child, message_id):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := _find_message(child, message_id):
                return found
    return None


def read_callback_card_content(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str,
    message_id: str,
    chat_id: str,
    app_id: str,
) -> object:
    """Retry best-effort callback hydration through exact message readback."""

    result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "api",
                "GET",
                f"/open-apis/im/v1/messages/{message_id}",
                "--params",
                json.dumps({"card_msg_content_type": "user_card_content"}),
                "--as",
                "bot",
            ],
        ),
    )
    if result.get("returncode") != 0:
        return None
    message = _find_message(json_payload(result), message_id)
    sender = message.get("sender") if isinstance(message, Mapping) else None
    if (
        not isinstance(message, Mapping)
        or str(message.get("chat_id") or "") != chat_id
        or not isinstance(sender, Mapping)
        or sender.get("sender_type") != "app"
        or sender.get("id") != app_id
    ):
        return None
    body = message.get("body") if isinstance(message, Mapping) else None
    return body.get("content") if isinstance(body, Mapping) else None


def _first_tenant_key(value: object) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("tenant_key")
        if isinstance(candidate, str) and candidate:
            return candidate
        for child in value.values():
            if found := _first_tenant_key(child):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := _first_tenant_key(child):
                return found
    return None


def _member_tenant_key(value: object, operator_id: str) -> str | None:
    if isinstance(value, Mapping):
        identities = {
            str(value.get(key) or "")
            for key in ("member_id", "open_id", "operator_id", "id")
        }
        tenant_key = value.get("tenant_key")
        if operator_id in identities and isinstance(tenant_key, str) and tenant_key:
            return tenant_key
        for child in value.values():
            if found := _member_tenant_key(child, operator_id):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := _member_tenant_key(child, operator_id):
                return found
    return None


def operator_membership_verified(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str,
    chat_id: str,
    operator_id: str,
) -> bool:
    chat_result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "im",
                "chats",
                "get",
                "--chat-id",
                chat_id,
                "--as",
                "bot",
                "--format",
                "json",
            ],
        ),
    )
    member_result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "im",
                "+chat-members-list",
                "--chat-id",
                chat_id,
                "--member-types",
                "user",
                "--member-id-type",
                "open_id",
                "--page-all",
                "--as",
                "bot",
                "--format",
                "json",
            ],
        ),
    )
    if chat_result.get("returncode") != 0 or member_result.get("returncode") != 0:
        return False
    chat_tenant = _first_tenant_key(json_payload(chat_result))
    member_tenant = _member_tenant_key(json_payload(member_result), operator_id)
    return bool(chat_tenant and member_tenant and chat_tenant == member_tenant)


def _result_card_readback_verified(
    payload: Mapping[str, object],
    *,
    message_id: str,
    chat_id: str,
    app_id: str,
    card: Mapping[str, object],
) -> bool:
    message = _find_message(payload, message_id)
    sender = message.get("sender") if isinstance(message, Mapping) else None
    return bool(
        payload.get("ok") is True
        and isinstance(message, Mapping)
        and str(message.get("chat_id") or "") == chat_id
        and isinstance(sender, Mapping)
        and sender.get("sender_type") == "app"
        and sender.get("id") == app_id
        and message_card_matches(message, card)
    )


def _read_result_card(
    *, runner: CommandRunner, cli_bin: str, profile: str, message_id: str
) -> tuple[Mapping[str, object], bool]:
    readback = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "im",
                "+messages-mget",
                "--message-ids",
                message_id,
                "--as",
                "bot",
                "--no-reactions",
                "--format",
                "json",
            ],
        ),
    )
    return json_payload(readback), readback.get("returncode") == 0


def update_callback_card(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str,
    token: str,
    card: Mapping[str, object],
    message_id: str,
    chat_id: str,
    app_id: str,
) -> dict[str, bool]:
    result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "api",
                "POST",
                "/open-apis/interactive/v1/card/update",
                "--as",
                "bot",
                "--data",
                json.dumps(
                    {"token": token, "card": dict(card)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ],
        ),
    )
    payload = json_payload(result)
    write_performed = result.get("returncode") == 0 and payload.get("ok") is True
    if not write_performed:
        return {"external_write_performed": False, "readback_verified": False}
    readback_payload, readback_ok = _read_result_card(
        runner=runner,
        cli_bin=cli_bin,
        profile=profile,
        message_id=message_id,
    )
    return {
        "external_write_performed": True,
        "readback_verified": bool(
            readback_ok
            and _result_card_readback_verified(
                readback_payload,
                message_id=message_id,
                chat_id=chat_id,
                app_id=app_id,
                card=card,
            )
        ),
    }


def patch_result_card(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str,
    card: Mapping[str, object],
    message_id: str,
    chat_id: str,
    app_id: str,
) -> dict[str, bool]:
    result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "im",
                "messages",
                "patch",
                "--message-id",
                message_id,
                "--data",
                json.dumps(
                    {
                        "content": json.dumps(
                            dict(card), ensure_ascii=False, separators=(",", ":")
                        )
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "--as",
                "bot",
                "--format",
                "json",
            ],
        ),
    )
    payload = json_payload(result)
    write_performed = result.get("returncode") == 0 and payload.get("ok") is True
    if not write_performed:
        return {"external_write_performed": False, "readback_verified": False}
    readback_payload, readback_ok = _read_result_card(
        runner=runner,
        cli_bin=cli_bin,
        profile=profile,
        message_id=message_id,
    )
    return {
        "external_write_performed": True,
        "readback_verified": bool(
            readback_ok
            and _result_card_readback_verified(
                readback_payload,
                message_id=message_id,
                chat_id=chat_id,
                app_id=app_id,
                card=card,
            )
        ),
    }


__all__ = [
    "callback_card_content_matches",
    "callback_timestamp",
    "operator_membership_verified",
    "patch_result_card",
    "read_callback_card_content",
    "update_callback_card",
]
