"""Stateless paging of one original Chat transcript; no storage or execution owner."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from ...control_plane.content_digest import BARE_SHA256_PATTERN


def page_chat_messages(session_id: str, messages: list[dict[str, Any]], *,
                 limit: int, cursor: str | None = None) -> dict[str, Any]:
    """Page an append-ordered frozen prefix; a cursor is a read hint, not authority."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("message_limit must be between 1 and 200")
    offset, total = 0, len(messages)
    expected_digest = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
                raise ValueError()
            decoded = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4),
                                                 altchars=b"-_", validate=True))
            source, offset, total, expected_digest = decoded
            if (source != session_id or type(offset) is not int or type(total) is not int
                    or not 0 < offset < total <= len(messages)
                    or not isinstance(expected_digest, str)
                    or not BARE_SHA256_PATTERN.fullmatch(expected_digest)):
                raise ValueError()
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid history cursor") from exc
    digest = hashlib.sha256(json.dumps(messages[:total], sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    if expected_digest is not None and expected_digest != digest:
        raise ValueError("history cursor source changed; restart the read")
    end = min(offset + limit, total)
    next_cursor = base64.urlsafe_b64encode(json.dumps([session_id, end, total, digest],
        separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=") if end < total else None
    return {"messages": messages[offset:end], "message_page": {
        "schema_version": "loopx_chat_message_page_v1", "offset": offset,
        "total": total, "snapshot_sha256": digest, "next_cursor": next_cursor,
    }}
