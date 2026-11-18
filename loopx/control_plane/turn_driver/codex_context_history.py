"""Bounded IO observations for native-selected Codex replay, never Turn authority.

SessionStart(resume) is emitted after native history reconstruction. Reuse still
assumes the qualified native host replays its selected append-only source. Bind
that source to the current baseline's reader interval, verify every completed
read and reject unknown/lossy replay tails. Earlier unrelated history is not a
dependency of this baseline and its size is not an eligibility condition.
This is not a scan of model inputs or a certificate from a session ID/journal.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
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
_MAX_HEADER_FIELD_BYTES = 16 * 1024
_MAX_PRIMITIVE_BYTES = 1024
_MAX_SKIP_DEPTH = 256
_MAX_SELECTED_REPLAY_READS = 32
_MAX_COMPLETE_REREADS = 32
_SHELL_OUTPUT = re.compile(r"\AChunk ID: [^\r\n]+\nWall time: [0-9.]+ seconds\n"
    r"Process exited with code 0\n(?:Original token count: [0-9]+\n)?Output:\n(.*)\Z", re.DOTALL)
_JSON_WS_STOP = re.compile(rb"[^ \t\r\n]")
_JSON_STR_SPAN = re.compile(rb'(?:[^"\\\x00-\x1f]+|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})){1,64}')
_JSON_TOKEN_STOP = re.compile(rb"[ \t\r\n,\}\]]")
_PREFIX_PAYLOAD_SPEC: dict[str, Any] = {
    "type": None,
    "call_id": None,
    "name": None,
    "turn_id": None,
    "item": {"type": None, "id": None, "exit_code": None},
    "internal_chat_message_metadata_passthrough": {"turn_id": None},
}
_PREFIX_RECORD_SPEC: dict[str, Any] = {"type": None, "payload": _PREFIX_PAYLOAD_SPEC}
_REPLAY_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError)
_SOURCE_IDENTITY_FIELDS = ("path", "device", "inode", "metadata_sha256", "model", "permission_mode")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


@contextmanager
def _snapshot(event: dict[str, Any]) -> Iterator[tuple[dict[str, Any], BinaryIO]]:
    path = Path(event.get("transcript_path") or "")
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("native replay source must be physical and absolute")
    # Inspect the opened descriptor before reading. A FIFO must not wait for a
    # writer while the hook holds its context journal lock.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
                or meta.get("cli_version") not in (
                    "0.153.4", "0.162.1", "0.163.0-alpha.11.1", "0.163.0-alpha.11.2",
                    "0.166.0-alpha.5.1", "0.167.0-alpha.3.2")
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


def _reader_spans(stream: BinaryIO, end: int) -> Iterator[tuple[int, int]]:
    """Walk backwards from the hook one bounded chunk at a time, yielding line spans."""
    if end <= 0:
        return
    stream.seek(end - 1)
    if stream.read(1) != b"\n":
        raise ValueError("incomplete native reader record")
    line_end = end
    cursor = end - 1
    while cursor > 0:
        chunk_start = max(0, cursor - _IO_CHUNK_BYTES)
        stream.seek(chunk_start)
        chunk = stream.read(cursor - chunk_start)
        if len(chunk) != cursor - chunk_start:
            raise ValueError("native reader interval was truncated")
        pos = len(chunk)
        while True:
            split = chunk.rfind(b"\n", 0, pos)
            if split < 0:
                break
            line_start = chunk_start + split + 1
            if line_end > line_start:
                yield line_start, line_end
            line_end = line_start
            pos = split
        cursor = chunk_start
    if line_end > 0:
        yield 0, line_end


def _reject_json_constant(token: str) -> Any:
    raise ValueError(f"unsupported JSON constant: {token}")


class _JsonSliceCursor:
    """Chunked JSON validator and header extractor for oversized Turn-prefix records."""

    def __init__(self, stream: BinaryIO, start: int, stop: int) -> None:
        stream.seek(start)
        self._stream = stream
        self._cursor = start
        self._stop = stop
        self._buf = b""
        self._pos = 0

    def _ensure(self, n: int = 1) -> bool:
        while len(self._buf) - self._pos < n and self._cursor < self._stop:
            chunk = self._stream.read(min(_IO_CHUNK_BYTES, self._stop - self._cursor))
            if not chunk:
                raise ValueError("native reader interval was truncated")
            self._cursor += len(chunk)
            self._buf = self._buf[self._pos:] + chunk
            self._pos = 0
        return len(self._buf) - self._pos >= n

    def skip_ws(self) -> int | None:
        while True:
            if self._pos >= len(self._buf) and not self._ensure(1):
                return None
            match = _JSON_WS_STOP.search(self._buf, self._pos)
            if match is not None:
                self._pos = match.start()
                return self._buf[self._pos]
            self._pos = len(self._buf)

    def consume_byte(self, expected: int) -> None:
        if not self._ensure(1) or self._buf[self._pos] != expected:
            raise ValueError("malformed native JSON record")
        self._pos += 1

    def scan_string(self, *, capture_limit: int | None = None, allow_long_key: bool = False) -> str:
        self.consume_byte(0x22)
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        raw: bytearray | None = bytearray(b'"') if capture_limit is not None else None

        def _append_raw(data: bytes) -> bytearray | None:
            if raw is None or capture_limit is None:
                return None
            if len(raw) + len(data) > capture_limit:
                if not allow_long_key:
                    raise ValueError("oversized JSON header field")
                return None
            raw.extend(data)
            return raw

        while True:
            if self._pos >= len(self._buf) and not self._ensure(1):
                raise ValueError("unterminated JSON string")
            match = _JSON_STR_SPAN.match(self._buf, self._pos)
            if match is not None:
                seg = self._buf[self._pos:match.end()]
                decoder.decode(seg, final=False)
                raw = _append_raw(seg)
                self._pos = match.end()
                continue
            ch = self._buf[self._pos]
            if ch == 0x22:
                decoder.decode(b"", final=True)
                self._pos += 1
                raw = _append_raw(b'"')
                return "" if raw is None else str(json.loads(bytes(raw)))
            if ch == 0x5c and len(self._buf) - self._pos < 6 and self._cursor < self._stop:
                before = len(self._buf) - self._pos
                self._ensure(6)
                if len(self._buf) - self._pos > before:
                    continue
            raise ValueError("invalid JSON string")

    def read_primitive(self) -> Any:
        raw = bytearray()
        while True:
            if self._pos >= len(self._buf) and not self._ensure(1):
                break
            match = _JSON_TOKEN_STOP.search(self._buf, self._pos)
            end = len(self._buf) if match is None else match.start()
            seg = self._buf[self._pos:end]
            if len(raw) + len(seg) > _MAX_PRIMITIVE_BYTES:
                raise ValueError("oversized JSON primitive")
            raw.extend(seg)
            self._pos = end
            if match is not None:
                break
        if not raw:
            raise ValueError("expected JSON value")
        return json.loads(bytes(raw), parse_constant=_reject_json_constant)

    def skip_value(self) -> None:
        stack: list[bool] = []  # True = object, False = array
        state = 0  # 0=val, 1=val|arr_end, 2=arr_sep, 3=key|obj_end, 4=key, 5=colon, 6=obj_sep
        while True:
            ch = self.skip_ws()
            if ch is None:
                raise ValueError("unexpected EOF in JSON value")
            if state in {3, 6}:
                if ch == 0x7d:
                    self._pos += 1
                    stack.pop()
                    if not stack:
                        return
                    state = 6 if stack[-1] else 2
                    continue
                if state == 6:
                    if ch != 0x2c:
                        raise ValueError("expected comma or end of JSON object")
                    self._pos += 1
                    state = 4
                    continue
                state = 4
            if state == 4:
                self.scan_string()
                state = 5
                continue
            if state == 5:
                if ch != 0x3a:
                    raise ValueError("expected colon in JSON object")
                self._pos += 1
                state = 0
                continue
            if state == 2:
                if ch == 0x5d:
                    self._pos += 1
                    stack.pop()
                    if not stack:
                        return
                    state = 6 if stack[-1] else 2
                    continue
                if ch != 0x2c:
                    raise ValueError("expected comma or end of JSON array")
                self._pos += 1
                state = 0
                continue
            if state == 1 and ch == 0x5d:
                self._pos += 1
                stack.pop()
                if not stack:
                    return
                state = 6 if stack[-1] else 2
                continue
            if ch == 0x7b:
                if len(stack) >= _MAX_SKIP_DEPTH:
                    raise RecursionError("JSON nesting too deep")
                self._pos += 1
                stack.append(True)
                state = 3
            elif ch == 0x5b:
                if len(stack) >= _MAX_SKIP_DEPTH:
                    raise RecursionError("JSON nesting too deep")
                self._pos += 1
                stack.append(False)
                state = 1
            elif ch == 0x22:
                self.scan_string()
                if not stack:
                    return
                state = 6 if stack[-1] else 2
            else:
                self.read_primitive()
                if not stack:
                    return
                state = 6 if stack[-1] else 2

    def read_small_value(self) -> Any:
        ch = self.skip_ws()
        if ch is None:
            raise ValueError("unexpected EOF in JSON value")
        if ch == 0x22:
            return self.scan_string(capture_limit=_MAX_HEADER_FIELD_BYTES)
        if ch == 0x7b:
            self.skip_value()
            return {}
        if ch == 0x5b:
            self.skip_value()
            return []
        return self.read_primitive()


def _scan_selected_object(cursor: _JsonSliceCursor, spec: Mapping[str, Any]) -> dict[str, Any]:
    cursor.consume_byte(0x7b)
    result: dict[str, Any] = {}
    ch = cursor.skip_ws()
    if ch == 0x7d:
        cursor._pos += 1
        return result
    while True:
        if ch != 0x22:
            raise ValueError("expected JSON object key")
        key = cursor.scan_string(capture_limit=128, allow_long_key=True)
        if cursor.skip_ws() != 0x3a:
            raise ValueError("expected colon in JSON object")
        cursor._pos += 1
        if key not in spec:
            cursor.skip_value()
        elif isinstance(spec[key], dict):
            if cursor.skip_ws() == 0x7b:
                result[key] = _scan_selected_object(cursor, spec[key])
            else:
                result[key] = cursor.read_small_value()
        else:
            result[key] = cursor.read_small_value()
        ch = cursor.skip_ws()
        if ch == 0x7d:
            cursor._pos += 1
            return result
        if ch != 0x2c:
            raise ValueError("expected comma or end of JSON object")
        cursor._pos += 1
        ch = cursor.skip_ws()


def _scan_turn_prefix_record(stream: BinaryIO, start: int, stop: int) -> dict[str, Any]:
    """Decode only Turn/parent metadata from a large earlier record in O(chunk) memory."""
    cursor = _JsonSliceCursor(stream, start, stop)
    if cursor.skip_ws() != 0x7b:
        raise ValueError("native replay record must be a JSON object")
    record = _scan_selected_object(cursor, _PREFIX_RECORD_SPEC)
    if cursor.skip_ws() is not None:
        raise ValueError("trailing data in native replay record")
    return record


def _turn_boundary(record: dict[str, Any]) -> bool:
    return record.get("type") == "event_msg" and record.get("payload", {}).get("type") == "task_started"


def _native_reader(stream: BinaryIO, end: int, event: dict[str, Any]) -> tuple[int, str] | None:
    """Bind a hook to its own call, or to a single open native Code Mode call."""
    reader = event["tool_use_id"]
    completed = False
    closed_parents: set[str] = set()
    parent: tuple[int, str] | None = None
    for offset, stop in _reader_spans(stream, end):
        raw_len = stop - offset - 1
        if parent is None:
            if raw_len > _MAX_RECORD_BYTES:
                raise ValueError("unsupported native reader record")
            stream.seek(offset)
            record = json.loads(stream.read(stop - offset))
        elif raw_len > _IO_CHUNK_BYTES:
            record = _scan_turn_prefix_record(stream, offset, stop - 1)
        else:
            stream.seek(offset)
            record = json.loads(stream.read(stop - offset))
        if _turn_boundary(record):
            break
        payload = record["payload"]
        kind = payload.get("type")
        if record.get("type") == "event_msg" and kind == "item_completed":
            item = payload.get("item", {})
            if item.get("type") == "CommandExecution" and item.get("id") == reader:
                if item.get("exit_code") != 0 or payload.get("turn_id") != event.get("turn_id"):
                    return None
                completed = True
        if record.get("type") != "response_item":
            continue
        call = payload.get("call_id")
        if not isinstance(call, str) or not call:
            continue
        if call == reader and kind in {"function_call", "function_call_output"}:
            if raw_len > _MAX_RECORD_BYTES:
                return None
            if parent is not None and raw_len > _IO_CHUNK_BYTES:
                stream.seek(offset)
                record = json.loads(stream.read(stop - offset))
            return offset, _digest(record)
        if kind == "custom_tool_call_output":
            closed_parents.add(call)
        if kind != "custom_tool_call" or payload.get("name") != "exec" or call in closed_parents:
            continue
        if not completed or parent is not None or not isinstance(event.get("turn_id"), str):
            return None
        # A qualified native record can omit message metadata. The completed
        # command still carries the Turn, and the parent is uniquely open.
        metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
        if metadata.get("turn_id") not in (None, event["turn_id"]):
            return None
        parent = offset, _digest(record)
    return parent


def _read_pages(output: object) -> list[dict[str, Any]]:
    from .codex_context import _context_pages

    # This is the native exec_command result envelope, not a prose classifier.
    if isinstance(output, str) and (match := _SHELL_OUTPUT.fullmatch(output)):
        output = match[1]
    if isinstance(output, list):
        return [page for item in output if isinstance(item, dict) and item.get("type") == "input_text"
                for page in _read_pages(item.get("text"))]
    return _context_pages(output)


def _covers_bytes(ranges: list[tuple[int, int]], total: int) -> bool:
    cursor = 0
    for start, stop in sorted(ranges):
        if start > cursor:
            return False
        cursor = max(cursor, stop)
    return cursor == total


def _same_replay_source(anchor: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    return all(anchor.get(k) == snapshot.get(k) for k in _SOURCE_IDENTITY_FIELDS)


def _matching_replay_interval(anchor: dict[str, Any], snapshot: dict[str, Any], stream: BinaryIO) -> bool:
    start, end = anchor.get("reader_start"), anchor.get("observed_bytes")
    return (_same_replay_source(anchor, snapshot) and type(start) is int and type(end) is int
        and 0 < start <= end <= snapshot["observed_bytes"]
        and _region_digest(stream, start, end) == anchor.get("reader_sha256"))


def _read_receipt(offset: int, digest: str, pages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"native_offset": offset, "native_sha256": digest, "pages_sha256": _digest(pages)}


def _matching_resume_session(state: dict[str, Any], event: dict[str, Any], anchor: object) -> bool:
    return (event.get("source") == "resume" and isinstance(anchor, dict)
        and state.get("session_id") == event.get("session_id")
        and state.get("cwd") == event.get("cwd"))


def _verified_replay_capture(anchor: dict[str, Any], snapshot: dict[str, Any], stream: BinaryIO) -> tuple[Path, bytes, dict[str, Any]] | None:
    from .decision_capture import read_context_capture_file

    reference = anchor.get("read_source")
    if not isinstance(reference, dict) or not _matching_replay_interval(anchor, snapshot, stream):
        return None
    return read_context_capture_file(reference["path"], reference["sha256"], private=True)


def _emission_pages(payload: dict[str, Any]) -> Iterator[list[dict[str, Any]]]:
    output = payload.get("output")
    if payload["type"] == "custom_tool_call_output" and isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and item.get("type") == "input_text":
                yield _read_pages(item.get("text"))
    else:
        yield _read_pages(output)


def _replayed_output_groups(anchor: dict[str, Any], snapshot: dict[str, Any], stream: BinaryIO,
                            event: dict[str, Any], reads: list[dict[str, Any]],
                            ) -> Iterator[tuple[int, str, list[dict[str, Any]]]]:
    # Old private checkpoints did not bind full reads to a call. They may
    # still serve their saved file, but cannot attest cross-process retention.
    call_offsets: dict[int, str] = {}
    for read in reads:
        offset, digest = read["native_offset"], read["native_sha256"]
        if call_offsets.setdefault(offset, digest) != digest:
            raise ValueError("native replay call changed between observations")
    native_calls: dict[str, tuple[int, str]] = {}
    closed_calls: set[str] = set()
    for offset, record in _records(stream, anchor["reader_start"], snapshot["observed_bytes"]):
        if not _append_only(record, event):
            raise ValueError("unsupported native replay continuation")
        payload = record["payload"]
        if _turn_boundary(record):
            native_calls.clear()
            closed_calls.clear()
        if record["type"] != "response_item":
            continue
        kind, call = payload.get("type"), payload.get("call_id")
        if kind in {"function_call", "custom_tool_call"}:
            # Call IDs can repeat in later Turns. Match the exact call
            # occurrence observed by the hook.
            native_calls.pop(call, None)
            closed_calls.discard(call)
            if offset in call_offsets and call_offsets[offset] == _digest(record):
                native_calls[call] = offset, kind
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            opened = native_calls.pop(call, None)
            if opened is None:
                if call in closed_calls:
                    raise ValueError("duplicate native tool output without a new call")
                if (kind != "function_call_output" or offset not in call_offsets
                        or call_offsets[offset] != _digest(record)):
                    continue
                call_offset = offset  # the direct output was already present at hook time
            else:
                call_offset, call_kind = opened
                if kind != call_kind + "_output" or call_offset not in call_offsets:
                    continue
            closed_calls.add(call)
            for group in _emission_pages(payload):
                if group:
                    yield call_offset, _digest(group), group


def _matches(anchor: dict[str, Any], snapshot: dict[str, Any], stream: BinaryIO,
             event: dict[str, Any]) -> bool:
    from .decision_capture import capture_page_range

    reads, rereads = anchor.get("reads"), anchor.get("rereads", [])
    if not isinstance(reads, list) or not reads or not isinstance(rereads, list):
        return False
    verified = _verified_replay_capture(anchor, snapshot, stream)
    if verified is None:
        return False
    _, body, _ = verified
    reference_sha = anchor["read_source"]["sha256"]
    all_reads = [*reads, *rereads]
    required = {(read["native_offset"], read["pages_sha256"]) for read in all_reads}
    ranges = []
    for offset, digest, group in _replayed_output_groups(anchor, snapshot, stream, event, all_reads):
        if (offset, digest) not in required:
            continue
        for page in group:
            span = capture_page_range(page, raw=body, digest=reference_sha)
            if span is None:
                return False
            ranges.append(span)
    # An independently observed reread can recover a missing range. A copy
    # emitted by an unrelated call cannot contribute even identical bytes.
    return _covers_bytes(ranges, len(body))


def _replayed_selection_sections(anchor: dict[str, Any], snapshot: dict[str, Any], stream: BinaryIO,
                                 event: dict[str, Any]) -> dict[str, str]:
    selections = anchor.get("selections")
    if not isinstance(selections, dict) or not selections or _verified_replay_capture(anchor, snapshot, stream) is None:
        return {}
    reads = [read for selection in selections.values() for read in selection["reads"]]
    outputs = {(offset, digest) for offset, digest, _ in _replayed_output_groups(anchor, snapshot, stream, event, reads)}
    retained = {}
    for selection in selections.values():
        ranges = [(read["start"], read["end"]) for read in selection["reads"]
                  if (read["native_offset"], read["pages_sha256"]) in outputs]
        if _covers_bytes(ranges, selection["total_bytes"]):
            retained.update(selection["sections"])
    return retained


def _append_only(record: dict[str, Any], event: dict[str, Any]) -> bool:
    kind, payload = record.get("type"), record.get("payload", {})
    if kind == "event_msg":
        if payload.get("type") not in _APPEND_EVENTS:
            return False
        if payload.get("type") == "thread_settings_applied" and payload.get("history_mode") not in (None, "paginated"):
            return False
    elif kind not in _APPEND_RECORDS:
        return False
    return kind != "turn_context" or (
        payload.get("cwd") == event["cwd"] and payload.get("model") == event["model"])


def observe_native_read(state: dict[str, Any], event: dict[str, Any],
                        pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Collect content-free IO facts alongside the existing pending byte ranges."""
    pending = state.get("pending")
    call = event.get("tool_use_id")
    if (not isinstance(call, str) or not call
            or not pages or any(page.get("ok") is not True for page in pages)):
        return state
    if pending is None:
        recovery, candidate = state.get("recovery"), state.get("candidate") or {}
        if (not isinstance(recovery, dict) or not isinstance(candidate, dict)
                or any(page.get("sha256") != recovery.get("sha256") for page in pages)
                or (candidate.get("capture_sha256") == recovery.get("sha256")
                    and candidate.get("capture_bytes_verified") is True)):
            return state
        # Invalidation clears pending reads. Record the first original-capture
        # recovery read too, including a full capture delivered in one page.
        # These are IO annotations only: the typed lifecycle still authenticates
        # coverage and decides whether a complete baseline can be established.
        pending = {}
    if not isinstance(pending, dict):
        return state
    try:
        with _snapshot(event) as (facts, stream):
            native_reader = _native_reader(stream, facts["observed_bytes"], event)
    except _REPLAY_ERRORS:
        return state
    if native_reader is None:
        return state
    offset, digest = native_reader
    read = _read_receipt(offset, digest, pages)
    start, reads = pending.get("native_reader_start"), pending.get("native_reads")
    if not isinstance(start, dict) or not isinstance(reads, list) or not _same_replay_source(start, facts):
        start, reads = {**facts, "reader_start": offset}, []
    elif offset < start["reader_start"]:
        start = {**facts, "reader_start": offset}
    if read not in reads:
        reads = [*reads, read]
    pending = {**pending, "native_reads": reads, "native_reader_start": start}
    return {**state, "pending": pending}


def observe_completed_native_reread(state: dict[str, Any], updated: dict[str, Any],
                                   event: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    """Add independently observed output to the current full-capture checkpoint."""
    candidate = updated.get("candidate")
    if not isinstance(candidate, dict) or candidate != state.get("candidate") or not pages:
        return
    anchor, reference = candidate.get("native_resume"), state.get("recovery")
    if (not isinstance(anchor, dict) or not isinstance(reference, dict)
            or anchor.get("read_source", {}).get("sha256") != reference.get("sha256")
            or any(page.get("ok") is not True or page.get("sha256") != reference.get("sha256") for page in pages)):
        return
    try:
        with _snapshot(event) as (facts, stream):
            # Resume checks the original interval digest. Extra observations
            # cannot change it and do not need to hash it again at each hook.
            if not _same_replay_source(anchor, facts) or anchor["observed_bytes"] > facts["observed_bytes"]:
                return
            native_reader = _native_reader(stream, facts["observed_bytes"], event)
            if native_reader is None or native_reader[0] < anchor["reader_start"]:
                return
            read = _read_receipt(*native_reader, pages)
        rereads = anchor.get("rereads", [])
        if read not in anchor["reads"] and read not in rereads:
            candidate["native_resume"] = {**anchor, "rereads": [*rereads, read][-_MAX_COMPLETE_REREADS:]}
    except _REPLAY_ERRORS:
        return


def native_resume_verified(state: dict[str, Any], event: dict[str, Any]) -> bool:
    """Observe a qualified replay source; the shared TS owner decides reuse."""
    candidate = state.get("candidate") or {}
    if not isinstance(candidate, dict):
        return False
    anchor = candidate.get("native_resume")
    if not _matching_resume_session(state, event, anchor) or state.get("pending") or not isinstance(anchor, dict):
        return False
    try:
        with _snapshot(event) as (facts, stream):
            verified = _matches(anchor, facts, stream, event)
        return verified
    except _REPLAY_ERRORS:
        return False


def native_resume_sections(state: dict[str, Any], event: dict[str, Any]) -> dict[str, str]:
    """Verify replay of exact selected outputs without claiming a whole capture read."""
    anchor = state.get("selected_resume")
    if (not _matching_resume_session(state, event, anchor) or not isinstance(anchor, dict)
            or anchor.get("scope") != state.get("read_scope")
            or anchor.get("epoch") != state.get("context_epoch")
            or anchor.get("read_source") != state.get("source_recovery")):
        return {}
    try:
        with _snapshot(event) as (facts, stream):
            retained = _replayed_selection_sections(anchor, facts, stream, event)
        return retained
    except _REPLAY_ERRORS:
        return {}


def checkpoint_native_selection(state: dict[str, Any], updated: dict[str, Any],
                                event: dict[str, Any], raw_pages: list[dict[str, Any]],
                                verified_pages: list[dict[str, Any]]) -> None:
    """Record bounded, self-contained selections after literal byte verification."""
    if len(raw_pages) != 1 or len(verified_pages) != 1:
        return
    raw, page = raw_pages[0], verified_pages[0]
    sections, source = page.get("read_sections"), state.get("source_recovery")
    scope, epoch, call = state.get("read_scope"), state.get("context_epoch"), event.get("tool_use_id")
    consumed = updated.get("consumed_pointers", {})
    if (not (raw.get("schema_version") == "context_capture_selection_page_v1"
            or "selected_pointers" in raw and "selection_sha256" in raw)
            or page.get("ok") is not True or page.get("read_sections_inherited")
            or not isinstance(sections, dict)
            or not isinstance(source, dict) or page.get("source_sha256") != source.get("sha256")
            or not isinstance(scope, dict) or not epoch or scope.get("context_epoch") != epoch
            or not isinstance(call, str) or not call or not isinstance(consumed, dict)):
        return
    previous = state.get("selected_resume")
    same_source = (isinstance(previous, dict) and previous.get("scope") == scope
        and previous.get("epoch") == epoch and previous.get("read_source") == source
        and previous.get("path") == event.get("transcript_path")
        and all(previous.get(k) == event.get(k) for k in ("model", "permission_mode")))
    try:
        with _snapshot(event) as (facts, stream):
            native_reader = _native_reader(stream, facts["observed_bytes"], event)
            if native_reader is None:
                return
            native_offset, native_sha256 = native_reader
            anchor = previous if isinstance(previous, dict) and same_source and _same_replay_source(previous, facts) else {}
            selections = dict(anchor.get("selections", {}))
            all_reads = [read for entry in selections.values() for read in entry["reads"]]
            if any(read["call_id"] == call and read["native_offset"] == native_offset for read in all_reads):
                return
            if not selections or len(all_reads) >= _MAX_SELECTED_REPLAY_READS:
                end = facts["observed_bytes"]
                anchor = {**facts, "reader_start": native_offset,
                    "reader_sha256": _region_digest(stream, native_offset, end),
                    "read_source": source, "scope": scope, "epoch": epoch}
                selections = {}
            if native_offset < anchor["reader_start"]:
                return
        # Offsets belong to the exact selected view, which can coincide across
        # different sources or presentation bindings. Only combine pages from
        # the same physical reader and its verified view.
        selection = _digest([raw.get("reference_file") or raw["sha256"], page["sha256"]])
        offset = page["offset"]
        end = offset + len(page["content"].encode("utf-8"))
        receipt = {"call_id": call, **_read_receipt(native_offset, native_sha256, raw_pages),
                   "start": offset, "end": end}
        entry = selections.get(selection, {})
        observed = entry.get("sections", {})
        progress = [*entry.get("reads", []), receipt]
        if _covers_bytes([(read["start"], read["end"]) for read in progress], page["total_bytes"]):
            # The reducer and this replay interval both observed full coverage.
            # Never resume an inherited selection from only its displayed pages.
            if sections and all(consumed.get(pointer) == digest for pointer, digest in sections.items()):
                observed = sections
        selections[selection] = {"reads": progress, "sections": observed, "total_bytes": page["total_bytes"]}
        updated["selected_resume"] = {**anchor, "selections": selections}
    except _REPLAY_ERRORS:
        return


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
    except _REPLAY_ERRORS:
        return
