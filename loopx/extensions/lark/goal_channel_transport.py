from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from enum import Enum
from typing import Any

# Re-exported for the existing callers of this module; the shapes themselves are
# decided once, in ``identity_shapes``.
from .identity_shapes import (  # noqa: F401
    LARK_APP_ID_PATTERN as APP_ID_PATTERN,
    LARK_CHAT_ID_SEARCH as CHAT_ID_PATTERN,
    LARK_MESSAGE_ID_SEARCH as MESSAGE_ID_PATTERN,
    LARK_OPEN_ID_SEARCH as OPEN_ID_PATTERN,
)
from .presentation.kanban import CommandRunner

SAFE_PROFILE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
REQUIRED_GOAL_TOPIC_SCOPES = ("im:message", "im:message:readonly")
REQUIRED_BOT_GROUP_HISTORY_SCOPES = (
    "im:message.group_msg",
    "im:message.group_msg.include_bot:read",
)
BOT_GROUP_HISTORY_API_PATH = "/document/server-docs/im-v1/message/list"


class BotChatMembershipResult(str, Enum):
    ALREADY_VERIFIED = "already_verified"
    ALREADY_UNVERIFIED = "already_unverified"
    ADDED_VERIFIED = "added_verified"
    ADD_FAILED = "add_failed"
    ADDED_UNVERIFIED = "added_unverified"

    @property
    def external_write_performed(self) -> bool:
        return self in {
            BotChatMembershipResult.ADDED_VERIFIED,
            BotChatMembershipResult.ADDED_UNVERIFIED,
        }


def bot_group_history_permission_guidance(app_id: str) -> dict[str, Any] | None:
    """Return an app-bound, credential-free repair contract for Bot history reads."""

    if not APP_ID_PATTERN.fullmatch(str(app_id or "")):
        return None
    query = f"?appId={app_id}"
    return {
        "schema_version": "lark_bot_group_history_permission_guidance_v0",
        "identity": "bot",
        "capability": "group_history_pagination",
        "action": "enable_application_scopes_and_publish",
        "required_scopes": list(REQUIRED_BOT_GROUP_HISTORY_SCOPES),
        "api_document_url": (
            f"https://open.feishu.cn{BOT_GROUP_HISTORY_API_PATH}{query}"
        ),
    }


def json_payload(result: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = result.get("stdout")
    try:
        payload = json.loads(str(raw or ""))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def find_first_string(
    payload: Any,
    keys: set[str],
    pattern: re.Pattern[str],
) -> str | None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and pattern.fullmatch(value):
                return value
        for value in payload.values():
            found = find_first_string(value, keys, pattern)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_first_string(value, keys, pattern)
            if found:
                return found
    return None


def lark_provider_mention_identities(mention: Mapping[str, Any]) -> set[str]:
    """Return provider-stable identities carried by one structured mention."""

    identity_keys = ("user_id", "open_id", "union_id", "app_id", "bot_id")
    identities = {
        str(mention.get(key) or "").strip()
        for key in identity_keys
        if str(mention.get(key) or "").strip()
    }
    raw_id = mention.get("id")
    if isinstance(raw_id, Mapping):
        identities.update(
            str(value).strip() for value in raw_id.values() if str(value).strip()
        )
    elif str(raw_id or "").strip():
        identities.add(str(raw_id).strip())
    return identities


def contains_exact_field(
    payload: Any,
    key: str,
    expected: str,
) -> bool:
    if isinstance(payload, Mapping):
        if str(payload.get(key) or "") == expected:
            return True
        return any(
            contains_exact_field(value, key, expected) for value in payload.values()
        )
    if isinstance(payload, list):
        return any(contains_exact_field(value, key, expected) for value in payload)
    return False


def payload_contains_text(payload: Any, expected: str) -> bool:
    if isinstance(payload, Mapping):
        return any(payload_contains_text(value, expected) for value in payload.values())
    if isinstance(payload, list):
        return any(payload_contains_text(value, expected) for value in payload)
    return isinstance(payload, str) and expected in payload


def payload_contains_exact(payload: Any, expected: str) -> bool:
    if isinstance(payload, Mapping):
        return any(
            payload_contains_exact(value, expected) for value in payload.values()
        )
    if isinstance(payload, list):
        return any(payload_contains_exact(value, expected) for value in payload)
    return isinstance(payload, str) and payload == expected


def call(
    runner: CommandRunner,
    args: list[str],
) -> Mapping[str, Any]:
    try:
        return runner(args, None, 30)
    except subprocess.TimeoutExpired:
        # The command ran and was killed, so a provider write it had already
        # started is neither proven nor excluded. Carry the fact instead of
        # folding it into a bare failure the callers would read as a verdict.
        return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True}
    except (OSError, subprocess.SubprocessError):
        # The command never started, so no provider write can have happened.
        return {"returncode": 1, "stdout": "", "stderr": "", "spawn_failed": True}


def profile_args(profile: str | None) -> list[str]:
    if not profile:
        return []
    if not SAFE_PROFILE_PATTERN.fullmatch(profile):
        raise ValueError("Lark profile must be a local safe-name token")
    return ["--profile", profile]


def lark_args(
    *,
    cli_bin: str,
    profile: str | None,
    tail: list[str],
) -> list[str]:
    return [cli_bin, *profile_args(profile), *tail]


def auth_verified(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str | None,
    identity: str,
    expected_bot_name: str | None,
) -> bool:
    auth = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=["auth", "status", "--verify", "--json"],
        ),
    )
    identities = json_payload(auth).get("identities")
    current = identities.get(identity) if isinstance(identities, Mapping) else None
    if auth.get("returncode") != 0 or not isinstance(current, Mapping):
        return False
    verified = current.get("available") is True and current.get("verified") is True
    if identity == "bot" and expected_bot_name:
        return verified and str(current.get("appName") or "") == expected_bot_name
    return verified


def verified_app_id(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str | None,
) -> str | None:
    auth = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=["auth", "status", "--verify", "--json"],
        ),
    )
    app_id = str(json_payload(auth).get("appId") or "")
    if auth.get("returncode") != 0 or not APP_ID_PATTERN.fullmatch(app_id):
        return None
    return app_id


def goal_topic_message_permissions(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str | None,
) -> dict[str, Any]:
    """Return a public-safe health result for Goal Topic auto replies."""

    result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "auth",
                "check",
                "--scope",
                " ".join(REQUIRED_GOAL_TOPIC_SCOPES),
                "--json",
            ],
        ),
    )
    payload = json_payload(result)
    granted_raw = payload.get("granted")
    granted_items = granted_raw if isinstance(granted_raw, list) else []
    granted = {str(scope) for scope in granted_items if isinstance(scope, str)}
    missing = [scope for scope in REQUIRED_GOAL_TOPIC_SCOPES if scope not in granted]
    ready = bool(
        result.get("returncode") == 0 and payload.get("ok") is True and not missing
    )
    return {
        "ready": ready,
        "error_code": None if ready else "lark_message_permissions_required",
        "required_scopes": list(REQUIRED_GOAL_TOPIC_SCOPES),
    }


def chat_verified(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str | None,
    identity: str,
    chat_id: str,
) -> bool:
    result = call(
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
                identity,
                "--format",
                "json",
            ],
        ),
    )
    return result.get("returncode") == 0


def bot_membership_verified(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str | None,
    chat_id: str,
    app_id: str,
) -> bool:
    result = call(
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
                "bot",
                "--as",
                "bot",
                "--format",
                "json",
            ],
        ),
    )
    if result.get("returncode") == 0:
        return contains_exact_field(json_payload(result), "app_id", app_id)

    # Some Lark tenants allow a bot to access its chats while denying the
    # member-list endpoint. Accessing this exact chat with the selected bot
    # profile still proves that the bot belongs to the chat; a bot outside the
    # chat cannot resolve it through the bot identity.
    return chat_verified(
        runner=runner,
        cli_bin=cli_bin,
        profile=profile,
        identity="bot",
        chat_id=chat_id,
    )


def add_bot_to_chat(
    *,
    runner: CommandRunner,
    cli_bin: str,
    chat_id: str,
    app_id: str,
) -> bool:
    """Ask the local user identity to add one exact App to one exact chat."""

    result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=None,
            tail=[
                "im",
                "chat.members",
                "create",
                "--chat-id",
                chat_id,
                "--member-id-type",
                "app_id",
                "--succeed-type",
                "2",
                "--data",
                json.dumps({"id_list": [app_id]}),
                "--as",
                "user",
                "--format",
                "json",
            ],
        ),
    )
    return result.get("returncode") == 0


def ensure_bot_chat_membership(
    *,
    runner: CommandRunner,
    cli_bin: str,
    membership_profile: str | None,
    bot_profile: str | None,
    chat_id: str,
    app_id: str,
) -> BotChatMembershipResult:
    """Add one App when needed and verify the exact bot-to-chat relationship."""

    already_member = bot_membership_verified(
        runner=runner,
        cli_bin=cli_bin,
        profile=membership_profile,
        chat_id=chat_id,
        app_id=app_id,
    )
    if not already_member and not add_bot_to_chat(
        runner=runner,
        cli_bin=cli_bin,
        chat_id=chat_id,
        app_id=app_id,
    ):
        return BotChatMembershipResult.ADD_FAILED
    verified = bot_membership_verified(
        runner=runner,
        cli_bin=cli_bin,
        profile=membership_profile,
        chat_id=chat_id,
        app_id=app_id,
    ) and chat_verified(
        runner=runner,
        cli_bin=cli_bin,
        profile=bot_profile,
        identity="bot",
        chat_id=chat_id,
    )
    if not verified:
        return (
            BotChatMembershipResult.ALREADY_UNVERIFIED
            if already_member
            else BotChatMembershipResult.ADDED_UNVERIFIED
        )
    return (
        BotChatMembershipResult.ALREADY_VERIFIED
        if already_member
        else BotChatMembershipResult.ADDED_VERIFIED
    )


def message_readback_verified(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str | None,
    identity: str,
    message_id: str,
    expected_text: str,
    expected_chat_id: str | None = None,
    expected_goal_id: str | None = None,
) -> bool:
    result = call(
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
                identity,
                "--no-reactions",
                "--format",
                "json",
            ],
        ),
    )
    payload = json_payload(result)
    if expected_goal_id is not None:
        # Topic roots and the earlier Goal control messages use different exact
        # line markers. Verify the marker in this message, never in a sibling
        # record returned by a batched provider response.
        markers = {f"Goal ID: {expected_goal_id}", f"LoopX Goal: {expected_goal_id}"}

        def has_marker(value: Any) -> bool:
            if isinstance(value, Mapping):
                return any(has_marker(item) for item in value.values())
            if isinstance(value, list):
                return any(has_marker(item) for item in value)
            if not isinstance(value, str):
                return False
            if markers.intersection(line.strip() for line in value.splitlines()):
                return True
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                return False
            return isinstance(decoded, (dict, list)) and has_marker(decoded)

        def matching_record(value: Any) -> bool:
            if isinstance(value, Mapping):
                if value.get("message_id") == message_id:
                    return bool(
                        not value.get("deleted")
                        and (
                            expected_chat_id is None
                            or value.get("chat_id") == expected_chat_id
                        )
                        and has_marker(value.get("body") or value.get("content"))
                    )
                return any(matching_record(item) for item in value.values())
            return isinstance(value, list) and any(
                matching_record(item) for item in value
            )

        return result.get("returncode") == 0 and matching_record(payload)
    return bool(
        result.get("returncode") == 0
        and contains_exact_field(payload, "message_id", message_id)
        and payload_contains_text(payload, expected_text)
        and (
            expected_chat_id is None
            or contains_exact_field(payload, "chat_id", expected_chat_id)
        )
    )


def pin_readback_verified(
    *,
    runner: CommandRunner,
    cli_bin: str,
    profile: str | None,
    identity: str,
    chat_id: str,
    message_id: str,
) -> bool:
    result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "im",
                "pins",
                "list",
                "--chat-id",
                chat_id,
                "--as",
                identity,
                "--format",
                "json",
            ],
        ),
    )
    return bool(
        result.get("returncode") == 0
        and contains_exact_field(json_payload(result), "message_id", message_id)
    )
