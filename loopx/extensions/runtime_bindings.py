"""Capability/extension binding resolution without duplicate state walks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from .readiness import ResolvedRuntimeEntrypoint
from .runtime_state import (
    EXTENSION_ACTIVATION_SCHEMA_VERSION,
    EXTENSION_BINDING_SCHEMA_VERSION,
    EXTENSION_DISABLED_STATUS,
    EXTENSION_DOCTOR_NOT_READY_STATUS,
    ExtensionCapabilityResolution,
    ExtensionRuntimeBinding,
    _active_snapshot_and_manifest,
    _coerce_object_dict,
    _extensions_dict,
    _manifest_incompatibility,
    _read_state,
    _resolved_active_extension,
    _runtime,
    _verified_entrypoint,
)


def _matching_capability_implementations(
    manifest: Mapping[str, object],
    *,
    capability_id: str,
    protocol: str,
) -> list[Mapping[str, object]]:
    implementations = manifest.get("implementations")
    if not isinstance(implementations, list):
        return []
    return [
        _coerce_object_dict(item)
        for item in implementations
        if isinstance(item, Mapping)
        and item.get("capability_id") == capability_id
        and item.get("protocol") == protocol
    ]


def _validate_runtime_binding_access(
    extension_id: str,
    *,
    provider: Mapping[str, object],
    runtime: Mapping[str, object],
    protocol: str,
    permission: str,
    mismatch_message: str,
) -> None:
    raw_permissions = provider.get("permissions")
    declared_permissions = raw_permissions if isinstance(raw_permissions, list) else []
    if permission not in declared_permissions:
        raise ValueError(
            f"extension `{extension_id}` does not declare permission `{permission}`"
        )
    raw_required = runtime.get("required_permissions")
    required_permissions = raw_required if isinstance(raw_required, list) else []
    if permission not in required_permissions:
        raise ValueError(
            f"extension `{extension_id}` runtime does not require permission "
            f"`{permission}`"
        )
    if runtime.get("protocol") != protocol:
        raise ValueError(mismatch_message)


def _assemble_runtime_binding(
    extension_id: str,
    *,
    provider: Mapping[str, object],
    runtime: Mapping[str, object],
    active_revision: str,
    verified_entrypoint: ResolvedRuntimeEntrypoint,
    protocol: str,
) -> ExtensionRuntimeBinding:
    raw_args = runtime.get("args")
    args = [str(value) for value in raw_args] if isinstance(raw_args, list) else []
    raw_doctor_args = runtime.get("doctor_args")
    doctor_args = (
        [str(value) for value in raw_doctor_args]
        if isinstance(raw_doctor_args, list)
        else []
    )
    raw_timeout = runtime["timeout_seconds"]
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
        raise ValueError("extension runtime timeout is invalid")
    return ExtensionRuntimeBinding(
        schema_version=EXTENSION_BINDING_SCHEMA_VERSION,
        extension_id=extension_id,
        provider_version=provider.get("version"),
        revision=active_revision,
        protocol=protocol,
        argv=[*verified_entrypoint.argv_prefix, *args],
        environment_path_prefix=verified_entrypoint.path_prefix,
        doctor_argv=[*verified_entrypoint.argv_prefix, *args, *doctor_args],
        timeout_seconds=raw_timeout,
    )


def _ready_capability_matches_in_state(
    state: Mapping[str, object],
    *,
    capability_id: str,
    protocol: str,
) -> list[tuple[str, dict[str, object], str, Mapping[str, object], ResolvedRuntimeEntrypoint]]:
    matches: list[
        tuple[str, dict[str, object], str, Mapping[str, object], ResolvedRuntimeEntrypoint]
    ] = []
    for extension_id, entry in _extensions_dict(state).items():
        if not isinstance(entry, dict):
            raise ValueError(f"extension `{extension_id}` runtime entry is invalid")
        active_revision, snapshot, manifest = _active_snapshot_and_manifest(entry)
        provider = manifest.get("provider")
        if not isinstance(provider, Mapping) or provider.get("id") != extension_id:
            raise ValueError(f"extension `{extension_id}` active manifest is invalid")
        if not entry.get("enabled"):
            continue
        if _manifest_incompatibility(entry, manifest) is not None:
            continue
        verified = _verified_entrypoint(
            entry,
            active_revision=active_revision,
            snapshot=snapshot,
            manifest=manifest,
        )
        if verified is None:
            continue
        if _matching_capability_implementations(
            manifest, capability_id=capability_id, protocol=protocol
        ):
            matches.append(
                (str(provider["id"]), entry, active_revision, manifest, verified)
            )
    return matches


def _select_single_capability_match(
    matches: list[
        tuple[str, dict[str, object], str, Mapping[str, object], ResolvedRuntimeEntrypoint]
    ],
    *,
    capability_id: str,
    protocol: str,
) -> tuple[str, dict[str, object], str, Mapping[str, object], ResolvedRuntimeEntrypoint]:
    matching_ids = [item[0] for item in matches]
    if not matching_ids:
        raise ValueError(
            f"no enabled, doctor-ready extension implements `{capability_id}` "
            f"with protocol `{protocol}`"
        )
    if len(matching_ids) != 1:
        raise ValueError(
            f"multiple enabled, doctor-ready extensions implement `{capability_id}` "
            f"with protocol `{protocol}`: {matching_ids}"
        )
    return matches[0]


def resolve_capability_extension_id(
    *,
    state_file: str | Path,
    capability_id: str,
    protocol: str,
) -> str:
    """Resolve one ready extension implementation without caller aliases."""
    state = _read_state(Path(state_file).expanduser())
    matches = _ready_capability_matches_in_state(
        state, capability_id=capability_id, protocol=protocol
    )
    selected = _select_single_capability_match(
        matches, capability_id=capability_id, protocol=protocol
    )
    return selected[0]


def resolve_extension_activation(
    extension_id: str,
    *,
    state_file: str | Path,
    required_permissions: Sequence[str] = (),
) -> dict[str, object]:
    """Resolve one enabled, revision-bound extension compatibility delegate."""
    active_revision, _, manifest = _resolved_active_extension(
        extension_id,
        state_file=state_file,
    )
    provider = manifest.get("provider")
    if not isinstance(provider, Mapping):
        raise ValueError("extension active manifest is incomplete")
    raw_permissions = provider.get("permissions")
    declared_permissions = {
        str(permission)
        for permission in (raw_permissions if isinstance(raw_permissions, list) else [])
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


def _capability_binding_from_entry(
    extension_id: str,
    *,
    entry: Mapping[str, object],
    capability_id: str,
    protocol: str,
    permission: str,
    active_revision: str | None = None,
    manifest: Mapping[str, object] | None = None,
    incompatibility_checked: bool = False,
    verified_entrypoint: ResolvedRuntimeEntrypoint | None = None,
    matching_checked: bool = False,
) -> ExtensionRuntimeBinding:
    if not entry.get("enabled"):
        raise ValueError(f"extension `{extension_id}` is disabled")
    if manifest is None or active_revision is None:
        active_revision, snapshot, manifest = _active_snapshot_and_manifest(entry)
    else:
        snapshot = None
    if not incompatibility_checked:
        incompatibility = _manifest_incompatibility(entry, manifest)
        if incompatibility is not None:
            raise ValueError(
                f"{incompatibility}; upgrade to a compatible manifest or roll back"
            )
    if verified_entrypoint is None:
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
    provider = manifest.get("provider")
    runtime = _runtime(manifest)
    implementations = manifest.get("implementations")
    if not isinstance(provider, Mapping) or not isinstance(implementations, list):
        raise ValueError("extension active manifest is incomplete")
    mismatch = (
        f"extension `{extension_id}` does not implement `{capability_id}` "
        f"with protocol `{protocol}`"
    )
    _validate_runtime_binding_access(
        extension_id,
        provider=provider,
        runtime=runtime,
        protocol=protocol,
        permission=permission,
        mismatch_message=mismatch,
    )
    if not matching_checked:
        matching = _matching_capability_implementations(
            manifest, capability_id=capability_id, protocol=protocol
        )
        if len(matching) != 1:
            raise ValueError(mismatch)
    return _assemble_runtime_binding(
        extension_id,
        provider=provider,
        runtime=runtime,
        active_revision=active_revision,
        verified_entrypoint=verified_entrypoint,
        protocol=protocol,
    )


def resolve_extension_binding(
    extension_id: str,
    *,
    state_file: str | Path,
    capability_id: str,
    protocol: str,
    permission: str,
) -> ExtensionRuntimeBinding:
    state = _read_state(Path(state_file).expanduser())
    entry = _extensions_dict(state).get(extension_id)
    if not isinstance(entry, dict):
        raise ValueError(f"extension `{extension_id}` is not installed")
    return _capability_binding_from_entry(
        extension_id,
        entry=entry,
        capability_id=capability_id,
        protocol=protocol,
        permission=permission,
    )


def _optional_binding_resolution(
    *,
    status: str,
    extension_id: str | None,
    installed: bool | None,
    enabled: bool | None,
    doctor_verified: bool | None,
    next_action: str | None,
    binding: Mapping[str, object] | None = None,
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


def _optional_binding_for_entry(
    extension_id: str,
    *,
    entry: Mapping[str, object],
    capability_id: str,
    protocol: str,
    permission: str,
    active_revision: str | None = None,
    snapshot: Mapping[str, object] | None = None,
    manifest: Mapping[str, object] | None = None,
    matching_implementations: list[Mapping[str, object]] | None = None,
) -> ExtensionCapabilityResolution:
    if active_revision is None or manifest is None:
        active_revision, snapshot, manifest = _active_snapshot_and_manifest(entry)
    provider = manifest.get("provider")
    verified_entrypoint = _verified_entrypoint(
        entry,
        active_revision=active_revision,
        snapshot=snapshot,
        manifest=manifest,
    )
    matches = (
        matching_implementations
        if matching_implementations is not None
        else _matching_capability_implementations(
            manifest, capability_id=capability_id, protocol=protocol
        )
    )
    if (
        not isinstance(provider, Mapping)
        or _manifest_incompatibility(entry, manifest) is not None
        or not matches
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
            status=EXTENSION_DISABLED_STATUS,
            extension_id=extension_id,
            installed=True,
            enabled=False,
            doctor_verified=False,
            next_action="enable_extension",
        )
    if verified_entrypoint is None:
        return _optional_binding_resolution(
            status=EXTENSION_DOCTOR_NOT_READY_STATUS,
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
            active_revision=active_revision,
            manifest=manifest,
            incompatibility_checked=True,
            verified_entrypoint=verified_entrypoint,
            matching_checked=len(matches) == 1,
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
    return ExtensionCapabilityResolution(
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
        entries = _extensions_dict(state)
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

        resolutions: list[ExtensionCapabilityResolution] = []
        for candidate_id, candidate_entry in entries.items():
            if not isinstance(candidate_entry, Mapping):
                raise ValueError(
                    f"extension `{candidate_id}` runtime entry is invalid"
                )
            active_rev, candidate_snap, manifest = _active_snapshot_and_manifest(
                candidate_entry
            )
            matches = _matching_capability_implementations(
                manifest, capability_id=capability_id, protocol=protocol
            )
            if matches:
                resolutions.append(
                    _optional_binding_for_entry(
                        str(candidate_id),
                        entry=candidate_entry,
                        capability_id=capability_id,
                        protocol=protocol,
                        permission=permission,
                        active_revision=active_rev,
                        snapshot=candidate_snap,
                        manifest=manifest,
                        matching_implementations=matches,
                    )
                )
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
) -> ExtensionRuntimeBinding:
    """Resolve an extension runtime without coupling dispatch to capability metadata."""
    active_revision, verified_entrypoint, manifest = _resolved_active_extension(
        extension_id,
        state_file=state_file,
    )
    provider = manifest.get("provider")
    runtime = _runtime(manifest)
    if not isinstance(provider, Mapping):
        raise ValueError("extension active manifest is incomplete")
    _validate_runtime_binding_access(
        extension_id,
        provider=provider,
        runtime=runtime,
        protocol=protocol,
        permission=permission,
        mismatch_message=(
            f"extension `{extension_id}` does not expose runtime protocol `{protocol}`"
        ),
    )
    return _assemble_runtime_binding(
        extension_id,
        provider=provider,
        runtime=runtime,
        active_revision=active_revision,
        verified_entrypoint=verified_entrypoint,
        protocol=protocol,
    )


def resolve_capability_binding(
    *,
    state_file: str | Path,
    capability_id: str,
    protocol: str,
    permission: str,
) -> ExtensionRuntimeBinding:
    state = _read_state(Path(state_file).expanduser())
    matches = _ready_capability_matches_in_state(
        state, capability_id=capability_id, protocol=protocol
    )
    extension_id, entry, active_revision, manifest, verified = (
        _select_single_capability_match(
            matches, capability_id=capability_id, protocol=protocol
        )
    )
    return _capability_binding_from_entry(
        extension_id,
        entry=entry,
        capability_id=capability_id,
        protocol=protocol,
        permission=permission,
        active_revision=active_revision,
        manifest=manifest,
        incompatibility_checked=True,
        verified_entrypoint=verified,
        matching_checked=True,
    )


__all__ = [
    "resolve_capability_binding",
    "resolve_capability_extension_id",
    "resolve_extension_activation",
    "resolve_extension_binding",
    "resolve_extension_runtime_binding",
    "resolve_optional_capability_binding",
]
