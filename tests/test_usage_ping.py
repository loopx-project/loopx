"""Opt-in usage ping: consent, privacy contract, once-per-day delivery."""

from __future__ import annotations

import json
import socket
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from loopx import usage_ping
from loopx.cli_runtime import main as cli_main

DAY1 = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)
DAY2 = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
ENDPOINT = {"LOOPX_USAGE_PING_ENDPOINT": "https://collector.example/v0/ping"}


class _Spawn:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)


def test_undecided_machine_never_schedules_or_creates_state(tmp_path):
    spawn = _Spawn()
    assert usage_ping.maybe_schedule(["status"], env=ENDPOINT, runtime_root=tmp_path, spawn=spawn) is False
    assert spawn.calls == []
    assert not usage_ping.state_path(tmp_path).exists()
    assert usage_ping.status(usage_ping.state_path(tmp_path), env=ENDPOINT)["consent"] == "undecided"


def test_enable_generates_random_id_and_disable_forgets_it(tmp_path):
    path = usage_ping.state_path(tmp_path)
    first = usage_ping.enable(path, now=DAY1)
    assert uuid.UUID(first["install_id"]).version == 4
    assert usage_ping.enable(path, now=DAY1)["install_id"] == first["install_id"]
    usage_ping.disable(path, now=DAY1)
    assert "install_id" not in json.loads(path.read_text())
    assert usage_ping.enable(path, now=DAY1)["install_id"] != first["install_id"]


def test_payload_is_exactly_the_documented_fields_and_leaks_no_machine_identity(tmp_path):
    state = usage_ping.enable(usage_ping.state_path(tmp_path), now=DAY1)
    payload = usage_ping.build_payload(state)
    assert set(payload) == {"schema", "install_id", "version", "os", "python", "channel"}
    assert payload["schema"] == "loopx_usage_ping_v0"
    assert payload["os"] in {"darwin", "linux", "windows", "other"}
    assert payload["channel"] in {"pip", "local_release", "source", "unknown"}
    serialized = json.dumps(payload)
    for secret in (str(Path.home()), socket.gethostname(), str(tmp_path)):
        assert secret not in serialized


@pytest.mark.parametrize(
    "env",
    [
        {"CI": "true"},
        {"DO_NOT_TRACK": "1"},
        {"LOOPX_USAGE_PING": "0"},
        {"LOOPX_USAGE_PING": "off"},
    ],
)
def test_environment_switches_block_sending_even_after_opt_in(tmp_path, env):
    path = usage_ping.state_path(tmp_path)
    usage_ping.enable(path, now=DAY1)
    spawn = _Spawn()
    merged = {**ENDPOINT, **env}
    assert usage_ping.maybe_schedule(["status"], env=merged, runtime_root=tmp_path, spawn=spawn) is False
    assert usage_ping.send(path, env=merged, now=DAY1, post=pytest.fail)["sent"] is False
    assert usage_ping.status(path, env=merged)["sending"] is False
    assert spawn.calls == []


def test_endpoint_must_be_https_or_loopback():
    assert usage_ping.resolve_endpoint({}) == usage_ping.DEFAULT_ENDPOINT
    assert usage_ping.resolve_endpoint({"LOOPX_USAGE_PING_ENDPOINT": "http://collector.example/x"}) == ""
    assert usage_ping.resolve_endpoint({"LOOPX_USAGE_PING_ENDPOINT": "http://127.0.0.1:8787/v0/ping"})
    assert usage_ping.resolve_endpoint(ENDPOINT) == ENDPOINT["LOOPX_USAGE_PING_ENDPOINT"]


def test_without_a_configured_collector_consent_is_recorded_but_nothing_is_scheduled(tmp_path):
    usage_ping.enable(usage_ping.state_path(tmp_path), now=DAY1)
    spawn = _Spawn()
    assert usage_ping.maybe_schedule(["status"], env={}, runtime_root=tmp_path, spawn=spawn) is False
    assert spawn.calls == []


def test_schedule_starts_one_detached_child_per_day(tmp_path):
    usage_ping.enable(usage_ping.state_path(tmp_path), now=DAY1)
    spawn = _Spawn()
    assert usage_ping.maybe_schedule(["status"], env=ENDPOINT, runtime_root=tmp_path, now=DAY1, spawn=spawn)
    assert not usage_ping.maybe_schedule(["todo"], env=ENDPOINT, runtime_root=tmp_path, now=DAY1, spawn=spawn)
    assert not usage_ping.maybe_schedule(["usage-ping"], env=ENDPOINT, runtime_root=tmp_path, now=DAY2, spawn=spawn)
    assert usage_ping.maybe_schedule(["status"], env=ENDPOINT, runtime_root=tmp_path, now=DAY2, spawn=spawn)
    assert len(spawn.calls) == 2
    assert spawn.calls[0][-3:] == ["loopx.usage_ping", "--send", str(tmp_path)]


def test_schedule_swallows_every_failure(tmp_path):
    usage_ping.enable(usage_ping.state_path(tmp_path), now=DAY1)

    def broken_spawn(argv, **kwargs):
        raise OSError("no fork for you")

    assert usage_ping.maybe_schedule(["status"], env=ENDPOINT, runtime_root=tmp_path, now=DAY1, spawn=broken_spawn) is False


def test_send_records_success_once_per_day_and_never_raises(tmp_path):
    path = usage_ping.state_path(tmp_path)
    usage_ping.enable(path, now=DAY1)
    bodies: list[dict] = []

    def post(endpoint, body, timeout):
        bodies.append(json.loads(body))
        return 204

    assert usage_ping.send(path, env=ENDPOINT, now=DAY1, post=post)["sent"] is True
    assert usage_ping.send(path, env=ENDPOINT, now=DAY1, post=post)["reason"] == "already_sent_today"
    assert len(bodies) == 1 and bodies[0]["install_id"] == usage_ping.load_state(path)["install_id"]

    def failing(endpoint, body, timeout):
        raise TimeoutError("collector down")

    result = usage_ping.send(path, env=ENDPOINT, now=DAY2, post=failing)
    assert result == {"sent": False, "reason": "request_failed", "error": "collector down"}
    assert usage_ping.load_state(path)["last_sent_day"] == "2026-09-26"
    assert usage_ping.send(path, env=ENDPOINT, now=DAY2, post=lambda *a: 400)["reason"] == "rejected"


def test_send_round_trips_to_a_loopback_collector(tmp_path):
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received.append({"body": json.loads(self.rfile.read(length)), "ua": self.headers["User-Agent"]})
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = usage_ping.state_path(tmp_path)
        usage_ping.enable(path, now=DAY1)
        env = {"LOOPX_USAGE_PING_ENDPOINT": f"http://127.0.0.1:{server.server_port}/v0/ping"}
        assert usage_ping.send(path, env=env, now=DAY1) == {"sent": True, "status": 204}
    finally:
        server.shutdown()
    assert received[0]["ua"] == "loopx-usage-ping"
    assert received[0]["body"] == usage_ping.build_payload(usage_ping.load_state(path))


def test_cli_enable_status_disable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(usage_ping, "DEFAULT_RUNTIME_ROOT", tmp_path)
    monkeypatch.delenv("LOOPX_USAGE_PING_ENDPOINT", raising=False)
    assert cli_main(["usage-ping", "enable", "--format", "json"]) == 0
    enabled = json.loads(capsys.readouterr().out)
    assert enabled["consent"] == "enabled"
    assert enabled["sending"] is False  # no collector configured in this build
    assert enabled["next_payload"]["install_id"]
    assert cli_main(["usage-ping", "disable"]) == 0
    assert "LoopX usage ping: disabled" in capsys.readouterr().out
