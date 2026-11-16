"""Real loopback admission/drain and real detached provider replacement."""
import importlib
from http.client import HTTPResponse
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    native = importlib.import_module("loopx_codex_provider_routing.native")
    owner = native.NativeAccounts(tmp_path)
    with owner._lock():
        state = owner._load()
        state["policy"].update(enabled=True, api_fallback=True)
        state["native"] = {"active_slot": "openai-api"}
        native.atomic(owner.path, state)
    owner.api_keys().add(expected_revision=0, label="Test", api_key="sk-" + "synthetic-key-" * 4)
    return owner


def request(server, path, token, *, body=None, instance=None):
    headers = {"Authorization": "Bearer " + token}
    if instance:
        headers["X-LoopX-Instance"] = instance
    call = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=body, headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_authenticated_drain_preserves_admitted_stream_and_fences_new_work(owner):
    module = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    admitted, release = threading.Event(), threading.Event()
    stream = b'data: {"type":"response.completed"}\n\n'
    calls = []
    class Response(io.BytesIO):
        status, headers = 200, {"Content-Type": "text/event-stream"}
        def read1(self, size):
            admitted.set()
            assert release.wait(5)
            return self.read(size)
    class Upstream:
        def open(self, call, timeout):
            calls.append(call)
            return Response(stream)
    server = module.RelayServer(owner, {"port": 0, "token": "worker", "control_token": "control"}, opener=Upstream())
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    body = json.dumps({"model": "selected-model", "stream": True}).encode()
    result = []
    client = threading.Thread(target=lambda: result.append(request(server, "/v1/responses", "worker", body=body)))
    try:
        assert request(server, "/health", "worker") == (200, b'{"ok":true}')
        assert request(server, "/runtime", "wrong")[0] == 401
        client.start()
        assert admitted.wait(5)
        live = json.loads(request(server, "/runtime", "worker")[1])
        assert live["active_requests"] == 1 and live["accepting"] is True
        assert request(server, "/control/drain", "worker", body=b"", instance=live["instance_id"])[0] == 401
        assert request(server, "/control/drain", "control", body=b"", instance="wrong")[0] == 409
        # Use a slow shutdown hook to independently observe the admission fence.
        shutdown, let_shutdown = server.shutdown, threading.Event()
        server.shutdown = lambda: (let_shutdown.wait(5), shutdown())
        assert request(server, "/control/drain", "control", body=b"", instance=live["instance_id"])[0] == 202
        assert request(server, "/v1/responses", "worker", body=body)[0] == 503
        release.set()
        client.join(5)
        assert result == [(200, stream)] and len(calls) == 1
        let_shutdown.set()
    finally:
        release.set()
        if 'let_shutdown' in locals():
            let_shutdown.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        client.join(5)


def _send_partial_request(server, request):
    connection = socket.create_connection(("127.0.0.1", server.server_port), timeout=3)
    connection.sendall(request)
    return connection


@pytest.mark.parametrize("framing", ["short", "ambiguous", "transfer_encoded"])
def test_invalid_model_request_framing_never_reaches_upstream(owner, framing):
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    body = b'{"model":"selected-model","input":"fixture"}'
    calls = []

    class Upstream:
        def open(self, call, timeout):
            calls.append(call.data)
            response = io.BytesIO(b'{"ok":true}')
            response.status, response.headers = 200, {"Content-Type": "application/json"}
            return response

    length = len(body) + 20 if framing == "short" else len(body)
    extra_header = {
        "short": b"",
        "ambiguous": b"Content-Length: 1\r\n",
        "transfer_encoded": b"Transfer-Encoding: chunked\r\n",
    }[framing]
    headers = (b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer worker\r\n"
               + f"Content-Length: {length}\r\n".encode() + extra_header + b"\r\n")
    server = relay.RelayServer(owner, {"port": 0, "token": "worker"}, opener=Upstream())
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with _send_partial_request(server, headers + body) as client, HTTPResponse(client) as result:
            client.shutdown(socket.SHUT_WR)
            result.begin()
            assert result.status == 400
            assert json.loads(result.read())["error"]["code"] == "invalid_request"
        assert calls == []
        assert request(server, "/v1/responses", "worker", body=body) == (200, b'{"ok":true}')
        assert calls == [body]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_drain_releases_stalled_request_headers_and_body_without_waiting_for_clients(owner, monkeypatch):
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    accepted_headers, accepted_body = threading.Event(), threading.Event()
    original_parse, original_admitted = relay.RelayHandler.parse_request, relay.RelayHandler._admitted

    def parse_request(handler):
        if handler.raw_requestline.startswith(b"GET /half"):
            accepted_headers.set()
        return original_parse(handler)

    def admitted(handler):
        if handler.path == "/v1/responses":
            accepted_body.set()
        return original_admitted(handler)

    monkeypatch.setattr(relay.RelayHandler, "parse_request", parse_request)
    monkeypatch.setattr(relay.RelayHandler, "_admitted", admitted)
    calls = []

    class Upstream:
        def open(self, call, timeout):
            calls.append(call.data)
            raise AssertionError("Draining incomplete request reached the upstream")

    server = relay.RelayServer(owner, {"port": 0, "token": "worker", "control_token": "control"}, opener=Upstream())
    thread = threading.Thread(target=server.serve_forever)
    closed = threading.Event()
    def close_after_drain():
        thread.join()
        server.server_close()
        closed.set()

    closer = threading.Thread(target=close_after_drain)
    thread.start()
    closer.start()
    try:
        with (
            _send_partial_request(server, b"GET /half HTTP/1.1\r\nHost: lo"),
            _send_partial_request(server, b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
                                  b"Authorization: Bearer worker\r\nContent-Length: 40\r\n\r\n{"),
        ):
            assert accepted_headers.wait(3) and accepted_body.wait(3)
            live = json.loads(request(server, "/runtime", "worker")[1])
            assert request(server, "/control/drain", "control", body=b"", instance=live["instance_id"])[0] == 202
            assert closed.wait(3)
            assert server.runtime()["active_requests"] == 0
            assert live["active_requests"] == 0 and calls == []
    finally:
        server.shutdown()
        closer.join(5)


def test_idle_body_read_closes_before_model_admission(owner, monkeypatch):
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    monkeypatch.setattr(relay.RelayHandler, "timeout", 0.2)
    calls = []

    class Upstream:
        def open(self, call, timeout):
            calls.append(call.data)
            raise AssertionError("Incomplete request reached the upstream")

    server = relay.RelayServer(owner, {"port": 0, "token": "worker"}, opener=Upstream())
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        incomplete = (b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer worker\r\n"
                      b"Content-Length: 40\r\n\r\n{")
        with _send_partial_request(server, incomplete) as client:
            assert client.recv(1) == b""
        assert calls == [] and server.runtime()["active_requests"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_real_detached_old_build_replaced_once_with_same_routes_and_private_state(owner, monkeypatch):
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    life = importlib.import_module("loopx_codex_provider_routing.relay_lifecycle")
    native = importlib.import_module("loopx_codex_provider_routing.native")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    value = {"schema_version": relay.SCHEMA, "port": port,
             "token": "worker-" + "x" * 32, "steward_token": "steward-" + "y" * 32,
             "control_token": "control-" + "z" * 32}
    native.atomic(owner.root / "api-relay.json", value)
    monkeypatch.setenv("PYTHONPATH", str(SRC))
    old = subprocess.Popen([sys.executable, "-c",
        "import loopx_codex_provider_routing.openai_relay as r; r.BUILD='0'*64; raise SystemExit(r.main())",
        "--runtime-root", str(owner.root.parents[1])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and life.runtime(value) is None:
            time.sleep(.05)
        assert life.status(owner)["state"] == "upgrade_required"
        before = (owner.root / "api-relay.json").read_bytes(), owner.api_keys().path.read_bytes(), owner.path.read_bytes()
        with owner._lock(name="api-relay-state.lock"):
            life.reconcile(owner, value, consumer="steward")
        current = life.status(owner)
        assert current["ready"] and current["runtime"]["pid"] != old.pid
        old.wait(5)
        with owner._lock(name="api-relay-state.lock"):
            life.reconcile(owner, value)
        assert life.status(owner)["runtime"]["pid"] == current["runtime"]["pid"]
        assert before == ((owner.root / "api-relay.json").read_bytes(), owner.api_keys().path.read_bytes(), owner.path.read_bytes())
        assert life.status(owner, consumer="steward")["ready"]
        # The same build must also adopt a newly issued consumer credential;
        # a worker health response cannot certify the steward route.
        value["steward_token"] = "replacement-steward-" + "q" * 32
        native.atomic(owner.root / "api-relay.json", value)
        assert not life.status(owner, consumer="steward")["ready"]
        staged_route = (owner.root / "api-relay.json").read_bytes()
        result = subprocess.run(
            [sys.executable, "-m", "loopx_codex_provider_routing.provider",
             "--runtime-root", str(owner.root.parents[1])],
            input=json.dumps({"schema_version": "loopx_native_account_provider_v1",
                              "operation": "reconcile", "execute": True}),
            capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stdout
        outcome = json.loads(result.stdout)
        assert outcome["effect_performed"] and outcome["same_relay_instance"]
        assert outcome["consumer_readiness"]["steward"]["ready"]
        assert life.status(owner)["runtime"]["pid"] != current["runtime"]["pid"]
        assert (owner.root / "api-relay.json").read_bytes() == staged_route
    finally:
        live = life.runtime(value)
        if live:
            life._drain(value, live)
        if old.poll() is None:
            old.terminate()  # only this synthetic fixture child, never a real consumer
            old.wait(5)
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline and not life._owner_released(owner):
            time.sleep(.05)
        assert life._owner_released(owner)


@pytest.mark.parametrize("identity", [None, (1, "/other/python", 1, 1)])
def test_legacy_unknown_owner_never_signalled(owner, monkeypatch, identity):
    life = importlib.import_module("loopx_codex_provider_routing.relay_lifecycle")
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    monkeypatch.setattr(life, "runtime", lambda *a, **k: None)
    monkeypatch.setattr(relay, "healthy", lambda *a, **k: True)
    monkeypatch.setattr(life, "process_identity", lambda *_: identity)
    monkeypatch.setattr(life.os, "kill", lambda *_: pytest.fail("Unknown PID cannot be signalled"))
    monkeypatch.setattr(life.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "/other -m wrong", ""))
    with pytest.raises(Exception, match="api_relay_owner_unverified"):
        life.reconcile(owner, {"port": 5000}, legacy_pid=12345)


def test_legacy_requires_explicit_migration_and_pending_drain_never_spawns(owner, monkeypatch):
    life = importlib.import_module("loopx_codex_provider_routing.relay_lifecycle")
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    monkeypatch.setattr(life, "runtime", lambda *a, **k: None)
    monkeypatch.setattr(relay, "healthy", lambda *a, **k: True)
    monkeypatch.setattr(life.subprocess, "Popen", lambda *a, **k: pytest.fail("No parallel daemon"))
    with pytest.raises(Exception, match="api_relay_legacy_migration_required"):
        life.reconcile(owner, {"port": 5000})
    monkeypatch.setattr(life, "runtime", lambda *a, **k: {"build": "old", "accepting": False})
    monkeypatch.setattr(life, "_drain", lambda *_: None)
    monkeypatch.setattr(life, "_owner_released", lambda *_: False)
    with pytest.raises(Exception, match="api_relay_drain_pending"):
        life.reconcile(owner, {"port": 5000}, timeout=0)


def test_provider_doctor_checks_real_dependencies_and_does_not_start_unconfigured_relay(owner, monkeypatch):
    provider = importlib.import_module("loopx_codex_provider_routing.provider")
    before = list(owner.root.iterdir())
    result = provider.doctor(owner)
    assert result["installation_ready"] and result["relay"]["state"] == "not_started"
    assert result["adapter_readiness"] == {"state": "not_required", "ready": True}
    assert result["consumer_readiness"]["steward"]["state"] == "not_configured"
    assert result["same_relay_instance"] is None
    assert result["message_delivery_verified"] is False and list(owner.root.iterdir()) == before
    monkeypatch.setenv("PATH", "")
    with pytest.raises(Exception, match="node_unavailable"):
        provider.doctor(owner)


def test_provider_doctor_rejects_stale_native_adapter_with_healthy_relay(owner, tmp_path, monkeypatch):
    native = importlib.import_module("loopx_codex_provider_routing.native")
    host = importlib.import_module("loopx_codex_provider_routing.native_host")
    life = importlib.import_module("loopx_codex_provider_routing.relay_lifecycle")
    provider = importlib.import_module("loopx_codex_provider_routing.provider")
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("")
    fake_codex.chmod(0o700)
    route = {"home": tmp_path / "codex-home", "frontend": tmp_path / "frontend"}
    monkeypatch.setattr(host.native_app, "route", lambda *_: route)
    original_which = host.shutil.which
    monkeypatch.setattr(host.shutil, "which", lambda name: str(fake_codex) if name == "codex" else original_which(name))
    current = host.discover()
    stale = {**current, "pins": {**current["pins"], "native_account_store.py": "0" * 64}}
    state = owner._load()
    native.atomic(owner.path, {**state, "adapter": stale})
    before = owner.path.read_bytes()
    runtime = {"instance_id": "relay", "pid": 1, "build": "current"}
    monkeypatch.setattr(life, "consumer_statuses", lambda _: {
        name: {"state": "ready", "ready": True, "runtime": runtime} for name in ("worker", "steward")
    })
    monkeypatch.setattr(host.NativeHost, "status", lambda *_: pytest.fail("Probe must use adapter checks only"))

    result = provider.doctor(owner)
    assert result["same_relay_instance"] is True
    assert result["installation_ready"] is False and result["ok"] is False
    assert result["adapter_readiness"] == {"state": "adapter_changed", "ready": False}
    assert owner.path.read_bytes() == before

    native.atomic(owner.path, {**state, "adapter": current})
    result = provider.doctor(owner)
    assert result["adapter_readiness"] == {"state": "ready", "ready": True}
    assert result["installation_ready"] and result["ok"]


def test_installed_doctor_separates_steward_readiness_from_worker_health(owner, monkeypatch):
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    native = importlib.import_module("loopx_codex_provider_routing.native")
    worker_token, steward_token = "worker-" + "w" * 32, "steward-" + "s" * 32
    monkeypatch.setenv("PYTHONPATH", str(SRC))
    server = relay.RelayServer(owner, {"port": 0, "token": worker_token, "steward_token": steward_token})
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    route_file = owner.root / "api-relay.json"
    base = {"schema_version": relay.SCHEMA, "port": server.server_port, "token": worker_token}
    def doctor(credentials):
        native.atomic(route_file, {**base, **credentials})
        before = route_file.read_bytes()
        result = subprocess.run(
            [sys.executable, "-m", "loopx_codex_provider_routing.provider",
             "--runtime-root", str(owner.root.parents[1]), "--doctor"],
            capture_output=True, text=True, timeout=8)
        assert route_file.read_bytes() == before
        assert worker_token not in result.stdout and steward_token not in result.stdout
        return result.returncode, json.loads(result.stdout)
    try:
        code, both = doctor({"steward_token": steward_token})
        assert code == 0
        code, stale = doctor({"steward_token": "stale-steward-" + "x" * 32})
        # Extension installation uses the process status, not the JSON output.
        assert code == 1
        assert stale["installation_ready"] is False
        assert stale["consumer_readiness"]["worker"]["ready"] is True
        assert stale["consumer_readiness"]["steward"]["state"] == "unavailable"
        assert stale["same_relay_instance"] is None
        assert request(server, "/health", worker_token)[0] == 200
        assert both["same_relay_instance"] is True
        assert all(row["ready"] for row in both["consumer_readiness"].values())
        assert both["relay"] == both["consumer_readiness"]["worker"]
        assert both["message_delivery_verified"] is False
        for invalid in (worker_token, "invalid\r\nheader"):
            code, unusable = doctor({"steward_token": invalid})
            assert code == 1
            assert unusable["consumer_readiness"]["steward"]["state"] == "unavailable"
            assert unusable["consumer_readiness"]["worker"]["ready"]
        code, optional = doctor({})
        assert code == 0 and optional["consumer_readiness"]["steward"]["state"] == "not_configured"
        with server._admission_lock:
            server.draining = True
        code, draining = doctor({"steward_token": steward_token})
        assert code == 1
        assert {v["state"] for v in draining["consumer_readiness"].values()} == {"draining"}
        assert draining["same_relay_instance"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("saved_policy", [
    {},
    {"policy": None},
    {"policy": []},
    {"policy": {"enabled": "true"}},
], ids=["missing", "null", "list", "invalid-enabled"])
@pytest.mark.parametrize("operation", ["doctor", "status"])
def test_provider_readiness_reports_invalid_saved_policy(owner, tmp_path, monkeypatch, saved_policy, operation):
    native = importlib.import_module("loopx_codex_provider_routing.native")
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    provider = importlib.import_module("loopx_codex_provider_routing.provider")
    probe = native.NativeAccounts(tmp_path / "probe")
    probe.root.mkdir(mode=0o700, parents=True)
    native.atomic(probe.path, {"schema_version": native.SCHEMA, "adapter": {}, **saved_policy})
    before = probe.path.read_bytes()
    credentials = {"token": "worker-" + "w" * 32, "steward_token": "steward-" + "s" * 32}
    server = relay.RelayServer(owner, {"port": 0, **credentials})
    native.atomic(probe.root / "api-relay.json", {
        "schema_version": relay.SCHEMA, "port": server.server_port, **credentials,
    })
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setenv("PYTHONPATH", str(SRC))
    command = [sys.executable, "-m", "loopx_codex_provider_routing.provider",
               "--runtime-root", str(tmp_path / "probe")]
    payload = None
    if operation == "doctor":
        command.append("--doctor")
    else:
        payload = json.dumps({"schema_version": provider.SCHEMA, "operation": "status"})
    try:
        result = subprocess.run(command, input=payload, capture_output=True, text=True, timeout=8)
        assert result.returncode == 1 and not result.stderr, result.stderr
        status = json.loads(result.stdout)
        assert status["ok"] is False and status["installation_ready"] is False
        assert status["adapter_readiness"] == {"state": "state_untrusted", "ready": False}
        assert status["same_relay_instance"] is True
        assert all(observation["ready"] for observation in status["consumer_readiness"].values())
        assert probe.path.read_bytes() == before
        assert all(token not in result.stdout for token in credentials.values())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("invalid", ["shared", "", "invalid\r\nheader"])
def test_reconcile_refuses_invalid_steward_credential_before_drain(owner, monkeypatch, invalid):
    life = importlib.import_module("loopx_codex_provider_routing.relay_lifecycle")
    monkeypatch.setattr(life, "runtime", lambda *_: pytest.fail("No relay may be drained"))
    with pytest.raises(life.HostError, match="api_relay_upgrade_required"):
        life.reconcile(owner, {"token": "shared", "steward_token": invalid}, consumer="steward")


def test_installed_doctor_does_not_combine_different_relay_instances(owner, monkeypatch):
    provider = importlib.import_module("loopx_codex_provider_routing.provider")
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    life = importlib.import_module("loopx_codex_provider_routing.relay_lifecycle")
    monkeypatch.setattr(relay, "route", lambda _: {"steward_token": "configured"})
    monkeypatch.setattr(life, "runtime", lambda _value, *, consumer: {
        "build": relay.BUILD, "accepting": True, "pid": 17,
        "instance_id": ("a" if consumer == "worker" else "b") * 32,
    })
    result = provider.doctor(owner)
    assert all(row["ready"] for row in result["consumer_readiness"].values())
    assert result["same_relay_instance"] is False
    assert result["installation_ready"] is False


def test_existing_healthy_legacy_is_not_silently_adopted(owner):
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    native = importlib.import_module("loopx_codex_provider_routing.native")
    class Legacy(relay.RelayHandler):
        def do_GET(self):
            if self.path.endswith("/runtime"):
                self._json(404, "unsupported_route")
            else:
                super().do_GET()
    server = relay.RelayServer(owner, {"port": 0, "token": "legacy-" + "x" * 32})
    server.RequestHandlerClass = Legacy
    value = {"schema_version": relay.SCHEMA, "port": server.server_port,
             "token": server.value["token"]}
    native.atomic(owner.root / "api-relay.json", value)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        assert relay.healthy(value)
        with pytest.raises(Exception, match="api_relay_legacy_migration_required"):
            relay.ensure(owner)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_legacy_changed_kernel_identity_is_not_signalled(owner, monkeypatch):
    life = importlib.import_module("loopx_codex_provider_routing.relay_lifecycle")
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    monkeypatch.setattr(life, "runtime", lambda *a, **k: None)
    monkeypatch.setattr(relay, "healthy", lambda *a, **k: True)
    identities = iter([(1, "/python", 1, 0), (1, "/python", 2, 0)])
    monkeypatch.setattr(life, "_legacy_identity", lambda *_: next(identities))
    monkeypatch.setattr(life.os, "kill", lambda *_: pytest.fail("Reused PID cannot be signalled"))
    with pytest.raises(Exception, match="api_relay_owner_changed"):
        life.reconcile(owner, {"port": 5000}, legacy_pid=12345)
