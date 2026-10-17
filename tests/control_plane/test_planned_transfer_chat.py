"""The original Goal Chat entry uses fresh destination Sessions after cutover.

File/SQLite and HTTP/Session/Turn owners are real. The upstream conversation
adapter is scripted: these cases do not qualify a model or sustained Host.
"""
from __future__ import annotations

import http.client
import json
import uuid

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.goals.planned_transfer_io import control_transferred_goal, inspect_registered_goal_transfer
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_operation import _http
from tests.control_plane.test_planned_transfer_product import _goal_document

source = source_fixture


def _request(server, method, path, body=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=90)
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None,
            {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _chat(source, monkeypatch):
    calls = []

    class Adapter:
        upstream_thread_id = ""

        def __init__(self, settings):
            self.upstream_thread_id = settings.get("resume_thread_id") or uuid.uuid4().hex
            calls.append(settings)

        def healthcheck(self):
            return True

        def close_session(self):
            pass

        def start_turn(self, message, event_sink):
            calls.append({"message": message})
            return {"schema_version": "loopx_agent_response_v0", "message": "Continue the original report.",
                "proposals": [], "protected_action": None, "gate": None}

    monkeypatch.setattr(ChatRuntimeController, "capabilities", lambda self: [
        {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"}])
    monkeypatch.setattr(ChatRuntimeController, "_start_adapter", lambda self, **settings: Adapter(settings))
    server, thread, transfer = _http(source)
    server.selected_goal_id = "report"
    server.chat_store = ChatSessionStore(source["runtime"])
    server.runtime_controller = ChatRuntimeController(store=server.chat_store,
        registry_path=source["registry"], codex_bin="unused-scripted-endpoint")
    return server, thread, transfer, calls


def test_goal_chat_after_transfer_uses_destination_and_keeps_source_history(source, tmp_path, monkeypatch):
    _goal_document(source, absolute=True)
    server, thread, transfer, calls = _chat(source, monkeypatch)
    old = server.chat_store.create_session(goal_id="report", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="source-thread", upstream_mode="chat")
    server.chat_store.append_message(old["session_id"], role="user", text="Keep the original report requirement.")
    old_task = server.chat_store.create_session(goal_id="report", agent_id="codex",
        channel_id=f"task.{source['todo']}", adapter_kind="codex_app_server", upstream_thread_id="source-task")
    destination = tmp_path / "destination"
    destination.mkdir()
    try:
        prepared = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report",
            "--operation-id", "move-chat", "--destination-root", str(destination), "--execute")
        # A fenced preparation is not an admitted destination or an invitation
        # to reopen the original upstream thread.
        assert _request(server, "POST", "/api/chat/sessions", {"goal_id": "report"})[0] >= 400
        assert calls == []
        assert transfer({"action": "confirm", "goal_id": "report", "operation_id": "move-chat",
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]})[0] == 200
        before = source["registry"].read_bytes(), source["fence"].read_bytes()
        old_bytes = server.chat_store._session_path(old["session_id"]).read_bytes()
        status, opened = _request(server, "POST", "/api/chat/sessions", {"goal_id": "report"})
        assert status == 201 and opened["resumed"] is False
        sid = opened["session_id"]
        target_store = ChatSessionStore(destination / "runtime")
        assert target_store.load_session(sid) is not None
        assert sid != old["session_id"] and server.chat_store.load_session(sid) is None
        assert calls[0]["work_dir"].is_relative_to(destination)
        assert "resume_thread_id" not in calls[0] or calls[0]["resume_thread_id"] is None
        assert _request(server, "GET", f"/api/chat/sessions/{sid}")[0] == 200
        sessions = _request(server, "GET", "/api/chat/sessions?goal_id=report")[1]["sessions"]
        assert next(row for row in sessions if row["session_id"] == sid)["resumable"] is True
        assert next(row for row in sessions if row["session_id"] == old["session_id"])["resumable"] is False
        status, answer = _request(server, "POST", f"/api/chat/sessions/{sid}/turns",
            {"message": "Read the original Task requirements", "client_turn_id": "current-message"})
        assert status == 202
        current = server.goal_chat_routes.for_session(sid)
        turn = current.wait_for_turn(session_id=sid, turn_id=answer["turn_id"])
        assert turn["status"] == "completed", turn
        evidence_message = next(row["message"] for row in calls if "message" in row)
        assert source["todo"] in evidence_message and "Write the input sum" in evidence_message
        assert str(source["project"]) not in evidence_message
        assert _request(server, "POST", f"/api/chat/sessions/{sid}/turns",
            {"message": "Read the original Task requirements", "client_turn_id": "current-message"})[1]["created"] is False
        assert len([row for row in calls if "message" in row]) == 1
        for old_id in (old["session_id"], old_task["session_id"]):
            assert _request(server, "POST", f"/api/chat/sessions/{old_id}/resume", {})[0] >= 400
            assert _request(server, "POST", f"/api/chat/sessions/{old_id}/turns",
                {"message": "stale source", "client_turn_id": "old-message", "delivery": "queue"})[0] >= 400
            assert _request(server, "DELETE", f"/api/chat/sessions/{old_id}")[0] >= 400
            assert _request(server, "POST", f"/api/chat/sessions/{old_id}/loopx", {"operation": "start"})[0] >= 400
        old_view = _request(server, "GET", f"/api/chat/sessions/{old['session_id']}")[1]
        assert old_view["session"]["resumable"] is False
        assert old_view["messages"][0]["text"] == "Keep the original report requirement."
        assert server.chat_store._session_path(old["session_id"]).read_bytes() == old_bytes
        # Discard every in-process route/adapter, retaining normal destination
        # Session/Turn files. Cold GET/resume still choose the exact target.
        upstream = target_store.load_session(sid)["upstream_thread_id"]
        server.goal_chat_routes.close()
        del server.goal_chat_routes
        assert _request(server, "GET", f"/api/chat/sessions/{sid}")[0] == 200
        assert _request(server, "POST", f"/api/chat/sessions/{sid}/resume", {})[0] == 200
        assert target_store.load_session(sid)["upstream_thread_id"] == upstream
        assert calls[-1]["resume_thread_id"] == upstream
        target_registry = destination / "workspace/.loopx/registry.json"
        retained = target_registry.with_suffix(".retained")
        target_before = target_store._session_path(sid).read_bytes()
        call_count = len(calls)
        target_registry.rename(retained)
        try:
            for method, path, body in [
                ("POST", "/api/chat/sessions", {"goal_id": "report"}),
                ("POST", f"/api/chat/sessions/{sid}/resume", {}),
                ("POST", f"/api/chat/sessions/{sid}/turns", {"message": "retain this draft", "client_turn_id": "offline"}),
            ]:
                status, refused = _request(server, method, path, body)
                assert status >= 400 and refused["ok"] is False
                assert str(destination) not in json.dumps(refused)
            assert target_store._session_path(sid).read_bytes() == target_before
            assert len(calls) == call_count
        finally:
            retained.rename(target_registry)
        control = inspect_registered_goal_transfer(registry_path=source["registry"], goal_id="report")["destination_control"]
        control_transferred_goal(registry_path=source["registry"], goal_id="report", operation_id="move-chat",
            operation="stop", expected_state_fingerprint=control["expected_state_fingerprint"])
        assert _request(server, "GET", f"/api/chat/sessions/{sid}")[0] == 200
        assert _request(server, "POST", f"/api/chat/sessions/{sid}/turns",
            {"message": "stopped destination", "client_turn_id": "stopped"})[0] >= 400
        assert target_store._session_path(sid).read_bytes() == target_before
        assert len(calls) == call_count
        status, saved = _request(server, "POST", "/api/chat/projection-messages",
            {"goal_id": "report", "context_kind": "goal", "question": "Read current status",
                "answer": "The destination Goal is stopped; original work is retained."})
        assert status == 201
        assert target_store.load_session(saved["session_id"])["agent_id"] == "status-only"
        assert server.chat_store.load_session(saved["session_id"]) is None
        assert _request(server, "DELETE", f"/api/chat/sessions/{sid}")[1]["closed"] is True
        assert before == (source["registry"].read_bytes(), source["fence"].read_bytes())
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_unmoved_goal_chat_uses_the_original_store_and_controller(source, monkeypatch):
    _goal_document(source, absolute=True)
    server, thread, _, _ = _chat(source, monkeypatch)
    try:
        status, opened = _request(server, "POST", "/api/chat/sessions", {"goal_id": "report"})
        assert status == 201
        sid = opened["session_id"]
        assert server.chat_store.load_session(sid) is not None
        assert _request(server, "POST", "/api/chat/sessions", {"goal_id": "report"})[1]["session_id"] == sid
        assert server.goal_chat_routes.controllers == {}
        assert _request(server, "DELETE", f"/api/chat/sessions/{sid}")[1]["closed"] is True
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
