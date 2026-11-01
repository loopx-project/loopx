from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


MACHINE_CONFIGURATION_SCHEMA = "loopx_machine_configuration_v0"
MACHINE_CONFIGURATION_CATALOG_SCHEMA = "machine_configuration_catalog_v0"
_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
Normalizer = Callable[[Mapping[str, Any]], dict[str, Any]]
Projector = Callable[[Mapping[str, Any]], dict[str, Any]]
PublicUpdater = Callable[
    [Mapping[str, Any] | None, Mapping[str, Any]],
    dict[str, Any],
]


@dataclass(frozen=True)
class MachineConfigurationNamespace:
    """One typed owner of a machine-configuration namespace."""

    namespace: str
    schema_versions: frozenset[str]
    normalize: Normalizer
    project_public: Projector
    apply_public_update: PublicUpdater
    title: str | None = None
    description: str | None = None
    documentation: Mapping[str, str] | None = None
    default_configuration: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not _NAMESPACE_RE.fullmatch(self.namespace):
            raise ValueError("machine-configuration namespace is invalid")
        if not self.schema_versions or any(
            not str(version).strip() for version in self.schema_versions
        ):
            raise ValueError("machine-configuration schema versions are required")
        if self.title is not None and not self.title.strip():
            raise ValueError("machine-configuration namespace title must not be empty")
        if self.description is not None and not self.description.strip():
            raise ValueError(
                "machine-configuration namespace description must not be empty"
            )
        if self.documentation is not None and any(
            not str(key).strip() or not str(value).strip()
            for key, value in self.documentation.items()
        ):
            raise ValueError(
                "machine-configuration namespace documentation must not be empty"
            )

    def public_descriptor(self) -> dict[str, Any]:
        if self.default_configuration is None:
            public_template = {"schema_version": sorted(self.schema_versions)[0]}
            template_status = "schema_only"
        else:
            default = deepcopy(dict(self.default_configuration))
            normalized_default = self.normalize(default)
            public_template = self.project_public(normalized_default)
            if public_template.get("schema_version") not in self.schema_versions:
                raise ValueError(
                    "machine-configuration namespace default changed schema_version: "
                    + self.namespace
                )
            template_status = "ready"
        return {
            "namespace": self.namespace,
            "title": self.title or self.namespace.replace("_", " ").title(),
            "description": self.description or "",
            "schema_versions": sorted(self.schema_versions),
            "configuration_template": public_template,
            "template_status": template_status,
            **(
                {"documentation": dict(self.documentation)}
                if self.documentation is not None
                else {}
            ),
        }


class MachineConfigurationRegistry:
    """Fail-closed registry shared by CLI, Dashboard, and other effect owners."""

    def __init__(self) -> None:
        self._namespaces: dict[str, MachineConfigurationNamespace] = {}
        self._deferred: dict[str, Callable[[], MachineConfigurationNamespace]] = {}

    def _reserve(self, namespace: str) -> None:
        if namespace in self._namespaces or namespace in self._deferred:
            raise ValueError(
                "machine-configuration namespace is already registered: " + namespace
            )

    def register(
        self, contract: MachineConfigurationNamespace
    ) -> MachineConfigurationRegistry:
        self._reserve(contract.namespace)
        # Validate public catalog metadata before this registry can own effects.
        # A bad provider template must fail before preview/apply/rollback runs.
        contract.public_descriptor()
        self._namespaces[contract.namespace] = contract
        return self

    def register_deferred(
        self, namespace: str, load: Callable[[], MachineConfigurationNamespace]
    ) -> MachineConfigurationRegistry:
        """Register an owner that is loaded when its namespace is first resolved.

        Readers then import only the namespaces they touch. Resolution applies
        the same template validation as ``register`` before the owner is
        returned, so a bad template still fails before that namespace can own a
        preview, apply or rollback effect.
        """
        if not _NAMESPACE_RE.fullmatch(namespace):
            raise ValueError("machine-configuration namespace is invalid")
        self._reserve(namespace)
        self._deferred[namespace] = load
        return self

    def resolve(self, namespace: str) -> MachineConfigurationNamespace:
        contract = self._namespaces.get(namespace)
        if contract is not None:
            return contract
        load = self._deferred.get(namespace)
        if load is None:
            raise ValueError(
                f"unsupported machine-configuration namespace: {namespace}"
            )
        contract = load()
        if contract.namespace != namespace:
            raise ValueError(
                "machine-configuration namespace owner declares another id: "
                + namespace
            )
        contract.public_descriptor()
        del self._deferred[namespace]
        self._namespaces[namespace] = contract
        return contract

    @property
    def namespace_ids(self) -> tuple[str, ...]:
        return tuple(sorted({*self._namespaces, *self._deferred}))

    def public_catalog(self) -> dict[str, Any]:
        return {
            "schema_version": MACHINE_CONFIGURATION_CATALOG_SCHEMA,
            "namespaces": [
                self.resolve(namespace).public_descriptor()
                for namespace in self.namespace_ids
            ],
        }


def merge_machine_configuration_namespace(
    current: Mapping[str, Any] | None,
    *,
    namespace: str,
    namespace_configuration: Mapping[str, Any],
    registry: MachineConfigurationRegistry,
) -> dict[str, Any]:
    """Build one whole-document update while preserving sibling namespaces."""

    contract = registry.resolve(namespace)
    # Namespace updates are also the recovery path for an installed namespace
    # whose previously valid value no longer satisfies the current contract.
    # Keep the stored value opaque until the owning namespace has replaced it;
    # the final whole-document normalization below still validates every
    # sibling and the new value before a plan can be produced.
    normalized_current = (
        _mapping(current, "machine_configuration")
        if current is not None
        else {"schema_version": MACHINE_CONFIGURATION_SCHEMA, "namespaces": {}}
    )
    if normalized_current.get("schema_version") != MACHINE_CONFIGURATION_SCHEMA:
        raise ValueError(
            f"machine_configuration must use {MACHINE_CONFIGURATION_SCHEMA}"
        )
    unknown = sorted(set(normalized_current) - {"schema_version", "namespaces"})
    if unknown:
        raise ValueError(
            "machine_configuration contains unsupported fields: " + ", ".join(unknown)
        )
    current_namespaces = _mapping(
        normalized_current.get("namespaces"), "machine_configuration.namespaces"
    )
    current_namespace = current_namespaces.get(namespace)
    if current_namespace is not None and not isinstance(current_namespace, Mapping):
        raise TypeError(
            f"machine_configuration.namespaces.{namespace} must be an object"
        )
    updated_namespace = _mapping(
        contract.apply_public_update(current_namespace, namespace_configuration),
        f"machine_configuration.namespaces.{namespace}",
    )
    return normalize_machine_configuration(
        {
            "schema_version": MACHINE_CONFIGURATION_SCHEMA,
            "namespaces": {
                **current_namespaces,
                namespace: updated_namespace,
            },
        },
        registry=registry,
    )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def normalize_machine_configuration(
    raw: object, *, registry: MachineConfigurationRegistry
) -> dict[str, Any]:
    payload = _mapping(raw, "machine_configuration")
    unknown = sorted(set(payload) - {"schema_version", "namespaces"})
    if unknown:
        raise ValueError(
            "machine_configuration contains unsupported fields: " + ", ".join(unknown)
        )
    if payload.get("schema_version") != MACHINE_CONFIGURATION_SCHEMA:
        raise ValueError(
            f"machine_configuration must use {MACHINE_CONFIGURATION_SCHEMA}"
        )
    namespaces = _mapping(payload.get("namespaces"), "machine_configuration.namespaces")
    if not namespaces:
        raise ValueError("machine_configuration.namespaces must not be empty")
    normalized: dict[str, Any] = {}
    for namespace in sorted(namespaces):
        contract = registry.resolve(namespace)
        candidate = _mapping(
            namespaces[namespace], f"machine_configuration.namespaces.{namespace}"
        )
        schema_version = str(candidate.get("schema_version") or "").strip()
        if schema_version not in contract.schema_versions:
            raise ValueError(
                f"machine_configuration namespace {namespace} has unsupported "
                f"schema_version: {schema_version or 'missing'}"
            )
        value = contract.normalize(candidate)
        if value.get("schema_version") != schema_version:
            raise ValueError(
                f"machine-configuration normalizer changed {namespace} schema_version"
            )
        normalized[namespace] = value
    return {
        "schema_version": MACHINE_CONFIGURATION_SCHEMA,
        "namespaces": normalized,
    }


def machine_configuration_revision(configuration: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        configuration, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def project_machine_configuration(
    configuration: object, *, registry: MachineConfigurationRegistry
) -> dict[str, Any]:
    normalized = normalize_machine_configuration(configuration, registry=registry)
    projected: dict[str, Any] = {}
    for namespace, value in normalized["namespaces"].items():
        contract = registry.resolve(namespace)
        public_value = contract.project_public(value)
        if public_value.get("schema_version") != value.get("schema_version"):
            raise ValueError(
                f"machine-configuration public projection changed {namespace} schema_version"
            )
        projected[namespace] = public_value
    return {
        "schema_version": MACHINE_CONFIGURATION_SCHEMA,
        "namespaces": projected,
    }


def remove_machine_configuration_namespace(
    current: Mapping[str, Any] | None,
    *,
    namespace: str,
    registry: MachineConfigurationRegistry,
) -> dict[str, Any] | None:
    """Remove one typed namespace, returning absence for an empty document."""

    registry.resolve(namespace)
    if current is None:
        return None
    normalized = normalize_machine_configuration(current, registry=registry)
    namespaces = dict(normalized["namespaces"])
    namespaces.pop(namespace, None)
    if not namespaces:
        return None
    return normalize_machine_configuration(
        {
            "schema_version": MACHINE_CONFIGURATION_SCHEMA,
            "namespaces": namespaces,
        },
        registry=registry,
    )


__all__ = [
    "MACHINE_CONFIGURATION_CATALOG_SCHEMA",
    "MACHINE_CONFIGURATION_SCHEMA",
    "MachineConfigurationNamespace",
    "MachineConfigurationRegistry",
    "machine_configuration_revision",
    "merge_machine_configuration_namespace",
    "normalize_machine_configuration",
    "project_machine_configuration",
    "remove_machine_configuration_namespace",
]
