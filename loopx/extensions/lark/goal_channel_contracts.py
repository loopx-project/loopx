from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...registry import registry_goals
from ...file_lock import exclusive_file_lock
from .private_json import write_private_json_atomic


GOAL_CHANNEL_BINDING_SCHEMA_VERSION = "loopx_goal_channel_lark_binding_v0"
GOAL_CHANNEL_OPERATION_SCHEMA_VERSION = "loopx_goal_channel_operation_v0"
GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION = "loopx_goal_channel_connection_set_v0"
# Compatibility export for older callers. Scheduler-projected reminder windows
# now own repeat-notification timing.
DEFAULT_GATE_COOLDOWN_SECONDS = 3600
HUMAN_GATE_AUTO_NOTIFY_SETTING = "human_gate_auto_notify_enabled"
HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION = (
    "loopx_goal_channel_auto_notify_marker_v0"
)
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
GATE_ACTION_PREFIX = re.compile(
    r"^(?:(?:[-*•]|\d+[.)])\s*)?(?:\[[ xX]\]\s*)?(?:\[P\d+\]\s*)?"
)


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


LARK_TOPIC_EVENT_REJECTION_REASONS = {
    item.value
    for item in LarkTopicEventDecisionReason
    if item is not LarkTopicEventDecisionReason.MATCHED
}


def normalize_lark_topic_event_rejection_reason(value: Any) -> str | None:
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
        raise ValueError(
            "Goal Channel default binding requires a project source registry"
        )
    return expanded.parent / "goal-channel.json"


def read_goal_channel_binding(path: Path) -> dict[str, Any]:
    binding_path = path.expanduser()
    if not binding_path.exists():
        return {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": {},
        }
    payload = json.loads(binding_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Goal Channel binding root must be a JSON object")
    if payload.get("schema_version") != GOAL_CHANNEL_BINDING_SCHEMA_VERSION:
        raise ValueError(
            f"Goal Channel binding must use {GOAL_CHANNEL_BINDING_SCHEMA_VERSION}"
        )
    bindings = payload.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("Goal Channel binding requires an object `bindings`")
    return payload


def write_goal_channel_binding(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    binding_path = path.expanduser()
    write_private_json_atomic(binding_path, payload)


def goal_from_registry(
    registry: Mapping[str, Any],
    goal_id: str,
) -> dict[str, Any]:
    safe_goal_id = str(goal_id or "").strip()
    match = next(
        (
            dict(goal)
            for goal in registry_goals(dict(registry))
            if str(goal.get("id") or "") == safe_goal_id
        ),
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


def bindings_for_goal(
    payload: Mapping[str, Any],
    goal_id: str,
    *,
    provider_target: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    bindings = payload.get("bindings")
    stored = bindings.get(goal_id) if isinstance(bindings, Mapping) else None
    if not isinstance(stored, Mapping):
        return []
    raw_connections = stored.get("connections")
    if stored.get(
        "schema_version"
    ) == GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION and isinstance(
        raw_connections, Mapping
    ):
        candidates = [
            {**dict(item), "connection_id": str(connection_id)}
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
    return [
        _resolve_goal_binding(candidate, provider_target=provider_target)
        for candidate in candidates
    ]


def _resolve_goal_binding(
    binding: Mapping[str, Any],
    *,
    provider_target: Mapping[str, Any] | None,
) -> dict[str, Any]:
    resolved = dict(binding)
    target_ref = str(resolved.get("target_ref") or "")
    if not target_ref or provider_target is None:
        return resolved
    if str(provider_target.get("name") or "") != target_ref:
        raise ValueError("Goal Channel provider target does not match target_ref")
    if str(provider_target.get("provider") or "") != str(
        resolved.get("provider") or ""
    ):
        raise ValueError("Goal Channel provider target does not match provider")
    target_channel = provider_target.get("channel")
    target_identity = provider_target.get("identity")
    if not isinstance(target_channel, Mapping) or not isinstance(
        target_identity, Mapping
    ):
        raise ValueError("Goal Channel provider target is incomplete")
    binding_channel = resolved.get("channel")
    binding_channel = (
        dict(binding_channel) if isinstance(binding_channel, Mapping) else {}
    )
    resolved["channel"] = {
        **dict(target_channel),
        **(
            {"pinned_message_id": binding_channel["pinned_message_id"]}
            if binding_channel.get("pinned_message_id")
            else {}
        ),
    }
    resolved["identity"] = dict(target_identity)
    return resolved


def binding_for_goal(
    payload: Mapping[str, Any],
    goal_id: str,
    *,
    provider_target: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    connection_id: str | None = None,
) -> dict[str, Any] | None:
    # Select one raw connection before resolving its provider target. A target
    # belongs to one connection; applying it to every sibling lets an unrelated
    # Agent's target invalidate the requested Agent's otherwise valid binding.
    candidates = bindings_for_goal(payload, goal_id)
    selected: dict[str, Any] | None = None
    if connection_id:
        selected = next(
            (item for item in candidates if item.get("connection_id") == connection_id),
            None,
        )
    elif agent_id is not None:
        selected = next(
            (item for item in candidates if item.get("agent_id") == agent_id),
            None,
        )
    else:
        bindings = payload.get("bindings")
        stored = bindings.get(goal_id) if isinstance(bindings, Mapping) else None
        default_id = (
            str(stored.get("default_connection_id") or "")
            if isinstance(stored, Mapping)
            else ""
        )
        if default_id:
            selected = next(
                (
                    item
                    for item in candidates
                    if item.get("connection_id") == default_id
                ),
                None,
            )
        if selected is None and candidates:
            # Keep the invalid-default fallback aligned with the writer in
            # _without_goal_topic_connection, which promotes min(connection_id).
            selected = min(
                candidates,
                key=lambda item: str(item.get("connection_id") or ""),
            )
    return (
        _resolve_goal_binding(selected, provider_target=provider_target)
        if selected is not None
        else None
    )


def human_gate_auto_notify_enabled(binding: Mapping[str, Any] | None) -> bool:
    automation = (
        binding.get("automation")
        if isinstance(binding, Mapping)
        and isinstance(binding.get("automation"), Mapping)
        else {}
    )
    return automation.get(HUMAN_GATE_AUTO_NOTIFY_SETTING) is True


def human_gate_auto_notify_marker_path(
    binding_path: Path,
    goal_id: str,
) -> Path:
    safe_goal_id = str(goal_id or "").strip()
    if (
        not safe_goal_id
        or safe_goal_id in {".", ".."}
        or "/" in safe_goal_id
        or "\\" in safe_goal_id
    ):
        raise ValueError("Goal Channel goal id must be a single path segment")
    return binding_path.with_name(
        f"{binding_path.stem}.{safe_goal_id}.human-gate-auto-notify.json"
    )


def human_gate_auto_notify_marker_enabled(path: Path) -> bool:
    marker_path = path.expanduser()
    if not marker_path.exists():
        return False
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return bool(
        isinstance(payload, Mapping)
        and payload.get("schema_version")
        == HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION
        and payload.get("enabled") is True
    )


def write_human_gate_auto_notify_marker(path: Path) -> None:
    write_private_json_atomic(
        path,
        {
            "schema_version": HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION,
            "enabled": True,
        },
    )


def clear_human_gate_auto_notify_marker(path: Path) -> None:
    path.expanduser().unlink(missing_ok=True)


def quota_human_gate_identity(quota_packet: Mapping[str, Any]) -> str:
    summary = quota_packet.get("user_todo_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    for key in ("gate_open_items", "first_open_items"):
        items = summary.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            identity = str(item.get("todo_id") or item.get("gate_id") or "").strip()
            if identity:
                return identity
    return str(
        quota_packet.get("gate_id")
        or quota_packet.get("operator_gate_id")
        or "unidentified_gate"
    ).strip()


def _quota_human_gate_items(
    quota_packet: Mapping[str, Any],
) -> list[dict[str, Any]]:
    summary = quota_packet.get("user_todo_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    for key in ("gate_open_items", "first_open_items"):
        raw_items = summary.get(key)
        if not isinstance(raw_items, list):
            continue
        items = [dict(item) for item in raw_items if isinstance(item, Mapping)]
        if items:
            return items[:3]
    return []


def quota_human_gate_state_generation(
    quota_packet: Mapping[str, Any],
) -> str:
    """Fingerprint the material state of the currently projected human gate."""

    material_items = []
    for item in _quota_human_gate_items(quota_packet):
        material_items.append(
            {
                key: item.get(key)
                for key in (
                    "todo_id",
                    "gate_id",
                    "status",
                    "task_class",
                    "updated_at",
                    "material_change_generation",
                    "decision_scope",
                    "decision_outcome",
                    "blocks_agent",
                    "global_gate",
                    "unblocks_todo_id",
                )
                if item.get(key) is not None
            }
        )
        text = public_safe_compact_text(
            item.get("text") or item.get("title"),
            limit=300,
        )
        if text:
            material_items[-1]["text"] = text
    material: dict[str, Any] = {
        "gate_identity": quota_human_gate_identity(quota_packet),
        "items": material_items,
    }
    if not material_items:
        material["state"] = str(quota_packet.get("state") or "")
        material["question"] = public_safe_compact_text(
            quota_packet.get("gate_prompt")
            or quota_packet.get("operator_question")
            or quota_packet.get("reason"),
            limit=900,
        )
    return semantic_key(
        "human_gate_state_v0",
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def quota_human_gate_reminder_generation(
    quota_packet: Mapping[str, Any],
) -> str | None:
    cooldown = quota_packet.get("user_gate_notification_cooldown")
    if (
        not isinstance(cooldown, Mapping)
        or cooldown.get("notification_due") is not True
    ):
        return None
    reminder_identity = {
        key: cooldown.get(key)
        for key in (
            "policy",
            "failed_at",
            "next_reminder_at",
            "cooldown_minutes",
            "reminder_window_minutes",
        )
        if cooldown.get(key) is not None
    }
    return semantic_key(
        "human_gate_reminder_v0",
        json.dumps(
            reminder_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def quota_human_gate_notification_suppressed(
    quota_packet: Mapping[str, Any],
) -> bool:
    cooldown = quota_packet.get("user_gate_notification_cooldown")
    return bool(
        isinstance(cooldown, Mapping)
        and cooldown.get("notification_suppressed") is True
    )


def quota_selects_human_gate(quota_packet: Mapping[str, Any]) -> bool:
    if quota_human_gate_notification_suppressed(quota_packet):
        return False
    interaction = quota_packet.get("interaction_contract")
    if isinstance(interaction, Mapping):
        user_channel = interaction.get("user_channel")
        if isinstance(user_channel, Mapping):
            if user_channel.get("notify") != "NOTIFY":
                return False
            if user_channel.get("action_required") is True or bool(
                user_channel.get("actions")
            ):
                return True
    return bool(
        quota_packet.get("state") == "operator_gate"
        or quota_packet.get("notify_user_on_gate") is True
        or quota_packet.get("notify_user_on_open_todo") is True
    )


def save_goal_binding(
    *,
    binding_path: Path,
    payload: Mapping[str, Any],
    goal_id: str,
    binding: Mapping[str, Any],
) -> None:
    """Compatibility writer for the Goal's explicit default connection.

    Existing notification/setup callers intentionally address the default
    route. Preserve peer Agent routes when the Goal has migrated to a
    connection set instead of collapsing the set back to v0.
    """
    bindings = payload.get("bindings")
    stored = bindings.get(goal_id) if isinstance(bindings, Mapping) else None
    if (
        isinstance(stored, Mapping)
        and stored.get("schema_version") == GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION
    ):
        save_goal_connection(
            binding_path=binding_path,
            payload=payload,
            goal_id=goal_id,
            binding={
                **dict(binding),
                "connection_id": str(stored.get("default_connection_id") or ""),
            },
            make_default=True,
        )
        return
    mutable_bindings = dict(bindings) if isinstance(bindings, Mapping) else {}
    mutable_bindings[goal_id] = dict(binding)
    write_goal_channel_binding(
        binding_path,
        {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": mutable_bindings,
        },
    )


def save_goal_connection(
    *,
    binding_path: Path,
    payload: Mapping[str, Any],
    goal_id: str,
    binding: Mapping[str, Any],
    make_default: bool = False,
) -> str:
    """Upsert one Agent route while retaining v0 single-binding read compatibility."""
    connection_id = str(
        binding.get("connection_id") or ""
    ) or goal_channel_connection_id(
        goal_id,
        str(binding.get("agent_id") or "") or None,
    )
    all_bindings = payload.get("bindings")
    mutable_bindings = dict(all_bindings) if isinstance(all_bindings, Mapping) else {}
    stored = mutable_bindings.get(goal_id)
    if (
        isinstance(stored, Mapping)
        and stored.get("schema_version") == GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION
        and isinstance(stored.get("connections"), Mapping)
    ):
        connections = dict(stored["connections"])
        default_id = str(stored.get("default_connection_id") or "")
    else:
        connections = {}
        default_id = ""
        if isinstance(stored, Mapping):
            legacy_id = goal_channel_connection_id(
                goal_id,
                str(stored.get("agent_id") or "") or None,
            )
            connections[legacy_id] = dict(stored)
            default_id = legacy_id
    connections[connection_id] = {
        **dict(binding),
        "connection_id": connection_id,
    }
    if make_default or not default_id:
        default_id = connection_id
    mutable_bindings[goal_id] = {
        "schema_version": GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION,
        "default_connection_id": default_id,
        "connections": connections,
    }
    write_goal_channel_binding(
        binding_path,
        {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": mutable_bindings,
        },
    )
    return connection_id


def semantic_key(*parts: str) -> str:
    digest = hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def provider_idempotency_key(receipt: str) -> str:
    return f"loopx-{receipt.removeprefix('sha256:')[:32]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: Any) -> datetime | None:
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
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    packet: dict[str, Any] = {
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


def assert_public_packet(packet: Mapping[str, Any]) -> None:
    from .goal_channel_transport import CHAT_ID_PATTERN, MESSAGE_ID_PATTERN

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in PRIVATE_PACKET_KEYS:
                    raise ValueError(
                        f"public Goal Channel packet contains private key `{key}`"
                    )
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            if CHAT_ID_PATTERN.search(value) or MESSAGE_ID_PATTERN.search(value):
                raise ValueError(
                    "public Goal Channel packet contains a private provider id"
                )

    visit(packet)


def goal_objective(goal: Mapping[str, Any]) -> str:
    return public_safe_compact_text(
        goal.get("objective") or goal.get("description") or goal.get("id"),
        limit=300,
    )


def control_message(
    *,
    goal_id: str,
    objective: str,
    kanban_url: str,
) -> str:
    lines = [
        f"LoopX Goal: {goal_id}",
        f"Objective: {objective}",
        "LoopX remains the source of truth for todos, gates, quota, and evidence.",
    ]
    if kanban_url:
        lines.append(f"Kanban: {kanban_url}")
    return "\n".join(lines)


def gate_message(
    *,
    goal_id: str,
    objective: str,
    quota_packet: Mapping[str, Any],
    kanban_url: str,
) -> tuple[str, str]:
    question = public_safe_compact_text(
        quota_packet.get("gate_prompt")
        or quota_packet.get("operator_question")
        or quota_packet.get("reason")
        or "A human decision is required.",
        limit=900,
    )
    # Scheduling labels cannot substitute for a decision request body.
    from ...control_plane.effect_runtime import effect_runtime_result

    notice = effect_runtime_result("presentation.decision_notice.project", {
        "requests": [
            {
                "request_id": public_safe_compact_text(item.get("todo_id") or item.get("gate_id"), limit=120),
                "text": public_safe_compact_text(item.get("text"), limit=900),
                "reason": public_safe_compact_text(item.get("note") or item.get("reason"), limit=450),
                "evidence": public_safe_compact_text(item.get("evidence"), limit=450),
            }
            for item in _quota_human_gate_items(quota_packet)
        ],
    })
    lines = [
        "LoopX · Action required",
        "",
        f"Goal: {goal_id}",
    ]
    if objective and objective != goal_id:
        lines.append(f"Objective: {objective}")
    lines.extend(["", "Decision requests:"])
    if notice["source"] == "unavailable":
        lines.append("Request details are unavailable. Open the current request in LoopX; a scheduling summary is not a decision body.")
    for index, item in enumerate(notice["items"], start=1):
        body = GATE_ACTION_PREFIX.sub("", item["text"]).strip()
        lines.append(f"{index}. {body}")
        if item["request_id"]:
            lines.append(f"   Request: {item['request_id']}")
        if item["reason"]:
            lines.append(f"   Context: {item['reason']}")
        if item["evidence"]:
            lines.append(f"   Evidence: {item['evidence']}")
    lines.extend([
        "",
        "Review the current request in LoopX before deciding; this notification is a bounded preview.",

        "Unchanged gate state will stay quiet until an explicit reminder window.",
    ])
    if notice["items"]:
        lines.append("Reply with the request ID (or number), your decision and a one-sentence reason.")
    if kanban_url:
        lines.extend(["", f"Kanban: {kanban_url}"])
    return "\n".join(lines), question


def reusable_goal_topic_root(
    payload: Mapping[str, Any],
    goal_id: str,
    *,
    connection_id: str,
    provider_target: Mapping[str, Any] | None,
    chat_id: str,
) -> str:
    """Return the established topic root when reconnect may adopt it.

    A reconnect may skip sending a fresh Goal Topic only when the stored
    connection for this ``connection_id`` is enabled, still resolves through
    ``provider_target`` (target_ref/provider/chat all validated by the typed
    reader), and its stored root is a well-formed message id for this chat.
    Anything else returns "" so the caller sends a new topic.
    """

    from .goal_channel_transport import MESSAGE_ID_PATTERN

    try:
        existing = binding_for_goal(
            payload,
            goal_id,
            connection_id=connection_id,
        )
        if existing is not None:
            existing = _resolve_goal_binding(existing, provider_target=provider_target)
    except ValueError:
        return ""
    if not existing or existing.get("enabled") is not True:
        return ""
    prior_topic = (
        existing.get("topic") if isinstance(existing.get("topic"), Mapping) else {}
    )
    prior_channel = (
        existing.get("channel") if isinstance(existing.get("channel"), Mapping) else {}
    )
    candidate_root = str(
        prior_topic.get("root_message_id")
        or prior_channel.get("pinned_message_id")
        or ""
    )
    if (
        MESSAGE_ID_PATTERN.fullmatch(candidate_root)
        and str(prior_channel.get("chat_id") or "") == chat_id
    ):
        return candidate_root
    return ""
