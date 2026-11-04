from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from benchmark.runtime.provider_request_admission import ProviderRequestAdmission


class _Admission:
    """Expose the lock ordering without introducing a second authority policy."""

    def __init__(self):
        self.lock = threading.Lock()
        self.local = threading.local()
        self.entries = 0
        self.pause_second = False
        self.paused = threading.Event()
        self.resume = threading.Event()

    def with_launch_decision(self, _check):
        return self

    @contextmanager
    def launch_lifetime(self, **_kwargs):
        with self.lock:
            self.entries += 1
            self.local.held = True
            try:
                if self.pause_second and self.entries == 2:
                    self.paused.set()
                    assert self.resume.wait(timeout=10)
                yield
            finally:
                self.local.held = False


@contextmanager
def _waiting_upstream():
    received = threading.Event()
    release = threading.Event()
    observed = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            pass

        def do_POST(self):
            observed["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            observed["accept_encoding"] = self.headers.get_all("Accept-Encoding")
            received.set()
            if release.wait(timeout=10):
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received, release, observed
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _guard(tmp_path, upstream, admission):
    receipt = {}
    session = SimpleNamespace(admission=admission, goal_ref={}, session_id="synthetic-session")
    guard = ProviderRequestAdmission(
        {"OPENAI_BASE_URL": upstream, "OPENAI_API_KEY": "synthetic-provider-key",
         "LOOPX_TURN": "synthetic-turn"},
        session, receipt, tmp_path,
    )
    guard.started(SimpleNamespace(poll=lambda: None))
    return guard, receipt


def _request(base_url):
    result = {}

    def run():
        parsed = urlsplit(base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=8)
        try:
            connection.request(
                "POST", parsed.path + "/responses", body=b'{"model":"fixture"}',
                headers={"Content-Type": "application/json", "Accept-Encoding": "gzip, deflate"},
            )
            response = connection.getresponse()
            result.update(status=response.status, body=response.read())
        except Exception as error:
            result["error"] = error
        finally:
            connection.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def test_provider_observes_dispatch_while_upstream_response_is_pending(tmp_path, monkeypatch):
    with _waiting_upstream() as (upstream, received, release, observed):
        admission = _Admission()
        guard, receipt = _guard(tmp_path, upstream, admission)
        dispatched = threading.Event()
        finished = threading.Event()
        observe = guard.observe

        def record(event):
            assert not getattr(admission.local, "held", False)
            observe(event)
            if event.get("event") == "dispatch":
                dispatched.set()
            if event.get("event", "finished") == "finished":
                finished.set()

        monkeypatch.setattr(guard, "observe", record)
        with guard.serve(["codex", "exec", "-"]):
            client, result = _request(guard.base_url)
            try:
                assert received.wait(timeout=5)
                summary = receipt["provider_request_invocations"][0]
                assert summary["requests"] == 1
                assert dispatched.wait(timeout=5)
                assert summary["sent"] == 1
                assert summary["pending"] == 1
                assert summary["unknown"] == summary["refused"] == 0
                assert not finished.is_set()
                release.set()
                assert finished.wait(timeout=5)
            finally:
                release.set()
                client.join(timeout=5)
        assert not client.is_alive()
        assert result == {"status": 200, "body": b'{"ok":true}'}
        assert summary["requests"] == summary["sent"] == 1
        assert summary["pending"] == 0
        assert observed["accept_encoding"] == ["gzip, deflate"]
        events = [json.loads(line) for line in (tmp_path / "provider-requests.jsonl").read_text().splitlines()]
        assert [event["event"] for event in events] == ["started", "dispatch", "finished"]
        assert len({event["request_id"] for event in events}) == 1
        assert "synthetic-provider-key" not in json.dumps(events)


def test_blocked_request_log_does_not_hold_invocation_or_admission_lock(tmp_path, monkeypatch):
    log_blocked = threading.Event()
    release_log = threading.Event()
    open_file = Path.open
    log_path = tmp_path / "provider-requests.jsonl"

    class SlowLog:
        def __init__(self, stream):
            self.stream = stream

        def write(self, line):
            if json.loads(line).get("event", "finished") == "finished":
                log_blocked.set()
                assert release_log.wait(timeout=10)
            return self.stream.write(line)

    @contextmanager
    def delayed_open(path, *args, **kwargs):
        with open_file(path, *args, **kwargs) as stream:
            yield SlowLog(stream) if path == log_path and args == ("a",) else stream

    monkeypatch.setattr(Path, "open", delayed_open)
    with _waiting_upstream() as (upstream, received, release_response, _observed):
        admission = _Admission()
        admission.pause_second = True
        guard, _receipt = _guard(tmp_path, upstream, admission)
        admitted = threading.Event()
        writer_done = threading.Event()

        def next_request():
            with guard.lifetime():
                admitted.set()

        def writer():
            with admission.lock:
                writer_done.set()

        next_thread = threading.Thread(target=next_request, daemon=True)
        writer_thread = threading.Thread(target=writer, daemon=True)
        with guard.serve(["codex", "exec", "-"]):
            client, result = _request(guard.base_url)
            try:
                assert received.wait(timeout=5)
                # The next admission has passed its initial state check and now
                # owns the canonical lock. Finish the first response into a slow log.
                next_thread.start()
                assert admission.paused.wait(timeout=5)
                release_response.set()
                assert log_blocked.wait(timeout=5)
                admission.resume.set()
                writer_thread.start()
                assert writer_done.wait(timeout=5)
                assert admitted.is_set()
                assert not release_log.is_set()
            finally:
                admission.resume.set()
                release_log.set()
                release_response.set()
                client.join(timeout=5)
                if next_thread.ident is not None:
                    next_thread.join(timeout=5)
                if writer_thread.ident is not None:
                    writer_thread.join(timeout=5)
        assert result == {"status": 200, "body": b'{"ok":true}'}
