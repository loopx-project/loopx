"""Invocation ownership across real HTTP waits and canonical Goal decisions.

Only transport scheduling is instrumented. Admission, registry locks, File /
SQLite stores, process liveness and provider bytes use their actual owners.
These synthetic providers do not qualify any external model or benchmark score.
"""
from __future__ import annotations

from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from benchmark.runtime.provider_request_admission import (
    ProviderRequestAdmission,
    request_admission_enabled,
)
from loopx.capabilities.benchmark_toolkit import provider_gateway
from loopx.control_plane.goals import activation_service, first_party_host_admission
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.projects.registry_codec import project_registry_transaction
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane import test_quota_settlement_cli as quota_fixture


@dataclass
class _Authority:
    project: Path
    runtime: Path
    registry: Path
    root: Path
    api_key: str = "fixture-provider-key"

    def invocation(self, upstream, name, *, timeout=None, receipt=None, wake=None, turn_id=None):
        if wake is None:
            wake = self.root / name
            wake.mkdir()
        env = {
            "LOOPX_PROJECT": str(self.project),
            "LOOPX_RUNTIME_ROOT": str(self.runtime),
            "LOOPX_GOAL_ID": quota_fixture.GOAL_ID,
            "LOOPX_AGENT_ID": quota_fixture.AGENT_ID,
            "LOOPX_TURN": turn_id or name,
            "OPENAI_BASE_URL": upstream,
            "OPENAI_API_KEY": self.api_key,
        }
        session = SimpleNamespace(
            admission=FirstPartyHostGoalAdmission.for_plan(
                registry_path=self.registry, goal_id=quota_fixture.GOAL_ID,
                planned_goal_ref=None,
            ),
            goal_ref=None, session_id=f"session-{name}",
        )
        if receipt is None:
            receipt = {}
        guard = ProviderRequestAdmission(env, session, receipt, wake, timeout=timeout)
        return guard, receipt, wake

    def stop(self):
        result = activation_service.set_goal_activation_state(
            registry_path=self.registry, goal_id=quota_fixture.GOAL_ID,
            state="stopped", reason="Stop this synthetic provider invocation.",
            runtime_root_override=str(self.runtime), actor_kind="owner", execute=True,
        )
        assert result["written"], result
        return result


@pytest.fixture(params=["file", "sqlite"])
def authority(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project, runtime, registry = quota_fixture._write_fixture(
        tmp_path, provider=request.param,
    )
    code, current = quota_fixture._run_cli(
        registry, runtime, "quota", "should-run", "--goal-id", quota_fixture.GOAL_ID,
        "--agent-id", quota_fixture.AGENT_ID, "--runtime-profile", "generic_cli",
    )
    assert code == 0 and current["should_run"] is True, current
    return _Authority(project, runtime, registry, tmp_path)


@contextmanager
def _process():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _async(function):
    result = Future()

    def run():
        try:
            result.set_result(function())
        except BaseException as error:
            result.set_exception(error)

    threading.Thread(target=run, daemon=True).start()
    return result


@contextmanager
def _upstream(*, hold_body=False, hold_response=False):
    state = SimpleNamespace(
        rows=[], headers=threading.Event(), body=threading.Event(),
        read_body=threading.Event(), respond=threading.Event(),
    )
    if not hold_body:
        state.read_body.set()
    if not hold_response:
        state.respond.set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def setup(self):
            # Small receive windows plus a multi-megabyte body establish real
            # backpressure; a 128 KiB write can fit entirely in kernel buffers.
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024)
            self.request.settimeout(5)
            super().setup()

        def log_message(self, _format, *args):
            pass

        def do_POST(self):
            row = {"path": self.path, "authorization": self.headers.get("Authorization"),
                   "length": int(self.headers["Content-Length"])}
            state.rows.append(row)
            state.headers.set()
            try:
                assert state.read_body.wait(12), "test did not release the upstream body reader"
                row["body"] = self.rfile.read(row["length"])
                state.body.set()
                assert state.respond.wait(12), "test did not release the upstream response"
                body = b'{"id":"synthetic-response"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (OSError, AssertionError) as error:
                row["transport_error"] = type(error).__name__
                state.body.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        state.read_body.set()
        state.respond.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(base_url, body=b"{}", *, path="/responses"):
    parsed = urlsplit(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=8)
    try:
        connection.request("POST", parsed.path + path, body=body,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _finished(wake, *, count=1, invocation_id=None):
    # A framed HTTP error body can reach the caller before the handler's
    # finally block. Await the observation itself, not an incidental socket EOF.
    deadline = time.monotonic() + 3
    while True:
        path = wake / "provider-requests.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        finished = [row for row in rows if row.get("event") == "finished"
                    and (invocation_id is None or row["invocation_id"] == invocation_id)]
        if len(finished) >= count:
            return finished
        assert time.monotonic() < deadline, "request did not publish its finished observation"
        threading.Event().wait(0.01)


@pytest.mark.parametrize("decision", ["allowed", "stop", "quota_wait"])
def test_each_request_rechecks_canonical_authority(authority, decision):
    with _upstream() as upstream, _process() as process:
        guard, receipt, wake = authority.invocation(upstream.base_url, "live")
        with guard.serve(["native", "-"]):
            guard.started(process)
            assert _post(guard.base_url)[0] == 200
            if decision == "stop":
                authority.stop()
            elif decision == "quota_wait":
                source_before = authority.registry.read_bytes()
                code, spent = quota_fixture._run_cli(
                    authority.registry, authority.runtime, "quota", "spend-slot",
                    "--goal-id", quota_fixture.GOAL_ID, "--agent-id", quota_fixture.AGENT_ID,
                    "--source", "controller", "--slots", "2", "--execute",
                )
                assert code == 0 and spent["appended"], spent
                assert spent["before"]["should_run"] is True
                assert spent["after"]["should_run"] is False
                # Exercise the live quota reader, not registry witness mismatch.
                assert authority.registry.read_bytes() == source_before
            status, payload = _post(guard.base_url)
            if decision == "allowed":
                assert status == 200 and process.poll() is None
                assert len(upstream.rows) == 2
            else:
                assert status == 409, payload
                assert payload["error"]["code"] == (
                    "goal_stopped" if decision == "stop" else "goal_launch_not_allowed"
                )
                process.wait(timeout=5)
                assert len(upstream.rows) == 1, "a refused second request reached the provider"
            assert all(row["body"] == b"{}" for row in upstream.rows)
            assert all(row["authorization"] == f"Bearer {authority.api_key}" for row in upstream.rows)
            assert len(_finished(wake, count=2)) == 2
            summary = receipt["provider_request_invocations"][0]
            assert summary["requests"] == 2 and summary["sent"] == len(upstream.rows)


@pytest.mark.parametrize("expired", ["process", "deadline"])
def test_registry_wait_rechecks_original_process_and_deadline(authority, monkeypatch, expired):
    waiting = threading.Event()
    original = first_party_host_admission.project_registry_transaction

    @contextmanager
    def observed_transaction(*args, **kwargs):
        if kwargs.get("operation") == "provider_request_dispatch":
            waiting.set()
        with original(*args, **kwargs) as transaction:
            yield transaction

    monkeypatch.setattr(first_party_host_admission, "project_registry_transaction", observed_transaction)
    with _upstream() as upstream, _process() as process:
        guard, _receipt, wake = authority.invocation(
            upstream.base_url, "contended", timeout=1.5 if expired == "deadline" else None,
        )
        with guard.serve(["native", "-"]):
            guard.started(process)
            # This is the real writer lock, not a substituted admission result.
            with project_registry_transaction(authority.registry, operation="test_writer_holds_registry"):
                request = _async(lambda: _post(guard.base_url))
                assert waiting.wait(3), "request did not reach the held registry lock"
                assert not request.done() and not upstream.rows
                if expired == "process":
                    process.terminate()
                    process.wait(timeout=5)
                else:
                    # A real deadline elapses while the actual authority lock is
                    # unavailable. No clock or live-owner predicate is replaced.
                    threading.Event().wait(1.6)
            status, payload = request.result(timeout=6)
            assert status == 409, payload
            assert payload["error"]["code"] == (
                "provider_request_without_live_owner" if expired == "process"
                else "provider_request_budget_exhausted"
            )
            assert not upstream.rows and _finished(wake)[0]["forwarded"] is False


def test_process_attachment_is_one_shot(authority):
    with _upstream() as upstream, _process() as old, _process() as replacement:
        guard, _receipt, _wake = authority.invocation(upstream.base_url, "one-shot")
        guard.started(old)
        with pytest.raises(ValueError, match="cannot be rebound"):
            guard.started(replacement)
        guard.retire()
        with pytest.raises(ValueError, match="cannot be rebound"):
            guard.started(replacement)
        assert replacement.poll() is None


@pytest.mark.parametrize("stage,shared_receipt", [
    ("backlog", False), ("accepted", False), ("headers", False), ("body", False),
    ("upstream_connect", False), ("upstream_connect", True),
])
def test_delayed_old_request_cannot_adopt_replacement(authority, monkeypatch, stage, shared_receipt):
    waiting, release = threading.Event(), threading.Event()
    old_port = [None]
    real_accept = provider_gateway.ThreadingHTTPServer.get_request
    real_connect = http.client.HTTPConnection.connect
    connect_claimed = threading.Event()
    upstream_port = [None]

    def observed_accept(server):
        if server.server_port == old_port[0] and stage == "backlog":
            waiting.set()
            assert release.wait(10), "test did not release the actual listen backlog"
        result = real_accept(server)
        if server.server_port == old_port[0] and stage == "accepted":
            waiting.set()
            assert release.wait(10), "test did not release the accepted old socket"
        if server.server_port == old_port[0] and stage in {"headers", "body"}:
            waiting.set()
        return result

    def observed_connect(connection):
        real_connect(connection)
        if (stage == "upstream_connect" and connection.port == upstream_port[0]
                and not connect_claimed.is_set()):
            connect_claimed.set()
            waiting.set()
            assert release.wait(10), "test did not release upstream connection preparation"

    monkeypatch.setattr(provider_gateway.ThreadingHTTPServer, "get_request", observed_accept)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", observed_connect)
    raw = None
    with _upstream() as upstream, _process() as old, _process() as replacement:
        upstream_port[0] = urlsplit(upstream.base_url).port
        original, original_receipt, original_wake = authority.invocation(upstream.base_url, "old")
        try:
            with original.serve(["native", "-"]):
                original.started(old)
                parsed = urlsplit(original.base_url)
                old_port[0] = parsed.port
                if stage == "upstream_connect":
                    pending = _async(lambda: _post(original.base_url))
                else:
                    raw = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
                    header = (f"POST {parsed.path}/responses HTTP/1.0\r\n"
                              "Host: localhost\r\nContent-Length: 2\r\n").encode()
                    raw.sendall(header + (
                        b"\r\n{}" if stage in {"backlog", "accepted"}
                        else b"\r\n{" if stage == "body" else b""
                    ))
                assert waiting.wait(3), f"old request did not reach {stage}"
                assert not upstream.rows
                original.retire()
                old.terminate()
                old.wait(timeout=5)
                shared = {"receipt": original_receipt, "wake": original_wake, "turn_id": "old"} if shared_receipt else {}
                successor, successor_receipt, successor_wake = authority.invocation(upstream.base_url, "new", **shared)
                with successor.serve(["native", "-"]):
                    successor.started(replacement)
                    assert _post(successor.base_url)[0] == 200
                    release.set()
                    if stage == "headers":
                        raw.sendall(b"\r\n{}")
                    elif stage == "body":
                        raw.sendall(b"}")
                    if raw is not None:
                        response = http.client.HTTPResponse(raw)
                        response.begin()
                        status, payload = response.status, json.loads(response.read())
                    else:
                        status, payload = pending.result(timeout=6)
                    assert status == 409 and payload["error"]["code"] == "provider_request_without_live_owner"
                    assert original.refusal_code is None, "a retired request latched a later invocation refusal"
                    assert successor.refusal_code is None and replacement.poll() is None
                    assert len(upstream.rows) == 1 and upstream.rows[0]["body"] == b"{}"
                    old_summary = original_receipt["provider_request_invocations"][0]
                    new_summary = successor_receipt["provider_request_invocations"][-1]
                    old_event, = _finished(original_wake, invocation_id=old_summary["invocation_id"])
                    new_event, = _finished(successor_wake, invocation_id=new_summary["invocation_id"])
                    assert old_event["turn_id"] == "old" and old_event["forwarded"] is False
                    assert new_event["turn_id"] == ("old" if shared_receipt else "new")
                    assert new_event["forwarded"] is True
                    assert old_event["session_id"] == "session-old" and new_event["session_id"] == "session-new"
                    assert old_event["invocation_id"] != new_event["invocation_id"]
                    assert old_summary["sent"] == 0 and old_summary["refused"] == 1
                    assert new_summary["sent"] == 1 and new_summary["refused"] == 0
                    assert old_summary["requests"] == new_summary["requests"] == 1
                    assert old_summary["pending"] == new_summary["pending"] == 0
                    assert "refusal_code" not in new_summary
                    if shared_receipt:
                        assert original_receipt is successor_receipt and original_wake == successor_wake
                        assert len(successor_receipt["provider_request_invocations"]) == 2
                    # Address the old path to the new listener, as after port reuse.
                    new_address = urlsplit(successor.base_url)
                    assert _post(f"http://{new_address.netloc}{parsed.path}")[0] == 404
                    assert replacement.poll() is None and len(upstream.rows) == 1
        finally:
            release.set()
            if raw is not None:
                raw.close()


def test_real_backpressure_bounds_writer_wait_and_records_unknown_acceptance(authority):
    body = b"x" * (8 * 1024 * 1024)
    with _upstream(hold_body=True) as upstream, _process() as process:
        guard, receipt, wake = authority.invocation(upstream.base_url, "backpressure")
        with guard.serve(["native", "-"]):
            guard.started(process)
            request = _async(lambda: _post(guard.base_url, body))
            assert upstream.headers.wait(5), "provider did not receive the POST headers"
            assert upstream.rows[0]["length"] == len(body)
            stopped = _async(authority.stop)
            try:
                # The provider still has not read its body. A canonical Stop
                # writer must complete once the bounded partial dispatch ends.
                assert stopped.result(timeout=6)["written"]
                assert not upstream.read_body.is_set()
                status, payload = request.result(timeout=3)
                assert status == 502, payload
                event = _finished(wake)[0]
                assert event["forwarded"] is None and "refusal_code" not in event
                assert guard.refusal_code is None and process.poll() is None
                summary = receipt["provider_request_invocations"][0]
                assert summary["unknown"] == 1 and summary["sent"] == 0
                assert summary["refused"] == 0
            finally:
                upstream.read_body.set()
            assert upstream.body.wait(5)
            assert 0 < len(upstream.rows[0]["body"]) < len(body), "fixture did not establish a partial send"
            assert _post(guard.base_url)[0] == 409
            assert len(upstream.rows) == 1, "unknown acceptance must not retry the POST"


def test_slow_response_does_not_hold_goal_writer(authority):
    with _upstream(hold_response=True) as upstream, _process() as process:
        guard, receipt, wake = authority.invocation(upstream.base_url, "slow-response")
        with guard.serve(["native", "-"]):
            guard.started(process)
            request = _async(lambda: _post(guard.base_url))
            assert upstream.body.wait(5)
            stopped = _async(authority.stop)
            try:
                assert stopped.result(timeout=4)["written"]
                assert not upstream.respond.is_set() and not request.done()
            finally:
                upstream.respond.set()
            assert request.result(timeout=3)[0] == 200
            assert _finished(wake)[0]["forwarded"] is True
            assert receipt["provider_request_invocations"][0]["sent"] == 1
            assert _post(guard.base_url)[0] == 409
            assert len(upstream.rows) == 1


@pytest.mark.parametrize("old_flag", [None, "0", "1"])
def test_existing_research_flag_never_enables_request_admission(old_flag):
    env = {} if old_flag is None else {"LOOPX_RESEARCH_LOOP": old_flag}
    assert request_admission_enabled(env, "plain") is False
    assert request_admission_enabled(env, "heartbeat") is False
    assert request_admission_enabled({**env, "LOOPX_PROVIDER_REQUEST_ADMISSION": "off"}, "turn") is False
