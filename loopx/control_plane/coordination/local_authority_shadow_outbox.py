"""Durable, transaction-bound outbox for the local authority shadow.

A legacy writer that changes coordination facts records, inside the lock it
already holds, a two-phase entry for exactly the state that lock guards (one
*partition*): a ``prepared`` file computed from the bytes about to be written,
then a ``committed`` marker after the primary write returns. Nothing in the
lock talks to the TypeScript runtime. A later drain (same process after the
lock, or an operator command) turns each committed entry into exactly one
candidate-store transaction whose ``operation_id`` is the entry id.

Prepare failures return typed evidence for the transaction owner to reject an
active-lineage write before changing primary bytes. Marker failures preserve
prepared evidence; candidate delivery happens after releasing primary locks.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .local_authority_shadow_projection import (
    PARTITIONS,
    TODO_PARTITION,
    canonical_value,
    partition_digest,
    sha256_digest,
    text_digest,
)
from .coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA,
)
from .shadow_management import (
    read_shadow_capture_binding,
    shadow_maintenance_lock_target,
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
_LEASE_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.json$")


class OutboxError(RuntimeError):
    """Typed outbox failure; never escapes into a primary write."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


TodoPartitionProjector = Callable[[str], dict[str, Any]]
"""Active-state Markdown text -> ``{"handoff_mode": str, "todos": [compact...]}``."""


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def outbox_root(runtime_root: Path, goal_id: str) -> Path:
    return runtime_root / "authority-shadow" / "outbox" / goal_id


def partition_directory(runtime_root: Path, goal_id: str, partition: str) -> Path:
    if partition not in PARTITIONS:
        raise OutboxError(
            "invalid_partition", f"unknown outbox partition {partition!r}"
        )
    return outbox_root(runtime_root, goal_id) / partition


def drain_lock_target(runtime_root: Path, goal_id: str) -> Path:
    return shadow_maintenance_lock_target(runtime_root, goal_id)


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


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Temp file in the same directory, fsync, atomic replace, directory fsync."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1).encode(
        "utf-8"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


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
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


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
        and _DIGEST_PATTERN.match(root_digest) is not None
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


def raw_bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def verify_observed_files(files: Iterable[tuple[Path, str]]) -> None:
    """Recheck the complete observed byte batch before checkpoint or unlink effects."""
    for path, expected_digest in files:
        if raw_bytes_digest(path.read_bytes()) != expected_digest:
            raise OutboxError("outbox_file_changed", "verified outbox bytes changed")


def reclaim_verified_files(files: Iterable[tuple[Path, str]]) -> int:
    """Remove only exact receipt-proven bytes under maintenance/primary locks.

    A watermark alone never authorizes deletion. Recheck the whole batch before
    the first unlink, including when the caller just wrote its checkpoint.
    """
    batch = list(files)
    verify_observed_files(batch)
    for path, _digest in batch:
        path.unlink()
        _fsync_directory(path.parent)
    return len(batch)


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


def require_source_recovered(directory: Path) -> None:
    """A later write must preserve an unresolved transaction's byte witness."""
    if any(entry.committed_path is None for entry in list_entries(directory)):
        raise OutboxError("source_recovery_required", "drain the unresolved source transaction before writing")


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
                not isinstance(digest, str) or _DIGEST_PATTERN.fullmatch(digest) is None
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


def write_cursor(
    directory: Path,
    *,
    partition: str,
    last_seq: int,
    last_entry_id: str,
    last_partition_digest: str | None,
    last_cursor: str | None,
    last_provider_revision: str | None,
) -> None:
    durable_write_json(
        cursor_path(directory),
        decode_cursor(
            {
                "schema_version": DRAIN_CURSOR_SCHEMA,
                "partition": partition,
                "last_seq": last_seq,
                "last_entry_id": last_entry_id,
                "last_partition_digest": last_partition_digest,
                "last_cursor": last_cursor,
                "last_provider_revision": last_provider_revision,
                "updated_at": utc_now_text(),
            },
            partition=partition,
        ),
    )


def _proved_sequence(runtime_root: Path, goal_id: str, partition: str) -> int:
    """Read settled progress while the primary lock prevents management changes.

    This exceptional missing-cursor path owns neither M nor cursor writes. The
    native boundary validates the complete bounded history without initializing
    a missing store. Primary exclusion keeps its lineage stable during the read.
    """
    from ..effect_runtime import effect_runtime_result

    binding = read_shadow_capture_binding(runtime_root, goal_id)
    if binding["status"] != "active":
        raise OutboxError(
            "bootstrap_required", "sequence recovery needs an active lineage"
        )
    view = effect_runtime_result(
        "coordination.runtime_shadow.outbox_read",
        {
            "schema_version": LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
            "runtime_root": str(runtime_root),
            "goal_id": goal_id,
            "scan_limit": 0,
            "read_model": "proof",
        },
        timeout=15.0,
    )
    proof = view.get("proof") if isinstance(view, dict) else None
    if (
        not isinstance(view, dict)
        or view.get("schema_version") != LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA
        or view.get("status") != "loaded"
        or not isinstance(proof, dict)
        or proof.get("capture_lineage_id") != binding["binding"]["capture_lineage_id"]
        or view.get("store_identity") != binding["binding"]["store_identity"]
        or read_shadow_capture_binding(runtime_root, goal_id) != binding
    ):
        raise OutboxError(
            "outbox_sequence_unproved", "missing cursor has no stable history proof"
        )
    sequences = proof.get("last_sequences")
    seq = sequences.get(partition) if isinstance(sequences, dict) else None
    if type(seq) is not int or not 0 <= seq <= MAX_OUTBOX_SEQUENCE:
        raise OutboxError(
            "outbox_sequence_unproved", "history has no verified partition progress"
        )
    return seq


def next_seq(
    directory: Path, *, runtime_root: Path | None = None, goal_id: str | None = None
) -> int:
    """Allocate after visible files and the cursor hint; drain proves continuity."""

    highest = 0
    if directory.is_dir():
        for path in directory.iterdir():
            match = _ENTRY_FILE.match(path.name)
            if match is not None:
                highest = max(highest, int(match.group("seq")))
    cursor = read_cursor(directory)
    if cursor is not None:
        highest = max(highest, int(cursor.get("last_seq") or 0))
    elif runtime_root is not None and goal_id is not None:
        highest = max(highest, _proved_sequence(runtime_root, goal_id, directory.name))
    if highest >= MAX_OUTBOX_SEQUENCE:
        raise OutboxError("outbox_sequence_exhausted", "outbox sequence is exhausted")
    return highest + 1


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


def read_lease_records(directory: Path) -> list[tuple[str, dict[str, Any]]]:
    """Top-level lease records of a goal; lifecycle receipts are excluded."""

    if not directory.is_dir():
        return []
    records: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(directory.iterdir()):
        if (
            not path.is_file()
            or not _LEASE_FILE.match(path.name)
            or path.name.startswith(".")
        ):
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            records.append((path.stem, raw))
    return records


def _writer(
    write_class: str, *, runtime: str, operation_id: str | None
) -> dict[str, Any]:
    return {
        "runtime": runtime,
        "write_class": write_class,
        "operation_id": operation_id,
    }


def _entry_record(
    *,
    goal_id: str,
    partition: str,
    seq: int,
    entry_id: str,
    writer: Mapping[str, Any],
    source: Mapping[str, Any],
    source_root_digest: str,
    capture_lineage_id: str,
    projection: Mapping[str, Any] | None,
    digest: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": OUTBOX_ENTRY_SCHEMA,
        "goal_id": goal_id,
        "partition": partition,
        "seq": seq,
        "entry_id": entry_id,
        "writer": dict(writer),
        "source": dict(source),
        "source_root_digest": source_root_digest,
        "capture_lineage_id": capture_lineage_id,
        "projection": canonical_value(dict(projection))
        if projection is not None
        else None,
        "partition_digest": digest,
        "prepared_at": utc_now_text(),
    }


@dataclass
class CaptureOutcome:
    """What the capture did for one primary write (attached to its evidence)."""

    entry_id: str | None = None
    partition: str | None = None
    seq: int | None = None
    partition_digest: str | None = None
    source_bytes_digest: str | None = None
    skipped_reason: str | None = None
    failure: dict[str, Any] | None = None

    @property
    def recorded(self) -> bool:
        return self.entry_id is not None and self.failure is None


class TodoPartitionCapture:
    """Two-phase capture of the todos partition inside the active-state lock.

    ``begin`` returns an inert capture when the goal has no shadow binding, so
    default-off writers create no directory, lock, or file.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        runtime_root: Path | None,
        goal_id: str,
        state_path: Path | None,
        write_class: str,
        original_text: str,
        projector: TodoPartitionProjector | None,
    ) -> None:
        self._enabled = enabled
        self._runtime_root = runtime_root
        self._goal_id = goal_id
        self._state_path = state_path
        self._write_class = write_class
        self._original_digest = text_digest(original_text)
        self._original_text = original_text
        self._projector = projector
        self._directory = (
            partition_directory(runtime_root, goal_id, TODO_PARTITION)
            if enabled and runtime_root is not None
            else None
        )
        self._seq: int | None = None
        self._entry_id: str | None = None
        self._event_id: str | None = None
        self._lineage_id: str | None = None
        self.outcome = CaptureOutcome(partition=TODO_PARTITION if enabled else None)

    @classmethod
    def begin(
        cls,
        *,
        enabled: bool,
        runtime_root: Path | None,
        goal_id: str,
        state_path: Path | None,
        write_class: str,
        original_text: str,
        projector: TodoPartitionProjector | None,
    ) -> TodoPartitionCapture:
        """``projector`` maps active-state text to the todos partition projection.

        It is injected so this module stays free of the Markdown parser; the
        adapter supplies the production projector.
        """

        return cls(
            enabled=enabled,
            runtime_root=runtime_root,
            goal_id=goal_id,
            state_path=state_path,
            write_class=write_class,
            original_text=original_text,
            projector=projector,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and self._directory is not None

    def skip(self, reason: str) -> None:
        """Record why this writer deliberately did not open a transaction."""

        if self.enabled and self.outcome.entry_id is None:
            self.outcome.skipped_reason = reason

    def _project(self, state_text: str) -> dict[str, Any]:
        if self._projector is None:
            raise OutboxError(
                "outbox_prepare_failed", "a todo partition projector is required"
            )
        projection = self._projector(state_text)
        if set(projection) != {"handoff_mode", "todos"}:
            raise OutboxError(
                "outbox_prepare_failed", "projector must return {handoff_mode, todos}"
            )
        return projection

    def _fail(self, reason_code: str, error: BaseException) -> None:
        self.outcome.failure = {
            "reason_code": reason_code,
            "error_class": error.__class__.__name__,
        }

    def prepare(self, new_text: str, *, event_id: str | None = None,
                event_log_path: Path | None = None, previous_exists: bool = True) -> None:
        """Record the prepared entry for the bytes about to be written.

        Event batches carry their bound log path; an event id alone is not a
        source transaction proof and retains the compatibility hold.
        """

        if not self.enabled or self._directory is None or self._runtime_root is None:
            self.outcome.skipped_reason = "shadow_disabled"
            return
        binding_view = read_shadow_capture_binding(self._runtime_root, self._goal_id)
        if binding_view["status"] != "active":
            self.outcome.skipped_reason = str(
                binding_view.get("reason_code") or "bootstrap_required"
            )
            return
        binding = binding_view["binding"]
        self._lineage_id = str(binding["capture_lineage_id"])
        source_root_digest = str(binding["source_root_digest"])
        if event_id is not None and event_log_path is None:
            self.outcome.skipped_reason = "event_log_writer_not_bound"
            return
        try:
            # A later write must not erase the byte witness of an interrupted
            # transaction, including when this write leaves Todos unchanged.
            require_source_recovered(self._directory)
            projection = self._project(new_text)
            digest = partition_digest(projection)
            previous_digest = partition_digest(self._project(self._original_text))
            if digest == previous_digest:
                self.outcome.skipped_reason = "partition_unchanged"
                return
            source_ref = text_digest(new_text)
            bytes_digest = source_ref
            source_kind = SOURCE_STATE_EVENT_LOG if event_log_path is not None else SOURCE_MARKDOWN
            seq = next_seq(
                self._directory, runtime_root=self._runtime_root, goal_id=self._goal_id
            )
            entry_id = entry_identity(
                goal_id=self._goal_id,
                partition=TODO_PARTITION,
                seq=seq,
                source_ref=source_ref,
                capture_lineage_id=self._lineage_id,
                source_root_digest=source_root_digest,
            )
            record = _entry_record(
                goal_id=self._goal_id,
                partition=TODO_PARTITION,
                seq=seq,
                entry_id=entry_id,
                writer=_writer(
                    self._write_class,
                    runtime=WRITER_RUNTIME_PYTHON,
                    operation_id=event_id,
                ),
                source={
                    "kind": source_kind,
                    "previous_bytes_digest": self._original_digest if previous_exists else None,
                    "previous_partition_digest": previous_digest,
                    "bytes_digest": bytes_digest,
                    "lease": None,
                    "event_id": event_id,
                    **({"event_log_path": str(event_log_path.resolve())} if event_log_path is not None else {}),
                },
                source_root_digest=source_root_digest,
                capture_lineage_id=self._lineage_id,
                projection=projection,
                digest=digest,
            )
            durable_write_json(
                self._directory / entry_file_name(seq, entry_id, "prepared"),
                record,
            )
        except Exception as error:  # noqa: BLE001 - the transaction owner enforces active preparation
            self._fail(error.reason_code if isinstance(error, OutboxError) else "outbox_prepare_failed", error)
            return
        self._seq = seq
        self._entry_id = entry_id
        self._event_id = event_id
        self.outcome.entry_id = entry_id
        self.outcome.seq = seq
        self.outcome.partition_digest = digest
        self.outcome.source_bytes_digest = bytes_digest

    def committed(self) -> None:
        """Mark the prepared entry committed after the primary write returned."""

        if self._seq is None or self._entry_id is None or self._directory is None:
            return
        if self.outcome.failure is not None:
            return
        marker: dict[str, Any] = {
            "schema_version": OUTBOX_COMMIT_SCHEMA,
            "entry_id": self._entry_id,
            "capture_lineage_id": self._lineage_id,
            "committed_at": utc_now_text(),
        }
        try:
            durable_write_json(
                self._directory
                / entry_file_name(self._seq, self._entry_id, "committed"),
                marker,
            )
        except Exception as error:  # noqa: BLE001 - the primary write already landed
            self._fail("outbox_commit_marker_failed", error)


def entries_by_partition(
    runtime_root: Path, goal_id: str
) -> dict[str, list[OutboxEntry]]:
    return {
        partition: list_entries(partition_directory(runtime_root, goal_id, partition))
        for partition in PARTITIONS
    }


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


def iter_committed(entries: Iterable[OutboxEntry]) -> list[OutboxEntry]:
    return [entry for entry in entries if entry.is_committed]


__all__ = [
    "DRAIN_CURSOR_SCHEMA",
    "OUTBOX_COMMIT_SCHEMA",
    "OUTBOX_ENTRY_SCHEMA",
    "SOURCE_MARKDOWN",
    "SOURCE_STATE_EVENT_LOG",
    "SOURCE_TASK_LEASE",
    "CaptureOutcome",
    "OutboxEntry",
    "OutboxError",
    "TodoPartitionCapture",
    "TodoPartitionProjector",
    "drain_lock_target",
    "durable_write_json",
    "entries_by_partition",
    "entry_file_name",
    "entry_identity",
    "iter_committed",
    "lease_directory",
    "list_entries",
    "next_seq",
    "outbox_root",
    "outbox_summary",
    "partition_directory",
    "read_cursor",
    "read_lease_records",
    "reclaim_verified_files",
    "raw_bytes_digest",
    "record_source_ref",
    "retired_residue",
    "runtime_root_digest",
    "utc_now_text",
    "write_cursor",
]
