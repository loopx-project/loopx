from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from loopx.extensions.external_connector_provider import (
    DocumentCommentConnectorRegistration,
    build_document_comment_connector_registration,
    build_external_connector_permission_requirement,
    build_external_connector_provider_page,
    capture_document_comment_provider_page,
    evaluate_external_connector_permissions,
    project_document_comment_connector_status,
    project_external_connector_permission_status,
    reply_and_settle_document_comment_event,
)
from loopx.extensions.external_connector_runtime import (
    EFFECT_RECEIPT_SCHEMA_VERSION,
    ExternalCapturePolicy,
    ExternalConnectorCapability,
    ExternalConnectorLifecycle,
    ExternalEffectKind,
    ExternalIngressPolicy,
    ExternalResponsePolicy,
    ExternalSourceKind,
    build_external_connector_binding,
    build_external_connector_event,
    inspect_external_connector_inbox,
)


def _connector() -> dict[str, Any]:
    return build_external_connector_binding(
        goal_ref="goal-alpha",
        agent_ref="agent-alpha",
        provider_kind="provider-fixture",
        source_kind=ExternalSourceKind.DOCUMENT_COMMENT.value,
        source_ref="source-fixture",
        capture_policy=ExternalCapturePolicy.INCREMENTAL.value,
        ingress_policy=ExternalIngressPolicy.ASYNC_INBOX.value,
        response_policy=ExternalResponsePolicy.SOURCE_THREAD.value,
        cursor_ref=".loopx/inbox/document-comments/cursor.json",
        lifecycle=ExternalConnectorLifecycle.CONNECTED.value,
        capabilities=[
            ExternalConnectorCapability.HISTORY_CATCH_UP.value,
            ExternalConnectorCapability.RESPONSE_WRITE.value,
            ExternalConnectorCapability.RESPONSE_READBACK.value,
            ExternalConnectorCapability.ACKNOWLEDGE.value,
        ],
        inbox_ref=".loopx/inbox/document-comments",
    )


def _permission(operation: str) -> dict[str, Any]:
    return build_external_connector_permission_requirement(
        provider_kind="provider-fixture",
        provider_identity="fixture-bot",
        operation=operation,
        required_scopes=[f"comments:{operation}"],
        publication_required=True,
        repair_url="https://provider.example/apps/app-fixture/permissions",
    )


def _requirements() -> list[dict[str, Any]]:
    return [
        _permission(ExternalConnectorCapability.HISTORY_CATCH_UP.value),
        _permission(ExternalConnectorCapability.RESPONSE_WRITE.value),
        _permission(ExternalConnectorCapability.RESPONSE_READBACK.value),
    ]


def _registration() -> DocumentCommentConnectorRegistration:
    return build_document_comment_connector_registration(
        connector=_connector(),
        authority_material_ref="material-alpha",
        project_materials={
            "material-alpha": {
                "id": "material-alpha",
                "source_kind": "document",
            }
        },
        permission_requirements=_requirements(),
    )


def _permission_guidance(*, ready: bool) -> dict[str, Any]:
    scopes = {
        "fixture-bot": [
            "comments:history_catch_up",
            "comments:response_write",
            "comments:response_readback",
        ]
        if ready
        else []
    }
    return evaluate_external_connector_permissions(
        requirements=_requirements(),
        granted_scopes=scopes,
        published=ready,
    )


def _effect() -> dict[str, Any]:
    return {
        "schema_version": EFFECT_RECEIPT_SCHEMA_VERSION,
        "event_id": "event-alpha",
        "effect_id": "effect-alpha",
        "effect_kind": ExternalEffectKind.TODO_UPDATE.value,
        "status": "committed",
    }


def test_document_comment_registration_requires_separate_authority_material() -> None:
    with pytest.raises(ValueError, match="registered before its comments"):
        build_document_comment_connector_registration(
            connector=_connector(),
            authority_material_ref="material-alpha",
            project_materials={},
            permission_requirements=_requirements(),
        )

    registration = _registration()
    assert registration["authority_relation"] == "reference_only"
    assert registration["comment_authority"] == "external_input_only"
    assert "content" not in registration


def test_document_comment_registration_requires_permission_coverage() -> None:
    with pytest.raises(ValueError, match="do not cover provider calls"):
        build_document_comment_connector_registration(
            connector=_connector(),
            authority_material_ref="material-alpha",
            project_materials={
                "material-alpha": {
                    "id": "material-alpha",
                    "source_kind": "document",
                }
            },
            permission_requirements=[
                _permission(ExternalConnectorCapability.HISTORY_CATCH_UP.value)
            ],
        )


def test_permission_projection_hides_exact_repair_guidance() -> None:
    guidance = _permission_guidance(ready=False)
    assert guidance["ready"] is False
    assert guidance["items"][0]["missing_scopes"]
    assert guidance["items"][0]["repair_url"].startswith("https://")

    status = project_external_connector_permission_status(guidance)
    assert status["ready"] is False
    assert status["missing_requirement_count"] == 3
    assert "items" not in status
    assert "required_scopes" not in status
    assert "repair_url" not in status
    assert status["private_scope_values_captured"] is False
    assert status["private_repair_urls_captured"] is False


def test_permission_projection_rejects_forged_ready_state() -> None:
    guidance = _permission_guidance(ready=False)
    guidance["ready"] = True

    with pytest.raises(ValueError, match="aggregate ready state is inconsistent"):
        project_external_connector_permission_status(guidance)


def test_document_comment_registration_rejects_unbound_permission_guidance() -> None:
    unrelated_requirement = build_external_connector_permission_requirement(
        provider_kind="provider-fixture",
        provider_identity="other-bot",
        operation=ExternalConnectorCapability.HISTORY_CATCH_UP.value,
        required_scopes=["comments:history_catch_up"],
        publication_required=True,
        repair_url="https://provider.example/apps/other-bot/permissions",
    )
    unrelated_guidance = evaluate_external_connector_permissions(
        requirements=[unrelated_requirement],
        granted_scopes={"other-bot": ["comments:history_catch_up"]},
        published=True,
    )

    with pytest.raises(ValueError, match="must match the registered Connector"):
        project_document_comment_connector_status(
            registration=_registration(),
            permission_guidance=unrelated_guidance,
        )


def test_document_comment_registration_revalidates_authority_policy() -> None:
    registration = _registration()
    registration["comment_authority"] = "authoritative"

    with pytest.raises(ValueError, match="authority policy is invalid"):
        project_document_comment_connector_status(
            registration=registration,
            permission_guidance=_permission_guidance(ready=True),
        )


def test_provider_capture_does_not_read_until_permissions_are_ready(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def reader(
        connector: dict[str, Any],
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        calls.append(str(connector["provider_kind"]))
        return build_external_connector_provider_page(
            events=[],
            expected_cursor=cursor,
            next_cursor="page-two",
            has_more=False,
        )

    result = capture_document_comment_provider_page(
        project=str(tmp_path),
        registration=_registration(),
        permission_guidance=_permission_guidance(ready=False),
        page_reader=reader,
        expected_cursor=None,
        execute=True,
    )

    assert result["status"] == "permission_required"
    assert result["external_read_performed"] is False
    assert calls == []


def test_document_comment_provider_callsite_runs_effect_response_ack_order(
    tmp_path: Path,
) -> None:
    call_order: list[str] = []
    registration = _registration()
    guidance = _permission_guidance(ready=True)

    def reader(
        connector: dict[str, Any],
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        call_order.append("provider_read")
        assert connector["provider_kind"] == "provider-fixture"
        assert cursor is None
        assert limit == 100
        return build_external_connector_provider_page(
            events=[
                build_external_connector_event(
                    event_id="event-alpha",
                    content="Please validate this comment.",
                    occurred_at="2026-08-23T00:00:00Z",
                    addressed=True,
                    event_cursor="event-cursor-alpha",
                    anchor_ref="anchor-alpha",
                    reply_chain_ref="reply-chain-alpha",
                )
            ],
            expected_cursor=cursor,
            next_cursor="page-two",
            has_more=False,
        )

    capture = capture_document_comment_provider_page(
        project=str(tmp_path),
        registration=registration,
        permission_guidance=guidance,
        page_reader=reader,
        expected_cursor=None,
        execute=True,
    )
    assert capture["ok"] is True
    assert capture["accepted_count"] == 1
    assert capture["external_read_performed"] is True

    def mismatched_writer(
        connector: dict[str, Any],
        event: dict[str, Any],
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        call_order.append("mismatched_provider_response")
        return {
            "idempotency_key": "sha256:receipt-for-a-different-event",
            "external_write_performed": True,
            "verification_performed": True,
            "readback_verified": True,
        }

    with pytest.raises(ValueError, match="bind the requested idempotency_key"):
        reply_and_settle_document_comment_event(
            project=str(tmp_path),
            registration=registration,
            permission_guidance=guidance,
            event_id="event-alpha",
            effect_receipt=_effect(),
            reply_text="A mismatched receipt cannot acknowledge this event.",
            response_writer=mismatched_writer,
            execute=True,
        )
    assert (
        inspect_external_connector_inbox(
            project=tmp_path,
            binding=registration["connector"],
        )["pending_count"]
        == 1
    )

    def writer(
        connector: dict[str, Any],
        event: dict[str, Any],
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        call_order.append("provider_response")
        assert connector["provider_kind"] == "provider-fixture"
        assert event["event_id"] == "event-alpha"
        assert event["anchor_ref"] == "anchor-alpha"
        assert text == "Accepted after durable writeback."
        assert idempotency_key.startswith("sha256:")
        return {
            "idempotency_key": idempotency_key,
            "external_write_performed": True,
            "verification_performed": True,
            "readback_verified": True,
        }

    call_order.append("durable_effect_committed")
    settlement = reply_and_settle_document_comment_event(
        project=str(tmp_path),
        registration=registration,
        permission_guidance=guidance,
        event_id="event-alpha",
        effect_receipt=_effect(),
        reply_text="Accepted after durable writeback.",
        response_writer=writer,
        execute=True,
    )

    assert settlement["ok"] is True
    assert settlement["status"] == "acknowledged"
    assert settlement["external_write_performed"] is True
    assert settlement["response_readback_verified"] is True
    assert settlement["cursor_advanced"] is True
    assert call_order == [
        "provider_read",
        "mismatched_provider_response",
        "durable_effect_committed",
        "provider_response",
    ]
    inbox_status = inspect_external_connector_inbox(
        project=tmp_path,
        binding=registration["connector"],
    )
    assert inbox_status["pending_count"] == 0
    projected = project_document_comment_connector_status(
        registration=registration,
        permission_guidance=guidance,
    )
    assert projected["authority_material_bound"] is True
    assert projected["comment_authority"] == "external_input_only"
    assert projected["permissions"]["ready"] is True
    assert projected["private_authority_material_ref_captured"] is False


def test_provider_response_is_not_called_before_durable_effect(
    tmp_path: Path,
) -> None:
    registration = _registration()
    guidance = _permission_guidance(ready=True)

    def reader(
        connector: dict[str, Any],
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        return build_external_connector_provider_page(
            events=[
                build_external_connector_event(
                    event_id="event-alpha",
                    content="Please validate this comment.",
                    occurred_at="2026-08-23T00:00:00Z",
                    addressed=True,
                    event_cursor="event-cursor-alpha",
                )
            ],
            expected_cursor=cursor,
            next_cursor="page-two",
            has_more=False,
        )

    capture_document_comment_provider_page(
        project=str(tmp_path),
        registration=registration,
        permission_guidance=guidance,
        page_reader=reader,
        expected_cursor=None,
        execute=True,
    )
    writes: list[str] = []

    def writer(
        connector: dict[str, Any],
        event: dict[str, Any],
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        writes.append(idempotency_key)
        return {
            "idempotency_key": idempotency_key,
            "external_write_performed": True,
            "verification_performed": True,
            "readback_verified": True,
        }

    result = reply_and_settle_document_comment_event(
        project=str(tmp_path),
        registration=registration,
        permission_guidance=guidance,
        event_id="event-alpha",
        effect_receipt=None,
        reply_text="This must not be sent.",
        response_writer=writer,
        execute=True,
    )

    assert result["status"] == "durable_effect_required"
    assert result["external_write_performed"] is False
    assert writes == []
