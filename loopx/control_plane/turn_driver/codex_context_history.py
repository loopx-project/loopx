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
import re
import stat
from collections.abc import Iterator
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
_MAX_SELECTED_REPLAY_READS = 32
_MAX_COMPLETE_REREADS = 32
_SHELL_OUTPUT = re.compile(r"\AChunk ID: [^\r\n]+\nWall time: [0-9.]+ seconds\n"
    r"Process exited with code 0\n(?:Original token count: [0-9]+\n)?Output:\n(.*)\Z", re.DOTALL)


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
                    "0.166.0-alpha.5.1", "0.167.0-alpha.3.2", "0.167.0-alpha.4.1")
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


def _reader_tail(stream: BinaryIO, end: int) -> Iterator[tuple[int, dict[str, Any]]]:
    """Walk backwards from the hook, one bounded record at a time."""
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
            yield cursor + split + 1, record
        if len(buffer) > _MAX_RECORD_BYTES:
            raise ValueError("unsupported native reader record")


def _turn_boundary(record: dict[str, Any]) -> bool:
    return record.get("type") == "event_msg" and record.get("payload", {}).get("type") == "task_started"


def _native_reader(stream: BinaryIO, end: int, event: dict[str, Any]) -> tuple[int, str] | None:
    """Bind a hook to its own call, or to a single open native Code Mode call."""
    reader = event["tool_use_id"]
    completed = False
    closed_parents: set[str] = set()
    parent = None
    for offset, record in _reader_tail(stream, end):
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
    return all(anchor.get(k) == snapshot.get(k) for k in
               ("path", "device", "inode", "metadata_sha256", "model", "permission_mode"))


def _matching_replay_interval(anchor: dict[str, Any], snapshot: dict[str, Any], stream: BinaryIO) -> bool:
    start, end = anchor.get("reader_start"), anchor.get("observed_bytes")
    return (_same_replay_source(anchor, snapshot) and type(start) is int and type(end) is int
        and 0 < start <= end <= snapshot["observed_bytes"]
        and _region_digest(stream, start, end) == anchor.get("reader_sha256"))


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
    from .decision_capture import capture_page_range, read_context_capture_file

    if not _matching_replay_interval(anchor, snapshot, stream):
        return False
    reads, reference = anchor.get("reads"), anchor.get("read_source")
    rereads = anchor.get("rereads", [])
    if not isinstance(reads, list) or not reads or not isinstance(rereads, list) or not isinstance(reference, dict):
        return False
    reads = [*reads, *rereads]
    _, body, _ = read_context_capture_file(reference["path"], reference["sha256"], private=True)
    required = {(read["native_offset"], read["pages_sha256"]) for read in reads}
    ranges = []
    for offset, digest, group in _replayed_output_groups(anchor, snapshot, stream, event, reads):
        if (offset, digest) not in required:
            continue
        for page in group:
            span = capture_page_range(page, raw=body, digest=reference["sha256"])
            if span is None:
                return False
            ranges.append(span)
    # An independently observed reread can recover a missing range. A copy
    # emitted by an unrelated call cannot contribute even identical bytes.
    return _covers_bytes(ranges, len(body))


def _replayed_selection_sections(anchor: dict[str, Any], snapshot: dict[str, Any], stream: BinaryIO,
                                 event: dict[str, Any]) -> dict[str, str]:
    from .decision_capture import read_context_capture_file

    selections = anchor.get("selections")
    if not isinstance(selections, dict) or not selections or not _matching_replay_interval(anchor, snapshot, stream):
        return {}
    source = anchor["read_source"]
    read_context_capture_file(source["path"], source["sha256"], private=True)
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
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return state
    if native_reader is None:
        return state
    offset, digest = native_reader
    read = {"native_offset": offset, "native_sha256": digest, "pages_sha256": _digest(pages)}
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
            offset, digest = native_reader
            read = {"native_offset": offset, "native_sha256": digest, "pages_sha256": _digest(pages)}
        rereads = anchor.get("rereads", [])
        if read not in anchor["reads"] and read not in rereads:
            candidate["native_resume"] = {**anchor, "rereads": [*rereads, read][-_MAX_COMPLETE_REREADS:]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return


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


def native_resume_sections(state: dict[str, Any], event: dict[str, Any]) -> dict[str, str]:
    """Verify replay of exact selected outputs without claiming a whole capture read."""
    anchor = state.get("selected_resume")
    if (event.get("source") != "resume" or not isinstance(anchor, dict)
            or state.get("session_id") != event.get("session_id") or state.get("cwd") != event.get("cwd")
            or anchor.get("scope") != state.get("read_scope") or anchor.get("epoch") != state.get("context_epoch")
            or anchor.get("read_source") != state.get("source_recovery")):
        return {}
    try:
        with _snapshot(event) as (facts, stream):
            retained = _replayed_selection_sections(anchor, facts, stream, event)
        return retained
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
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
        receipt = {"call_id": call, "native_offset": native_offset, "native_sha256": native_sha256,
                   "pages_sha256": _digest(raw_pages), "start": offset, "end": end}
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
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
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
            if not _same_replay_source(start, facts):
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
