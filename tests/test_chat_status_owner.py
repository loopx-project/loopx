"""The status owner is canonical and has no top-level compatibility shim."""

from __future__ import annotations

import http.client
import importlib.util
import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler


def test_status_owner_imports_canonically_and_has_no_legacy_module():
    code = """
import importlib.util
from loopx.chat.status_api import ChatStatusRequestMixin, goal_read_runtime_root
from loopx.chat.todo_detail import goal_read_runtime_root as todo_runtime_root
from loopx.chat_server import ChatRequestHandler

assert issubclass(ChatRequestHandler, ChatStatusRequestMixin)
assert ChatStatusRequestMixin.__module__ == 'loopx.chat.status_api'
assert goal_read_runtime_root.__module__ == 'loopx.chat.status_api'
assert todo_runtime_root is goal_read_runtime_root
assert importlib.util.find_spec('loopx.chat_status_api') is None
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert importlib.util.find_spec("loopx.chat_status_api") is None


@pytest.mark.parametrize("source_bound,controller_root,expected", [
    (True, "coordinator-decoy", None),
    (False, "controller-runtime", "controller-runtime"),
    (False, None, "chat-fallback"),
])
def test_registry_patch_reaches_already_imported_todo_consumer(
    monkeypatch, tmp_path, source_bound, controller_root, expected,
):
    from loopx.chat.todo_detail import goal_read_runtime_root

    registry_path = tmp_path / "registry.json"
    calls = []

    def registry(path):
        calls.append(path)
        return {"common_runtime_root": "source-runtime"} if source_bound else {}

    monkeypatch.setattr("loopx.chat.status_api.load_project_registry", registry)
    server = SimpleNamespace(
        registry_path=registry_path,
        runtime_root_override="chat-fallback",
        runtime_controller=SimpleNamespace(coordination_runtime_root=controller_root),
    )
    assert goal_read_runtime_root(server) == expected
    assert calls == [registry_path]


@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError, ValueError])
def test_discovery_patch_only_tolerates_missing_registry(monkeypatch, failure):
    from loopx.chat import status_api

    def unreadable(_path):
        raise failure("synthetic unavailable registry")

    monkeypatch.setattr("loopx.chat.status_api.load_project_registry", unreadable)
    server = SimpleNamespace(registry_path=None, runtime_root_override=None)
    if failure is FileNotFoundError:
        assert status_api.goal_read_runtime_root(server) is None
    else:
        with pytest.raises(failure):
            status_api.goal_read_runtime_root(server)


@pytest.fixture
def status_server(tmp_path):
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = tmp_path / "registry.json"
    server.runtime_root = tmp_path / "runtime"
    server.runtime_root_override = None
    server.selected_goal_id = None
    server.scan_roots = []
    server.limit = 20
    server.verbose = False
    server.goal_subagent_configuration_enabled = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def get(query):
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request("GET", "/status.json" + query)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        yield server, get
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_canonical_status_patch_points_reach_the_real_http_handler(monkeypatch, status_server):
    server, get = status_server
    registry = {"goals": [{"id": "alpha"}]}
    registry_reads, collected = [], []

    def load_registry(path):
        registry_reads.append(path)
        return registry

    def collect_status(**kwargs):
        collected.append(kwargs)
        return {"ok": True, "owner_probe": "collector"}

    def attach_activity(projection, **_kwargs):
        projection["owner_activity"] = True

    def attach_delivery(projection, **_kwargs):
        projection["owner_delivery"] = True

    monkeypatch.setattr("loopx.chat.status_api.load_registry", load_registry)
    monkeypatch.setattr("loopx.chat.status_api.load_project_registry", lambda _: {})
    monkeypatch.setattr("loopx.chat.status_api.collect_status", collect_status)
    monkeypatch.setattr("loopx.chat.status_api.attach_host_thread_activity", attach_activity)
    monkeypatch.setattr("loopx.chat.status_api.attach_host_delivery_windows", attach_delivery)
    monkeypatch.setattr("loopx.chat.status_api.codex_thread_observers", lambda **_: [])
    code, payload = get("?goal_id=alpha")
    assert code == 200, payload
    assert payload["owner_probe"] == "collector"
    assert payload["owner_activity"] is True
    assert payload["owner_delivery"] is True
    assert len(collected) == 1 and collected[0]["goal_id"] == "alpha"
    assert registry_reads == [server.registry_path, server.registry_path]


def test_lifecycle_registry_discovery_does_not_grant_status_execution(monkeypatch, status_server):
    server, get = status_server
    server.registry_path.write_text(json.dumps({
        "schema_version": 1, "profile_id": "source_session_v1",
        "goals": [{"id": "alpha"}],
    }))
    collected = []

    def forbidden(**kwargs):
        collected.append(kwargs)
        pytest.fail("lifecycle metadata must not reach the status collector")

    monkeypatch.setattr("loopx.chat.status_api.collect_status", forbidden)
    code, directory = get("?view=workspace-directory")
    assert code == 200, directory
    assert [row["id"] for row in directory["goals"]] == ["alpha"]
    code, refused = get("?goal_id=alpha")
    assert code >= 400 and not refused["ok"]
    assert collected == []
