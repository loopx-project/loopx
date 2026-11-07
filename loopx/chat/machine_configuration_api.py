from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from ..capabilities.configuration_ui import build_capability_configuration_catalog
from ..configuration_catalog import build_configuration_capability_descriptors
from ..capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from ..capabilities.machine_configuration.contract import (
    MACHINE_CONFIGURATION_SCHEMA,
    MachineConfigurationRegistry,
    machine_configuration_revision,
    merge_machine_configuration_namespace,
    normalize_machine_configuration,
    remove_machine_configuration_namespace,
)
from ..capabilities.machine_configuration.store import (
    configure_machine_configuration,
    inspect_machine_configuration,
    read_stored_machine_configuration,
    rollback_machine_configuration,
)

CHAT_MACHINE_CONFIGURATION_PATH = "/api/chat/machine-configuration"
CHAT_MACHINE_CONFIGURATION_PREVIEW_PATH = f"{CHAT_MACHINE_CONFIGURATION_PATH}/preview"
CHAT_MACHINE_CONFIGURATION_APPLY_PATH = f"{CHAT_MACHINE_CONFIGURATION_PATH}/apply"
CHAT_MACHINE_CONFIGURATION_ROLLBACK_PATH = f"{CHAT_MACHINE_CONFIGURATION_PATH}/rollback"


class _MachineConfigurationServer(Protocol):
    runtime_root: Path


def _public_payload(
    payload: dict[str, Any], *, registry: MachineConfigurationRegistry
) -> dict[str, Any]:
    """Keep the browser contract path-free and limited to public projections."""

    allowed = {
        "ok",
        "schema_version",
        "status",
        "action",
        "reason",
        "revision",
        "current_revision",
        "desired_revision",
        "applied_revision",
        "prior_revision",
        "restored_revision",
        "target_revision",
        "plan_revision",
        "receipt_revision",
        "transaction_id",
        "rollback_id",
        "rollback_allowed",
        "rollback_available",
        "readback_verified",
        "writes_required",
        "changed_namespaces",
        "invalid_namespaces",
        "machine_configuration",
    }
    projected = {key: value for key, value in payload.items() if key in allowed}
    namespace_catalog = registry.public_catalog()
    projected["available_namespaces"] = list(registry.namespace_ids)
    projected["namespace_catalog"] = namespace_catalog
    projected["capability_catalog"] = build_capability_configuration_catalog(
        machine_namespaces=namespace_catalog["namespaces"],
        goal_features=build_configuration_capability_descriptors(),
    )
    for capability in projected["capability_catalog"]["capabilities"]:
        if capability["capability_id"] == "periodic_report":
            # Human review is bound to an original Goal Session, not a device default.
            editor = capability["configuration_editor"]
            editor["fields"] = [field for field in editor["fields"] if field["key"] != "review_required"]
    return projected


def _invalid_machine_configuration_inspection(
    configuration: Mapping[str, Any],
    *,
    registry: MachineConfigurationRegistry,
) -> dict[str, Any]:
    """Return a value-free repair projection for an invalid stored document."""

    namespaces = configuration.get("namespaces")
    invalid_namespaces: list[str] = []
    if configuration.get(
        "schema_version"
    ) == MACHINE_CONFIGURATION_SCHEMA and isinstance(namespaces, Mapping):
        for namespace in registry.namespace_ids:
            if namespace not in namespaces:
                continue
            try:
                normalize_machine_configuration(
                    {
                        "schema_version": MACHINE_CONFIGURATION_SCHEMA,
                        "namespaces": {namespace: namespaces[namespace]},
                    },
                    registry=registry,
                )
            except (TypeError, ValueError):
                invalid_namespaces.append(namespace)
    return {
        "ok": True,
        "schema_version": "machine_configuration_inspection_v0",
        "status": "invalid",
        "reason": "machine_configuration_invalid",
        "revision": machine_configuration_revision(configuration),
        "changed_namespaces": [],
        "invalid_namespaces": invalid_namespaces,
        # Invalid values can contain private provider data. The Dashboard gets
        # the safe catalog and affected namespace IDs, never the stored values.
        "machine_configuration": None,
    }


class MachineConfigurationRequestMixin:
    server: _MachineConfigurationServer

    def _read_json(self) -> dict[str, Any]:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(
        self,
        message: str,
        *,
        status: int,
        error_code: str,
        **kwargs: Any,
    ) -> None:
        raise NotImplementedError

    def _machine_configuration_registry(self) -> MachineConfigurationRegistry:
        return cast(
            MachineConfigurationRegistry, build_builtin_machine_configuration_registry()
        )

    def _machine_configuration_namespace_update(
        self,
        body: dict[str, Any],
        *,
        registry: MachineConfigurationRegistry,
    ) -> dict[str, Any] | None:
        namespace = str(body.get("namespace") or "").strip()
        registry.resolve(namespace)
        # The selected namespace update is also its repair path. Keep the old
        # document opaque until that owner replaces its invalid value; final
        # whole-document normalization still rejects any invalid sibling.
        current = read_stored_machine_configuration(self.server.runtime_root)
        operation = str(body.get("operation") or "upsert").strip()
        if operation == "remove":
            if "namespace_configuration" in body:
                raise ValueError(
                    "namespace_configuration is not accepted for remove operations"
                )
            return remove_machine_configuration_namespace(
                current,
                namespace=namespace,
                registry=registry,
            )
        if operation != "upsert":
            raise ValueError("operation must be upsert or remove")
        namespace_configuration = body.get("namespace_configuration")
        if not isinstance(namespace_configuration, dict):
            raise TypeError("namespace_configuration must be an object")
        return merge_machine_configuration_namespace(
            current,
            namespace=namespace,
            namespace_configuration=namespace_configuration,
            registry=registry,
        )

    def _machine_configuration_inspect(self) -> None:
        registry = self._machine_configuration_registry()
        try:
            result = inspect_machine_configuration(
                self.server.runtime_root,
                registry=registry,
            )
        except (TypeError, ValueError):
            try:
                stored = read_stored_machine_configuration(self.server.runtime_root)
                if stored is None:
                    raise ValueError(
                        "machine configuration disappeared during inspection"
                    )
                result = _invalid_machine_configuration_inspection(
                    stored,
                    registry=registry,
                )
            except Exception:  # noqa: BLE001 - sanitize invalid stored values.
                self._send_error(
                    "Machine configuration could not be inspected.",
                    status=500,
                    error_code="machine_configuration_inspection_failed",
                )
                return
            self._send_json(_public_payload(result, registry=registry))
            return
        except Exception:  # noqa: BLE001 - sanitize the browser-facing error boundary.
            self._send_error(
                "Machine configuration could not be inspected.",
                status=500,
                error_code="machine_configuration_inspection_failed",
            )
            return
        self._send_json(_public_payload(result, registry=registry))

    def _machine_configuration_update(self, *, execute: bool) -> None:
        registry = self._machine_configuration_registry()
        try:
            body = self._read_json()
            allowed = {"namespace", "namespace_configuration", "operation"}
            if execute:
                allowed.add("expected_plan_revision")
            if set(body) - allowed:
                raise ValueError(
                    "machine configuration request contains unknown fields"
                )
            configuration = self._machine_configuration_namespace_update(
                body,
                registry=registry,
            )
            result = configure_machine_configuration(
                runtime_root=self.server.runtime_root,
                configuration=configuration,
                registry=registry,
                execute=execute,
                expected_plan_revision=(
                    str(body.get("expected_plan_revision") or "") if execute else None
                ),
            )
        except (TypeError, ValueError) as exc:
            self._send_error(
                str(exc),
                status=409 if "preview again" in str(exc) else 400,
                error_code=(
                    "machine_configuration_preview_stale"
                    if "preview again" in str(exc)
                    else "invalid_machine_configuration"
                ),
            )
            return
        except Exception:  # noqa: BLE001 - store performs its own restoration.
            self._send_error(
                "Machine configuration could not be applied; the prior state was preserved.",
                status=500,
                error_code="machine_configuration_apply_failed",
            )
            return
        self._send_json(
            _public_payload(result, registry=registry),
            status=200 if execute else 201,
        )

    def _machine_configuration_rollback(self) -> None:
        registry = self._machine_configuration_registry()
        try:
            body = self._read_json()
            if set(body) - {"transaction_id", "execute", "expected_plan_revision"}:
                raise ValueError(
                    "machine configuration rollback contains unknown fields"
                )
            transaction_id = str(body.get("transaction_id") or "").strip()
            execute = body.get("execute", False)
            if not isinstance(execute, bool):
                raise TypeError("execute must be a boolean")
            result = rollback_machine_configuration(
                runtime_root=self.server.runtime_root,
                transaction_id=transaction_id,
                registry=registry,
                execute=execute,
                expected_plan_revision=(
                    str(body.get("expected_plan_revision") or "") if execute else None
                ),
            )
        except (TypeError, ValueError) as exc:
            message = str(exc)
            stale = "preview again" in message or "newer revision" in message
            self._send_error(
                message,
                status=409 if stale else 400,
                error_code=(
                    "machine_configuration_rollback_stale"
                    if stale
                    else "invalid_machine_configuration_rollback"
                ),
            )
            return
        except Exception:  # noqa: BLE001 - store performs its own restoration.
            self._send_error(
                "Machine configuration rollback failed; the applied state was preserved.",
                status=500,
                error_code="machine_configuration_rollback_failed",
            )
            return
        self._send_json(
            _public_payload(result, registry=registry),
            status=200 if execute else 201,
        )


__all__ = [
    "CHAT_MACHINE_CONFIGURATION_APPLY_PATH",
    "CHAT_MACHINE_CONFIGURATION_PATH",
    "CHAT_MACHINE_CONFIGURATION_PREVIEW_PATH",
    "CHAT_MACHINE_CONFIGURATION_ROLLBACK_PATH",
    "MachineConfigurationRequestMixin",
]
