"""Read-only historical outbox codecs and filesystem inventory.

These retained Python IO codecs preserve legacy diagnostic behavior. They do
not allocate sequences, write markers, reclaim residue or authorize delivery;
TypeScript owns candidate history proof and receipt-proven drain effects.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..content_digest import ENVELOPED_SHA256_PATTERN
from .local_authority_shadow_projection import PARTITIONS, sha256_digest, text_digest
from .coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
)

OUTBOX_ENTRY_SCHEMA = LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA
OUTBOX_COMMIT_SCHEMA = LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA
DRAIN_CURSOR_SCHEMA = LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA
SOURCE_MARKDOWN = "markdown_active_state"
SOURCE_STATE_EVENT_LOG = "state_event_log"
SOURCE_TASK_LEASE = "task_lease_record"
WRITER_RUNTIME_PYTHON = "python"
WRITER_RUNTIME_TYPESCRIPT = "typescript"
ENTRY_ID_PREFIX = "local-shadow-tx-"
_ENTRY_FILE = re.compile(
    r"^(?P<seq>\d{10})-(?P<entry_id>local-shadow-tx-[0-9a-f]{64})\.(?P<phase>prepared|committed)\.json$"
)


class OutboxError(RuntimeError):
    """Typed outbox failure; never escapes into a primary write."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code

def outbox_root(runtime_root: Path, goal_id: str) -> Path:
    return runtime_root / "authority-shadow" / "outbox" / goal_id


def partition_directory(runtime_root: Path, goal_id: str, partition: str) -> Path:
    if partition not in PARTITIONS:
        raise OutboxError(
            "invalid_partition", f"unknown outbox partition {partition!r}"
        )
    return outbox_root(runtime_root, goal_id) / partition

def lease_directory(runtime_root: Path, goal_id: str) -> Path:
    return runtime_root / "goals" / goal_id / "task-leases"


def entry_identity(
    *,
    goal_id: str,
    partition: str,
    seq: int,
    source_ref: str,
    capture_lineage_id: str,
    source_root_digest: str,
) -> str:
    """Bind the entry id to the exact primary bytes (or event) it records."""

    return ENTRY_ID_PREFIX + sha256_digest(
        {
            "goal_id": goal_id,
            "partition": partition,
            "seq": seq,
            "source_ref": source_ref,
            "capture_lineage_id": capture_lineage_id,
            "source_root_digest": source_root_digest,
        }
    ).removeprefix("sha256:")


def entry_file_name(seq: int, entry_id: str, phase: str) -> str:
    return f"{seq:010d}-{entry_id}.{phase}.json"

def _as_object(value: object) -> dict[str, Any]:
    """Narrow an untyped JSON value to an object; anything else is empty."""

    return dict(value) if isinstance(value, Mapping) else {}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise OutboxError(
            "outbox_file_invalid", f"{path.name} is not valid JSON"
        ) from error
    except FileNotFoundError:
        raise
    except OSError as error:
        raise OutboxError(
            "outbox_file_unavailable", f"{path.name} cannot be read"
        ) from error
    if not isinstance(raw, dict):
        raise OutboxError("outbox_file_invalid", f"{path.name} is not a JSON object")
    return raw


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """One two-phase entry as found on disk."""

    partition: str
    seq: int
    entry_id: str
    prepared_path: Path
    committed_path: Path | None
    prepared: dict[str, Any]
    committed: dict[str, Any] | None

    @property
    def is_committed(self) -> bool:
        return self.committed is not None

    @property
    def source_ref(self) -> str:
        return str(record_source_ref(self.prepared))

    def projection(self) -> dict[str, Any] | None:
        """The partition projection recorded for this entry, if any."""

        for record in (self.committed, self.prepared):
            if isinstance(record, dict) and isinstance(record.get("projection"), dict):
                return dict(record["projection"])
        return None

    def recorded_partition_digest(self) -> str | None:
        for record in (self.committed, self.prepared):
            if isinstance(record, dict) and isinstance(
                record.get("partition_digest"), str
            ):
                return str(record["partition_digest"])
        return None


_EntryKey = tuple[int, str]


def _index_entry_files(
    directory: Path,
) -> tuple[dict[_EntryKey, Path], dict[_EntryKey, Path]]:
    """Map ``(seq, entry_id)`` to the prepared and committed files present."""

    prepared: dict[_EntryKey, Path] = {}
    committed: dict[_EntryKey, Path] = {}
    try:
        paths = list(directory.iterdir())
    except FileNotFoundError:
        return prepared, committed
    except OSError as error:
        raise OutboxError(
            "outbox_file_unavailable", "outbox inventory cannot be read"
        ) from error
    for path in paths:
        if path.name == "drain-cursor.json" and not path.is_symlink():
            continue
        match = _ENTRY_FILE.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            raise OutboxError(
                "outbox_file_invalid", "outbox contains unclassified evidence"
            )
        if int(match.group("seq")) < 1:
            raise OutboxError(
                "outbox_file_invalid", "outbox sequence is outside its range"
            )
        key = (int(match.group("seq")), match.group("entry_id"))
        target = prepared if match.group("phase") == "prepared" else committed
        target[key] = path
    return prepared, committed


_WRITER_RUNTIMES = frozenset({WRITER_RUNTIME_PYTHON, WRITER_RUNTIME_TYPESCRIPT})
_SOURCE_KINDS = frozenset({SOURCE_MARKDOWN, SOURCE_STATE_EVENT_LOG, SOURCE_TASK_LEASE})


def _load_prepared_record(
    path: Path,
    *,
    directory: Path,
    seq: int,
    entry_id: str,
) -> dict[str, Any]:
    """Load one prepared record and prove it binds this directory and entry id.

    The file name, the directory (``outbox/<goal>/<partition>``), the record's
    own goal/partition fields, and the entry id recomputed from the record's
    source reference must all agree; a record that was copied, edited, or
    written for another goal fails closed before it can reach the candidate.
    """

    record = _load_json(path)
    writer = _as_object(record.get("writer"))
    source = _as_object(record.get("source"))
    expected_goal = directory.parent.name
    expected_partition = directory.name
    source_ref = record_source_ref(record)
    root_digest = record.get("source_root_digest")
    lineage_id = record.get("capture_lineage_id")
    bound = (
        record.get("schema_version") == OUTBOX_ENTRY_SCHEMA
        and record.get("entry_id") == entry_id
        and record.get("seq") == seq
        and type(record.get("seq")) is int
        and 1 <= seq <= MAX_OUTBOX_SEQUENCE
        and record.get("goal_id") == expected_goal
        and record.get("partition") == expected_partition
        and writer.get("runtime") in _WRITER_RUNTIMES
        and isinstance(writer.get("write_class"), str)
        and bool(writer.get("write_class"))
        and source.get("kind") in _SOURCE_KINDS
        and isinstance(root_digest, str)
        and ENVELOPED_SHA256_PATTERN.match(root_digest) is not None
        and isinstance(lineage_id, str)
        and bool(lineage_id)
        and source_ref is not None
        and entry_identity(
            goal_id=expected_goal,
            partition=expected_partition,
            seq=seq,
            source_ref=source_ref,
            capture_lineage_id=lineage_id,
            source_root_digest=root_digest,
        )
        == entry_id
    )
    if not bound:
        raise OutboxError(
            "outbox_file_invalid",
            f"{path.name} does not bind its directory, identity, and source reference",
        )
    return record


def _load_committed_record(
    path: Path | None, *, entry_id: str, capture_lineage_id: str | None = None
) -> dict[str, Any] | None:
    if path is None:
        return None
    record = _load_json(path)
    if (
        record.get("schema_version") != OUTBOX_COMMIT_SCHEMA
        or record.get("entry_id") != entry_id
        or not isinstance(record.get("capture_lineage_id"), str)
        or not record["capture_lineage_id"]
        or (
            capture_lineage_id is not None
            and record["capture_lineage_id"] != capture_lineage_id
        )
    ):
        raise OutboxError(
            "outbox_file_invalid", f"{path.name} does not match its entry"
        )
    return record


def _retired_watermark(directory: Path) -> int:
    cursor = read_cursor(directory)
    return int(cursor.get("last_seq") or 0) if cursor is not None else 0


def retired_residue(directory: Path) -> list[Path]:
    """Entry files at or below the durable cursor.

    The cursor is written before an entry's files are unlinked, so anything it
    covers is already settled in the candidate store. A crash between the
    cursor write and the unlinks, or between the two unlinks, leaves these
    files behind. This is a diagnostic count only: every file still requires
    an exact receipt proof before it can be reclaimed.
    """

    if not directory.is_dir():
        return []
    watermark = _retired_watermark(directory)
    prepared, committed = _index_entry_files(directory)
    residue = [
        path
        for key, path in [*prepared.items(), *committed.items()]
        if key[0] <= watermark
    ]
    return sorted(residue)


def list_entries(
    directory: Path, *, allow_committed_only: bool = False
) -> list[OutboxEntry]:
    """All entries, including unverified residue, independent of cursor hints."""

    prepared, committed = _index_entry_files(directory)
    identities: dict[int, str] = {}
    for seq, entry_id in [*prepared, *committed]:
        if seq in identities and identities[seq] != entry_id:
            raise OutboxError(
                "outbox_file_invalid", "sequence has multiple entry identities"
            )
        identities[seq] = entry_id
    orphan_markers = sorted(set(committed) - set(prepared))
    if orphan_markers and not allow_committed_only:
        seq, entry_id = orphan_markers[0]
        raise OutboxError(
            "outbox_file_invalid",
            f"committed marker without prepared entry: {entry_file_name(seq, entry_id, 'committed')}",
        )
    entries: list[OutboxEntry] = []
    for seq, entry_id in orphan_markers:
        marker_path = committed[(seq, entry_id)]
        entries.append(
            OutboxEntry(
                partition=directory.name,
                seq=seq,
                entry_id=entry_id,
                prepared_path=directory / entry_file_name(seq, entry_id, "prepared"),
                committed_path=marker_path,
                prepared={},
                committed=_load_committed_record(marker_path, entry_id=entry_id),
            )
        )
    for seq, entry_id in sorted(prepared):
        key = (seq, entry_id)
        prepared_record = _load_prepared_record(
            prepared[key], directory=directory, seq=seq, entry_id=entry_id
        )
        entries.append(
            OutboxEntry(
                partition=str(prepared_record.get("partition") or directory.name),
                seq=seq,
                entry_id=entry_id,
                prepared_path=prepared[key],
                committed_path=committed.get(key),
                prepared=prepared_record,
                committed=_load_committed_record(
                    committed.get(key),
                    entry_id=entry_id,
                    capture_lineage_id=prepared_record["capture_lineage_id"],
                ),
            )
        )
    return sorted(entries, key=lambda entry: entry.seq)


def cursor_path(directory: Path) -> Path:
    return directory / "drain-cursor.json"


def read_cursor(directory: Path) -> dict[str, Any] | None:
    path = cursor_path(directory)
    if path.is_symlink():
        raise OutboxError(
            "outbox_file_invalid", "cursor must belong to its own partition"
        )
    try:
        record = _load_json(path)
    except FileNotFoundError:
        return None
    return decode_cursor(record, partition=directory.name)


_CURSOR_FIELDS = frozenset(
    {
        "schema_version",
        "partition",
        "last_seq",
        "last_entry_id",
        "last_partition_digest",
        "last_cursor",
        "last_provider_revision",
        "updated_at",
    }
)
MAX_OUTBOX_SEQUENCE = 9_999_999_999


def decode_cursor(value: object, *, partition: str) -> dict[str, Any]:
    """Decode the shared wire cursor. It is a hint, never a delivery receipt."""

    def invalid() -> OutboxError:
        return OutboxError("outbox_file_invalid", "drain cursor binding is invalid")

    if not isinstance(value, dict) or set(value) != _CURSOR_FIELDS:
        raise invalid()
    seq = value.get("last_seq")
    if isinstance(seq, bool) or not isinstance(seq, (int, float)):
        raise invalid()
    if not 1 <= seq <= MAX_OUTBOX_SEQUENCE or not math.isfinite(seq) or seq != int(seq):
        raise invalid()
    entry_id = value.get("last_entry_id")
    digest = value.get("last_partition_digest")
    if (
        value.get("schema_version") != DRAIN_CURSOR_SCHEMA
        or partition not in PARTITIONS
        or value.get("partition") != partition
        or not isinstance(entry_id, str)
        or re.fullmatch(r"local-shadow-tx-[0-9a-f]{64}", entry_id) is None
        or (
            digest is not None
            and (
                not isinstance(digest, str)
                or ENVELOPED_SHA256_PATTERN.fullmatch(digest) is None
            )
        )
        or any(
            not isinstance(value.get(key), str) or not value[key].strip()
            for key in ("last_cursor", "last_provider_revision")
        )
    ):
        raise invalid()
    timestamp = value.get("updated_at")
    if (
        not isinstance(timestamp, str)
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", timestamp
        )
        is None
    ):
        raise invalid()
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise invalid() from error
    if parsed.utcoffset() != timedelta(0):
        raise invalid()
    return {**value, "last_seq": int(seq)}

def runtime_root_digest(runtime_root: Path) -> str:
    """Digest of the absolute, dot-normalized root as this process spells it.

    Diagnostic only (drain evidence). Outbox entries and receipts carry the
    active binding's ``source_root_digest`` instead, which the TypeScript owner
    derives from the resolved root, so a root reached through a symlink hashes
    differently from this lexical spelling (#4892).
    """

    return text_digest(os.path.abspath(str(runtime_root)))


def record_source_ref(record: Mapping[str, Any]) -> str | None:
    """The source reference an entry id binds: bytes digest, event id, or seed digest."""

    source = _as_object(record.get("source"))
    bytes_digest = source.get("bytes_digest")
    if isinstance(bytes_digest, str) and bytes_digest:
        return bytes_digest
    event_id = source.get("event_id")
    if isinstance(event_id, str) and event_id:
        return f"event:{event_id}"
    digest = record.get("partition_digest")
    if isinstance(digest, str) and digest:
        return f"seed:{digest}"
    return None

def outbox_summary(runtime_root: Path, goal_id: str) -> dict[str, Any]:
    """Counts per partition for operator readback; never raises on an empty outbox."""

    summary: dict[str, Any] = {}
    for partition in PARTITIONS:
        directory = partition_directory(runtime_root, goal_id, partition)
        try:
            entries = list_entries(directory)
            cursor = read_cursor(directory)
            residue_count = len(retired_residue(directory))
            invalid: str | None = None
        except OutboxError as error:
            entries, cursor, invalid = [], None, error.reason_code
            residue_count = 0
        except OSError:
            entries, cursor, invalid = [], None, "outbox_file_unavailable"
            residue_count = 0
        summary[partition] = {
            "committed_pending": sum(1 for entry in entries if entry.is_committed),
            "prepared_only": sum(1 for entry in entries if not entry.is_committed),
            "retired_residue": residue_count,
            "next_seq": (
                max((entry.seq for entry in entries), default=0) if entries else 0
            ),
            "cursor_last_seq": int(cursor.get("last_seq") or 0) if cursor else None,
            "cursor_last_entry_id": cursor.get("last_entry_id") if cursor else None,
            "invalid": invalid,
        }
    return summary
