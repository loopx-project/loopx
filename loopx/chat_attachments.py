"""Validation and normalization for bounded Chat image attachments."""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from typing import Any


CHAT_IMAGE_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
CHAT_IMAGE_MAX_COUNT = 4
CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
CHAT_IMAGE_MAX_TOTAL_BYTES = 12 * 1024 * 1024
# Images travel as base64 inside the JSON turn request. Keep the existing
# non-image envelope budget; only this transport receives the image allowance.
CHAT_JSON_MAX_BYTES = 64_000
CHAT_TURN_MAX_BODY_BYTES = (
    CHAT_JSON_MAX_BYTES
    + ((CHAT_IMAGE_MAX_TOTAL_BYTES + 2) // 3) * 4
    + CHAT_IMAGE_MAX_COUNT * (len("data:image/jpeg;base64,") + 4)
)
_CHAT_IMAGE_DATA_URL = re.compile(
    r"^data:(image/(?:gif|jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$"
)


def validate_chat_turn_envelope(body: dict[str, Any]) -> None:
    """Image data must not subsidize oversized text or attachment metadata."""
    envelope = dict(body)
    attachments = body.get("attachments")
    if isinstance(attachments, list):
        envelope["attachments"] = [
            {key: value for key, value in raw.items() if key != "data_url"}
            if isinstance(raw, dict) else raw
            for raw in attachments
        ]
    if len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) > CHAT_JSON_MAX_BYTES:
        raise ValueError("message and attachment metadata exceed the 64KB limit")


def _compact_text(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def normalize_chat_image_attachments(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    if not isinstance(value, list):
        raise ValueError("attachments must be an array")
    if len(value) > CHAT_IMAGE_MAX_COUNT:
        raise ValueError(
            f"at most {CHAT_IMAGE_MAX_COUNT} image attachments are allowed"
        )
    normalized: list[dict[str, Any]] = []
    total_bytes = 0
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("each attachment must be an object")
        unknown = set(raw) - {"data_url", "id", "mime_type", "name", "size"}
        if unknown:
            raise ValueError("unknown image attachment field")
        data_url = str(raw.get("data_url") or "")
        match = _CHAT_IMAGE_DATA_URL.fullmatch(data_url)
        if match is None:
            raise ValueError("image attachment must be a supported base64 data URL")
        mime_type = str(raw.get("mime_type") or match.group(1)).lower()
        if mime_type not in CHAT_IMAGE_TYPES or mime_type != match.group(1):
            raise ValueError("unsupported image attachment type")
        encoded = match.group(2)
        if len(encoded) > ((CHAT_IMAGE_MAX_BYTES + 2) // 3) * 4 + 4:
            raise ValueError("image attachment exceeds the 5MB limit")
        try:
            decoded_size = len(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image attachment contains invalid base64 data") from exc
        if decoded_size > CHAT_IMAGE_MAX_BYTES:
            raise ValueError("image attachment exceeds the 5MB limit")
        declared_size = raw.get("size")
        if declared_size is not None and int(declared_size) != decoded_size:
            raise ValueError("image attachment size does not match its data")
        total_bytes += decoded_size
        if total_bytes > CHAT_IMAGE_MAX_TOTAL_BYTES:
            raise ValueError("image attachments exceed the 12MB total limit")
        normalized.append(
            {
                "data_url": data_url,
                "id": _compact_text(raw.get("id"), limit=160) or uuid.uuid4().hex,
                "mime_type": mime_type,
                "name": _compact_text(raw.get("name"), limit=180)
                or f"image-{len(normalized) + 1}",
                "size": decoded_size,
            }
        )
    return normalized
