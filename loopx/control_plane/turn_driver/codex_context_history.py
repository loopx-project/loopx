"""Bounded IO observations for native-selected Codex replay, never Turn authority.

SessionStart(resume) is emitted after native history reconstruction. Reuse still
assumes the qualified native host replays its selected append-only source. Bind
that source to the current baseline's reader interval, verify every completed
read and reject unknown/lossy replay tails. Earlier unrelated history is not a
dependency of this baseline and its size is not an eligibility condition.
This is not a scan of model inputs or a certificate from a session ID/journal.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
import re
import stat
from typing import Any, BinaryIO

# A compatibility profile of the native rollout, not a shared control vocabulary.
# Unknown record types fall back to full until their replay semantics qualify.
_APPEND_RECORDS = {"response_item", "turn_context", "world_state", "token_usage_record"}
_APPEND_EVENTS = {"task_started", "task_complete", "item_completed", "token_count",
    "thread_settings_applied", "exec_command_begin", "exec_command_end", "agent_message",
    "agent_reasoning", "agent_reasoning_raw_content", "agent_reasoning_section_break",
    "agent_reasoning_raw_content_delta", "agent_reasoning_delta", "agent_message_delta",
    "hook_started", "hook_completed", "warning", "error", "stream_error", "turn_aborted",
    "terminal_interaction", "background_event"}
_IO_CHUNK_BYTES = 64 * 1024
# Bound one decoded protocol record, not the history or the replay interval.
_MAX_RECORD_BYTES = 16 * 1024 * 1024
_SHELL_OUTPUT = re.compile(r"\AChunk ID: [^\r\n]+\nWall time: [0-9.]+ seconds\n"
    r"Process exited with code 0\n(?:Original token count: [0-9]+\n)?Output:\n(.*)\Z", re.S)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


@contextmanager
def _snapshot(event: dict[str, Any]) -> Iterator[tuple[dict[str, Any], BinaryIO]]:
    path = Path(event.get("transcript_path") or "")
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("native replay source must be physical and absolute")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_ino != path.lstat().st_ino
                or info.st_mode & 0o022 or info.st_size <= 0):
            raise ValueError("native replay source must be an owned regular file")
        first_raw = stream.readline(_MAX_RECORD_BYTES + 1)
        if len(first_raw) > _MAX_RECORD_BYTES or not first_raw.endswith(b"\n"):
            raise ValueError("invalid native session metadata")
        first = json.loads(first_raw)
        meta = first.get("payload", {})
        if (first.get("type") != "session_meta" or meta.get("id") != event.get("session_id")
                or meta.get("cwd") != event.get("cwd") or meta.get("forked_from_id")
                or meta.get("history_mode") != "paginated"
                or meta.get("cli_version") not in ("0.153.4", "0.162.1", "0.163.0-alpha.11.1")
                or not event.get("model") or not event.get("permission_mode")):
            raise ValueError("unqualified native replay metadata")
        stream.seek(info.st_size - 1)
        if stream.read(1) != b"\n":
            raise ValueError("native replay observation ends within a record")
        # cli_version describes the original writer, not the current host.
        # Qualify the supported replay records below, including after upgrade.
        facts = {"path": str(path), "device": str(info.st_dev), "inode": str(info.st_ino),
            "metadata_sha256": hashlib.sha256(first_raw).hexdigest(),
            "observed_bytes": info.st_size, "model": event["model"],
            "permission_mode": event["permission_mode"]}
        yield facts, stream
        after = os.fstat(stream.fileno())
        if (after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns
                or path.lstat().st_ino != info.st_ino):
            raise ValueError("native replay source changed during observation")


def _records(stream: BinaryIO, start: int, end: int) -> Iterator[tuple[int, dict[str, Any]]]:
    stream.seek(start)
    while stream.tell() < end:
        offset = stream.tell()
        raw = stream.readline(min(_MAX_RECORD_BYTES + 1, end - offset))
        if len(raw) > _MAX_RECORD_BYTES or not raw.endswith(b"\n"):
            raise ValueError("incomplete or unsupported native replay record")
        yield offset, json.loads(raw)


def _region_digest(stream: BinaryIO, start: int, end: int) -> str:
    digest = hashlib.sha256()
    stream.seek(start)
    remaining = end - start
    while remaining:
        chunk = stream.read(min(_IO_CHUNK_BYTES, remaining))
        if not chunk:
            raise ValueError("native reader interval was truncated")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def _reader_start(stream: BinaryIO, end: int, pages: list[dict[str, Any]]) -> int:
    """Locate this reader at the tail, stopping at its native call boundary.

    A nested code-mode hook may run before the parent output is appended. In
    that case the call record anchors the following output, which must exist
    and pass complete coverage on resume. Never search the unrelated prefix.
    """
    cursor, buffer = end, b""
    while cursor:
        start = max(0, cursor - _IO_CHUNK_BYTES)
        stream.seek(start)
        buffer = stream.read(cursor - start) + buffer
        cursor = start
        while (split := buffer.rstrip(b"\n").rfind(b"\n")) >= 0:
            raw, buffer = buffer[split + 1:], buffer[:split + 1]
            if len(raw) > _MAX_RECORD_BYTES:
                raise ValueError("unsupported native reader record")
            record = json.loads(raw)
            payload = record.get("payload", {})
            offset = cursor + split + 1
            if record.get("type") == "response_item":
                if _read_pages(payload.get("output")) == pages:
                    return offset
                if payload.get("type") in {"function_call", "custom_tool_call"}:
                    return offset
            if record.get("type") == "event_msg" and payload.get("type") == "task_started":
                return end
        if len(buffer) > _MAX_RECORD_BYTES:
            raise ValueError("unsupported native reader record")
    return end


def _read_pages(output: object) -> list[dict[str, Any]]:
    from .codex_context import _context_pages

    # This is the native exec_command result envelope, not a prose classifier.
    if isinstance(output, str) and (match := _SHELL_OUTPUT.fullmatch(output)):
        output = match[1]
    if isinstance(output, list):
        return [page for item in output if isinstance(item, dict) and item.get("type") == "input_text"
                for page in _read_pages(item.get("text"))]
    return _context_pages(output)


def _matches(anchor: dict[str, Any], snapshot: dict[str, Any], stream: BinaryIO,
             event: dict[str, Any]) -> bool:
    from .decision_capture import capture_page_range, read_context_capture_file

    if any(anchor.get(k) != snapshot.get(k) for k in
           ("path", "device", "inode", "metadata_sha256", "model", "permission_mode")):
        return False
    start, end = anchor.get("reader_start"), anchor.get("observed_bytes")
    if (type(start) is not int or type(end) is not int or not 0 < start <= end <= snapshot["observed_bytes"]
            or _region_digest(stream, start, end) != anchor.get("reader_sha256")):
        return False
    reads, reference = anchor.get("reads"), anchor.get("read_source")
    if not isinstance(reads, list) or not reads or not isinstance(reference, dict):
        return False
    required = {read["call_id"]: read["pages_sha256"] for read in reads}
    if len(required) != len(reads):
        return False
    _, body, _ = read_context_capture_file(reference["path"], reference["sha256"], private=True)
    # Verify every observed read, not just EOF. A complete last page must never
    # hide an earlier output that the native replay dropped or truncated.
    seen: set[str] = set()
    expected_pages = Counter(required.values())
    seen_pages: Counter[str] = Counter()
    ranges = []
    for _, record in _records(stream, start, snapshot["observed_bytes"]):
        payload = record.get("payload", {})
        native_output = payload.get("type") in {"function_call_output", "custom_tool_call_output"}
        if record.get("type") == "response_item" and native_output:
            call = payload["call_id"]
            pages = _read_pages(payload.get("output"))
            digest = _digest(pages)
            if call in required:
                if call in seen or digest != required[call]:
                    return False
                seen.add(call)
            elif payload.get("type") != "custom_tool_call_output" or digest not in expected_pages:
                pages = []
            if pages:
                seen_pages[digest] += 1
            for page in pages:
                if page.get("sha256") != reference["sha256"]:
                    continue
                span = capture_page_range(page, raw=body, digest=reference["sha256"])
                if span is None:
                    return False
                ranges.append(span)
        if not _append_only(record, event):
            return False
    cursor = 0
    for start, stop in sorted(ranges):
        if start > cursor:
            return False
        cursor = max(cursor, stop)
    if seen_pages != expected_pages or cursor != len(body):
        return False
    return True


def _append_only(record: dict[str, Any], event: dict[str, Any]) -> bool:
    kind, payload = record.get("type"), record.get("payload", {})
    if kind == "event_msg":
        if payload.get("type") not in _APPEND_EVENTS:
            return False
        if payload.get("type") == "thread_settings_applied" and payload.get("history_mode") not in (None, "paginated"):
            return False
    elif kind not in _APPEND_RECORDS:
        return False
    if kind == "turn_context" and (payload.get("cwd") != event["cwd"] or payload.get("model") != event["model"]):
        return False
    return True


def observe_native_read(state: dict[str, Any], event: dict[str, Any],
                        pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Collect content-free IO facts alongside the existing pending byte ranges."""
    pending = state.get("pending")
    call = event.get("tool_use_id")
    if (not isinstance(pending, dict) or not isinstance(call, str) or not call
            or not pages or any(page.get("ok") is not True for page in pages)):
        return state
    read = {"call_id": call, "pages_sha256": _digest(pages)}
    reads = pending.get("native_reads", [])
    if not isinstance(reads, list):
        reads = []
    if read not in reads:
        reads = [*reads, read]
    pending = {**pending, "native_reads": reads}
    if "native_reader_start" not in pending:
        try:
            with _snapshot(event) as (facts, stream):
                start = _reader_start(stream, facts["observed_bytes"], pages)
            pending["native_reader_start"] = {**facts, "reader_start": start}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            pass
    return {**state, "pending": pending}


def native_resume_verified(state: dict[str, Any], event: dict[str, Any]) -> bool:
    """Observe a qualified replay source; the shared TS owner decides reuse."""
    candidate = state.get("candidate") or {}
    if not isinstance(candidate, dict):
        return False
    anchor = candidate.get("native_resume")
    if (event.get("source") != "resume" or state.get("pending") or not isinstance(anchor, dict)
            or state.get("session_id") != event.get("session_id") or state.get("cwd") != event.get("cwd")):
        return False
    try:
        with _snapshot(event) as (facts, stream):
            verified = _matches(anchor, facts, stream, event)
        return verified
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def checkpoint_native_read(state: dict[str, Any], updated: dict[str, Any],
                           event: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    """Attach content-free native source facts only after the typed complete read.

    A delta checkpoint can advance only a previously verified replay chain.
    Failure affects cross-process reuse, not normal in-process reader delivery.
    """
    candidate = updated.get("candidate")
    if (not isinstance(candidate, dict) or updated.get("pending") or not pages
            or candidate == state.get("candidate") or not event.get("tool_use_id")):
        return
    try:
        pending = state.get("pending") or {}
        reference = pending if candidate.get("ancestry", 0) else state.get("recovery")
        reads = pending.get("native_reads")
        start = pending.get("native_reader_start")
        if not isinstance(reference, dict) or not isinstance(reads, list) or not reads or not isinstance(start, dict):
            return
        with _snapshot(event) as (facts, stream):
            if any(start.get(k) != facts.get(k) for k in ("path", "device", "inode", "metadata_sha256")):
                return
            reader_start = start["reader_start"]
            if candidate.get("ancestry", 0):
                anchor = (state.get("candidate") or {}).get("native_resume")
                if not isinstance(anchor, dict) or not _matches(anchor, facts, stream, event):
                    return
                # A delta still depends on its earlier complete baseline.
                reader_start = min(reader_start, anchor["reader_start"])
            if not 0 < reader_start <= facts["observed_bytes"]:
                return
            checkpoint = {**facts, "reader_start": reader_start,
                "reader_sha256": _region_digest(stream, reader_start, facts["observed_bytes"]),
                "reads": reads, "read_source": {key: reference[key] for key in ("path", "sha256")}}
        candidate["native_resume"] = checkpoint
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return
