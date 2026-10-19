"""Request dispatch rechecks the original owner after network and lock waits."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

import benchmark.runtime.research_request_guard as guard_module
import loopx.capabilities.benchmark_toolkit.provider_gateway as gateway_module
from benchmark.runtime.research_request_guard import ResearchRequestGuard
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.projects.registry_codec import project_registry_transaction
from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as fixture
from tests.capabilities.test_provider_gateway import _recording_upstream


def _admitted_owner(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    fixture._initialize_fixture_authority(project, runtime, provider=provider)
    turn = "request-race-turn"
    code, initial = fixture._run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
        "--runtime-profile", "generic_cli", "--todo-id", fixture.TODO_ID,
        "--turn-instance-id", turn, "--scan-path", str(project), cwd=project)
    assert code == 0 and initial["should_run"], initial
    session = SimpleNamespace(session_id="native-request-race", goal_ref=None,
        admission=FirstPartyHostGoalAdmission.for_plan(registry_path=registry,
            goal_id=fixture.GOAL_ID, planned_goal_ref=None))
    env = dict(os.environ, LOOPX_TURN=turn, LOOPX_REGISTRY=str(registry),
        LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_PROJECT=str(project), LOOPX_GOAL_ID=fixture.GOAL_ID,
        LOOPX_AGENT_ID=fixture.AGENT_ID, LOOPX_PHASE_DEADLINE_EPOCH=str(time.time() + 180),
        OPENAI_API_KEY="fixture-credential", NO_PROXY="127.0.0.1,localhost")
    return env, session, initial, registry


@contextmanager
def _native_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)"],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _send(guard, result, body=b'{"input":"synthetic request"}'):
    try:
        with urllib.request.urlopen(urllib.request.Request(guard.base_url + "/responses",
                data=body), timeout=40) as response:
            result["status"] = response.status
    except urllib.error.HTTPError as error:
        result["status"] = error.code
    except Exception as error:
        result["error"] = type(error).__name__


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_process_and_phase_are_rechecked_after_registry_contention(tmp_path, monkeypatch, provider):
    env, session, initial, registry = _admitted_owner(tmp_path, monkeypatch, provider)
    # The clock is confined to the transport guard; canonical quota retains its
    # real clock. Events ensure invalidation happens after the initial check.
    clock = [time.time()]
    monkeypatch.setattr(guard_module, "time", SimpleNamespace(time=lambda: clock[0]))
    for invalidation in ("process_exit", "phase_deadline"):
        clock[0] = time.time()
        current_env = dict(env, LOOPX_PHASE_DEADLINE_EPOCH=str(clock[0] + 180))
        wake = tmp_path / invalidation
        wake.mkdir()
        receipt = {"turn_id": "wake-" + invalidation}
        reached_lock = threading.Event()
        with _recording_upstream() as (url, upstream), _native_process() as process:
            current_env["OPENAI_BASE_URL"] = url
            guard = ResearchRequestGuard(current_env, receipt, wake)
            with guard.serve():
                guard.bind(process, current_env, session, original_quota=initial)
                original = guard.admission

                @contextmanager
                def observed_lifetime(**kwargs):
                    reached_lock.set()
                    with original.launch_lifetime(**kwargs):
                        yield

                guard.admission = SimpleNamespace(launch_lifetime=observed_lifetime)
                response = {}
                with project_registry_transaction(registry, operation="fixture_request_contention"):
                    reader = threading.Thread(target=_send, args=(guard, response))
                    reader.start()
                    assert reached_lock.wait(30), "request did not reach canonical admission"
                    if invalidation == "process_exit":
                        process.kill()
                        process.wait(timeout=5)
                    else:
                        clock[0] = float(current_env["LOOPX_PHASE_DEADLINE_EPOCH"])
                    assert upstream == {}, "the owner lock must precede the provider effect"
                reader.join(timeout=40)
                assert not reader.is_alive()
                assert response == {"status": 409}
                assert upstream == {} and guard.refused
                expected = ("research_request_without_live_owner" if invalidation == "process_exit"
                            else "research_phase_budget_exhausted")
                assert receipt["request_admission_refused"]["code"] == expected
        events = [json.loads(line) for line in (wake / "provider-requests.jsonl").read_text().splitlines()]
        assert len(events) == 1 and events[0]["event"] == "finished"
        assert events[0]["forwarded"] is False and events[0]["loopx_turn_id"] == env["LOOPX_TURN"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_slow_proxy_connect_cannot_hold_the_goal_stop_writer(tmp_path, monkeypatch, provider):
    env, session, initial, registry = _admitted_owner(tmp_path, monkeypatch, provider)
    connected, release = threading.Event(), threading.Event()
    seen = {}

    class SlowProxy(socketserver.BaseRequestHandler):
        def handle(self):
            seen["method"] = self.request.recv(65536).split(b" ", 1)[0]
            connected.set()
            release.wait(30)
            # Closing this synthetic tunnel is a connection failure, never an
            # accepted model request. No external destination is contacted.

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), SlowProxy) as proxy, _native_process() as process:
        proxy.daemon_threads = True
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        proxy_thread.start()
        current_env = dict(env, OPENAI_BASE_URL="https://model.example.invalid/v1",
            HTTPS_PROXY=f"http://127.0.0.1:{proxy.server_address[1]}")
        wake = tmp_path / "proxy-wake"
        wake.mkdir()
        guard = ResearchRequestGuard(current_env, {"turn_id": "wake-proxy"}, wake)
        response = {}
        reader = None
        try:
            with guard.serve():
                guard.bind(process, current_env, session, original_quota=initial)
                reader = threading.Thread(target=_send, args=(guard, response))
                reader.start()
                assert connected.wait(30), "request did not reach the declared proxy"
                assert seen["method"] == b"CONNECT"
                with project_registry_transaction(registry, operation="fixture_stop_during_connect") as transaction:
                    payload = transaction.payload_copy()
                    goal = next(row for row in payload["goals"] if row["id"] == fixture.GOAL_ID)
                    goal["activation"] = {"schema_version": "loopx_goal_activation_v1", "state": "stopped",
                        "updated_at": "2026-01-01T00:00:00Z", "reason": "Synthetic Stop during connection setup"}
                    transaction.commit(payload)
                assert not release.is_set(), "Stop must commit while connection setup is still pending"
                assert json.loads(registry.read_text())["goals"][0]["activation"]["state"] == "stopped"
                release.set()
                reader.join(timeout=40)
                assert not reader.is_alive() and response == {"status": 502}
        finally:
            release.set()
            if reader is not None:
                reader.join(timeout=40)
            proxy.shutdown()
            proxy_thread.join(timeout=5)
    events = [json.loads(line) for line in (wake / "provider-requests.jsonl").read_text().splitlines()]
    assert len(events) == 1 and events[0]["event"] == "finished"
    assert events[0]["forwarded"] is False
    assert events[0]["loopx_turn_id"] == env["LOOPX_TURN"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_backpressured_post_releases_the_goal_stop_writer(tmp_path, monkeypatch, provider):
    env, session, initial, registry = _admitted_owner(tmp_path, monkeypatch, provider)
    headers_received, release = threading.Event(), threading.Event()
    original_connection = gateway_module._upstream_connection

    def connection(*args, **kwargs):
        current = original_connection(*args, **kwargs)
        connect = current.connect

        def connect_with_small_buffer():
            connect()
            current.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)

        current.connect = connect_with_small_buffer
        return current

    class BackpressuredUpstream(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(10)
            headers = bytearray()
            while not headers.endswith(b"\r\n\r\n"):
                chunk = self.request.recv(1)
                if not chunk:
                    return
                headers.extend(chunk)
            headers_received.set()
            # No body reads: even a modest request fills the real TCP buffers.
            release.wait(20)

    monkeypatch.setattr(gateway_module, "_upstream_connection", connection)
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), BackpressuredUpstream) as upstream, _native_process() as process:
        upstream.daemon_threads = True
        upstream.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        current_env = dict(env, OPENAI_BASE_URL=f"http://127.0.0.1:{upstream.server_address[1]}/v1")
        wake = tmp_path / "body-send-wake"
        wake.mkdir()
        guard = ResearchRequestGuard(current_env, {"turn_id": "wake-body-send"}, wake)
        response, reader = {}, None
        try:
            with guard.serve():
                guard.bind(process, current_env, session, original_quota=initial)
                body = json.dumps({"input": "x" * (128 * 1024)}).encode()
                reader = threading.Thread(target=_send, args=(guard, response, body))
                reader.start()
                assert headers_received.wait(30), "request did not reach the admitted POST"
                with project_registry_transaction(registry, operation="fixture_stop_during_body_send") as transaction:
                    payload = transaction.payload_copy()
                    goal = next(row for row in payload["goals"] if row["id"] == fixture.GOAL_ID)
                    goal["activation"] = {"schema_version": "loopx_goal_activation_v1", "state": "stopped",
                        "updated_at": "2026-01-01T00:00:00Z", "reason": "Synthetic Stop during POST backpressure"}
                    transaction.commit(payload)
                assert not release.is_set(), "Stop must commit without waiting for the upstream to drain"
                assert json.loads(registry.read_text())["goals"][0]["activation"]["state"] == "stopped"
                reader.join(timeout=10)
                assert not reader.is_alive() and response == {"status": 502}
                assert not guard.refused and process.poll() is None, "transport timeout is not an admission refusal"
        finally:
            release.set()
            if reader is not None:
                reader.join(timeout=40)
            upstream.shutdown()
            upstream_thread.join(timeout=5)
    events = [json.loads(line) for line in (wake / "provider-requests.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["dispatch", "finished"]
    assert all(event["forwarded"] is None for event in events), "partial sends retain unknown acceptance"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_pending_connection_cannot_borrow_a_rebound_process_or_turn(tmp_path, monkeypatch, provider):
    env, session, initial, _registry = _admitted_owner(tmp_path, monkeypatch, provider)
    connected, release = threading.Event(), threading.Event()
    original_connection = gateway_module._upstream_connection

    def connection(*args, **kwargs):
        current = original_connection(*args, **kwargs)
        connect = current.connect

        def slow_connect():
            connected.set()
            assert release.wait(30)
            return connect()

        current.connect = slow_connect
        return current

    monkeypatch.setattr(gateway_module, "_upstream_connection", connection)
    wake = tmp_path / "rebound-wake"
    wake.mkdir()
    receipt = {"turn_id": "wake-rebound"}
    with _recording_upstream() as (url, upstream), _native_process() as original, _native_process() as replacement:
        env["OPENAI_BASE_URL"] = url
        guard = ResearchRequestGuard(env, receipt, wake)
        with guard.serve():
            guard.bind(original, env, session, original_quota=initial)
            response = {}
            reader = threading.Thread(target=_send, args=(guard, response))
            reader.start()
            try:
                assert connected.wait(30), "request did not reach connection setup"
                rebound_env = dict(env, LOOPX_TURN="replacement-held-turn")
                guard.bind(replacement, rebound_env, session, original_quota=initial)
            finally:
                release.set()
                reader.join(timeout=40)
            assert not reader.is_alive() and response == {"status": 409}
            assert upstream == {} and replacement.poll() is None
            original.wait(timeout=5)
            assert not guard.refused and not guard.admission_error
            assert "request_admission_refused" not in receipt
            # A replacement legitimately resuming the admitted original Turn
            # must still be able to send, after the stale request was refused.
            guard.bind(replacement, env, session, original_quota=initial)
            next_response = {}
            _send(guard, next_response)
            assert next_response == {"status": 200}
            assert upstream["body"] and replacement.poll() is None
    events = [json.loads(line) for line in (wake / "provider-requests.jsonl").read_text().splitlines()]
    finished = [event for event in events if event["event"] == "finished"]
    assert [event["forwarded"] for event in finished] == [False, True]
    assert finished[0]["refusal_code"] == "research_request_without_live_owner"
    assert finished[0]["loopx_turn_id"] == env["LOOPX_TURN"], "failed request retains its original Turn"


def test_current_admission_failure_stays_latched_across_rebinding(tmp_path):
    @contextmanager
    def unavailable(**_kwargs):
        raise OSError("synthetic authority read failure")
        yield

    entered = []

    @contextmanager
    def healthy(**_kwargs):
        entered.append(True)
        yield

    wake = tmp_path / "failed-admission-wake"
    wake.mkdir()
    receipt = {"turn_id": "wake-failed-admission"}
    owner = SimpleNamespace(launch_lifetime=unavailable)
    session = SimpleNamespace(session_id="native-failed-admission", goal_ref=None,
        admission=SimpleNamespace(with_launch_decision=lambda _check: owner))
    with _recording_upstream() as (url, upstream), _native_process() as original, _native_process() as replacement:
        env = dict(OPENAI_BASE_URL=url, OPENAI_API_KEY="fixture-credential", NO_PROXY="127.0.0.1",
            LOOPX_TURN="failed-admission-turn", LOOPX_GOAL_ID="fixture-goal", LOOPX_AGENT_ID="fixture-agent",
            LOOPX_PHASE_DEADLINE_EPOCH=str(time.time() + 180))
        guard = ResearchRequestGuard(env, receipt, wake)
        with guard.serve():
            guard.bind(original, env, session)
            response = {}
            _send(guard, response)
            assert response == {"status": 409}
            assert guard.refused and guard.admission_error
            assert receipt["request_admission_refused"]["code"] == "research_request_admission_unavailable"
            original.wait(timeout=5)
            owner = SimpleNamespace(launch_lifetime=healthy)
            guard.bind(replacement, env, session)
            next_response = {}
            _send(guard, next_response)
            assert next_response == {"status": 409}
            replacement.wait(timeout=5)
            assert guard.refused and guard.admission_error and not entered and not upstream
    events = [json.loads(line) for line in (wake / "provider-requests.jsonl").read_text().splitlines()]
    assert [event["refusal_code"] for event in events] == [
        "research_request_admission_unavailable", "research_original_request_recovery_required"]
