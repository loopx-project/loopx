"""Topic-lane routing helpers for private Steward Lark conversations.

Parent conversation bindings retain sole identity and portfolio authority.
Child topic lanes isolate concurrent session queues and upstream threads under
``(binding_id, source_ref, topic_ref)`` without creating a second binding or
mutating historical non-threaded request provenance.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .conversation_identity import identity_ref, lark_private_source
from .identity_shapes import LARK_MESSAGE_ID_PATTERN


def expected_private_source(
    *,
    provider_ref: str,
    event: Mapping[str, Any],
    saved_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return canonical private provenance, preserving an admitted Steward topic_ref."""
    base = lark_private_source(provider_ref=provider_ref, event=event)
    topic_ref = (saved_context or {}).get("topic_ref") if isinstance(saved_context, Mapping) else None
    if isinstance(topic_ref, str) and topic_ref:
        return {**base, "topic_ref": topic_ref}
    return base


def _has_open_session(store: Any, channel_id: str) -> bool:
    return any(
        row.get("status") != "closed"
        for row in store.list_sessions(channel_id=channel_id)
    )


def _steers_active_pre_lane_lobby_turn(
    store: Any,
    lobby_channel: str,
    private_root: Any,
    ancestry_root: str,
) -> bool:
    """Return True only when an active lobby Turn came from the same pre-lane thread."""
    import json
    import re
    from pathlib import Path

    if not isinstance(private_root, Path):
        return False
    for row in store.list_sessions(channel_id=lobby_channel):
        if row.get("status") == "closed":
            continue
        session = store.load_session(str(row["session_id"]))
        active_id = (session or {}).get("active_turn_id")
        if not active_id:
            continue
        turn = store.load_turn(str(session["session_id"]), str(active_id))
        match = re.fullmatch(r"external-([a-f0-9]{24})", str((turn or {}).get("client_turn_id") or ""))
        if match is None:
            continue
        try:
            orig = json.loads((private_root / f"{match[1]}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        orig_source = orig.get("source")
        orig_event = orig.get("event")
        if not isinstance(orig_source, Mapping) or "topic_ref" in orig_source or not isinstance(orig_event, Mapping):
            continue
        orig_root = str(orig_event.get("root_id") or orig_event.get("parent_id") or orig_event.get("message_id") or "").strip()
        if orig_root == ancestry_root:
            return True
    return False


def _lobby_session_busy(store: Any, channel_id: str) -> bool:
    for row in store.list_sessions(channel_id=channel_id):
        if row.get("status") == "closed":
            continue
        session = store.load_session(str(row["session_id"]))
        if session is None or session.get("status") == "closed":
            continue
        if session.get("active_turn_id") or store.queued_turns(str(session["session_id"])):
            return True
    return False


def resolve_private_steward_lane(
    *,
    controller: Any,
    binding: Mapping[str, Any],
    base_source: dict[str, Any],
    event: Mapping[str, Any],
    existing_record: Mapping[str, Any] | None = None,
    history_review: bool = False,
    allow_auto_fork: bool = True,
    is_control_or_mention: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Resolve ``(source, auto_topic_lane)`` for one p2p Steward admission."""
    if existing_record is not None and isinstance(existing_record.get("source"), Mapping):
        frozen = dict(existing_record["source"])
        if all(frozen.get(k) == base_source.get(k) for k in ("source_ref", "sender_ref", "private_human_message")):
            return frozen, bool(existing_record.get("auto_topic_lane"))
    if (
        history_review
        or binding.get("context_kind") != "steward"
        or binding.get("audience", "owner") != "owner"
        or event.get("chat_type") != "p2p"
        or base_source.get("private_human_message") is not True
    ):
        return base_source, False

    store = controller.store
    binding_id = str(binding["binding_id"])
    provider_ref = str(binding["provider_ref"])
    chat_id = str(event.get("chat_id") or "")
    message_id = str(event.get("message_id") or "")
    raw_content = str(event.get("content") or "").strip()
    is_slash_command = raw_content.startswith("/")
    has_mentions = is_control_or_mention or bool(event.get("mentions"))
    ancestry_root = str(event.get("root_id") or event.get("parent_id") or "").strip()

    if ancestry_root and LARK_MESSAGE_ID_PATTERN.fullmatch(ancestry_root):
        topic_ref = identity_ref(provider_ref, chat_id, ancestry_root)
        topic_channel = f"manager.external.native.{binding_id}.{base_source['source_ref']}.topic.{topic_ref}"
        lobby_channel = f"manager.external.native.{binding_id}.{base_source['source_ref']}"
        if (
            not is_slash_command
            and not _has_open_session(store, topic_channel)
            and _steers_active_pre_lane_lobby_turn(
                store,
                lobby_channel,
                controller.store.root / "lark-private-deliveries",
                ancestry_root,
            )
        ):
            return base_source, False
        auto_thread = not bool(event.get("root_id") or event.get("thread_id"))
        return {**base_source, "topic_ref": topic_ref}, auto_thread

    if (
        not allow_auto_fork
        or is_control_or_mention
        or is_slash_command
        or has_mentions
        or str(event.get("message_type") or "") not in {"text", "image", "post"}
        or not LARK_MESSAGE_ID_PATTERN.fullmatch(message_id)
    ):
        return base_source, False

    lobby_channel = f"manager.external.native.{binding_id}.{base_source['source_ref']}"
    if not _lobby_session_busy(store, lobby_channel):
        return base_source, False

    topic_ref = identity_ref(provider_ref, chat_id, message_id)
    return {**base_source, "topic_ref": topic_ref}, True


def finalize_private_steward_lane(
    *,
    transport: Any,
    path: Any,
    record: dict[str, Any],
    binding: Mapping[str, Any],
    base_source: dict[str, Any],
    current_source: dict[str, Any],
    selected: dict[str, Any],
    event: Mapping[str, Any],
    request_ref: str,
    fresh_capture: bool,
    history_review: bool,
    is_control_or_mention: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Upgrade a fresh top-level Steward capture to a Topic Lane when Lobby is busy."""
    from ...chat_store import _atomic_write_json

    if not fresh_capture or (transport.core.root / f"{request_ref}.json").exists() or current_source != base_source:
        return current_source, selected
    resolved_source, auto_topic_lane = resolve_private_steward_lane(
        controller=transport.core.controller,
        binding=binding,
        base_source=base_source,
        event=event,
        history_review=history_review,
        is_control_or_mention=is_control_or_mention,
    )
    if resolved_source != record["source"] or auto_topic_lane != bool(record.get("auto_topic_lane")):
        record["source"] = resolved_source
        if auto_topic_lane:
            record["auto_topic_lane"] = True
        _atomic_write_json(path, record)
        selected = transport.bindings.resolve(binding_id=str(binding["binding_id"]), **resolved_source, include_observation=True)
    return resolved_source, selected
