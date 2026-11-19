"""Direct smoke/CLI entrypoints stay silent after inherited opt-outs are lost."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from loopx import usage_ping

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def local_collector():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/ping", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def rebuilt_environment(home: Path, endpoint: str) -> dict[str, str]:
    # Model an allowlisted subprocess environment: no pytest, canary, CI or
    # inherited telemetry flag. Every recipient is strictly numeric loopback.
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "PATHEXT",
        "LOCALAPPDATA", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    return {**env, "HOME": str(home), "USERPROFILE": str(home),
            "LOOPX_USAGE_PING_ENDPOINT": endpoint}


def cli(env: dict[str, str], *args: str) -> dict:
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *args],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=20, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("entrypoint", ["direct_cli", "standalone_smoke_helper"])
def test_unmarked_temporary_home_does_not_create_an_id_or_contact_any_channel(tmp_path, entrypoint):
    home = tmp_path / "home"
    home.mkdir()
    with local_collector() as (endpoint, requests):
        env = rebuilt_environment(home, endpoint)
        if entrypoint == "direct_cli":
            for _ in range(4):
                assert cli(env, "version")["ok"]
        else:
            # The same standalone helper that previously inherited collection
            # must force it off, including a caller's explicit enable value.
            script = """
import json,runpy
helper = runpy.run_path('examples/project/connect-route-collision-guard-smoke.py')['run_cli']
for _ in range(4):
    result = helper(None, 'version', env={'LOOPX_USAGE_PING':'1'})
    assert json.loads(result.stdout)['ok']
"""
            subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                           text=True, capture_output=True, timeout=60, check=True)
        status = cli(env, "usage-ping", "status")
        assert status["blocked_by"] == "temporary_path"
        assert not status["automatic_notice_required"]
        assert not (home / ".loopx" / "usage-ping.json").exists()
        assert not (home / ".codex" / "loopx" / "usage-ping.json").exists()
        assert requests == []


def test_rebuilt_sender_environment_cannot_send_any_previously_enabled_channel(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with local_collector() as (endpoint, requests):
        env = rebuilt_environment(home, endpoint)
        status = cli(env, "usage-ping", "enable")
        assert status["consent"] == "enabled" and status["blocked_by"] == "temporary_path"
        path = next(home.rglob("usage-ping.json"))
        before = path.read_bytes()
        generation = json.loads(before)["generation"]
        facts = {"version": "1.2.3", "python": "3.11", "channel": "source"}
        now = time.time_ns() // 1_000_000
        observations = [
            {"action": "start"},
            {"action": "observe", "feature": "version", "elapsed_ms": 1, "outcome": "ok", "error": "none"},
            {"action": "observe", "feature": "version", "elapsed_ms": 1, "exit_code": 0},
            {"action": "goal", "observation": {"key": "a" * 64, "start": now - 60000,
                "end": now, "measurement": "host_call", "host": "unknown"}},
            {"action": "cycle", "observation": {"key": "b" * 64, "lane": "c" * 64,
                "turn": None, "phase": "start", "at": now, "host": "unknown"}},
        ]
        for observation in observations:
            result = subprocess.run(usage_ping._command(), input=json.dumps({"path": str(path), "facts": facts,
                "generation": generation, **observation}), cwd=ROOT, env=env,
                text=True, capture_output=True, timeout=15, check=True)
            assert json.loads(result.stdout) == {"sent": False, "reason": "blocked"}
        assert path.read_bytes() == before
        assert not Path(str(path) + ".installation").exists()
        assert requests == []


def test_real_chat_settings_read_back_temporary_suppression_and_explicit_disable(tmp_path, monkeypatch):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    monkeypatch.setattr(usage_ping, "select_default_runtime_root", lambda: tmp_path)
    for key in ("CI", "DO_NOT_TRACK", "LOOPX_USAGE_PING"):
        monkeypatch.delenv(key, raising=False)
    with local_collector() as (endpoint, requests):
        monkeypatch.setenv("LOOPX_USAGE_PING_ENDPOINT", endpoint)
        server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
        server.verbose = False
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=8)
        route = "/api/chat/usage-statistics"
        def request(method, body=None):
            connection.request(method, route, json.dumps(body) if body else None,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 200
            return json.loads(response.read())
        try:
            initial = request("GET")
            assert initial["blocked_by"] == "temporary_path" and not initial["automatic_notice_required"]
            request("POST", {"notice": initial["notice"]})
            assert not usage_ping.state_path().exists()
            enabled = request("POST", {"enabled": True})
            assert enabled["consent"] == "enabled" and not enabled["sending"]
            assert enabled["blocked_by"] == "temporary_path"
            disabled = request("POST", {"enabled": False})
            assert disabled["blocked_by"] == "disabled" and disabled["next_payload"] is None
            assert request("GET")["consent"] == "disabled"
            assert requests == []
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()
