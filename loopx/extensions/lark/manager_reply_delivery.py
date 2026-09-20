"""Private, restart-safe delivery state for synchronous manager replies."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .event_inbox import MESSAGE_ID_PATTERN, load_lark_event_inbox_config
from .private_json import write_private_json_atomic

SCHEMA_VERSION = "lark_manager_reply_delivery_v0"


def source_digest(event: Mapping[str, Any]) -> str:
    payload = {
        "event_id": str(event.get("event_id") or ""),
        "message_id": str(event.get("message_id") or ""),
        "sender_id": str(event.get("sender_id") or ""),
        "content": str(event.get("content") or ""),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def text_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def delivery_path(
    *, project: Path, config_path: Path, message_id: str
) -> Path:
    if not MESSAGE_ID_PATTERN.fullmatch(message_id):
        raise ValueError("manager delivery requires a valid message id")
    config = load_lark_event_inbox_config(
        project=project, config_path=config_path
    )
    return Path(config["inbox_path"]) / "manager-delivery" / f"{message_id}.json"


def load_delivery(
    *, project: Path, config_path: Path, event: Mapping[str, Any]
) -> tuple[Path, dict[str, Any] | None]:
    message_id = str(event.get("message_id") or "")
    path = delivery_path(
        project=project, config_path=config_path, message_id=message_id
    )
    if not path.is_file():
        return path, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("message_id") != message_id
        or payload.get("source_digest") != source_digest(event)
        or payload.get("status")
        not in {"pending", "sent_verified", "acknowledged"}
    ):
        raise ValueError("manager delivery state is invalid")
    context_material_ids = payload.get("context_material_ids")
    if context_material_ids is not None:
        if not isinstance(context_material_ids, list):
            raise ValueError("manager delivery context material ids are invalid")
        normalized_ids = [str(value) for value in context_material_ids]
        if len(set(normalized_ids)) != len(normalized_ids) or any(
            not MESSAGE_ID_PATTERN.fullmatch(value) for value in normalized_ids
        ):
            raise ValueError("manager delivery context material ids are invalid")
    if payload.get("status") != "acknowledged":
        delivery_text = payload.get("delivery_text")
        if (
            not isinstance(delivery_text, str)
            or not delivery_text.strip()
            or payload.get("delivery_digest") != text_digest(delivery_text)
            or payload.get("content_format") not in {"markdown", "text"}
        ):
            raise ValueError("manager pending delivery content is invalid")
    if payload.get("status") == "sent_verified" and (
        payload.get("external_write_performed") is not True
        or payload.get("verification_performed") is not True
        or payload.get("reply_verified") is not True
        or not isinstance(payload.get("reply_idempotency_key"), str)
        or not str(payload["reply_idempotency_key"]).startswith("sha256:")
    ):
        raise ValueError("manager verified delivery receipt is invalid")
    # Part accounting is optional for answers delivered as one message. When it
    # is present it must be complete: a count without progress, or progress past
    # the count, would let a retry resume from an unverifiable position.
    part_count = payload.get("delivery_part_count")
    parts_sent = payload.get("delivery_parts_sent")
    for value in (part_count, parts_sent):
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError("manager delivery part accounting is invalid")
    if part_count is not None and (parts_sent is None or parts_sent > part_count):
        raise ValueError("manager delivery part accounting is incomplete")
    truncated = payload.get("delivery_truncated")
    if truncated is not None and not isinstance(truncated, bool):
        raise ValueError("manager delivery truncation flag is invalid")
    source_char_count = payload.get("delivery_source_char_count")
    if source_char_count is not None and (
        not isinstance(source_char_count, int)
        or isinstance(source_char_count, bool)
        or source_char_count < 0
    ):
        raise ValueError("manager delivery source length is invalid")
    return path, payload


def write_delivery(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    write_private_json_atomic(path, payload)


def pending_delivery(
    *,
    event: Mapping[str, Any],
    text: str,
    content_format: str,
    effect_receipt: Mapping[str, Any] | None,
    failure_code: str | None,
    context_material_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    normalized_context_ids = [str(value) for value in (context_material_ids or [])]
    if len(set(normalized_context_ids)) != len(normalized_context_ids) or any(
        not MESSAGE_ID_PATTERN.fullmatch(value) for value in normalized_context_ids
    ):
        raise ValueError("manager delivery context material ids are invalid")
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": str(event.get("message_id") or ""),
        "source_digest": source_digest(event),
        "status": "pending",
        "delivery_text": text,
        "delivery_digest": text_digest(text),
        "content_format": content_format,
        "effect_receipt": (
            dict(effect_receipt) if effect_receipt is not None else None
        ),
        # Persist the exact context set used to produce this answer so a
        # transport retry cannot silently switch to newer arrivals.
        "context_material_ids": normalized_context_ids,
        "failure_code": failure_code,
        "format_degraded": False,
        "attempt_count": 0,
        "created_at": now,
        "updated_at": now,
    }
