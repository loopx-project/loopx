from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...file_lock import exclusive_file_lock
from ...registry import atomic_write_json, read_json
from ...configuration_transaction import (
    CONFIGURATION_REVISION_MISSING,
    build_configuration_update_plan,
    configuration_payload_revision,
    require_expected_configuration_plan_revision,
)
from .contract import (
    MachineConfigurationRegistry,
    machine_configuration_revision,
    normalize_machine_configuration,
    project_machine_configuration,
)
from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN


MACHINE_CONFIGURATION_UPDATE_PLAN_SCHEMA = "machine_configuration_update_plan_v0"
MACHINE_CONFIGURATION_TRANSACTION_SCHEMA = "machine_configuration_transaction_v0"
MACHINE_CONFIGURATION_BACKUP_SCHEMA = "machine_configuration_backup_v0"
MACHINE_CONFIGURATION_INSPECTION_SCHEMA = "machine_configuration_inspection_v0"
MACHINE_CONFIGURATION_ROLLBACK_PLAN_SCHEMA = "machine_configuration_rollback_plan_v0"
MACHINE_CONFIGURATION_ROLLBACK_RECEIPT_SCHEMA = (
    "machine_configuration_rollback_receipt_v0"
)

_MISSING_REVISION = CONFIGURATION_REVISION_MISSING
_TRANSACTION_ID_RE = re.compile(r"^machine_configuration_[0-9a-f]{24}$")
JsonWriter = Callable[[Path, dict[str, Any]], None]


def _digest(value: object) -> str:
    return configuration_payload_revision(value)


def _now_iso(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _runtime_root(runtime_root: Path) -> Path:
    return runtime_root.expanduser().resolve()


def machine_configuration_store_path(runtime_root: Path) -> Path:
    return _runtime_root(runtime_root) / "machine" / "configuration.json"


def _store_ref() -> str:
    return "machine/configuration.json"


def _transaction_ref(transaction_id: str) -> str:
    return f"machine/configuration/transactions/{transaction_id}.json"


def _backup_ref(transaction_id: str) -> str:
    return f"machine/configuration/backups/{transaction_id}.json"


def _transaction_path(runtime_root: Path, transaction_id: str) -> Path:
    return _runtime_root(runtime_root) / _transaction_ref(transaction_id)


def _backup_path(runtime_root: Path, transaction_id: str) -> Path:
    return _runtime_root(runtime_root) / _backup_ref(transaction_id)


def _safe_transaction_id(value: str) -> str:
    transaction_id = str(value or "").strip()
    if not _TRANSACTION_ID_RE.fullmatch(transaction_id):
        raise ValueError("transaction_id is invalid")
    return transaction_id


def _new_transaction_id() -> str:
    return f"machine_configuration_{uuid4().hex[:24]}"


def _new_rollback_id() -> str:
    return f"machine_configuration_rollback_{uuid4().hex[:24]}"


def _secure_write(
    path: Path, payload: dict[str, Any], *, writer: JsonWriter = atomic_write_json
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    writer(path, payload)
    path.chmod(0o600)


def read_machine_configuration(
    runtime_root: Path, *, registry: MachineConfigurationRegistry
) -> dict[str, Any] | None:
    path = machine_configuration_store_path(runtime_root)
    if not path.is_file():
        return None
    normalized: dict[str, Any] = normalize_machine_configuration(
        read_json(path), registry=registry
    )
    return normalized


def read_stored_machine_configuration(runtime_root: Path) -> dict[str, Any] | None:
    """Read the exact stored envelope for fenced repair and rollback.

    Normal reads remain fail-closed through ``read_machine_configuration``.
    Mutation planning needs the prior bytes even when one namespace became
    legacy, otherwise the transactional API cannot replace or restore it.
    """

    path = machine_configuration_store_path(runtime_root)
    if not path.is_file():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("machine_configuration must be an object")
    return payload


def inspect_machine_configuration(
    runtime_root: Path, *, registry: MachineConfigurationRegistry
) -> dict[str, Any]:
    current = read_machine_configuration(runtime_root, registry=registry)
    return {
        "ok": True,
        "schema_version": MACHINE_CONFIGURATION_INSPECTION_SCHEMA,
        "status": "configured" if current is not None else "absent",
        "defaults_ref": _store_ref(),
        "revision": (
            machine_configuration_revision(current)
            if current is not None
            else _MISSING_REVISION
        ),
        "changed_namespaces": [],
        "machine_configuration": (
            project_machine_configuration(current, registry=registry)
            if current is not None
            else None
        ),
    }


def _update_plan(
    *,
    current: Mapping[str, Any] | None,
    desired: Mapping[str, Any] | None,
    registry: MachineConfigurationRegistry,
) -> dict[str, Any]:
    normalized_desired = (
        normalize_machine_configuration(desired, registry=registry)
        if desired is not None
        else None
    )
    stored_current = dict(current) if current is not None else None
    current_revision = (
        machine_configuration_revision(stored_current)
        if stored_current is not None
        else _MISSING_REVISION
    )
    desired_revision = (
        machine_configuration_revision(normalized_desired)
        if normalized_desired is not None
        else _MISSING_REVISION
    )
    changed_namespaces = sorted(
            namespace
            for namespace in set((normalized_desired or {}).get("namespaces", {}))
            | set((stored_current or {}).get("namespaces", {}))
            if (stored_current or {}).get("namespaces", {}).get(namespace)
            != (normalized_desired or {}).get("namespaces", {}).get(namespace)
        )
    return build_configuration_update_plan(
        schema_version=MACHINE_CONFIGURATION_UPDATE_PLAN_SCHEMA,
        current_present=stored_current is not None,
        desired_present=normalized_desired is not None,
        current_revision=current_revision,
        desired_revision=desired_revision,
        target_identity={"defaults_ref": _store_ref()},
        changed_units={"changed_namespaces": changed_namespaces},
        projected_configuration=(
            project_machine_configuration(normalized_desired, registry=registry)
            if normalized_desired is not None
            else None
        ),
        projection_field="machine_configuration",
    )


def plan_machine_configuration_update(
    *,
    runtime_root: Path,
    configuration: Mapping[str, Any] | None,
    registry: MachineConfigurationRegistry,
) -> dict[str, Any]:
    return _update_plan(
        current=read_stored_machine_configuration(runtime_root),
        desired=configuration,
        registry=registry,
    )


def _restore_prior_state(
    *,
    store_path: Path,
    prior: Mapping[str, Any] | None,
    writer: JsonWriter,
) -> None:
    if prior is None:
        store_path.unlink(missing_ok=True)
        return
    _secure_write(store_path, dict(prior), writer=writer)


def configure_machine_configuration(
    *,
    runtime_root: Path,
    configuration: Mapping[str, Any] | None,
    registry: MachineConfigurationRegistry,
    execute: bool = False,
    expected_plan_revision: str | None = None,
    now: datetime | None = None,
    writer: JsonWriter = atomic_write_json,
) -> dict[str, Any]:
    """Preview or atomically apply one machine policy with a rollback receipt."""

    preview = plan_machine_configuration_update(
        runtime_root=runtime_root,
        configuration=configuration,
        registry=registry,
    )
    if not execute:
        return preview
    store_path = machine_configuration_store_path(runtime_root)
    with exclusive_file_lock(store_path, operation="configure_machine_configuration"):
        current = read_stored_machine_configuration(runtime_root)
        plan = _update_plan(current=current, desired=configuration, registry=registry)
        require_expected_configuration_plan_revision(
            expected_plan_revision=expected_plan_revision,
            actual_plan_revision=str(plan["plan_revision"]),
            subject="machine-configuration",
        )
        if plan["action"] == "unchanged":
            return {
                **plan,
                "schema_version": MACHINE_CONFIGURATION_TRANSACTION_SCHEMA,
                "status": "unchanged",
                "transaction_id": None,
                "transaction_ref": None,
                "backup_ref": None,
                "readback_verified": True,
                "rollback_available": False,
            }

        transaction_id = _new_transaction_id()
        backup_path = _backup_path(runtime_root, transaction_id)
        receipt_path = _transaction_path(runtime_root, transaction_id)
        prior_revision = str(plan["current_revision"])
        backup = {
            "schema_version": MACHINE_CONFIGURATION_BACKUP_SCHEMA,
            "transaction_id": transaction_id,
            "prior_revision": prior_revision,
            "prior_machine_configuration": current,
        }
        desired = (
            normalize_machine_configuration(configuration, registry=registry)
            if configuration is not None
            else None
        )
        try:
            _secure_write(backup_path, backup, writer=writer)
            if desired is None:
                store_path.unlink(missing_ok=True)
            else:
                _secure_write(store_path, desired, writer=writer)
            readback = read_machine_configuration(runtime_root, registry=registry)
            if readback != desired:
                raise RuntimeError(
                    "machine-configuration readback did not match the requested policy"
                )
            receipt = {
                "ok": True,
                "schema_version": MACHINE_CONFIGURATION_TRANSACTION_SCHEMA,
                "status": "applied",
                "transaction_id": transaction_id,
                "transaction_ref": _transaction_ref(transaction_id),
                "backup_ref": _backup_ref(transaction_id),
                "defaults_ref": _store_ref(),
                "plan_revision": plan["plan_revision"],
                "prior_revision": prior_revision,
                "applied_revision": plan["desired_revision"],
                "applied_at": _now_iso(now),
                "readback_verified": True,
                "rollback_available": True,
                "changed_namespaces": plan["changed_namespaces"],
                "machine_configuration": (
                    project_machine_configuration(readback, registry=registry)
                    if readback is not None
                    else None
                ),
            }
            receipt["receipt_revision"] = _digest(receipt)
            _secure_write(receipt_path, receipt, writer=writer)
            return receipt
        except Exception as exc:
            try:
                _restore_prior_state(
                    store_path=store_path, prior=current, writer=writer
                )
            except Exception as rollback_exc:
                raise RuntimeError(
                    "machine-configuration apply failed and automatic rollback also failed"
                ) from rollback_exc
            raise RuntimeError(
                "machine-configuration apply failed; prior state was restored"
            ) from exc


def _read_transaction(runtime_root: Path, transaction_id: str) -> dict[str, Any]:
    safe_id = _safe_transaction_id(transaction_id)
    path = _transaction_path(runtime_root, safe_id)
    if not path.is_file():
        raise ValueError(f"machine-configuration transaction not found: {safe_id}")
    receipt: dict[str, Any] = read_json(path)
    if (
        receipt.get("schema_version") != MACHINE_CONFIGURATION_TRANSACTION_SCHEMA
        or receipt.get("transaction_id") != safe_id
        or receipt.get("status") != "applied"
        or receipt.get("transaction_ref") != _transaction_ref(safe_id)
        or receipt.get("defaults_ref") != _store_ref()
    ):
        raise ValueError("machine-configuration transaction receipt is invalid")
    receipt_revision = str(receipt.pop("receipt_revision", ""))
    if receipt_revision != _digest(receipt):
        raise ValueError("machine-configuration transaction receipt is invalid")
    receipt["receipt_revision"] = receipt_revision
    applied_revision = str(receipt.get("applied_revision") or "")
    if applied_revision != _MISSING_REVISION and not (
        ENVELOPED_SHA256_PATTERN.fullmatch(applied_revision)
    ):
        raise ValueError("machine-configuration transaction revision is invalid")
    return receipt


def _read_backup(
    runtime_root: Path,
    transaction_id: str,
    receipt: Mapping[str, Any],
    *,
    registry: MachineConfigurationRegistry,
) -> dict[str, Any]:
    path = _backup_path(runtime_root, transaction_id)
    if receipt.get("backup_ref") != _backup_ref(transaction_id) or not path.is_file():
        raise ValueError("machine-configuration transaction backup is unavailable")
    backup = read_json(path)
    if (
        backup.get("schema_version") != MACHINE_CONFIGURATION_BACKUP_SCHEMA
        or backup.get("transaction_id") != transaction_id
    ):
        raise ValueError("machine-configuration transaction backup is invalid")
    prior = backup.get("prior_machine_configuration")
    if prior is not None and not isinstance(prior, Mapping):
        raise ValueError("machine-configuration transaction backup is invalid")
    normalized_prior = dict(prior) if prior is not None else None
    prior_revision = (
        machine_configuration_revision(normalized_prior)
        if normalized_prior is not None
        else _MISSING_REVISION
    )
    if prior_revision != backup.get("prior_revision") or prior_revision != receipt.get(
        "prior_revision"
    ):
        raise ValueError(
            "machine-configuration backup revision does not match its receipt"
        )
    return {**backup, "prior_machine_configuration": normalized_prior}


def _rollback_plan(
    *,
    transaction_id: str,
    receipt: Mapping[str, Any],
    backup: Mapping[str, Any],
    current: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current_revision = (
        machine_configuration_revision(current)
        if current is not None
        else _MISSING_REVISION
    )
    applied_revision = str(receipt.get("applied_revision") or "")
    target_revision = str(backup.get("prior_revision") or "")
    if current_revision == target_revision:
        action, allowed, reason = "unchanged", True, "already_rolled_back"
    elif current_revision != applied_revision:
        action, allowed, reason = "blocked", False, "current_revision_changed"
    else:
        action = "delete" if target_revision == _MISSING_REVISION else "restore"
        allowed, reason = True, "exact_applied_revision_matches"
    identity = {
        "transaction_id": transaction_id,
        "current_revision": current_revision,
        "applied_revision": applied_revision,
        "target_revision": target_revision,
        "action": action,
    }
    return {
        "ok": True,
        "schema_version": MACHINE_CONFIGURATION_ROLLBACK_PLAN_SCHEMA,
        "status": "preview",
        **identity,
        "reason": reason,
        "rollback_allowed": allowed,
        "writes_required": int(action in {"delete", "restore"}),
        "plan_revision": _digest(identity),
    }


def plan_machine_configuration_rollback(
    *,
    runtime_root: Path,
    transaction_id: str,
    registry: MachineConfigurationRegistry,
) -> dict[str, Any]:
    safe_id = _safe_transaction_id(transaction_id)
    receipt = _read_transaction(runtime_root, safe_id)
    backup = _read_backup(runtime_root, safe_id, receipt, registry=registry)
    current = read_stored_machine_configuration(runtime_root)
    return _rollback_plan(
        transaction_id=safe_id,
        receipt=receipt,
        backup=backup,
        current=current,
    )


def rollback_machine_configuration(
    *,
    runtime_root: Path,
    transaction_id: str,
    registry: MachineConfigurationRegistry,
    execute: bool = False,
    expected_plan_revision: str | None = None,
    now: datetime | None = None,
    writer: JsonWriter = atomic_write_json,
) -> dict[str, Any]:
    preview = plan_machine_configuration_rollback(
        runtime_root=runtime_root,
        transaction_id=transaction_id,
        registry=registry,
    )
    if not execute:
        return preview
    safe_id = _safe_transaction_id(transaction_id)
    store_path = machine_configuration_store_path(runtime_root)
    with exclusive_file_lock(store_path, operation="rollback_machine_configuration"):
        receipt = _read_transaction(runtime_root, safe_id)
        backup = _read_backup(runtime_root, safe_id, receipt, registry=registry)
        current = read_stored_machine_configuration(runtime_root)
        plan = _rollback_plan(
            transaction_id=safe_id,
            receipt=receipt,
            backup=backup,
            current=current,
        )
        require_expected_configuration_plan_revision(
            expected_plan_revision=expected_plan_revision,
            actual_plan_revision=str(plan["plan_revision"]),
            subject="machine-configuration rollback",
        )
        if not plan["rollback_allowed"]:
            raise ValueError(
                "machine-configuration rollback is blocked by a newer revision"
            )
        if plan["action"] == "unchanged":
            return {
                **plan,
                "schema_version": MACHINE_CONFIGURATION_ROLLBACK_RECEIPT_SCHEMA,
                "status": "unchanged",
                "rollback_id": None,
                "readback_verified": True,
            }

        prior = backup.get("prior_machine_configuration")
        rollback_id = _new_rollback_id()
        rollback_path = _runtime_root(runtime_root) / _transaction_ref(rollback_id)
        try:
            _restore_prior_state(store_path=store_path, prior=prior, writer=writer)
            readback = read_stored_machine_configuration(runtime_root)
            if readback != prior:
                raise RuntimeError(
                    "machine-configuration rollback readback did not match backup"
                )
            result = {
                "ok": True,
                "schema_version": MACHINE_CONFIGURATION_ROLLBACK_RECEIPT_SCHEMA,
                "status": "rolled_back",
                "rollback_id": rollback_id,
                "rollback_ref": _transaction_ref(rollback_id),
                "transaction_id": safe_id,
                "plan_revision": plan["plan_revision"],
                "restored_revision": plan["target_revision"],
                "rolled_back_at": _now_iso(now),
                "readback_verified": True,
            }
            _secure_write(rollback_path, result, writer=writer)
            return result
        except Exception as exc:
            try:
                _restore_prior_state(
                    store_path=store_path, prior=current, writer=writer
                )
            except Exception as restore_exc:
                raise RuntimeError(
                    "machine-configuration rollback failed and applied state could not be restored"
                ) from restore_exc
            raise RuntimeError(
                "machine-configuration rollback failed; applied state was restored"
            ) from exc


__all__ = [
    "MACHINE_CONFIGURATION_BACKUP_SCHEMA",
    "MACHINE_CONFIGURATION_INSPECTION_SCHEMA",
    "MACHINE_CONFIGURATION_ROLLBACK_PLAN_SCHEMA",
    "MACHINE_CONFIGURATION_ROLLBACK_RECEIPT_SCHEMA",
    "MACHINE_CONFIGURATION_TRANSACTION_SCHEMA",
    "MACHINE_CONFIGURATION_UPDATE_PLAN_SCHEMA",
    "configure_machine_configuration",
    "inspect_machine_configuration",
    "machine_configuration_store_path",
    "plan_machine_configuration_rollback",
    "plan_machine_configuration_update",
    "read_machine_configuration",
    "read_stored_machine_configuration",
    "rollback_machine_configuration",
]
