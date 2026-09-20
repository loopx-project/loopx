"""Bounded multi-message delivery for a manager answer that does not fit once.

A persisted manager answer that the provider rejects for length used to be left
on the channel as nothing at all. This module owns the alternative: split the
already-validated body into ordered parts, send the parts the provider has not
accepted yet, and record that progress in the same durable delivery state the
single-message path uses, so a retry resumes instead of re-sending.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .inbox_reply import reply_lark_event_inbox
from .outbound import DEFAULT_LARK_TEXT_LIMIT, split_lark_outbound_text

# An oversized answer is delivered as a bounded sequence rather than a flood:
# past this many parts the answer keeps its leading parts and ends with a note
# naming where the full text is already saved.
MANAGER_REPLY_MAX_PARTS = 8
MANAGER_REPLY_OVERFLOW_NOTE = (
    "本条答复超过可投递长度，上面已按顺序发送前面的部分；"
    "完整答复保存在 LoopX 管家会话中。"
)


def plan_manager_reply_parts(reply_text: str) -> tuple[list[str], bool]:
    """Return the parts to deliver and whether the remainder was replaced."""

    parts = split_lark_outbound_text(
        reply_text,
        limit=DEFAULT_LARK_TEXT_LIMIT,
        max_parts=MANAGER_REPLY_MAX_PARTS,
        overflow_note=MANAGER_REPLY_OVERFLOW_NOTE,
    )
    truncated = len(parts) == MANAGER_REPLY_MAX_PARTS and (
        MANAGER_REPLY_OVERFLOW_NOTE in parts[-1]
    )
    return parts, truncated


def deliver_manager_reply_parts(
    *,
    parts: list[str],
    delivery_state: dict[str, Any],
    delivery_path: Path,
    write_delivery,
    reply_runner: Any,
    root: Path,
    config_path: Path,
    message_id: str,
    content_format: str,
) -> Mapping[str, Any] | None:
    """Send the remaining parts in order, resuming from the recorded count.

    The durable state, not the return value, is the record of what the provider
    accepted: each accepted part advances `delivery_parts_sent` before the next
    send, and a rejected part stops the sequence there.
    """

    recorded_count = delivery_state.get("delivery_part_count")
    sent = delivery_state.get("delivery_parts_sent")
    if (
        not isinstance(recorded_count, int)
        or isinstance(recorded_count, bool)
        or recorded_count != len(parts)
        or not isinstance(sent, int)
        or isinstance(sent, bool)
        or not 0 <= sent <= len(parts)
    ):
        # A different split than the one on record cannot be resumed safely.
        sent = 0
    delivery_state.update(
        delivery_part_count=len(parts),
        delivery_parts_sent=sent,
        format_degraded=True,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    write_delivery(delivery_path, delivery_state)
    last: Mapping[str, Any] | None = None
    for index in range(sent, len(parts)):
        last = reply_lark_event_inbox(
            project=root,
            config_path=config_path,
            message_id=message_id,
            text=parts[index],
            content_format=content_format,
            execute=True,
            runner=reply_runner,
        )
        if not last.get("ok"):
            delivery_state.update(
                delivery_parts_sent=index,
                last_delivery_status=str(last.get("status") or "reply_failed"),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            write_delivery(delivery_path, delivery_state)
            return None
        delivery_state.update(
            delivery_parts_sent=index + 1,
            reply_idempotency_key=last.get("idempotency_key"),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        write_delivery(delivery_path, delivery_state)
    return last


def deliver_manager_reply_after_length_failure(
    *,
    reply_text: str,
    delivery_state: dict[str, Any],
    delivery_path: Path,
    write_delivery,
    reply_runner: Any,
    root: Path,
    config_path: Path,
    message_id: str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Deliver one over-limit manager answer as bounded parts.

    Returns the last accepted reply, or ``None`` plus the reason to report when
    a part was rejected. Plain text is the only format a split can promise, so
    the caller has already degraded presentation before calling this.
    """

    parts, truncated = plan_manager_reply_parts(reply_text)
    if truncated:
        delivery_state.update(
            delivery_truncated=True,
            delivery_source_char_count=len(reply_text),
        )
    reply = deliver_manager_reply_parts(
        parts=parts,
        delivery_state=delivery_state,
        delivery_path=delivery_path,
        write_delivery=write_delivery,
        reply_runner=reply_runner,
        root=root,
        config_path=config_path,
        message_id=message_id,
        content_format="text",
    )
    return reply, (None if reply is not None else "reply_part_delivery_incomplete")


def manager_part_delivery_pending_result(
    *,
    reason: str,
    delivery_state: Mapping[str, Any],
    goal_id: str,
    inbox_config_ref: str,
) -> dict[str, Any]:
    """The typed pending result for a part sequence the provider interrupted."""

    return {
        "ok": False,
        "status": "reply_delivery_pending",
        "reason": reason,
        "delivery_part_count": delivery_state.get("delivery_part_count"),
        "delivery_parts_sent": delivery_state.get("delivery_parts_sent"),
        "format_degraded": True,
        "goal_id": goal_id,
        "inbox_config_ref": inbox_config_ref,
        "source_acknowledged": False,
    }


def manager_part_delivery_readback(delivery_state: Mapping[str, Any]) -> dict[str, Any]:
    """Part accounting for a delivered answer, empty when it was one message."""

    if not (
        isinstance(delivery_state.get("delivery_part_count"), int)
        and isinstance(delivery_state.get("delivery_parts_sent"), int)
    ):
        return {}
    return {
        "delivery_part_count": delivery_state["delivery_part_count"],
        "delivery_parts_sent": delivery_state["delivery_parts_sent"],
        "delivery_truncated": bool(delivery_state.get("delivery_truncated")),
    }
