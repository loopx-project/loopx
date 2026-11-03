from __future__ import annotations

import http.client
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import loopx.chat_server as chat_server
from loopx.chat_store import ChatSessionStore


@pytest.fixture
def current_session_settings(tmp_path, monkeypatch):
    from loopx.chat_runtime import ChatRuntimeController

    store = ChatSessionStore(tmp_path / "runtime")
    home = tmp_path / "codex-home"
    home.mkdir()
    controller = ChatRuntimeController(store=store, codex_bin="codex")
    controller.codex_home = home
    monkeypatch.setattr(chat_server.ChatRequestHandler, "_registry_and_goal", lambda *_: ({}, {}))
    monkeypatch.setattr(chat_server, "_goal_public_context", lambda *_: {
        "project": tmp_path, "objective": "Keep this conversation.", "title": "Fixture"})
    monkeypatch.setattr("loopx.chat_providers.codex_model_options", lambda *_args, **_kwargs: {
        "options": [{"value": "first", "reasoning_efforts": ["low", "high"]},
                    {"value": "second", "reasoning_efforts": ["low", "high"]}]})
    session = store.create_session(goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="original-thread", agent_model_id="first", agent_reasoning_effort="low",
        requested_agent_model_id="first", requested_agent_reasoning_effort="low", upstream_mode="chat")
    from loopx.chat.coordination import PROJECT_CONTEXT_VERSION
    session = store.update_session(session["session_id"], coordination_context_version=PROJECT_CONTEXT_VERSION)
    server = chat_server.ChatHTTPServer(("127.0.0.1", 0), chat_server.ChatRequestHandler)
    server.verbose = False
    server.chat_store, server.runtime_controller = store, controller
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(body, action="model-settings"):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("POST", f"/api/chat/sessions/{session['session_id']}/{action}",
                body=json.dumps(body), headers={"Content-Type": "application/json",
                                              "Origin": f"http://127.0.0.1:{server.server_port}"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        yield store, session, post
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        controller.close()


@pytest.mark.parametrize("busy", [False, True])
def test_current_session_settings_preserve_identity_active_work_and_actual_readback(current_session_settings, busy):
    store, session, post = current_session_settings
    sid = session["session_id"]
    if busy:
        store.update_session(sid, status="busy", active_turn_id="existing-turn")
    before = store.load_session(sid)
    status, body = post({"agent_model_id": "second", "agent_reasoning_effort": "high"})
    assert status == 200, body
    after = store.load_session(sid)
    assert after["session_id"] == sid and after["upstream_thread_id"] == "original-thread"
    assert after["active_turn_id"] == before["active_turn_id"]
    assert after["agent_model_id"] == "first" and after["agent_reasoning_effort"] == "low"
    assert body["session"]["requested_agent_model_id"] == "second"
    assert body["session"]["requested_agent_reasoning_effort"] == "high"
    assert not store.messages(sid) and not list((store.sessions_root / sid / "turns").iterdir())
    reopened = ChatSessionStore(store.root.parent).load_session(sid)
    assert reopened == after


@pytest.mark.parametrize("changes", [
    {"agent_id": "claude-code"}, {"session_mode": "attached_host"},
    {"managed_agent_id": "registered-worker"}, {"channel_id": "manager"},
    {"steward_context": {}}, {"upstream_mode": "chat_native_goal"},
    {"upstream_mode": "default"}, {"coordination_context_version": 0},
    {"project_context": {"audience": "bound_owner"}},
])
def test_host_owned_and_legacy_sessions_do_not_offer_or_admit_model_changes(current_session_settings, monkeypatch, changes):
    store, session, post = current_session_settings
    # Feed existing persisted identity to the reader; never test by rewriting a
    # live authority or granting a fabricated external audience.
    original = store.load_session(session["session_id"])
    before = {**original, **changes}
    monkeypatch.setattr(store, "load_session", lambda _sid: dict(before))
    monkeypatch.setattr(chat_server.ChatRequestHandler, "_session_context", lambda *_: {})
    assert store.public_session(before)["model_settings_supported"] is False
    status, body = post({"agent_model_id": "second", "agent_reasoning_effort": "high"})
    assert status == 424 and body["error_code"] == "unsupported_configuration"
    assert ChatSessionStore(store.root.parent).load_session(session["session_id"]) == original


def test_current_session_setting_changes_are_isolated(current_session_settings):
    store, session, post = current_session_settings
    other = store.create_session(goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="another-thread", agent_model_id="first", upstream_mode="chat")
    assert post({"agent_model_id": "second", "agent_reasoning_effort": "high"})[0] == 200
    assert store.load_session(other["session_id"]) == other


@pytest.mark.parametrize("message,expected_status", [("Original message.", 202), ("Conflicting message.", 400)])
def test_completed_request_retry_keeps_its_receipt_after_later_settings_change(current_session_settings, monkeypatch, message, expected_status):
    from loopx.chat_runtime import ChatRuntimeController
    store, session, post = current_session_settings
    sid = session["session_id"]
    accepted = store.accept_managed_turn(sid, client_turn_id="original-send", message="Original message.")
    store.update_turn(sid, accepted.turn["turn_id"], status="completed", response={"message": "Original answer."})
    store.update_session(sid, status="ready", active_turn_id=None)
    assert post({"agent_model_id": "second", "agent_reasoning_effort": "high"})[0] == 200
    before = store.load_session(sid)
    monkeypatch.setattr(ChatRuntimeController, "_ensure_adapter_locked", lambda *_a, **_kw: pytest.fail(
        "Recovering an existing receipt cannot prepare or dispatch a later configuration"))
    status, body = post({"client_turn_id": "original-send", "message": message}, "turns")
    assert status == expected_status
    if status == 202:
        assert body["created"] is False and body["turn_id"] == accepted.turn["turn_id"]
    assert store.load_session(sid) == before


@pytest.mark.parametrize("failure", [ValueError("grant revoked"), OSError("provider unavailable"), KeyError("closed")])
def test_configuration_preparation_failure_is_known_non_delivery(current_session_settings, monkeypatch, failure):
    from loopx.chat_runtime import ChatRuntimeController
    store, session, post = current_session_settings
    assert post({"agent_model_id": "second", "agent_reasoning_effort": "high"})[0] == 200
    monkeypatch.setattr(ChatRuntimeController, "_ensure_adapter_locked", lambda *_a, **_kw: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(store, "accept_managed_turn", lambda *_a, **_kw: pytest.fail("Failed preparation must precede admission"))
    status, body = post({"client_turn_id": "explicit-send", "message": "Keep this draft."}, "turns")
    assert status == 424 and body["delivery_state"] == "not_delivered" and body["turn_replay_safe"] is True
    assert not store.messages(session["session_id"])
    assert not list((store.sessions_root / session["session_id"] / "turns").iterdir())


@pytest.mark.parametrize("client_id", ["invalid/id", "invalid id", "invalid:identity"])
def test_invalid_turn_identity_is_rejected_before_pending_model_preparation(current_session_settings, monkeypatch, client_id):
    from loopx.chat_agent import CodexChatAgentError
    from loopx.chat_runtime import ChatRuntimeController
    store, session, post = current_session_settings
    assert post({"agent_model_id": "second", "agent_reasoning_effort": "high"})[0] == 200
    before = store.load_session(session["session_id"])
    preparations = []
    def prepare(*_args, **_kwargs):
        preparations.append(True)
        raise CodexChatAgentError("Native configuration rejected", error_code="unsupported_configuration", gate=None)
    monkeypatch.setattr(ChatRuntimeController, "_ensure_adapter_locked", prepare)
    status, body = post({"client_turn_id": client_id, "message": "Invalid identity must start no native operation."}, "turns")
    assert status == 400 and "compact opaque id" in body["error"]
    assert not preparations
    assert store.load_session(session["session_id"]) == before
    assert not store.messages(session["session_id"])


@pytest.mark.parametrize("body", [{"agent_model_id": "unknown"},
    {"agent_model_id": "second", "agent_reasoning_effort": "unsupported"},
    {"agent_model_id": "second", "origin": "lark"}, {"agent_reasoning_effort": "high"}])
def test_current_session_settings_refuse_invalid_or_claimed_authority_without_changes(current_session_settings, body):
    store, session, post = current_session_settings
    before = store.load_session(session["session_id"])
    status, _ = post(body)
    assert status in {400, 424}
    assert store.load_session(session["session_id"]) == before


def test_current_session_settings_do_not_modify_a_session_closed_during_catalog_read(current_session_settings, monkeypatch):
    store, session, post = current_session_settings
    def catalog(*_args, **_kwargs):
        store.update_session(session["session_id"], status="closed")
        return {"options": [{"value": "second", "reasoning_efforts": ["low", "high"]}]}
    monkeypatch.setattr("loopx.chat_providers.codex_model_options", catalog)
    status, _ = post({"agent_model_id": "second", "agent_reasoning_effort": "high"})
    assert status == 404
    saved = store.load_session(session["session_id"])
    assert saved["status"] == "closed" and saved["requested_agent_model_id"] == "first"


@pytest.mark.parametrize("failure", ["unavailable", "write"])
def test_current_session_settings_failure_preserves_previous_values(current_session_settings, monkeypatch, failure):
    store, session, post = current_session_settings
    before = store.load_session(session["session_id"])
    if failure == "unavailable":
        monkeypatch.setattr("loopx.chat_providers.codex_model_options", lambda *_a, **_kw: None)
    else:
        monkeypatch.setattr(store, "update_session", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("write refused")))
    status, _ = post({"agent_model_id": "second", "agent_reasoning_effort": "high"})
    assert status == (424 if failure == "unavailable" else 503)
    assert store.load_session(session["session_id"]) == before


@pytest.mark.parametrize("value", ["vendor/model  fast", "vendor/model-fast", " vendor/model "])
def test_http_session_preserves_exact_model_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    status, payload, received = _create_session(tmp_path, monkeypatch, value)
    assert status == 201, payload
    assert received == [value]
    assert payload["session"]["agent_model_id"] == value


@pytest.mark.parametrize("value", ["model\nnext", "model\tfast", "x" * 161, "   "])
def test_http_session_rejects_invalid_model_before_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    status, payload, received = _create_session(tmp_path, monkeypatch, value)
    assert status == 400, payload
    assert received == []
    assert "agent_model_id" in payload["error"]


def _create_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> tuple[int, dict, list[str]]:
    received: list[str] = []
    store = ChatSessionStore(tmp_path / "runtime")
    monkeypatch.setattr(chat_server.ChatRequestHandler, "_registry_and_goal", lambda _self, _goal: ({}, {}))
    monkeypatch.setattr(chat_server, "_goal_public_context", lambda *_args: {
        "project": tmp_path, "objective": "Validate model input.", "title": "Fixture",
    })

    def open_session(**kwargs):
        model = kwargs["agent_model_id"]
        received.append(model)
        return store.create_session(
            goal_id="fixture", agent_id="fixture-acp", adapter_kind="acp",
            upstream_thread_id="fixture-thread", agent_model_id=model,
        ), False

    monkeypatch.setattr(chat_server, "open_chat_session", open_session)
    server = chat_server.ChatHTTPServer(("127.0.0.1", 0), chat_server.ChatRequestHandler)
    server.verbose = False
    server.selected_goal_id = "fixture"
    server.chat_store = store
    server.runtime_controller = SimpleNamespace(close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("POST", "/api/chat/sessions", body=json.dumps({
            "goal_id": "fixture", "agent_id": "fixture-acp", "agent_model_id": value, "mode": "new",
        }), headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.server_port}"})
        response = connection.getresponse()
        return response.status, json.loads(response.read()), received
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("requested", [None, "explicit-model"])
def test_native_resolved_model_survives_restart_without_turning_default_into_pin(tmp_path, monkeypatch, requested):
    from loopx.chat_runtime import ChatRuntimeController, CodexAppServerAdapter

    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin="codex")
    monkeypatch.setattr(runtime, "capabilities", lambda: [
        {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"},
    ])
    calls = []

    def start(**kwargs):
        calls.append(kwargs)
        resolved = requested or ("runtime-default" if len(calls) == 1 else "new-runtime-default")
        return CodexAppServerAdapter(SimpleNamespace(
            model=resolved, resolved_model=resolved, thread_id="native-thread", process=SimpleNamespace(poll=lambda: None), close=lambda: None,
        ))

    monkeypatch.setattr(runtime, "_start_adapter", start)
    try:
        session, _ = runtime.open_session(goal_id="fixture", agent_id="codex", work_dir=tmp_path,
            objective="Check the model.", mode="new", agent_model_id=requested)
        assert store.public_session(session)["agent_model_id"] == (requested or "runtime-default")
        assert session["requested_agent_model_id"] == requested
        runtime.adapters.clear()  # Only this disposable controller restarts; no native Turn runs.
        restored, reused = runtime.open_session(goal_id="fixture", agent_id="codex", work_dir=tmp_path,
            objective="Check the model.", mode="resume_latest")
        assert reused and restored["session_id"] == session["session_id"]
        assert calls[-1]["resume_thread_id"] == "native-thread"
        assert calls[-1]["agent_config"] == ({"model": requested} if requested else None)
        assert store.public_session(restored)["agent_model_id"] == (requested or "new-runtime-default")
        assert not store.messages(session["session_id"])
        assert not list((store.sessions_root / session["session_id"] / "turns").iterdir())
    finally:
        runtime.close()


def test_http_replay_withholds_legacy_raw_reasoning_without_rewriting_history(tmp_path):
    store = ChatSessionStore(tmp_path / "runtime")
    session = store.create_session(goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="native-thread")
    sid = session["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="original", message="Inspect.")
    tid = turn["turn_id"]
    store.append_event(sid, tid, kind="agent.phase", payload={"label": "Agent working", "step": {
        "id": "legacy", "kind": "reasoning", "state": "completed", "title": "raw-private-fixture",
        "detail": "raw-private-fixture", "duration_ms": 12,
    }})
    store.append_event(sid, tid, kind="agent.phase", payload={"label": "Agent working", "step": {
        "id": "summary", "kind": "reasoning", "state": "completed", "title": "Public summary",
        "detail": "Public summary", "reasoning_summary": True,
    }})
    store.update_turn(sid, tid, status="completed")
    store.append_event(sid, tid, kind="turn.completed", payload={"message": "Done."})
    server = chat_server.ChatHTTPServer(("127.0.0.1", 0), chat_server.ChatRequestHandler)
    server.verbose = False
    server.chat_store = store
    # A completed replay has no exited execution to recover. Keep the HTTP
    # fixture's terminal recovery hook aligned with the real controller.
    server.runtime_controller = SimpleNamespace(close=lambda: None, recover_exited_turn=lambda *_: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", f"/api/chat/sessions/{sid}/turns/{tid}/events")
        response = connection.getresponse()
        body = response.read().decode()
        assert response.status == 200
        assert "raw-private-fixture" not in body
        assert "Public summary" in body and '"duration_ms":12' in body
        assert "raw-private-fixture" in json.dumps(store.events_after(sid, tid, "0")), "Readback must not rewrite stored history"
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
