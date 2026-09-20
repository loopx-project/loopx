"""Shared structural routing rules for Lark Goal Topic events."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum
from typing import Any

from .goal_channel_contracts import LarkTopicEventDecisionReason
from .goal_channel_transport import (
    CHAT_ID_PATTERN,
    MESSAGE_ID_PATTERN,
    lark_provider_mention_identities,
)


class CaptureScope(str, Enum):
    ADDRESSED_ONLY = "addressed_only"
    CONFIGURED_CHAT_ALL = "configured_chat_all"


class IngressMode(str, Enum):
    LIVE_STEERING = "live_steering"
    SESSION_QUEUE = "session_queue"
    # Read compatibility for bindings created by the first Goal Topic slice.
    DIRECT_SESSION = "direct_session"
    ASYNC_INBOX = "async_inbox"


class ReplyMode(str, Enum):
    TOPIC_REPLY = "topic_reply"


def _routing_value(
    enum_type: type[CaptureScope | IngressMode | ReplyMode],
    value: Any,
    *,
    default: str,
    field: str,
) -> str:
    normalized = str(value or default).strip().lower()
    try:
        return enum_type(normalized).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field} must be one of: {allowed}") from exc


def _connection_routing_modes(
    routing: Mapping[str, Any],
) -> tuple[str, str, str]:
    """Normalize persisted modes for both connection readback and event routing."""

    capture_scope = _routing_value(
        CaptureScope,
        routing.get("capture_scope")
        or (
            "configured_chat_all"
            if routing.get("incoming_mode") == "all"
            else "addressed_only"
        ),
        default=CaptureScope.ADDRESSED_ONLY.value,
        field="capture_scope",
    )
    ingress_mode = _routing_value(
        IngressMode,
        routing.get("ingress_mode"),
        default=IngressMode.DIRECT_SESSION.value,
        field="ingress_mode",
    )
    reply_mode = _routing_value(
        ReplyMode,
        routing.get("reply_mode"),
        default=ReplyMode.TOPIC_REPLY.value,
        field="reply_mode",
    )
    return capture_scope, ingress_mode, reply_mode


def _normalize_mention_name(name: str) -> str:
    cleaned = str(name or "").strip()
    if cleaned.startswith("@"):
        cleaned = cleaned[1:].strip()
    return " ".join(cleaned.split()).casefold()


def _match_content_mention(content: str, name: str) -> bool:
    if not content or not name:
        return False
    escaped = re.escape(name.lstrip("@"))
    pattern = rf"(?:^|\s|[,，!！?？;；:：])@{escaped}(?:\b|[\s,，!！?？;；:：]|$)"
    return bool(re.search(pattern, content, re.IGNORECASE))


def is_event_addressed_to_bot(
    event: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> bool:
    if (
        event.get("reply_to_bot") is True
        and event.get("reply_context_verified") is True
    ):
        return True
    bot_app_id = str(identity.get("bot_app_id") or "").strip()
    bot_open_id = str(identity.get("bot_open_id") or "").strip()
    bot_display_name = str(
        identity.get("bot_display_name") or identity.get("bot_name") or ""
    ).strip()
    sender_profile = str(identity.get("sender_profile") or "").strip()
    if not (bot_app_id or bot_open_id or bot_display_name or sender_profile):
        return False

    normalized_display_name = _normalize_mention_name(bot_display_name)
    normalized_sender_profile = _normalize_mention_name(sender_profile)

    if "mentions" in event:
        mentions = event.get("mentions")
        if isinstance(mentions, list):
            expected_ids = {value for value in (bot_app_id, bot_open_id) if value}
            for item in mentions:
                if not isinstance(item, Mapping):
                    continue
                candidate_ids = lark_provider_mention_identities(item)
                if expected_ids and candidate_ids.intersection(expected_ids):
                    return True
                if expected_ids:
                    continue
                normalized_item_name = _normalize_mention_name(
                    str(item.get("name") or "")
                )
                if (
                    normalized_display_name
                    and normalized_item_name == normalized_display_name
                ):
                    return True
                if (
                    normalized_sender_profile
                    and normalized_item_name == normalized_sender_profile
                ):
                    return True
            return False

    content = str(event.get("content") or "").strip()
    if content:
        if bot_display_name and _match_content_mention(content, bot_display_name):
            return True
        if sender_profile and _match_content_mention(content, sender_profile):
            return True

    return False


def decide_lark_topic_route_event(
    *,
    event: Mapping[str, Any],
    chat_id: str,
    topic_root_message_id: str,
    capture_scope: str,
    identity: Mapping[str, Any],
) -> LarkTopicEventDecisionReason:
    """Apply the shared structural authority for one resolved Goal Topic route."""

    event_chat_id = str(event.get("chat_id") or "")
    root_id = str(event.get("root_id") or "")
    message_id = str(event.get("message_id") or "")
    if not (
        CHAT_ID_PATTERN.fullmatch(event_chat_id)
        and MESSAGE_ID_PATTERN.fullmatch(root_id)
        and MESSAGE_ID_PATTERN.fullmatch(message_id)
    ):
        return LarkTopicEventDecisionReason.INVALID_EVENT
    try:
        normalized_capture_scope = CaptureScope(capture_scope)
    except ValueError:
        return LarkTopicEventDecisionReason.INVALID_ROUTING_STATE
    if event_chat_id != chat_id:
        return LarkTopicEventDecisionReason.CHAT_MISMATCH
    if (
        root_id != topic_root_message_id
        and normalized_capture_scope is not CaptureScope.CONFIGURED_CHAT_ALL
    ):
        return LarkTopicEventDecisionReason.TOPIC_MISMATCH
    sender_id = str(event.get("sender_id") or "")
    if sender_id and sender_id in {
        str(identity.get("bot_app_id") or ""),
        str(identity.get("bot_open_id") or ""),
    }:
        return LarkTopicEventDecisionReason.SELF_MESSAGE
    if (
        normalized_capture_scope is not CaptureScope.CONFIGURED_CHAT_ALL
        and not is_event_addressed_to_bot(event, identity)
    ):
        return LarkTopicEventDecisionReason.NOT_ADDRESSED
    return LarkTopicEventDecisionReason.MATCHED
