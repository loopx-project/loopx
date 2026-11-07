"""The real Chat status endpoint must not override a Goal's source authority."""
# ruff: noqa: F811 - pytest injects the imported provider fixture by name.
import http.client
import json
import threading
from types import SimpleNamespace

import pytest

from test_todo_source_runtime_route import GOAL, routed, seed  # noqa: F401 - shared isolated provider fixture

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.effect_runtime import restart_effect_runtime


@pytest.fixture
def status_api(routed, tmp_path, monkeypatch):
    _, _, registry, shared, _, _ = routed
    chat = tmp_path / "private-chat"
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root = chat
    server.runtime_root_override = str(chat)
    server.runtime_controller = SimpleNamespace(coordination_runtime_root=shared, close=lambda: None)
    server.selected_goal_id = None
    server.scan_roots = [tmp_path]
    server.limit = 80
    server.verbose = False
    server.goal_subagent_configuration_enabled = False
    monkeypatch.setattr("loopx.chat_status_api.attach_host_thread_activity", lambda *a, **k: None)
    monkeypatch.setattr("loopx.chat_status_api.attach_host_delivery_windows", lambda *a, **k: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(query="", *, delivery_review=False):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=90)
        connection.request("GET", ("/api/chat/delivery-review" if delivery_review else "/status.json") + query)
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        assert str(tmp_path) not in json.dumps(value), "HTTP must redact source paths"
        return response.status, value

    yield call
    server.shutdown()
    thread.join(5)
    server.server_close()
    restart_effect_runtime()


@pytest.mark.parametrize("query", ["", f"?goal_id={GOAL}"])
def test_status_reads_source_authority_without_creating_a_chat_copy(status_api, routed, query):
    source, runtime, registry, shared, state, _ = routed
    before = state.read_bytes(), source.read_bytes(), registry.read_bytes()
    authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    code, result = status_api(query)
    assert code == 200, result
    assert result["run_history"]["goals"][0]["id"] == GOAL
    assert "todo_source_0" in json.dumps(result)
    assert (state.read_bytes(), source.read_bytes(), registry.read_bytes()) == before
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == authority
    assert not (shared / "goals" / GOAL).exists()
    assert runtime != shared


def test_delivery_review_task_map_uses_the_same_source(status_api):
    code, result = status_api(f"?goal_id={GOAL}", delivery_review=True)
    assert code == 200, result
    assert "todo_source_0" in json.dumps(result["goal_map"])


@pytest.mark.parametrize("failure", ["missing", "unreadable", "wrong_goal"])
def test_source_loss_never_falls_back_to_shared_decoy(status_api, routed, failure):
    source, _, _, shared, state, provider = routed
    seed(shared, state, provider, prefix="decoy")
    if failure == "wrong_goal":
        payload = json.loads(source.read_text())
        payload["goals"] = []
        source.write_text(json.dumps(payload))
    else:
        source.unlink()
        if failure == "unreadable":
            source.mkdir()
    code, result = status_api(f"?goal_id={GOAL}")
    assert code >= 400 and not result["ok"]
    assert "todo_decoy" not in json.dumps(result)
    code, directory = status_api("?view=workspace-directory")
    assert code == 200 and directory["goals"][0]["id"] == GOAL


def test_source_runtime_is_reread_without_restarting_chat(status_api, routed):
    source, _, _, _, state, provider = routed
    assert status_api(f"?goal_id={GOAL}")[0] == 200
    replacement = state.parent / "replacement-runtime"
    seed(replacement, state, provider, prefix="replacement")
    payload = json.loads(source.read_text())
    payload["common_runtime_root"] = str(replacement)
    source.write_text(json.dumps(payload))
    code, result = status_api(f"?goal_id={GOAL}")
    assert code == 200, result
    assert "todo_replacement_0" in json.dumps(result)
    assert "todo_source_0" not in json.dumps(result)
