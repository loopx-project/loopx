from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ...control_plane.work_items.operator_inbox import (
    CAPTURE_SCOPES,
    OperatorInboxSourceContract,
    operator_inbox_attention_kind,
    project_operator_inbox_urgency,
)
from ...file_lock import exclusive_file_lock
from ..external_connector_runtime import (
    EFFECT_RECEIPT_SCHEMA_VERSION,
    ExternalEffectKind,
    ExternalResponsePolicy,
    decide_external_event_ack,
    external_event_ref,
)
from .goal_channel_transport import (
    APP_ID_PATTERN,
    OPEN_ID_PATTERN,
    lark_provider_mention_identities,
)
from .identity_shapes import (  # noqa: F401
    LARK_CHAT_ID_PATTERN as CHAT_ID_PATTERN,
    LARK_MESSAGE_ID_PATTERN as MESSAGE_ID_PATTERN,
)

EVENT_SCHEMA_VERSION = "lark_event_inbox_event_v0"
CONFIG_SCHEMA_VERSION = "lark_event_inbox_config_v0"
PROCESSED_SCHEMA_VERSION = "lark_event_inbox_processed_v0"
MATERIAL_REVIEW_LEDGER_SCHEMA_VERSION = "lark_material_review_ledger_v0"
EVENT_ID_PATTERN = re.compile(r"[A-Za-z0-9:_-]{1,200}")
SAFE_PROFILE_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
REACTION_EMOJI_PATTERN = re.compile(r"[A-Za-z0-9_]{1,64}")
REPLY_PLACEMENT_POLICIES = {"source_thread", "source_context"}
REPLY_EDITORIAL_STYLES = {"concise", "bullet_points_preferred"}
ROUTE_KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
SENDER_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}")
ADDRESSING_SOURCES = {"provider_mention", "verified_reply", "legacy_text"}
LARK_OPERATOR_INBOX_SOURCE_CONTRACT = OperatorInboxSourceContract(
    config_schema_version=CONFIG_SCHEMA_VERSION,
    event_schema_version=EVENT_SCHEMA_VERSION,
    processed_schema_version=PROCESSED_SCHEMA_VERSION,
    message_id_pattern=MESSAGE_ID_PATTERN,
    event_id_pattern=EVENT_ID_PATTERN,
    sender_profile_pattern=SAFE_PROFILE_PATTERN,
    required_sender_identity="bot",
    reply_flag_field="reply_to_bot",
    operator_display_name_field="bot_display_name",
    destination_field="chat_id",
    destination_pattern=CHAT_ID_PATTERN,
    attachment_count_field="attachment_count",
    addressed_flag_field="addressed_to_bot",
)


def _safe_inbox_path(project: str | Path, raw_path: str) -> Path:
    relative = PurePosixPath(str(raw_path or "").strip().replace("\\", "/"))
    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:2] != (".loopx", "inbox")
    ):
        raise ValueError("lark inbox path must stay under .loopx/inbox")
    root = Path(project).expanduser().resolve()
    resolved = (root / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("lark inbox path escapes the project") from exc
    return resolved


def load_lark_event_inbox_config(
    *, project: str | Path, config_path: str | Path
) -> dict[str, Any]:
    root = Path(project).expanduser().resolve()
    path = Path(config_path).expanduser()
    path = path if path.is_absolute() else root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("lark inbox config must stay inside the project") from exc
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != CONFIG_SCHEMA_VERSION
    ):
        raise ValueError("lark inbox config schema is invalid")
    enabled = payload.get("enabled") is True
    inbox_dir = str(payload.get("inbox_dir") or "").strip()
    if enabled and not inbox_dir:
        raise ValueError("enabled lark event inbox requires inbox_dir")
    capture_scope = str(payload.get("capture_scope") or "addressed_only").strip()
    if capture_scope not in CAPTURE_SCOPES:
        raise ValueError(
            "lark inbox capture_scope must be addressed_only or configured_chat_all"
        )
    topic_root_message_id = str(
        payload.get("topic_root_message_id") or ""
    ).strip()
    if topic_root_message_id and not MESSAGE_ID_PATTERN.fullmatch(
        topic_root_message_id
    ):
        raise ValueError("lark inbox topic_root_message_id is invalid")
    reply_payload = payload.get("reply")
    if reply_payload is not None and not isinstance(reply_payload, Mapping):
        raise ValueError("lark inbox reply config must be an object")
    reply_payload = reply_payload if isinstance(reply_payload, Mapping) else {}
    reply_enabled = reply_payload.get("enabled") is True
    sender_profile = str(reply_payload.get("sender_profile") or "").strip()
    sender_identity = str(reply_payload.get("sender_identity") or "").strip()
    bot_display_name = " ".join(
        str(reply_payload.get("bot_display_name") or "").split()
    )[:100]
    bot_app_id = str(reply_payload.get("bot_app_id") or "").strip()
    bot_open_id = str(reply_payload.get("bot_open_id") or "").strip()
    chat_id = str(reply_payload.get("chat_id") or "").strip()
    placement_policy = str(
        reply_payload.get("placement_policy") or "source_thread"
    ).strip()
    editorial_style = str(reply_payload.get("editorial_style") or "concise").strip()
    if placement_policy not in REPLY_PLACEMENT_POLICIES:
        raise ValueError(
            "lark inbox placement_policy must be source_thread or source_context"
        )
    if editorial_style not in REPLY_EDITORIAL_STYLES:
        raise ValueError(
            "lark inbox editorial_style must be concise or bullet_points_preferred"
        )
    # A reply-capable Inbox acknowledges Agent consumption by default.  Keep
    # the missing field distinct from an explicit empty value so operators can
    # disable the provider write without inventing a second config switch.
    if "received_reaction_emoji" in reply_payload:
        received_reaction_emoji = str(
            reply_payload.get("received_reaction_emoji") or ""
        ).strip()
    else:
        received_reaction_emoji = "Get" if reply_enabled else ""
    received_reaction_policy = reply_payload.get("received_reaction_policy", "transient")
    if received_reaction_policy not in ("transient", "retain"):
        raise ValueError("lark inbox received_reaction_policy must be transient or retain")
    processing_reaction_emoji = str(
        reply_payload.get("processing_reaction_emoji") or ""
    ).strip()
    for field, emoji_type in (
        ("received_reaction_emoji", received_reaction_emoji),
        ("processing_reaction_emoji", processing_reaction_emoji),
    ):
        if emoji_type and not REACTION_EMOJI_PATTERN.fullmatch(emoji_type):
            raise ValueError(f"lark inbox {field} must be a valid emoji type")
        if emoji_type and not reply_enabled:
            raise ValueError(f"lark inbox {field} requires enabled reply")
    if processing_reaction_emoji and not received_reaction_emoji:
        raise ValueError(
            "lark inbox processing_reaction_emoji requires received_reaction_emoji"
        )
    if (
        processing_reaction_emoji
        and processing_reaction_emoji == received_reaction_emoji
    ):
        raise ValueError(
            "lark inbox processing_reaction_emoji must differ from "
            "received_reaction_emoji"
        )
    if reply_enabled and (
        not SAFE_PROFILE_PATTERN.fullmatch(sender_profile)
        or sender_profile.lower() == "default"
        or sender_identity != "bot"
        or not bot_display_name
        or not CHAT_ID_PATTERN.fullmatch(chat_id)
    ):
        raise ValueError(
            "enabled lark inbox reply requires an explicit non-default "
            "sender_profile, bot identity, bot_display_name, and chat_id"
        )
    if bot_app_id and not APP_ID_PATTERN.fullmatch(bot_app_id):
        raise ValueError("lark inbox bot_app_id is invalid")
    if bot_open_id and not OPEN_ID_PATTERN.fullmatch(bot_open_id):
        raise ValueError("lark inbox bot_open_id is invalid")
    material_review_payload = payload.get("material_review")
    if material_review_payload is not None and not isinstance(
        material_review_payload, Mapping
    ):
        raise ValueError("lark inbox material_review config must be an object")
    material_review_payload = (
        material_review_payload if isinstance(material_review_payload, Mapping) else {}
    )
    material_review_enabled = material_review_payload.get("enabled") is True
    material_review_drain_limit = material_review_payload.get("drain_limit", 20)
    if (
        isinstance(material_review_drain_limit, bool)
        or not isinstance(material_review_drain_limit, int)
        or not 1 <= material_review_drain_limit <= 100
    ):
        raise ValueError("lark inbox material_review drain_limit is invalid")
    if material_review_enabled and capture_scope != "configured_chat_all":
        raise ValueError(
            "lark inbox material_review requires configured_chat_all capture"
        )
    return {
        "enabled": enabled,
        "configured": True,
        "inbox_path": _safe_inbox_path(root, inbox_dir) if enabled else None,
        "capture_scope": capture_scope,
        "thread_complete": capture_scope == "configured_chat_all",
        "topic_root_message_id": topic_root_message_id,
        "reply": {
            "enabled": reply_enabled,
            "sender_profile": sender_profile,
            "sender_identity": sender_identity,
            "bot_display_name": bot_display_name,
            "bot_app_id": bot_app_id,
            "bot_open_id": bot_open_id,
            "chat_id": chat_id,
            "placement_policy": placement_policy,
            "editorial_style": editorial_style,
            "received_reaction_emoji": received_reaction_emoji,
            "received_reaction_policy": received_reaction_policy,
            "processing_reaction_emoji": processing_reaction_emoji,
        },
        "material_review": {
            "enabled": material_review_enabled,
            "drain_limit": material_review_drain_limit,
        },
    }


def _load_processed(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != PROCESSED_SCHEMA_VERSION
    ):
        raise ValueError("lark inbox processed-state schema is invalid")
    values = payload.get("message_ids") if isinstance(payload, Mapping) else []
    return {
        str(value)
        for value in (values if isinstance(values, list) else [])
        if MESSAGE_ID_PATTERN.fullmatch(str(value))
    }


def _event_from_payload(
    payload: object,
    *,
    bot_display_name: str | None = None,
    bot_app_id: str | None = None,
    bot_open_id: str | None = None,
    allow_text_addressing: bool = False,
) -> dict[str, Any] | None:
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != EVENT_SCHEMA_VERSION
    ):
        return None
    message_id = str(payload.get("message_id") or "").strip()
    event_id = str(payload.get("event_id") or message_id).strip()
    if not MESSAGE_ID_PATTERN.fullmatch(message_id) or not EVENT_ID_PATTERN.fullmatch(
        event_id
    ):
        return None
    content = " ".join(str(payload.get("content") or "").split())[:1200]
    raw_attachment_count = payload.get("attachment_count", 0)
    if (
        isinstance(raw_attachment_count, bool)
        or not isinstance(raw_attachment_count, int)
        or not 0 <= raw_attachment_count <= 50
    ):
        return None
    if not content and raw_attachment_count == 0:
        return None
    event = {
        "event_id": event_id,
        "message_id": message_id,
        "create_time": str(payload.get("create_time") or "")[:40],
        "content": content,
        "attachment_count": raw_attachment_count,
    }
    sender_type = str(payload.get("sender_type") or "").strip().lower()
    if sender_type:
        if not SENDER_TYPE_PATTERN.fullmatch(sender_type):
            return None
        event["sender_type"] = sender_type
    if "route_key" in payload:
        route_key = str(payload.get("route_key") or "").strip()
        if not ROUTE_KEY_PATTERN.fullmatch(route_key):
            return None
        event["route_key"] = route_key
    parent_id = str(payload.get("parent_id") or "").strip()
    root_id = str(payload.get("root_id") or "").strip()
    if MESSAGE_ID_PATTERN.fullmatch(parent_id):
        event["parent_id"] = parent_id
    if MESSAGE_ID_PATTERN.fullmatch(root_id):
        event["root_id"] = root_id
    if isinstance(payload.get("thread_id"), str):
        event["thread_id"] = payload["thread_id"][:200]
    thread_context = payload.get("thread_context")
    if isinstance(thread_context, Mapping):
        # Provider adapter bounds the observation; shared TS validates lineage
        # and selects the visible excerpt. Keep it immutable on ingress replay.
        messages = thread_context.get("messages")
        if isinstance(messages, list) and len(messages) <= 64:
            rows = []
            for raw in messages:
                if not isinstance(raw, Mapping):
                    continue
                text = raw.get("content")
                text = text if isinstance(text, str) else ""
                sender = raw.get("sender")
                sender = sender if isinstance(sender, Mapping) else {}
                rows.append({
                    **{key: raw.get(key, "")[:200] if isinstance(raw.get(key), str) else ""
                       for key in ("message_id", "conversation_id", "thread_id")},
                    "position": raw.get("position"), "content": text[:16000],
                    "content_truncated": len(text) > 16000 or raw.get("content_truncated") is True,
                    "sender": {key: sender.get(key, "")[:200] if isinstance(sender.get(key), str) else ""
                               for key in ("id", "kind")},
                    "created_at": raw.get("created_at", "")[:80] if isinstance(raw.get("created_at"), str) else "",
                })
            event["thread_context"] = {
                **{key: thread_context.get(key, "")[:200] if isinstance(thread_context.get(key), str) else ""
                   for key in ("root_message_id", "conversation_id", "thread_id")},
                "messages": rows, "truncated": thread_context.get("truncated") is True,
            }
    reply_context_verified = payload.get("reply_context_verified") is True
    event["reply_context_verified"] = reply_context_verified
    event["reply_to_bot"] = bool(
        reply_context_verified
        and "parent_id" in event
        and payload.get("reply_to_bot") is True
    )
    # Transport only bounded provider text and its source identifiers. The TS
    # conversation owner checks the parent/conversation join before model use.
    reply_context = payload.get("reply_context")
    if isinstance(reply_context, Mapping):
        reply_content = reply_context.get("content")
        reply_content = reply_content if isinstance(reply_content, str) else ""
        event["reply_context"] = {
            "message_id": str(reply_context.get("message_id") or "")[:200],
            "conversation_id": str(reply_context.get("conversation_id") or "")[:200],
            "content": reply_content[:16000],
            "content_truncated": len(reply_content) > 16000
            or reply_context.get("content_truncated") is True,
        }
    addressed_to_bot = bool(
        event["reply_to_bot"]
        or (
            bot_display_name is not None
            and lark_event_mentions_bot(
                payload,
                bot_display_name=bot_display_name,
                bot_app_id=bot_app_id,
                bot_open_id=bot_open_id,
                allow_text_fallback=allow_text_addressing,
            )
        )
        or (bot_display_name is None and payload.get("addressed_to_bot") is True)
    )
    event["addressed_to_bot"] = addressed_to_bot
    historical_context_only = payload.get("historical_context_only") is True
    if historical_context_only:
        # History catch-up is evidence recovery, never delayed Turn authority.
        # Preserve this provenance so a later authorized manager Turn may use
        # the item as context even when the old message contained a real Bot
        # mention.  Live delivery still owns all execution authority.
        event["historical_context_only"] = True

    mentions = payload.get("mentions")
    provider_mention_count = 0
    target_mention_count = 0
    if isinstance(mentions, list):
        provider_mentions = [item for item in mentions if isinstance(item, Mapping)]
        provider_mention_count = len(provider_mentions)
        expected_identities = {
            value
            for value in (
                str(bot_app_id or "").strip(),
                str(bot_open_id or "").strip(),
            )
            if value
        }
        expected_name = _normalized_mention_name(bot_display_name)
        if expected_identities:
            target_mention_count = sum(
                bool(
                    lark_provider_mention_identities(item).intersection(
                        expected_identities
                    )
                )
                for item in provider_mentions
            )
        elif expected_name:
            target_mention_count = sum(
                _normalized_mention_name(item.get("name")) == expected_name
                for item in provider_mentions
            )
        elif payload.get("mentioned") is True and provider_mention_count == 1:
            # Provider history may identify the current Bot with a typed
            # `mentioned` flag while an inbox route intentionally has no
            # reply/display-name configuration. Preserve the exact single-
            # mention proof without persisting the provider identity.
            target_mention_count = 1
    else:
        raw_provider_count = payload.get("provider_mention_count")
        raw_target_count = payload.get("target_mention_count")
        if (
            type(raw_provider_count) is int
            and type(raw_target_count) is int
            and 0 <= raw_target_count <= raw_provider_count <= 50
        ):
            provider_mention_count = raw_provider_count
            target_mention_count = raw_target_count
    event["provider_mention_count"] = provider_mention_count
    event["target_mention_count"] = target_mention_count

    stored_addressing_source = str(payload.get("addressing_source") or "").strip()
    if stored_addressing_source and stored_addressing_source not in ADDRESSING_SOURCES:
        return None
    if event["reply_to_bot"]:
        addressing_source = "verified_reply"
    elif target_mention_count:
        addressing_source = "provider_mention"
    elif addressed_to_bot:
        addressing_source = stored_addressing_source or "legacy_text"
    else:
        addressing_source = ""
    if historical_context_only:
        # Preserve what the provider observed without allowing a recovered
        # historical mention/reply to re-enter the live attention or reply
        # authority lanes.  Both the generic urgency projector and the Lark
        # settlement adapter consume the normalized flags below.
        event["historical_was_addressed_to_bot"] = addressed_to_bot
        event["historical_was_reply_to_bot"] = event["reply_to_bot"]
        if addressing_source:
            event["historical_addressing_source"] = addressing_source
        event["addressed_to_bot"] = False
        event["reply_to_bot"] = False
        event["reply_context_verified"] = False
    elif addressing_source:
        event["addressing_source"] = addressing_source
    return event


def _event_from_file(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _event_from_payload(payload)


def _pending_events(config: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    inbox = config["inbox_path"]
    processed = _load_processed(inbox / "processed.json")
    events: dict[str, dict[str, Any]] = {}
    invalid_count = 0
    for path in sorted(inbox.glob("*.json")) if inbox.is_dir() else []:
        if path.name == "processed.json":
            continue
        event = _event_from_file(path)
        if event is None:
            invalid_count += 1
            continue
        events.setdefault(event["message_id"], event)
    pending = [event for key, event in events.items() if key not in processed]
    pending.sort(key=lambda item: (item["create_time"], item["message_id"]))
    return pending, len(events), invalid_count


def project_lark_event_inbox_urgency(
    *,
    project: str | Path,
    config_path: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bind the generic urgency projector to the extension-owned Lark contract."""

    load_lark_event_inbox_config(project=project, config_path=config_path)
    urgency = project_operator_inbox_urgency(
        project=project,
        config_path=config_path,
        source_contract=LARK_OPERATOR_INBOX_SOURCE_CONTRACT,
        now=now,
    )
    urgency["schema_version"] = "lark_event_inbox_urgency_v0"
    urgency["reply_to_bot_count"] = urgency.pop("reply_to_operator_count")
    return urgency


def _event_attention_kind(
    event: Mapping[str, Any],
    *,
    bot_display_name: str,
    capture_scope: str,
) -> str | None:
    if event.get("historical_context_only") is True:
        return None
    normalized = dict(event)
    normalized["addressed_to_operator"] = bool(
        event.get("addressed_to_bot") is True
        or lark_event_mentions_bot(event, bot_display_name=bot_display_name)
    )
    normalized["reply_to_operator"] = bool(
        event.get("reply_context_verified") is True
        and event.get("reply_to_bot") is True
    )
    kind = operator_inbox_attention_kind(
        normalized,
        operator_display_name=bot_display_name,
        capture_scope=capture_scope,
    )
    return "reply_to_bot" if kind == "reply_to_operator" else kind


def _normalized_mention_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lstrip("@").split()).casefold()


def lark_event_mentions_bot(
    event: Mapping[str, Any],
    *,
    bot_display_name: str,
    bot_app_id: str | None = None,
    bot_open_id: str | None = None,
    allow_text_fallback: bool = True,
) -> bool:
    """Recognize one provider-native or exact legacy Bot mention."""

    mentions = event.get("mentions")
    expected_identities = {
        value
        for value in (
            str(bot_app_id or "").strip(),
            str(bot_open_id or "").strip(),
        )
        if value
    }
    expected_name = _normalized_mention_name(bot_display_name)
    if isinstance(mentions, list):
        provider_mentions = [
            mention for mention in mentions if isinstance(mention, Mapping)
        ]
        if expected_identities:
            return any(
                lark_provider_mention_identities(mention).intersection(
                    expected_identities
                )
                for mention in provider_mentions
            )
        if expected_name:
            return any(
                _normalized_mention_name(mention.get("name")) == expected_name
                for mention in provider_mentions
            )
        return event.get("mentioned") is True and len(provider_mentions) == 1
    if event.get("mentioned") is True:
        return True
    # A provider-supplied structured negative is authoritative.  In
    # particular, do not reinterpret an @mention of somebody else because the
    # surrounding message also discusses LoopX.
    if "mentions" in event or "mentioned" in event:
        return False
    if not expected_name or not allow_text_fallback:
        return False
    content = str(event.get("content") or "")
    escaped = re.escape(" ".join(str(bot_display_name).strip().lstrip("@").split()))
    return bool(
        escaped
        and re.search(
            rf"(?:^|[\s,，!！?？;；:：])@{escaped}(?:$|[\s,，!！?？;；:：])",
            content,
            re.IGNORECASE,
        )
    )


def ingest_lark_event_inbox(
    *,
    project: str | Path,
    config_path: str | Path,
    events: Sequence[object],
    execute: bool = False,
) -> dict[str, Any]:
    """Persist canonical compact events supplied by a host collector or backfill."""

    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"]:
        raise ValueError("lark event inbox is not enabled")
    inbox = config["inbox_path"]
    existing_message_ids = {
        event["message_id"]
        for path in sorted(inbox.glob("*.json"))
        if inbox.is_dir()
        if path.name != "processed.json"
        if (event := _event_from_file(path)) is not None
    }
    accepted: dict[str, dict[str, Any]] = {}
    invalid_count = 0
    duplicate_count = 0
    for payload in events:
        event = _event_from_payload(
            payload,
            bot_display_name=str(config["reply"].get("bot_display_name") or ""),
            bot_app_id=str(config["reply"].get("bot_app_id") or ""),
            bot_open_id=str(config["reply"].get("bot_open_id") or ""),
            allow_text_addressing=config["capture_scope"] == "addressed_only",
        )
        if event is None:
            invalid_count += 1
            continue
        message_id = event["message_id"]
        if message_id in existing_message_ids or message_id in accepted:
            duplicate_count += 1
            continue
        accepted[message_id] = event

    if execute and accepted:
        inbox.mkdir(parents=True, exist_ok=True)
        os.chmod(inbox, 0o700)
        for event in accepted.values():
            path = inbox / f"{event['message_id']}.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(
                    {"schema_version": EVENT_SCHEMA_VERSION, **event},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            os.chmod(path, 0o600)
    return {
        "ok": True,
        "schema_version": "lark_event_inbox_ingest_v0",
        "execute": execute,
        "requested_count": len(events),
        "accepted_count": len(accepted),
        "invalid_count": invalid_count,
        "duplicate_count": duplicate_count,
        "write_performed": bool(execute and accepted),
        "local_private_content_returned": False,
        "external_reads_performed": False,
        "external_writes_performed": False,
    }


def inspect_lark_event_inbox(
    *, project: str | Path, config_path: str | Path, limit: int = 20
) -> dict[str, Any]:
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"]:
        return {
            "ok": True,
            "schema_version": "lark_event_inbox_projection_v0",
            "enabled": False,
            "configured": config["configured"],
            "capture_scope": config["capture_scope"],
            "thread_complete": config["thread_complete"],
            "pending_count": 0,
            "items": [],
            "local_private_content_returned": False,
            "external_reads_performed": False,
        }
    inbox = config["inbox_path"]
    processed = _load_processed(inbox / "processed.json")
    pending, captured_count, invalid_count = _pending_events(config)
    requested_limit = int(limit)
    # Internal retention/retry callers use zero to read the complete pending
    # projection. Public callers retain the historical 1..100 bound.
    bounded = (
        pending
        if requested_limit <= 0
        else pending[: max(1, min(requested_limit, 100))]
    )
    return {
        "ok": True,
        "schema_version": "lark_event_inbox_projection_v0",
        "enabled": True,
        "configured": True,
        "capture_scope": config["capture_scope"],
        "thread_complete": config["thread_complete"],
        "reply_guidance": {
            "placement_policy": config["reply"]["placement_policy"],
            "editorial_style": config["reply"]["editorial_style"],
        },
        "coverage_warning": (
            None
            if config["thread_complete"]
            else "addressed_only capture does not include unaddressed thread replies"
        ),
        "pending_count": len(pending),
        "captured_count": captured_count,
        "returned_count": len(bounded),
        "processed_count": len(processed),
        "invalid_count": invalid_count,
        "items": bounded,
        "local_private_content_returned": bool(bounded),
        "external_reads_performed": False,
        "instruction": (
            "For an actionable item, first run `loopx lark-inbox processing` for "
            "its message_id, then translate it into a todo, vision correction, PR "
            "update, or no-follow-up rationale. Send and verify any required reply "
            "before acknowledging the message_id. Follow reply_guidance for "
            "placement and editorial style. If no reply is required, run "
            "`loopx lark-inbox material-review` with a committed effect receipt "
            "or an explicit no-follow-up rationale; the command settles the "
            "message idempotently without sending a reply."
        ),
    }


def _material_review_ledger(inbox: Path) -> tuple[Path, dict[str, Any]]:
    path = inbox / "material-review" / "receipts.json"
    if not path.is_file():
        return path, {
            "schema_version": MATERIAL_REVIEW_LEDGER_SCHEMA_VERSION,
            "receipts": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != MATERIAL_REVIEW_LEDGER_SCHEMA_VERSION
        or not isinstance(payload.get("receipts"), dict)
    ):
        raise ValueError("lark material-review receipt ledger is invalid")
    return path, payload


def _write_material_review_ledger(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _acknowledge_lark_event_inbox_state(
    *,
    inbox: Path,
    message_ids: Sequence[str],
    execute: bool,
) -> dict[str, Any]:
    processed_path = inbox / "processed.json"
    existing = _load_processed(processed_path)
    added = [value for value in message_ids if value not in existing]
    if execute and added:
        inbox.mkdir(parents=True, exist_ok=True)
        os.chmod(inbox, 0o700)
        merged = sorted(existing | set(added))
        payload = {
            "schema_version": PROCESSED_SCHEMA_VERSION,
            "message_ids": merged,
            "last_processed_at": datetime.now(UTC).isoformat(),
        }
        temporary = processed_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(processed_path)
        os.chmod(processed_path, 0o600)
    return {
        "ok": True,
        "schema_version": "lark_event_inbox_ack_v0",
        "execute": execute,
        "requested_count": len(message_ids),
        "new_count": len(added),
        "already_acknowledged_count": len(message_ids) - len(added),
        "write_performed": bool(execute and added),
        "message_ids": list(message_ids),
        "local_private_content_captured": False,
        "external_writes_performed": False,
    }


def settle_lark_event_inbox_material_review(
    *,
    project: str | Path,
    config_path: str | Path,
    message_id: str,
    effect_receipt: Mapping[str, Any] | None = None,
    no_follow_up_reason: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Settle one unaddressed material only after an event-bound durable decision."""

    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"] or not config["material_review"]["enabled"]:
        raise ValueError("lark inbox material_review is not enabled")
    message = str(message_id or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(message):
        raise ValueError("material review requires a valid Lark message id")
    inbox = config["inbox_path"]
    event = _event_from_file(inbox / f"{message}.json")
    if event is None:
        raise ValueError("material review message is not captured")
    kind = _event_attention_kind(
        event,
        bot_display_name=str(config["reply"].get("bot_display_name") or ""),
        capture_scope=str(config["capture_scope"]),
    )
    if kind is not None:
        raise ValueError("addressed Lark events must use the reply_due settlement path")

    normalized_reason = " ".join(str(no_follow_up_reason or "").split())
    if effect_receipt is not None and normalized_reason:
        raise ValueError(
            "material review accepts either effect_receipt or no_follow_up_reason"
        )
    if effect_receipt is None and not normalized_reason:
        raise ValueError(
            "material review requires effect_receipt or no_follow_up_reason"
        )
    if len(normalized_reason) > 400:
        raise ValueError("material review no_follow_up_reason is too long")
    receipt: Mapping[str, Any]
    if effect_receipt is not None:
        receipt = effect_receipt
    else:
        rationale_digest = hashlib.sha256(
            f"{event['event_id']}\0{normalized_reason}".encode()
        ).hexdigest()[:24]
        receipt = {
            "schema_version": EFFECT_RECEIPT_SCHEMA_VERSION,
            "event_id": str(event["event_id"]),
            "effect_id": f"no-follow-up-{rationale_digest}",
            "effect_kind": ExternalEffectKind.NO_FOLLOW_UP.value,
            "status": "committed",
        }
    decision = decide_external_event_ack(
        event_id=str(event["event_id"]),
        effect_receipt=receipt,
        response_policy=ExternalResponsePolicy.NO_RESPONSE.value,
    )
    if not decision["ack_allowed"]:
        return {
            "ok": False,
            "schema_version": "lark_event_inbox_material_review_settlement_v0",
            "status": str(decision["reason"]),
            "execute": execute,
            "event_ref": external_event_ref(str(event["event_id"])),
            "ack_decision": decision,
            "write_performed": False,
            "local_private_content_returned": False,
        }
    event_ref = external_event_ref(str(event["event_id"]))
    effect_ref = external_event_ref(str(receipt.get("effect_id") or ""))
    compact_receipt = {
        "event_ref": event_ref,
        "effect_ref": effect_ref,
        "effect_kind": str(receipt.get("effect_kind") or ""),
        "status": "committed",
    }
    lock = (
        exclusive_file_lock(
            inbox / ".state" / "settlement",
            operation="settle_lark_event_inbox_material_review",
        )
        if execute
        else nullcontext()
    )
    with lock:
        ledger_path, ledger = _material_review_ledger(inbox)
        receipts = dict(ledger["receipts"])
        existing_receipt = receipts.get(message)
        if existing_receipt is not None and existing_receipt != compact_receipt:
            return {
                "ok": False,
                "schema_version": "lark_event_inbox_material_review_settlement_v0",
                "status": "material_review_receipt_conflict",
                "execute": execute,
                "event_ref": event_ref,
                "write_performed": False,
                "local_private_content_returned": False,
            }
        processed = _load_processed(inbox / "processed.json")
        if message in processed and existing_receipt is None:
            return {
                "ok": False,
                "schema_version": "lark_event_inbox_material_review_settlement_v0",
                "status": "material_review_receipt_missing_for_processed_event",
                "execute": execute,
                "event_ref": event_ref,
                "write_performed": False,
                "local_private_content_returned": False,
            }
        ledger_written = False
        if execute and existing_receipt is None:
            receipts[message] = compact_receipt
            _write_material_review_ledger(
                ledger_path,
                {
                    "schema_version": MATERIAL_REVIEW_LEDGER_SCHEMA_VERSION,
                    "receipts": receipts,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
            ledger_written = True
        acknowledged = _acknowledge_lark_event_inbox_state(
            inbox=inbox,
            message_ids=[message],
            execute=execute,
        )
    return {
        "ok": True,
        "schema_version": "lark_event_inbox_material_review_settlement_v0",
        "status": (
            "preview_ready"
            if not execute and int(acknowledged.get("new_count") or 0) > 0
            else "settled"
            if int(acknowledged.get("new_count") or 0) > 0
            else "already_settled"
        ),
        "execute": execute,
        "event_ref": event_ref,
        "effect_kind": str(receipt.get("effect_kind") or ""),
        "effect_ref": effect_ref,
        "receipt_recorded": existing_receipt is not None or ledger_written,
        "write_performed": bool(
            ledger_written or acknowledged.get("write_performed") is True
        ),
        "local_private_content_returned": False,
    }


def lark_event_inbox_contains_text(
    *, project: str | Path, config_path: str | Path, text: str
) -> bool:
    """Return content-free exact evidence from persisted configured-chat history."""

    needle = " ".join(str(text or "").split())
    if not needle:
        raise ValueError("lark inbox history lookup requires non-empty text")
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"] or not config["thread_complete"]:
        return False
    inbox = config["inbox_path"]
    return any(
        needle in str(event.get("content") or "")
        for path in (inbox.glob("*.json") if inbox.is_dir() else [])
        if path.name != "processed.json"
        if (event := _event_from_file(path)) is not None
    )


def acknowledge_lark_event_inbox(
    *,
    project: str | Path,
    config_path: str | Path,
    message_ids: Sequence[str],
    execute: bool = False,
) -> dict[str, Any]:
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"]:
        raise ValueError("lark event inbox is not enabled")
    normalized = list(
        dict.fromkeys(
            str(value).strip()
            for value in message_ids
            if MESSAGE_ID_PATTERN.fullmatch(str(value).strip())
        )
    )
    if not normalized or len(normalized) != len(message_ids):
        raise ValueError("ack requires valid Lark message ids")
    inbox = config["inbox_path"]
    lock = (
        exclusive_file_lock(
            inbox / ".state" / "settlement",
            operation="acknowledge_lark_event_inbox",
        )
        if execute
        else nullcontext()
    )
    with lock:
        return _acknowledge_lark_event_inbox_state(
            inbox=inbox,
            message_ids=normalized,
            execute=execute,
        )
