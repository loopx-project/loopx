"""Binding persistence, routing enums, and public packet helpers for Lark Goal Channel."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import ParamSpec, TypeVar, cast

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...file_lock import exclusive_file_lock
from ...registry import registry_goals
from .goal_channel_gate_contracts import (
    BLOCKED_NOTICE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION as BLOCKED_NOTICE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION,
    BLOCKED_NOTICE_AUTO_NOTIFY_SETTING as BLOCKED_NOTICE_AUTO_NOTIFY_SETTING,
    BLOCKED_NOTICE_RETIRED_STATES as BLOCKED_NOTICE_RETIRED_STATES,
    DEFAULT_GATE_COOLDOWN_SECONDS as DEFAULT_GATE_COOLDOWN_SECONDS,
    HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION as HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION,
    HUMAN_GATE_AUTO_NOTIFY_SETTING as HUMAN_GATE_AUTO_NOTIFY_SETTING,
    BlockedNoticeReceiptState as BlockedNoticeReceiptState,
    _gate_notice_projection as _gate_notice_projection,
    _quota_human_gate_items as _quota_human_gate_items,
    blocked_notice_auto_notify_enabled as blocked_notice_auto_notify_enabled,
    blocked_notice_auto_notify_marker_enabled as blocked_notice_auto_notify_marker_enabled,
    blocked_notice_auto_notify_marker_path as blocked_notice_auto_notify_marker_path,
    blocked_notice_receipt_matches_target as blocked_notice_receipt_matches_target,
    clear_blocked_notice_auto_notify_marker as clear_blocked_notice_auto_notify_marker,
    clear_human_gate_auto_notify_marker as clear_human_gate_auto_notify_marker,
    human_gate_auto_notify_enabled as human_gate_auto_notify_enabled,
    human_gate_auto_notify_marker_enabled as human_gate_auto_notify_marker_enabled,
    human_gate_auto_notify_marker_path as human_gate_auto_notify_marker_path,
    notification_request_snapshot as notification_request_snapshot,
    quota_human_gate_identity as quota_human_gate_identity,
    quota_human_gate_notification_suppressed as quota_human_gate_notification_suppressed,
    quota_human_gate_reminder_generation as quota_human_gate_reminder_generation,
    quota_human_gate_state_generation as quota_human_gate_state_generation,
    quota_selects_human_gate as quota_selects_human_gate,
    semantic_key as semantic_key,
    write_blocked_notice_auto_notify_marker as write_blocked_notice_auto_notify_marker,
    write_human_gate_auto_notify_marker as write_human_gate_auto_notify_marker,
)
from .private_json import write_private_json_atomic

Payload = dict[str, object]
PayloadView = Mapping[str, object]

GOAL_CHANNEL_BINDING_SCHEMA_VERSION = "loopx_goal_channel_lark_binding_v0"
GOAL_CHANNEL_OPERATION_SCHEMA_VERSION = "loopx_goal_channel_operation_v0"
GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION = "loopx_goal_channel_connection_set_v0"
PRIVATE_PACKET_KEYS = {
    "base_token",
    "chat_id",
    "config_path",
    "message_id",
    "path",
    "profile",
    "sender_profile",
    "table_id",
}


class LarkTopicEventDecisionReason(str, Enum):
    """Typed, content-free outcome of Goal Topic routing."""

    MATCHED = "matched"
    INVALID_EVENT = "invalid_event"
    BINDING_UNAVAILABLE = "binding_unavailable"
    CHAT_MISMATCH = "chat_mismatch"
    TOPIC_MISMATCH = "topic_mismatch"
    ROUTE_AMBIGUOUS = "route_ambiguous"
    SELF_MESSAGE = "self_message"
    INVALID_ROUTING_STATE = "invalid_routing_state"
    NOT_ADDRESSED = "not_addressed"
    HISTORICAL_CONTEXT_ONLY = "historical_context_only"
    BOT_MESSAGE = "bot_message"
    HUMAN_IDENTITY_UNVERIFIED = "human_identity_unverified"


LARK_TOPIC_EVENT_REJECTION_REASONS = {
    item.value
    for item in LarkTopicEventDecisionReason
    if item is not LarkTopicEventDecisionReason.MATCHED
}


def normalize_lark_topic_event_rejection_reason(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized if normalized in LARK_TOPIC_EVENT_REJECTION_REASONS else None


_P = ParamSpec("_P")
_R = TypeVar("_R")


def serialize_goal_binding_mutation(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Serialize a complete binding read/effect/write transaction.

    All public binding writers share this lock, including setup recovery and
    notification receipts. Locking only the final rename cannot protect the
    snapshot read before a provider call. Preview remains non-mutating.
    Decorate entrypoints, not save helpers, to avoid nested kernel locks.
    """
    execute_parameter = inspect.signature(function).parameters.get("execute")
    execute_default = execute_parameter.default if execute_parameter else True

    @wraps(function)
    def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        binding_path = kwargs["binding_path"]
        if not isinstance(binding_path, Path):
            raise TypeError("binding_path must be a Path")
        lock = (
            exclusive_file_lock(binding_path, operation="lark_goal_binding")
            if kwargs.get("execute", execute_default)
            else nullcontext()
        )
        with lock:
            return function(*args, **kwargs)

    return serialized


def default_goal_channel_binding_path(registry_path: Path) -> Path:
    expanded = registry_path.expanduser().resolve()
    if expanded.parent.name != ".loopx":
        raise ValueError("Goal Channel default binding requires a project source registry")
    return expanded.parent / "goal-channel.json"


def read_goal_channel_binding(path: Path) -> Payload:
    binding_path = path.expanduser()
    if not binding_path.exists():
        return {"schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION, "bindings": {}}
    payload = json.loads(binding_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Goal Channel binding root must be a JSON object")
    if payload.get("schema_version") != GOAL_CHANNEL_BINDING_SCHEMA_VERSION:
        raise ValueError(f"Goal Channel binding must use {GOAL_CHANNEL_BINDING_SCHEMA_VERSION}")
    if not isinstance(payload.get("bindings"), dict):
        raise ValueError("Goal Channel binding requires an object `bindings`")
    return cast(Payload, payload)


def write_goal_channel_binding(path: Path, payload: PayloadView) -> None:
    write_private_json_atomic(path.expanduser(), dict(payload) if not isinstance(payload, dict) else payload)


def goal_from_registry(registry: PayloadView, goal_id: str) -> Payload:
    safe_goal_id = str(goal_id or "").strip()
    registry_dict = cast(Payload, registry) if isinstance(registry, dict) else dict(registry)
    match = next(
        (dict(goal) for goal in registry_goals(registry_dict) if str(goal.get("id") or "") == safe_goal_id),
        None,
    )
    if match is None:
        raise ValueError(f"Goal Channel goal `{safe_goal_id}` is not registered")
    return match


def goal_channel_connection_id(goal_id: str, agent_id: str | None) -> str:
    """Return a stable public-safe identity for one Goal recipient route."""
    lane = str(agent_id or "notification-default").strip()
    digest = hashlib.sha256(f"{goal_id}\0{lane}".encode()).hexdigest()[:20]
    return f"lark_{digest}"


def _stored_goal_entry(payload: PayloadView, goal_id: str) -> PayloadView | None:
    bindings = payload.get("bindings")
    stored = bindings.get(goal_id) if isinstance(bindings, Mapping) else None
    return cast(PayloadView, stored) if isinstance(stored, Mapping) else None


def _resolve_goal_binding(
    binding: PayloadView,
    *,
    provider_target: PayloadView | None,
    copy: bool = True,
) -> Payload:
    resolved = dict(binding) if copy or not isinstance(binding, dict) else cast(Payload, binding)
    target_ref = str(resolved.get("target_ref") or "")
    if not target_ref or provider_target is None:
        return resolved
    if str(provider_target.get("name") or "") != target_ref:
        raise ValueError("Goal Channel provider target does not match target_ref")
    if str(provider_target.get("provider") or "") != str(resolved.get("provider") or ""):
        raise ValueError("Goal Channel provider target does not match provider")
    target_channel = provider_target.get("channel")
    target_identity = provider_target.get("identity")
    if not isinstance(target_channel, Mapping) or not isinstance(target_identity, Mapping):
        raise ValueError("Goal Channel provider target is incomplete")
    raw_channel = resolved.get("channel")
    binding_channel = raw_channel if isinstance(raw_channel, Mapping) else {}
    pinned_id = binding_channel.get("pinned_message_id")
    resolved["channel"] = {
        **dict(cast(PayloadView, target_channel)),
        **({"pinned_message_id": pinned_id} if pinned_id else {}),
    }
    resolved["identity"] = dict(cast(PayloadView, target_identity))
    return resolved


def bindings_for_goal(
    payload: PayloadView,
    goal_id: str,
    *,
    provider_target: PayloadView | None = None,
) -> list[Payload]:
    stored = _stored_goal_entry(payload, goal_id)
    if stored is None:
        return []
    raw_connections = stored.get("connections")
    if stored.get("schema_version") == GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION and isinstance(
        raw_connections, Mapping
    ):
        candidates = [
            {**dict(cast(PayloadView, item)), "connection_id": str(connection_id)}
            for connection_id, item in raw_connections.items()
            if isinstance(item, Mapping)
        ]
    else:
        candidates = [
            {
                **dict(stored),
                "connection_id": goal_channel_connection_id(
                    goal_id, str(stored.get("agent_id") or "") or None
                ),
            }
        ]
    return [_resolve_goal_binding(candidate, provider_target=provider_target, copy=False) for candidate in candidates]


def _select_connection_set_binding(
    stored: PayloadView,
    raw_connections: Mapping[object, object],
    *,
    agent_id: str | None,
    connection_id: str | None,
) -> Payload | None:
    if connection_id:
        item = raw_connections.get(connection_id)
        if not isinstance(item, Mapping):
            return None
        return {**dict(cast(PayloadView, item)), "connection_id": str(connection_id)}
    if agent_id is not None:
        for cid, item in raw_connections.items():
            if isinstance(item, Mapping) and item.get("agent_id") == agent_id:
                return {**dict(cast(PayloadView, item)), "connection_id": str(cid)}
        return None
    default_id = str(stored.get("default_connection_id") or "")
    if default_id:
        default_item = raw_connections.get(default_id)
        if isinstance(default_item, Mapping):
            return {**dict(cast(PayloadView, default_item)), "connection_id": default_id}
    best_cid: str | None = None
    best_item: PayloadView | None = None
    for cid, item in raw_connections.items():
        if not isinstance(item, Mapping):
            continue
        cid_text = str(cid)
        if best_cid is None or cid_text < best_cid:
            best_cid = cid_text
            best_item = cast(PayloadView, item)
    return {**dict(best_item), "connection_id": best_cid} if best_item is not None and best_cid is not None else None


def binding_for_goal(
    payload: PayloadView,
    goal_id: str,
    *,
    provider_target: PayloadView | None = None,
    agent_id: str | None = None,
    connection_id: str | None = None,
) -> Payload | None:
    # Select one raw connection before resolving its provider target. A target
    # belongs to one connection; applying it to every sibling lets an unrelated
    # Agent's target invalidate the requested Agent's otherwise valid binding.
    stored = _stored_goal_entry(payload, goal_id)
    if stored is None:
        return None
    raw_connections = stored.get("connections")
    if stored.get("schema_version") == GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION and isinstance(
        raw_connections, Mapping
    ):
        selected = _select_connection_set_binding(
            stored, raw_connections, agent_id=agent_id, connection_id=connection_id
        )
    else:
        cid = goal_channel_connection_id(goal_id, str(stored.get("agent_id") or "") or None)
        if connection_id and cid != connection_id:
            selected = None
        elif connection_id is None and agent_id is not None and stored.get("agent_id") != agent_id:
            selected = None
        else:
            selected = {**dict(stored), "connection_id": cid}
    return _resolve_goal_binding(selected, provider_target=provider_target, copy=False) if selected is not None else None


def save_goal_binding(
    *,
    binding_path: Path,
    payload: PayloadView,
    goal_id: str,
    binding: PayloadView,
) -> None:
    """Compatibility writer for the Goal's explicit default connection.

    Existing notification/setup callers intentionally address the default
    route. Preserve peer Agent routes when the Goal has migrated to a
    connection set instead of collapsing the set back to v0.
    """
    stored = _stored_goal_entry(payload, goal_id)
    if stored is not None and stored.get("schema_version") == GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION:
        save_goal_connection(
            binding_path=binding_path,
            payload=payload,
            goal_id=goal_id,
            binding={**dict(binding), "connection_id": str(stored.get("default_connection_id") or "")},
            make_default=True,
        )
        return
    raw_bindings = payload.get("bindings")
    mutable_bindings: Payload = dict(cast(PayloadView, raw_bindings)) if isinstance(raw_bindings, Mapping) else {}
    mutable_bindings[goal_id] = dict(binding)
    write_goal_channel_binding(
        binding_path,
        {"schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION, "bindings": mutable_bindings},
    )


def save_goal_connection(
    *,
    binding_path: Path,
    payload: PayloadView,
    goal_id: str,
    binding: PayloadView,
    make_default: bool = False,
) -> str:
    """Upsert one Agent route while retaining v0 single-binding read compatibility."""
    connection_id = str(binding.get("connection_id") or "") or goal_channel_connection_id(
        goal_id, str(binding.get("agent_id") or "") or None
    )
    all_bindings = payload.get("bindings")
    mutable_bindings: Payload = dict(cast(PayloadView, all_bindings)) if isinstance(all_bindings, Mapping) else {}
    stored = mutable_bindings.get(goal_id)
    if (
        isinstance(stored, Mapping)
        and stored.get("schema_version") == GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION
        and isinstance(stored.get("connections"), Mapping)
    ):
        connections: Payload = dict(cast(PayloadView, stored["connections"]))
        default_id = str(stored.get("default_connection_id") or "")
    else:
        connections = {}
        default_id = ""
        if isinstance(stored, Mapping):
            legacy_id = goal_channel_connection_id(goal_id, str(stored.get("agent_id") or "") or None)
            connections[legacy_id] = dict(cast(PayloadView, stored))
            default_id = legacy_id
    connections[connection_id] = {**dict(binding), "connection_id": connection_id}
    if make_default or not default_id:
        default_id = connection_id
    mutable_bindings[goal_id] = {
        "schema_version": GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION,
        "default_connection_id": default_id,
        "connections": connections,
    }
    write_goal_channel_binding(
        binding_path,
        {"schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION, "bindings": mutable_bindings},
    )
    return connection_id


def provider_idempotency_key(receipt: str) -> str:
    return f"loopx-{receipt.removeprefix('sha256:')[:32]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def operation_packet(
    *,
    ok: bool,
    goal_id: str | None,
    operation: str,
    execute: bool,
    status: str,
    public_summary: str,
    external_write_performed: bool = False,
    readback_verified: bool = False,
    idempotency_key: str | None = None,
    receipt_id: str | None = None,
    blocker: str | None = None,
    details: PayloadView | None = None,
) -> Payload:
    packet: Payload = {
        "schema_version": GOAL_CHANNEL_OPERATION_SCHEMA_VERSION,
        "ok": ok,
        "goal_id": goal_id,
        "provider": "lark",
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
    assert_public_packet(packet)
    return packet


def assert_public_packet(packet: PayloadView) -> None:
    from .identity_shapes import LARK_CHAT_ID_SEARCH, LARK_MESSAGE_ID_SEARCH

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in PRIVATE_PACKET_KEYS:
                    raise ValueError(f"public Goal Channel packet contains private key `{key}`")
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            if LARK_CHAT_ID_SEARCH.search(value) or LARK_MESSAGE_ID_SEARCH.search(value):
                raise ValueError("public Goal Channel packet contains a private provider id")

    visit(packet)


def goal_objective(goal: PayloadView) -> str:
    return cast(
        str,
        public_safe_compact_text(
            cast(str | None, goal.get("objective") or goal.get("description") or goal.get("id")),
            limit=300,
        ),
    )


def control_message(*, goal_id: str, objective: str, kanban_url: str) -> str:
    lines = [
        f"LoopX Goal: {goal_id}",
        f"Objective: {objective}",
        "LoopX remains the source of truth for todos, gates, quota, and evidence.",
    ]
    if kanban_url:
        lines.append(f"Kanban: {kanban_url}")
    return "\n".join(lines)


def reusable_goal_topic_root(
    payload: PayloadView,
    goal_id: str,
    *,
    connection_id: str,
    provider_target: PayloadView | None,
    chat_id: str,
) -> str:
    """Return the established topic root when reconnect may adopt it.

    A reconnect may skip sending a fresh Goal Topic only when the stored
    connection for this ``connection_id`` is enabled, still resolves through
    ``provider_target`` (target_ref/provider/chat all validated by the typed
    reader), and its stored root is a well-formed message id for this chat.
    Anything else returns "" so the caller sends a new topic.
    """
    from .identity_shapes import LARK_MESSAGE_ID_PATTERN

    try:
        existing = binding_for_goal(
            payload,
            goal_id,
            connection_id=connection_id,
            provider_target=provider_target,
        )
    except ValueError:
        return ""
    if not existing or existing.get("enabled") is not True:
        return ""
    raw_topic = existing.get("topic")
    prior_topic = raw_topic if isinstance(raw_topic, Mapping) else {}
    raw_channel = existing.get("channel")
    prior_channel = raw_channel if isinstance(raw_channel, Mapping) else {}
    candidate_root = str(
        prior_topic.get("root_message_id") or prior_channel.get("pinned_message_id") or ""
    )
    if LARK_MESSAGE_ID_PATTERN.fullmatch(candidate_root) and str(prior_channel.get("chat_id") or "") == chat_id:
        return candidate_root
    return ""
