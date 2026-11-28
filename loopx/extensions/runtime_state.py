"""Extension runtime state persistence, revision retention, and readiness helpers."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import TypedDict

from ..paths import select_default_runtime_root
from .manifest import loopx_api_incompatibility
from .readiness import (
    ResolvedRuntimeEntrypoint,
    declared_view_validators,
    extension_runtime,
    resolve_runtime_entrypoint,
)
from .runtime_location import located_runtime

EXTENSION_STATE_SCHEMA_VERSION = "loopx_extension_state_v0"
EXTENSION_OPERATION_SCHEMA_VERSION = "loopx_extension_operation_v0"
EXTENSION_BINDING_SCHEMA_VERSION = "loopx_extension_runtime_binding_v0"
EXTENSION_PROVIDER_READINESS_SCHEMA_VERSION = "loopx_extension_provider_readiness_v0"
EXTENSION_ACTIVATION_SCHEMA_VERSION = "loopx_extension_activation_v0"
EXTENSION_RUN_SCHEMA_VERSION = "loopx_extension_run_receipt_v0"
EXTENSION_DOCTOR_BATCH_SCHEMA_VERSION = "loopx_extension_doctor_batch_v0"
# Content-free lifecycle non-admission codes. Optional capability resolution
# reports them as `ExtensionCapabilityResolution.status`; hook-adapter discovery
# reuses them for declared adapters whose extension it does not activate.
EXTENSION_DISABLED_STATUS = "extension_disabled"
EXTENSION_DOCTOR_NOT_READY_STATUS = "extension_doctor_not_ready"
MAX_REVISIONS = 5
# Requalification is required on eviction; unprobed artifacts never activate.
MAX_VERIFIED_ENTRYPOINTS = 32
MAX_EXTENSION_REQUEST_BYTES = 1_000_000
MAX_EXTENSION_RESPONSE_BYTES = 1_000_000


class ExtensionRuntimeBinding(TypedDict):
    """Strict runtime binding packet resolved from an active extension manifest."""

    schema_version: str
    extension_id: str
    provider_version: object
    revision: str
    protocol: str
    argv: list[str]
    environment_path_prefix: str | None
    doctor_argv: list[str]
    timeout_seconds: int | float


class ExtensionHookAdapterEntry(TypedDict, total=False):
    """Manifest hook adapter record consumed by hook adapter discovery."""

    id: str
    phase: str
    capability_id: str
    target_hook_id: str
    factory: str
    required_permissions: list[str]
    ports: list[str]


class _ExtensionCatalogEntryBase(TypedDict):
    provider: dict[str, object]
    capabilities: list[dict[str, object]]
    implementations: list[dict[str, object]]


class ExtensionCatalogEntry(_ExtensionCatalogEntryBase, total=False):
    """Typed extension manifest enriched with lifecycle readiness metadata."""

    runtime: dict[str, object] | None
    hook_adapters: list[ExtensionHookAdapterEntry]
    presentation_surfaces: list[dict[str, object]]
    documentation: dict[str, object] | None


@dataclass(frozen=True)
class ExtensionCapabilityResolution:
    """One atomic lifecycle/readiness snapshot for an optional provider."""

    status: str
    extension_id: str | None
    installed: bool | None
    enabled: bool | None
    doctor_verified: bool | None
    next_action: str | None
    binding: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if (self.status == "ready") != (self.binding is not None):
            raise ValueError(
                "a ready extension capability resolution requires exactly one binding"
            )

    def public_readiness(self) -> dict[str, object]:
        return {
            "schema_version": EXTENSION_PROVIDER_READINESS_SCHEMA_VERSION,
            "status": self.status,
            "extension_id": self.extension_id,
            "installed": self.installed,
            "enabled": self.enabled,
            "doctor_verified": self.doctor_verified,
            "next_action": self.next_action,
        }


def _coerce_object_dict(mapping: Mapping[object, object]) -> dict[str, object]:
    return {str(key): value for key, value in mapping.items()}


def _coerce_runtime_mapping(mapping: Mapping[object, object]) -> Mapping[str, object]:
    if all(isinstance(key, str) for key in mapping):
        return {str(key): value for key, value in mapping.items()}
    return _coerce_object_dict(mapping)


def default_extension_state_file(runtime_root: str | Path | None = None) -> Path:
    root = (
        Path(runtime_root).expanduser()
        if runtime_root is not None
        else select_default_runtime_root()
    )
    return root / "extensions" / "state.json"


def _empty_state() -> dict[str, object]:
    return {
        "schema_version": EXTENSION_STATE_SCHEMA_VERSION,
        "extensions": {},
    }


def _read_state(path: Path) -> dict[str, object]:
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
    return _coerce_object_dict(payload)


def _extensions_dict(state: Mapping[str, object]) -> dict[str, object]:
    raw = state.get("extensions")
    if not isinstance(raw, dict):
        raise ValueError(
            f"extension runtime state must use {EXTENSION_STATE_SCHEMA_VERSION}"
        )
    return raw


def _write_state(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _revision(manifest: Mapping[str, object]) -> str:
    serialized = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _runtime(manifest: Mapping[str, object]) -> Mapping[str, object]:
    return extension_runtime(manifest)


def _manifest_snapshot(manifest: Mapping[str, object]) -> dict[str, object]:
    return deepcopy(dict(manifest))


def _entry_for_revision(entry: Mapping[str, object], revision: str) -> dict[str, object]:
    revisions = entry.get("revisions")
    if not isinstance(revisions, list):
        raise ValueError("extension revision history is invalid")
    for item in revisions:
        if isinstance(item, dict) and item.get("revision") == revision:
            return item
    raise ValueError("extension active revision is missing")


def _retain_revisions(
    revisions: list[dict[str, object]],
    new_revision: dict[str, object],
    *,
    required_revision: str | None,
) -> list[dict[str, object]]:
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


def _verified_identities(entry: Mapping[str, object], revision: str) -> list[str]:
    if entry.get("doctor_verified_revision") != revision:
        return []
    identities = entry.get("doctor_verified_entrypoint_identities")
    if identities is None:
        # Read legacy state without rewriting it during status/activation.
        identities = [entry.get("doctor_verified_entrypoint_identity")]
    if not isinstance(identities, list):
        return []
    return list(
        dict.fromkeys(
            identity for identity in identities if isinstance(identity, str) and identity
        )
    )[-MAX_VERIFIED_ENTRYPOINTS:]


def _record_doctor(
    entry: dict[str, object], revision: str, doctor: Mapping[str, object]
) -> None:
    identities = _verified_identities(entry, revision)
    identity = doctor.get("probed_entrypoint_identity")
    if doctor.get("verified") is True:
        identity = doctor.get("entrypoint_identity")
    if isinstance(identity, str) and identity:
        identities = [value for value in identities if value != identity]
        if doctor.get("verified") is True:
            identities.append(identity)
    entry.pop("doctor_verified_entrypoint_identity", None)
    if identities:
        entry["doctor_verified_revision"] = revision
        entry["doctor_verified_entrypoint_identities"] = identities[
            -MAX_VERIFIED_ENTRYPOINTS:
        ]
    else:
        entry.pop("doctor_verified_revision", None)
        entry.pop("doctor_verified_entrypoint_identities", None)


def _active_snapshot_and_manifest(
    entry: Mapping[str, object],
) -> tuple[str, dict[str, object], Mapping[str, object]]:
    active_revision = str(entry.get("active_revision") or "")
    snapshot = _entry_for_revision(entry, active_revision)
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("extension active manifest is invalid")
    return active_revision, snapshot, _coerce_runtime_mapping(manifest)


def _verified_entrypoint(
    entry: Mapping[str, object],
    *,
    active_revision: str | None = None,
    snapshot: Mapping[str, object] | None = None,
    manifest: Mapping[str, object] | None = None,
) -> ResolvedRuntimeEntrypoint | None:
    resolved_revision = (
        str(entry.get("active_revision") or "")
        if active_revision is None
        else active_revision
    )
    if entry.get("doctor_verified_revision") != resolved_revision:
        return None
    if snapshot is None:
        try:
            snapshot = _entry_for_revision(entry, resolved_revision)
        except ValueError:
            return None
    resolved_manifest: Mapping[str, object]
    if manifest is None:
        raw_manifest = snapshot.get("manifest")
        if not isinstance(raw_manifest, Mapping):
            return None
        resolved_manifest = _coerce_runtime_mapping(raw_manifest)
    else:
        resolved_manifest = manifest
    runtime = located_runtime(resolved_manifest, snapshot.get("entrypoint_path"))
    identity = resolve_runtime_entrypoint(
        runtime,
        view_validators=declared_view_validators(resolved_manifest),
    )
    if identity is None or identity.identity not in _verified_identities(
        entry, resolved_revision
    ):
        return None
    return identity


def _active_manifest(entry: Mapping[str, object]) -> Mapping[str, object]:
    _, _, manifest = _active_snapshot_and_manifest(entry)
    return manifest


def _manifest_incompatibility(
    entry: Mapping[str, object],
    manifest: Mapping[str, object],
) -> str | None:
    provider = manifest.get("provider")
    if not isinstance(provider, Mapping):
        return "extension active manifest is incomplete"
    extension_id = str(provider.get("id") or entry.get("id") or "")
    return loopx_api_incompatibility(
        provider.get("requires_loopx_api"),
        context=f"extension `{extension_id}` active manifest",
    )


def _active_manifest_incompatibility(entry: Mapping[str, object]) -> str | None:
    """Re-check the installed snapshot against the runtime reading it.

    `install`/`upgrade` validate `requires_loopx_api` when they load the
    manifest, but the saved copy outlives that runtime. A later LoopX whose
    extension API moved must treat the snapshot as incompatible, not ready.
    """
    return _manifest_incompatibility(entry, _active_manifest(entry))


def _resolved_active_extension(
    extension_id: str,
    *,
    state_file: str | Path,
) -> tuple[str, ResolvedRuntimeEntrypoint, Mapping[str, object]]:
    state = _read_state(Path(state_file).expanduser())
    entry = _extensions_dict(state).get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    if not entry.get("enabled"):
        raise ValueError(f"extension `{extension_id}` is disabled")
    active_revision, snapshot, manifest = _active_snapshot_and_manifest(entry)
    incompatibility = _manifest_incompatibility(entry, manifest)
    if incompatibility is not None:
        raise ValueError(
            f"{incompatibility}; upgrade to a compatible manifest or roll back"
        )
    verified_entrypoint = _verified_entrypoint(
        entry,
        active_revision=active_revision,
        snapshot=snapshot,
        manifest=manifest,
    )
    if verified_entrypoint is None:
        raise ValueError(
            f"extension `{extension_id}` doctor readiness is stale; run "
            f"`loopx extension doctor {extension_id} --execute`"
        )
    return active_revision, verified_entrypoint, manifest
