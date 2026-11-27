"""Contract, zero-Any, and boundary tests for multi-part Lark manager replies."""

from __future__ import annotations

import ast
from collections.abc import Mapping
import io
from pathlib import Path
import tokenize

import pytest

from loopx.canary.maintainability_ratchet import (
    DECISION_POINT_LIMIT,
    DECISION_STATEMENT_LIMIT,
    _FunctionMetrics,
    _iter_functions,
)
import loopx.extensions.lark.manager_reply_parts as parts_module
import loopx.extensions.lark.manager_reply_parts_state as parts_state_module

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ast_any_count(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "Any" for alias in node.names)
        )
    )


def _comment_ignore_or_noqa_count(source: str) -> int:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return sum(
        1
        for tok in tokens
        if tok.type == tokenize.COMMENT
        and ("type: ignore" in tok.string or "noqa" in tok.string)
    )


def test_manager_reply_parts_modules_meet_zero_any_size_and_reexport_contracts() -> None:
    main_path = REPO_ROOT / "loopx" / "extensions" / "lark" / "manager_reply_parts.py"
    state_path = REPO_ROOT / "loopx" / "extensions" / "lark" / "manager_reply_parts_state.py"
    for path in (main_path, state_path, Path(__file__)):
        source = path.read_text(encoding="utf-8")
        assert _ast_any_count(source) == 0, path
        assert _comment_ignore_or_noqa_count(source) == 0, path

    assert len(main_path.read_text(encoding="utf-8").splitlines()) <= 550
    assert len(state_path.read_text(encoding="utf-8").splitlines()) < 750

    for path in (main_path, state_path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for qname, fn in _iter_functions(tree.body):
            metrics = _FunctionMetrics(fn)
            metrics.visit(fn)
            assert metrics.statement_count <= DECISION_STATEMENT_LIMIT, (path, qname)
            assert metrics.decision_points <= DECISION_POINT_LIMIT, (path, qname)

    for symbol in (
        "PART_DELIVERY_COMPLETE_KEY",
        "PART_DELIVERY_VERIFIED_KEY",
        "PART_ATTEMPT_KEY",
        "PART_STALL_NOTICE_KEY",
        "PART_STALL_COUNT_KEY",
        "PART_STALL_NOTICE_ATTEMPT_KEY",
        "PART_DELIVERY_INCOMPLETE",
        "PART_DELIVERY_COMPLETION_UNVERIFIED",
        "PART_STALL_NOTICE_MIN_STALLS",
        "MANAGER_REPLY_STALL_NOTICE",
        "completed_part_delivery_receipt",
        "part_delivery_incomplete_reason",
        "recorded_part_attempt",
        "attempt_provider_locator",
        "recorded_stall_notice_attempt",
        "plan_stalled_part_notice",
        "manager_part_delivery_pending_result",
        "manager_part_delivery_readback",
    ):
        assert getattr(parts_module, symbol) is getattr(parts_state_module, symbol), symbol


def test_receipt_reason_and_stall_planner_reject_booleans_and_malformed_attempts() -> None:
    assert parts_module.completed_part_delivery_receipt({}) is None
    assert (
        parts_module.completed_part_delivery_receipt(
            {
                parts_module.PART_DELIVERY_COMPLETE_KEY: True,
                parts_module.PART_DELIVERY_VERIFIED_KEY: True,
                "reply_idempotency_key": "sha256:abc",
            }
        )
        == {
            "ok": True,
            "status": "sent_verified",
            "idempotency_key": "sha256:abc",
            "content_format": "text",
            "external_write_performed": True,
            "verification_performed": True,
            "reply_verified": True,
            "part_delivery_reused": True,
        }
    )

    assert (
        parts_module.part_delivery_incomplete_reason(
            {"delivery_part_count": True, "delivery_parts_sent": True}
        )
        == parts_module.PART_DELIVERY_INCOMPLETE
    )
    assert (
        parts_module.part_delivery_incomplete_reason(
            {"delivery_part_count": 0, "delivery_parts_sent": 0}
        )
        == parts_module.PART_DELIVERY_INCOMPLETE
    )
    assert (
        parts_module.part_delivery_incomplete_reason(
            {"delivery_part_count": 3, "delivery_parts_sent": 3}
        )
        == parts_module.PART_DELIVERY_COMPLETION_UNVERIFIED
    )

    assert (
        parts_module.recorded_part_attempt(
            {parts_module.PART_ATTEMPT_KEY: "not-a-map"}, 0
        )
        is None
    )
    assert (
        parts_module.recorded_part_attempt(
            {parts_module.PART_ATTEMPT_KEY: {"index": 1, "attempt": "bad"}}, 1
        )
        is None
    )
    assert parts_module.attempt_provider_locator({"message_ref": "  "}) is None
    assert parts_module.attempt_provider_locator({"message_ref": "om_ok123"}) == "om_ok123"

    assert (
        parts_module.plan_stalled_part_notice(
            {
                parts_module.PART_STALL_COUNT_KEY: True,
                "delivery_parts_sent": 1,
                "delivery_part_count": 3,
            }
        )
        is None
    )
    assert (
        parts_module.plan_stalled_part_notice(
            {
                parts_module.PART_STALL_COUNT_KEY: 3,
                "delivery_parts_sent": True,
                "delivery_part_count": 3,
            }
        )
        is None
    )


def test_reconcile_helpers_strip_before_send_only_on_notice_and_preserve_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def fake_verify(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "ok": True,
            "verification_performed": True,
            "reply_verified": True,
        }

    monkeypatch.setattr(parts_module, "verify_lark_inbox_reply", fake_verify)
    def before_hook(_intent: str) -> dict[str, object]:
        return {"ok": True}
    state: dict[str, object] = {
        parts_module.PART_ATTEMPT_KEY: {"index": 0, "attempt": {"message_ref": "om_part0"}},
        parts_module.PART_STALL_NOTICE_ATTEMPT_KEY: {"attempt": {"message_ref": "om_notice"}},
    }
    opts: dict[str, object] = {
        "reply_placement": "chat_root",
        "before_send": before_hook,
    }
    part_res = parts_module.reconciled_part_reply(
        parts=["p0"],
        index=0,
        delivery_state=state,
        reply_runner=object(),
        root=tmp_path,
        config_path=tmp_path / "cfg.json",
        message_id="om_src",
        reply_options=opts,
    )
    assert part_res is not None and part_res["part_reconciled"] is True
    assert "before_send" in calls[0]

    notice_res = parts_module.reconciled_stall_notice(
        notice="notice text",
        delivery_state=state,
        reply_runner=object(),
        root=tmp_path,
        config_path=tmp_path / "cfg.json",
        message_id="om_src",
        reply_options=opts,
    )
    assert notice_res is not None and notice_res["notice_reconciled"] is True
    assert "before_send" not in calls[1]


def test_pending_readback_and_string_stall_coercion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert parts_module.manager_part_delivery_readback({}) == {}
    assert parts_module.manager_part_delivery_readback(
        {"delivery_part_count": 4, "delivery_parts_sent": 2, "delivery_truncated": 1}
    ) == {
        "delivery_part_count": 4,
        "delivery_parts_sent": 2,
        "delivery_truncated": True,
    }
    pending = parts_module.manager_part_delivery_pending_result(
        reason=parts_module.PART_DELIVERY_INCOMPLETE,
        delivery_state={"delivery_part_count": 4, "delivery_parts_sent": 2, "content_format": "markdown"},
        goal_id="g-demo",
        inbox_config_ref="cfg.json",
    )
    assert pending["format_degraded"] is False
    assert pending["delivery_notice_sent"] is False

    seen: list[str] = []

    def stub_reply(**kwargs: object) -> Mapping[str, object]:
        text = str(kwargs["text"])
        seen.append(text)
        if text.startswith("本条答复"):
            return {"ok": True, "verification_performed": True, "reply_verified": True}
        return {"ok": False, "status": "reply_provider_failed", "idempotency_key": None}

    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", stub_reply)
    body = "\n".join(f"- item {i} " + "x" * 60 for i in range(1, 2000))
    parts, truncated = parts_module.plan_manager_reply_parts(body)
    assert truncated is False and len(parts) > 1
    state: dict[str, object] = {
        "delivery_part_count": len(parts),
        "delivery_parts_sent": 1,
        parts_module.PART_STALL_COUNT_KEY: "2",
    }
    reply, reason = parts_module.deliver_manager_reply_after_length_failure(
        reply_text=body,
        delivery_state=state,
        delivery_path=tmp_path / "d.json",
        write_delivery=lambda _p, _s: None,
        reply_runner=object(),
        root=tmp_path,
        config_path=tmp_path / "cfg.json",
        message_id="om_src",
    )
    assert reply is None
    assert reason == parts_module.PART_DELIVERY_INCOMPLETE
    assert state[parts_module.PART_STALL_COUNT_KEY] == 3
    assert state[parts_module.PART_STALL_NOTICE_KEY] is True
    assert any(msg.startswith("本条答复") for msg in seen)
