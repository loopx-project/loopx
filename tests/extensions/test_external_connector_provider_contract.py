"""Contract, zero-Any, boundary, and efficiency tests for external_connector_provider."""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType
import pytest

from loopx.extensions import (
    external_connector_permissions as perms,
    external_connector_provider as provider,
    external_connector_runtime as runtime,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _binding(response_policy: str = "source_thread") -> dict[str, object]:
    caps = ["history_catch_up", "acknowledge"]
    if response_policy != "no_response":
        caps.extend(["response_write", "response_readback"])
    raw = runtime.build_external_connector_binding(
        goal_ref="goal-1",
        agent_ref="agent-1",
        provider_kind="provider-x",
        source_kind=runtime.ExternalSourceKind.DOCUMENT_COMMENT.value,
        source_ref="src-1",
        capture_policy=runtime.ExternalCapturePolicy.INCREMENTAL.value,
        ingress_policy=runtime.ExternalIngressPolicy.ASYNC_INBOX.value,
        response_policy=response_policy,
        cursor_ref=".loopx/inbox/doc/cursor.json",
        lifecycle=runtime.ExternalConnectorLifecycle.CONNECTED.value,
        capabilities=caps,
        inbox_ref=".loopx/inbox/doc",
    )
    return {str(k): v for k, v in raw.items()}


def _req(op: str) -> dict[str, object]:
    return provider.build_external_connector_permission_requirement(
        provider_kind="provider-x",
        provider_identity="bot-x",
        operation=op,
        required_scopes=[f"scope:{op}", "scope:shared"],
        publication_required=True,
        repair_url="https://example.org/apps/x/permissions",
    )


def _event(event_id: str = "evt-1") -> dict[str, object]:
    raw = runtime.build_external_connector_event(
        event_id=event_id,
        content="Review comment body",
        occurred_at="2026-11-26T00:00:00Z",
        addressed=True,
        event_cursor=f"cur-{event_id}",
        anchor_ref="anchor-1",
        reply_chain_ref="chain-1",
    )
    return {str(k): v for k, v in raw.items()}


def test_connector_provider_zero_any_budgets_and_pin_preservation() -> None:
    targets = [
        ("loopx/extensions/external_connector_provider.py", 550),
        ("loopx/extensions/external_connector_permissions.py", 550),
        ("tests/extensions/test_external_connector_provider_contract.py", 500),
    ]
    for rel, max_loc in targets:
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert len(src.splitlines()) <= max_loc, rel
        tree = ast.parse(src)
        anys = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "Any"]
        assert anys == [], rel
        assert ("type: " + "ignore") not in src, rel
        assert ("no" + "qa") not in src, rel

    assert provider.SAFE_TOKEN_PATTERN is runtime.SAFE_TOKEN_PATTERN
    assert provider.SAFE_SCOPE_PATTERN is perms.SAFE_SCOPE_PATTERN
    assert provider.build_external_connector_permission_requirement is (
        perms.build_external_connector_permission_requirement
    )
    assert provider.evaluate_external_connector_permissions is (
        perms.evaluate_external_connector_permissions
    )
    assert provider.project_external_connector_permission_status is (
        perms.project_external_connector_permission_status
    )


def test_token_scope_and_repair_url_boundaries() -> None:
    assert provider._safe_token("a" * 200, field="tok") == "a" * 200
    for bad_token in ("", "-lead", "bad token", "a/b", "a" * 201):
        with pytest.raises(ValueError, match="opaque public-safe token"):
            provider._safe_token(bad_token, field="tok")

    assert provider._scope_values(["b:1", "a/2", "a/2"], field="scopes") == ["a/2", "b:1"]
    for bad_scopes in ([], ["ok", "bad scope"], ["a/b/" + "x" * 200]):
        with pytest.raises(ValueError, match="provider scope tokens"):
            provider._scope_values(bad_scopes, field="scopes")

    for bad_url in (
        "http://example.org/x",
        "https://user:pass@example.org/x",
        "https://example.org/x#frag",
        "https://example.org/" + "a" * 2040,
    ):
        with pytest.raises(ValueError, match="official HTTPS URL"):
            provider._repair_url(bad_url)

    with pytest.raises(ValueError, match="operation must be one of"):
        provider.build_external_connector_permission_requirement(
            provider_kind="provider-x",
            provider_identity="bot-x",
            operation="invalid_op",
            required_scopes=["scope:a"],
            publication_required=False,
            repair_url="https://example.org/repair",
        )


def test_permission_compound_error_precedence_and_guidance_consistency() -> None:
    r0 = _req("history_catch_up")
    bad_schema = {**r0, "schema_version": "wrong"}
    normalize_reqs = getattr(provider, "_normalize_permission_requirements")
    with pytest.raises(TypeError, match="requirements are invalid"):
        normalize_reqs([r0, r0, "not_mapping"])
    with pytest.raises(ValueError, match="requirement schema is invalid"):
        provider._normalize_permission_requirements([r0, r0, bad_schema])
    with pytest.raises(ValueError, match="requirements are duplicated"):
        provider._normalize_permission_requirements([r0, r0])

    valid_guidance = provider.evaluate_external_connector_permissions(
        requirements=[r0],
        granted_scopes={"bot-x": ["scope:history_catch_up", "scope:shared"]},
        published=True,
    )
    items = valid_guidance["items"]
    assert isinstance(items, list) and len(items) == 1
    first = dict(items[0])
    with pytest.raises(ValueError, match="belong to required_scopes"):
        provider._normalize_permission_guidance(
            {
                **valid_guidance,
                "items": [{**first, "missing_scopes": ["scope:foreign"]}],
            }
        )
    with pytest.raises(ValueError, match="ready state is inconsistent"):
        provider._normalize_permission_guidance(
            {
                **valid_guidance,
                "items": [{**first, "ready": False}],
            }
        )
    with pytest.raises(ValueError, match="aggregate ready state is inconsistent"):
        provider._normalize_permission_guidance({**valid_guidance, "ready": False})


def test_provider_page_bounds_and_mapping_proxy_compatibility() -> None:
    ev = _event()
    with pytest.raises(ValueError, match="exceeds 500 events"):
        provider.build_external_connector_provider_page(
            events=[ev] * 501,
            expected_cursor=None,
            next_cursor="next-1",
            has_more=False,
        )
    with pytest.raises(ValueError, match="next_cursor"):
        provider.build_external_connector_provider_page(
            events=[],
            expected_cursor=None,
            next_cursor="c" * 2049,
            has_more=False,
        )
    with pytest.raises(ValueError, match="expected_cursor"):
        provider.build_external_connector_provider_page(
            events=[],
            expected_cursor="",
            next_cursor="next-1",
            has_more=False,
        )

    page = provider.build_external_connector_provider_page(
        events=[MappingProxyType(ev)],
        expected_cursor="cur-0",
        next_cursor="next-1",
        has_more=True,
    )
    assert page["events"] == [ev]


def test_capture_and_settlement_validation_and_single_guidance_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reqs = [_req("history_catch_up"), _req("response_write"), _req("response_readback")]
    conn = MappingProxyType(_binding("source_thread"))
    reg = provider.build_document_comment_connector_registration(
        connector=conn,
        authority_material_ref="mat-1",
        project_materials=MappingProxyType(
            {"mat-1": MappingProxyType({"id": "mat-1"})}
        ),
        permission_requirements=[MappingProxyType(r) for r in reqs],
    )
    guidance = provider.evaluate_external_connector_permissions(
        requirements=reqs,
        granted_scopes={
            "bot-x": [
                "scope:history_catch_up",
                "scope:response_write",
                "scope:response_readback",
                "scope:shared",
            ]
        },
        published=True,
    )
    reg_view = MappingProxyType(reg)
    guidance_view = MappingProxyType(guidance)

    norm_calls = 0
    real_norm = perms._normalize_permission_guidance_items

    def _counting_norm(
        value: object,
    ) -> tuple[bool, int, list[tuple[perms._RequirementSpec, dict[str, object]]]]:
        nonlocal norm_calls
        norm_calls += 1
        assert isinstance(value, dict | MappingProxyType)
        return real_norm(value)

    monkeypatch.setattr(perms, "_normalize_permission_guidance_items", _counting_norm)

    status = provider.project_document_comment_connector_status(
        registration=reg_view,
        permission_guidance=guidance_view,
    )
    assert status["permissions"]["ready"] is True
    assert norm_calls == 1

    with pytest.raises(ValueError, match="exceeded the requested limit"):
        provider.capture_document_comment_provider_page(
            project=str(tmp_path),
            registration=reg_view,
            permission_guidance=guidance_view,
            page_reader=lambda _c, cur, _l: provider.build_external_connector_provider_page(
                events=[_event("e1"), _event("e2")],
                expected_cursor=cur,
                next_cursor="c2",
                has_more=False,
            ),
            expected_cursor=None,
            limit=1,
            execute=True,
        )
    assert norm_calls == 2

    captured = provider.capture_document_comment_provider_page(
        project=str(tmp_path),
        registration=reg_view,
        permission_guidance=guidance_view,
        page_reader=lambda _c, cur, _l: provider.build_external_connector_provider_page(
            events=[_event("evt-1")],
            expected_cursor=cur,
            next_cursor="c2",
            has_more=False,
        ),
        expected_cursor=None,
        execute=True,
    )
    assert captured["ok"] is True and norm_calls == 3

    effect = MappingProxyType(
        {
            "schema_version": runtime.EFFECT_RECEIPT_SCHEMA_VERSION,
            "event_id": "evt-1",
            "effect_id": "eff-1",
            "effect_kind": runtime.ExternalEffectKind.TODO_UPDATE.value,
            "status": "committed",
        }
    )
    with pytest.raises(ValueError, match="requires reply_text"):
        provider.reply_and_settle_document_comment_event(
            project=str(tmp_path),
            registration=reg_view,
            permission_guidance=guidance_view,
            event_id="evt-1",
            effect_receipt=effect,
            reply_text="   ",
            response_writer=lambda *_a: {},
            execute=True,
        )
    with pytest.raises(ValueError, match="requires a response_writer"):
        provider.reply_and_settle_document_comment_event(
            project=str(tmp_path),
            registration=reg_view,
            permission_guidance=guidance_view,
            event_id="evt-1",
            effect_receipt=effect,
            reply_text="Ack",
            response_writer=None,
            execute=True,
        )
    with pytest.raises(ValueError, match="idempotency_key"):
        provider.reply_and_settle_document_comment_event(
            project=str(tmp_path),
            registration=reg_view,
            permission_guidance=guidance_view,
            event_id="evt-1",
            effect_receipt=effect,
            reply_text="Ack",
            response_writer=lambda *_a: {"idempotency_key": "wrong"},
            execute=True,
        )

    settled = provider.reply_and_settle_document_comment_event(
        project=str(tmp_path),
        registration=reg_view,
        permission_guidance=guidance_view,
        event_id="evt-1",
        effect_receipt=effect,
        reply_text="Done.",
        response_writer=lambda _c, _i, _t, ik: MappingProxyType(
            {
                "idempotency_key": ik,
                "external_write_performed": True,
                "verification_performed": True,
                "readback_verified": True,
            }
        ),
        execute=True,
    )
    assert settled["ok"] is True
    assert settled["response_readback_verified"] is True
