from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from .runtime import (
    EXTENSION_DISABLED_STATUS,
    EXTENSION_DOCTOR_NOT_READY_STATUS,
    extension_catalog_entries,
    resolve_extension_activation,
)


HOOK_ADAPTER_FACTORY_CONTEXT_SCHEMA_VERSION = (
    "loopx_extension_hook_adapter_factory_context_v0"
)
HOOK_ADAPTER_UNAVAILABLE = "extension_hook_adapter_unavailable"


@dataclass(frozen=True, slots=True)
class ExtensionHookAdapterPortBinding:
    extension_id: str
    adapter_id: str
    capability_id: str
    target_hook_id: str
    phase: str
    port_name: str
    handler: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ExtensionHookAdapterFailure:
    """Content-free failure for one optional manifest adapter.

    `error_code` is the extension readiness status when lifecycle refused
    admission (disabled or doctor-stale), otherwise
    `extension_hook_adapter_unavailable` for a ready extension whose
    activation, factory or ports failed.
    """

    extension_id: str
    adapter_id: str
    capability_id: str
    target_hook_id: str
    phase: str
    error_code: str = HOOK_ADAPTER_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class ExtensionHookAdapterDiscovery:
    """Activated provider ports plus isolated content-free failures.

    `failures` names each declared adapter that matched the requested hook but
    was not activated: a disabled or doctor-stale extension (never imported),
    or a ready one whose permission, factory import or ports failed.
    `state_error_code` is set when the extension state itself could
    not be read, so an empty `ports` is distinguishable from "no provider is
    installed". Neither carries provider output or exception text.
    """

    ports: tuple[ExtensionHookAdapterPortBinding, ...]
    failures: tuple[ExtensionHookAdapterFailure, ...]
    state_error_code: str | None = None


def _load_factory(reference: str) -> Callable[..., Mapping[str, Callable[..., Any]]]:
    module_name, separator, callable_name = reference.partition(":")
    if not separator:
        raise ValueError("hook adapter factory reference is invalid")
    factory = getattr(import_module(module_name), callable_name, None)
    if not callable(factory):
        raise ValueError("hook adapter factory is unavailable")
    return cast(Callable[..., Mapping[str, Callable[..., Any]]], factory)


def _failure(
    *,
    extension_id: str,
    adapter: Mapping[str, Any],
    error_code: str = HOOK_ADAPTER_UNAVAILABLE,
) -> ExtensionHookAdapterFailure:
    return ExtensionHookAdapterFailure(
        extension_id=extension_id,
        adapter_id=str(adapter["id"]),
        capability_id=str(adapter["capability_id"]),
        target_hook_id=str(adapter["target_hook_id"]),
        phase=str(adapter["phase"]),
        error_code=error_code,
    )


def _lifecycle_non_admission(provider: Mapping[str, Any]) -> str | None:
    """Readiness status that keeps an installed extension from activating."""

    if provider.get("ready") is True:
        return None
    if provider.get("enabled") is not True:
        return EXTENSION_DISABLED_STATUS
    return EXTENSION_DOCTOR_NOT_READY_STATUS


def discover_extension_hook_adapters(
    *,
    state_file: str | Path,
    phase: str,
    capability_id: str,
    target_hook_id: str,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
) -> ExtensionHookAdapterDiscovery:
    """Discover manifest-declared adapters, activating only ready extensions.

    Factories are imported only after lifecycle and permission activation.
    A matching declaration of a disabled or doctor-stale extension is named
    as a failure without resolving activation, importing its factory or
    running its doctor.
    Factory/import/activation failures remain ordinary data so an optional
    provider cannot alter the capability or control-plane kernel.
    """

    try:
        catalog = extension_catalog_entries(state_file=state_file)
    except (OSError, TypeError, ValueError):
        return ExtensionHookAdapterDiscovery(
            ports=(), failures=(), state_error_code="extension_state_unavailable"
        )

    ports: list[ExtensionHookAdapterPortBinding] = []
    failures: list[ExtensionHookAdapterFailure] = []
    for manifest in catalog:
        provider = manifest.get("provider")
        adapters = manifest.get("hook_adapters")
        if not isinstance(provider, Mapping) or not isinstance(adapters, list):
            continue
        if provider.get("installed") is not True:
            # A declared-only manifest is "no provider installed", not a
            # disabled one; lifecycle codes below describe installed copies.
            continue
        extension_id = str(provider.get("id") or "")
        non_admission = _lifecycle_non_admission(provider)
        for adapter in adapters:
            if (
                not isinstance(adapter, Mapping)
                or adapter.get("phase") != phase
                or adapter.get("capability_id") != capability_id
                or adapter.get("target_hook_id") != target_hook_id
            ):
                continue
            if non_admission is not None:
                failures.append(
                    _failure(
                        extension_id=extension_id,
                        adapter=adapter,
                        error_code=non_admission,
                    )
                )
                continue
            try:
                activation = resolve_extension_activation(
                    extension_id,
                    state_file=state_file,
                    required_permissions=tuple(adapter["required_permissions"]),
                )
                factory = _load_factory(str(adapter["factory"]))
                produced = factory(
                    {
                        "schema_version": (
                            HOOK_ADAPTER_FACTORY_CONTEXT_SCHEMA_VERSION
                        ),
                        "extension_id": extension_id,
                        "adapter_id": str(adapter["id"]),
                        "capability_id": str(adapter["capability_id"]),
                        "target_hook_id": str(adapter["target_hook_id"]),
                        "phase": phase,
                        "activation": activation,
                        "registry_path": registry_path,
                        "runtime_root": runtime_root,
                        "goal_id": goal_id,
                        "agent_id": str(agent_id or ""),
                    }
                )
                if not isinstance(produced, Mapping) or set(produced) != set(
                    adapter["ports"]
                ):
                    raise ValueError("hook adapter factory ports do not match manifest")
                if any(not callable(handler) for handler in produced.values()):
                    raise ValueError("hook adapter factory returned a non-callable port")
            except Exception:  # Optional provider discovery is fail-closed and isolated.
                failures.append(_failure(extension_id=extension_id, adapter=adapter))
                continue
            ports.extend(
                ExtensionHookAdapterPortBinding(
                    extension_id=extension_id,
                    adapter_id=str(adapter["id"]),
                    capability_id=str(adapter["capability_id"]),
                    target_hook_id=target_hook_id,
                    phase=phase,
                    port_name=str(port_name),
                    handler=handler,
                )
                for port_name, handler in produced.items()
            )
    return ExtensionHookAdapterDiscovery(
        ports=tuple(ports),
        failures=tuple(failures),
    )


__all__ = [
    "ExtensionHookAdapterDiscovery",
    "ExtensionHookAdapterFailure",
    "ExtensionHookAdapterPortBinding",
    "HOOK_ADAPTER_FACTORY_CONTEXT_SCHEMA_VERSION",
    "discover_extension_hook_adapters",
]
