"""Python typed mirror of the TypeScript HostHarnessCapabilities co-design contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import importlib
import re
import threading
from typing import Literal, TypeGuard

HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION: Literal[
    "loopx_host_harness_capabilities_v0"
] = "loopx_host_harness_capabilities_v0"

HostIntegrationTier = Literal[
    "boundary_cli",
    "orchestrated_app",
    "in_tree_codesigned",
]

HostHarnessCapabilityFlag = Literal[
    "session_fork",
    "epoch_reset",
    "fold_and_prefetch",
    "quiet_turn",
    "compact_hook",
    "verifier_hook",
    "native_context_budget",
    "managed_thread_resume",
]

HOST_INTEGRATION_TIER_VALUES: tuple[HostIntegrationTier, ...] = (
    "boundary_cli",
    "orchestrated_app",
    "in_tree_codesigned",
)

HOST_HARNESS_CAPABILITY_FLAG_VALUES: tuple[HostHarnessCapabilityFlag, ...] = (
    "session_fork",
    "epoch_reset",
    "fold_and_prefetch",
    "quiet_turn",
    "compact_hook",
    "verifier_hook",
    "native_context_budget",
    "managed_thread_resume",
)

_HOST_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MODULE_PATH_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_HOOK_EVENT_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
# Shared Python/TS contract bound measured in Unicode code points (len(str) / Array.from(str).length).
_MAX_DESCRIPTION_CODE_POINTS = 512
# Explicit cross-runtime edge-whitespace set matching TypeScript DESCRIPTION_EDGE_WHITESPACE_RE:
# strips ASCII whitespace, U+0085 NEL, Unicode Zs/Zl/Zp separators, and U+FEFF BOM,
# while deliberately NOT stripping ASCII separators U+001C..U+001F.
_DESCRIPTION_WHITESPACE_CHARS = (
    "\t\n\x0b\x0c\r \u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

_TIER_MAP: dict[str, HostIntegrationTier] = {
    tier: tier for tier in HOST_INTEGRATION_TIER_VALUES
}
_FLAG_MAP: dict[str, HostHarnessCapabilityFlag] = {
    flag: flag for flag in HOST_HARNESS_CAPABILITY_FLAG_VALUES
}
_FLAG_ORDER: dict[HostHarnessCapabilityFlag, int] = {
    flag: index for index, flag in enumerate(HOST_HARNESS_CAPABILITY_FLAG_VALUES)
}
_HOOK_CAPABILITY_FLAGS: frozenset[HostHarnessCapabilityFlag] = frozenset(
    {"compact_hook", "verifier_hook"}
)

_DESCRIPTOR_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "host_id",
        "integration_tier",
        "adapter_module",
        "supported_capabilities",
        "hook_events",
        "requires_utf8_stdio",
        "native_goal_primitive",
        "native_scheduler_primitive",
        "description",
    }
)

BUILTIN_HOST_ADAPTER_MODULES: Mapping[str, str] = {
    "agy": "loopx.agy_goal_mode",
    "claude": "loopx.claude_goal_mode",
    "codex-app": "loopx.hosts.codex_capabilities",
    "codex-app-server": "loopx.hosts.codex_capabilities",
    "codex-cli": "loopx.hosts.codex_capabilities",
    "codex-cli-hooks": "loopx.hosts.codex_capabilities",
    "dsh": "loopx.dsh_goal_mode.turn_host_adapter",
}
_BUILTIN_DESCRIPTORS_LOCK = threading.Lock()
_BUILTIN_DESCRIPTORS: dict[str, HostHarnessCapabilities] = {}


def _resolve_loaded_builtin_descriptor(
    host_id: str,
    module: object,
) -> HostHarnessCapabilities | None:
    with _BUILTIN_DESCRIPTORS_LOCK:
        cached = _BUILTIN_DESCRIPTORS.get(host_id)
    if cached is not None:
        return cached
    module_dict = getattr(module, "__dict__", None)
    if not isinstance(module_dict, Mapping):
        return None
    matches = [
        value
        for value in module_dict.values()
        if isinstance(value, HostHarnessCapabilities) and value.host_id == host_id
    ]
    return matches[0] if len(matches) == 1 else None


def is_host_integration_tier(value: object) -> TypeGuard[HostIntegrationTier]:
    return isinstance(value, str) and value in _TIER_MAP


def is_host_harness_capability_flag(
    value: object,
) -> TypeGuard[HostHarnessCapabilityFlag]:
    return isinstance(value, str) and value in _FLAG_MAP


def validate_host_integration_tier(value: object) -> HostIntegrationTier:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("integration_tier must be a non-empty string")
    tier = _TIER_MAP.get(value)
    if tier is None:
        raise ValueError(f"unsupported host integration tier: {value}")
    return tier


def validate_host_harness_capability_flag(
    value: object,
) -> HostHarnessCapabilityFlag:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("capability must be a non-empty string")
    flag = _FLAG_MAP.get(value)
    if flag is None:
        raise ValueError(f"unsupported host harness capability flag: {value}")
    return flag


def _validate_host_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("host_id must be a non-empty string")
    if _HOST_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "host_id must be a lowercase slug matching ^[a-z][a-z0-9_-]{0,63}$"
        )
    return value


def _validate_adapter_module(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("adapter_module must be a non-empty string")
    if _MODULE_PATH_PATTERN.fullmatch(value) is None:
        raise ValueError("adapter_module must be a dotted Python module path")
    return value


def _validate_capability_flags(
    value: object,
) -> tuple[HostHarnessCapabilityFlag, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("supported_capabilities must be a sequence of strings")
    seen: set[HostHarnessCapabilityFlag] = set()
    for item in value:
        flag = validate_host_harness_capability_flag(item)
        if flag in seen:
            raise ValueError(f"duplicate host harness capability flag: {flag}")
        seen.add(flag)
    return tuple(sorted(seen, key=lambda flag: _FLAG_ORDER[flag]))


def _validate_hook_events(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("hook_events must be a sequence of strings")
    seen: set[str] = set()
    events: list[str] = []
    for item in value:
        if not isinstance(item, str) or _HOOK_EVENT_PATTERN.fullmatch(item) is None:
            raise ValueError(f"invalid hook_events entry: {item!r}")
        if item in seen:
            raise ValueError(f"duplicate hook_events entry: {item}")
        seen.add(item)
        events.append(item)
    return tuple(events)


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _validate_description(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("description must be a non-empty string")
    # Strip with the explicit cross-runtime whitespace set instead of str.strip()
    # so U+0085 NEL and U+FEFF BOM trim identically and U+001C..U+001F stay intact.
    trimmed = value.strip(_DESCRIPTION_WHITESPACE_CHARS)
    if not trimmed:
        raise ValueError("description must be a non-empty string")
    if len(trimmed) > _MAX_DESCRIPTION_CODE_POINTS:
        raise ValueError(
            f"description exceeds {_MAX_DESCRIPTION_CODE_POINTS} Unicode code points"
        )
    return trimmed


def _validate_descriptor_fields(
    *,
    schema_version: object,
    host_id: object,
    integration_tier: object,
    adapter_module: object,
    supported_capabilities: object,
    hook_events: object,
    requires_utf8_stdio: object,
    native_goal_primitive: object,
    native_scheduler_primitive: object,
    description: object,
) -> tuple[
    Literal["loopx_host_harness_capabilities_v0"],
    str,
    HostIntegrationTier,
    str,
    tuple[HostHarnessCapabilityFlag, ...],
    tuple[str, ...],
    bool,
    bool,
    bool,
    str,
]:
    if (
        not isinstance(schema_version, str)
        or schema_version != HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION
    ):
        raise ValueError(
            f"unsupported host harness capabilities schema_version: {schema_version!r}"
        )
    validated_host_id = _validate_host_id(host_id)
    validated_tier = validate_host_integration_tier(integration_tier)
    validated_module = _validate_adapter_module(adapter_module)
    validated_flags = _validate_capability_flags(supported_capabilities)
    validated_hooks = _validate_hook_events(hook_events)
    validated_utf8 = _require_bool(requires_utf8_stdio, "requires_utf8_stdio")
    validated_goal = _require_bool(native_goal_primitive, "native_goal_primitive")
    validated_scheduler = _require_bool(
        native_scheduler_primitive, "native_scheduler_primitive"
    )
    validated_description = _validate_description(description)

    if any(flag in _HOOK_CAPABILITY_FLAGS for flag in validated_flags) and not validated_hooks:
        raise ValueError(
            "compact_hook and verifier_hook capabilities require non-empty hook_events"
        )

    return (
        HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION,
        validated_host_id,
        validated_tier,
        validated_module,
        validated_flags,
        validated_hooks,
        validated_utf8,
        validated_goal,
        validated_scheduler,
        validated_description,
    )


@dataclass(frozen=True)
class HostHarnessCapabilities:
    """Canonical capability descriptor for a LoopX host harness adapter."""

    host_id: str
    integration_tier: HostIntegrationTier
    adapter_module: str
    supported_capabilities: tuple[HostHarnessCapabilityFlag, ...] = ()
    hook_events: tuple[str, ...] = ()
    requires_utf8_stdio: bool = True
    native_goal_primitive: bool = False
    native_scheduler_primitive: bool = False
    description: str = ""
    schema_version: Literal["loopx_host_harness_capabilities_v0"] = (
        HOST_HARNESS_CAPABILITIES_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        (
            schema_version,
            host_id,
            integration_tier,
            adapter_module,
            supported_capabilities,
            hook_events,
            requires_utf8_stdio,
            native_goal_primitive,
            native_scheduler_primitive,
            description,
        ) = _validate_descriptor_fields(
            schema_version=self.schema_version,
            host_id=self.host_id,
            integration_tier=self.integration_tier,
            adapter_module=self.adapter_module,
            supported_capabilities=self.supported_capabilities,
            hook_events=self.hook_events,
            requires_utf8_stdio=self.requires_utf8_stdio,
            native_goal_primitive=self.native_goal_primitive,
            native_scheduler_primitive=self.native_scheduler_primitive,
            description=self.description,
        )
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "host_id", host_id)
        object.__setattr__(self, "integration_tier", integration_tier)
        object.__setattr__(self, "adapter_module", adapter_module)
        object.__setattr__(self, "supported_capabilities", supported_capabilities)
        object.__setattr__(self, "hook_events", hook_events)
        object.__setattr__(self, "requires_utf8_stdio", requires_utf8_stdio)
        object.__setattr__(self, "native_goal_primitive", native_goal_primitive)
        object.__setattr__(
            self, "native_scheduler_primitive", native_scheduler_primitive
        )
        object.__setattr__(self, "description", description)

    def supports(self, capability: HostHarnessCapabilityFlag | str) -> bool:
        flag = validate_host_harness_capability_flag(capability)
        return flag in self.supported_capabilities

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "host_id": self.host_id,
            "integration_tier": self.integration_tier,
            "adapter_module": self.adapter_module,
            "supported_capabilities": list(self.supported_capabilities),
            "hook_events": list(self.hook_events),
            "requires_utf8_stdio": self.requires_utf8_stdio,
            "native_goal_primitive": self.native_goal_primitive,
            "native_scheduler_primitive": self.native_scheduler_primitive,
            "description": self.description,
        }


def validate_host_harness_capabilities(
    value: HostHarnessCapabilities | Mapping[str, object],
) -> HostHarnessCapabilities:
    """Validate and normalize a host harness capability descriptor."""
    if isinstance(value, HostHarnessCapabilities):
        _validate_descriptor_fields(
            schema_version=value.schema_version,
            host_id=value.host_id,
            integration_tier=value.integration_tier,
            adapter_module=value.adapter_module,
            supported_capabilities=value.supported_capabilities,
            hook_events=value.hook_events,
            requires_utf8_stdio=value.requires_utf8_stdio,
            native_goal_primitive=value.native_goal_primitive,
            native_scheduler_primitive=value.native_scheduler_primitive,
            description=value.description,
        )
        return value
    if not isinstance(value, Mapping):
        raise ValueError("host harness capabilities must be a mapping or descriptor")
    keys = {str(key) for key in value.keys()}
    unknown_keys = sorted(keys - _DESCRIPTOR_KEYS)
    if unknown_keys:
        raise ValueError(
            f"unsupported host harness capabilities fields: {', '.join(unknown_keys)}"
        )
    missing_keys = sorted(_DESCRIPTOR_KEYS - keys)
    if missing_keys:
        raise ValueError(
            f"missing host harness capabilities fields: {', '.join(missing_keys)}"
        )
    (
        schema_version,
        host_id,
        integration_tier,
        adapter_module,
        supported_capabilities,
        hook_events,
        requires_utf8_stdio,
        native_goal_primitive,
        native_scheduler_primitive,
        description,
    ) = _validate_descriptor_fields(
        schema_version=value["schema_version"],
        host_id=value["host_id"],
        integration_tier=value["integration_tier"],
        adapter_module=value["adapter_module"],
        supported_capabilities=value["supported_capabilities"],
        hook_events=value["hook_events"],
        requires_utf8_stdio=value["requires_utf8_stdio"],
        native_goal_primitive=value["native_goal_primitive"],
        native_scheduler_primitive=value["native_scheduler_primitive"],
        description=value["description"],
    )
    return HostHarnessCapabilities(
        schema_version=schema_version,
        host_id=host_id,
        integration_tier=integration_tier,
        adapter_module=adapter_module,
        supported_capabilities=supported_capabilities,
        hook_events=hook_events,
        requires_utf8_stdio=requires_utf8_stdio,
        native_goal_primitive=native_goal_primitive,
        native_scheduler_primitive=native_scheduler_primitive,
        description=description,
    )


class HostCapabilitiesRegistry:
    """In-memory registry of validated host harness capability descriptors."""

    def __init__(
        self,
        initial: Sequence[HostHarnessCapabilities] = (),
        *,
        builtin_modules: Mapping[str, str] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, HostHarnessCapabilities] = {}
        self._builtin_modules: dict[str, str] = (
            dict(builtin_modules) if builtin_modules is not None else {}
        )
        self._builtin_locks: dict[str, threading.RLock] = {}
        self._loading_threads: dict[str, int] = {}
        for item in initial:
            self.register(item)

    def _ensure_builtin_loaded(self, host_id: str | None = None) -> None:
        if host_id is None:
            with self._lock:
                builtin_ids = tuple(self._builtin_modules.keys())
            for builtin_id in builtin_ids:
                self._ensure_builtin_loaded(builtin_id)
            return

        current_thread_id = threading.get_ident()
        with self._lock:
            if host_id in self._entries:
                return
            module_name = self._builtin_modules.get(host_id)
            if module_name is None:
                return
            if self._loading_threads.get(host_id) == current_thread_id:
                return
            builtin_lock = self._builtin_locks.setdefault(host_id, threading.RLock())

        with builtin_lock:
            with self._lock:
                if host_id in self._entries:
                    return
                if self._loading_threads.get(host_id) == current_thread_id:
                    return
                self._loading_threads[host_id] = current_thread_id
            try:
                module = importlib.import_module(module_name)
                self._sync_loaded_builtin(host_id, module_name, module)
            finally:
                with self._lock:
                    self._loading_threads.pop(host_id, None)

    def _sync_loaded_builtin(
        self,
        host_id: str,
        module_name: str,
        module: object,
    ) -> None:
        if BUILTIN_HOST_ADAPTER_MODULES.get(host_id) != module_name:
            return
        descriptor = _resolve_loaded_builtin_descriptor(host_id, module)
        if descriptor is None:
            return
        validated = validate_host_harness_capabilities(descriptor)
        with self._lock:
            if host_id not in self._entries:
                self._entries[host_id] = validated

    def _write_entry_locked(
        self,
        validated: HostHarnessCapabilities,
        *,
        replace: bool,
    ) -> HostHarnessCapabilities:
        with self._lock:
            existing = self._entries.get(validated.host_id)
            if existing is not None and not replace:
                if existing == validated:
                    return existing
                raise ValueError(
                    f"conflicting host capabilities registration for {validated.host_id}"
                )
            self._entries[validated.host_id] = validated
            return validated

    def _register_builtin(
        self,
        descriptor: HostHarnessCapabilities,
    ) -> HostHarnessCapabilities:
        """Register an authoritative built-in descriptor during adapter import.

        Avoids re-entering _ensure_builtin_loaded/_builtin_locks while Python's
        per-module import lock is already held by the importing thread.
        """
        validated = validate_host_harness_capabilities(descriptor)
        return self._write_entry_locked(validated, replace=False)

    def register(
        self,
        descriptor: HostHarnessCapabilities | Mapping[str, object],
        *,
        replace: bool = False,
    ) -> HostHarnessCapabilities:
        validated = validate_host_harness_capabilities(descriptor)
        self._ensure_builtin_loaded(validated.host_id)
        return self._write_entry_locked(validated, replace=replace)

    def get(self, host_id: str) -> HostHarnessCapabilities | None:
        normalized = _validate_host_id(host_id)
        self._ensure_builtin_loaded(normalized)
        with self._lock:
            return self._entries.get(normalized)

    def supports(
        self,
        host_id: str,
        capability: HostHarnessCapabilityFlag | str,
    ) -> bool:
        flag = validate_host_harness_capability_flag(capability)
        descriptor = self.get(host_id)
        return descriptor is not None and flag in descriptor.supported_capabilities

    def list(
        self,
        *,
        tier: HostIntegrationTier | str | None = None,
        capability: HostHarnessCapabilityFlag | str | None = None,
    ) -> tuple[HostHarnessCapabilities, ...]:
        tier_filter = (
            validate_host_integration_tier(tier) if tier is not None else None
        )
        capability_filter = (
            validate_host_harness_capability_flag(capability)
            if capability is not None
            else None
        )
        self._ensure_builtin_loaded()
        with self._lock:
            entries = tuple(self._entries.values())
        return tuple(
            sorted(
                (
                    entry
                    for entry in entries
                    if (tier_filter is None or entry.integration_tier == tier_filter)
                    and (
                        capability_filter is None
                        or capability_filter in entry.supported_capabilities
                    )
                ),
                key=lambda entry: entry.host_id,
            )
        )


DEFAULT_HOST_CAPABILITIES_REGISTRY = HostCapabilitiesRegistry(
    builtin_modules=BUILTIN_HOST_ADAPTER_MODULES,
)


def _register_builtin_host_capabilities(
    descriptor: HostHarnessCapabilities,
    *,
    registry: HostCapabilitiesRegistry | None = None,
) -> HostHarnessCapabilities:
    validated = validate_host_harness_capabilities(descriptor)
    if registry is None:
        with _BUILTIN_DESCRIPTORS_LOCK:
            existing = _BUILTIN_DESCRIPTORS.get(validated.host_id)
            if existing is not None and existing != validated:
                raise ValueError(
                    f"conflicting host capabilities registration for {validated.host_id}"
                )
            _BUILTIN_DESCRIPTORS[validated.host_id] = validated
        target = DEFAULT_HOST_CAPABILITIES_REGISTRY
    else:
        target = registry
    return target._register_builtin(validated)


def register_host_capabilities(
    descriptor: HostHarnessCapabilities | Mapping[str, object],
    *,
    registry: HostCapabilitiesRegistry | None = None,
    replace: bool = False,
) -> HostHarnessCapabilities:
    target = DEFAULT_HOST_CAPABILITIES_REGISTRY if registry is None else registry
    return target.register(descriptor, replace=replace)


def get_host_capabilities(
    host_id: str,
    *,
    registry: HostCapabilitiesRegistry | None = None,
) -> HostHarnessCapabilities | None:
    target = DEFAULT_HOST_CAPABILITIES_REGISTRY if registry is None else registry
    return target.get(host_id)


def host_supports_capability(
    host_id: str,
    capability: HostHarnessCapabilityFlag | str,
    *,
    registry: HostCapabilitiesRegistry | None = None,
) -> bool:
    target = DEFAULT_HOST_CAPABILITIES_REGISTRY if registry is None else registry
    return target.supports(host_id, capability)


def list_host_capabilities(
    *,
    tier: HostIntegrationTier | str | None = None,
    capability: HostHarnessCapabilityFlag | str | None = None,
    registry: HostCapabilitiesRegistry | None = None,
) -> tuple[HostHarnessCapabilities, ...]:
    target = DEFAULT_HOST_CAPABILITIES_REGISTRY if registry is None else registry
    return target.list(tier=tier, capability=capability)
