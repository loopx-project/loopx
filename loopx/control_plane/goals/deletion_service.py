"""Canonical owner-confirmed deletion for stopped Goals."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path

from ..projects.registry_codec import (
    ProjectRegistryTransaction,
    decode_registry_snapshot as decode_registry_snapshot,
    project_registry_transaction as project_registry_transaction,
)
from ...file_lock import exclusive_cross_runtime_file_lock
from ...history import load_registry as load_registry
from ...public_safe_text import OPAQUE_ID_PATTERN
from ...registry import atomic_write_json as atomic_write_json, read_json
from ..effect_runtime import CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS
from ..runtime.time import now_local_iso as now_local_iso
from .activation import GoalActivationState, goal_activation_state
from .activation_service import (
    GoalActivationAuthorityRouteMode,
    _same_path,
    _source_and_target,
)
from ..content_digest import BARE_SHA256_PATTERN
from .deletion_basis import (
    GOAL_DELETION_RECOVERY_SCHEMA_VERSION as GOAL_DELETION_RECOVERY_SCHEMA_VERSION,
    GOAL_DELETION_SCHEMA_VERSION as GOAL_DELETION_SCHEMA_VERSION,
    GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION as GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION,
    GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION as GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION,
    _MAX_RECOVERY_RECEIPT_BYTES as _MAX_RECOVERY_RECEIPT_BYTES,
    DeletionReadback as DeletionReadback,
    DeletionResult as DeletionResult,
    DeletionRoute as DeletionRoute,
    Payload as Payload,
    PayloadView as PayloadView,
    _apply_lock_or_writability_failure,
    _as_payload,
    _backup_path as _backup_path,
    _canonical_writer_guard_path,
    _create_backup,
    _deletion_write_paths as _deletion_write_paths,
    _fsync_parent as _fsync_parent,
    _goal_payload_or_none,
    _initial_deletion_payload,
    _mark_stale,
    _missing_stale_payload,
    _missing_state_fingerprint as _missing_state_fingerprint,
    _normalize_source_basis_with_pattern,
    _recovery_receipt_path as _recovery_receipt_path,
    _registry_fingerprint as _registry_fingerprint,
    _registry_identity as _registry_identity,
    _remove_goal,
    _require_opaque_id,
    _route_matches_locked_paths,
    _source_basis,
    _state_fingerprint,
    deletion_readback as deletion_readback,
)
from .deletion_recovery import (
    RecoveryRecord as RecoveryRecord,
    RecoveryRuntimeHooks as RecoveryRuntimeHooks,
    RecoverySnapshot as RecoverySnapshot,
    _build_recovery_record,
    _execute_recovery as _run_execute_recovery,
    _load_recovery_record as _load_recovery_record,
    _matching_recovery_record,
    _recovery_images as _recovery_images,
    _recovery_readback as _recovery_readback,
    _recovery_receipts_match as _recovery_receipts_match,
    _recovery_result,
    _recovery_states,
    _validated_recovery_path as _validated_recovery_path,
    _validated_recovery_record as _validated_recovery_record,
    _write_recovery_receipts,
)

_OPAQUE_ID = OPAQUE_ID_PATTERN


def _normalize_source_basis(value: PayloadView | None) -> dict[str, str] | None:
    return _normalize_source_basis_with_pattern(
        value,
        sha256_pattern=BARE_SHA256_PATTERN,
    )


def _load_registry_payload(path: Path) -> Payload:
    return _as_payload(load_registry(path))


def _recovery_hooks() -> RecoveryRuntimeHooks:
    return RecoveryRuntimeHooks(
        load_registry_fn=_load_registry_payload,
        atomic_write_json_fn=atomic_write_json,
        project_registry_transaction_fn=project_registry_transaction,
        write_locked_registry_fn=_write_locked_registry,
        restore_locked_registry_fn=_restore_locked_registry,
        now_local_iso_fn=now_local_iso,
        sha256_pattern=BARE_SHA256_PATTERN,
    )


def _execute_recovery(record: RecoveryRecord | PayloadView) -> DeletionResult:
    return _run_execute_recovery(record, hooks=_recovery_hooks())


def _resolve_route(
    goal_id: str,
    registry_path: Path,
    *,
    require_stopped: bool = True,
) -> DeletionRoute:
    route = _source_and_target(
        registry_path=registry_path,
        goal_id=goal_id,
        target_state=GoalActivationState.STOPPED,
        runtime_root_override=None,
    )
    source_available = (
        route.mode is not GoalActivationAuthorityRouteMode.ORPHANED_GLOBAL_STOP_FALLBACK
    )
    source_payload = _load_registry_payload(route.source_registry) if source_available else None
    target_payload = _as_payload(read_json(route.target_registry))
    source_goal = _goal_payload_or_none(source_payload, goal_id)
    target_goal = _goal_payload_or_none(target_payload, goal_id)
    goal = source_goal or target_goal
    if goal is None:
        raise ValueError(f"goal id not found in registry: {goal_id}")
    if require_stopped and goal_activation_state(goal) is not GoalActivationState.STOPPED:
        raise ValueError("stop the Goal before deleting it")
    if target_goal is None:
        raise ValueError("global registry does not contain the Goal projection")
    declared_source_registry = route.source_registry
    if route.mode is GoalActivationAuthorityRouteMode.ORPHANED_GLOBAL_STOP_FALLBACK:
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


def _route_snapshot(
    *,
    goal_id: str,
    requested_registry: Path,
) -> tuple[DeletionRoute, dict[str, str], str]:
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
            target_registry=route["target_registry"],
        ),
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
    payload: DeletionResult,
) -> tuple[DeletionRoute, dict[str, str], str] | None:
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
    if (
        route_changed
        or (
            expected_state_fingerprint is not None
            and current_fingerprint != expected_state_fingerprint
        )
        or (
            expected_source_basis is not None
            and current_source_basis != expected_source_basis
        )
    ):
        _mark_stale(
            payload,
            current_state_fingerprint=current_fingerprint,
            current_source_basis=current_source_basis,
        )
        return None
    return locked_route, current_source_basis, current_fingerprint


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
    payload: DeletionResult,
) -> tuple[dict[Path, Payload], dict[str, str], str] | None:
    current_source = (
        _as_payload(read_json(source_registry))
        if source_available and same_registry
        else _load_registry_payload(source_registry)
        if source_available
        else None
    )
    current_target = _as_payload(read_json(target_registry))
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

    source_goal = _goal_payload_or_none(current_source, goal_id)
    target_goal = _goal_payload_or_none(current_target, goal_id)
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
    current_payloads: dict[Path, Payload], goal_id: str
) -> dict[Path, Payload]:
    updated_payloads: dict[Path, Payload] = {}
    for path, current in current_payloads.items():
        updated, changed = _remove_goal(
            current,
            goal_id,
            now_local_iso_fn=now_local_iso,
        )
        if not changed:
            raise ValueError("Goal disappeared before deletion; refresh and retry")
        updated_payloads[path] = updated
    return updated_payloads


def _write_deletion(
    *,
    current_payloads: dict[Path, Payload],
    updated_payloads: dict[Path, Payload],
    source_registry: Path,
    declared_source_registry: Path,
    target_registry: Path,
    source_available: bool,
    locked_source_basis: Mapping[str, str],
    locked_state_fingerprint: str,
    goal_id: str,
    payload: DeletionResult,
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
        backup_list = payload["backup_paths"]
        for path in paths:
            backup = _create_backup(path, timestamp=timestamp)
            backup_paths[path] = backup
            backup_list.append(str(backup))
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
        _write_recovery_receipts(
            recovery_record,
            sha256_pattern=BARE_SHA256_PATTERN,
            atomic_write_json_fn=atomic_write_json,
        )
        for path in paths:
            _write_locked_registry(
                path=path,
                updated=updated_payloads[path],
                source_registry=source_registry,
                source_transaction=source_transaction,
            )
            written_paths.append(path)
        if source_available:
            source_after = _load_registry_payload(source_registry)
            source_missing = _goal_payload_or_none(source_after, goal_id) is None
        else:
            from .activation_service import _source_status

            source_missing = (
                _source_basis(
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
        target_after = _as_payload(read_json(target_registry))
        global_missing = _goal_payload_or_none(target_after, goal_id) is None
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
    updated: Payload,
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
    current: Payload,
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
    payload: DeletionResult,
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
        source_transaction: ProjectRegistryTransaction | None = None
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
            exclusive_cross_runtime_file_lock(
                target_registry, operation="delete_stopped_goal"
            )
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
    expected_source_basis: PayloadView | None = None,
) -> DeletionResult:
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
        sha256_pattern=BARE_SHA256_PATTERN,
    )
    if recovery_record is not None:
        if execute:
            return _execute_recovery(recovery_record)
        recovery_states, _recovery_payloads = _recovery_states(
            recovery_record,
            load_registry_fn=_load_registry_payload,
        )
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

    payload = _initial_deletion_payload(
        goal_id=normalized_goal_id,
        route=route,
        execute=execute,
        normalized_fingerprint=normalized_fingerprint,
        observed_source_basis=observed_source_basis,
        observed_fingerprint=observed_fingerprint,
    )
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

    if _apply_lock_or_writability_failure(payload, route):
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
