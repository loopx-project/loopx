"""Replay verification and interval digestion helpers for native Codex history."""
from __future__ import annotations

from collections.abc import Hashable, Iterator, Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
from typing import BinaryIO, cast

from .codex_context_history_json import (
    _IO_CHUNK_BYTES,
    _MAX_RECORD_BYTES,
    _digest,
    _scan_turn_prefix_record,
)

# A compatibility profile of the native rollout, not a shared control vocabulary.
# Unknown record types fall back to full until their replay semantics qualify.
_APPEND_RECORDS = {"response_item", "turn_context", "world_state", "token_usage_record"}
_APPEND_EVENTS = {
    "task_started",
    "task_complete",
    "item_completed",
    "token_count",
    "thread_settings_applied",
    "exec_command_begin",
    "exec_command_end",
    "agent_message",
    "agent_reasoning",
    "agent_reasoning_raw_content",
    "agent_reasoning_section_break",
    "agent_reasoning_raw_content_delta",
    "agent_reasoning_delta",
    "agent_message_delta",
    "hook_started",
    "hook_completed",
    "warning",
    "error",
    "stream_error",
    "turn_aborted",
    "terminal_interaction",
    "background_event",
}
_MAX_SELECTED_REPLAY_READS = 32
_MAX_COMPLETE_REREADS = 32
_SHELL_OUTPUT = re.compile(
    r"\AChunk ID: [^\r\n]+\nWall time: [0-9.]+ seconds\n"
    r"Process exited with code 0\n(?:Original token count: [0-9]+\n)?Output:\n(.*)\Z",
    re.DOTALL,
)
_REPLAY_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError)
_SOURCE_IDENTITY_FIELDS = (
    "path",
    "device",
    "inode",
    "metadata_sha256",
    "model",
    "permission_mode",
)


def _mapping_field(record: Mapping[str, object], key: str) -> Mapping[str, object]:
    """Match ``dict.get(key, {}).get(...)`` semantics cleanly for typed mappings."""
    if key not in record:
        return {}
    value = record[key]
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    raise AttributeError(f"{key} must be a mapping")


def _records(stream: BinaryIO, start: int, end: int) -> Iterator[tuple[int, dict[str, object]]]:
    stream.seek(start)
    while stream.tell() < end:
        offset = stream.tell()
        raw = stream.readline(min(_MAX_RECORD_BYTES + 1, end - offset))
        if len(raw) > _MAX_RECORD_BYTES or not raw.endswith(b"\n"):
            raise ValueError("incomplete or unsupported native replay record")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise TypeError("native replay record must be a JSON object")
        yield offset, cast(dict[str, object], parsed)


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


def _turn_boundary(record: Mapping[str, object]) -> bool:
    return (
        record.get("type") == "event_msg"
        and _mapping_field(record, "payload").get("type") == "task_started"
    )


def _read_pages(output: object) -> list[dict[str, object]]:
    from .codex_context import _context_pages

    # This is the native exec_command result envelope, not a prose classifier.
    if isinstance(output, str) and (match := _SHELL_OUTPUT.fullmatch(output)):
        output = match[1]
    if isinstance(output, list):
        return [
            page
            for item in output
            if isinstance(item, dict) and item.get("type") == "input_text"
            for page in _read_pages(item.get("text"))
        ]
    return cast(list[dict[str, object]], _context_pages(output))


def _covers_bytes(ranges: list[tuple[int, int]], total: int) -> bool:
    cursor = 0
    for start, stop in sorted(ranges):
        if start > cursor:
            return False
        cursor = max(cursor, stop)
    return cursor == total


def _same_replay_source(
    anchor: Mapping[str, object], snapshot: Mapping[str, object]
) -> bool:
    return all(anchor.get(k) == snapshot.get(k) for k in _SOURCE_IDENTITY_FIELDS)


def _matching_replay_interval(
    anchor: Mapping[str, object], snapshot: Mapping[str, object], stream: BinaryIO
) -> bool:
    start, end = anchor.get("reader_start"), anchor.get("observed_bytes")
    observed = snapshot["observed_bytes"]
    return (
        _same_replay_source(anchor, snapshot)
        and type(start) is int
        and type(end) is int
        and isinstance(observed, int)
        and 0 < start <= end <= observed
        and _region_digest(stream, start, end) == anchor.get("reader_sha256")
    )


def _read_receipt(
    offset: int, digest: str, pages: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    return {
        "native_offset": offset,
        "native_sha256": digest,
        "pages_sha256": _digest([dict(page) for page in pages]),
    }


def _matching_resume_session(
    state: Mapping[str, object], event: Mapping[str, object], anchor: object
) -> bool:
    return (
        event.get("source") == "resume"
        and isinstance(anchor, dict)
        and state.get("session_id") == event.get("session_id")
        and state.get("cwd") == event.get("cwd")
    )


def _verified_replay_capture(
    anchor: Mapping[str, object], snapshot: Mapping[str, object], stream: BinaryIO
) -> tuple[Path, bytes, dict[str, object]] | None:
    from .decision_capture import read_context_capture_file

    reference = anchor.get("read_source")
    if not isinstance(reference, Mapping) or not _matching_replay_interval(
        anchor, snapshot, stream
    ):
        return None
    ref_path = reference["path"]
    ref_sha = reference["sha256"]
    if not isinstance(ref_path, str) or not isinstance(ref_sha, str):
        raise TypeError("read_source path and sha256 must be strings")
    path, body, payload = read_context_capture_file(ref_path, ref_sha, private=True)
    return path, body, cast(dict[str, object], payload)


def _emission_pages(payload: Mapping[str, object]) -> Iterator[list[dict[str, object]]]:
    output = payload.get("output")
    if payload["type"] == "custom_tool_call_output" and isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and item.get("type") == "input_text":
                yield _read_pages(item.get("text"))
    else:
        yield _read_pages(output)


def _append_only(record: Mapping[str, object], event: Mapping[str, object]) -> bool:
    kind = record.get("type")
    payload = _mapping_field(record, "payload")
    if kind == "event_msg":
        if payload.get("type") not in _APPEND_EVENTS:
            return False
        if payload.get("type") == "thread_settings_applied" and payload.get(
            "history_mode"
        ) not in (None, "paginated"):
            return False
    elif kind not in _APPEND_RECORDS:
        return False
    return kind != "turn_context" or (
        payload.get("cwd") == event["cwd"] and payload.get("model") == event["model"]
    )


def _replayed_output_groups(
    anchor: Mapping[str, object],
    snapshot: Mapping[str, object],
    stream: BinaryIO,
    event: Mapping[str, object],
    reads: Sequence[Mapping[str, object]],
) -> Iterator[tuple[int, str, list[dict[str, object]]]]:
    # Old private checkpoints did not bind full reads to a call. They may
    # still serve their saved file, but cannot attest cross-process retention.
    call_offsets: dict[int, str] = {}
    for read in reads:
        offset = read["native_offset"]
        digest = read["native_sha256"]
        if not isinstance(offset, int) or not isinstance(digest, str):
            raise TypeError("native read receipt must carry int offset and str digest")
        if call_offsets.setdefault(offset, digest) != digest:
            raise ValueError("native replay call changed between observations")
    reader_start = anchor["reader_start"]
    observed_bytes = snapshot["observed_bytes"]
    if not isinstance(reader_start, int) or not isinstance(observed_bytes, int):
        raise TypeError("replay bounds must be integers")
    native_calls: dict[Hashable, tuple[int, str]] = {}
    closed_calls: set[Hashable] = set()
    for offset, record in _records(stream, reader_start, observed_bytes):
        if not _append_only(record, event):
            raise ValueError("unsupported native replay continuation")
        raw_payload = record["payload"]
        if not isinstance(raw_payload, Mapping):
            raise TypeError("native replay payload must be a mapping")
        payload = cast(Mapping[str, object], raw_payload)
        if _turn_boundary(record):
            native_calls.clear()
            closed_calls.clear()
        if record["type"] != "response_item":
            continue
        kind, call = payload.get("type"), payload.get("call_id")
        if kind in {"function_call", "custom_tool_call"}:
            if not isinstance(call, Hashable):
                raise TypeError("native tool call_id must be hashable")
            # Call IDs can repeat in later Turns. Match the exact call
            # occurrence observed by the hook.
            native_calls.pop(call, None)
            closed_calls.discard(call)
            if offset in call_offsets and call_offsets[offset] == _digest(record):
                native_calls[call] = offset, str(kind)
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            if not isinstance(call, Hashable):
                raise TypeError("native tool call_id must be hashable")
            opened = native_calls.pop(call, None)
            if opened is None:
                if call in closed_calls:
                    raise ValueError("duplicate native tool output without a new call")
                if (
                    kind != "function_call_output"
                    or offset not in call_offsets
                    or call_offsets[offset] != _digest(record)
                ):
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


def _matches(
    anchor: Mapping[str, object],
    snapshot: Mapping[str, object],
    stream: BinaryIO,
    event: Mapping[str, object],
) -> bool:
    from .decision_capture import capture_page_range

    reads, rereads = anchor.get("reads"), anchor.get("rereads", [])
    if not isinstance(reads, list) or not reads or not isinstance(rereads, list):
        return False
    verified = _verified_replay_capture(anchor, snapshot, stream)
    if verified is None:
        return False
    _, body, _ = verified
    read_source = anchor["read_source"]
    if not isinstance(read_source, Mapping):
        raise TypeError("read_source must be a mapping")
    reference_sha = str(read_source["sha256"])
    all_reads = cast(list[Mapping[str, object]], [*reads, *rereads])
    required = {(read["native_offset"], read["pages_sha256"]) for read in all_reads}
    ranges: list[tuple[int, int]] = []
    for offset, digest, group in _replayed_output_groups(
        anchor, snapshot, stream, event, all_reads
    ):
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


def _int_value(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int")
    return value


def _reader_slices(
    stream: BinaryIO, end: int
) -> Iterator[tuple[int, int, bytes | None]]:
    """Walk backwards from ``end`` one chunk at a time, sharing in-chunk line slices."""
    if end <= 0:
        return
    stream.seek(end - 1)
    if stream.read(1) != b"\n":
        raise ValueError("incomplete native reader record")
    line_end = end
    cursor = end - 1
    chunk = b""
    chunk_end = 0
    while cursor > 0:
        chunk_start = max(0, cursor - _IO_CHUNK_BYTES)
        stream.seek(chunk_start)
        chunk = stream.read(cursor - chunk_start)
        if len(chunk) != cursor - chunk_start:
            raise ValueError("native reader interval was truncated")
        chunk_end = chunk_start + len(chunk)
        pos = len(chunk)
        while True:
            split = chunk.rfind(b"\n", 0, pos)
            if split < 0:
                break
            line_start = chunk_start + split + 1
            if line_end > line_start:
                raw_slice: bytes | None = None
                if line_end <= chunk_end:
                    raw_slice = chunk[line_start - chunk_start : line_end - chunk_start]
                elif line_end == end and cursor == end - 1:
                    raw_slice = chunk[line_start - chunk_start :] + b"\n"
                yield line_start, line_end, raw_slice
            line_end = line_start
            pos = split
        cursor = chunk_start
    if line_end > 0:
        head_slice = chunk[:line_end] if line_end <= chunk_end else None
        yield 0, line_end, head_slice


def _reader_spans(stream: BinaryIO, end: int) -> Iterator[tuple[int, int]]:
    """Walk backwards from the hook one bounded chunk at a time, yielding line spans."""
    for start, stop, _ in _reader_slices(stream, end):
        yield start, stop


def _native_reader(
    stream: BinaryIO, end: int, event: Mapping[str, object]
) -> tuple[int, str] | None:
    """Bind a hook to its own call, or to a single open native Code Mode call."""
    reader = event["tool_use_id"]
    completed = False
    closed_parents: set[str] = set()
    parent: tuple[int, str] | None = None
    for offset, stop, raw_slice in _reader_slices(stream, end):
        raw_len = stop - offset - 1
        if raw_len > _MAX_RECORD_BYTES:
            if parent is None:
                raise ValueError("unsupported native reader record")
            record = _scan_turn_prefix_record(stream, offset, stop - 1)
        else:
            if raw_slice is None:
                stream.seek(offset)
                raw_slice = stream.read(stop - offset)
            parsed = json.loads(raw_slice)
            if not isinstance(parsed, dict):
                raise TypeError("native replay record must be a JSON object")
            record = cast(dict[str, object], parsed)
        if _turn_boundary(record):
            break
        raw_payload = record["payload"]
        if not isinstance(raw_payload, Mapping):
            raise TypeError("native replay payload must be a mapping")
        payload = cast(Mapping[str, object], raw_payload)
        kind = payload.get("type")
        if record.get("type") == "event_msg" and kind == "item_completed":
            item = _mapping_field(payload, "item")
            if item.get("type") == "CommandExecution" and item.get("id") == reader:
                if item.get("exit_code") != 0 or payload.get("turn_id") != event.get(
                    "turn_id"
                ):
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
            return offset, _digest(record)
        if kind == "custom_tool_call_output":
            closed_parents.add(call)
        if (
            kind != "custom_tool_call"
            or payload.get("name") != "exec"
            or call in closed_parents
        ):
            continue
        if (
            not completed
            or parent is not None
            or not isinstance(event.get("turn_id"), str)
        ):
            return None
        # A qualified native record can omit message metadata. The completed
        # command still carries the Turn, and the parent is uniquely open.
        metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
        if not isinstance(metadata, Mapping) or metadata.get("turn_id") not in (
            None,
            event["turn_id"],
        ):
            return None
        parent = offset, _digest(record)
    return parent


def _replayed_selection_sections(
    anchor: Mapping[str, object],
    snapshot: Mapping[str, object],
    stream: BinaryIO,
    event: Mapping[str, object],
) -> dict[str, str]:
    selections = anchor.get("selections")
    if (
        not isinstance(selections, dict)
        or not selections
        or _verified_replay_capture(anchor, snapshot, stream) is None
    ):
        return {}
    reads: list[Mapping[str, object]] = [
        cast(Mapping[str, object], read)
        for selection in selections.values()
        if isinstance(selection, Mapping)
        for read in cast(Sequence[object], selection["reads"])
        if isinstance(read, Mapping)
    ]
    outputs = {
        (offset, digest)
        for offset, digest, _ in _replayed_output_groups(
            anchor, snapshot, stream, event, reads
        )
    }
    retained: dict[str, str] = {}
    for selection in selections.values():
        if not isinstance(selection, Mapping):
            continue
        raw_reads = selection["reads"]
        ranges = [
            (
                _int_value(read["start"], field_name="start"),
                _int_value(read["end"], field_name="end"),
            )
            for read in cast(Sequence[Mapping[str, object]], raw_reads)
            if (read["native_offset"], read["pages_sha256"]) in outputs
        ]
        total_bytes = selection["total_bytes"]
        if isinstance(total_bytes, int) and _covers_bytes(ranges, total_bytes):
            sections = selection["sections"]
            if isinstance(sections, Mapping):
                retained.update(
                    {str(k): str(v) for k, v in sections.items()}
                )
    return retained
