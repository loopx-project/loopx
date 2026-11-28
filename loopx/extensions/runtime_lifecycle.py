"""Extension installation, enablement, rollback, status, doctor, and catalog."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import cast

from ..file_lock import exclusive_file_lock
from .manifest import load_extension_manifest, loopx_api_incompatibility
from .readiness import EXTENSION_DOCTOR_SCHEMA_VERSION, ResolvedRuntimeEntrypoint
from .runtime_location import probe_runtime_location, record_runtime_location
from .runtime_state import (
    EXTENSION_DOCTOR_BATCH_SCHEMA_VERSION,
    EXTENSION_OPERATION_SCHEMA_VERSION,
    EXTENSION_STATE_SCHEMA_VERSION,
    ExtensionCatalogEntry,
    _active_snapshot_and_manifest,
    _coerce_object_dict,
    _entry_for_revision,
    _extensions_dict,
    _manifest_incompatibility,
    _manifest_snapshot,
    _read_state,
    _record_doctor,
    _retain_revisions,
    _revision,
    _runtime,
    _verified_entrypoint,
    _verified_identities,
    _write_state,
)


def _require_mapping_field(
    container: Mapping[str, object], key: str, *, error: str
) -> Mapping[str, object]:
    value = container.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(error)
    return _coerce_object_dict(value)


def _commit_locked_doctor_update(
    path: Path,
    extension_id: str,
    *,
    active_revision: str,
    expected_enabled: bool,
    doctor: Mapping[str, object],
    location: str | None,
    expected_location: object,
    operation_label: str,
    clear_verified_proof: bool = False,
    new_enabled: bool | None = None,
) -> bool:
    with exclusive_file_lock(path):
        current_state = _read_state(path)
        current_entry = _extensions_dict(current_state).get(extension_id)
        if (
            not isinstance(current_entry, dict)
            or current_entry.get("active_revision") != active_revision
            or bool(current_entry.get("enabled")) != expected_enabled
        ):
            raise ValueError(f"extension state changed during {operation_label}; retry")
        if clear_verified_proof:
            current_entry.pop("doctor_verified_revision", None)
            current_entry.pop("doctor_verified_entrypoint_identities", None)
            current_entry.pop("doctor_verified_entrypoint_identity", None)
        if doctor.get("verified"):
            record_runtime_location(
                _entry_for_revision(current_entry, active_revision),
                location=location,
                expected=expected_location,
            )
        if new_enabled is not None:
            current_entry["enabled"] = new_enabled
        _record_doctor(current_entry, active_revision, doctor)
        _write_state(path, current_state)
        return bool(current_entry.get("enabled"))


def install_extension(
    manifest_path: str | Path,
    *,
    state_file: str | Path,
    operation: str = "install",
    execute: bool = False,
) -> dict[str, object]:
    if operation not in {"install", "upgrade"}:
        raise ValueError("extension operation must be install or upgrade")
    manifest = load_extension_manifest(manifest_path)
    _runtime(manifest)
    provider = _require_mapping_field(
        manifest, "provider", error="extension active manifest is invalid"
    )
    extension_id = str(provider["id"])
    revision = _revision(manifest)
    doctor, location = probe_runtime_location(manifest, execute=execute)
    if execute and not doctor["verified"]:
        raise ValueError(
            f"extension `{extension_id}` doctor is not ready: {doctor['status']}"
        )
    path = Path(state_file).expanduser()
    changed = False
    lock = exclusive_file_lock(path) if execute else nullcontext()
    with lock:
        state = _read_state(path)
        extensions = _extensions_dict(state)
        existing = extensions.get(extension_id)
        if operation == "install" and existing is not None:
            raise ValueError(f"extension `{extension_id}` is already installed")
        if operation == "upgrade" and not isinstance(existing, dict):
            raise ValueError(f"extension `{extension_id}` is not installed")
        if isinstance(existing, dict) and existing.get("active_revision") == revision:
            raise ValueError(f"extension `{extension_id}` revision is already active")
        previous_revision = (
            str(existing.get("active_revision")) if isinstance(existing, dict) else None
        )
        # A fresh install activates the provider. An upgrade changes only the
        # active revision; the operator's enable/disable decision is separate
        # state and survives the revision change.
        enabled = bool(existing.get("enabled")) if isinstance(existing, dict) else True
        if execute:
            raw_revisions = (
                existing.get("revisions") if isinstance(existing, dict) else None
            )
            revisions = (
                [item for item in raw_revisions if isinstance(item, dict)]
                if isinstance(raw_revisions, list)
                else []
            )
            revisions = _retain_revisions(
                revisions,
                {
                    "revision": revision,
                    "version": provider["version"],
                    "manifest": _manifest_snapshot(manifest),
                    **({"entrypoint_path": location} if location is not None else {}),
                },
                required_revision=previous_revision,
            )
            entry_payload: dict[str, object] = {
                "id": extension_id,
                "enabled": enabled,
                "active_revision": revision,
                "rollback_revision": previous_revision,
                "revisions": revisions,
            }
            _record_doctor(entry_payload, revision, doctor)
            extensions[extension_id] = entry_payload
            _write_state(path, state)
            changed = True
    return {
        "ok": True,
        "schema_version": EXTENSION_OPERATION_SCHEMA_VERSION,
        "operation": operation,
        "dry_run": not execute,
        "changed": changed,
        "extension_id": extension_id,
        "version": provider["version"],
        "revision": revision,
        "previous_revision": previous_revision,
        "enabled": enabled if execute else None,
        "doctor": doctor,
        "rollback_available": previous_revision is not None,
    }


def enable_extension(
    extension_id: str,
    *,
    state_file: str | Path,
    execute: bool = False,
) -> dict[str, object]:
    path = Path(state_file).expanduser()
    state = _read_state(path)
    entry = _extensions_dict(state).get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    active_revision, snapshot, manifest = _active_snapshot_and_manifest(entry)
    already_enabled = bool(entry.get("enabled"))
    incompatibility = _manifest_incompatibility(entry, manifest)
    if incompatibility is not None:
        raise ValueError(
            f"{incompatibility}; upgrade to a compatible manifest or roll back"
        )
    doctor, location = probe_runtime_location(
        manifest, location=snapshot.get("entrypoint_path"), execute=execute
    )
    if execute and not doctor["verified"]:
        _commit_locked_doctor_update(
            path,
            extension_id,
            active_revision=active_revision,
            expected_enabled=already_enabled,
            doctor=doctor,
            location=location,
            expected_location=snapshot.get("entrypoint_path"),
            operation_label="enable",
        )
        raise ValueError(
            f"extension `{extension_id}` enable doctor is not ready: {doctor['status']}"
        )
    changed = False
    if execute:
        _commit_locked_doctor_update(
            path,
            extension_id,
            active_revision=active_revision,
            expected_enabled=already_enabled,
            doctor=doctor,
            location=location,
            expected_location=snapshot.get("entrypoint_path"),
            operation_label="enable",
            new_enabled=True,
        )
        changed = not already_enabled
    return {
        "ok": True,
        "schema_version": EXTENSION_OPERATION_SCHEMA_VERSION,
        "operation": "enable",
        "dry_run": not execute,
        "changed": changed,
        "would_change": not already_enabled,
        "extension_id": extension_id,
        "enabled": True if execute else already_enabled,
        "active_revision": active_revision,
        "doctor": doctor,
    }


def disable_extension(
    extension_id: str,
    *,
    state_file: str | Path,
    execute: bool = False,
) -> dict[str, object]:
    path = Path(state_file).expanduser()
    lock = exclusive_file_lock(path) if execute else nullcontext()
    with lock:
        state = _read_state(path)
        entry = _extensions_dict(state).get(extension_id)
        if not isinstance(entry, dict):
            raise ValueError(f"extension `{extension_id}` is not installed")
        changed = bool(entry.get("enabled"))
        if execute and changed:
            entry["enabled"] = False
            _write_state(path, state)
    return {
        "ok": True,
        "schema_version": EXTENSION_OPERATION_SCHEMA_VERSION,
        "operation": "disable",
        "dry_run": not execute,
        "changed": changed if execute else False,
        "would_change": changed,
        "extension_id": extension_id,
        "enabled": False if execute else bool(entry.get("enabled")),
        "active_revision": entry.get("active_revision"),
    }


def rollback_extension(
    extension_id: str,
    *,
    state_file: str | Path,
    execute: bool = False,
) -> dict[str, object]:
    path = Path(state_file).expanduser()
    state = _read_state(path)
    entry = _extensions_dict(state).get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    target_revision = str(entry.get("rollback_revision") or "")
    if not target_revision:
        raise ValueError(f"extension `{extension_id}` has no rollback revision")
    target = _entry_for_revision(entry, target_revision)
    manifest = target.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("extension rollback manifest is invalid")
    target_provider = manifest.get("provider")
    incompatibility = loopx_api_incompatibility(
        target_provider.get("requires_loopx_api")
        if isinstance(target_provider, Mapping)
        else None,
        context=f"extension `{extension_id}` rollback revision `{target_revision}`",
    )
    if incompatibility is not None:
        # Probing the entrypoint cannot admit a snapshot this runtime rejects;
        # refuse before switching rather than leaving an active revision that
        # every reader then fails closed on.
        raise ValueError(
            f"{incompatibility}; roll forward with a compatible manifest instead"
        )
    doctor, location = probe_runtime_location(
        manifest, location=target.get("entrypoint_path"), execute=execute
    )
    if execute and not doctor["verified"]:
        raise ValueError(
            f"extension `{extension_id}` rollback doctor is not ready: {doctor['status']}"
        )
    previous_revision = str(entry.get("active_revision") or "")
    # Dry-run previews the enablement read before the probe; the execute path
    # replaces it with the value committed under the lock below.
    enabled = bool(entry.get("enabled"))
    if execute:
        with exclusive_file_lock(path):
            current_state = _read_state(path)
            current_entry = _extensions_dict(current_state).get(extension_id)
            if not isinstance(current_entry, dict):
                raise ValueError(f"extension `{extension_id}` is not installed")
            if (
                current_entry.get("active_revision") != previous_revision
                or current_entry.get("rollback_revision") != target_revision
            ):
                raise ValueError("extension state changed during rollback; retry")
            record_runtime_location(
                _entry_for_revision(current_entry, target_revision),
                location=location,
                expected=target.get("entrypoint_path"),
            )
            current_entry["active_revision"] = target_revision
            current_entry["rollback_revision"] = previous_revision
            _record_doctor(current_entry, target_revision, doctor)
            # Rollback switches the active revision only; it does not undo an
            # operator's enable/disable decision, including one that completed
            # while the probe ran. The result must report what was committed
            # here, not the pre-probe snapshot.
            enabled = bool(current_entry.get("enabled"))
            _write_state(path, current_state)
    return {
        "ok": True,
        "schema_version": EXTENSION_OPERATION_SCHEMA_VERSION,
        "operation": "rollback",
        "dry_run": not execute,
        "changed": execute,
        "extension_id": extension_id,
        "version": target.get("version"),
        "revision": target_revision,
        "previous_revision": previous_revision,
        "enabled": enabled,
        "doctor": doctor,
    }


def _status_doctor_verified(entry: Mapping[str, object]) -> bool:
    if not entry.get("enabled"):
        return False
    # Resolve proof before parsing the active manifest; a valid proof must
    # still match this runtime's extension API.
    active_revision = str(entry.get("active_revision") or "")
    if entry.get("doctor_verified_revision") != active_revision:
        return False
    try:
        snapshot = _entry_for_revision(entry, active_revision)
    except ValueError:
        return False
    raw_manifest = snapshot.get("manifest")
    if not isinstance(raw_manifest, Mapping):
        return False
    manifest = _coerce_object_dict(raw_manifest)
    verified: ResolvedRuntimeEntrypoint | None = _verified_entrypoint(
        entry,
        active_revision=active_revision,
        snapshot=snapshot,
        manifest=manifest,
    )
    if verified is None:
        return False
    return _manifest_incompatibility(entry, manifest) is None


def extension_status(
    *,
    state_file: str | Path,
    extension_id: str | None = None,
) -> dict[str, object]:
    state = _read_state(Path(state_file).expanduser())
    entries = _extensions_dict(state)
    if extension_id is not None:
        entry = entries.get(extension_id)
        if not isinstance(entry, dict):
            raise ValueError(f"extension `{extension_id}` is not installed")
        visible = [entry]
    else:
        visible = [entry for entry in entries.values() if isinstance(entry, dict)]
    rows: list[dict[str, object]] = []
    for entry in visible:
        raw_revisions = entry.get("revisions")
        revision_count = len(raw_revisions) if isinstance(raw_revisions, list) else 0
        rows.append(
            {
                "id": entry.get("id"),
                "enabled": bool(entry.get("enabled")),
                "active_revision": entry.get("active_revision"),
                "rollback_available": bool(entry.get("rollback_revision")),
                "doctor_verified": _status_doctor_verified(entry),
                "revision_count": revision_count,
            }
        )
    return {
        "ok": True,
        "schema_version": EXTENSION_STATE_SCHEMA_VERSION,
        "extensions": rows,
    }


def doctor_installed_extension(
    extension_id: str,
    *,
    state_file: str | Path,
    execute: bool = False,
) -> dict[str, object]:
    path = Path(state_file).expanduser()
    state = _read_state(path)
    entry = _extensions_dict(state).get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    if not entry.get("enabled"):
        return {
            "ok": True,
            "schema_version": EXTENSION_DOCTOR_SCHEMA_VERSION,
            "extension_id": extension_id,
            "status": "disabled",
            "available": False,
            "verified": False,
            "entrypoint_identity": None,
            "failure_kind": None,
            "external_writes_performed": False,
        }
    active_revision, snapshot, manifest = _active_snapshot_and_manifest(entry)
    incompatibility = _manifest_incompatibility(entry, manifest)
    if incompatibility is not None:
        # The snapshot was valid for the runtime that installed it; this
        # runtime's extension API moved. No probe can repair that, so the
        # doctor fails closed and, when executed, revokes the stale proof.
        raw_provider = manifest.get("provider")
        version = (
            raw_provider.get("version") if isinstance(raw_provider, Mapping) else None
        )
        doctor: dict[str, object] = {
            "ok": True,
            "schema_version": EXTENSION_DOCTOR_SCHEMA_VERSION,
            "extension_id": extension_id,
            "version": version,
            "status": "api_incompatible",
            "available": False,
            "verified": False,
            "entrypoint_identity": None,
            "probed_entrypoint_identity": None,
            "failure_kind": "api_incompatible",
            "reason": incompatibility,
            "external_writes_performed": False,
        }
        location: str | None = None
    else:
        doctor, location = probe_runtime_location(
            manifest, location=snapshot.get("entrypoint_path"), execute=execute
        )
    if execute:
        _commit_locked_doctor_update(
            path,
            extension_id,
            active_revision=active_revision,
            expected_enabled=True,
            doctor=doctor,
            location=location,
            expected_location=snapshot.get("entrypoint_path"),
            operation_label="doctor",
            clear_verified_proof=incompatibility is not None,
        )
    return doctor


def doctor_enabled_extensions(
    *,
    state_file: str | Path,
    execute: bool = False,
) -> dict[str, object]:
    """Revalidate every enabled extension after a runtime release transition.

    Extension doctors are declared read-only provider probes. Running them as
    one bounded batch lets installers and rollback paths refresh the runtime
    identity without weakening the fail-closed activation fence.
    """
    path = Path(state_file).expanduser()
    state = _read_state(path)
    extension_ids = sorted(
        str(extension_id)
        for extension_id, entry in _extensions_dict(state).items()
        if isinstance(entry, Mapping) and entry.get("enabled")
    )
    doctors = [
        doctor_installed_extension(
            extension_id,
            state_file=path,
            execute=execute,
        )
        for extension_id in extension_ids
    ]
    verified_count = sum(bool(doctor.get("verified")) for doctor in doctors)
    blocked_count = len(doctors) - verified_count if execute else 0
    return {
        "ok": blocked_count == 0,
        "schema_version": EXTENSION_DOCTOR_BATCH_SCHEMA_VERSION,
        "status": (
            "ready"
            if execute and blocked_count == 0
            else "blocked"
            if execute
            else "probe_required"
            if doctors
            else "ready"
        ),
        "execute": execute,
        "enabled_count": len(doctors),
        "verified_count": verified_count,
        "blocked_count": blocked_count,
        "extensions": doctors,
        "local_state_write_performed": execute and bool(doctors),
        "external_writes_performed": False,
    }


def _catalog_entry_snapshot(
    manifest: Mapping[str, object],
    lifecycle_state: Mapping[str, object],
) -> ExtensionCatalogEntry:
    normalized = deepcopy(dict(manifest))
    provider_raw = normalized.get("provider")
    provider_map = dict(provider_raw) if isinstance(provider_raw, Mapping) else {}
    provider_map.update(lifecycle_state)
    normalized["provider"] = provider_map
    return cast(ExtensionCatalogEntry, normalized)


def extension_catalog_entries(
    extension_manifest_paths: Iterable[str | Path] = (),
    *,
    state_file: str | Path | None = None,
) -> list[ExtensionCatalogEntry]:
    """Compose declared manifests with installed runtime lifecycle state."""
    manifests: dict[str, Mapping[str, object]] = {}
    lifecycle: dict[str, dict[str, object]] = {}
    for manifest_path in extension_manifest_paths:
        declared_manifest = load_extension_manifest(manifest_path)
        declared_provider = declared_manifest.get("provider")
        if not isinstance(declared_provider, Mapping):
            raise ValueError("extension active manifest is invalid")
        extension_id = str(declared_provider["id"])
        if extension_id in manifests:
            raise ValueError(f"duplicate capability provider `{extension_id}`")
        manifests[extension_id] = declared_manifest
        lifecycle[extension_id] = {
            "declared": True,
            "installed": False,
            "enabled": False,
            "ready": False,
        }

    if state_file is not None:
        state = _read_state(Path(state_file).expanduser())
        for extension_id, entry in _extensions_dict(state).items():
            if not isinstance(entry, Mapping):
                raise ValueError(f"extension `{extension_id}` runtime entry is invalid")
            active_revision, snapshot, active_manifest = _active_snapshot_and_manifest(
                entry
            )
            provider = active_manifest.get("provider")
            if not isinstance(provider, Mapping) or provider.get("id") != extension_id:
                raise ValueError(f"extension `{extension_id}` active manifest is invalid")
            declared = manifests.get(extension_id)
            if declared is not None and _revision(declared) != active_revision:
                # Two different manifests for one id: the installed state is
                # the lifecycle source, so a declaration may only repeat it.
                raise ValueError(
                    f"duplicate capability provider `{extension_id}`: declared "
                    f"manifest revision `{_revision(declared)}` differs from the "
                    f"installed active revision `{active_revision}`"
                )
            manifests[extension_id] = active_manifest
            enabled = bool(entry.get("enabled"))
            compatible = _manifest_incompatibility(entry, active_manifest) is None
            verified = (
                enabled
                and compatible
                and _verified_entrypoint(
                    entry,
                    active_revision=active_revision,
                    snapshot=snapshot,
                    manifest=active_manifest,
                )
                is not None
            )
            lifecycle[extension_id] = {
                "declared": True,
                "installed": True,
                "enabled": enabled,
                "ready": verified,
                "active_revision": active_revision,
            }

    return [
        _catalog_entry_snapshot(manifest, lifecycle[extension_id])
        for extension_id, manifest in manifests.items()
    ]


__all__ = [
    "_verified_identities",
    "disable_extension",
    "doctor_enabled_extensions",
    "doctor_installed_extension",
    "enable_extension",
    "extension_catalog_entries",
    "extension_status",
    "install_extension",
    "rollback_extension",
]
