"""Real native timeout settlement with synthetic host and provider transports."""
import json
import threading

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from test_native_steward_private import steward  # noqa: F401

from loopx.chat_codex_goal import CodexGoalDriver
from loopx.extensions.lark.private_conversations import LarkPrivateConversations


@pytest.mark.parametrize("timeout_kind", ["idle", "hard"])
def test_private_timeout_returns_once_after_delivery_readback_recovery(ordinary, timeout_kind):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    runtime.idle_timeout_sec = .1 if timeout_kind == "idle" else 30
    runtime.hard_timeout_sec = .1 if timeout_kind == "hard" else 30
    try:
        event = provider.event("notes-app", "timeout", "wait for interrupt")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        sid, tid = row["session_id"], row["turn_id"]
        terminal = runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert terminal["status"] == "timed_out"
        assert terminal["error_code"] == f"{timeout_kind}_timeout"
        original = store.load_session(sid)["upstream_thread_id"]

        provider.verify_replies = False
        assert transport.reconcile() == 0
        assert any(profile == "notes-app" and "本次执行超时" in text for profile, text in provider.writes)
        assert not transport.core.read_request(row["request_ref"]).get("delivery_verified")
        count = len(provider.writes)
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
                                            runner=provider, cli_bin="lark-cli")
        assert restarted.reconcile() == 0
        assert len(provider.writes) == count
        provider.verify_replies = True
        assert restarted.reconcile() == 1
        assert restarted.core.read_request(row["request_ref"])["delivery_verified"] is True
        assert restarted.health()[row["binding_id"]] == {"pending_count": 0, "recovery_count": 0}
        assert restarted.admit("notes-app", {**event, "event_id": "redelivery"})["status"] == "durably_accepted"
        restarted.reconcile()
        assert len(provider.writes) == count
        assert len(store.list_sessions()) == 1
        assert store.load_session(sid)["upstream_thread_id"] == original
        assert store.load_session(sid)["goal_id"] is None
        assert store.load_turn(sid, tid)["status"] == "timed_out"
        requests = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        assert len([r for r in requests if r.get("method") == "turn/start"]) == 1
    finally:
        runtime.close()


def test_commission_timeout_returns_original_resources_without_restarting_work(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, binding, capture, _ = steward
    started, release = threading.Event(), threading.Event()
    # Inject a typed host timeout after Goal activation. Core persists the
    # terminal Turn; this does not qualify the real native Goal deadline.
    def timeout(self, emit):
        started.set()
        assert release.wait(10), "timeout fixture was not released"
        raise self.session._timeout_error("hard_timeout", "Synthetic host timeout")
    monkeypatch.setattr(CodexGoalDriver, "_observe", timeout)
    event = provider.event("steward-app", "delegate", "/delegate --tokens 12000 wait for interrupt")
    assert transport.admit("steward-app", event)["status"] == "command_recorded"
    transport.reconcile()
    proposal = transport.core.actions.store.list()[0]
    confirm = provider.event("steward-app", "confirm", "/confirm " + proposal["proposal_id"])
    assert transport.admit("steward-app", confirm)["status"] == "command_recorded"
    transport.reconcile()
    resources = transport.core.actions.load(proposal["proposal_id"])["receipt"]["resource_ids"]
    assert started.wait(10)
    provider.verify_replies = False
    release.set()
    turn = runtime.wait_for_turn(session_id=resources["session_id"], turn_id=resources["turn_id"], timeout_sec=10)
    assert turn["status"] == "timed_out" and turn["error_code"] == "hard_timeout"
    transport.reconcile()
    assert any(profile == "steward-app" and "委托首轮执行超时" in text and
               f"/resume-commission {proposal['proposal_id']}" in text for profile, text in provider.writes)
    count = len(provider.writes)
    before = capture.read_text()
    restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
                                        runner=provider, cli_bin="lark-cli")
    assert restarted.reconcile() == 0
    assert len(provider.writes) == count
    provider.verify_replies = True
    assert restarted.reconcile() == 1
    assert restarted.health()[binding["binding_id"]] == {"pending_count": 0, "recovery_count": 0}
    restarted.admit("steward-app", {**confirm, "event_id": "redelivery"})
    restarted.reconcile()
    assert len(provider.writes) == count
    assert transport.core.actions.load(proposal["proposal_id"])["receipt"]["resource_ids"] == resources
    assert len(json.loads(runtime.registry_path.read_text())["goals"]) == 1
    assert store.load_turn(resources["session_id"], resources["turn_id"])["status"] == "timed_out"
    assert capture.read_text() == before
    assert not any(profile == "notes-app" for profile, _ in provider.writes)
