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
The native drain owns cursor publication and receipt-proven cleanup; Python
retains source capture; historical inventory/decoding has an independent IO owner.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .local_authority_shadow_projection import (
    TODO_PARTITION,
    canonical_value,
    partition_digest,
    text_digest,
)
from .coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA,
)
from .shadow_management import (
    read_shadow_capture_binding,
    shadow_maintenance_lock_target,
)


# Retained imports preserve existing writer/tool callers; the definitions and
# read-only CLI no longer depend on this producer.
from .local_authority_shadow_history import (
    DRAIN_CURSOR_SCHEMA as DRAIN_CURSOR_SCHEMA,
    OUTBOX_COMMIT_SCHEMA as OUTBOX_COMMIT_SCHEMA,
    OUTBOX_ENTRY_SCHEMA as OUTBOX_ENTRY_SCHEMA,
    SOURCE_MARKDOWN as SOURCE_MARKDOWN,
    SOURCE_STATE_EVENT_LOG as SOURCE_STATE_EVENT_LOG,
    SOURCE_TASK_LEASE as SOURCE_TASK_LEASE,
    WRITER_RUNTIME_PYTHON as WRITER_RUNTIME_PYTHON,
    WRITER_RUNTIME_TYPESCRIPT as WRITER_RUNTIME_TYPESCRIPT,
    ENTRY_ID_PREFIX as ENTRY_ID_PREFIX,
    MAX_OUTBOX_SEQUENCE as MAX_OUTBOX_SEQUENCE,
    OutboxEntry as OutboxEntry,
    OutboxError as OutboxError,
    outbox_root as outbox_root,
    partition_directory as partition_directory,
    lease_directory as lease_directory,
    entry_identity as entry_identity,
    entry_file_name as entry_file_name,
    list_entries as list_entries,
    cursor_path as cursor_path,
    read_cursor as read_cursor,
    decode_cursor as decode_cursor,
    retired_residue as retired_residue,
    record_source_ref as record_source_ref,
    runtime_root_digest as runtime_root_digest,
    outbox_summary as outbox_summary,
    _ENTRY_FILE,
)

TodoPartitionProjector = Callable[[str], dict[str, Any]]
"""Active-state Markdown text -> ``{"handoff_mode": str, "todos": [compact...]}``."""


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def drain_lock_target(runtime_root: Path, goal_id: str) -> Path:
    return shadow_maintenance_lock_target(runtime_root, goal_id)


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


def _require_binding_goal_ref(
    binding: Mapping[str, Any],
    *,
    goal_id: str,
    goal_ref: Mapping[str, str] | None,
) -> None:
    bound = binding.get("goal_ref")
    if goal_ref is None:
        if bound is not None:
            raise OutboxError(
                "goal_instance_id_missing",
                "exact shadow operation requires the source GoalRef",
            )
        return
    if bound is None:
        raise OutboxError(
            "legacy_goal_binding",
            "exact shadow operation cannot infer a legacy binding",
        )
    if bound != dict(goal_ref) or bound.get("goal_id") != goal_id:
        raise OutboxError(
            "stale_goal_instance",
            "shadow operation belongs to another Goal instance",
        )


def _proved_sequence(
    runtime_root: Path,
    goal_id: str,
    partition: str,
    goal_ref: Mapping[str, str] | None,
) -> int:
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
    _require_binding_goal_ref(
        binding["binding"], goal_id=goal_id, goal_ref=goal_ref
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
    directory: Path,
    *,
    runtime_root: Path | None = None,
    goal_id: str | None = None,
    goal_ref: Mapping[str, str] | None = None,
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
        highest = max(
            highest,
            _proved_sequence(
                runtime_root,
                goal_id,
                directory.name,
                goal_ref,
            ),
        )
    if highest >= MAX_OUTBOX_SEQUENCE:
        raise OutboxError("outbox_sequence_exhausted", "outbox sequence is exhausted")
    return highest + 1


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
        registry_path: Path | None,
        runtime_root: Path | None,
        goal_id: str,
        state_path: Path | None,
        write_class: str,
        original_text: str,
        projector: TodoPartitionProjector | None,
        goal_ref: Mapping[str, str] | None = None,
    ) -> None:
        self._enabled = enabled
        self._registry_path = registry_path
        self._runtime_root = runtime_root
        self._goal_id = goal_id
        self._state_path = state_path
        self._write_class = write_class
        self._original_digest = text_digest(original_text)
        self._original_text = original_text
        self._projector = projector
        self._goal_ref = dict(goal_ref) if goal_ref is not None else None
        self._directory = (
            partition_directory(runtime_root, goal_id, TODO_PARTITION)
            if enabled and runtime_root is not None
            else None
        )
        self._seq: int | None = None
        self._entry_id: str | None = None
        self._lineage_id: str | None = None
        self.outcome = CaptureOutcome(partition=TODO_PARTITION if enabled else None)

    @classmethod
    def begin(
        cls,
        *,
        enabled: bool,
        registry_path: Path | None = None,
        runtime_root: Path | None,
        goal_id: str,
        state_path: Path | None,
        write_class: str,
        original_text: str,
        projector: TodoPartitionProjector | None,
        goal_ref: Mapping[str, str] | None = None,
    ) -> TodoPartitionCapture:
        """``projector`` maps active-state text to the todos partition projection.

        It is injected so this module stays free of the Markdown parser; the
        adapter supplies the production projector.
        """

        return cls(
            enabled=enabled,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            state_path=state_path,
            write_class=write_class,
            original_text=original_text,
            projector=projector,
            goal_ref=goal_ref,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and self._directory is not None

    @property
    def goal_ref(self) -> dict[str, str] | None:
        return dict(self._goal_ref) if self._goal_ref is not None else None

    @property
    def registry_path(self) -> Path | None:
        return self._registry_path

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

    def prepare(self, new_text: str) -> None:
        """Prepare capture for the exact Markdown bytes about to be written."""

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
        try:
            _require_binding_goal_ref(
                binding,
                goal_id=self._goal_id,
                goal_ref=self._goal_ref,
            )
        except OutboxError as error:
            self._fail(
                error.reason_code,
                error,
            )
            return
        self._lineage_id = str(binding["capture_lineage_id"])
        source_root_digest = str(binding["source_root_digest"])
        try:
            projection = self._project(new_text)
            digest = partition_digest(projection)
            previous_digest = partition_digest(self._project(self._original_text))
            if digest == previous_digest:
                self.outcome.skipped_reason = "partition_unchanged"
                return
            source_ref = text_digest(new_text)
            bytes_digest = source_ref
            source_kind = SOURCE_MARKDOWN
            seq = next_seq(
                self._directory,
                runtime_root=self._runtime_root,
                goal_id=self._goal_id,
                goal_ref=self._goal_ref,
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
                    operation_id=None,
                ),
                source={
                    "kind": source_kind,
                    "previous_bytes_digest": self._original_digest,
                    "previous_partition_digest": previous_digest,
                    "bytes_digest": bytes_digest,
                    "lease": None,
                    "event_id": None,
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
            self._fail("outbox_prepare_failed", error)
            return
        self._seq = seq
        self._entry_id = entry_id
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
    "entry_file_name",
    "entry_identity",
    "lease_directory",
    "list_entries",
    "next_seq",
    "outbox_root",
    "outbox_summary",
    "partition_directory",
    "read_cursor",
    "record_source_ref",
    "retired_residue",
    "runtime_root_digest",
    "utc_now_text",
]
