"""Canonical owner-confirmed deletion for stopped Goals."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import Any
import uuid

from ..projects.registry_codec import (
    ProjectRegistryTransaction,
    decode_registry_snapshot,
    project_registry_transaction,
)
from ...configuration_transaction import configuration_payload_revision
from ...file_lock import (
    EFFECT_MUTATION_LOCK_SUFFIX,
    exclusive_cross_runtime_file_lock,
    lock_holder_paths,
    lock_incident_path,
)
from ...history import load_registry
from ...registry import atomic_write_json, read_json
from ...registry_writability import probe_registry_write_path
from ..coordination.shadow_management import shadow_maintenance_lock_target
from ..effect_runtime import CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS
from ..runtime.time import now_local_iso
from .activation import GoalActivationState, goal_activation_state
from .activation_service import (
    GoalActivationAuthorityRouteMode,
    _goal_activation_source_identity,
    _goal_or_none,
    _same_path,
    _source_and_target,
    _source_status,
)
from ..content_digest import BARE_SHA256_PATTERN


GOAL_DELETION_SCHEMA_VERSION = "loopx_goal_deletion_v1"
GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION = "loopx_goal_deletion_source_basis_v1"
GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION = (
    "loopx_goal_deletion_state_fingerprint_v1"
)
GOAL_DELETION_RECOVERY_SCHEMA_VERSION = "loopx_goal_deletion_recovery_v1"
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_MAX_RECOVERY_RECEIPT_BYTES = 64 * 1024


def _require_opaque_id(value: str, *, field: str) -> str:
    """Reject any value that is not a compact opaque id; safe for path segments."""

    token = str(value or "").strip()
    if not _OPAQUE_ID.fullmatch(token):
        raise ValueError(f"{field} must be a compact opaque id")
    return token


def _registry_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry_identity(path: Path) -> str:
    return configuration_payload_revision(
        {"registry_path": str(path.expanduser().resolve())}
    ).removeprefix("sha256:")


def _source_basis(route: dict[str, Any]) -> dict[str, str]:
    source_registry = Path(route["declared_source_registry"])
    try:
        source_content = source_registry.read_bytes()
    except OSError:
        source_content = (
            f"unavailable:{route['source_status']}:{_registry_identity(source_registry)}"
        ).encode()
    return {
        "schema_version": GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION,
        "source_identity": _goal_activation_source_identity(source_registry),
        "source_content_sha256": hashlib.sha256(source_content).hexdigest(),
        "route_mode": str(route["route_mode"]),
    }


def _state_fingerprint(
    *,
    goal_id: str,
    source_basis: Mapping[str, str],
    target_registry: Path,
) -> str:
    return configuration_payload_revision(
        {
            "schema_version": GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION,
            "goal_id": goal_id,
            "source_basis": dict(source_basis),
            "target_identity": _registry_identity(target_registry),
            "target_content_sha256": _registry_fingerprint(target_registry),
        }
    ).removeprefix("sha256:")


def _normalize_source_basis(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    schema_version = str(value.get("schema_version") or "")
    source_identity = str(value.get("source_identity") or "")
    source_content_sha256 = str(value.get("source_content_sha256") or "")
    route_mode = str(value.get("route_mode") or "")
    if schema_version != GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION:
        raise ValueError("expected source basis has an unsupported schema version")
    if not BARE_SHA256_PATTERN.fullmatch(source_identity):
        raise ValueError("expected source identity must be a SHA-256 digest")
    if not BARE_SHA256_PATTERN.fullmatch(source_content_sha256):
        raise ValueError("expected source content digest must be a SHA-256 digest")
    if route_mode not in {mode.value for mode in GoalActivationAuthorityRouteMode}:
        raise ValueError("expected source route mode is unsupported")
    return {
        "schema_version": schema_version,
        "source_identity": source_identity,
        "source_content_sha256": source_content_sha256,
        "route_mode": route_mode,
    }


def _mark_stale(
    payload: dict[str, Any],
    *,
    current_state_fingerprint: str,
    current_source_basis: Mapping[str, str],
) -> None:
    payload.update(
        {
            "ok": False,
            "stale": True,
            "error_kind": "goal_registry_changed",
            "error": (
                "Goal source or registry route changed after preview; "
                "regenerate the deletion preview"
            ),
            "current_state_fingerprint": current_state_fingerprint,
            "observed_state_fingerprint": current_state_fingerprint,
            "source_basis": dict(current_source_basis),
        }
    )


def _missing_state_fingerprint(*, goal_id: str, registry_path: Path) -> str:
    target_content_sha256 = (
        _registry_fingerprint(registry_path) if registry_path.is_file() else None
    )
    return configuration_payload_revision(
        {
            "schema_version": GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION,
            "goal_id": goal_id,
            "deletion_state": "missing",
            "target_identity": _registry_identity(registry_path),
            "target_content_sha256": target_content_sha256,
        }
    ).removeprefix("sha256:")


def _missing_stale_payload(
    *,
    goal_id: str,
    registry_path: Path,
    execute: bool,
    expected_state_fingerprint: str | None,
) -> dict[str, Any]:
    current_fingerprint = _missing_state_fingerprint(
        goal_id=goal_id,
        registry_path=registry_path,
    )
    return {
        "ok": False,
        "schema_version": GOAL_DELETION_SCHEMA_VERSION,
        "dry_run": not execute,
        "execute": execute,
        "goal_id": goal_id,
        "target_global_registry": str(registry_path),
        "expected_state_fingerprint": expected_state_fingerprint,
        "observed_state_fingerprint": current_fingerprint,
        "current_state_fingerprint": current_fingerprint,
        "written": False,
        "partial_write": False,
        "backup_paths": [],
        "recovery_pending": False,
        "recovered": False,
        "replayed": False,
        "readback": {
            "source_missing": True,
            "global_missing": True,
            "verified": False,
        },
        "stale": True,
        "error_kind": "goal_registry_changed",
        "error": "Goal disappeared after preview; regenerate the deletion preview",
    }


def _backup_path(path: Path, timestamp: str, nonce: str) -> Path:
    compact_timestamp = timestamp.replace(":", "").replace("-", "")
    return path.with_name(
        f"{path.name}.goal-delete-{compact_timestamp}-{nonce}.bak"
    )


def _create_backup(path: Path, *, timestamp: str) -> Path:
    """Create an independent snapshot without ever overwriting an old backup."""

    source_path = path.expanduser().resolve(strict=True)
    if not source_path.is_file():
        raise ValueError(f"registry path is not a regular file: {source_path}")
    for _ in range(8):
        backup = _backup_path(source_path, timestamp, uuid.uuid4().hex)
        try:
            descriptor = os.open(
                backup,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            continue
        try:
            with source_path.open("rb") as source, os.fdopen(descriptor, "wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            shutil.copystat(source_path, backup)
        except Exception:
            backup.unlink(missing_ok=True)
            raise
        _fsync_parent(backup)
        return backup
    raise FileExistsError(f"could not allocate a unique Goal deletion backup for {source_path}")


def _remove_goal(
    payload: dict[str, Any],
    goal_id: str,
    *,
    updated_at: str | None = None,
) -> tuple[dict[str, Any], bool]:
    goals = payload.get("goals")
    if not isinstance(goals, list):
        raise ValueError("registry goals must be a list")
    retained = [
        goal
        for goal in goals
        if not (isinstance(goal, dict) and str(goal.get("id") or "") == goal_id)
    ]
    changed = len(retained) != len(goals)
    updated = dict(payload)
    updated["goals"] = retained
    if changed:
        updated["updated_at"] = now_local_iso() if updated_at is None else updated_at
    return updated, changed


def _recovery_receipt_path(registry_path: Path, goal_id: str) -> Path:
    registry = registry_path.expanduser().resolve()
    goal_digest = hashlib.sha256(goal_id.encode("utf-8")).hexdigest()
    return registry.with_name(
        f".{registry.name}.goal-delete-{goal_digest}.recovery.json"
    )


def _canonical_writer_guard_path(target_registry: Path, goal_id: str) -> Path:
    runtime_root = target_registry.expanduser().resolve().parent
    return shadow_maintenance_lock_target(runtime_root, goal_id)


def _fsync_parent(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
) -> dict[str, Any]:
    snapshots = []
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


def _validated_recovery_path(value: Any, *, field: str) -> Path:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise ValueError(f"Goal deletion recovery {field} must be absolute")
    return path.resolve()


def _validated_recovery_record(
    value: Mapping[str, Any],
    *,
    receipt_path: Path,
    goal_id: str,
) -> dict[str, Any]:
    if value.get("schema_version") != GOAL_DELETION_RECOVERY_SCHEMA_VERSION:
        raise ValueError("Goal deletion recovery schema is unsupported")
    if value.get("goal_id") != goal_id:
        raise ValueError("Goal deletion recovery goal identity does not match")
    state_fingerprint = str(value.get("state_fingerprint") or "")
    if not BARE_SHA256_PATTERN.fullmatch(state_fingerprint):
        raise ValueError("Goal deletion recovery state fingerprint is invalid")
    source_basis_value = value.get("source_basis")
    if not isinstance(source_basis_value, Mapping):
        raise ValueError("Goal deletion recovery source basis is missing")
    source_basis = _normalize_source_basis(source_basis_value)
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
    snapshots_value = value.get("snapshots")
    if not isinstance(snapshots_value, list):
        raise ValueError("Goal deletion recovery snapshots are missing")
    expected_paths = [target_registry]
    if source_available and not same_registry:
        expected_paths.insert(0, source_registry)
    snapshots: list[dict[str, str]] = []
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
        if not BARE_SHA256_PATTERN.fullmatch(preimage_sha256):
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

    normalized: dict[str, Any] = {
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
    expected_receipts = [
        _recovery_receipt_path(path, goal_id) for path in expected_paths
    ]
    if not any(_same_path(receipt_path, expected) for expected in expected_receipts):
        raise ValueError("Goal deletion recovery receipt path is invalid")
    return normalized


def _load_recovery_record(path: Path, goal_id: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Goal deletion recovery receipt is unavailable")
    if path.stat().st_size > _MAX_RECOVERY_RECEIPT_BYTES:
        raise ValueError("Goal deletion recovery receipt exceeds its size limit")
    return _validated_recovery_record(
        read_json(path),
        receipt_path=path,
        goal_id=goal_id,
    )


def _matching_recovery_record(
    *,
    requested_registry: Path,
    goal_id: str,
    expected_state_fingerprint: str | None,
    expected_source_basis: Mapping[str, str] | None,
) -> dict[str, Any] | None:
    if expected_state_fingerprint is None or expected_source_basis is None:
        return None
    receipt_path = _recovery_receipt_path(requested_registry, goal_id)
    if not os.path.lexists(receipt_path):
        return None
    record = _load_recovery_record(receipt_path, goal_id)
    if record["state_fingerprint"] != expected_state_fingerprint or record[
        "source_basis"
    ] != dict(expected_source_basis):
        return None
    return record


def _write_recovery_receipts(record: Mapping[str, Any]) -> None:
    goal_id = str(record["goal_id"])
    for snapshot in record["snapshots"]:
        receipt_path = _recovery_receipt_path(
            Path(snapshot["registry_path"]),
            goal_id,
        )
        if receipt_path.is_symlink() or (
            receipt_path.exists() and not receipt_path.is_file()
        ):
            raise ValueError("Goal deletion recovery receipt path is unsafe")
        atomic_write_json(receipt_path, dict(record), preserve_mode=True)
        _fsync_parent(receipt_path)
        if _load_recovery_record(receipt_path, goal_id) != record:
            raise ValueError("Goal deletion recovery receipt readback did not verify")


def _recovery_images(
    record: Mapping[str, Any],
) -> dict[Path, tuple[dict[str, Any], str]]:
    images: dict[Path, tuple[dict[str, Any], str]] = {}
    for snapshot in record["snapshots"]:
        registry_path = Path(snapshot["registry_path"])
        backup_path = Path(snapshot["backup_path"])
        raw_preimage = backup_path.read_bytes()
        preimage_sha256 = hashlib.sha256(raw_preimage).hexdigest()
        if preimage_sha256 != snapshot["preimage_sha256"]:
            raise ValueError("Goal deletion recovery backup digest does not match")
        preimage = decode_registry_snapshot(registry_path, raw_preimage)
        if _goal_or_none(preimage, str(record["goal_id"])) is None:
            raise ValueError("Goal deletion recovery backup is missing its Goal")
        images[registry_path] = (preimage, preimage_sha256)
    return images


def _recovery_states(
    record: Mapping[str, Any],
) -> tuple[
    dict[Path, str],
    dict[Path, tuple[dict[str, Any], str]],
]:
    images = _recovery_images(record)
    states: dict[Path, str] = {}
    for path, (preimage, preimage_sha256) in images.items():
        try:
            current_bytes = path.read_bytes()
            current = load_registry(path)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                "Goal deletion recovery conflicts with current registry state"
            ) from exc
        if hashlib.sha256(current_bytes).hexdigest() == preimage_sha256:
            states[path] = "preimage"
            continue
        postimage, changed = _remove_goal(
            preimage,
            str(record["goal_id"]),
            updated_at=current.get("updated_at"),
        )
        if not changed or current != postimage:
            raise ValueError(
                "Goal deletion recovery conflicts with current registry state"
            )
        states[path] = "postimage"
    return states, images


def _recovery_readback(
    record: Mapping[str, Any],
    states: Mapping[Path, str],
) -> dict[str, bool]:
    source_registry = Path(record["source_registry"])
    declared_source_registry = Path(record["declared_source_registry"])
    target_registry = Path(record["target_registry"])
    source_available = bool(record["source_available"])
    source_missing = (
        states[source_registry] == "postimage"
        if source_available
        else _source_basis(
            {
                "declared_source_registry": declared_source_registry,
                "source_status": _source_status(
                    declared_source_registry,
                    goal_id=str(record["goal_id"]),
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
    record: Mapping[str, Any],
    states: Mapping[Path, str],
    *,
    execute: bool,
    written: bool,
) -> dict[str, Any]:
    readback = _recovery_readback(record, states)
    if not record["source_available"] and not readback["source_missing"]:
        raise ValueError("Goal deletion recovery conflicts with current registry state")
    source_registry = Path(record["source_registry"])
    target_registry = Path(record["target_registry"])
    return {
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
            str(snapshot["backup_path"]) for snapshot in record["snapshots"]
        ],
        "readback": readback,
        "recovery_pending": not execute,
        "recovered": execute and readback["verified"],
        "replayed": execute and readback["verified"] and not written,
    }


def _recovery_receipts_match(record: Mapping[str, Any]) -> bool:
    goal_id = str(record["goal_id"])
    for snapshot in record["snapshots"]:
        receipt_path = _recovery_receipt_path(
            Path(snapshot["registry_path"]),
            goal_id,
        )
        if not os.path.lexists(receipt_path):
            return False
        current = _load_recovery_record(receipt_path, goal_id)
        if current != record:
            return False
    return True


def _execute_recovery(record: Mapping[str, Any]) -> dict[str, Any]:
    """Converge every recorded preimage to deletion without overwriting drift."""

    source_registry = Path(record["source_registry"])
    declared_source_registry = Path(record["declared_source_registry"])
    target_registry = Path(record["target_registry"])
    source_available = bool(record["source_available"])
    same_registry = bool(record["same_registry"])
    locked_source_registry = (
        source_registry if source_available else declared_source_registry
    )
    with ExitStack() as stack:
        stack.enter_context(
            exclusive_cross_runtime_file_lock(
                _canonical_writer_guard_path(target_registry, str(record["goal_id"])),
                operation="recover_delete_stopped_goal_canonical",
                timeout_seconds=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
            )
        )
        source_transaction = None
        if not _same_path(locked_source_registry, target_registry):
            if source_available and not same_registry:
                source_transaction = stack.enter_context(
                    project_registry_transaction(
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
        states, images = _recovery_states(record)
        if not _recovery_receipts_match(record):
            if any(state == "postimage" for state in states.values()):
                raise ValueError(
                    "Goal deletion recovery conflicts with current registry state"
                )
            _write_recovery_receipts(record)

        written_paths: list[Path] = []
        try:
            for snapshot in record["snapshots"]:
                path = Path(snapshot["registry_path"])
                if states[path] == "postimage":
                    continue
                preimage, _digest = images[path]
                postimage, changed = _remove_goal(
                    preimage,
                    str(record["goal_id"]),
                )
                if not changed:
                    raise ValueError(
                        "Goal deletion recovery backup is missing its Goal"
                    )
                _write_locked_registry(
                    path=path,
                    updated=postimage,
                    source_registry=source_registry,
                    source_transaction=source_transaction,
                )
                written_paths.append(path)
            final_states, _final_images = _recovery_states(record)
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
                _restore_locked_registry(
                    path=path,
                    current=preimage,
                    source_registry=source_registry,
                    source_transaction=source_transaction,
                )
            raise


def _resolve_route(
    goal_id: str,
    registry_path: Path,
    *,
    require_stopped: bool = True,
) -> dict[str, Any]:
    route = _source_and_target(
        registry_path=registry_path,
        goal_id=goal_id,
        target_state=GoalActivationState.STOPPED,
        runtime_root_override=None,
    )
    source_available = route.mode is not GoalActivationAuthorityRouteMode.ORPHANED_GLOBAL_STOP_FALLBACK
    source_payload = load_registry(route.source_registry) if source_available else None
    target_payload = read_json(route.target_registry)
    source_goal = _goal_or_none(source_payload, goal_id) if source_payload else None
    target_goal = _goal_or_none(target_payload, goal_id)
    goal = source_goal or target_goal
    if goal is None:
        raise ValueError(f"goal id not found in registry: {goal_id}")
    if require_stopped and goal_activation_state(goal) is not GoalActivationState.STOPPED:
        raise ValueError("stop the Goal before deleting it")
    if target_goal is None:
        raise ValueError("global registry does not contain the Goal projection")
    declared_source_registry = route.source_registry
    if (
        route.mode is GoalActivationAuthorityRouteMode.ORPHANED_GLOBAL_STOP_FALLBACK
    ):
        source_ref = str(target_goal.get("source_registry") or "").strip()
        if source_ref:
            declared_source_registry = Path(source_ref).expanduser().resolve()
    return {
        "source_registry": route.source_registry,
        "declared_source_registry": declared_source_registry,
        "target_registry": route.target_registry,
        "source_available": source_available,
        "source_status": route.source_status.value,
        "source_goal": source_goal,
        "target_goal": target_goal,
        "same_registry": _same_path(route.source_registry, route.target_registry),
        "route_mode": route.mode.value,
    }


def _check_writability(paths: list[Path]) -> dict[str, Any] | None:
    writability = [
        probe_registry_write_path(path, create_parent=False) for path in paths
    ]
    return next((item for item in writability if not item.get("ok")), None)


def _orphan_source_lock_error(path: Path) -> str | None:
    parent = path.parent
    if parent.exists() and (not parent.is_dir() or parent.is_symlink()):
        return "Goal source registry parent is unavailable for safe locking"
    kernel_lock = path.with_name(f"{path.name}.lock")
    lock_artifacts = {
        kernel_lock,
        *lock_holder_paths(path),
        lock_incident_path(path),
        Path(f"{path}{EFFECT_MUTATION_LOCK_SUFFIX}"),
    }
    if any(
        artifact.is_symlink()
        or (artifact.exists() and not artifact.is_file())
        for artifact in lock_artifacts
    ):
        return "Goal source registry lock path is unsafe"
    return None


def _route_matches_locked_paths(
    route: Mapping[str, Any],
    *,
    source_registry: Path,
    declared_source_registry: Path,
    target_registry: Path,
    source_available: bool,
    same_registry: bool,
) -> bool:
    return (
        _same_path(Path(route["source_registry"]), source_registry)
        and _same_path(
            Path(route["declared_source_registry"]),
            declared_source_registry,
        )
        and _same_path(Path(route["target_registry"]), target_registry)
        and bool(route["source_available"]) is source_available
        and bool(route["same_registry"]) is same_registry
    )


def _validated_locked_route_snapshot(
    *,
    requested_registry: Path,
    source_registry: Path,
    declared_source_registry: Path,
    target_registry: Path,
    source_available: bool,
    same_registry: bool,
    goal_id: str,
    expected_state_fingerprint: str | None,
    expected_source_basis: Mapping[str, str] | None,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str], str] | None:
    try:
        locked_route, current_source_basis, current_fingerprint = _route_snapshot(
            goal_id=goal_id,
            requested_registry=requested_registry,
        )
    except ValueError as exc:
        if str(exc) != f"goal id not found in registry: {goal_id}":
            raise
        payload.update(
            _missing_stale_payload(
                goal_id=goal_id,
                registry_path=requested_registry,
                execute=True,
                expected_state_fingerprint=expected_state_fingerprint,
            )
        )
        return None
    route_changed = not _route_matches_locked_paths(
        locked_route,
        source_registry=source_registry,
        declared_source_registry=declared_source_registry,
        target_registry=target_registry,
        source_available=source_available,
        same_registry=same_registry,
    )
    if route_changed or (
        expected_state_fingerprint is not None
        and current_fingerprint != expected_state_fingerprint
    ) or (
        expected_source_basis is not None
        and current_source_basis != expected_source_basis
    ):
        _mark_stale(
            payload,
            current_state_fingerprint=current_fingerprint,
            current_source_basis=current_source_basis,
        )
        return None
    return locked_route, current_source_basis, current_fingerprint


def _route_snapshot(
    *,
    goal_id: str,
    requested_registry: Path,
) -> tuple[dict[str, Any], dict[str, str], str]:
    route = _resolve_route(
        goal_id,
        requested_registry,
        require_stopped=False,
    )
    source_basis = _source_basis(route)
    return (
        route,
        source_basis,
        _state_fingerprint(
            goal_id=goal_id,
            source_basis=source_basis,
            target_registry=Path(route["target_registry"]),
        ),
    )


def _load_locked_payloads(
    *,
    requested_registry: Path,
    source_registry: Path,
    declared_source_registry: Path,
    target_registry: Path,
    source_available: bool,
    same_registry: bool,
    goal_id: str,
    expected_state_fingerprint: str | None,
    expected_source_basis: Mapping[str, str] | None,
    payload: dict[str, Any],
) -> tuple[dict[Path, dict[str, Any]], dict[str, str], str] | None:
    current_source = (
        read_json(source_registry)
        if source_available and same_registry
        else load_registry(source_registry)
        if source_available
        else None
    )
    current_target = read_json(target_registry)
    snapshot = _validated_locked_route_snapshot(
        requested_registry=requested_registry,
        source_registry=source_registry,
        declared_source_registry=declared_source_registry,
        target_registry=target_registry,
        source_available=source_available,
        same_registry=same_registry,
        goal_id=goal_id,
        expected_state_fingerprint=expected_state_fingerprint,
        expected_source_basis=expected_source_basis,
        payload=payload,
    )
    if snapshot is None:
        return None
    _, current_source_basis, current_fingerprint = snapshot

    source_goal = _goal_or_none(current_source, goal_id) if current_source else None
    target_goal = _goal_or_none(current_target, goal_id)
    locked_goal = source_goal or target_goal
    if locked_goal is None or target_goal is None:
        raise ValueError("Goal disappeared before deletion; refresh and retry")
    if goal_activation_state(locked_goal) is not GoalActivationState.STOPPED:
        raise ValueError("Goal activation changed; stop the Goal before deleting it")
    current_payloads = {target_registry: current_target}
    if source_available and not same_registry:
        if current_source is None or source_goal is None:
            raise ValueError("Goal source registry changed; refresh and retry")
        current_payloads[source_registry] = current_source
    return current_payloads, current_source_basis, current_fingerprint


def _updated_payloads(
    current_payloads: dict[Path, dict[str, Any]], goal_id: str
) -> dict[Path, dict[str, Any]]:
    updated_payloads: dict[Path, dict[str, Any]] = {}
    for path, current in current_payloads.items():
        updated, changed = _remove_goal(current, goal_id)
        if not changed:
            raise ValueError("Goal disappeared before deletion; refresh and retry")
        updated_payloads[path] = updated
    return updated_payloads


def _deletion_write_paths(
    *,
    current_payloads: Mapping[Path, dict[str, Any]],
    source_registry: Path,
    target_registry: Path,
    source_available: bool,
) -> list[Path]:
    paths: list[Path] = []
    if (
        source_available
        and not _same_path(source_registry, target_registry)
        and source_registry in current_payloads
    ):
        paths.append(source_registry)
    if target_registry in current_payloads:
        paths.append(target_registry)
    if len(paths) != len(current_payloads):
        raise ValueError("Goal deletion registry set is invalid")
    return paths


def _write_deletion(
    *,
    current_payloads: dict[Path, dict[str, Any]],
    updated_payloads: dict[Path, dict[str, Any]],
    source_registry: Path,
    declared_source_registry: Path,
    target_registry: Path,
    source_available: bool,
    locked_source_basis: Mapping[str, str],
    locked_state_fingerprint: str,
    goal_id: str,
    payload: dict[str, Any],
    source_transaction: ProjectRegistryTransaction | None,
) -> None:
    written_paths: list[Path] = []
    try:
        timestamp = now_local_iso()
        paths = _deletion_write_paths(
            current_payloads=current_payloads,
            source_registry=source_registry,
            target_registry=target_registry,
            source_available=source_available,
        )
        backup_paths: dict[Path, Path] = {}
        for path in paths:
            backup = _create_backup(path, timestamp=timestamp)
            backup_paths[path] = backup
            payload["backup_paths"].append(str(backup))
        recovery_record = _build_recovery_record(
            goal_id=goal_id,
            state_fingerprint=locked_state_fingerprint,
            source_basis=locked_source_basis,
            source_registry=source_registry,
            declared_source_registry=declared_source_registry,
            target_registry=target_registry,
            source_available=source_available,
            same_registry=_same_path(source_registry, target_registry),
            paths=paths,
            backup_paths=backup_paths,
        )
        _write_recovery_receipts(recovery_record)
        for path in paths:
            _write_locked_registry(
                path=path,
                updated=updated_payloads[path],
                source_registry=source_registry,
                source_transaction=source_transaction,
            )
            written_paths.append(path)
        source_missing = (
            _goal_or_none(load_registry(source_registry), goal_id) is None
            if source_available
            else _source_basis(
                {
                    "declared_source_registry": declared_source_registry,
                    "source_status": _source_status(
                        declared_source_registry,
                        goal_id=goal_id,
                    ).value,
                    "route_mode": locked_source_basis["route_mode"],
                }
            )
            == locked_source_basis
        )
        target_after = read_json(target_registry)
        global_missing = _goal_or_none(target_after, goal_id) is None
        if not source_missing or not global_missing:
            raise ValueError("Goal deletion readback did not verify")
        payload["readback"] = {
            "source_missing": source_missing,
            "global_missing": global_missing,
            "verified": source_missing and global_missing,
        }
        payload["written"] = bool(written_paths)
        payload["ok"] = True
        payload["partial_write"] = False
        payload["recovered"] = False
        payload["replayed"] = False
    except Exception:
        for path in reversed(written_paths):
            _restore_locked_registry(
                path=path,
                current=current_payloads[path],
                source_registry=source_registry,
                source_transaction=source_transaction,
            )
        raise


def _write_locked_registry(
    *,
    path: Path,
    updated: dict[str, Any],
    source_registry: Path,
    source_transaction: ProjectRegistryTransaction | None,
) -> None:
    if source_transaction is not None and _same_path(path, source_registry):
        source_transaction.commit(updated)
        return
    atomic_write_json(path, updated, preserve_mode=True)


def _restore_locked_registry(
    *,
    path: Path,
    current: dict[str, Any],
    source_registry: Path,
    source_transaction: ProjectRegistryTransaction | None,
) -> None:
    if source_transaction is not None and _same_path(path, source_registry):
        source_transaction.restore()
        return
    atomic_write_json(path, current, preserve_mode=True)


def _execute_deletion(
    *,
    requested_registry: Path,
    source_registry: Path,
    declared_source_registry: Path,
    target_registry: Path,
    source_available: bool,
    same_registry: bool,
    goal_id: str,
    expected_state_fingerprint: str | None,
    expected_source_basis: Mapping[str, str] | None,
    payload: dict[str, Any],
) -> None:
    """Apply deletion and read it back while registry locks are held."""

    locked_source_registry = (
        source_registry if source_available else declared_source_registry
    )
    with ExitStack() as stack:
        stack.enter_context(
            exclusive_cross_runtime_file_lock(
                _canonical_writer_guard_path(target_registry, goal_id),
                operation="delete_stopped_goal_canonical",
                timeout_seconds=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
            )
        )
        source_transaction = None
        if not _same_path(locked_source_registry, target_registry):
            if source_available and not same_registry:
                source_transaction = stack.enter_context(
                    project_registry_transaction(
                        locked_source_registry,
                        operation="delete_stopped_goal",
                    )
                )
            else:
                stack.enter_context(
                    exclusive_cross_runtime_file_lock(
                        locked_source_registry,
                        operation="delete_stopped_goal",
                    )
                )
        stack.enter_context(
            exclusive_cross_runtime_file_lock(target_registry, operation="delete_stopped_goal")
        )
        locked_state = _load_locked_payloads(
            requested_registry=requested_registry,
            source_registry=source_registry,
            declared_source_registry=declared_source_registry,
            target_registry=target_registry,
            source_available=source_available,
            same_registry=same_registry,
            goal_id=goal_id,
            expected_state_fingerprint=expected_state_fingerprint,
            expected_source_basis=expected_source_basis,
            payload=payload,
        )
        if locked_state is None:
            return
        current_payloads, locked_source_basis, locked_state_fingerprint = locked_state
        updated_payloads = _updated_payloads(current_payloads, goal_id)
        if (
            _validated_locked_route_snapshot(
                requested_registry=requested_registry,
                source_registry=source_registry,
                declared_source_registry=declared_source_registry,
                target_registry=target_registry,
                source_available=source_available,
                same_registry=same_registry,
                goal_id=goal_id,
                expected_state_fingerprint=locked_state_fingerprint,
                expected_source_basis=locked_source_basis,
                payload=payload,
            )
            is None
        ):
            return
        _write_deletion(
            current_payloads=current_payloads,
            updated_payloads=updated_payloads,
            source_registry=source_registry,
            declared_source_registry=declared_source_registry,
            target_registry=target_registry,
            source_available=source_available,
            locked_source_basis=locked_source_basis,
            locked_state_fingerprint=locked_state_fingerprint,
            goal_id=goal_id,
            payload=payload,
            source_transaction=source_transaction,
        )


def delete_stopped_goal(
    *,
    registry_path: Path,
    goal_id: str,
    execute: bool = False,
    expected_state_fingerprint: str | None = None,
    expected_source_basis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview or permanently remove one stopped Goal from its registries.

    Goal data such as state files and project files is intentionally retained;
    deletion removes only the registry entries that make the Goal visible to
    the LoopX control plane. Expected state and source values are checked again
    while both registry locks are held. A matching write-ahead recovery receipt
    makes retries from the same preview idempotent after process termination.
    """

    normalized_goal_id = _require_opaque_id(goal_id, field="goal_id")
    requested_registry = Path(registry_path)
    normalized_fingerprint = str(expected_state_fingerprint or "").strip() or None
    if normalized_fingerprint is not None and not BARE_SHA256_PATTERN.fullmatch(
        normalized_fingerprint
    ):
        raise ValueError("expected state fingerprint must be a SHA-256 digest")
    normalized_source_basis = _normalize_source_basis(expected_source_basis)
    recovery_record = _matching_recovery_record(
        requested_registry=requested_registry,
        goal_id=normalized_goal_id,
        expected_state_fingerprint=normalized_fingerprint,
        expected_source_basis=normalized_source_basis,
    )
    if recovery_record is not None:
        if execute:
            return _execute_recovery(recovery_record)
        recovery_states, _recovery_payloads = _recovery_states(recovery_record)
        return _recovery_result(
            recovery_record,
            recovery_states,
            execute=False,
            written=False,
        )
    try:
        route = _resolve_route(
            normalized_goal_id,
            requested_registry,
            require_stopped=False,
        )
    except ValueError as exc:
        if (
            normalized_fingerprint is None
            or str(exc) != f"goal id not found in registry: {normalized_goal_id}"
        ):
            raise
        return _missing_stale_payload(
            goal_id=normalized_goal_id,
            registry_path=requested_registry,
            execute=execute,
            expected_state_fingerprint=normalized_fingerprint,
        )
    observed_source_basis = _source_basis(route)
    observed_fingerprint = _state_fingerprint(
        goal_id=normalized_goal_id,
        source_basis=observed_source_basis,
        target_registry=route["target_registry"],
    )

    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": GOAL_DELETION_SCHEMA_VERSION,
        "dry_run": not execute,
        "execute": execute,
        "goal_id": normalized_goal_id,
        "source_registry": str(route["source_registry"]),
        "target_global_registry": str(route["target_registry"]),
        "source_registry_present": route["source_goal"] is not None,
        "global_registry_present": route["target_goal"] is not None,
        "source_basis": observed_source_basis,
        "authority_route_mode": route["route_mode"],
        "expected_state_fingerprint": normalized_fingerprint,
        "observed_state_fingerprint": observed_fingerprint,
        "written": False,
        "partial_write": False,
        "backup_paths": [],
        "recovery_pending": False,
        "recovered": False,
        "replayed": False,
        "readback": {
            "source_missing": route["source_goal"] is None,
            "global_missing": False,
            "verified": False,
        },
    }
    if (
        normalized_fingerprint is not None
        and observed_fingerprint != normalized_fingerprint
    ) or (
        normalized_source_basis is not None
        and observed_source_basis != normalized_source_basis
    ):
        _mark_stale(
            payload,
            current_state_fingerprint=observed_fingerprint,
            current_source_basis=observed_source_basis,
        )
        return payload
    goal = route["source_goal"] or route["target_goal"]
    if goal_activation_state(goal) is not GoalActivationState.STOPPED:
        raise ValueError("stop the Goal before deleting it")
    if not execute:
        return payload

    if not route["source_available"]:
        lock_error = _orphan_source_lock_error(route["declared_source_registry"])
        if lock_error is not None:
            payload.update(
                {
                    "ok": False,
                    "error_kind": "goal_source_lock_unavailable",
                    "error": lock_error,
                    "recommended_action": (
                        "Repair the Goal source registry route before deleting "
                        "this Goal."
                    ),
                }
            )
            return payload

    paths = [route["target_registry"]]
    if not route["same_registry"]:
        paths.append(route["source_registry"])
    writability = _check_writability(paths)
    if writability is not None:
        payload.update(
            {
                "ok": False,
                "error_kind": "goal_registry_write_denied",
                "error": str(writability.get("error") or "Goal registry is not writable"),
                "recommended_action": writability.get("recommended_action"),
            }
        )
        return payload

    _execute_deletion(
        requested_registry=requested_registry,
        source_registry=route["source_registry"],
        declared_source_registry=route["declared_source_registry"],
        target_registry=route["target_registry"],
        source_available=route["source_available"],
        same_registry=route["same_registry"],
        goal_id=normalized_goal_id,
        expected_state_fingerprint=normalized_fingerprint,
        expected_source_basis=normalized_source_basis,
        payload=payload,
    )
    return payload
