"""Lark-specific identity observation for a Core-owned private conversation.

Each non-default profile independently verifies its App and logged-in owner.
Core receives only opaque, App-scoped references. Tokens remain in lark-cli.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from .goal_channel_transport import APP_ID_PATTERN, call, json_payload, lark_args
from .identity_shapes import LARK_OPEN_ID_PATTERN as OPEN_ID_PATTERN
from .goal_topic_connections import _profile_ref
from .presentation.kanban import CommandRunner


def identity_ref(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()[:24]


def observe_lark_conversation_identity(*, profile: str, runner: CommandRunner,
                                       cli_bin: str) -> dict[str, Any]:
    profile = _profile_ref(profile)
    if profile.casefold() == "default":
        raise ValueError("private conversations require an explicit non-default App profile")
    result = call(runner, lark_args(cli_bin=cli_bin, profile=profile,
                                  tail=["auth", "status", "--verify", "--json"]))
    payload = json_payload(result)
    identities = payload.get("identities")
    identities = identities if isinstance(identities, Mapping) else {}
    bot, owner = identities.get("bot"), identities.get("user")
    app_id = str(payload.get("appId") or "")
    if result.get("returncode") != 0 or not APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError("the selected App identity could not be verified")
    if not isinstance(bot, Mapping) or not isinstance(owner, Mapping):
        raise ValueError("verify this App and its owner independently before binding private Chat")
    if not all(row.get("available") is True and row.get("verified") is True
               for row in [bot, owner]):
        raise ValueError("verify this App and its owner independently before binding private Chat")
    owner_id = str(owner.get("openId") or "")
    if not OPEN_ID_PATTERN.fullmatch(owner_id):
        raise ValueError("the selected App has no verified owner identity")
    provider_ref = identity_ref(app_id)
    return {"transport_ref": profile, "provider_ref": provider_ref,
            "operator_ref": identity_ref(provider_ref, owner_id), "verified": True, "consumer_ref": hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:32], "bot_display_name": str(bot.get("appName") or "")}


def lark_private_source(*, provider_ref: str, event: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize provider provenance; Core still decides whether it is admitted."""
    from .identity_shapes import LARK_CHAT_ID_PATTERN, LARK_MESSAGE_ID_PATTERN

    chat = str(event.get("chat_id") or "")
    sender = str(event.get("sender_id") or "")
    message = str(event.get("message_id") or "")
    if not (re.fullmatch(r"[a-f0-9]{24}", provider_ref) and LARK_CHAT_ID_PATTERN.fullmatch(chat)
            and LARK_MESSAGE_ID_PATTERN.fullmatch(message) and OPEN_ID_PATTERN.fullmatch(sender)):
        raise ValueError("incomplete private-message provenance")
    if event.get("chat_type") == "group":
        root = str(event.get("root_id") or message)
        if not LARK_MESSAGE_ID_PATTERN.fullmatch(root) or (event.get("parent_id") and not event.get("root_id")):
            raise ValueError("group topic root is unavailable")
        return {"source_ref": identity_ref(provider_ref, chat, root), "sender_ref": identity_ref(provider_ref, sender),
                "private_human_message": False, "group_human_message": event.get("sender_type") == "user",
                "group_ref": identity_ref(provider_ref, chat), "topic_ref": identity_ref(provider_ref, chat, root)}
    return {"source_ref": identity_ref(provider_ref, chat, sender), "sender_ref": identity_ref(provider_ref, sender),
            "private_human_message": event.get("chat_type") == "p2p" and event.get("sender_type") == "user"}
