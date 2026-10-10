from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from loopx.extensions.external_connector_provider import (
    build_document_comment_connector_registration,
    capture_document_comment_provider_page,
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
    drain_external_connector_inbox,
    inspect_external_connector_inbox,
)
from loopx.extensions.lark.document_comment_provider import (
    LARK_DOCUMENT_COMMENT_CREATE_SCOPE,
    LARK_DOCUMENT_COMMENT_READ_SCOPE,
    LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA,
    LarkCliDocumentCommentProvider,
    LarkDocumentCommentProviderError,
    LarkDocumentCommentTarget,
    _epoch_rfc3339,
)


def _completed(payload: dict[str, Any], *, returncode: int = 0) -> dict[str, Any]:
    return {
        "returncode": returncode,
        "stdout": json.dumps(payload) if returncode == 0 else "",
        "stderr": json.dumps(payload) if returncode != 0 else "",
    }


def _connector() -> dict[str, Any]:
    return build_external_connector_binding(
        goal_ref="goal-alpha",
        agent_ref="agent-alpha",
        provider_kind="loopx-lark",
        source_kind=ExternalSourceKind.DOCUMENT_COMMENT.value,
        source_ref="document-alpha",
        capture_policy=ExternalCapturePolicy.INCREMENTAL.value,
        ingress_policy=ExternalIngressPolicy.ASYNC_INBOX.value,
        response_policy=ExternalResponsePolicy.SOURCE_THREAD.value,
        cursor_ref=".loopx/inbox/lark-comments/cursor.json",
        lifecycle=ExternalConnectorLifecycle.CONNECTED.value,
        capabilities=[
            ExternalConnectorCapability.HISTORY_CATCH_UP.value,
            ExternalConnectorCapability.RESPONSE_WRITE.value,
            ExternalConnectorCapability.RESPONSE_READBACK.value,
            ExternalConnectorCapability.ACKNOWLEDGE.value,
        ],
        inbox_ref=".loopx/inbox/lark-comments",
    )


def _target() -> LarkDocumentCommentTarget:
    return LarkDocumentCommentTarget(
        source_ref="document-alpha",
        document_url="https://example.larksuite.com/docx/document-alpha",
        provider_identity="fixture-profile",
        profile="fixture-profile",
        identity="bot",
    )


def _comment_page(*, whole: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "identity": "bot",
        "data": {
            "file_token": "document-alpha",
            "file_type": "docx",
            "items": [
                {
                    "comment_id": "comment-alpha",
                    "create_time": 1_787_377_486,
                    "is_solved": False,
                    "is_whole": whole,
                    "quote": "Selected paragraph",
                    "reply_list": {
                        "replies": [
                            {
                                "reply_id": "reply-human-alpha",
                                "create_time": 1_787_377_486,
                                "content": {
                                    "elements": [
                                        {
                                            "type": "text_run",
                                            "text_run": {
                                                "text": "Please validate this comment."
                                            },
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                }
            ],
            "has_more": False,
            "page_token": "",
            "count": 1,
        },
    }


def _reply_page(text: str, *, extra: object | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "identity": "bot",
        "data": {
            "items": [
                {
                    "reply_id": "reply-agent-alpha",
                    "create_time": 1_787_377_500,
                    "extra": extra,
                    "content": {
                        "elements": [
                            {
                                "type": "text_run",
                                "text_run": {"text": text},
                            }
                        ]
                    },
                }
            ],
            "has_more": False,
            "page_token": "",
        },
    }


def _bot_identity(*, profile: str = "fixture-profile") -> dict[str, Any]:
    return {
        "identity": "bot",
        "available": True,
        "profile": profile,
        "appId": "cli_fixture",
    }


def _app_version_page(*, scopes: list[str]) -> dict[str, Any]:
    return {
        "ok": True,
        "identity": "bot",
        "data": {
            "items": [
                {
                    "status": 1,
                    "publish_time": "1787377000",
                    "scopes": [
                        {"scope": scope, "token_types": ["tenant"]} for scope in scopes
                    ],
                }
            ]
        },
    }


def test_lark_bot_permission_probe_uses_published_app_scope_ledger(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def runner(args, _cwd, _timeout):
        calls.append(list(args))
        if args[-3:] == ["auth", "status", "--json"]:
            return _completed({"identity": "bot"})
        if args[-2:] == ["whoami", "--json"]:
            return _completed(_bot_identity())
        assert "auth" not in args or "check" not in args
        return _completed(
            _app_version_page(
                scopes=[
                    LARK_DOCUMENT_COMMENT_CREATE_SCOPE,
                    LARK_DOCUMENT_COMMENT_READ_SCOPE,
                ]
            )
        )

    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=runner,
    )
    guidance = provider.permission_guidance(
        response_enabled=True,
        published=True,
    )

    assert guidance["ready"] is True
    assert guidance["missing_requirement_count"] == 0
    assert calls == [
        [
            "lark-cli",
            "--profile",
            "fixture-profile",
            "auth",
            "status",
            "--json",
        ],
        [
            "lark-cli",
            "--profile",
            "fixture-profile",
            "whoami",
            "--json",
        ],
        [
            "lark-cli",
            "--profile",
            "fixture-profile",
            "api",
            "GET",
            "/open-apis/application/v6/applications/cli_fixture/app_versions",
            "--as",
            "bot",
            "--params",
            '{"lang":"zh_cn","page_size":"2"}',
            "--json",
        ],
    ]


def test_lark_bot_permission_probe_reports_missing_published_scope(
    tmp_path: Path,
) -> None:
    def runner(args, _cwd, _timeout):
        if args[-3:] == ["auth", "status", "--json"]:
            return _completed({"identity": "bot"})
        if args[-2:] == ["whoami", "--json"]:
            return _completed(_bot_identity())
        payload = _app_version_page(scopes=[LARK_DOCUMENT_COMMENT_READ_SCOPE])
        payload["data"]["items"][0]["scopes"].append(
            {
                "scope": LARK_DOCUMENT_COMMENT_CREATE_SCOPE,
                "token_types": ["user"],
            }
        )
        return _completed(payload)

    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=runner,
    )
    guidance = provider.permission_guidance(
        response_enabled=True,
        published=True,
    )

    assert guidance["ready"] is False
    assert guidance["missing_requirement_count"] == 1
    assert guidance["items"][1]["missing_scopes"] == [
        LARK_DOCUMENT_COMMENT_CREATE_SCOPE
    ]


def test_lark_bot_permission_probe_unknown_fails_closed(tmp_path: Path) -> None:
    def runner(args, _cwd, _timeout):
        if args[-3:] == ["auth", "status", "--json"]:
            return _completed({"identity": "bot"})
        if args[-2:] == ["whoami", "--json"]:
            return _completed(_bot_identity())
        return _completed({"ok": True, "identity": "bot", "data": {"items": []}})

    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=runner,
    )

    try:
        provider.permission_guidance(response_enabled=True, published=True)
    except LarkDocumentCommentProviderError as error:
        assert error.code == "lark_document_comment_permission_probe_unknown"
    else:
        raise AssertionError("unknown Bot scope readiness must fail closed")


def test_lark_bot_permission_probe_rejects_profile_mismatch(tmp_path: Path) -> None:
    def runner(args, _cwd, _timeout):
        if args[-3:] == ["auth", "status", "--json"]:
            return _completed({"identity": "bot"})
        return _completed(_bot_identity(profile="different-profile"))

    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=runner,
    )

    try:
        provider.permission_guidance(response_enabled=True, published=True)
    except LarkDocumentCommentProviderError as error:
        assert error.code == (
            "lark_document_comment_permission_probe_identity_mismatch"
        )
    else:
        raise AssertionError("Bot scope evidence must bind the configured profile")


def test_lark_bot_permission_guidance_does_not_return_app_metadata(
    tmp_path: Path,
) -> None:
    def runner(args, _cwd, _timeout):
        if args[-3:] == ["auth", "status", "--json"]:
            return _completed({"identity": "bot"})
        if args[-2:] == ["whoami", "--json"]:
            return _completed(_bot_identity())
        payload = _app_version_page(
            scopes=[
                LARK_DOCUMENT_COMMENT_CREATE_SCOPE,
                LARK_DOCUMENT_COMMENT_READ_SCOPE,
            ]
        )
        payload["data"]["private_payload"] = "must-not-escape"
        return _completed(payload)

    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=runner,
    )
    serialized = json.dumps(
        provider.permission_guidance(response_enabled=True, published=True)
    )

    assert "cli_fixture" not in serialized
    assert "must-not-escape" not in serialized
    assert "token" not in serialized.lower()


def test_lark_user_permission_probe_still_uses_user_oauth_scopes(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def runner(args, _cwd, _timeout):
        calls.append(list(args))
        if args[-3:] == ["auth", "status", "--json"]:
            return _completed({"identity": "user"})
        return _completed(
            {
                "ok": True,
                "granted": [
                    LARK_DOCUMENT_COMMENT_CREATE_SCOPE,
                    LARK_DOCUMENT_COMMENT_READ_SCOPE,
                ],
                "missing": [],
            }
        )

    target = _target()
    provider = LarkCliDocumentCommentProvider(
        target=LarkDocumentCommentTarget(
            source_ref=target.source_ref,
            document_url=target.document_url,
            provider_identity=target.provider_identity,
            profile=target.profile,
            identity="user",
        ),
        reply_store=tmp_path / "reply-receipts.json",
        runner=runner,
    )

    guidance = provider.permission_guidance(response_enabled=True, published=True)

    assert guidance["ready"] is True
    assert any("auth" in call and "check" in call for call in calls)
    assert not any("app_versions" in " ".join(call) for call in calls)


def test_lark_user_permission_probe_preserves_missing_scope_predicate(
    tmp_path: Path,
) -> None:
    def runner(args, _cwd, _timeout):
        if args[-3:] == ["auth", "status", "--json"]:
            return _completed({"identity": "user"})
        return {
            "returncode": 1,
            "stdout": json.dumps(
                {
                    "ok": False,
                    "granted": [LARK_DOCUMENT_COMMENT_READ_SCOPE],
                    "missing": [LARK_DOCUMENT_COMMENT_CREATE_SCOPE],
                }
            ),
            "stderr": "",
        }

    target = _target()
    provider = LarkCliDocumentCommentProvider(
        target=LarkDocumentCommentTarget(
            source_ref=target.source_ref,
            document_url=target.document_url,
            provider_identity=target.provider_identity,
            profile=target.profile,
            identity="user",
        ),
        reply_store=tmp_path / "reply-receipts.json",
        runner=runner,
    )

    guidance = provider.permission_guidance(response_enabled=True, published=True)

    assert guidance["ready"] is False
    assert guidance["missing_requirement_count"] == 1
    assert guidance["items"][1]["missing_scopes"] == [
        LARK_DOCUMENT_COMMENT_CREATE_SCOPE
    ]


def test_lark_permission_probe_rejects_identity_mismatch(tmp_path: Path) -> None:
    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=lambda *_: _completed({"identity": "user"}),
    )

    try:
        provider.permission_guidance(response_enabled=True, published=True)
    except LarkDocumentCommentProviderError as error:
        assert error.code == (
            "lark_document_comment_permission_probe_identity_mismatch"
        )
    else:
        raise AssertionError("permission probes must use the configured identity")


def test_lark_provider_filters_unreplyable_comment_cards(tmp_path: Path) -> None:
    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=lambda *_: _completed(_comment_page(whole=True)),
    )

    page = provider.page_reader(_connector(), None, 100)

    assert page["events"] == []
    assert page["has_more"] is False
    assert page["next_cursor"].startswith("lark-comment-v0.")


def test_lark_provider_rejects_boolean_comment_timestamp(tmp_path: Path) -> None:
    payload = _comment_page()
    payload["data"]["items"][0]["reply_list"]["replies"][0]["create_time"] = True
    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=lambda *_: _completed(payload),
    )

    try:
        provider.page_reader(_connector(), None, 100)
    except LarkDocumentCommentProviderError as error:
        assert error.code == "comment_timestamp_invalid"
    else:
        raise AssertionError("boolean timestamps must fail closed")


def test_lark_document_comment_timestamp_accepts_microsecond_epoch():
    assert _epoch_rfc3339("1700000000123456") == "2023-11-14T22:13:20.123456Z"


def test_lark_document_comment_callsite_runs_read_effect_reply_readback_ack(
    tmp_path: Path,
) -> None:
    call_order: list[str] = []
    created_reply_text = "Accepted after durable writeback."

    def runner(args, _cwd, _timeout):
        command = list(args)
        if command[-3:] == ["auth", "status", "--json"]:
            call_order.append("identity_probe")
            return _completed({"identity": "bot"})
        if command[-2:] == ["whoami", "--json"]:
            call_order.append("identity_binding")
            return _completed(_bot_identity())
        if "app_versions" in " ".join(command):
            call_order.append("permission_probe")
            return _completed(
                _app_version_page(
                    scopes=[
                        LARK_DOCUMENT_COMMENT_CREATE_SCOPE,
                        LARK_DOCUMENT_COMMENT_READ_SCOPE,
                    ]
                )
            )
        if "+list-comments" in command:
            call_order.append("provider_read")
            assert command[command.index("--page-size") + 1] == "1"
            return _completed(_comment_page())
        if (
            "file.comment.replys" in command
            and command[command.index("file.comment.replys") + 1] == "create"
        ):
            call_order.append("provider_reply")
            payload = json.loads(command[command.index("--data") + 1])
            assert json.loads(payload["extra"])["loopx_idempotency_key"].startswith(
                "sha256:"
            )
            assert payload["content"]["elements"][0]["text_run"]["text"] == (
                created_reply_text
            )
            return _completed(
                {
                    "ok": True,
                    "identity": "bot",
                    "data": {"reply_id": "reply-agent-alpha"},
                }
            )
        if (
            "file.comment.replys" in command
            and command[command.index("file.comment.replys") + 1] == "list"
        ):
            call_order.append("provider_readback")
            return _completed(_reply_page(created_reply_text))
        raise AssertionError(f"unexpected lark-cli call: {command}")

    reply_store = tmp_path / "reply-receipts.json"
    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=reply_store,
        runner=runner,
    )
    requirements = provider.permission_requirements(response_enabled=True)
    guidance = provider.permission_guidance(
        response_enabled=True,
        published=True,
    )
    registration = build_document_comment_connector_registration(
        connector=_connector(),
        authority_material_ref="material-alpha",
        project_materials={
            "material-alpha": {"id": "material-alpha", "source_kind": "document"}
        },
        permission_requirements=requirements,
    )
    capture = capture_document_comment_provider_page(
        project=str(tmp_path),
        registration=registration,
        permission_guidance=guidance,
        page_reader=provider.page_reader,
        expected_cursor=None,
        execute=True,
    )
    assert capture["accepted_count"] == 1

    event = drain_external_connector_inbox(
        project=tmp_path,
        binding=registration["connector"],
        limit=1,
    )["items"][0]
    assert event["addressed"] is False
    call_order.append("durable_effect")
    settlement = reply_and_settle_document_comment_event(
        project=str(tmp_path),
        registration=registration,
        permission_guidance=guidance,
        event_id=event["event_id"],
        effect_receipt={
            "schema_version": EFFECT_RECEIPT_SCHEMA_VERSION,
            "event_id": event["event_id"],
            "effect_id": "effect-alpha",
            "effect_kind": ExternalEffectKind.TODO_UPDATE.value,
            "status": "committed",
        },
        reply_text=created_reply_text,
        response_writer=provider.response_writer,
        execute=True,
    )

    assert settlement["status"] == "acknowledged"
    assert settlement["response_readback_verified"] is True
    assert call_order == [
        "identity_probe",
        "identity_binding",
        "permission_probe",
        "provider_read",
        "durable_effect",
        "provider_reply",
        "provider_readback",
    ]
    assert (
        inspect_external_connector_inbox(
            project=tmp_path,
            binding=registration["connector"],
        )["pending_count"]
        == 0
    )
    receipt_text = reply_store.read_text(encoding="utf-8")
    assert created_reply_text not in receipt_text
    assert "pending_readback" not in receipt_text
    assert '"status": "verified"' in receipt_text

    receipt = json.loads(receipt_text)
    idempotency_key = next(iter(receipt["entries"]))
    second = provider.response_writer(
        registration["connector"],
        event,
        created_reply_text,
        idempotency_key,
    )
    assert second["idempotency_key"] == idempotency_key
    assert second["readback_verified"] is True
    assert call_order.count("provider_reply") == 1
    assert call_order.count("provider_readback") == 2


def test_lark_response_writer_recovers_create_before_receipt_crash(
    tmp_path: Path,
) -> None:
    idempotency_key = "sha256:" + "a" * 64
    reply_text = "Recovered reply."
    reply_store = tmp_path / "reply-receipts.json"
    reply_store.write_text(
        json.dumps(
            {
                "schema_version": LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA,
                "entries": {
                    idempotency_key: {
                        "source_ref": "document-alpha",
                        "file_token": "document-alpha",
                        "file_type": "docx",
                        "comment_id": "comment-alpha",
                        "text_digest": "sha256:"
                        + hashlib.sha256(reply_text.encode()).hexdigest(),
                        "status": "creating",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def runner(args, _cwd, _timeout):
        command = list(args)
        calls.append(command)
        if "+list-comments" in command:
            return _completed(_comment_page())
        assert "file.comment.replys" in command
        assert command[command.index("file.comment.replys") + 1] == "list"
        return _completed(
            _reply_page(
                reply_text,
                extra=json.dumps({"loopx_idempotency_key": idempotency_key}),
            )
        )

    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=reply_store,
        runner=runner,
    )
    event = {
        "reply_chain_ref": next(
            item["reply_chain_ref"]
            for item in provider.page_reader(
                _connector(),
                None,
                1,
            ).get("events", [])
        )
    }
    receipt = provider.response_writer(
        _connector(),
        event,
        reply_text,
        idempotency_key,
    )

    assert receipt["idempotency_key"] == idempotency_key
    assert not any(
        "file.comment.replys" in command
        and command[command.index("file.comment.replys") + 1] == "create"
        for command in calls
    )
    assert (
        json.loads(reply_store.read_text(encoding="utf-8"))["entries"][idempotency_key][
            "status"
        ]
        == "verified"
    )


def test_lark_provider_rejects_connector_target_mismatch(tmp_path: Path) -> None:
    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=lambda *_: (_ for _ in ()).throw(AssertionError("must not call CLI")),
    )
    connector = _connector()
    connector["source_ref"] = "different-document"

    try:
        provider.page_reader(connector, None, 10)
    except LarkDocumentCommentProviderError as error:
        assert error.code == "connector_source_ref_mismatch"
    else:
        raise AssertionError("target mismatch must fail closed")


def test_lark_provider_rejects_unconfigured_address_inference(tmp_path: Path) -> None:
    provider = LarkCliDocumentCommentProvider(
        target=_target(),
        reply_store=tmp_path / "reply-receipts.json",
        runner=lambda *_: (_ for _ in ()).throw(AssertionError("must not call CLI")),
    )
    connector = _connector()
    connector["capture_policy"] = ExternalCapturePolicy.ADDRESSED_ONLY.value

    try:
        provider.page_reader(connector, None, 10)
    except LarkDocumentCommentProviderError as error:
        assert error.code == "connector_address_filter_unsupported"
    else:
        raise AssertionError("unsupported address inference must fail closed")
