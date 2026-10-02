from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest

from loopx.chat import VisibleResponseStreamFilter, parse_agent_response, redact_local_paths
from loopx.chat_status_api import ChatStatusRequestMixin


@pytest.mark.parametrize("root", ["/custom-volume/private-state", "/custom-volume/private state", r"Q:\private state"])
def test_declared_roots_hide_the_entire_descendant_and_preserve_json(root):
    separator = "\\" if root.startswith("Q:") else "/"
    child = root + separator + "private" + separator + "gate.json"
    paths = ["/custom-project", root]
    assert redact_local_paths(f"Stopped at {child}.", protected_paths=paths) == "Stopped at [local-path]."
    assert json.loads(redact_local_paths(json.dumps({"path": child}), protected_paths=paths)) == {"path": "[local-path]"}
    assert parse_agent_response(f"Stopped at {child}.", protected_paths=paths)["message"] == "Stopped at [local-path]."


def test_project_label_requires_a_complete_path_boundary():
    root = "/custom-volume/project"
    assert redact_local_paths(f"{root}/a.txt and {root}.", protected_paths=[root]) == "[project] and [project]."
    unrelated = f"{root}-backup/a.txt relative{root}/a.txt https://example.org{root}/a.txt"
    assert redact_local_paths(unrelated, protected_paths=[root]) == unrelated


def test_canonical_private_path_shapes_and_public_urls():
    assert redact_local_paths("/mnt/private/a.txt") == "[local-path]"
    public = "https://example.org/tmp/file.json ./relative/file.json docs/file.json"
    assert redact_local_paths(public) == public


def test_stream_does_not_split_a_long_custom_path_before_redacting():
    root = "/custom-volume/private-state"
    stream = VisibleResponseStreamFilter(protected_paths=["/custom-project", root])
    path = root + "/" + "s" * 190 + "/gate.json"
    chunks = [stream.feed(path[:170]), stream.feed(path[170:] + "\n"), stream.finish()]
    assert "".join(chunks) == "[local-path]\n"


def test_status_http_hides_the_selected_custom_runtime_root(monkeypatch):
    root = Path("/custom-volume/private-state")
    monkeypatch.setattr("loopx.chat_status_api.collect_status", lambda **kwargs: {
        "ok": True, "runtime_root": str(root), "diagnostic": str(root / "private/gate.json"),
    })
    monkeypatch.setattr("loopx.chat_status_api.attach_host_thread_activity", lambda *args, **kwargs: None)

    class Handler(ChatStatusRequestMixin, BaseHTTPRequestHandler):
        def do_GET(self):
            self._status()

        def _send_json(self, payload, *, status=200):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def _send_error(self, message, **kwargs):
            self._send_json({"error": message}, status=kwargs.get("status", 500))

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.selected_goal_id = None
    server.registry_path = Path("/custom-project/registry.json")
    server.scan_roots = [Path("/custom-project")]
    server.runtime_root = root
    server.runtime_root_override = root
    server.limit = 80
    server.goal_subagent_configuration_enabled = False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/status.json", timeout=5) as response:
            assert json.load(response) == {"ok": True, "runtime_root": "[local-path]", "diagnostic": "[local-path]"}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
