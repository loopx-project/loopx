from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..file_lock import exclusive_file_lock
from ..paths import select_default_runtime_root
from .manifest import load_extension_manifest
from .process_runtime import run_capped_process
from .runtime_location import located_runtime, probe_runtime_location, record_runtime_location
from .readiness import (
    EXTENSION_DOCTOR_SCHEMA_VERSION,
    ResolvedRuntimeEntrypoint,
    declared_view_validators,
    extension_runtime,
    resolve_runtime_entrypoint,
    runtime_process_environment,
)

EXTENSION_STATE_SCHEMA_VERSION = "loopx_extension_state_v0"
EXTENSION_OPERATION_SCHEMA_VERSION = "loopx_extension_operation_v0"
EXTENSION_BINDING_SCHEMA_VERSION = "loopx_extension_runtime_binding_v0"
EXTENSION_PROVIDER_READINESS_SCHEMA_VERSION = "loopx_extension_provider_readiness_v0"
EXTENSION_ACTIVATION_SCHEMA_VERSION = "loopx_extension_activation_v0"
EXTENSION_RUN_SCHEMA_VERSION = "loopx_extension_run_receipt_v0"
EXTENSION_DOCTOR_BATCH_SCHEMA_VERSION = "loopx_extension_doctor_batch_v0"
MAX_REVISIONS = 5
MAX_EXTENSION_REQUEST_BYTES = 1_000_000
MAX_EXTENSION_RESPONSE_BYTES = 1_000_000


@dataclass(frozen=True)
class ExtensionCapabilityResolution:
    """One atomic lifecycle/readiness snapshot for an optional provider."""

    status: str
    extension_id: str | None
    installed: bool | None
    enabled: bool | None
    doctor_verified: bool | None
    next_action: str | None
    binding: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if (self.status == "ready") != (self.binding is not None):
            raise ValueError(
                "a ready extension capability resolution requires exactly one binding"
            )

    def public_readiness(self) -> dict[str, Any]:
        return {
            "schema_version": EXTENSION_PROVIDER_READINESS_SCHEMA_VERSION,
            "status": self.status,
            "extension_id": self.extension_id,
            "installed": self.installed,
            "enabled": self.enabled,
            "doctor_verified": self.doctor_verified,
            "next_action": self.next_action,
        }


def default_extension_state_file(runtime_root: str | Path | None = None) -> Path:
    root = (
        Path(runtime_root).expanduser()
        if runtime_root is not None
        else select_default_runtime_root()
    )
    return root / "extensions" / "state.json"


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": EXTENSION_STATE_SCHEMA_VERSION,
        "extensions": {},
    }


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("extension runtime state is unreadable") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != EXTENSION_STATE_SCHEMA_VERSION
        or not isinstance(payload.get("extensions"), dict)
    ):
        raise ValueError(
            f"extension runtime state must use {EXTENSION_STATE_SCHEMA_VERSION}"
        )
    return payload


def _write_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _revision(manifest: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _runtime(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    return extension_runtime(manifest)


def _manifest_snapshot(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(manifest))


def _entry_for_revision(entry: Mapping[str, Any], revision: str) -> dict[str, Any]:
    revisions = entry.get("revisions")
    if not isinstance(revisions, list):
        raise ValueError("extension revision history is invalid")
    for item in revisions:
        if isinstance(item, dict) and item.get("revision") == revision:
            return item
    raise ValueError("extension active revision is missing")


def _retain_revisions(
    revisions: list[dict[str, Any]],
    new_revision: dict[str, Any],
    *,
    required_revision: str | None,
) -> list[dict[str, Any]]:
    deduplicated = [
        item
        for item in revisions
        if item.get("revision") != new_revision.get("revision")
    ]
    required = next(
        (
            item
            for item in deduplicated
            if required_revision and item.get("revision") == required_revision
        ),
        None,
    )
    retained = [*deduplicated, new_revision][-MAX_REVISIONS:]
    if required is not None and required not in retained:
        retained = [required, *retained[-(MAX_REVISIONS - 1) :]]
    return retained


def install_extension(
    manifest_path: str | Path,
    *,
    state_file: str | Path,
    operation: str = "install",
    execute: bool = False,
) -> dict[str, Any]:
    if operation not in {"install", "upgrade"}:
        raise ValueError("extension operation must be install or upgrade")
    manifest = load_extension_manifest(manifest_path)
    _runtime(manifest)
    provider = manifest["provider"]
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
        extensions = state["extensions"]
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
        if execute:
            revisions = (
                list(existing.get("revisions") or [])
                if isinstance(existing, dict)
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
            extensions[extension_id] = {
                "id": extension_id,
                "enabled": True,
                "active_revision": revision,
                "rollback_revision": previous_revision,
                "doctor_verified_revision": revision,
                "doctor_verified_entrypoint_identity": doctor["entrypoint_identity"],
                "revisions": revisions,
            }
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
        "enabled": True if execute else None,
        "doctor": doctor,
        "rollback_available": previous_revision is not None,
    }


def enable_extension(
    extension_id: str,
    *,
    state_file: str | Path,
    execute: bool = False,
) -> dict[str, Any]:
    path = Path(state_file).expanduser()
    state = _read_state(path)
    entry = state["extensions"].get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    active_revision = str(entry.get("active_revision") or "")
    snapshot = _entry_for_revision(entry, active_revision)
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("extension active manifest is invalid")
    already_enabled = bool(entry.get("enabled"))
    doctor, location = probe_runtime_location(
        manifest, location=snapshot.get("entrypoint_path"), execute=execute,
    )
    if execute and not doctor["verified"]:
        with exclusive_file_lock(path):
            current_state = _read_state(path)
            current_entry = current_state["extensions"].get(extension_id)
            if (
                not isinstance(current_entry, dict)
                or current_entry.get("active_revision") != active_revision
                or bool(current_entry.get("enabled")) != already_enabled
            ):
                raise ValueError("extension state changed during enable; retry")
            current_entry.pop("doctor_verified_revision", None)
            current_entry.pop("doctor_verified_entrypoint_identity", None)
            _write_state(path, current_state)
        raise ValueError(
            f"extension `{extension_id}` enable doctor is not ready: {doctor['status']}"
        )
    changed = False
    if execute:
        with exclusive_file_lock(path):
            current_state = _read_state(path)
            current_entry = current_state["extensions"].get(extension_id)
            if (
                not isinstance(current_entry, dict)
                or current_entry.get("active_revision") != active_revision
                or bool(current_entry.get("enabled")) != already_enabled
            ):
                raise ValueError("extension state changed during enable; retry")
            record_runtime_location(
                _entry_for_revision(current_entry, active_revision),
                location=location, expected=snapshot.get("entrypoint_path"),
            )
            current_entry["enabled"] = True
            current_entry["doctor_verified_revision"] = active_revision
            current_entry["doctor_verified_entrypoint_identity"] = doctor[
                "entrypoint_identity"
            ]
            _write_state(path, current_state)
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
) -> dict[str, Any]:
    path = Path(state_file).expanduser()
    lock = exclusive_file_lock(path) if execute else nullcontext()
    with lock:
        state = _read_state(path)
        entry = state["extensions"].get(extension_id)
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
) -> dict[str, Any]:
    path = Path(state_file).expanduser()
    state = _read_state(path)
    entry = state["extensions"].get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    target_revision = str(entry.get("rollback_revision") or "")
    if not target_revision:
        raise ValueError(f"extension `{extension_id}` has no rollback revision")
    target = _entry_for_revision(entry, target_revision)
    manifest = target.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("extension rollback manifest is invalid")
    doctor, location = probe_runtime_location(
        manifest, location=target.get("entrypoint_path"), execute=execute,
    )
    if execute and not doctor["verified"]:
        raise ValueError(
            f"extension `{extension_id}` rollback doctor is not ready: {doctor['status']}"
        )
    previous_revision = str(entry.get("active_revision") or "")
    if execute:
        with exclusive_file_lock(path):
            current_state = _read_state(path)
            current_entry = current_state["extensions"].get(extension_id)
            if not isinstance(current_entry, dict):
                raise ValueError(f"extension `{extension_id}` is not installed")
            if (
                current_entry.get("active_revision") != previous_revision
                or current_entry.get("rollback_revision") != target_revision
            ):
                raise ValueError("extension state changed during rollback; retry")
            record_runtime_location(
                _entry_for_revision(current_entry, target_revision),
                location=location, expected=target.get("entrypoint_path"),
            )
            current_entry["active_revision"] = target_revision
            current_entry["rollback_revision"] = previous_revision
            current_entry["doctor_verified_revision"] = target_revision
            current_entry["doctor_verified_entrypoint_identity"] = doctor[
                "entrypoint_identity"
            ]
            current_entry["enabled"] = True
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
        "enabled": True if execute else bool(entry.get("enabled")),
        "doctor": doctor,
    }


def extension_status(
    *,
    state_file: str | Path,
    extension_id: str | None = None,
) -> dict[str, Any]:
    state = _read_state(Path(state_file).expanduser())
    entries = state["extensions"]
    if extension_id is not None:
        entry = entries.get(extension_id)
        if not isinstance(entry, dict):
            raise ValueError(f"extension `{extension_id}` is not installed")
        visible = [entry]
    else:
        visible = [entry for entry in entries.values() if isinstance(entry, dict)]
    return {
        "ok": True,
        "schema_version": EXTENSION_STATE_SCHEMA_VERSION,
        "extensions": [
            {
                "id": entry.get("id"),
                "enabled": bool(entry.get("enabled")),
                "active_revision": entry.get("active_revision"),
                "rollback_available": bool(entry.get("rollback_revision")),
                "doctor_verified": bool(entry.get("enabled"))
                and _verified_entrypoint(entry) is not None,
                "revision_count": len(entry.get("revisions") or []),
            }
            for entry in visible
        ],
    }


def doctor_installed_extension(
    extension_id: str,
    *,
    state_file: str | Path,
    execute: bool = False,
) -> dict[str, Any]:
    path = Path(state_file).expanduser()
    state = _read_state(path)
    entry = state["extensions"].get(extension_id)
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
    active_revision = str(entry.get("active_revision") or "")
    snapshot = _entry_for_revision(entry, active_revision)
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("extension active manifest is invalid")
    doctor, location = probe_runtime_location(
        manifest, location=snapshot.get("entrypoint_path"), execute=execute,
    )
    if execute:
        with exclusive_file_lock(path):
            current_state = _read_state(path)
            current_entry = current_state["extensions"].get(extension_id)
            if (
                not isinstance(current_entry, dict)
                or current_entry.get("active_revision") != active_revision
                or not current_entry.get("enabled")
            ):
                raise ValueError("extension state changed during doctor; retry")
            if doctor["verified"]:
                record_runtime_location(
                    _entry_for_revision(current_entry, active_revision),
                    location=location, expected=snapshot.get("entrypoint_path"),
                )
                current_entry["doctor_verified_revision"] = active_revision
                current_entry["doctor_verified_entrypoint_identity"] = doctor[
                    "entrypoint_identity"
                ]
            else:
                current_entry.pop("doctor_verified_revision", None)
                current_entry.pop("doctor_verified_entrypoint_identity", None)
            _write_state(path, current_state)
    return doctor


def doctor_enabled_extensions(
    *,
    state_file: str | Path,
    execute: bool = False,
) -> dict[str, Any]:
    """Revalidate every enabled extension after a runtime release transition.

    Extension doctors are declared read-only provider probes. Running them as
    one bounded batch lets installers and rollback paths refresh the runtime
    identity without weakening the fail-closed activation fence.
    """

    path = Path(state_file).expanduser()
    state = _read_state(path)
    extension_ids = sorted(
        str(extension_id)
        for extension_id, entry in state["extensions"].items()
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


def _verified_entrypoint(
    entry: Mapping[str, Any],
) -> ResolvedRuntimeEntrypoint | None:
    active_revision = str(entry.get("active_revision") or "")
    if entry.get("doctor_verified_revision") != active_revision:
        return None
    try:
        snapshot = _entry_for_revision(entry, active_revision)
    except ValueError:
        return None
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, Mapping):
        return None
    runtime = located_runtime(manifest, snapshot.get("entrypoint_path"))
    identity = resolve_runtime_entrypoint(
        runtime,
        view_validators=declared_view_validators(manifest),
    )
    if identity is None or identity.identity != entry.get(
        "doctor_verified_entrypoint_identity"
    ):
        return None
    return identity


def _active_manifest(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    active_revision = str(entry.get("active_revision") or "")
    snapshot = _entry_for_revision(entry, active_revision)
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("extension active manifest is invalid")
    return manifest


def extension_catalog_entries(
    extension_manifest_paths: Iterable[str | Path] = (),
    *,
    state_file: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Compose declared manifests with installed runtime lifecycle state."""

    manifests: dict[str, Mapping[str, Any]] = {}
    lifecycle: dict[str, dict[str, Any]] = {}
    for manifest_path in extension_manifest_paths:
        manifest = load_extension_manifest(manifest_path)
        extension_id = str(manifest["provider"]["id"])
        if extension_id in manifests:
            raise ValueError(f"duplicate capability provider `{extension_id}`")
        manifests[extension_id] = manifest
        lifecycle[extension_id] = {
            "declared": True,
            "installed": False,
            "enabled": False,
            "ready": False,
        }

    if state_file is not None:
        state = _read_state(Path(state_file).expanduser())
        for extension_id, entry in state["extensions"].items():
            if not isinstance(entry, Mapping):
                raise ValueError(f"extension `{extension_id}` runtime entry is invalid")
            manifest = _active_manifest(entry)
            provider = manifest.get("provider")
            if not isinstance(provider, Mapping) or provider.get("id") != extension_id:
                raise ValueError(
                    f"extension `{extension_id}` active manifest is invalid"
                )
            manifests[extension_id] = manifest
            enabled = bool(entry.get("enabled"))
            lifecycle[extension_id] = {
                "declared": True,
                "installed": True,
                "enabled": enabled,
                "ready": enabled and _verified_entrypoint(entry) is not None,
                "active_revision": str(entry.get("active_revision") or ""),
            }

    entries: list[dict[str, Any]] = []
    for extension_id, manifest in manifests.items():
        normalized = deepcopy(dict(manifest))
        normalized["provider"] = (
            deepcopy(dict(manifest["provider"])) | lifecycle[extension_id]
        )
        entries.append(normalized)
    return entries


def resolve_capability_extension_id(
    *,
    state_file: str | Path,
    capability_id: str,
    protocol: str,
) -> str:
    """Resolve one ready extension implementation without caller aliases."""

    matching: list[str] = []
    for entry in extension_catalog_entries(state_file=state_file):
        provider = entry.get("provider")
        if not isinstance(provider, Mapping) or not provider.get("ready"):
            continue
        if _matching_capability_implementations(
            entry, capability_id=capability_id, protocol=protocol
        ):
            matching.append(str(entry["provider"]["id"]))
    if not matching:
        raise ValueError(
            f"no enabled, doctor-ready extension implements `{capability_id}` "
            f"with protocol `{protocol}`"
        )
    if len(matching) != 1:
        raise ValueError(
            f"multiple enabled, doctor-ready extensions implement `{capability_id}` "
            f"with protocol `{protocol}`: {matching}"
        )
    return matching[0]


def _resolved_active_extension(
    extension_id: str,
    *,
    state_file: str | Path,
) -> tuple[str, ResolvedRuntimeEntrypoint, Mapping[str, Any]]:
    state = _read_state(Path(state_file).expanduser())
    entry = state["extensions"].get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    if not entry.get("enabled"):
        raise ValueError(f"extension `{extension_id}` is disabled")
    active_revision = str(entry.get("active_revision") or "")
    verified_entrypoint = _verified_entrypoint(entry)
    if verified_entrypoint is None:
        raise ValueError(
            f"extension `{extension_id}` doctor readiness is stale; run "
            f"`loopx extension doctor {extension_id} --execute`"
        )
    manifest = _active_manifest(entry)
    return active_revision, verified_entrypoint, manifest


def resolve_extension_activation(
    extension_id: str,
    *,
    state_file: str | Path,
    required_permissions: Sequence[str] = (),
) -> dict[str, Any]:
    """Resolve one enabled, revision-bound extension compatibility delegate."""

    active_revision, _, manifest = _resolved_active_extension(
        extension_id,
        state_file=state_file,
    )
    provider = manifest.get("provider")
    if not isinstance(provider, Mapping):
        raise ValueError("extension active manifest is incomplete")
    declared_permissions = {
        str(permission)
        for permission in provider.get("permissions") or []
        if str(permission).strip()
    }
    required = {
        str(permission).strip()
        for permission in required_permissions
        if str(permission).strip()
    }
    missing = sorted(required - declared_permissions)
    if missing:
        raise ValueError(
            f"extension `{extension_id}` does not declare permissions {missing}"
        )
    return {
        "schema_version": EXTENSION_ACTIVATION_SCHEMA_VERSION,
        "extension_id": extension_id,
        "provider_version": provider.get("version"),
        "revision": active_revision,
        "enabled": True,
        "doctor_verified": True,
        "required_permissions": sorted(required),
    }


def resolve_extension_binding(
    extension_id: str,
    *,
    state_file: str | Path,
    capability_id: str,
    protocol: str,
    permission: str,
) -> dict[str, Any]:
    state = _read_state(Path(state_file).expanduser())
    entry = state["extensions"].get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    return _capability_binding_from_entry(
        extension_id,
        entry=entry,
        capability_id=capability_id,
        protocol=protocol,
        permission=permission,
    )


def _capability_binding_from_entry(
    extension_id: str,
    *,
    entry: Mapping[str, Any],
    capability_id: str,
    protocol: str,
    permission: str,
    verified_entrypoint: ResolvedRuntimeEntrypoint | None = None,
) -> dict[str, Any]:
    if not entry.get("enabled"):
        raise ValueError(f"extension `{extension_id}` is disabled")
    active_revision = str(entry.get("active_revision") or "")
    verified_entrypoint = verified_entrypoint or _verified_entrypoint(entry)
    if verified_entrypoint is None:
        raise ValueError(
            f"extension `{extension_id}` doctor readiness is stale; run "
            f"`loopx extension doctor {extension_id} --execute`"
        )
    manifest = _active_manifest(entry)
    provider = manifest.get("provider")
    runtime = _runtime(manifest)
    implementations = manifest.get("implementations")
    if not isinstance(provider, Mapping) or not isinstance(implementations, list):
        raise ValueError("extension active manifest is incomplete")
    if permission not in (provider.get("permissions") or []):
        raise ValueError(
            f"extension `{extension_id}` does not declare permission `{permission}`"
        )
    if permission not in (runtime.get("required_permissions") or []):
        raise ValueError(
            f"extension `{extension_id}` runtime does not require permission "
            f"`{permission}`"
        )
    matching = _matching_capability_implementations(
        manifest, capability_id=capability_id, protocol=protocol
    )
    if len(matching) != 1 or runtime.get("protocol") != protocol:
        raise ValueError(
            f"extension `{extension_id}` does not implement `{capability_id}` "
            f"with protocol `{protocol}`"
        )
    return {
        "schema_version": EXTENSION_BINDING_SCHEMA_VERSION,
        "extension_id": extension_id,
        "provider_version": provider.get("version"),
        "revision": active_revision,
        "protocol": protocol,
        "argv": [*verified_entrypoint.argv_prefix, *(runtime.get("args") or [])],
        "environment_path_prefix": verified_entrypoint.path_prefix,
        "doctor_argv": [
            *verified_entrypoint.argv_prefix,
            *(runtime.get("args") or []),
            *(runtime.get("doctor_args") or []),
        ],
        "timeout_seconds": runtime["timeout_seconds"],
    }


def _optional_binding_resolution(
    *,
    status: str,
    extension_id: str | None,
    installed: bool | None,
    enabled: bool | None,
    doctor_verified: bool | None,
    next_action: str | None,
    binding: Mapping[str, Any] | None = None,
) -> ExtensionCapabilityResolution:
    return ExtensionCapabilityResolution(
        status=status,
        extension_id=extension_id,
        installed=installed,
        enabled=enabled,
        doctor_verified=doctor_verified,
        next_action=next_action,
        binding=dict(binding) if binding is not None else None,
    )


def _matching_capability_implementations(
    manifest: Mapping[str, Any],
    *,
    capability_id: str,
    protocol: str,
) -> list[Mapping[str, Any]]:
    implementations = manifest.get("implementations")
    if not isinstance(implementations, list):
        return []
    return [
        item
        for item in implementations
        if isinstance(item, Mapping)
        and item.get("capability_id") == capability_id
        and item.get("protocol") == protocol
    ]


def _optional_binding_for_entry(
    extension_id: str,
    *,
    entry: Mapping[str, Any],
    capability_id: str,
    protocol: str,
    permission: str,
) -> ExtensionCapabilityResolution:
    manifest = _active_manifest(entry)
    provider = manifest.get("provider")
    verified_entrypoint = _verified_entrypoint(entry)
    if (
        not isinstance(provider, Mapping)
        or not _matching_capability_implementations(
            manifest, capability_id=capability_id, protocol=protocol
        )
    ):
        return _optional_binding_resolution(
            status="extension_incompatible",
            extension_id=extension_id,
            installed=True,
            enabled=bool(entry.get("enabled")),
            doctor_verified=verified_entrypoint is not None,
            next_action="install_compatible_extension",
        )
    if not entry.get("enabled"):
        return _optional_binding_resolution(
            status="extension_disabled",
            extension_id=extension_id,
            installed=True,
            enabled=False,
            doctor_verified=False,
            next_action="enable_extension",
        )
    if verified_entrypoint is None:
        return _optional_binding_resolution(
            status="extension_doctor_not_ready",
            extension_id=extension_id,
            installed=True,
            enabled=True,
            doctor_verified=False,
            next_action="run_extension_doctor",
        )
    try:
        binding = _capability_binding_from_entry(
            extension_id,
            entry=entry,
            capability_id=capability_id,
            protocol=protocol,
            permission=permission,
            verified_entrypoint=verified_entrypoint,
        )
    except ValueError:
        return _optional_binding_resolution(
            status="extension_incompatible",
            extension_id=extension_id,
            installed=True,
            enabled=True,
            doctor_verified=True,
            next_action="install_compatible_extension",
        )
    return _optional_binding_resolution(
        status="ready",
        extension_id=extension_id,
        installed=True,
        enabled=True,
        doctor_verified=True,
        next_action=None,
        binding=binding,
    )


def resolve_optional_capability_binding(
    *,
    state_file: str | Path,
    capability_id: str,
    protocol: str,
    permission: str,
    extension_id: str | None = None,
) -> ExtensionCapabilityResolution:
    """Resolve selection, lifecycle readiness, and binding from one state read."""

    try:
        state = _read_state(Path(state_file).expanduser())
        entries = state["extensions"]
        if extension_id is not None:
            entry = entries.get(extension_id)
            if not isinstance(entry, Mapping):
                return _optional_binding_resolution(
                    status="extension_not_installed",
                    extension_id=extension_id,
                    installed=False,
                    enabled=False,
                    doctor_verified=False,
                    next_action="install_extension",
                )
            return _optional_binding_for_entry(
                extension_id,
                entry=entry,
                capability_id=capability_id,
                protocol=protocol,
                permission=permission,
            )

        candidates: list[tuple[str, Mapping[str, Any]]] = []
        for candidate_id, candidate_entry in entries.items():
            if not isinstance(candidate_entry, Mapping):
                raise ValueError(
                    f"extension `{candidate_id}` runtime entry is invalid"
                )
            manifest = _active_manifest(candidate_entry)
            if _matching_capability_implementations(
                manifest, capability_id=capability_id, protocol=protocol
            ):
                candidates.append((str(candidate_id), candidate_entry))
        resolutions = [
            _optional_binding_for_entry(
                candidate_id,
                entry=candidate_entry,
                capability_id=capability_id,
                protocol=protocol,
                permission=permission,
            )
            for candidate_id, candidate_entry in candidates
        ]
    except (OSError, ValueError):
        return _optional_binding_resolution(
            status="extension_state_unavailable",
            extension_id=extension_id,
            installed=None,
            enabled=None,
            doctor_verified=None,
            next_action="repair_extension_state",
        )

    ready = [item for item in resolutions if item.status == "ready"]
    if len(ready) == 1:
        return ready[0]
    if len(ready) > 1:
        return _optional_binding_resolution(
            status="extension_provider_ambiguous",
            extension_id=None,
            installed=True,
            enabled=True,
            doctor_verified=True,
            next_action="select_extension_id",
        )
    if len(resolutions) == 1:
        return resolutions[0]
    return _optional_binding_resolution(
        status=(
            "extension_provider_not_installed"
            if not resolutions
            else "extension_provider_not_ready"
        ),
        extension_id=None,
        installed=bool(resolutions),
        enabled=any(item.enabled is True for item in resolutions),
        doctor_verified=False,
        next_action=(
            "install_or_select_extension"
            if not resolutions
            else "select_or_repair_extension"
        ),
    )


def resolve_extension_runtime_binding(
    extension_id: str,
    *,
    state_file: str | Path,
    protocol: str,
    permission: str,
) -> dict[str, Any]:
    """Resolve an extension runtime without coupling dispatch to capability metadata."""

    active_revision, verified_entrypoint, manifest = _resolved_active_extension(
        extension_id,
        state_file=state_file,
    )
    provider = manifest.get("provider")
    runtime = _runtime(manifest)
    if not isinstance(provider, Mapping):
        raise ValueError("extension active manifest is incomplete")
    if permission not in (provider.get("permissions") or []):
        raise ValueError(
            f"extension `{extension_id}` does not declare permission `{permission}`"
        )
    if permission not in (runtime.get("required_permissions") or []):
        raise ValueError(
            f"extension `{extension_id}` runtime does not require permission "
            f"`{permission}`"
        )
    if runtime.get("protocol") != protocol:
        raise ValueError(
            f"extension `{extension_id}` does not expose runtime protocol `{protocol}`"
        )
    return {
        "schema_version": EXTENSION_BINDING_SCHEMA_VERSION,
        "extension_id": extension_id,
        "provider_version": provider.get("version"),
        "revision": active_revision,
        "protocol": protocol,
        "argv": [*verified_entrypoint.argv_prefix, *(runtime.get("args") or [])],
        "environment_path_prefix": verified_entrypoint.path_prefix,
        "doctor_argv": [
            *verified_entrypoint.argv_prefix,
            *(runtime.get("args") or []),
            *(runtime.get("doctor_args") or []),
        ],
        "timeout_seconds": runtime["timeout_seconds"],
    }


def _serialize_extension_request(
    request: Mapping[str, Any],
    *,
    operation: str,
) -> bytes:
    try:
        request_bytes = json.dumps(
            dict(request),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"extension {operation} request must be JSON serializable"
        ) from exc
    if len(request_bytes) > MAX_EXTENSION_REQUEST_BYTES:
        raise ValueError(
            f"extension {operation} request exceeds the 1000000-byte limit"
        )
    return request_bytes


def _parse_extension_response(response: bytes) -> tuple[bool, object]:
    try:
        return True, json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, None


def run_standalone_extension(
    extension_id: str,
    *,
    state_file: str | Path,
    request: Mapping[str, Any],
    execute: bool = False,
) -> dict[str, Any]:
    """Run one lifecycle-gated standalone extension over bounded JSON stdin/stdout."""

    active_revision, verified_entrypoint, manifest = _resolved_active_extension(
        extension_id,
        state_file=state_file,
    )
    provider = manifest.get("provider")
    runtime = _runtime(manifest)
    if not isinstance(provider, Mapping):
        raise ValueError("extension active manifest is incomplete")
    if manifest.get("capabilities") or manifest.get("implementations"):
        raise ValueError(
            "extension run only accepts standalone extensions; invoke capability "
            "providers through their capability or domain command"
        )
    if not isinstance(request, Mapping):
        raise ValueError("extension run request must be a JSON object")

    required_permissions = [
        str(value) for value in runtime.get("required_permissions") or []
    ]
    declared_permissions = {str(value) for value in provider.get("permissions") or []}
    missing_permissions = sorted(set(required_permissions) - declared_permissions)
    if missing_permissions:
        raise ValueError(
            f"extension `{extension_id}` does not declare permissions "
            f"{missing_permissions}"
        )
    if declared_permissions:
        raise ValueError(
            f"extension `{extension_id}` declares permissions "
            f"{sorted(declared_permissions)}; standalone extension run grants no "
            "effect dispatch, so use a capability or domain command that owns "
            "policy checks and a request-bound execution envelope"
        )

    request_bytes = _serialize_extension_request(request, operation="run")

    receipt: dict[str, Any] = {
        "ok": True,
        "schema_version": EXTENSION_RUN_SCHEMA_VERSION,
        "operation": "run",
        "extension_id": extension_id,
        "provider_version": provider.get("version"),
        "revision": active_revision,
        "protocol": runtime["protocol"],
        "required_permissions": required_permissions,
        "dry_run": not execute,
        "executed": False,
        "status": "ready" if not execute else "running",
        "input_schema_version": request.get("schema_version"),
    }
    if not execute:
        return receipt

    argv = [*verified_entrypoint.argv_prefix, *(runtime.get("args") or [])]
    try:
        completed = run_capped_process(
            argv,
            stdin=request_bytes,
            timeout_seconds=int(runtime["timeout_seconds"]),
            output_limit_bytes=MAX_EXTENSION_RESPONSE_BYTES,
            env=runtime_process_environment(verified_entrypoint.path_prefix),
        )
    except OSError:
        return {
            **receipt,
            "ok": False,
            "executed": True,
            "status": "provider_failed",
            "failure_kind": "execution_failed",
            "exit_code": None,
        }
    if completed.failure_kind is not None:
        response_too_large = completed.failure_kind == "response_too_large"
        return {
            **receipt,
            "ok": False,
            "executed": True,
            "status": (
                "invalid_provider_output" if response_too_large else "provider_failed"
            ),
            "failure_kind": completed.failure_kind,
            "exit_code": (
                None if completed.failure_kind == "timeout" else completed.returncode
            ),
        }

    parsed, provider_result = _parse_extension_response(completed.stdout)
    if not parsed or not isinstance(provider_result, dict):
        return {
            **receipt,
            "ok": False,
            "executed": True,
            "status": "invalid_provider_output",
            "failure_kind": "response_not_json_object",
            "exit_code": completed.returncode,
        }
    succeeded = completed.returncode == 0 and provider_result.get("ok") is not False
    return {
        **receipt,
        "ok": succeeded,
        "executed": True,
        "status": "succeeded" if succeeded else "provider_failed",
        "exit_code": completed.returncode,
        "provider_result": provider_result,
    }


def resolve_capability_binding(
    *,
    state_file: str | Path,
    capability_id: str,
    protocol: str,
    permission: str,
) -> dict[str, Any]:
    extension_id = resolve_capability_extension_id(
        state_file=state_file,
        capability_id=capability_id,
        protocol=protocol,
    )
    return resolve_extension_binding(
        extension_id,
        state_file=state_file,
        capability_id=capability_id,
        protocol=protocol,
        permission=permission,
    )


def _runtime_binding_invocation(
    binding: Mapping[str, Any],
) -> tuple[list[str], float]:
    if binding.get("schema_version") != EXTENSION_BINDING_SCHEMA_VERSION:
        raise ValueError(
            f"extension runtime binding must use {EXTENSION_BINDING_SCHEMA_VERSION}"
        )
    argv = binding.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
    ):
        raise ValueError("extension runtime binding argv is invalid")
    raw_timeout = binding.get("timeout_seconds")
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
        raise ValueError("extension runtime binding timeout is invalid")
    timeout_seconds = float(raw_timeout)
    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 120:
        raise ValueError("extension runtime binding timeout is invalid")
    return argv, timeout_seconds


def execute_extension_runtime_binding(
    binding: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute a resolved binding after its capability authorizes the request."""

    argv, timeout_seconds = _runtime_binding_invocation(binding)
    request_bytes = _serialize_extension_request(request, operation="runtime")
    try:
        completed = run_capped_process(
            argv,
            stdin=request_bytes,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=MAX_EXTENSION_RESPONSE_BYTES,
            env=runtime_process_environment(binding.get("environment_path_prefix"), environment),
        )
    except OSError as exc:
        raise RuntimeError("extension provider execution failed") from exc
    if completed.failure_kind is not None:
        raise RuntimeError(
            f"extension provider execution failed: {completed.failure_kind}"
        )
    parsed, provider_result = _parse_extension_response(completed.stdout)
    if not parsed:
        raise RuntimeError("extension provider returned invalid JSON")
    if not isinstance(provider_result, dict):
        raise RuntimeError("extension provider returned a non-object")
    if completed.returncode != 0 or provider_result.get("ok") is False:
        raise RuntimeError("extension provider reported failure")
    return provider_result
