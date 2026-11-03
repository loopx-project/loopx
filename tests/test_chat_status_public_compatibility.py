"""I3 protects historical imports and their live patch points, not copied exports."""

from __future__ import annotations

import http.client
import importlib
import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler


@pytest.mark.parametrize("first_import", [
    "loopx.chat_status_api", "loopx.chat_server", "loopx.chat.todo_detail",
])
def test_historical_status_imports_survive_import_order_reload_and_pickle(first_import):
    # A fresh process avoids an earlier test silently choosing the import order.
    code = """
import importlib
import pickle
import sys
from types import SimpleNamespace

importlib.import_module(sys.argv[1])
public = importlib.import_module('loopx.chat_status_api')
from loopx.chat_status_api import ChatStatusRequestMixin, goal_read_runtime_root
from loopx.chat_server import ChatRequestHandler
from loopx.chat.todo_detail import goal_read_runtime_root as todo_runtime_root

assert issubclass(ChatRequestHandler, ChatStatusRequestMixin)
assert todo_runtime_root is goal_read_runtime_root
assert pickle.loads(b'cloopx.chat_status_api\\nChatStatusRequestMixin\\n.') is ChatStatusRequestMixin
assert pickle.loads(b'cloopx.chat_status_api\\ngoal_read_runtime_root\\n.') is goal_read_runtime_root

# Reload replaces definitions, but already-imported functions keep live globals.
assert importlib.reload(public) is public
assert importlib.import_module('loopx.chat_status_api') is public
public.load_project_registry = lambda _: {'common_runtime_root': 'source-owned'}
server = SimpleNamespace(registry_path=None, runtime_root_override='chat-decoy')
assert goal_read_runtime_root(server) is None
assert todo_runtime_root(server) is None
"""
    result = subprocess.run(
        [sys.executable, "-c", code, first_import],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("source_bound,controller_root,expected", [
    (True, "coordinator-decoy", None),
    (False, "controller-runtime", "controller-runtime"),
    (False, None, "chat-fallback"),
])
def test_historical_registry_patch_reaches_already_imported_todo_consumer(
    monkeypatch, tmp_path, source_bound, controller_root, expected,
):
    from loopx.chat.todo_detail import goal_read_runtime_root

    registry_path = tmp_path / "registry.json"
    calls = []

    def registry(path):
        calls.append(path)
        return {"common_runtime_root": "source-runtime"} if source_bound else {}

    monkeypatch.setattr("loopx.chat_status_api.load_project_registry", registry)
    server = SimpleNamespace(
        registry_path=registry_path,
        runtime_root_override="chat-fallback",
        runtime_controller=SimpleNamespace(coordination_runtime_root=controller_root),
    )
    assert goal_read_runtime_root(server) == expected
    assert calls == [registry_path]


@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError, ValueError])
def test_historical_discovery_patch_only_tolerates_missing_registry(monkeypatch, failure):
    public = importlib.import_module("loopx.chat_status_api")

    def unreadable(_path):
        raise failure("synthetic unavailable registry")

    monkeypatch.setattr("loopx.chat_status_api.load_project_registry", unreadable)
    server = SimpleNamespace(registry_path=None, runtime_root_override=None)
    if failure is FileNotFoundError:
        assert public.goal_read_runtime_root(server) is None
    else:
        with pytest.raises(failure):
            public.goal_read_runtime_root(server)


def test_historical_status_patch_points_reach_the_real_http_handler(monkeypatch, tmp_path):
    registry = {"goals": [{"id": "alpha"}]}
    registry_reads, collected = [], []

    def load_registry(path):
        registry_reads.append(path)
        return registry

    def collect_status(**kwargs):
        collected.append(kwargs)
        return {"ok": True, "compatibility_probe": "collector"}

    def attach_activity(projection, **_kwargs):
        projection["compatibility_activity"] = True

    def attach_delivery(projection, **_kwargs):
        projection["compatibility_delivery"] = True

    monkeypatch.setattr("loopx.chat_status_api.load_registry", load_registry)
    monkeypatch.setattr("loopx.chat_status_api.load_project_registry", lambda _: {})
    monkeypatch.setattr("loopx.chat_status_api.collect_status", collect_status)
    monkeypatch.setattr("loopx.chat_status_api.attach_host_thread_activity", attach_activity)
    monkeypatch.setattr("loopx.chat_status_api.attach_host_delivery_windows", attach_delivery)
    monkeypatch.setattr("loopx.chat_status_api.codex_thread_observers", lambda **_: [])
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
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request("GET", "/status.json?goal_id=alpha")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200, payload
        assert payload["compatibility_probe"] == "collector"
        assert payload["compatibility_activity"] is True
        assert payload["compatibility_delivery"] is True
        assert len(collected) == 1 and collected[0]["goal_id"] == "alpha"
        assert registry_reads == [server.registry_path, server.registry_path]
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
