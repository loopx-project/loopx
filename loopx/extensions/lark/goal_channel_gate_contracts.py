"""Human-gate, blocked-notice, and quota projection contracts for Lark Goal Channel."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import TypedDict, cast

from ...control_plane.runtime.public_safety import public_safe_compact_text
from .private_json import write_private_json_atomic

Payload = dict[str, object]
PayloadView = Mapping[str, object]


class GateNoticeProjection(TypedDict, total=False):
    goal_id: str
    items: list[Payload]
    requests: list[Payload]

# Compatibility export for older callers. Scheduler-projected reminder windows
# now own repeat-notification timing.
DEFAULT_GATE_COOLDOWN_SECONDS = 3600
HUMAN_GATE_AUTO_NOTIFY_SETTING = "human_gate_auto_notify_enabled"
BLOCKED_NOTICE_AUTO_NOTIFY_SETTING = "blocked_notice_auto_notify_enabled"
BLOCKED_NOTICE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION = (
    "loopx_goal_channel_blocked_notice_marker_v0"
)
HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION = "loopx_goal_channel_auto_notify_marker_v0"

_MATERIAL_ITEM_KEYS: tuple[str, ...] = (
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
_REMINDER_IDENTITY_KEYS: tuple[str, ...] = (
    "policy",
    "failed_at",
    "next_reminder_at",
    "cooldown_minutes",
    "reminder_window_minutes",
)


class BlockedNoticeReceiptState(str, Enum):
    """Provider delivery lifecycle; it does not mutate canonical Todo state."""

    PENDING = "pending"
    SENT_UNVERIFIED = "sent_unverified"
    DELIVERED = "delivered"
    RESUMED = "resumed"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


BLOCKED_NOTICE_RETIRED_STATES = frozenset(
    {
        BlockedNoticeReceiptState.RESUMED,
        BlockedNoticeReceiptState.RESOLVED,
        BlockedNoticeReceiptState.SUPERSEDED,
    }
)


def semantic_key(*parts: str) -> str:
    digest = hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def blocked_notice_receipt_matches_target(
    key: str,
    receipt: PayloadView,
    *,
    goal_id: str,
    chat_id: str,
) -> bool:
    if not chat_id or receipt.get("kind") != "blocked_notice":
        return False
    if receipt.get("chat_id"):
        return receipt["chat_id"] == chat_id
    # Older initial receipts encode the destination in their semantic key.
    # An unbound historical/reopened receipt cannot prove this target's delivery.
    return key == semantic_key(
        goal_id,
        "lark",
        "blocked_notice",
        str(receipt.get("blocker_identity") or ""),
        str(receipt.get("blocker_revision") or ""),
        chat_id,
    )


def _automation_flag(binding: PayloadView | None, setting: str) -> bool:
    automation = (
        binding.get("automation")
        if isinstance(binding, Mapping) and isinstance(binding.get("automation"), Mapping)
        else None
    )
    return isinstance(automation, Mapping) and automation.get(setting) is True


def human_gate_auto_notify_enabled(binding: PayloadView | None) -> bool:
    return _automation_flag(binding, HUMAN_GATE_AUTO_NOTIFY_SETTING)


def blocked_notice_auto_notify_enabled(binding: PayloadView | None) -> bool:
    return _automation_flag(binding, BLOCKED_NOTICE_AUTO_NOTIFY_SETTING)


def human_gate_auto_notify_marker_path(binding_path: Path, goal_id: str) -> Path:
    safe_goal_id = str(goal_id or "").strip()
    if (
        not safe_goal_id
        or safe_goal_id in {".", ".."}
        or "/" in safe_goal_id
        or "\\" in safe_goal_id
    ):
        raise ValueError("Goal Channel goal id must be a single path segment")
    return binding_path.with_name(f"{binding_path.stem}.{safe_goal_id}.human-gate-auto-notify.json")


def _marker_enabled(path: Path, schema_version: str) -> bool:
    marker_path = path.expanduser()
    if not marker_path.exists():
        return False
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A damaged enabled marker must leave the sink visibly failed.
        return True
    return bool(
        isinstance(payload, Mapping)
        and payload.get("schema_version") == schema_version
        and payload.get("enabled") is True
    )


def _write_marker(path: Path, schema_version: str) -> None:
    write_private_json_atomic(path, {"schema_version": schema_version, "enabled": True})


def human_gate_auto_notify_marker_enabled(path: Path) -> bool:
    return _marker_enabled(path, HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION)


def write_human_gate_auto_notify_marker(path: Path) -> None:
    _write_marker(path, HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION)


def clear_human_gate_auto_notify_marker(path: Path) -> None:
    path.expanduser().unlink(missing_ok=True)


def blocked_notice_auto_notify_marker_path(binding_path: Path, goal_id: str) -> Path:
    human_marker = human_gate_auto_notify_marker_path(binding_path, goal_id)
    return human_marker.with_name(
        human_marker.name.replace(".human-gate-auto-notify.json", ".blocked-notice-auto-notify.json")
    )


def blocked_notice_auto_notify_marker_enabled(path: Path) -> bool:
    return _marker_enabled(path, BLOCKED_NOTICE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION)


def write_blocked_notice_auto_notify_marker(path: Path) -> None:
    _write_marker(path, BLOCKED_NOTICE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION)


def clear_blocked_notice_auto_notify_marker(path: Path) -> None:
    path.expanduser().unlink(missing_ok=True)


def _summary_gate_items(quota_packet: PayloadView) -> list[PayloadView]:
    raw_summary = quota_packet.get("user_todo_summary")
    summary = raw_summary if isinstance(raw_summary, Mapping) else None
    if summary is None:
        return []
    for key in ("gate_open_items", "first_open_items"):
        raw_items = summary.get(key)
        if not isinstance(raw_items, list):
            continue
        items = [cast(PayloadView, item) for item in raw_items if isinstance(item, Mapping)]
        if items:
            return items
    return []


def _gate_identity_from_items(quota_packet: PayloadView, items: list[PayloadView]) -> str:
    for item in items:
        identity = str(item.get("todo_id") or item.get("gate_id") or "").strip()
        if identity:
            return identity
    return str(
        quota_packet.get("gate_id")
        or quota_packet.get("operator_gate_id")
        or "unidentified_gate"
    ).strip()


def quota_human_gate_identity(quota_packet: PayloadView) -> str:
    return _gate_identity_from_items(quota_packet, _summary_gate_items(quota_packet))


def _quota_human_gate_items(quota_packet: PayloadView) -> list[Payload]:
    return [dict(item) for item in _summary_gate_items(quota_packet)[:3]]


def notification_request_snapshot(status: PayloadView, goal_id: str) -> Payload:
    """Adapt the existing complete same-read Goal Todo projection for transport.

    This never admits requests: the shared TS owner joins only already selected
    quota IDs and verifies their version/lifecycle before taking display fields.
    """
    raw_queue = status.get("attention_queue")
    queue = raw_queue if isinstance(raw_queue, Mapping) else {}
    raw_items = queue.get("items")
    rows = (
        [
            item
            for item in raw_items
            if isinstance(item, Mapping) and item.get("goal_id") == goal_id
        ]
        if isinstance(raw_items, list)
        else []
    )
    raw_summary = rows[0].get("user_todos") if len(rows) == 1 else None
    summary = raw_summary if isinstance(raw_summary, Mapping) else {}
    summary_items = summary.get("items", [])
    return {"goal_id": goal_id, "items": summary_items}


def _gate_notice_projection(*, goal_id: str, quota_packet: PayloadView) -> GateNoticeProjection:
    # Scheduling labels cannot substitute for a decision request body.
    from ...capabilities.manager_context.goal_attention import notice_request_fields
    from ...control_plane.effect_runtime import effect_runtime_result

    requests: list[Payload] = []
    for item in _summary_gate_items(quota_packet)[:3]:
        fields = cast(Payload, notice_request_fields(item))
        fields["request_id"] = public_safe_compact_text(
            cast(str | None, item.get("todo_id") or item.get("gate_id")),
            limit=120,
        )
        fields["reason"] = fields["note"]
        requests.append(fields)
    notice_input: Payload = {"goal_id": goal_id, "requests": requests}
    if "request_snapshot" in quota_packet:
        raw_snapshot = quota_packet.get("request_snapshot")
        snapshot = raw_snapshot if isinstance(raw_snapshot, Mapping) else {}
        raw_snapshot_items = snapshot.get("items", [])
        snapshot_items = (
            [
                notice_request_fields(item)
                for item in raw_snapshot_items
                if isinstance(item, Mapping)
            ]
            if isinstance(raw_snapshot_items, list)
            else []
        )
        notice_input["request_snapshot"] = {
            "goal_id": snapshot.get("goal_id"),
            "items": snapshot_items,
        }
    return cast(
        GateNoticeProjection,
        dict(
            cast(PayloadView, effect_runtime_result("presentation.decision_notice.project", notice_input))
        ),
    )


def quota_human_gate_state_generation(quota_packet: PayloadView) -> str:
    """Fingerprint the material state of the currently projected human gate."""
    raw_items = _summary_gate_items(quota_packet)
    material_items: list[Payload] = []
    for item in raw_items[:3]:
        row: Payload = {
            key: item.get(key)
            for key in _MATERIAL_ITEM_KEYS
            if item.get(key) is not None
        }
        text = public_safe_compact_text(
            cast(str | None, item.get("text") or item.get("title")),
            limit=300,
        )
        if text:
            row["text"] = text
        material_items.append(row)
    material: Payload = {
        "gate_identity": _gate_identity_from_items(quota_packet, raw_items),
        "items": material_items,
    }
    if "request_snapshot" in quota_packet:
        # Delivery idempotency must cover the complete selected display body,
        # not only the first 180/300 characters of a scheduling label.
        material["request_content"] = _gate_notice_projection(
            quota_packet=quota_packet,
            goal_id=str(quota_packet.get("goal_id") or ""),
        )
    if not material_items:
        material["state"] = str(quota_packet.get("state") or "")
        material["question"] = public_safe_compact_text(
            cast(
                str | None,
                quota_packet.get("gate_prompt")
                or quota_packet.get("operator_question")
                or quota_packet.get("reason"),
            ),
            limit=900,
        )
    return semantic_key(
        "human_gate_state_v0",
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def quota_human_gate_reminder_generation(quota_packet: PayloadView) -> str | None:
    cooldown = quota_packet.get("user_gate_notification_cooldown")
    if not isinstance(cooldown, Mapping) or cooldown.get("notification_due") is not True:
        return None
    reminder_identity = {
        key: cooldown.get(key)
        for key in _REMINDER_IDENTITY_KEYS
        if cooldown.get(key) is not None
    }
    return semantic_key(
        "human_gate_reminder_v0",
        json.dumps(reminder_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def quota_human_gate_notification_suppressed(quota_packet: PayloadView) -> bool:
    cooldown = quota_packet.get("user_gate_notification_cooldown")
    return bool(isinstance(cooldown, Mapping) and cooldown.get("notification_suppressed") is True)


def quota_selects_human_gate(quota_packet: PayloadView) -> bool:
    if quota_human_gate_notification_suppressed(quota_packet):
        return False
    interaction = quota_packet.get("interaction_contract")
    if isinstance(interaction, Mapping):
        user_channel = interaction.get("user_channel")
        if isinstance(user_channel, Mapping):
            if user_channel.get("notify") != "NOTIFY":
                return False
            if user_channel.get("action_required") is True or bool(user_channel.get("actions")):
                return True
    return bool(
        quota_packet.get("state") == "operator_gate"
        or quota_packet.get("notify_user_on_gate") is True
        or quota_packet.get("notify_user_on_open_todo") is True
    )
