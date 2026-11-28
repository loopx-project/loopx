"""Bounded IO observations for native-selected Codex replay, never Turn authority.

SessionStart(resume) is emitted after native history reconstruction. Reuse still
assumes the qualified native host replays its selected append-only source. Bind
that source to the current baseline's reader interval, verify every completed
read and reject unknown/lossy replay tails.

This is not a scan of model inputs or a certificate from a session ID/journal.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import BinaryIO, cast

from .codex_context_history_json import (
    _IO_CHUNK_BYTES as _IO_CHUNK_BYTES,
    _MAX_HEADER_FIELD_BYTES as _MAX_HEADER_FIELD_BYTES,
    _MAX_PRIMITIVE_BYTES as _MAX_PRIMITIVE_BYTES,
    _MAX_RECORD_BYTES as _MAX_RECORD_BYTES,
    _MAX_SKIP_DEPTH as _MAX_SKIP_DEPTH,
    _PREFIX_PAYLOAD_SPEC as _PREFIX_PAYLOAD_SPEC,
    _PREFIX_RECORD_SPEC as _PREFIX_RECORD_SPEC,
    _JsonSliceCursor as _JsonSliceCursor,
    _digest as _digest,
    _reject_json_constant as _reject_json_constant,
    _scan_selected_object as _scan_selected_object,
    _scan_turn_prefix_record as _scan_turn_prefix_record,
)
from .codex_context_history_replay import (
    _APPEND_EVENTS as _APPEND_EVENTS,
    _APPEND_RECORDS as _APPEND_RECORDS,
    _MAX_COMPLETE_REREADS as _MAX_COMPLETE_REREADS,
    _MAX_SELECTED_REPLAY_READS as _MAX_SELECTED_REPLAY_READS,
    _REPLAY_ERRORS as _REPLAY_ERRORS,
    _SOURCE_IDENTITY_FIELDS as _SOURCE_IDENTITY_FIELDS,
    _append_only as _append_only,
    _covers_bytes as _covers_bytes,
    _emission_pages as _emission_pages,
    _mapping_field,
    _matches as _matches,
    _matching_replay_interval as _matching_replay_interval,
    _matching_resume_session as _matching_resume_session,
    _native_reader as _native_reader,
    _read_pages as _read_pages,
    _read_receipt as _read_receipt,
    _reader_slices as _reader_slices,
    _reader_spans as _reader_spans,
    _records as _records,
    _region_digest as _region_digest,
    _replayed_output_groups as _replayed_output_groups,
    _replayed_selection_sections as _replayed_selection_sections,
    _same_replay_source as _same_replay_source,
    _turn_boundary as _turn_boundary,
    _verified_replay_capture as _verified_replay_capture,
)


@contextmanager
def _snapshot(
    event: Mapping[str, object],
) -> Iterator[tuple[dict[str, object], BinaryIO]]:
    path = Path(str(event.get("transcript_path") or ""))
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("native replay source must be physical and absolute")
    # Inspect the opened descriptor before reading. A FIFO must not wait for a
    # writer while the hook holds its context journal lock.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_ino != path.lstat().st_ino
            or info.st_mode & 0o022
            or info.st_size <= 0
        ):
            raise ValueError("native replay source must be an owned regular file")
        first_raw = stream.readline(_MAX_RECORD_BYTES + 1)
        if len(first_raw) > _MAX_RECORD_BYTES or not first_raw.endswith(b"\n"):
            raise ValueError("invalid native session metadata")
        first = json.loads(first_raw)
        if not isinstance(first, Mapping):
            raise ValueError("invalid native session metadata")
        meta = _mapping_field(first, "payload")
        if (
            first.get("type") != "session_meta"
            or meta.get("id") != event.get("session_id")
            or meta.get("cwd") != event.get("cwd")
            or meta.get("forked_from_id")
            or meta.get("history_mode") != "paginated"
            or meta.get("cli_version")
            not in (
                "0.153.4",
                "0.162.1",
                "0.163.0-alpha.11.1",
                "0.163.0-alpha.11.2",
                "0.166.0-alpha.5.1",
                "0.167.0-alpha.3.2",
                "0.169.0-alpha.11.1",
            )
            or not event.get("model")
            or not event.get("permission_mode")
        ):
            raise ValueError("unqualified native replay metadata")
        stream.seek(info.st_size - 1)
        if stream.read(1) != b"\n":
            raise ValueError("native replay observation ends within a record")
        # cli_version describes the original writer, not the current host.
        # Qualify the supported replay records below, including after upgrade.
        facts: dict[str, object] = {
            "path": str(path),
            "device": str(info.st_dev),
            "inode": str(info.st_ino),
            "metadata_sha256": hashlib.sha256(first_raw).hexdigest(),
            "observed_bytes": info.st_size,
            "model": event["model"],
            "permission_mode": event["permission_mode"],
        }
        yield facts, stream
        after = os.fstat(stream.fileno())
        if (
            after.st_size != info.st_size
            or after.st_mtime_ns != info.st_mtime_ns
            or path.lstat().st_ino != info.st_ino
        ):
            raise ValueError("native replay source changed during observation")


def observe_native_read(
    state: Mapping[str, object],
    event: Mapping[str, object],
    pages: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Collect content-free IO facts alongside the existing pending byte ranges."""
    pending = state.get("pending")
    call = event.get("tool_use_id")
    if (
        not isinstance(call, str)
        or not call
        or not pages
        or any(page.get("ok") is not True for page in pages)
    ):
        return dict(state)
    if pending is None:
        recovery = state.get("recovery")
        candidate = state.get("candidate") or {}
        if (
            not isinstance(recovery, dict)
            or not isinstance(candidate, dict)
            or any(page.get("sha256") != recovery.get("sha256") for page in pages)
            or (
                candidate.get("capture_sha256") == recovery.get("sha256")
                and candidate.get("capture_bytes_verified") is True
            )
        ):
            return dict(state)
        # Invalidation clears pending reads. Record the first original-capture
        # recovery read too, including a full capture delivered in one page.
        # These are IO annotations only: the typed lifecycle still authenticates
        # coverage and decides whether a complete baseline can be established.
        pending = {}
    if not isinstance(pending, dict):
        return dict(state)
    try:
        with _snapshot(event) as (facts, stream):
            observed_bytes = facts["observed_bytes"]
            if not isinstance(observed_bytes, int):
                raise TypeError("observed_bytes must be an int")
            native_reader = _native_reader(stream, observed_bytes, event)
    except _REPLAY_ERRORS:
        return dict(state)
    if native_reader is None:
        return dict(state)
    offset, digest = native_reader
    read = _read_receipt(offset, digest, pages)
    start, reads = pending.get("native_reader_start"), pending.get("native_reads")
    if (
        not isinstance(start, dict)
        or not isinstance(reads, list)
        or not _same_replay_source(start, facts)
    ):
        start, reads = {**facts, "reader_start": offset}, []
    elif isinstance(start.get("reader_start"), int) and offset < start["reader_start"]:
        start = {**facts, "reader_start": offset}
    if read not in reads:
        reads = [*reads, read]
    next_pending = {**pending, "native_reads": reads, "native_reader_start": start}
    return {**state, "pending": next_pending}


def observe_completed_native_reread(
    state: Mapping[str, object],
    updated: dict[str, object],
    event: Mapping[str, object],
    pages: Sequence[Mapping[str, object]],
) -> None:
    """Add independently observed output to the current full-capture checkpoint."""
    candidate = updated.get("candidate")
    if not isinstance(candidate, dict) or candidate != state.get("candidate") or not pages:
        return
    anchor, reference = candidate.get("native_resume"), state.get("recovery")
    if not isinstance(anchor, dict) or not isinstance(reference, dict):
        return
    anchor_read_source = anchor.get("read_source")
    if (
        not isinstance(anchor_read_source, Mapping)
        or anchor_read_source.get("sha256") != reference.get("sha256")
        or any(
            page.get("ok") is not True or page.get("sha256") != reference.get("sha256")
            for page in pages
        )
    ):
        return
    try:
        with _snapshot(event) as (facts, stream):
            # Resume checks the original interval digest. Extra observations
            # cannot change it and do not need to hash it again at each hook.
            anchor_bytes = anchor["observed_bytes"]
            facts_bytes = facts["observed_bytes"]
            anchor_start = anchor["reader_start"]
            if (
                not _same_replay_source(anchor, facts)
                or not isinstance(anchor_bytes, int)
                or not isinstance(facts_bytes, int)
                or not isinstance(anchor_start, int)
                or anchor_bytes > facts_bytes
            ):
                return
            native_reader = _native_reader(stream, facts_bytes, event)
            if native_reader is None or native_reader[0] < anchor_start:
                return
            read = _read_receipt(*native_reader, pages)
        rereads = anchor.get("rereads", [])
        existing_reads = anchor["reads"]
        if (
            isinstance(existing_reads, list)
            and isinstance(rereads, list)
            and read not in existing_reads
            and read not in rereads
        ):
            candidate["native_resume"] = {
                **anchor,
                "rereads": [*rereads, read][-_MAX_COMPLETE_REREADS:],
            }
    except _REPLAY_ERRORS:
        return


def native_resume_verified(
    state: Mapping[str, object], event: Mapping[str, object]
) -> bool:
    """Observe a qualified replay source; the shared TS owner decides reuse."""
    candidate = state.get("candidate") or {}
    if not isinstance(candidate, dict):
        return False
    anchor = candidate.get("native_resume")
    if (
        not _matching_resume_session(state, event, anchor)
        or state.get("pending")
        or not isinstance(anchor, dict)
    ):
        return False
    try:
        with _snapshot(event) as (facts, stream):
            verified = _matches(anchor, facts, stream, event)
        return verified
    except _REPLAY_ERRORS:
        return False


def native_resume_sections(
    state: Mapping[str, object], event: Mapping[str, object]
) -> dict[str, str]:
    """Verify replay of exact selected outputs without claiming a whole capture read."""
    anchor = state.get("selected_resume")
    if (
        not _matching_resume_session(state, event, anchor)
        or not isinstance(anchor, dict)
        or anchor.get("scope") != state.get("read_scope")
        or anchor.get("epoch") != state.get("context_epoch")
        or anchor.get("read_source") != state.get("source_recovery")
    ):
        return {}
    try:
        with _snapshot(event) as (facts, stream):
            retained = _replayed_selection_sections(anchor, facts, stream, event)
        return retained
    except _REPLAY_ERRORS:
        return {}


def _same_selected_source(
    previous: object,
    *,
    scope: object,
    epoch: object,
    source: object,
    event: Mapping[str, object],
) -> bool:
    return (
        isinstance(previous, dict)
        and previous.get("scope") == scope
        and previous.get("epoch") == epoch
        and previous.get("read_source") == source
        and previous.get("path") == event.get("transcript_path")
        and all(previous.get(k) == event.get(k) for k in ("model", "permission_mode"))
    )


def checkpoint_native_selection(
    state: Mapping[str, object],
    updated: dict[str, object],
    event: Mapping[str, object],
    raw_pages: Sequence[Mapping[str, object]],
    verified_pages: Sequence[Mapping[str, object]],
) -> None:
    """Record bounded, self-contained selections after literal byte verification."""
    if len(raw_pages) != 1 or len(verified_pages) != 1:
        return
    raw, page = raw_pages[0], verified_pages[0]
    sections, source = page.get("read_sections"), state.get("source_recovery")
    scope, epoch, call = (
        state.get("read_scope"),
        state.get("context_epoch"),
        event.get("tool_use_id"),
    )
    consumed = updated.get("consumed_pointers", {})
    if (
        not (
            raw.get("schema_version") == "context_capture_selection_page_v1"
            or ("selected_pointers" in raw and "selection_sha256" in raw)
        )
        or page.get("ok") is not True
        or page.get("read_sections_inherited")
        or not isinstance(sections, dict)
        or not isinstance(source, dict)
        or page.get("source_sha256") != source.get("sha256")
        or not isinstance(scope, dict)
        or not epoch
        or scope.get("context_epoch") != epoch
        or not isinstance(call, str)
        or not call
        or not isinstance(consumed, dict)
    ):
        return
    previous = state.get("selected_resume")
    same_source = _same_selected_source(
        previous, scope=scope, epoch=epoch, source=source, event=event
    )
    try:
        with _snapshot(event) as (facts, stream):
            end = facts["observed_bytes"]
            if not isinstance(end, int):
                raise TypeError("observed_bytes must be an int")
            native_reader = _native_reader(stream, end, event)
            if native_reader is None:
                return
            native_offset, native_sha256 = native_reader
            anchor: dict[str, object] = (
                dict(previous)
                if isinstance(previous, dict)
                and same_source
                and _same_replay_source(previous, facts)
                else {}
            )
            raw_selections = anchor.get("selections", {})
            selections: dict[str, dict[str, object]] = (
                dict(raw_selections) if isinstance(raw_selections, dict) else {}
            )
            all_reads = [
                read
                for entry in selections.values()
                if isinstance(entry, Mapping)
                for read in cast(Sequence[Mapping[str, object]], entry["reads"])
            ]
            if any(
                read["call_id"] == call and read["native_offset"] == native_offset
                for read in all_reads
            ):
                return
            if not selections or len(all_reads) >= _MAX_SELECTED_REPLAY_READS:
                anchor = {
                    **facts,
                    "reader_start": native_offset,
                    "reader_sha256": _region_digest(stream, native_offset, end),
                    "read_source": source,
                    "scope": scope,
                    "epoch": epoch,
                }
                selections = {}
            anchor_start = anchor["reader_start"]
            if not isinstance(anchor_start, int) or native_offset < anchor_start:
                return
        # Offsets belong to the exact selected view, which can coincide across
        # different sources or presentation bindings. Only combine pages from
        # the same physical reader and its verified view.
        selection = _digest([raw.get("reference_file") or raw["sha256"], page["sha256"]])
        offset = page["offset"]
        content = page["content"]
        total_bytes = page["total_bytes"]
        if (
            not isinstance(offset, int)
            or not isinstance(content, str)
            or not isinstance(total_bytes, int)
        ):
            raise TypeError("verified selection page missing integer bounds")
        end_offset = offset + len(content.encode("utf-8"))
        receipt: dict[str, object] = {
            "call_id": call,
            **_read_receipt(native_offset, native_sha256, raw_pages),
            "start": offset,
            "end": end_offset,
        }
        entry = selections.get(selection, {})
        observed = entry.get("sections", {}) if isinstance(entry, Mapping) else {}
        prev_reads = entry.get("reads", []) if isinstance(entry, Mapping) else []
        progress = [
            *cast(Sequence[Mapping[str, object]], prev_reads),
            receipt,
        ]
        if _covers_bytes(
            [(int(str(r["start"])), int(str(r["end"]))) for r in progress], total_bytes
        ):
            # The reducer and this replay interval both observed full coverage.
            # Never resume an inherited selection from only its displayed pages.
            if sections and all(
                consumed.get(pointer) == digest for pointer, digest in sections.items()
            ):
                observed = sections
        selections[selection] = {
            "reads": progress,
            "sections": observed,
            "total_bytes": total_bytes,
        }
        updated["selected_resume"] = {**anchor, "selections": selections}
    except _REPLAY_ERRORS:
        return


def checkpoint_native_read(
    state: Mapping[str, object],
    updated: dict[str, object],
    event: Mapping[str, object],
    pages: Sequence[Mapping[str, object]],
) -> None:
    """Attach content-free native source facts only after the typed complete read.

    A delta checkpoint can advance only a previously verified replay chain.
    Failure affects cross-process reuse, not normal in-process reader delivery.
    """
    candidate = updated.get("candidate")
    if (
        not isinstance(candidate, dict)
        or updated.get("pending")
        or not pages
        or candidate == state.get("candidate")
        or not event.get("tool_use_id")
    ):
        return
    try:
        pending = state.get("pending") or {}
        if not isinstance(pending, Mapping):
            return
        reference = pending if candidate.get("ancestry", 0) else state.get("recovery")
        reads = pending.get("native_reads")
        start = pending.get("native_reader_start")
        if (
            not isinstance(reference, dict)
            or not isinstance(reads, list)
            or not reads
            or not isinstance(start, dict)
        ):
            return
        with _snapshot(event) as (facts, stream):
            if any(
                start.get(k) != facts.get(k)
                for k in ("path", "device", "inode", "metadata_sha256")
            ):
                return
            reader_start = start["reader_start"]
            observed_bytes = facts["observed_bytes"]
            if not isinstance(reader_start, int) or not isinstance(observed_bytes, int):
                return
            if candidate.get("ancestry", 0):
                state_candidate = state.get("candidate") or {}
                anchor = (
                    state_candidate.get("native_resume")
                    if isinstance(state_candidate, Mapping)
                    else None
                )
                if not isinstance(anchor, dict) or not _matches(
                    anchor, facts, stream, event
                ):
                    return
                # A delta still depends on its earlier complete baseline.
                anchor_start = anchor["reader_start"]
                if not isinstance(anchor_start, int):
                    return
                reader_start = min(reader_start, anchor_start)
            if not 0 < reader_start <= observed_bytes:
                return
            checkpoint: dict[str, object] = {
                **facts,
                "reader_start": reader_start,
                "reader_sha256": _region_digest(stream, reader_start, observed_bytes),
                "reads": reads,
                "read_source": {key: reference[key] for key in ("path", "sha256")},
            }
        candidate["native_resume"] = checkpoint
    except _REPLAY_ERRORS:
        return
