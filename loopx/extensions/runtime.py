"""Lifecycle-gated extension execution and stable runtime facade."""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
from pathlib import Path

from .process_runtime import run_capped_process as run_capped_process
from .readiness import runtime_process_environment
from .runtime_bindings import (
    _capability_binding_from_entry as _capability_binding_from_entry,
    _matching_capability_implementations as _matching_capability_implementations,
    _optional_binding_for_entry as _optional_binding_for_entry,
    _optional_binding_resolution as _optional_binding_resolution,
    resolve_capability_binding as resolve_capability_binding,
    resolve_capability_extension_id as resolve_capability_extension_id,
    resolve_extension_activation as resolve_extension_activation,
    resolve_extension_binding as resolve_extension_binding,
    resolve_extension_runtime_binding as resolve_extension_runtime_binding,
    resolve_optional_capability_binding as resolve_optional_capability_binding,
)
from .runtime_lifecycle import (
    disable_extension as disable_extension,
    doctor_enabled_extensions as doctor_enabled_extensions,
    doctor_installed_extension as doctor_installed_extension,
    enable_extension as enable_extension,
    extension_catalog_entries as extension_catalog_entries,
    extension_status as extension_status,
    install_extension as install_extension,
    rollback_extension as rollback_extension,
)
from .runtime_state import (
    EXTENSION_ACTIVATION_SCHEMA_VERSION as EXTENSION_ACTIVATION_SCHEMA_VERSION,
    EXTENSION_BINDING_SCHEMA_VERSION as EXTENSION_BINDING_SCHEMA_VERSION,
    EXTENSION_DISABLED_STATUS as EXTENSION_DISABLED_STATUS,
    EXTENSION_DOCTOR_BATCH_SCHEMA_VERSION as EXTENSION_DOCTOR_BATCH_SCHEMA_VERSION,
    EXTENSION_DOCTOR_NOT_READY_STATUS as EXTENSION_DOCTOR_NOT_READY_STATUS,
    EXTENSION_OPERATION_SCHEMA_VERSION as EXTENSION_OPERATION_SCHEMA_VERSION,
    EXTENSION_PROVIDER_READINESS_SCHEMA_VERSION as EXTENSION_PROVIDER_READINESS_SCHEMA_VERSION,
    EXTENSION_RUN_SCHEMA_VERSION as EXTENSION_RUN_SCHEMA_VERSION,
    EXTENSION_STATE_SCHEMA_VERSION as EXTENSION_STATE_SCHEMA_VERSION,
    MAX_EXTENSION_REQUEST_BYTES as MAX_EXTENSION_REQUEST_BYTES,
    MAX_EXTENSION_RESPONSE_BYTES as MAX_EXTENSION_RESPONSE_BYTES,
    MAX_REVISIONS as MAX_REVISIONS,
    MAX_VERIFIED_ENTRYPOINTS as MAX_VERIFIED_ENTRYPOINTS,
    ExtensionCapabilityResolution as ExtensionCapabilityResolution,
    ExtensionCatalogEntry as ExtensionCatalogEntry,
    ExtensionHookAdapterEntry as ExtensionHookAdapterEntry,
    ExtensionRuntimeBinding as ExtensionRuntimeBinding,
    _active_manifest as _active_manifest,
    _active_manifest_incompatibility as _active_manifest_incompatibility,
    _coerce_object_dict,
    _empty_state as _empty_state,
    _entry_for_revision as _entry_for_revision,
    _manifest_snapshot as _manifest_snapshot,
    _read_state as _read_state,
    _record_doctor as _record_doctor,
    _resolved_active_extension as _resolved_active_extension,
    _retain_revisions as _retain_revisions,
    _revision as _revision,
    _runtime as _runtime,
    _verified_entrypoint as _verified_entrypoint,
    _verified_identities as _verified_identities,
    _write_state as _write_state,
    default_extension_state_file as default_extension_state_file,
)


def _serialize_extension_request(
    request: Mapping[str, object],
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


def _validate_standalone_run(
    extension_id: str,
    *,
    manifest: Mapping[str, object],
    request: Mapping[str, object],
) -> tuple[Mapping[str, object], Mapping[str, object], list[str], bytes]:
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

    raw_required = runtime.get("required_permissions")
    required_permissions = [
        str(value)
        for value in (raw_required if isinstance(raw_required, list) else [])
    ]
    raw_declared = provider.get("permissions")
    declared_permissions = {
        str(value)
        for value in (raw_declared if isinstance(raw_declared, list) else [])
    }
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
    return provider, runtime, required_permissions, request_bytes


def _execute_standalone_process(
    receipt: dict[str, object],
    *,
    argv: list[str],
    request_bytes: bytes,
    timeout_seconds: int,
    env: Mapping[str, str] | None,
) -> dict[str, object]:
    try:
        completed = run_capped_process(
            argv,
            stdin=request_bytes,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=MAX_EXTENSION_RESPONSE_BYTES,
            env=env,
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


def run_standalone_extension(
    extension_id: str,
    *,
    state_file: str | Path,
    request: Mapping[str, object],
    execute: bool = False,
) -> dict[str, object]:
    """Run one lifecycle-gated standalone extension over bounded JSON stdin/stdout."""
    active_revision, verified_entrypoint, manifest = _resolved_active_extension(
        extension_id,
        state_file=state_file,
    )
    provider, runtime, required_permissions, request_bytes = _validate_standalone_run(
        extension_id,
        manifest=manifest,
        request=request,
    )
    receipt: dict[str, object] = {
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

    raw_args = runtime.get("args")
    args = [str(value) for value in raw_args] if isinstance(raw_args, list) else []
    argv = [*verified_entrypoint.argv_prefix, *args]
    raw_timeout = runtime["timeout_seconds"]
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
        raise ValueError("extension runtime timeout is invalid")
    return _execute_standalone_process(
        receipt,
        argv=argv,
        request_bytes=request_bytes,
        timeout_seconds=int(raw_timeout),
        env=runtime_process_environment(verified_entrypoint.path_prefix),
    )


def _runtime_binding_invocation(
    binding: Mapping[str, object],
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
    return [str(value) for value in argv], timeout_seconds


def execute_extension_runtime_binding(
    binding: Mapping[str, object],
    *,
    request: Mapping[str, object],
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Execute a resolved binding after its capability authorizes the request."""
    argv, timeout_seconds = _runtime_binding_invocation(binding)
    request_bytes = _serialize_extension_request(request, operation="runtime")
    raw_prefix = binding.get("environment_path_prefix")
    if raw_prefix is not None and not isinstance(raw_prefix, str):
        raise ValueError("extension runtime search directory must be absolute")
    path_prefix = raw_prefix
    try:
        completed = run_capped_process(
            argv,
            stdin=request_bytes,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=MAX_EXTENSION_RESPONSE_BYTES,
            env=runtime_process_environment(path_prefix, environment),
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
    return _coerce_object_dict(provider_result)
