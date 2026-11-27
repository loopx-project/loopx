"""Permission requirement, guidance, and projection helpers for external Connectors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import TypedDict
from urllib.parse import urlsplit

from .external_connector_runtime import (
    SAFE_TOKEN_PATTERN,
    ExternalConnectorCapability,
    ExternalResponsePolicy,
)

PERMISSION_REQUIREMENT_SCHEMA_VERSION = (
    "agent_external_connector_permission_requirement_v0"
)
PERMISSION_GUIDANCE_SCHEMA_VERSION = "agent_external_connector_permission_guidance_v0"
PERMISSION_STATUS_SCHEMA_VERSION = "agent_external_connector_permission_status_v0"

SAFE_SCOPE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")

PermissionRequirementFingerprint = tuple[str, str, str, tuple[str, ...], bool, str]


class ExternalConnectorPermissionStatus(TypedDict):
    """Typed public-safe permission status projection without secret scopes."""

    schema_version: str
    ready: bool
    requirement_count: int
    missing_requirement_count: int
    operations: list[str]
    publication_required: bool
    private_scope_values_captured: bool
    private_repair_urls_captured: bool
    private_provider_identity_captured: bool


@dataclass(frozen=True)
class _RequirementSpec:
    provider_kind: str
    provider_identity: str
    operation: str
    required_scopes: tuple[str, ...]
    publication_required: bool
    repair_url: str

    def identity_key(self) -> tuple[str, str, str]:
        return (self.provider_kind, self.provider_identity, self.operation)

    def fingerprint(self) -> PermissionRequirementFingerprint:
        return (
            self.provider_kind,
            self.provider_identity,
            self.operation,
            self.required_scopes,
            self.publication_required,
            self.repair_url,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PERMISSION_REQUIREMENT_SCHEMA_VERSION,
            "provider_kind": self.provider_kind,
            "provider_identity": self.provider_identity,
            "operation": self.operation,
            "required_scopes": list(self.required_scopes),
            "publication_required": self.publication_required,
            "repair_url": self.repair_url,
        }


def safe_token(
    value: object,
    *,
    field: str,
    pattern: re.Pattern[str] = SAFE_TOKEN_PATTERN,
) -> str:
    """Validate and return a normalized opaque connector token."""
    normalized = str(value or "").strip()
    if not pattern.fullmatch(normalized):
        raise ValueError(f"{field} must be an opaque public-safe token")
    return normalized


def scope_values(values: Sequence[str], *, field: str) -> list[str]:
    """Validate and return sorted unique provider scope tokens."""
    normalized = {str(value or "").strip() for value in values}
    if not normalized or any(
        not SAFE_SCOPE_PATTERN.fullmatch(value) for value in normalized
    ):
        raise ValueError(f"{field} must contain provider scope tokens")
    return sorted(normalized)


def repair_url(value: object) -> str:
    """Validate a bounded HTTPS repair URL without credentials or fragments."""
    normalized = str(value or "").strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
        or len(normalized) > 2048
    ):
        raise ValueError("repair_url must be a bounded official HTTPS URL")
    return normalized


def _requirement_spec_from_args(
    *,
    provider_kind: str,
    provider_identity: str,
    operation: str,
    required_scopes: Sequence[str],
    publication_required: bool,
    repair_url_value: str,
) -> _RequirementSpec:
    try:
        capability = ExternalConnectorCapability(str(operation or "").strip()).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ExternalConnectorCapability)
        raise ValueError(f"operation must be one of: {allowed}") from exc
    return _RequirementSpec(
        provider_kind=safe_token(provider_kind, field="provider_kind"),
        provider_identity=safe_token(provider_identity, field="provider_identity"),
        operation=capability,
        required_scopes=tuple(scope_values(required_scopes, field="required_scopes")),
        publication_required=bool(publication_required),
        repair_url=repair_url(repair_url_value),
    )


def _normalize_requirement_spec(value: Mapping[str, object]) -> _RequirementSpec:
    if value.get("schema_version") != PERMISSION_REQUIREMENT_SCHEMA_VERSION:
        raise ValueError("external connector permission requirement schema is invalid")
    scopes = value.get("required_scopes")
    if not isinstance(scopes, Sequence) or isinstance(scopes, (str, bytes)):
        raise TypeError("required_scopes must be a sequence")
    return _requirement_spec_from_args(
        provider_kind=str(value.get("provider_kind") or ""),
        provider_identity=str(value.get("provider_identity") or ""),
        operation=str(value.get("operation") or ""),
        required_scopes=[str(scope) for scope in scopes],
        publication_required=value.get("publication_required") is True,
        repair_url_value=str(value.get("repair_url") or ""),
    )


def build_external_connector_permission_requirement(
    *,
    provider_kind: str,
    provider_identity: str,
    operation: str,
    required_scopes: Sequence[str],
    publication_required: bool,
    repair_url: str,
) -> dict[str, object]:
    """Build provider-owned, owner-local permission repair guidance."""

    return _requirement_spec_from_args(
        provider_kind=provider_kind,
        provider_identity=provider_identity,
        operation=operation,
        required_scopes=required_scopes,
        publication_required=publication_required,
        repair_url_value=repair_url,
    ).to_dict()


def normalize_permission_requirement(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Validate and normalize one permission requirement payload."""
    return _normalize_requirement_spec(value).to_dict()


def normalize_permission_requirement_specs(
    values: Sequence[Mapping[str, object]],
) -> list[_RequirementSpec]:
    """Validate, normalize, and check uniqueness for requirement specs."""
    if isinstance(values, (str, bytes)) or any(
        not isinstance(requirement, Mapping) for requirement in values
    ):
        raise TypeError("external connector permission requirements are invalid")
    specs: list[_RequirementSpec] = []
    identities: set[tuple[str, str, str]] = set()
    for requirement in values:
        spec = _normalize_requirement_spec(requirement)
        specs.append(spec)
        identities.add(spec.identity_key())
    if len(specs) != len(identities):
        raise ValueError("external connector permission requirements are duplicated")
    return specs


def normalize_permission_requirements(
    values: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Validate and normalize a unique sequence of permission requirements."""
    return [spec.to_dict() for spec in normalize_permission_requirement_specs(values)]


def permission_requirement_fingerprint(
    requirement: Mapping[str, object],
) -> PermissionRequirementFingerprint:
    """Return a hashable comparison tuple for one permission requirement."""
    raw_scopes = requirement["required_scopes"]
    if not isinstance(raw_scopes, Sequence) or isinstance(raw_scopes, (str, bytes)):
        raise TypeError("required_scopes must be a sequence")
    return (
        str(requirement["provider_kind"]),
        str(requirement["provider_identity"]),
        str(requirement["operation"]),
        tuple(str(scope) for scope in raw_scopes),
        bool(requirement["publication_required"]),
        str(requirement["repair_url"]),
    )


def evaluate_external_connector_permissions(
    *,
    requirements: Sequence[Mapping[str, object]],
    granted_scopes: Mapping[str, Sequence[str]],
    published: bool,
) -> dict[str, object]:
    """Return exact owner-local guidance without promoting it to public status."""

    specs = normalize_permission_requirement_specs(requirements)
    items: list[dict[str, object]] = []
    missing_count = 0
    granted_cache: dict[str, set[str]] = {}
    for spec in specs:
        granted = granted_cache.get(spec.provider_identity)
        if granted is None:
            granted = {
                str(scope or "").strip()
                for scope in granted_scopes.get(spec.provider_identity, ())
            }
            granted_cache[spec.provider_identity] = granted
        missing_scopes = [
            scope for scope in spec.required_scopes if scope not in granted
        ]
        publication_ready = bool(published or not spec.publication_required)
        ready = not missing_scopes and publication_ready
        if not ready:
            missing_count += 1
        items.append(
            {
                **spec.to_dict(),
                "missing_scopes": missing_scopes,
                "publication_ready": publication_ready,
                "ready": ready,
            }
        )
    return {
        "ok": True,
        "schema_version": PERMISSION_GUIDANCE_SCHEMA_VERSION,
        "ready": missing_count == 0,
        "requirement_count": len(items),
        "missing_requirement_count": missing_count,
        "items": items,
        "local_private_guidance_returned": bool(items),
        "credentials_captured": False,
    }


def _normalize_permission_guidance_items(
    value: Mapping[str, object],
) -> tuple[bool, int, list[tuple[_RequirementSpec, dict[str, object]]]]:
    if value.get("schema_version") != PERMISSION_GUIDANCE_SCHEMA_VERSION:
        raise ValueError("external connector permission guidance schema is invalid")
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or any(
        not isinstance(item, Mapping) for item in raw_items
    ):
        raise TypeError("external connector permission guidance items are invalid")
    entries: list[tuple[_RequirementSpec, dict[str, object]]] = []
    missing_count = 0
    for raw_item in raw_items:
        spec = _normalize_requirement_spec(raw_item)
        missing_scopes = raw_item.get("missing_scopes")
        if not isinstance(missing_scopes, list):
            raise TypeError("permission missing_scopes are invalid")
        normalized_missing = (
            scope_values(
                [str(scope) for scope in missing_scopes],
                field="missing_scopes",
            )
            if missing_scopes
            else []
        )
        if not set(normalized_missing).issubset(spec.required_scopes):
            raise ValueError("missing_scopes must belong to required_scopes")
        publication_ready = raw_item.get("publication_ready") is True
        expected_ready = not normalized_missing and publication_ready
        if (raw_item.get("ready") is True) != expected_ready:
            raise ValueError("permission guidance ready state is inconsistent")
        if not expected_ready:
            missing_count += 1
        entries.append(
            (
                spec,
                {
                    **spec.to_dict(),
                    "missing_scopes": normalized_missing,
                    "publication_ready": publication_ready,
                    "ready": expected_ready,
                },
            )
        )
    ready = missing_count == 0
    if (value.get("ready") is True) != ready:
        raise ValueError("permission guidance aggregate ready state is inconsistent")
    return ready, missing_count, entries


def normalize_permission_guidance(value: Mapping[str, object]) -> dict[str, object]:
    """Validate and normalize a permission guidance payload."""
    ready, missing_count, entries = _normalize_permission_guidance_items(value)
    return {
        "schema_version": PERMISSION_GUIDANCE_SCHEMA_VERSION,
        "ready": ready,
        "requirement_count": len(entries),
        "missing_requirement_count": missing_count,
        "items": [item for _, item in entries],
    }


def _project_permission_status_entries(
    *,
    ready: bool,
    missing_count: int,
    entries: Sequence[tuple[_RequirementSpec, Mapping[str, object]]],
) -> ExternalConnectorPermissionStatus:
    operations = sorted(
        {safe_token(spec.operation, field="operation") for spec, _ in entries}
    )
    publication_required = any(spec.publication_required for spec, _ in entries)
    return {
        "schema_version": PERMISSION_STATUS_SCHEMA_VERSION,
        "ready": ready,
        "requirement_count": len(entries),
        "missing_requirement_count": missing_count,
        "operations": operations,
        "publication_required": publication_required,
        "private_scope_values_captured": False,
        "private_repair_urls_captured": False,
        "private_provider_identity_captured": False,
    }


def project_permission_status_for_registration(
    *,
    expected_specs: Sequence[_RequirementSpec],
    permission_guidance: Mapping[str, object],
) -> ExternalConnectorPermissionStatus:
    """Validate guidance alignment against registration specs and project status once."""
    ready, missing_count, entries = _normalize_permission_guidance_items(
        permission_guidance
    )
    expected = sorted(spec.fingerprint() for spec in expected_specs)
    observed = sorted(spec.fingerprint() for spec, _ in entries)
    if observed != expected:
        raise ValueError(
            "permission guidance must match the registered Connector requirements"
        )
    return _project_permission_status_entries(
        ready=ready,
        missing_count=missing_count,
        entries=entries,
    )


def permission_guidance_for_registration(
    *,
    registration: Mapping[str, object],
    permission_guidance: Mapping[str, object],
) -> dict[str, object]:
    """Verify permission guidance covers exact registration requirement fingerprints."""
    raw_expected = registration["permission_requirements"]
    if not isinstance(raw_expected, Sequence) or isinstance(raw_expected, (str, bytes)):
        raise TypeError("registration permission_requirements are invalid")
    expected_specs = [
        _normalize_requirement_spec(item)
        if isinstance(item, Mapping)
        else _invalid_requirement_type()
        for item in raw_expected
    ]
    ready, missing_count, entries = _normalize_permission_guidance_items(
        permission_guidance
    )
    expected = sorted(spec.fingerprint() for spec in expected_specs)
    observed = sorted(spec.fingerprint() for spec, _ in entries)
    if observed != expected:
        raise ValueError(
            "permission guidance must match the registered Connector requirements"
        )
    return {
        "schema_version": PERMISSION_GUIDANCE_SCHEMA_VERSION,
        "ready": ready,
        "requirement_count": len(entries),
        "missing_requirement_count": missing_count,
        "items": [item for _, item in entries],
    }


def _invalid_requirement_type() -> _RequirementSpec:
    raise TypeError("external connector permission requirements are invalid")


def project_external_connector_permission_status(
    guidance: Mapping[str, object],
) -> ExternalConnectorPermissionStatus:
    """Hide exact scopes and repair URLs from status and quota projections."""

    ready, missing_count, entries = _normalize_permission_guidance_items(guidance)
    return _project_permission_status_entries(
        ready=ready,
        missing_count=missing_count,
        entries=entries,
    )


def validate_permission_requirements_for_connector(
    *,
    connector: Mapping[str, object],
    requirements: Sequence[Mapping[str, object]],
) -> None:
    """Ensure registered permission requirements cover Connector operations."""
    specs = [
        _RequirementSpec(
            provider_kind=str(req["provider_kind"]),
            provider_identity="",
            operation=str(req["operation"]),
            required_scopes=(),
            publication_required=False,
            repair_url="",
        )
        for req in requirements
    ]
    validate_permission_specs_for_connector(connector=connector, specs=specs)


def validate_permission_specs_for_connector(
    *,
    connector: Mapping[str, object],
    specs: Sequence[_RequirementSpec],
) -> None:
    """Validate provider kind and advertised capability coverage against specs."""
    raw_capabilities = connector.get("capabilities")
    if not isinstance(raw_capabilities, Sequence) or isinstance(
        raw_capabilities, (str, bytes)
    ):
        raise TypeError("connector capabilities must be a sequence")
    capability_values = {str(cap) for cap in raw_capabilities}
    provider_kind = str(connector.get("provider_kind") or "")
    for spec in specs:
        if spec.provider_kind != provider_kind:
            raise ValueError("permission provider_kind must match the Connector")
        if spec.operation not in capability_values:
            raise ValueError("permission operation must be advertised by the Connector")
    required_operations = {ExternalConnectorCapability.HISTORY_CATCH_UP.value}
    if connector.get("response_policy") != ExternalResponsePolicy.NO_RESPONSE.value:
        required_operations.update(
            {
                ExternalConnectorCapability.RESPONSE_WRITE.value,
                ExternalConnectorCapability.RESPONSE_READBACK.value,
            }
        )
    if required_operations.difference(spec.operation for spec in specs):
        raise ValueError(
            "document-comment permission requirements do not cover provider calls"
        )
