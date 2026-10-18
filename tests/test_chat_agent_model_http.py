from __future__ import annotations

import http.client
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import loopx.chat_server as chat_server
from loopx.chat_store import ChatSessionStore


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
