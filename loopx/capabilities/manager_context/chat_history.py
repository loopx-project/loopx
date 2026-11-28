"""Stateless paging of one original Chat transcript; no storage or execution owner."""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import json
from threading import Lock

from ...control_plane.content_digest import BARE_SHA256_PATTERN

MAX_KNOWN_HISTORY_MESSAGES = 50_000
_SNAPSHOT_DIGEST_CACHE_MAX = 64
_SNAPSHOT_DIGEST_LOCK = Lock()
_SNAPSHOT_DIGEST_CACHE: OrderedDict[tuple[str, str | None, bytes, bytes, int], str] = OrderedDict()


def _snapshot_prefix_digest(
    session_id: str, turn_id: str | None, messages: list[dict[str, object]], total: int,
) -> str:
    journal_sha256 = getattr(messages, "journal_sha256", None)
    markers_sha256 = getattr(messages, "markers_sha256", b"")
    cache_key: tuple[str, str | None, bytes, bytes, int] | None = None
    if isinstance(journal_sha256, bytes) and isinstance(markers_sha256, bytes):
        cache_key = (session_id, turn_id, journal_sha256, markers_sha256, total)
        with _SNAPSHOT_DIGEST_LOCK:
            cached = _SNAPSHOT_DIGEST_CACHE.get(cache_key)
            if cached is not None:
                _SNAPSHOT_DIGEST_CACHE.move_to_end(cache_key)
                return cached
    digest = hashlib.sha256(json.dumps(messages[:total], sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    if cache_key is not None:
        with _SNAPSHOT_DIGEST_LOCK:
            _SNAPSHOT_DIGEST_CACHE[cache_key] = digest
            _SNAPSHOT_DIGEST_CACHE.move_to_end(cache_key)
            while len(_SNAPSHOT_DIGEST_CACHE) > _SNAPSHOT_DIGEST_CACHE_MAX:
                _SNAPSHOT_DIGEST_CACHE.popitem(last=False)
    return digest


def page_chat_messages(session_id: str, messages: list[dict[str, object]], *,
                 limit: int, cursor: str | None = None, turn_id: str | None = None) -> dict[str, object]:
    """Page an append-ordered frozen prefix; a cursor is a read hint, not authority."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("message_limit must be between 1 and 200")
    offset, total = 0, len(messages)
    source_identity = session_id if turn_id is None else [session_id, turn_id]
    expected_digest = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
                raise ValueError()
            decoded = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4),
                                                 altchars=b"-_", validate=True))
            source, offset, total, expected_digest = decoded
            if (source != source_identity or type(offset) is not int or type(total) is not int
                    or not 0 < offset < total <= len(messages)
                    or not isinstance(expected_digest, str)
                    or not BARE_SHA256_PATTERN.fullmatch(expected_digest)):
                raise ValueError()
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid history cursor") from exc
    digest = _snapshot_prefix_digest(session_id, turn_id, messages, total)
    if expected_digest is not None and expected_digest != digest:
        raise ValueError("history cursor source changed; restart the read")
    end = min(offset + limit, total)
    next_cursor = base64.urlsafe_b64encode(json.dumps([source_identity, end, total, digest],
        separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=") if end < total else None
    return {"messages": messages[offset:end], "message_page": {
        "schema_version": "loopx_chat_message_page_v1", "offset": offset,
        "total": total, "snapshot_sha256": digest, "next_cursor": next_cursor,
        **({"turn_id": turn_id} if turn_id is not None else {}),
    }}
