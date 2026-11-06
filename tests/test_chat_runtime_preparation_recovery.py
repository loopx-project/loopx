"""Recover the original source after an account outage, without replaying work."""
import json

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_chat_queue_preparation import session_runtime
from test_lark_private_conversations import connect
from test_manager_preparation_recovery import advance_recovery_clock
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_store import _read_json


def test_account_preparation_keeps_its_error_before_native_dispatch(tmp_path, monkeypatch):
    store, sid, runtime = session_runtime(tmp_path)

    def unavailable(**_kwargs):
        raise CodexChatAgentError("Native account API transport is unavailable.",
                                  error_code="model_auth_unavailable", gate=None)

    monkeypatch.setattr(runtime, "_start_adapter", unavailable)
    try:
        turn, _ = runtime.enqueue_turn(session_id=sid, client_turn_id="account-outage",
            message="Original work", work_dir=tmp_path, objective="Research")
        failed = runtime.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=5)
        assert failed["error_code"] == "model_auth_unavailable"
        assert failed["started_at"] is None and failed["first_event_at"] is None
        assert [e["kind"] for e in store.events_after(sid, turn["turn_id"], None)] == ["turn.queued", "turn.failed"]
    finally:
        runtime.close()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("fault", [None, "route-unavailable", "stop", "source-changed", "duplicate", "admission-crash"])
def test_original_project_worker_recovers_and_returns_exact_source(ordinary, monkeypatch, legacy, fault):  # noqa: F811
    from loopx.capabilities.native_chat import codex_auth
    store, runtime, provider, transport = connect(ordinary)
    ensure = runtime._ensure_adapter

    def unavailable(*_args, **_kwargs):
        raise CodexChatAgentError("The previous Agent conversation could not be restored." if legacy
            else "Native account API transport is unavailable.",
            error_code="resume_failed" if legacy else "model_auth_unavailable", gate=None)

    monkeypatch.setattr(runtime, "_ensure_adapter", unavailable)
    try:
        event = provider.event("notes-app", "original-account-outage", "Explain this original material")
        transport.admit("notes-app", event)
        row = transport.core.pending()[0]
        failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert failed["started_at"] is None
        transport.reconcile()
        path = transport.root / f"{row['request_ref']}.json"
        original_delivery = _read_json(path)["deliveries"]["terminal"]
        assert "准备执行器连接时失败" in original_delivery["text"]
        assert "剩余次数接续" in original_delivery["text"]
        original_bytes = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
        monkeypatch.setattr(runtime, "_ensure_adapter", ensure)
        monkeypatch.setattr(codex_auth, "native_api_transport", lambda *_a, **_kw: None)
        advance_recovery_clock(monkeypatch)
        if fault == "route-unavailable":
            monkeypatch.setattr(codex_auth, "native_api_transport", lambda *_a, **_kw:
                (_ for _ in ()).throw(codex_auth.CodexHostAuthUnavailable()))
        elif fault == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Different original question"
        elif fault == "stop":
            transport.admit("notes-app", provider.event("notes-app", "later-stop", "/stop"))
        elif fault == "duplicate":
            transport.admit("notes-app", provider.event("notes-app", "resubmitted", row["message"]))
            later = next(x for x in transport.core.pending() if x["request_ref"] != row["request_ref"])
            runtime.wait_for_turn(session_id=later["session_id"], turn_id=later["turn_id"], timeout_sec=10)
        elif fault == "admission-crash":
            enqueue = runtime.enqueue_turn

            def committed(**kwargs):
                enqueue(**kwargs)
                monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
                raise OSError("crash after committed continuation")

            monkeypatch.setattr(runtime, "enqueue_turn", committed)
            with pytest.raises(OSError, match="committed continuation"):
                transport.reconcile_request(path)
        transport.reconcile_request(path)
        current = transport.core.read_request(row["request_ref"])
        if fault in {"route-unavailable", "stop", "source-changed", "duplicate"}:
            assert current["turn_id"] == row["turn_id"] and not current.get("context_recovery")
            return
        assert current["session_id"] == row["session_id"] and current["turn_id"] != row["turn_id"]
        assert current["context_recovery"]["preparation_attempt"] == 1
        result = runtime.wait_for_turn(session_id=row["session_id"], turn_id=current["turn_id"], timeout_sec=15)
        assert result["status"] == "completed"
        transport.reconcile()
        record = _read_json(path)
        final = next(v for k, v in record["deliveries"].items() if k.startswith("terminal.recovery."))
        assert final["verified"] and final["text"] == result["response"]["message"]
        actual = provider.messages[final["attempt"]["message_ref"]]
        assert actual["chat_id"] == event["chat_id"]
        assert actual["sender"]["id"] == provider.profile_apps["notes-app"]
        assert record["deliveries"]["terminal"] == original_delivery
        assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == original_bytes
        before = ordinary[4].read_bytes()
        transport.reconcile()
        assert ordinary[4].read_bytes() == before
        assert sum(json.loads(line)["method"] == "turn/start" for line in before.decode().splitlines()) == 1
    finally:
        runtime.close()
