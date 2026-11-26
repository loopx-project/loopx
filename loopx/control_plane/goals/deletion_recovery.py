"""Write-ahead recovery receipts and idempotent completion for Goal deletion."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Protocol, TypedDict

from ..projects.registry_codec import (
    ProjectRegistryTransaction,
    decode_registry_snapshot,
)
from ...file_lock import exclusive_cross_runtime_file_lock
from ...history import load_registry
from ...registry import atomic_write_json, read_json
from ..effect_runtime import CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS
from .activation_service import (
    _goal_activation_source_identity,
    _same_path,
    _source_status,
)
from .deletion_basis import (
    GOAL_DELETION_RECOVERY_SCHEMA_VERSION,
    GOAL_DELETION_SCHEMA_VERSION,
    _MAX_RECOVERY_RECEIPT_BYTES,
    DeletionReadback,
    DeletionResult,
    NowIsoFn,
    Payload,
    PayloadView,
    _as_payload,
    _canonical_writer_guard_path,
    _fsync_parent,
    _goal_payload_or_none,
    _normalize_source_basis_with_pattern,
    _recovery_receipt_path,
    _remove_goal,
    _source_basis,
)


class RecoverySnapshot(TypedDict):
    """Serialized snapshot pointer stored inside a Goal deletion recovery record."""

    registry_path: str
    backup_path: str
    preimage_sha256: str


class RecoveryRecord(TypedDict):
    """Typed write-ahead recovery receipt for idempotent Goal deletion replay."""

    schema_version: str
    goal_id: str
    state_fingerprint: str
    source_basis: dict[str, str]
    source_registry: str
    declared_source_registry: str
    target_registry: str
    source_available: bool
    same_registry: bool
    snapshots: list[RecoverySnapshot]


class AtomicWriteJsonFn(Protocol):
    """Callable protocol matching ``loopx.registry.atomic_write_json``."""

    def __call__(
        self,
        path: Path,
        payload: Payload,
        *,
        preserve_mode: bool = False,
    ) -> None: ...


class LockedRegistryWriteFn(Protocol):
    """Callable protocol matching locked write/restore operations."""

    def __call__(
        self,
        *,
        path: Path,
        updated: Payload,
        source_registry: Path,
        source_transaction: ProjectRegistryTransaction | None,
    ) -> None: ...


class LockedRegistryRestoreFn(Protocol):
    """Callable protocol matching locked rollback restoration."""

    def __call__(
        self,
        *,
        path: Path,
        current: Payload,
        source_registry: Path,
        source_transaction: ProjectRegistryTransaction | None,
    ) -> None: ...


class RegistryTransactionFn(Protocol):
    """Callable protocol matching ``project_registry_transaction``."""

    def __call__(
        self,
        path: Path,
        *,
        operation: str,
    ) -> AbstractContextManager[ProjectRegistryTransaction]: ...


LoadRegistryFn = Callable[[Path], Payload]


@dataclass(frozen=True, slots=True)
class RecoveryRuntimeHooks:
    """Runtime dependencies injected from ``deletion_service`` for monkeypatch parity."""

    load_registry_fn: LoadRegistryFn
    atomic_write_json_fn: AtomicWriteJsonFn
    project_registry_transaction_fn: RegistryTransactionFn
    write_locked_registry_fn: LockedRegistryWriteFn
    restore_locked_registry_fn: LockedRegistryRestoreFn
    now_local_iso_fn: NowIsoFn
    sha256_pattern: re.Pattern[str]


def _default_load_registry(path: Path) -> Payload:
    return _as_payload(load_registry(path))


def _build_recovery_record(
    *,
    goal_id: str,
    state_fingerprint: str,
    source_basis: Mapping[str, str],
    source_registry: Path,
    declared_source_registry: Path,
    target_registry: Path,
    source_available: bool,
    same_registry: bool,
    paths: list[Path],
    backup_paths: Mapping[Path, Path],
) -> RecoveryRecord:
    snapshots: list[RecoverySnapshot] = []
    for path in paths:
        backup = backup_paths[path]
        snapshots.append(
            {
                "registry_path": str(path.expanduser().resolve()),
                "backup_path": str(backup.expanduser().resolve()),
                "preimage_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            }
        )
    return {
        "schema_version": GOAL_DELETION_RECOVERY_SCHEMA_VERSION,
        "goal_id": goal_id,
        "state_fingerprint": state_fingerprint,
        "source_basis": dict(source_basis),
        "source_registry": str(source_registry.expanduser().resolve()),
        "declared_source_registry": str(
            declared_source_registry.expanduser().resolve()
        ),
        "target_registry": str(target_registry.expanduser().resolve()),
        "source_available": source_available,
        "same_registry": same_registry,
        "snapshots": snapshots,
    }


def _validated_recovery_path(value: object, *, field: str) -> Path:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise ValueError(f"Goal deletion recovery {field} must be absolute")
    return path.resolve()


def _validated_recovery_snapshots(
    snapshots_value: object,
    *,
    expected_paths: list[Path],
    sha256_pattern: re.Pattern[str],
) -> list[RecoverySnapshot]:
    if not isinstance(snapshots_value, list):
        raise ValueError("Goal deletion recovery snapshots are missing")
    snapshots: list[RecoverySnapshot] = []
    seen_paths: list[Path] = []
    for snapshot_value in snapshots_value:
        if not isinstance(snapshot_value, Mapping):
            raise ValueError("Goal deletion recovery snapshot is invalid")
        registry_path = _validated_recovery_path(
            snapshot_value.get("registry_path"),
            field="snapshot registry path",
        )
        backup_path = _validated_recovery_path(
            snapshot_value.get("backup_path"),
            field="snapshot backup path",
        )
        preimage_sha256 = str(snapshot_value.get("preimage_sha256") or "")
        if not sha256_pattern.fullmatch(preimage_sha256):
            raise ValueError("Goal deletion recovery preimage digest is invalid")
        if backup_path.parent != registry_path.parent or not (
            backup_path.name.startswith(f"{registry_path.name}.goal-delete-")
            and backup_path.name.endswith(".bak")
        ):
            raise ValueError("Goal deletion recovery backup path is invalid")
        if backup_path.is_symlink() or not backup_path.is_file():
            raise ValueError("Goal deletion recovery backup is unavailable")
        if any(_same_path(registry_path, seen) for seen in seen_paths):
            raise ValueError("Goal deletion recovery repeats a registry path")
        seen_paths.append(registry_path)
        snapshots.append(
            {
                "registry_path": str(registry_path),
                "backup_path": str(backup_path),
                "preimage_sha256": preimage_sha256,
            }
        )
    if len(seen_paths) != len(expected_paths) or any(
        not _same_path(actual, expected)
        for actual, expected in zip(seen_paths, expected_paths, strict=True)
    ):
        raise ValueError("Goal deletion recovery registry set is invalid")
    return snapshots


def _validated_recovery_record(
    value: PayloadView,
    *,
    receipt_path: Path,
    goal_id: str,
    sha256_pattern: re.Pattern[str],
) -> RecoveryRecord:
    if value.get("schema_version") != GOAL_DELETION_RECOVERY_SCHEMA_VERSION:
        raise ValueError("Goal deletion recovery schema is unsupported")
    if value.get("goal_id") != goal_id:
        raise ValueError("Goal deletion recovery goal identity does not match")
    state_fingerprint = str(value.get("state_fingerprint") or "")
    if not sha256_pattern.fullmatch(state_fingerprint):
        raise ValueError("Goal deletion recovery state fingerprint is invalid")
    source_basis_value = value.get("source_basis")
    if not isinstance(source_basis_value, Mapping):
        raise ValueError("Goal deletion recovery source basis is missing")
    source_basis = _normalize_source_basis_with_pattern(
        source_basis_value,
        sha256_pattern=sha256_pattern,
    )
    if source_basis is None:
        raise ValueError("Goal deletion recovery source basis is missing")
    source_registry = _validated_recovery_path(
        value.get("source_registry"),
        field="source registry",
    )
    declared_source_registry = _validated_recovery_path(
        value.get("declared_source_registry"),
        field="declared source registry",
    )
    target_registry = _validated_recovery_path(
        value.get("target_registry"),
        field="target registry",
    )
    source_available = value.get("source_available")
    same_registry = value.get("same_registry")
    if not isinstance(source_available, bool) or not isinstance(same_registry, bool):
        raise ValueError("Goal deletion recovery route flags are invalid")
    if same_registry != _same_path(source_registry, target_registry):
        raise ValueError("Goal deletion recovery same-registry flag is invalid")
    if source_available != _same_path(source_registry, declared_source_registry):
        raise ValueError("Goal deletion recovery source route is invalid")
    if not source_available and not same_registry:
        raise ValueError("Goal deletion recovery fallback route is invalid")
    expected_source_identity = _goal_activation_source_identity(
        declared_source_registry
    )
    if source_basis["source_identity"] != expected_source_identity:
        raise ValueError("Goal deletion recovery source identity does not match")
    expected_paths = [target_registry]
    if source_available and not same_registry:
        expected_paths.insert(0, source_registry)
    snapshots = _validated_recovery_snapshots(
        value.get("snapshots"),
        expected_paths=expected_paths,
        sha256_pattern=sha256_pattern,
    )
    expected_receipts = [
        _recovery_receipt_path(path, goal_id) for path in expected_paths
    ]
    if not any(_same_path(receipt_path, expected) for expected in expected_receipts):
        raise ValueError("Goal deletion recovery receipt path is invalid")
    return {
        "schema_version": GOAL_DELETION_RECOVERY_SCHEMA_VERSION,
        "goal_id": goal_id,
        "state_fingerprint": state_fingerprint,
        "source_basis": source_basis,
        "source_registry": str(source_registry),
        "declared_source_registry": str(declared_source_registry),
        "target_registry": str(target_registry),
        "source_available": source_available,
        "same_registry": same_registry,
        "snapshots": snapshots,
    }


def _load_recovery_record(
    path: Path,
    goal_id: str,
    *,
    sha256_pattern: re.Pattern[str],
) -> RecoveryRecord:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Goal deletion recovery receipt is unavailable")
    if path.stat().st_size > _MAX_RECOVERY_RECEIPT_BYTES:
        raise ValueError("Goal deletion recovery receipt exceeds its size limit")
    return _validated_recovery_record(
        _as_payload(read_json(path)),
        receipt_path=path,
        goal_id=goal_id,
        sha256_pattern=sha256_pattern,
    )


def _matching_recovery_record(
    *,
    requested_registry: Path,
    goal_id: str,
    expected_state_fingerprint: str | None,
    expected_source_basis: Mapping[str, str] | None,
    sha256_pattern: re.Pattern[str],
) -> RecoveryRecord | None:
    if expected_state_fingerprint is None or expected_source_basis is None:
        return None
    receipt_path = _recovery_receipt_path(requested_registry, goal_id)
    if not os.path.lexists(receipt_path):
        return None
    record = _load_recovery_record(
        receipt_path,
        goal_id,
        sha256_pattern=sha256_pattern,
    )
    if record["state_fingerprint"] != expected_state_fingerprint or record[
        "source_basis"
    ] != dict(expected_source_basis):
        return None
    return record


def _write_recovery_receipts(
    record: RecoveryRecord,
    *,
    sha256_pattern: re.Pattern[str],
    atomic_write_json_fn: AtomicWriteJsonFn = atomic_write_json,
) -> None:
    goal_id = record["goal_id"]
    for snapshot in record["snapshots"]:
        receipt_path = _recovery_receipt_path(
            Path(snapshot["registry_path"]),
            goal_id,
        )
        if receipt_path.is_symlink() or (
            receipt_path.exists() and not receipt_path.is_file()
        ):
            raise ValueError("Goal deletion recovery receipt path is unsafe")
        atomic_write_json_fn(receipt_path, dict(record), preserve_mode=True)
        _fsync_parent(receipt_path)
        if (
            _load_recovery_record(
                receipt_path,
                goal_id,
                sha256_pattern=sha256_pattern,
            )
            != record
        ):
            raise ValueError("Goal deletion recovery receipt readback did not verify")


def _recovery_images(
    record: RecoveryRecord,
) -> dict[Path, tuple[Payload, str]]:
    images: dict[Path, tuple[Payload, str]] = {}
    for snapshot in record["snapshots"]:
        registry_path = Path(snapshot["registry_path"])
        backup_path = Path(snapshot["backup_path"])
        raw_preimage = backup_path.read_bytes()
        preimage_sha256 = hashlib.sha256(raw_preimage).hexdigest()
        if preimage_sha256 != snapshot["preimage_sha256"]:
            raise ValueError("Goal deletion recovery backup digest does not match")
        preimage = _as_payload(decode_registry_snapshot(registry_path, raw_preimage))
        if _goal_payload_or_none(preimage, record["goal_id"]) is None:
            raise ValueError("Goal deletion recovery backup is missing its Goal")
        images[registry_path] = (preimage, preimage_sha256)
    return images


def _recovery_states(
    record: RecoveryRecord,
    *,
    load_registry_fn: LoadRegistryFn = _default_load_registry,
) -> tuple[dict[Path, str], dict[Path, tuple[Payload, str]]]:
    images = _recovery_images(record)
    states: dict[Path, str] = {}
    for path, (preimage, preimage_sha256) in images.items():
        try:
            current_bytes = path.read_bytes()
            current = load_registry_fn(path)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                "Goal deletion recovery conflicts with current registry state"
            ) from exc
        if hashlib.sha256(current_bytes).hexdigest() == preimage_sha256:
            states[path] = "preimage"
            continue
        postimage, changed = _remove_goal(
            preimage,
            record["goal_id"],
            updated_at=current.get("updated_at"),
        )
        if not changed or current != postimage:
            raise ValueError(
                "Goal deletion recovery conflicts with current registry state"
            )
        states[path] = "postimage"
    return states, images


def _recovery_readback(
    record: RecoveryRecord,
    states: Mapping[Path, str],
) -> DeletionReadback:
    source_registry = Path(record["source_registry"])
    declared_source_registry = Path(record["declared_source_registry"])
    target_registry = Path(record["target_registry"])
    source_available = record["source_available"]
    source_missing = (
        states[source_registry] == "postimage"
        if source_available
        else _source_basis(
            {
                "declared_source_registry": declared_source_registry,
                "source_status": _source_status(
                    declared_source_registry,
                    goal_id=record["goal_id"],
                ).value,
                "route_mode": record["source_basis"]["route_mode"],
            }
        )
        == record["source_basis"]
    )
    global_missing = states[target_registry] == "postimage"
    return {
        "source_missing": source_missing,
        "global_missing": global_missing,
        "verified": source_missing and global_missing,
    }


def _recovery_result(
    record: RecoveryRecord,
    states: Mapping[Path, str],
    *,
    execute: bool,
    written: bool,
) -> DeletionResult:
    readback = _recovery_readback(record, states)
    if not record["source_available"] and not readback["source_missing"]:
        raise ValueError("Goal deletion recovery conflicts with current registry state")
    source_registry = Path(record["source_registry"])
    target_registry = Path(record["target_registry"])
    return DeletionResult(
        {
            "ok": True,
            "schema_version": GOAL_DELETION_SCHEMA_VERSION,
            "dry_run": not execute,
            "execute": execute,
            "goal_id": record["goal_id"],
            "source_registry": str(source_registry),
            "target_global_registry": str(target_registry),
            "source_registry_present": bool(
                record["source_available"] and states[source_registry] == "preimage"
            ),
            "global_registry_present": states[target_registry] == "preimage",
            "source_basis": dict(record["source_basis"]),
            "authority_route_mode": record["source_basis"]["route_mode"],
            "expected_state_fingerprint": record["state_fingerprint"],
            "observed_state_fingerprint": record["state_fingerprint"],
            "written": written,
            "partial_write": any(state == "postimage" for state in states.values())
            and not readback["verified"],
            "backup_paths": [
                snapshot["backup_path"] for snapshot in record["snapshots"]
            ],
            "readback": readback,
            "recovery_pending": not execute,
            "recovered": execute and readback["verified"],
            "replayed": execute and readback["verified"] and not written,
        }
    )


def _as_recovery_record(
    record: RecoveryRecord | PayloadView,
    *,
    sha256_pattern: re.Pattern[str],
) -> RecoveryRecord:
    raw_goal_id = record.get("goal_id")
    goal_id = raw_goal_id if isinstance(raw_goal_id, str) else ""
    if not goal_id.strip():
        raise ValueError("Goal deletion recovery goal identity does not match")
    target_registry = _validated_recovery_path(
        record.get("target_registry"),
        field="target registry",
    )
    return _validated_recovery_record(
        record,
        receipt_path=_recovery_receipt_path(target_registry, goal_id),
        goal_id=goal_id,
        sha256_pattern=sha256_pattern,
    )


def _recovery_receipts_match(
    record: RecoveryRecord,
    *,
    sha256_pattern: re.Pattern[str],
) -> bool:
    goal_id = record["goal_id"]
    for snapshot in record["snapshots"]:
        receipt_path = _recovery_receipt_path(
            Path(snapshot["registry_path"]),
            goal_id,
        )
        if not os.path.lexists(receipt_path):
            return False
        current = _load_recovery_record(
            receipt_path,
            goal_id,
            sha256_pattern=sha256_pattern,
        )
        if current != record:
            return False
    return True


def _execute_recovery(
    raw_record: RecoveryRecord | PayloadView,
    *,
    hooks: RecoveryRuntimeHooks,
) -> DeletionResult:
    """Converge every recorded preimage to deletion without overwriting drift."""
    record = _as_recovery_record(raw_record, sha256_pattern=hooks.sha256_pattern)
    source_registry = Path(record["source_registry"])
    declared_source_registry = Path(record["declared_source_registry"])
    target_registry = Path(record["target_registry"])
    source_available = record["source_available"]
    same_registry = record["same_registry"]
    locked_source_registry = (
        source_registry if source_available else declared_source_registry
    )
    with ExitStack() as stack:
        stack.enter_context(
            exclusive_cross_runtime_file_lock(
                _canonical_writer_guard_path(target_registry, record["goal_id"]),
                operation="recover_delete_stopped_goal_canonical",
                timeout_seconds=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
            )
        )
        source_transaction: ProjectRegistryTransaction | None = None
        if not _same_path(locked_source_registry, target_registry):
            if source_available and not same_registry:
                source_transaction = stack.enter_context(
                    hooks.project_registry_transaction_fn(
                        locked_source_registry,
                        operation="recover_delete_stopped_goal",
                    )
                )
            else:
                stack.enter_context(
                    exclusive_cross_runtime_file_lock(
                        locked_source_registry,
                        operation="recover_delete_stopped_goal",
                    )
                )
        stack.enter_context(
            exclusive_cross_runtime_file_lock(
                target_registry,
                operation="recover_delete_stopped_goal",
            )
        )
        states, images = _recovery_states(
            record,
            load_registry_fn=hooks.load_registry_fn,
        )
        if not _recovery_receipts_match(
            record,
            sha256_pattern=hooks.sha256_pattern,
        ):
            if any(state == "postimage" for state in states.values()):
                raise ValueError(
                    "Goal deletion recovery conflicts with current registry state"
                )
            _write_recovery_receipts(
                record,
                sha256_pattern=hooks.sha256_pattern,
                atomic_write_json_fn=hooks.atomic_write_json_fn,
            )

        written_paths: list[Path] = []
        try:
            for snapshot in record["snapshots"]:
                path = Path(snapshot["registry_path"])
                if states[path] == "postimage":
                    continue
                preimage, _digest = images[path]
                postimage, changed = _remove_goal(
                    preimage,
                    record["goal_id"],
                    now_local_iso_fn=hooks.now_local_iso_fn,
                )
                if not changed:
                    raise ValueError(
                        "Goal deletion recovery backup is missing its Goal"
                    )
                hooks.write_locked_registry_fn(
                    path=path,
                    updated=postimage,
                    source_registry=source_registry,
                    source_transaction=source_transaction,
                )
                written_paths.append(path)
            final_states, _final_images = _recovery_states(
                record,
                load_registry_fn=hooks.load_registry_fn,
            )
            result = _recovery_result(
                record,
                final_states,
                execute=True,
                written=bool(written_paths),
            )
            if not result["readback"]["verified"]:
                raise ValueError("Goal deletion recovery readback did not verify")
            return result
        except Exception:
            for path in reversed(written_paths):
                preimage, _digest = images[path]
                hooks.restore_locked_registry_fn(
                    path=path,
                    current=preimage,
                    source_registry=source_registry,
                    source_transaction=source_transaction,
                )
            raise
