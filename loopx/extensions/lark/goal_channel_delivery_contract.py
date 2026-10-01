from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from .identity_shapes import LARK_CHAT_ID_PATTERN

_GOAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_LARK_APP_ID_RE = re.compile(r"^cli_[A-Za-z0-9_-]+$")
_LARK_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def goal_channel_delivery_route(
    goal_id: object,
    resolve_goal_channel: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    safe_goal_id = str(goal_id or "").strip()
    if not _GOAL_ID_RE.fullmatch(safe_goal_id):
        raise ValueError("goal_id must be a stable LoopX Goal id")
    binding = dict(resolve_goal_channel(safe_goal_id))
    channel = binding.get("channel")
    identity = binding.get("identity")
    if (
        binding.get("goal_id") != safe_goal_id
        or binding.get("provider") != "lark"
        or binding.get("enabled") is not True
        or not isinstance(channel, Mapping)
        or not isinstance(identity, Mapping)
    ):
        raise ValueError(
            "Goal Channel delivery requires the enabled Lark Goal Channel binding"
        )
    chat_id = str(channel.get("chat_id") or "").strip()
    sender_profile = str(identity.get("sender_profile") or "").strip()
    sender_identity = str(identity.get("sender_identity") or "").strip()
    bot_app_id = str(identity.get("bot_app_id") or "").strip()
    bot_display_name = str(identity.get("bot_display_name") or "").strip()
    cli_bin = str(identity.get("cli_bin") or "lark-cli").strip()
    if (
        identity.get("mode") != "project_bot"
        or sender_identity != "bot"
        or not LARK_CHAT_ID_PATTERN.fullmatch(chat_id)
        or not _LARK_PROFILE_RE.fullmatch(sender_profile)
        or sender_profile.lower() == "default"
        or not _LARK_APP_ID_RE.fullmatch(bot_app_id)
        or not bot_display_name
        or not cli_bin
    ):
        raise ValueError(
            "Goal Channel delivery requires a complete project_bot Goal Channel identity"
        )
    return {
        "goal_id": safe_goal_id,
        "chat_id": chat_id,
        "sender_profile": sender_profile,
        "sender_identity": sender_identity,
        "bot_app_id": bot_app_id,
        "bot_display_name": bot_display_name,
        "cli_bin": cli_bin,
    }


def goal_channel_binding_digest(binding: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(binding), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = ["goal_channel_binding_digest", "goal_channel_delivery_route"]
