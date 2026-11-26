"""Provider call boundary for Agent-scoped external Connectors.

The provider owns credentials, API payloads, exact scopes, repair URLs, and
external writes.  LoopX owns the typed binding, owner-local inbox, durable
effect ordering, and content-free status projection.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
from typing import TypedDict

from ..authority import normalize_project_materials
from .external_connector_permissions import (
    PERMISSION_GUIDANCE_SCHEMA_VERSION,
    PERMISSION_REQUIREMENT_SCHEMA_VERSION,
    SAFE_SCOPE_PATTERN,
    ExternalConnectorPermissionStatus,
    _RequirementSpec,
    build_external_connector_permission_requirement,
    evaluate_external_connector_permissions,
    normalize_permission_guidance as _normalize_permission_guidance,
    normalize_permission_requirement as _normalize_permission_requirement,
    normalize_permission_requirement_specs,
    normalize_permission_requirements as _normalize_permission_requirements,
    permission_guidance_for_registration as _permission_guidance_for_registration,
    permission_requirement_fingerprint as _permission_requirement_fingerprint,
    project_external_connector_permission_status,
    project_permission_status_for_registration,
    repair_url as _repair_url,
    safe_token,
    scope_values as _scope_values,
    validate_permission_requirements_for_connector as _validate_permission_requirements_for_connector,
    validate_permission_specs_for_connector,
)
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

DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION = (
    "agent_document_comment_connector_registration_v0"
)
PROVIDER_PAGE_SCHEMA_VERSION = "agent_external_connector_provider_page_v0"
PROVIDER_CAPTURE_SCHEMA_VERSION = "agent_external_connector_provider_capture_v0"
PROVIDER_SETTLEMENT_SCHEMA_VERSION = "agent_external_connector_provider_settlement_v0"

ProviderPageReader = Callable[
    [dict[str, object], str | None, int],
    Mapping[str, object],
]
ProviderResponseWriter = Callable[
    [dict[str, object], dict[str, object], str, str],
    Mapping[str, object],
]


class DocumentCommentConnectorRegistration(TypedDict):
    """Typed document-comment connector registration payload."""

    schema_version: str
    connector: dict[str, object]
    authority_material_ref: str
    authority_relation: str
    comment_authority: str
    permission_requirements: list[dict[str, object]]


class DocumentCommentConnectorStatus(TypedDict):
    """Typed content-free document-comment connector status projection."""

    schema_version: str
    connector: dict[str, object]
    authority_material_bound: bool
    authority_relation: str
    comment_authority: str
    permissions: ExternalConnectorPermissionStatus
    private_authority_material_ref_captured: bool


def _safe_token(value: object, *, field: str) -> str:
    return safe_token(value, field=field, pattern=SAFE_TOKEN_PATTERN)


def _normalized_registration_parts(
    value: Mapping[str, object],
) -> tuple[DocumentCommentConnectorRegistration, dict[str, object], list[_RequirementSpec]]:
    if value.get("schema_version") != DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("document-comment Connector registration schema is invalid")
    connector = value.get("connector")
    if not isinstance(connector, Mapping):
        raise TypeError("document-comment Connector is invalid")
    raw_connector = normalize_external_connector_binding(connector)
    normalized_connector: dict[str, object] = {
        str(key): item for key, item in raw_connector.items()
    }
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
    specs = normalize_permission_requirement_specs(requirements)
    validate_permission_specs_for_connector(
        connector=normalized_connector,
        specs=specs,
    )
    registration: DocumentCommentConnectorRegistration = {
        "schema_version": DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION,
        "connector": normalized_connector,
        "authority_material_ref": _safe_token(
            value.get("authority_material_ref"),
            field="authority_material_ref",
        ),
        "authority_relation": "reference_only",
        "comment_authority": "external_input_only",
        "permission_requirements": [spec.to_dict() for spec in specs],
    }
    return registration, normalized_connector, specs


def _normalize_document_comment_registration(
    value: Mapping[str, object],
) -> DocumentCommentConnectorRegistration:
    registration, _, _ = _normalized_registration_parts(value)
    return registration


def build_document_comment_connector_registration(
    *,
    connector: Mapping[str, object],
    authority_material_ref: str,
    project_materials: Mapping[str, object] | Sequence[Mapping[str, object]],
    permission_requirements: Sequence[Mapping[str, object]],
) -> DocumentCommentConnectorRegistration:
    """Link comments to a separately registered material without granting authority."""

    raw_connector = normalize_external_connector_binding(connector)
    normalized_connector: dict[str, object] = {
        str(key): item for key, item in raw_connector.items()
    }
    if normalized_connector["source_kind"] != ExternalSourceKind.DOCUMENT_COMMENT.value:
        raise ValueError("document-comment registration requires document_comment")
    material_ref = _safe_token(
        authority_material_ref,
        field="authority_material_ref",
    )
    normalized_materials: object = project_materials
    if isinstance(project_materials, Mapping):
        normalized_materials = {
            str(key): dict(entry) if isinstance(entry, Mapping) else entry
            for key, entry in project_materials.items()
        }
    elif isinstance(project_materials, Sequence) and not isinstance(
        project_materials, (str, bytes)
    ):
        normalized_materials = [
            dict(entry) if isinstance(entry, Mapping) else entry
            for entry in project_materials
        ]
    materials = normalize_project_materials(normalized_materials)
    if material_ref not in materials:
        raise ValueError("authority material must be registered before its comments")
    specs = normalize_permission_requirement_specs(permission_requirements)
    validate_permission_specs_for_connector(
        connector=normalized_connector,
        specs=specs,
    )
    return {
        "schema_version": DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION,
        "connector": normalized_connector,
        "authority_material_ref": material_ref,
        "authority_relation": "reference_only",
        "comment_authority": "external_input_only",
        "permission_requirements": [spec.to_dict() for spec in specs],
    }


def project_document_comment_connector_status(
    *,
    registration: Mapping[str, object],
    permission_guidance: Mapping[str, object],
) -> DocumentCommentConnectorStatus:
    """Project a content-free registration and permission status."""

    normalized, connector, expected_specs = _normalized_registration_parts(
        registration
    )
    permission_status = project_permission_status_for_registration(
        expected_specs=expected_specs,
        permission_guidance=permission_guidance,
    )
    raw_status = project_external_connector_status(connector)
    return {
        "schema_version": "agent_document_comment_connector_status_v0",
        "connector": {str(key): item for key, item in raw_status.items()},
        "authority_material_bound": True,
        "authority_relation": normalized["authority_relation"],
        "comment_authority": normalized["comment_authority"],
        "permissions": permission_status,
        "private_authority_material_ref_captured": False,
    }


def build_external_connector_provider_page(
    *,
    events: Sequence[Mapping[str, object]],
    expected_cursor: str | None,
    next_cursor: str,
    has_more: bool,
) -> dict[str, object]:
    """Build one owner-private page returned by a provider adapter."""

    if len(events) > 500:
        raise ValueError("external connector provider page exceeds 500 events")
    copied_events: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise TypeError("provider page events must be objects")
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise ValueError("provider page contains an invalid event schema")
        copied_events.append(dict(event))
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
        "events": copied_events,
    }


def capture_document_comment_provider_page(
    *,
    project: str,
    registration: Mapping[str, object],
    permission_guidance: Mapping[str, object],
    page_reader: ProviderPageReader,
    expected_cursor: str | None,
    limit: int = 100,
    max_pending: int = 1000,
    execute: bool = False,
) -> dict[str, object]:
    """Read and durably capture one provider page after permission readiness."""

    _, connector, expected_specs = _normalized_registration_parts(registration)
    permission_status = project_permission_status_for_registration(
        expected_specs=expected_specs,
        permission_guidance=permission_guidance,
    )
    if permission_status["ready"] is not True:
        return {
            "ok": False,
            "schema_version": PROVIDER_CAPTURE_SCHEMA_VERSION,
            "status": "permission_required",
            "execute": execute,
            "external_read_performed": False,
            "private_provider_payload_captured": False,
        }
    bounded_limit = max(1, min(int(limit), 500))
    if not execute:
        return {
            "ok": True,
            "schema_version": PROVIDER_CAPTURE_SCHEMA_VERSION,
            "status": "preview_ready",
            "execute": False,
            "external_read_performed": False,
            "private_provider_payload_captured": False,
        }
    page = page_reader(connector, expected_cursor, bounded_limit)
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
        binding=connector,
        events=events,
        expected_cursor=expected_cursor,
        next_cursor=str(page.get("next_cursor") or ""),
        max_pending=max_pending,
        execute=True,
    )
    return {
        **capture,
        "schema_version": PROVIDER_CAPTURE_SCHEMA_VERSION,
        "provider_status": "captured" if capture["ok"] else capture["status"],
        "has_more": page.get("has_more") is True,
        "external_read_performed": True,
        "private_provider_payload_captured": False,
    }


def _settlement_guard_result(
    *,
    ok: bool,
    status: str,
    execute: bool,
    ack_decision: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": ok,
        "schema_version": PROVIDER_SETTLEMENT_SCHEMA_VERSION,
        "status": status,
        "execute": execute,
        "external_write_performed": False,
    }
    if ack_decision is not None:
        payload["ack_decision"] = dict(ack_decision)
    return payload


def reply_and_settle_document_comment_event(
    *,
    project: str,
    registration: Mapping[str, object],
    permission_guidance: Mapping[str, object],
    event_id: str,
    effect_receipt: Mapping[str, object] | None,
    reply_text: str | None,
    response_writer: ProviderResponseWriter | None,
    execute: bool = False,
) -> dict[str, object]:
    """Enforce effect-before-response-before-ACK at the provider call site."""

    _, connector, expected_specs = _normalized_registration_parts(registration)
    permission_status = project_permission_status_for_registration(
        expected_specs=expected_specs,
        permission_guidance=permission_guidance,
    )
    if permission_status["ready"] is not True:
        return _settlement_guard_result(
            ok=False,
            status="permission_required",
            execute=execute,
        )
    pending = drain_external_connector_inbox(
        project=project,
        binding=connector,
        limit=1,
    )
    items = pending.get("items")
    item = items[0] if isinstance(items, list) and items else None
    if not isinstance(item, Mapping) or str(item.get("event_id") or "") != event_id:
        return _settlement_guard_result(
            ok=False,
            status="ordered_event_required",
            execute=execute,
        )
    effect_decision = decide_external_event_ack(
        event_id=event_id,
        effect_receipt=effect_receipt,
        response_policy=ExternalResponsePolicy.NO_RESPONSE.value,
    )
    if effect_decision["effect_ready"] is not True:
        return _settlement_guard_result(
            ok=False,
            status="durable_effect_required",
            execute=execute,
            ack_decision=effect_decision,
        )
    response_policy = str(connector["response_policy"])
    response_receipt: Mapping[str, object] | None = None
    if response_policy != ExternalResponsePolicy.NO_RESPONSE.value:
        text = " ".join(str(reply_text or "").split())[:1200]
        if not text:
            raise ValueError("response-capable Connector requires reply_text")
        if response_writer is None:
            raise ValueError("response-capable Connector requires a response_writer")
        if not execute:
            return _settlement_guard_result(
                ok=True,
                status="preview_ready",
                execute=False,
            )
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
        raw_receipt = response_writer(
            connector,
            dict(item),
            text,
            idempotency_key,
        )
        if not isinstance(raw_receipt, Mapping):
            raise TypeError("response_writer must return a response receipt")
        if raw_receipt.get("idempotency_key") != idempotency_key:
            raise ValueError("response receipt must bind the requested idempotency_key")
        response_receipt = build_external_event_response_receipt(
            event_id=event_id,
            external_write_performed=(
                raw_receipt.get("external_write_performed") is True
            ),
            verification_performed=(
                raw_receipt.get("verification_performed") is True
            ),
            response_verified=(
                raw_receipt.get("response_verified") is True
                or raw_receipt.get("reply_verified") is True
                or raw_receipt.get("readback_verified") is True
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
        "schema_version": PROVIDER_SETTLEMENT_SCHEMA_VERSION,
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


__all__ = [
    "DOCUMENT_COMMENT_REGISTRATION_SCHEMA_VERSION",
    "EVENT_SCHEMA_VERSION",
    "PERMISSION_GUIDANCE_SCHEMA_VERSION",
    "PERMISSION_REQUIREMENT_SCHEMA_VERSION",
    "PROVIDER_CAPTURE_SCHEMA_VERSION",
    "PROVIDER_PAGE_SCHEMA_VERSION",
    "PROVIDER_SETTLEMENT_SCHEMA_VERSION",
    "SAFE_SCOPE_PATTERN",
    "SAFE_TOKEN_PATTERN",
    "DocumentCommentConnectorRegistration",
    "DocumentCommentConnectorStatus",
    "ExternalConnectorCapability",
    "ExternalConnectorPermissionStatus",
    "ProviderPageReader",
    "ProviderResponseWriter",
    "_normalize_document_comment_registration",
    "_normalize_permission_guidance",
    "_normalize_permission_requirement",
    "_normalize_permission_requirements",
    "_permission_guidance_for_registration",
    "_permission_requirement_fingerprint",
    "_repair_url",
    "_safe_token",
    "_scope_values",
    "_validate_permission_requirements_for_connector",
    "build_document_comment_connector_registration",
    "build_external_connector_permission_requirement",
    "build_external_connector_provider_page",
    "capture_document_comment_provider_page",
    "evaluate_external_connector_permissions",
    "project_document_comment_connector_status",
    "project_external_connector_permission_status",
    "reply_and_settle_document_comment_event",
]
