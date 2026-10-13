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
        }), headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1"})
        response = connection.getresponse()
        return response.status, json.loads(response.read()), received
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
