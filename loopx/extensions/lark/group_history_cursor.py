"""Owner-local cursor and history-window transitions for Lark catch-up."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .private_json import write_private_json_atomic

CURSOR_SCHEMA_VERSION = "lark_group_history_cursor_v1"
LEGACY_CURSOR_SCHEMA_VERSION = "lark_group_history_cursor_v0"


def normalize_group_history_timestamp(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _timestamp_order_key(value: str) -> datetime:
    """Compare normalized RFC3339 values by time, not variable-width text."""

    return datetime.fromisoformat(value)


def group_history_source_fingerprint(
    *,
    route_key: str,
    profile: str,
    chat_id: str,
    event_inbox_config_ref: str,
    inbox_path_ref: str,
    capture_scope: str,
    route_binding_ref: str = "",
) -> str:
    value = (
        f"{route_key}\0{profile}\0{chat_id}\0{event_inbox_config_ref}"
        f"\0{inbox_path_ref}\0{capture_scope}"
    ).encode()
    if route_binding_ref:
        value += f"\0{route_binding_ref}".encode()
    return f"sha256:{hashlib.sha256(value).hexdigest()[:24]}"


def group_history_cursor_path(project: Path, route_key: str) -> Path:
    return project / ".loopx" / "inbox" / ".history" / f"{route_key}.json"


def group_history_cursor_digest(state: Mapping[str, Any]) -> str:
    payload = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def load_group_history_cursor(
    path: Path,
    *,
    route_key: str,
    source_fingerprint: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Lark group-history cursor is unreadable") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") not in {
        LEGACY_CURSOR_SCHEMA_VERSION,
        CURSOR_SCHEMA_VERSION,
    }:
        raise ValueError("Lark group-history cursor schema is invalid")
    legacy_cursor = payload.get("schema_version") == LEGACY_CURSOR_SCHEMA_VERSION
    if payload.get("route_key") != route_key:
        raise ValueError("Lark group-history cursor route binding changed")
    if payload.get("source_fingerprint") != source_fingerprint:
        raise ValueError("Lark group-history cursor source binding changed")
    window_kind = str(payload.get("window_kind") or "")
    if window_kind not in {"initial", "earlier", "forward"}:
        raise ValueError("Lark group-history cursor window kind is invalid")
    window_start = normalize_group_history_timestamp(
        str(payload.get("window_start") or ""), field="window_start"
    )
    window_end = normalize_group_history_timestamp(
        str(payload.get("window_end") or ""), field="window_end"
    )
    coverage_start = normalize_group_history_timestamp(
        str(payload.get("coverage_start") or ""), field="coverage_start"
    )
    raw_coverage_end = payload.get("coverage_end")
    if legacy_cursor:
        # v0 did not retain the latest completed upper bound after an earlier
        # backfill.  Falling back to that window's end may replay already
        # ingested messages, but it cannot skip unseen provider history.
        raw_coverage_end = (
            window_end
            if window_kind == "earlier" or payload.get("history_complete")
            else window_start
        )
    coverage_end = normalize_group_history_timestamp(
        str(raw_coverage_end or ""), field="coverage_end"
    )
    window_start_time = _timestamp_order_key(window_start)
    window_end_time = _timestamp_order_key(window_end)
    coverage_start_time = _timestamp_order_key(coverage_start)
    coverage_end_time = _timestamp_order_key(coverage_end)
    if (
        not window_start_time < window_end_time
        or not coverage_start_time <= coverage_end_time
        or (coverage_end_time > window_end_time and window_kind != "earlier")
    ):
        raise ValueError("Lark group-history cursor window is invalid")
    history_complete = payload.get("history_complete")
    earlier_start_used = payload.get("earlier_start_used")
    if not isinstance(history_complete, bool) or not isinstance(
        earlier_start_used, bool
    ):
        raise TypeError("Lark group-history cursor state flags are invalid")
    next_page_token = payload.get("next_page_token")
    if next_page_token is not None and (
        not isinstance(next_page_token, str) or not next_page_token.strip()
    ):
        raise ValueError("Lark group-history cursor page token is invalid")
    if history_complete and next_page_token is not None:
        raise ValueError("completed Lark group-history cursor cannot have a page token")
    if (
        not history_complete
        and int(payload.get("page_count") or 0) > 0
        and next_page_token is None
    ):
        raise ValueError("active Lark group-history cursor requires a page token")
    page_count = payload.get("page_count")
    message_count = payload.get("message_count")
    if (
        isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or page_count < 1
        or isinstance(message_count, bool)
        or not isinstance(message_count, int)
        or message_count < 0
    ):
        raise ValueError("Lark group-history cursor counters are invalid")
    if window_kind == "initial" and (
        coverage_start != window_start
        or earlier_start_used
        or coverage_end != (window_end if history_complete else window_start)
    ):
        raise ValueError("initial Lark group-history cursor state is inconsistent")
    if window_kind == "earlier" and (
        not earlier_start_used
        or coverage_start != (window_start if history_complete else window_end)
        or coverage_end < window_end
    ):
        raise ValueError("earlier Lark group-history cursor state is inconsistent")
    if window_kind == "forward" and (
        coverage_start > window_start
        or coverage_end != (window_end if history_complete else window_start)
    ):
        raise ValueError("forward Lark group-history cursor state is inconsistent")
    last_page_digest = str(payload.get("last_page_digest") or "")
    if not re.fullmatch(r"[0-9a-f]{16}", last_page_digest):
        raise ValueError("Lark group-history cursor page digest is invalid")
    return {
        "schema_version": CURSOR_SCHEMA_VERSION,
        "route_key": route_key,
        "source_fingerprint": source_fingerprint,
        "window_kind": window_kind,
        "window_start": window_start,
        "window_end": window_end,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "next_page_token": next_page_token,
        "history_complete": history_complete,
        "earlier_start_used": earlier_start_used,
        "page_count": page_count,
        "message_count": message_count,
        "last_page_digest": last_page_digest,
    }


def write_group_history_cursor(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    write_private_json_atomic(path, state)


def _new_group_history_cursor(
    *,
    route_key: str,
    source_fingerprint: str,
    window_start: str,
    window_end: str,
    window_kind: str,
    coverage_start: str,
    coverage_end: str,
    earlier_start_used: bool,
) -> dict[str, Any]:
    return {
        "schema_version": CURSOR_SCHEMA_VERSION,
        "route_key": route_key,
        "source_fingerprint": source_fingerprint,
        "window_kind": window_kind,
        "window_start": window_start,
        "window_end": window_end,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "next_page_token": None,
        "history_complete": False,
        "earlier_start_used": earlier_start_used,
        "page_count": 0,
        "message_count": 0,
        "last_page_digest": "",
    }


def resolve_group_history_window(
    *,
    existing: Mapping[str, Any] | None,
    route_key: str,
    source_fingerprint: str,
    requested_start: str,
    snapshot_end: str,
) -> tuple[dict[str, Any], str, bool]:
    requested_start_time = _timestamp_order_key(requested_start)
    snapshot_end_time = _timestamp_order_key(snapshot_end)
    if existing is None:
        if requested_start_time >= snapshot_end_time:
            raise ValueError("Lark group-history start must be earlier than now")
        return (
            _new_group_history_cursor(
                route_key=route_key,
                source_fingerprint=source_fingerprint,
                window_start=requested_start,
                window_end=snapshot_end,
                window_kind="initial",
                coverage_start=requested_start,
                coverage_end=requested_start,
                earlier_start_used=False,
            ),
            "initialized",
            False,
        )
    state = dict(existing)
    coverage_start_time = _timestamp_order_key(str(state["coverage_start"]))
    coverage_end_time = _timestamp_order_key(str(state["coverage_end"]))
    window_start_time = _timestamp_order_key(str(state["window_start"]))
    if state["history_complete"] is not True:
        forward_start_is_covered = (
            state["window_kind"] == "forward"
            and coverage_start_time <= requested_start_time <= window_start_time
        )
        if requested_start != state["window_start"] and not forward_start_is_covered:
            raise ValueError(
                "active Lark group-history catch-up requires the same start"
            )
        return state, "resumed", False
    if (
        requested_start_time >= coverage_start_time
        and snapshot_end_time <= coverage_end_time
    ):
        return state, "replayed", True
    if requested_start_time < coverage_start_time:
        if state["earlier_start_used"] is True:
            raise ValueError(
                "Lark group-history earlier-start backfill is already used"
            )
        return (
            _new_group_history_cursor(
                route_key=route_key,
                source_fingerprint=source_fingerprint,
                window_start=requested_start,
                window_end=str(state["coverage_start"]),
                window_kind="earlier",
                coverage_start=str(state["coverage_start"]),
                coverage_end=str(state["coverage_end"]),
                earlier_start_used=True,
            ),
            "earlier_window_initialized",
            False,
        )
    return (
        _new_group_history_cursor(
            route_key=route_key,
            source_fingerprint=source_fingerprint,
            window_start=str(state["coverage_end"]),
            window_end=snapshot_end,
            window_kind="forward",
            coverage_start=str(state["coverage_start"]),
            coverage_end=str(state["coverage_end"]),
            earlier_start_used=bool(state["earlier_start_used"]),
        ),
        "forward_window_initialized",
        False,
    )
