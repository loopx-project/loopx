"""Bounded, owner-local views of the Codex rollout, never an execution source.

Native record names stay provider-local. Only visible conversation channels,
tool input/output and explicit lifecycle events are projected; internal model
reasoning and configuration are not conversation content.
"""

from __future__ import annotations

import hashlib
import json
import mmap
import os
from pathlib import Path
from typing import Any

from ...public_safe_text import CATEGORY_CREDENTIAL, classify_private_text

WINDOW_BYTES = 2 * 1024 * 1024
RECORD_BYTES = 8 * 1024 * 1024
CONTENT_CHARS = 24000
PAGE_ROWS = 100


def history_revision(stamp: os.stat_result) -> str:
    return f"{stamp.st_dev}:{stamp.st_ino}:{stamp.st_size}:{stamp.st_mtime_ns}"


def source_ref(stamp: os.stat_result) -> str:
    return hashlib.sha256(f"{stamp.st_dev}:{stamp.st_ino}".encode()).hexdigest()


def _display_record(raw: bytes) -> dict[str, Any] | None:
    record = json.loads(raw)
    item = record.get("payload", {})
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    text = ""
    role = "activity"
    label = kind
    tool_exit_codes: list[int] = []
    if record.get("type") == "response_item":
        if kind == "message":
            message_role = item.get("role")
            if message_role not in {"user", "assistant"} or item.get("channel") not in {
                None,
                "commentary",
                "final",
            }:
                return None
            role = str(message_role)
            text = "\n".join(
                part["text"]
                for part in item.get("content", [])
                if isinstance(part, dict)
                and part.get("type") in {"input_text", "output_text"}
                and isinstance(part.get("text"), str)
            )
            # Attachments are not silently represented as a complete text message.
            if any(
                isinstance(part, dict)
                and part.get("type")
                not in {
                    "input_text",
                    "output_text",
                }
                for part in item.get("content", [])
            ):
                text += "\n[Non-text content is not available in this view.]"
        elif kind in {"function_call", "custom_tool_call"}:
            role = "tool"
            label = item.get("name", kind)
            text = item.get("arguments" if kind == "function_call" else "input", "")
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            role = "tool"
            text = item.get("output", "")
            # Codex custom tools persist native input/output text blocks as well
            # as MCP text blocks. Decode only those exact shapes before paging.
            # Preserve unfamiliar blocks/metadata as JSON rather than drop facts.
            parts = [text] if isinstance(text, str) else []
            if (
                isinstance(text, list)
                and text
                and all(
                    isinstance(part, dict)
                    and set(part) == {"type", "text"}
                    and part["type"] in {"text", "input_text", "output_text"}
                    and isinstance(part["text"], str)
                    for part in text
                )
            ):
                parts = [part["text"] for part in text]
                text = "\n".join(parts)
            # Read only the saved shell envelope's explicit observation. A
            # nonzero code is not a failed task, and prose/input is not evidence.
            for part in parts:
                try:
                    shell = json.loads(part)
                except ValueError:
                    continue
                if (
                    isinstance(shell, dict)
                    and isinstance(shell.get("output"), str)
                    and type(shell.get("wall_time_seconds")) in {int, float}
                    and type(shell.get("exit_code")) is int
                ):
                    tool_exit_codes.append(shell["exit_code"])
        elif kind == "reasoning":
            return None  # Never expose reasoning/encrypted_content.
        else:
            return {
                "role": "activity",
                "kind": "unavailable",
                "label": "unavailable",
                "text": "",
                "unavailable": "unsupported_record_type",
            }
    elif record.get("type") == "event_msg":
        if kind not in {"task_started", "task_complete", "turn_aborted", "error"}:
            return None  # Message mirrors and token counters are not new activity.
        text = item.get("message", "") if kind == "error" else ""
    else:
        return None
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False, indent=2)
    call_id = str(item.get("call_id", ""))[:160]
    redacted = (
        classify_private_text(
            text + "\n" + str(label) + "\n" + call_id,
            categories=frozenset({CATEGORY_CREDENTIAL}),
            include_compound_field_assignment=True,
        )
        is not None
    )
    # The detector identifies shapes, not byte-complete values. Withhold the
    # whole field instead of accidentally leaking a suffix through partial masking.
    if redacted:
        text = "[Content withheld because it contains credential-shaped data.]"
        label = kind
        call_id = ""
    return {
        "role": role,
        "kind": kind,
        "label": str(label)[:160],
        "text": text,
        "redacted": redacted,
        "timestamp": record.get("timestamp"),
        "call_id": call_id,
        **(
            {"tool_exit_codes": tool_exit_codes}
            if tool_exit_codes and not redacted
            else {}
        ),
    }


def _entry(data: mmap.mmap, start: int, end: int) -> dict[str, Any] | None:
    if end - start > RECORD_BYTES:
        # Preserve the position and disclosure, rather than silently dropping a
        # record that exceeds the host frame bound. No raw bytes leave this path.
        return {
            "id": str(start),
            "end": end,
            "role": "activity",
            "kind": "unavailable",
            "label": "unavailable",
            "text": "",
            "unavailable": "record_exceeds_read_limit",
            "truncated": False,
        }
    raw = data[start:end]
    try:
        value = _display_record(raw)
    except (ValueError, TypeError, AttributeError):
        return {
            "id": str(start),
            "end": end,
            "role": "activity",
            "kind": "unavailable",
            "label": "unavailable",
            "text": "",
            "unavailable": "record_unreadable",
            "truncated": False,
        }
    if value is None:
        return None
    return {
        **value,
        "id": str(start),
        "end": end,
        "content_ref": hashlib.sha256(raw).hexdigest(),
        "text": value["text"][:CONTENT_CHARS],
        "truncated": len(value["text"]) > CONTENT_CHARS,
    }


def read_messages(
    path: Path,
    *,
    before: int | None = None,
    after: int | None = None,
    expected_source: str | None = None,
) -> dict[str, Any]:
    if before is not None and after is not None:
        raise ValueError("Choose one history direction")
    if any(position is not None and position < 0 for position in (before, after)):
        raise ValueError("History position must be nonnegative")
    with path.open("rb") as stream:
        stamp = os.fstat(stream.fileno())
        size = stamp.st_size
        source = source_ref(stamp)
        reset = expected_source is not None and expected_source != source
        reset = reset or (after is not None and after > size)
        if reset:
            before = after = None
        rows: list[dict[str, Any]] = []
        lower = upper = 0
        more = False
        if size:
            with mmap.mmap(stream.fileno(), size, access=mmap.ACCESS_READ) as data:
                # An incomplete append is retried, including a record longer than
                # the normal page window. Cursors only point to complete lines.
                complete = data.rfind(b"\n") + 1
                if after is not None:
                    if after > complete or (after and data[after - 1 : after] != b"\n"):
                        raise ValueError("History cursor is not a record boundary")
                    lower = upper = after
                    while upper < complete and len(rows) < PAGE_ROWS:
                        end = data.find(b"\n", upper, complete) + 1
                        row = _entry(data, upper, end)
                        upper = end
                        if row is not None:
                            rows.append(row)
                        if upper - lower >= WINDOW_BYTES:
                            break
                    more = upper < complete
                else:
                    upper = min(before, complete) if before is not None else complete
                    if upper and data[upper - 1 : upper] != b"\n":
                        raise ValueError("History cursor is not a record boundary")
                    lower = upper
                    while lower > 0 and len(rows) < PAGE_ROWS:
                        start = data.rfind(b"\n", 0, lower - 1) + 1
                        row = _entry(data, start, lower)
                        lower = start
                        if row is not None:
                            rows.append(row)
                        if upper - lower >= WINDOW_BYTES:
                            break
                    rows.reverse()
                    more = upper < complete
    return {
        "messages": rows,
        "before": lower or None,
        "after": upper,
        "has_more": more,
        "reset": reset,
        "revision": history_revision(stamp),
        "source_ref": source,
        "history_size": size,
        "bounded": bool(lower or more),
    }


def read_content(
    path: Path,
    *,
    entry_id: int,
    offset: int,
    expected_source: str,
    content_ref: str,
) -> dict[str, Any]:
    if entry_id < 0 or offset < 0:
        raise ValueError("Content position must be nonnegative")
    with path.open("rb") as stream:
        stamp = os.fstat(stream.fileno())
        if source_ref(stamp) != expected_source or entry_id >= stamp.st_size:
            raise ValueError("History source changed")
        if entry_id:
            stream.seek(entry_id - 1)
            if stream.read(1) != b"\n":
                raise ValueError("Content cursor is not a record boundary")
        stream.seek(entry_id)
        raw = stream.readline(RECORD_BYTES + 1)
        if (
            len(raw) > RECORD_BYTES
            or not raw.endswith(b"\n")
            or hashlib.sha256(raw).hexdigest() != content_ref
        ):
            raise ValueError("Content changed or is unavailable")
        value = _display_record(raw)
        if value is None or value.get("unavailable") or offset > len(value["text"]):
            raise ValueError("Content is unavailable")
        end = min(offset + CONTENT_CHARS, len(value["text"]))
        return {
            "text": value["text"][offset:end],
            "next_offset": end,
            "has_more": end < len(value["text"]),
            "redacted": value["redacted"],
        }
