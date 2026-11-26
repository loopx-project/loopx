"""Contract, zero-Any, MappingProxyType, and error-path tests for chat_turn_acceptance."""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType
import pytest

from loopx.control_plane import chat_turn_acceptance as cta


REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_PATH = REPO_ROOT / "loopx" / "control_plane" / "chat_turn_acceptance.py"


def test_chat_turn_acceptance_zero_any_budget_and_exports() -> None:
    for rel, max_loc in (
        ("loopx/control_plane/chat_turn_acceptance.py", 450),
        ("tests/control_plane/test_chat_turn_acceptance_contract.py", 400),
    ):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert len(src.splitlines()) <= max_loc, rel
        tree = ast.parse(src)
        anys = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "Any"]
        assert anys == [], rel
        assert ("type: " + "ignore") not in src, rel
        assert ("no" + "qa") not in src, rel

    for symbol in (
        "CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA",
        "CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA",
        "CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA",
        "TERMINAL_TURN_STATUSES",
        "ManagedTurnReplayConflictError",
        "ManagedTurnAcceptanceWrites",
        "ManagedTurnAcceptancePlan",
        "AcceptedManagedTurn",
        "QueuedChatMutationPlan",
        "queued_chat_turn_ids",
        "plan_native_mention_ingress",
        "plan_queued_chat_steering",
        "plan_queued_chat_mutation",
        "managed_turn_execution_allowed",
        "managed_turn_recovery_allowed",
        "plan_managed_turn_acceptance",
    ):
        assert hasattr(cta, symbol), symbol


def test_digest_and_fact_projections_cover_fast_paths_and_multi_matches() -> None:
    assert cta._text_digest(None) == "invalid"
    assert cta._text_digest(123) == "invalid"
    assert cta._text_digest("") == cta._EMPTY_TEXT_DIGEST
    assert cta._json_digest(None) == cta._NULL_JSON_DIGEST
    assert cta._json_digest(object()) == "invalid"
    assert cta._json_digest(MappingProxyType({"a": 1})) == cta._json_digest({"a": 1})
    assert cta._json_digest((MappingProxyType({"k": "v"}),)) == cta._json_digest([{"k": "v"}])

    assert cta._acceptance_facts({}) is None
    assert cta._acceptance_facts({"_acceptance": "bad"}) == {"schema_version": None}

    msgs = (
        MappingProxyType({"role": "user", "turn_id": "t1", "message_id": "m1", "text": ""}),
        MappingProxyType({"role": "assistant", "turn_id": "t1", "text": "ok"}),
    )
    assert cta._transcript_facts(msgs, None) == {"count": 0}
    single = cta._transcript_facts(msgs, "t1")
    assert single["count"] == 1 and single["message_id"] == "m1"
    dup = cta._transcript_facts(
        (*msgs, MappingProxyType({"role": "user", "turn_id": "t1", "message_id": "m2", "text": "x"})),
        "t1",
    )
    assert dup == {"count": 2}

    events = (
        MappingProxyType({"kind": "turn.queued", "payload": {"n": 1}}),
        MappingProxyType({"kind": "turn.queued", "payload": {"n": 2}}),
    )
    assert cta._queued_event_facts(events[:1])["count"] == 1
    assert cta._queued_event_facts(events) == {"count": 2}

    turns = (
        MappingProxyType({"turn_id": "done", "status": "completed", "_acceptance": {}}),
        MappingProxyType({"turn_id": "p1", "client_turn_id": "c1", "status": "running", "_acceptance": {}}),
        MappingProxyType({"turn_id": "p2", "client_turn_id": "c2", "status": "queued", "_acceptance": {}}),
    )
    assert cta._prepared_turn_facts(turns[:2]) == {
        "count": 1,
        "turn_id": "p1",
        "client_turn_id": "c1",
    }
    assert cta._prepared_turn_facts(turns) == {"count": 2}


def test_real_ts_round_trips_accept_mapping_proxy_inputs() -> None:
    session_busy = MappingProxyType({"status": "busy", "active_turn_id": "t-act"})
    session_ready = MappingProxyType({"status": "ready", "active_turn_id": None})
    turn_queued = MappingProxyType(
        {
            "turn_id": "t-q",
            "status": "queued",
            "origin": "web",
            "message": "orig",
            "input_revision": 0,
            "attachments": (MappingProxyType({"id": "a1"}),),
        }
    )
    turn_active = MappingProxyType({"turn_id": "t-act", "status": "running", "origin": "web"})

    assert cta.queued_chat_turn_ids((turn_queued,), None) == ["t-q"]
    mention = cta.plan_native_mention_ingress(
        mentioned=True,
        binding_matches=True,
        text_only=True,
        may_deliver_new=True,
        target_turn_id="t-act",
        queued_turn_exists=False,
        receipt=None,
        receipt_message_matches=False,
    )
    assert isinstance(mention, dict) and "action" in mention

    steer_with_att = cta.plan_queued_chat_steering(
        session_busy,
        turn_queued,
        turn_active,
        None,
        ingress_id="in-1",
        operation_id="op-1",
        target="t-act",
        phase="prepare",
        web_origin_only=True,
    )
    assert steer_with_att == {
        "action": "reject",
        "error_code": "queue_steering_attachments_unavailable",
    }
    steer_text = cta.plan_queued_chat_steering(
        session_busy,
        MappingProxyType(
            {"turn_id": "t-q", "status": "queued", "origin": "web", "message": "orig"}
        ),
        turn_active,
        None,
        ingress_id="in-1",
        operation_id="op-1",
        target="t-act",
        phase="prepare",
        web_origin_only=True,
    )
    assert steer_text["action"] == "dispatch"

    edit_plan = cta.plan_queued_chat_mutation(
        session_busy,
        turn_queued,
        MappingProxyType(
            {"operation_id": "op1", "expected_revision": 0, "action": "edit", "message": "updated"}
        ),
    )
    assert edit_plan["action"] == "apply"
    assert edit_plan["message_patch"]["text"] == "updated"

    conflict_plan = cta.plan_queued_chat_mutation(
        session_busy,
        turn_queued,
        MappingProxyType({"operation_id": "op2", "expected_revision": 9, "action": "cancel"}),
    )
    assert conflict_plan == {"action": "reject", "error_code": "queue_input_revision_conflict"}

    assert cta.managed_turn_execution_allowed(session_busy, turn_active) is True
    assert cta.managed_turn_recovery_allowed(
        session_busy, turn_active, managed=True, owner_dead=True
    ) is True

    plan = cta.plan_managed_turn_acceptance(
        session_id="s1",
        client_turn_id="ct1",
        message="hi",
        display_message="",
        attachments=(),
        origin="web",
        loopx_execution=False,
        loopx_request=None,
        candidate_turn_id="t1",
        candidate_message_id="m1",
        accepted_at="2026-11-26T00:00:00Z",
        session=session_ready,
        active_turn=None,
        matching_turn=None,
        turns=(),
        messages=(),
        queued_events=(),
    )
    assert plan.disposition == "created"
    assert plan.dispatch_required is True


def test_decode_rejection_errors_and_malformed_effect_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for code, exc_type in (
        ("session_not_found", KeyError),
        ("session_closed", KeyError),
        ("request_conflict", cta.ManagedTurnReplayConflictError),
        ("active_turn_conflict", RuntimeError),
        ("original_request_unavailable", ValueError),
        ("durable_state_conflict", cta.ManagedTurnReplayConflictError),
        ("unsupported_code", ValueError),
    ):
        with pytest.raises(exc_type):
            cta._decode_plan(
                MappingProxyType(
                    {
                        "schema_version": cta.CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA,
                        "kind": "rejected",
                        "code": code,
                        "active_turn_id": "t-act",
                    }
                )
            )

    valid_writes = {
        "prepare_turn": True,
        "activate_session": True,
        "append_message": True,
        "append_queued_event": True,
        "settle_turn": False,
    }
    for bad in (
        "not_mapping",
        {"schema_version": "wrong"},
        {"schema_version": cta.CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA, "kind": "other"},
        {
            "schema_version": cta.CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA,
            "kind": "accepted",
            "disposition": "bad",
        },
        {
            "schema_version": cta.CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA,
            "kind": "accepted",
            "disposition": "created",
            "writes": "bad",
            "dispatch": {"kind": "required"},
        },
        {
            "schema_version": cta.CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA,
            "kind": "accepted",
            "disposition": "created",
            "writes": valid_writes,
            "dispatch": {"kind": "required", "reason": "terminal"},
        },
        {
            "schema_version": cta.CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA,
            "kind": "accepted",
            "disposition": "replayed",
            "writes": valid_writes,
            "dispatch": {"kind": "not_required", "reason": "bad_reason"},
        },
    ):
        with pytest.raises(ValueError):
            cta._decode_plan(bad)

    monkeypatch.setattr(cta, "effect_runtime_result", lambda *_a, **_kw: {"turn_ids": [123]})
    with pytest.raises(ValueError, match="turn_ids"):
        cta.queued_chat_turn_ids((), None)

    monkeypatch.setattr(cta, "effect_runtime_result", lambda *_a, **_kw: "bad")
    with pytest.raises(ValueError, match="must be an object"):
        cta.plan_native_mention_ingress(mentioned=True)
    with pytest.raises(ValueError, match="must be an object"):
        cta.managed_turn_execution_allowed(None, None)

    monkeypatch.setattr(
        cta,
        "effect_runtime_result",
        lambda *_a, **_kw: {"action": "apply", "event": {"kind": "", "payload": {}}},
    )
    with pytest.raises(ValueError, match="event kind"):
        cta.plan_queued_chat_mutation({}, {"turn_id": "t1", "status": "queued"}, {})
