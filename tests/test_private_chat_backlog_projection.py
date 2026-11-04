"""Read-only backlog projection uses bounded RPCs and retains native stop facts."""
from types import SimpleNamespace
from copy import deepcopy
import json

import pytest

from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION, ChatSessionStore, _atomic_write_json
from loopx.control_plane import effect_runtime
from loopx.extensions.lark.private_conversations import LarkPrivateConversations


def fixture_history(tmp_path, count=130):
    store = ChatSessionStore(tmp_path)
    session = store.create_session(goal_id="demo", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="native", session_id="a" * 32)
    controller = SimpleNamespace(store=store,
        project_contexts=SimpleNamespace(conversation_bindings=SimpleNamespace()))
    core = ChatExternalConversations(controller)
    transport = LarkPrivateConversations.__new__(LarkPrivateConversations)
    transport.core, transport.root = core, store.root / "lark-private-deliveries"
    rows = []
    for index in range(count):
        ref, tid = f"{index:024x}", f"{index:032x}"
        row = {"request_ref": ref, "binding_id": "b" * 24,
            "source": {"source_ref": "c" * 24}, "session_id": session["session_id"], "turn_id": tid,
            "command": None, "status": "accepted", "delivery_verified": True,
            "message": "Original user request", "created_at": "2026-01-01T00:00:00+00:00"}
        turn = {"schema_version": CHAT_TURN_SCHEMA_VERSION, "session_id": session["session_id"],
            "turn_id": tid, "client_turn_id": f"external-{ref}", "status": "failed",
            "error_code": "usage_limit_exceeded", "response": None}
        _atomic_write_json(store._turn_path(session["session_id"], tid), turn)
        _atomic_write_json(core.root / f"{ref}.json", row)
        _atomic_write_json(transport.root / f"{ref}.json", dict(row, status="delivered", deliveries={}))
        rows.append(row)
    return store, core, transport, rows


def durable_state(store):
    return {str(p.relative_to(store.root)): p.read_bytes() for p in store.root.rglob("*")
        if p.suffix in {".json", ".jsonl"}}


def test_full_history_is_read_once_per_batch_and_control_changes_are_fresh(tmp_path, monkeypatch):
    store, core, transport, rows = fixture_history(tmp_path)
    before = durable_state(store)
    call = effect_runtime.effect_runtime_result
    calls = []

    def observed(method, params, **kwargs):
        if method == "collaboration.conversation.request":
            calls.append(deepcopy(params))
        return call(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", observed)
    assert transport.health()["b" * 24] == {"pending_count": 130, "recovery_count": 0}
    assert [len(item["pending_observations"]) for item in calls] == [64, 64, 2]
    assert durable_state(store) == before
    # A later stop affects the next read even though the earlier scan shared IO.
    control = dict(rows[0], request_ref="f" * 24, command="stop", created_at="2026-01-02T00:00:00+00:00")
    _atomic_write_json(core.root / ("f" * 24 + ".json"), control)
    after_stop = durable_state(store)
    assert transport.health()["b" * 24]["pending_count"] == 0
    assert durable_state(store) == after_stop
    assert all(not core.quota_backlog_pending(row) for row in rows)


@pytest.mark.parametrize("size", [600_000, 1_100_000])
def test_byte_split_and_original_large_single_request_keep_order(tmp_path, monkeypatch, size):
    _, core, _, rows = fixture_history(tmp_path, count=3)
    rows[0]["message"] = "x" * size
    rows[1]["message"] = "x" * size
    # The third unrelated policy refusal must not acquire a neighbour's flag.
    store = core.controller.store
    store.update_turn(rows[2]["session_id"], rows[2]["turn_id"], error_code="policy")
    expected = [core.quota_backlog_pending(row) for row in rows]
    call = effect_runtime.effect_runtime_result
    params_seen = []

    def observed(method, params, **kwargs):
        if method == "collaboration.conversation.request":
            params_seen.append(deepcopy(params))
        return call(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", observed)
    assert core.quota_backlog_observer()(rows) == expected == [True, True, False]
    assert len(params_seen) >= 2
    assert all(len(json.dumps(p).encode()) < 1_250_000 for p in params_seen)
    assert any("request" in p for p in params_seen) is (size > 1_048_576)


@pytest.mark.parametrize("result", [None, {}, {"pending_flags": []}, {"pending_flags": [1]}])
def test_unavailable_or_misaligned_batch_result_is_never_empty_work(tmp_path, monkeypatch, result):
    _, core, _, rows = fixture_history(tmp_path, count=1)
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", lambda *_args, **_kwargs: result)
    with pytest.raises(ValueError, match="unavailable"):
        core.quota_backlog_observer()(rows)
