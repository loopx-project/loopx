"""Provider call boundary for Agent-scoped external Connectors.

The provider owns credentials, API payloads, exact scopes, repair URLs, and
external writes.  LoopX owns the typed binding, owner-local inbox, durable
effect ordering, and content-free status projection.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from ..authority import normalize_project_materials
from .external_connector_runtime import (
    CONNECTOR_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    SAFE_TOKEN_PATTERN,
    ExternalConnectorCapability,
    ExternalResponsePolicy,
    ExternalSourceKind,
    build_external_event_response_receipt,
    capture_external_connector_events,
    decide_external_event_ack,
    drain_external_connector_inbox,
    normalize_external_connector_binding,
    project_external_connector_status,
    settle_external_connector_event,
)

PERMISSION_REQUIREMENT_SCHEMA_VERSION = (
    "agent_external_connector_permission_requirement_v0"
)
PERMISSION_GUIDANCE_SCHEMA_VERSION = "agent_external_connector_permission_guidance_v0"
DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION = (
    "agent_document_comment_connector_registration_v0"
)
PROVIDER_PAGE_SCHEMA_VERSION = "agent_external_connector_provider_page_v0"

SAFE_SCOPE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")

ProviderPageReader = Callable[[Mapping[str, Any], str | None, int], Mapping[str, Any]]
ProviderResponseWriter = Callable[
    [Mapping[str, Any], Mapping[str, Any], str, str],
    Mapping[str, Any],
]


def _safe_token(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not SAFE_TOKEN_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be an opaque public-safe token")
    return normalized


def _scope_values(values: Sequence[str], *, field: str) -> list[str]:
    normalized = {str(value or "").strip() for value in values}
    if not normalized or any(
        not SAFE_SCOPE_PATTERN.fullmatch(value) for value in normalized
    ):
        raise ValueError(f"{field} must contain provider scope tokens")
    return sorted(normalized)


def _repair_url(value: Any) -> str:
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


def build_external_connector_permission_requirement(
    *,
    provider_kind: str,
    provider_identity: str,
    operation: str,
    required_scopes: Sequence[str],
    publication_required: bool,
    repair_url: str,
) -> dict[str, Any]:
    """Build provider-owned, owner-local permission repair guidance."""

    try:
        capability = ExternalConnectorCapability(str(operation or "").strip()).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ExternalConnectorCapability)
        raise ValueError(f"operation must be one of: {allowed}") from exc
    return {
        "schema_version": PERMISSION_REQUIREMENT_SCHEMA_VERSION,
        "provider_kind": _safe_token(provider_kind, field="provider_kind"),
        "provider_identity": _safe_token(
            provider_identity,
            field="provider_identity",
        ),
        "operation": capability,
        "required_scopes": _scope_values(
            required_scopes,
            field="required_scopes",
        ),
        "publication_required": bool(publication_required),
        "repair_url": _repair_url(repair_url),
    }


def _normalize_permission_requirement(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != PERMISSION_REQUIREMENT_SCHEMA_VERSION:
        raise ValueError("external connector permission requirement schema is invalid")
    scopes = value.get("required_scopes")
    if not isinstance(scopes, Sequence) or isinstance(scopes, (str, bytes)):
        raise TypeError("required_scopes must be a sequence")
    return build_external_connector_permission_requirement(
        provider_kind=str(value.get("provider_kind") or ""),
        provider_identity=str(value.get("provider_identity") or ""),
        operation=str(value.get("operation") or ""),
        required_scopes=[str(scope) for scope in scopes],
        publication_required=value.get("publication_required") is True,
        repair_url=str(value.get("repair_url") or ""),
    )


def _normalize_permission_requirements(
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(values, (str, bytes)) or any(
        not isinstance(requirement, Mapping) for requirement in values
    ):
        raise TypeError("external connector permission requirements are invalid")
    requirements = [
        _normalize_permission_requirement(requirement) for requirement in values
    ]
    identities = [
        (
            requirement["provider_kind"],
            requirement["provider_identity"],
            requirement["operation"],
        )
        for requirement in requirements
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("external connector permission requirements are duplicated")
    return requirements


def _permission_requirement_fingerprint(
    requirement: Mapping[str, Any],
) -> tuple[Any, ...]:
    return (
        requirement["provider_kind"],
        requirement["provider_identity"],
        requirement["operation"],
        tuple(requirement["required_scopes"]),
        requirement["publication_required"],
        requirement["repair_url"],
    )


def evaluate_external_connector_permissions(
    *,
    requirements: Sequence[Mapping[str, Any]],
    granted_scopes: Mapping[str, Sequence[str]],
    published: bool,
) -> dict[str, Any]:
    """Return exact owner-local guidance without promoting it to public status."""

    normalized_requirements = _normalize_permission_requirements(requirements)
    items: list[dict[str, Any]] = []
    for requirement in normalized_requirements:
        identity = str(requirement["provider_identity"])
        granted = {
            str(scope or "").strip() for scope in granted_scopes.get(identity, [])
        }
        missing_scopes = [
            scope for scope in requirement["required_scopes"] if scope not in granted
        ]
        publication_ready = bool(
            published or requirement["publication_required"] is not True
        )
        ready = not missing_scopes and publication_ready
        items.append(
            {
                **requirement,
                "missing_scopes": missing_scopes,
                "publication_ready": publication_ready,
                "ready": ready,
            }
        )
    return {
        "ok": True,
        "schema_version": PERMISSION_GUIDANCE_SCHEMA_VERSION,
        "ready": all(item["ready"] for item in items),
        "requirement_count": len(items),
        "missing_requirement_count": sum(not item["ready"] for item in items),
        "items": items,
        "local_private_guidance_returned": bool(items),
        "credentials_captured": False,
    }


def _normalize_permission_guidance(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != PERMISSION_GUIDANCE_SCHEMA_VERSION:
        raise ValueError("external connector permission guidance schema is invalid")
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or any(
        not isinstance(item, Mapping) for item in raw_items
    ):
        raise TypeError("external connector permission guidance items are invalid")
    items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        requirement = _normalize_permission_requirement(raw_item)
        missing_scopes = raw_item.get("missing_scopes")
        if not isinstance(missing_scopes, list):
            raise TypeError("permission missing_scopes are invalid")
        normalized_missing = (
            _scope_values(
                [str(scope) for scope in missing_scopes],
                field="missing_scopes",
            )
            if missing_scopes
            else []
        )
        if not set(normalized_missing).issubset(requirement["required_scopes"]):
            raise ValueError("missing_scopes must belong to required_scopes")
        publication_ready = raw_item.get("publication_ready") is True
        expected_ready = not normalized_missing and publication_ready
        if (raw_item.get("ready") is True) != expected_ready:
            raise ValueError("permission guidance ready state is inconsistent")
        items.append(
            {
                **requirement,
                "missing_scopes": normalized_missing,
                "publication_ready": publication_ready,
                "ready": expected_ready,
            }
        )
    ready = all(item["ready"] for item in items)
    if (value.get("ready") is True) != ready:
        raise ValueError("permission guidance aggregate ready state is inconsistent")
    return {
        "schema_version": PERMISSION_GUIDANCE_SCHEMA_VERSION,
        "ready": ready,
        "requirement_count": len(items),
        "missing_requirement_count": sum(not item["ready"] for item in items),
        "items": items,
    }


def _permission_guidance_for_registration(
    *,
    registration: Mapping[str, Any],
    permission_guidance: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_guidance = _normalize_permission_guidance(permission_guidance)
    expected = sorted(
        _permission_requirement_fingerprint(requirement)
        for requirement in registration["permission_requirements"]
    )
    observed = sorted(
        _permission_requirement_fingerprint(requirement)
        for requirement in normalized_guidance["items"]
    )
    if observed != expected:
        raise ValueError(
            "permission guidance must match the registered Connector requirements"
        )
    return normalized_guidance


def project_external_connector_permission_status(
    guidance: Mapping[str, Any],
) -> dict[str, Any]:
    """Hide exact scopes and repair URLs from status and quota projections."""

    normalized = _normalize_permission_guidance(guidance)
    items = normalized["items"]
    operations = sorted(
        {
            _safe_token(item.get("operation"), field="operation")
            for item in items
            if isinstance(item, Mapping)
        }
    )
    return {
        "schema_version": "agent_external_connector_permission_status_v0",
        "ready": normalized["ready"],
        "requirement_count": len(items),
        "missing_requirement_count": sum(
            isinstance(item, Mapping) and item.get("ready") is not True
            for item in items
        ),
        "operations": operations,
        "publication_required": any(
            isinstance(item, Mapping) and item.get("publication_required") is True
            for item in items
        ),
        "private_scope_values_captured": False,
        "private_repair_urls_captured": False,
        "private_provider_identity_captured": False,
    }


def _validate_permission_requirements_for_connector(
    *,
    connector: Mapping[str, Any],
    requirements: Sequence[Mapping[str, Any]],
) -> None:
    capability_values = set(connector["capabilities"])
    for requirement in requirements:
        if requirement["provider_kind"] != connector["provider_kind"]:
            raise ValueError("permission provider_kind must match the Connector")
        if requirement["operation"] not in capability_values:
            raise ValueError("permission operation must be advertised by the Connector")
    required_operations = {ExternalConnectorCapability.HISTORY_CATCH_UP.value}
    if connector["response_policy"] != ExternalResponsePolicy.NO_RESPONSE.value:
        required_operations.update(
            {
                ExternalConnectorCapability.RESPONSE_WRITE.value,
                ExternalConnectorCapability.RESPONSE_READBACK.value,
            }
        )
    missing_operations = required_operations.difference(
        requirement["operation"] for requirement in requirements
    )
    if missing_operations:
        raise ValueError(
            "document-comment permission requirements do not cover provider calls"
        )


def build_document_comment_connector_registration(
    *,
    connector: Mapping[str, Any],
    authority_material_ref: str,
    project_materials: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    permission_requirements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Link comments to a separately registered material without granting authority."""

    normalized_connector = normalize_external_connector_binding(connector)
    if normalized_connector["source_kind"] != ExternalSourceKind.DOCUMENT_COMMENT.value:
        raise ValueError("document-comment registration requires document_comment")
    material_ref = _safe_token(
        authority_material_ref,
        field="authority_material_ref",
    )
    materials = normalize_project_materials(project_materials)
    if material_ref not in materials:
        raise ValueError("authority material must be registered before its comments")
    requirements = _normalize_permission_requirements(permission_requirements)
    _validate_permission_requirements_for_connector(
        connector=normalized_connector,
        requirements=requirements,
    )
    return {
        "schema_version": DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION,
        "connector": normalized_connector,
        "authority_material_ref": material_ref,
        "authority_relation": "reference_only",
        "comment_authority": "external_input_only",
        "permission_requirements": requirements,
    }


def project_document_comment_connector_status(
    *,
    registration: Mapping[str, Any],
    permission_guidance: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a content-free registration and permission status."""

    normalized = _normalize_document_comment_registration(registration)
    normalized_guidance = _permission_guidance_for_registration(
        registration=normalized,
        permission_guidance=permission_guidance,
    )
    return {
        "schema_version": "agent_document_comment_connector_status_v0",
        "connector": project_external_connector_status(normalized["connector"]),
        "authority_material_bound": True,
        "authority_relation": normalized["authority_relation"],
        "comment_authority": normalized["comment_authority"],
        "permissions": project_external_connector_permission_status(
            normalized_guidance
        ),
        "private_authority_material_ref_captured": False,
    }


def _normalize_document_comment_registration(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if value.get("schema_version") != DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("document-comment Connector registration schema is invalid")
    connector = value.get("connector")
    if not isinstance(connector, Mapping):
        raise TypeError("document-comment Connector is invalid")
    normalized_connector = normalize_external_connector_binding(connector)
    if normalized_connector["schema_version"] != CONNECTOR_SCHEMA_VERSION:
        raise ValueError("document-comment Connector schema is invalid")
    if normalized_connector["source_kind"] != ExternalSourceKind.DOCUMENT_COMMENT.value:
        raise ValueError("document-comment registration source kind is invalid")
    if value.get("authority_relation") != "reference_only":
        raise ValueError("document-comment authority relation is invalid")
    if value.get("comment_authority") != "external_input_only":
        raise ValueError("document-comment authority policy is invalid")
    requirements = value.get("permission_requirements")
    if not isinstance(requirements, list) or any(
        not isinstance(requirement, Mapping) for requirement in requirements
    ):
        raise TypeError("document-comment permission requirements are invalid")
    normalized_requirements = _normalize_permission_requirements(requirements)
    _validate_permission_requirements_for_connector(
        connector=normalized_connector,
        requirements=normalized_requirements,
    )
    return {
        "schema_version": DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION,
        "connector": normalized_connector,
        "authority_material_ref": _safe_token(
            value.get("authority_material_ref"),
            field="authority_material_ref",
        ),
        "authority_relation": "reference_only",
        "comment_authority": "external_input_only",
        "permission_requirements": normalized_requirements,
    }


def build_external_connector_provider_page(
    *,
    events: Sequence[Mapping[str, Any]],
    expected_cursor: str | None,
    next_cursor: str,
    has_more: bool,
) -> dict[str, Any]:
    """Build one owner-private page returned by a provider adapter."""

    if len(events) > 500:
        raise ValueError("external connector provider page exceeds 500 events")
    for event in events:
        if not isinstance(event, Mapping):
            raise TypeError("provider page events must be objects")
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise ValueError("provider page contains an invalid event schema")
    normalized_next_cursor = str(next_cursor or "").strip()
    if not normalized_next_cursor or len(normalized_next_cursor) > 2048:
        raise ValueError("next_cursor must be a bounded owner-private value")
    normalized_expected_cursor = (
        str(expected_cursor).strip() if expected_cursor is not None else None
    )
    if normalized_expected_cursor is not None and (
        not normalized_expected_cursor or len(normalized_expected_cursor) > 2048
    ):
        raise ValueError("expected_cursor must be a bounded owner-private value")
    return {
        "schema_version": PROVIDER_PAGE_SCHEMA_VERSION,
        "expected_cursor": normalized_expected_cursor,
        "next_cursor": normalized_next_cursor,
        "has_more": bool(has_more),
        "events": [dict(event) for event in events],
    }


def capture_document_comment_provider_page(
    *,
    project: str,
    registration: Mapping[str, Any],
    permission_guidance: Mapping[str, Any],
    page_reader: ProviderPageReader,
    expected_cursor: str | None,
    limit: int = 100,
    max_pending: int = 1000,
    execute: bool = False,
) -> dict[str, Any]:
    """Read and durably capture one provider page after permission readiness."""

    normalized = _normalize_document_comment_registration(registration)
    normalized_guidance = _permission_guidance_for_registration(
        registration=normalized,
        permission_guidance=permission_guidance,
    )
    permission_status = project_external_connector_permission_status(
        normalized_guidance
    )
    if permission_status["ready"] is not True:
        return {
            "ok": False,
            "schema_version": "agent_external_connector_provider_capture_v0",
            "status": "permission_required",
            "execute": execute,
            "external_read_performed": False,
            "private_provider_payload_captured": False,
        }
    bounded_limit = max(1, min(int(limit), 500))
    if not execute:
        return {
            "ok": True,
            "schema_version": "agent_external_connector_provider_capture_v0",
            "status": "preview_ready",
            "execute": False,
            "external_read_performed": False,
            "private_provider_payload_captured": False,
        }
    page = page_reader(normalized["connector"], expected_cursor, bounded_limit)
    if not isinstance(page, Mapping):
        raise TypeError("external connector provider page is invalid")
    if page.get("schema_version") != PROVIDER_PAGE_SCHEMA_VERSION:
        raise ValueError("external connector provider page schema is invalid")
    if page.get("expected_cursor") != expected_cursor:
        raise ValueError("provider page does not match the requested cursor")
    events = page.get("events")
    if not isinstance(events, list) or any(
        not isinstance(event, Mapping) for event in events
    ):
        raise TypeError("external connector provider page events are invalid")
    if len(events) > bounded_limit:
        raise ValueError("external connector provider exceeded the requested limit")
    capture = capture_external_connector_events(
        project=project,
        binding=normalized["connector"],
        events=events,
        expected_cursor=expected_cursor,
        next_cursor=str(page.get("next_cursor") or ""),
        max_pending=max_pending,
        execute=True,
    )
    return {
        **capture,
        "schema_version": "agent_external_connector_provider_capture_v0",
        "provider_status": "captured" if capture["ok"] else capture["status"],
        "has_more": page.get("has_more") is True,
        "external_read_performed": True,
        "private_provider_payload_captured": False,
    }


def reply_and_settle_document_comment_event(
    *,
    project: str,
    registration: Mapping[str, Any],
    permission_guidance: Mapping[str, Any],
    event_id: str,
    effect_receipt: Mapping[str, Any] | None,
    reply_text: str | None,
    response_writer: ProviderResponseWriter | None,
    execute: bool = False,
) -> dict[str, Any]:
    """Enforce effect-before-response-before-ACK at the provider call site."""

    normalized = _normalize_document_comment_registration(registration)
    connector = normalized["connector"]
    normalized_guidance = _permission_guidance_for_registration(
        registration=normalized,
        permission_guidance=permission_guidance,
    )
    permission_status = project_external_connector_permission_status(
        normalized_guidance
    )
    if permission_status["ready"] is not True:
        return {
            "ok": False,
            "schema_version": "agent_external_connector_provider_settlement_v0",
            "status": "permission_required",
            "execute": execute,
            "external_write_performed": False,
        }
    pending = drain_external_connector_inbox(
        project=project,
        binding=connector,
        limit=1,
    )
    items = pending.get("items")
    item = items[0] if isinstance(items, list) and items else None
    if not isinstance(item, Mapping) or str(item.get("event_id") or "") != event_id:
        return {
            "ok": False,
            "schema_version": "agent_external_connector_provider_settlement_v0",
            "status": "ordered_event_required",
            "execute": execute,
            "external_write_performed": False,
        }
    effect_decision = decide_external_event_ack(
        event_id=event_id,
        effect_receipt=effect_receipt,
        response_policy=ExternalResponsePolicy.NO_RESPONSE.value,
    )
    if effect_decision["effect_ready"] is not True:
        return {
            "ok": False,
            "schema_version": "agent_external_connector_provider_settlement_v0",
            "status": "durable_effect_required",
            "execute": execute,
            "external_write_performed": False,
            "ack_decision": effect_decision,
        }
    response_policy = str(connector["response_policy"])
    response_receipt: Mapping[str, Any] | None = None
    if response_policy != ExternalResponsePolicy.NO_RESPONSE.value:
        text = " ".join(str(reply_text or "").split())[:1200]
        if not text:
            raise ValueError("response-capable Connector requires reply_text")
        if response_writer is None:
            raise ValueError("response-capable Connector requires a response_writer")
        if not execute:
            return {
                "ok": True,
                "schema_version": "agent_external_connector_provider_settlement_v0",
                "status": "preview_ready",
                "execute": False,
                "external_write_performed": False,
            }
        idempotency_key = (
            "sha256:"
            + hashlib.sha256(
                "\0".join(
                    (
                        event_id,
                        str((effect_receipt or {}).get("effect_id") or ""),
                        text,
                    )
                ).encode("utf-8")
            ).hexdigest()
        )
        response_receipt = response_writer(
            connector,
            item,
            text,
            idempotency_key,
        )
        if not isinstance(response_receipt, Mapping):
            raise TypeError("response_writer must return a response receipt")
        if response_receipt.get("idempotency_key") != idempotency_key:
            raise ValueError("response receipt must bind the requested idempotency_key")
        response_receipt = build_external_event_response_receipt(
            event_id=event_id,
            external_write_performed=(
                response_receipt.get("external_write_performed") is True
            ),
            verification_performed=(
                response_receipt.get("verification_performed") is True
            ),
            response_verified=(
                response_receipt.get("response_verified") is True
                or response_receipt.get("reply_verified") is True
                or response_receipt.get("readback_verified") is True
            ),
        )
    settlement = settle_external_connector_event(
        project=project,
        binding=connector,
        event_id=event_id,
        effect_receipt=effect_receipt,
        response_receipt=response_receipt,
        execute=execute,
    )
    return {
        **settlement,
        "schema_version": "agent_external_connector_provider_settlement_v0",
        "external_write_performed": bool(
            response_receipt
            and response_receipt.get("external_write_performed") is True
        ),
        "response_readback_verified": bool(
            response_receipt
            and response_receipt.get("verification_performed") is True
            and response_receipt.get("response_verified") is True
        ),
        "private_provider_payload_captured": False,
    }
