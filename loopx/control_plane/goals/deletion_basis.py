"""Pure deletion basis, state fingerprint, backup, and locking helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import Literal, TypedDict, overload
import uuid

from ...configuration_transaction import configuration_payload_revision
from ...file_lock import (
    EFFECT_MUTATION_LOCK_SUFFIX,
    lock_holder_paths,
    lock_incident_path,
)
from ...public_safe_text import OPAQUE_ID_PATTERN
from ...registry_writability import probe_registry_write_path
from ..coordination.shadow_management import shadow_maintenance_lock_target
from ..runtime.time import now_local_iso
from .activation_service import (
    GoalActivationAuthorityRouteMode,
    _goal_activation_source_identity,
    _goal_or_none,
    _same_path,
)

GOAL_DELETION_SCHEMA_VERSION = "loopx_goal_deletion_v1"
GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION = "loopx_goal_deletion_source_basis_v1"
GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION = (
    "loopx_goal_deletion_state_fingerprint_v1"
)
GOAL_DELETION_RECOVERY_SCHEMA_VERSION = "loopx_goal_deletion_recovery_v1"
_MAX_RECOVERY_RECEIPT_BYTES = 64 * 1024
_OPAQUE_ID: re.Pattern[str] = OPAQUE_ID_PATTERN

Payload = dict[str, object]
PayloadView = Mapping[str, object]
NowIsoFn = Callable[[], str]


class DeletionReadback(TypedDict):
    """Readback verification flags produced after deletion or recovery."""

    source_missing: bool
    global_missing: bool
    verified: bool


class DeletionRoute(TypedDict):
    """Resolved source and global registry routing metadata for a Goal."""

    source_registry: Path
    declared_source_registry: Path
    target_registry: Path
    source_available: bool
    source_status: str
    source_goal: Payload | None
    target_goal: Payload
    same_registry: bool
    route_mode: str


class DeletionResult(dict[str, object]):
    """Structured dict returned by Goal deletion preview, execution, and recovery."""

    @overload
    def __getitem__(
        self,
        key: Literal[
            "schema_version",
            "goal_id",
            "source_registry",
            "target_global_registry",
            "authority_route_mode",
            "observed_state_fingerprint",
            "current_state_fingerprint",
            "error_kind",
            "error",
        ],
        /,
    ) -> str: ...

    @overload
    def __getitem__(
        self,
        key: Literal["expected_state_fingerprint", "recommended_action"],
        /,
    ) -> str | None: ...

    @overload
    def __getitem__(
        self,
        key: Literal[
            "ok",
            "dry_run",
            "execute",
            "source_registry_present",
            "global_registry_present",
            "written",
            "partial_write",
            "recovery_pending",
            "recovered",
            "replayed",
            "stale",
        ],
        /,
    ) -> bool: ...

    @overload
    def __getitem__(
        self,
        key: Literal["source_basis"],
        /,
    ) -> dict[str, str]: ...

    @overload
    def __getitem__(
        self,
        key: Literal["backup_paths"],
        /,
    ) -> list[str]: ...

    @overload
    def __getitem__(
        self,
        key: Literal["readback"],
        /,
    ) -> DeletionReadback: ...

    @overload
    def __getitem__(self, key: str, /) -> object: ...

    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)


def _as_payload(value: Mapping[str, object]) -> Payload:
    return {str(key): item for key, item in value.items()}


def _goal_payload_or_none(payload: Payload | None, goal_id: str) -> Payload | None:
    if payload is None:
        return None
    found = _goal_or_none(payload, goal_id)
    if not isinstance(found, Mapping):
        return None
    return {str(key): item for key, item in found.items()}


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


def _source_basis(route: PayloadView) -> dict[str, str]:
    source_registry = Path(str(route["declared_source_registry"]))
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


def _normalize_source_basis_with_pattern(
    value: PayloadView | None,
    *,
    sha256_pattern: re.Pattern[str],
) -> dict[str, str] | None:
    """Validate and normalize an expected Goal deletion source-basis mapping."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("expected source basis must be a mapping")
    schema_version = str(value.get("schema_version") or "")
    source_identity = str(value.get("source_identity") or "")
    source_content_sha256 = str(value.get("source_content_sha256") or "")
    route_mode = str(value.get("route_mode") or "")
    if schema_version != GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION:
        raise ValueError("expected source basis has an unsupported schema version")
    if not sha256_pattern.fullmatch(source_identity):
        raise ValueError("expected source identity must be a SHA-256 digest")
    if not sha256_pattern.fullmatch(source_content_sha256):
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
    payload: DeletionResult,
    *,
    current_state_fingerprint: str,
    current_source_basis: Mapping[str, str],
) -> None:
    payload["ok"] = False
    payload["stale"] = True
    payload["error_kind"] = "goal_registry_changed"
    payload["error"] = (
        "Goal source or registry route changed after preview; "
        "regenerate the deletion preview"
    )
    payload["current_state_fingerprint"] = current_state_fingerprint
    payload["observed_state_fingerprint"] = current_state_fingerprint
    payload["source_basis"] = dict(current_source_basis)


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
) -> DeletionResult:
    current_fingerprint = _missing_state_fingerprint(
        goal_id=goal_id,
        registry_path=registry_path,
    )
    return DeletionResult(
        {
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
    )


def _backup_path(path: Path, timestamp: str, nonce: str) -> Path:
    compact_timestamp = timestamp.replace(":", "").replace("-", "")
    return path.with_name(
        f"{path.name}.goal-delete-{compact_timestamp}-{nonce}.bak"
    )


def _fsync_parent(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    raise FileExistsError(
        f"could not allocate a unique Goal deletion backup for {source_path}"
    )


def _remove_goal(
    payload: Payload,
    goal_id: str,
    *,
    updated_at: object = None,
    now_local_iso_fn: NowIsoFn = now_local_iso,
) -> tuple[Payload, bool]:
    goals = payload.get("goals")
    if not isinstance(goals, list):
        raise ValueError("registry goals must be a list")
    retained: list[object] = [
        goal
        for goal in goals
        if not (isinstance(goal, dict) and str(goal.get("id") or "") == goal_id)
    ]
    changed = len(retained) != len(goals)
    updated = dict(payload)
    updated["goals"] = retained
    if changed:
        updated["updated_at"] = (
            now_local_iso_fn() if updated_at is None else updated_at
        )
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


def _check_writability(paths: list[Path]) -> Payload | None:
    writability = [
        _as_payload(probe_registry_write_path(path, create_parent=False))
        for path in paths
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


def _initial_deletion_payload(
    *,
    goal_id: str,
    route: DeletionRoute,
    execute: bool,
    normalized_fingerprint: str | None,
    observed_source_basis: Mapping[str, str],
    observed_fingerprint: str,
) -> DeletionResult:
    return DeletionResult(
        {
            "ok": True,
            "schema_version": GOAL_DELETION_SCHEMA_VERSION,
            "dry_run": not execute,
            "execute": execute,
            "goal_id": goal_id,
            "source_registry": str(route["source_registry"]),
            "target_global_registry": str(route["target_registry"]),
            "source_registry_present": route["source_goal"] is not None,
            "global_registry_present": route["target_goal"] is not None,
            "source_basis": dict(observed_source_basis),
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
    )


def _deletion_write_paths(
    *,
    current_payloads: Mapping[Path, Payload],
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


def _apply_lock_or_writability_failure(
    payload: DeletionResult,
    route: DeletionRoute,
) -> bool:
    if not route["source_available"]:
        lock_error = _orphan_source_lock_error(route["declared_source_registry"])
        if lock_error is not None:
            payload["ok"] = False
            payload["error_kind"] = "goal_source_lock_unavailable"
            payload["error"] = lock_error
            payload["recommended_action"] = (
                "Repair the Goal source registry route before deleting this Goal."
            )
            return True
    paths = [route["target_registry"]]
    if not route["same_registry"]:
        paths.append(route["source_registry"])
    writability = _check_writability(paths)
    if writability is not None:
        raw_action = writability.get("recommended_action")
        payload["ok"] = False
        payload["error_kind"] = "goal_registry_write_denied"
        payload["error"] = str(
            writability.get("error") or "Goal registry is not writable"
        )
        payload["recommended_action"] = None if raw_action is None else str(raw_action)
        return True
    return False


def _route_matches_locked_paths(
    route: PayloadView,
    *,
    source_registry: Path,
    declared_source_registry: Path,
    target_registry: Path,
    source_available: bool,
    same_registry: bool,
) -> bool:
    return (
        _same_path(Path(str(route["source_registry"])), source_registry)
        and _same_path(
            Path(str(route["declared_source_registry"])),
            declared_source_registry,
        )
        and _same_path(Path(str(route["target_registry"])), target_registry)
        and bool(route["source_available"]) is source_available
        and bool(route["same_registry"]) is same_registry
    )
