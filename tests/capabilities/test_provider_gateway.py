from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import loopx.capabilities.benchmark_toolkit.provider_gateway as gateway_module
from loopx.capabilities.benchmark_toolkit.provider_gateway import (
    serve_runner_owned_provider_gateway,
)


@contextmanager
def _recording_upstream():
    observed: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            body = self.rfile.read(int(self.headers["Content-Length"]))
            observed.update(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": body,
                }
            )
            response = json.dumps({"ok": True}, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/v1", observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_runner_owned_gateway_injects_upstream_credential_and_streams_response() -> (
    None
):
    with _recording_upstream() as (upstream, observed):
        with serve_runner_owned_provider_gateway(
            upstream_base_url=upstream,
            upstream_bearer_token="fixture-upstream-secret",
        ) as gateway:
            assert gateway.base_url.startswith("http://127.0.0.1:")
            assert "fixture-upstream-secret" not in repr(gateway)
            request = urllib.request.Request(
                f"{gateway.base_url}/responses?trace=1",
                data=b'{"model":"fixture"}',
                headers={
                    "Authorization": "Bearer " + "agent-visible-sentinel",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.load(response)

    assert payload == {"ok": True}
    assert observed == {
        "path": "/v1/responses?trace=1",
        "authorization": "Bearer " + "fixture-upstream-secret",
        "body": b'{"model":"fixture"}',
    }


def test_runner_owned_gateway_rejects_non_model_paths_without_upstream_call() -> None:
    with _recording_upstream() as (upstream, observed):
        with serve_runner_owned_provider_gateway(
            upstream_base_url=upstream,
            upstream_bearer_token="fixture-upstream-secret",
        ) as gateway:
            request = urllib.request.Request(
                f"{gateway.base_url}/models",
                data=b"{}",
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)

    assert error.value.code == 404
    assert observed == {}


def test_explicit_http_proxy_preserves_endpoint_and_https_tunnel_target():
    from urllib.parse import urlsplit
    from loopx.capabilities.benchmark_toolkit.provider_gateway import _upstream_connection

    with _recording_upstream() as (proxy, observed):
        origin = urlsplit(proxy)
        proxy_url = f"http://{origin.hostname}:{origin.port}"
        with serve_runner_owned_provider_gateway(upstream_base_url="http://127.0.0.1:1/v1",
                upstream_bearer_token="fixture-proxy-credential", upstream_proxy_url=proxy_url) as gateway:
            with urllib.request.urlopen(urllib.request.Request(gateway.base_url + "/responses", data=b"{}"), timeout=5) as response:
                assert json.load(response) == {"ok": True}
        assert observed["path"] == "http://127.0.0.1:1/v1/responses"
        assert observed["authorization"] == "Bearer " + "fixture-proxy-credential"
        connection = _upstream_connection(urlsplit("https://model.example.invalid/v1"),
            timeout_sec=5, proxy=urlsplit(proxy_url))
        assert connection.host == origin.hostname and connection.port == origin.port
        assert connection._tunnel_host == "model.example.invalid" and connection._tunnel_port == 443
        connection.close()


@pytest.mark.parametrize("guarded", [False, True])
def test_connection_setup_is_outside_guarded_dispatch_and_default_path_stays_lazy(monkeypatch, guarded):
    original_connection = gateway_module._upstream_connection
    observed = []
    state = {"bound": False, "in_lifetime": False, "sending": False}

    def connection(*args, **kwargs):
        current = original_connection(*args, **kwargs)
        connect, request = current.connect, current.request

        def observed_connect():
            observed.append(dict(state))
            return connect()

        def observed_request(*args, **kwargs):
            state["sending"] = True
            assert state["in_lifetime"] is guarded
            return request(*args, **kwargs)

        current.connect, current.request = observed_connect, observed_request
        return current

    @contextmanager
    def lifetime():
        state["in_lifetime"] = True
        try:
            yield
        finally:
            state["in_lifetime"] = False

    def bind(_request):
        state["bound"] = True
        return lifetime()

    monkeypatch.setattr(gateway_module, "_upstream_connection", connection)
    with _recording_upstream() as (upstream, _observed):
        with serve_runner_owned_provider_gateway(upstream_base_url=upstream,
                upstream_bearer_token="fixture-credential", request_lifetime=bind if guarded else None) as gateway:
            with urllib.request.urlopen(urllib.request.Request(gateway.base_url + "/responses", data=b"{}"), timeout=5) as response:
                assert response.status == 200
    assert observed == [{"bound": guarded, "in_lifetime": False, "sending": not guarded}]


@pytest.mark.parametrize("failure", ["connect", "send"])
def test_connection_failure_is_unforwarded_but_failed_send_remains_unknown(monkeypatch, failure):
    original_connection = gateway_module._upstream_connection
    events = []

    def connection(*args, **kwargs):
        current = original_connection(*args, **kwargs)
        request = current.request

        def failed_connect():
            raise OSError("synthetic connection failure")

        def uncertain_send(*args, **kwargs):
            request(*args, **kwargs)
            raise OSError("synthetic loss after sending")

        if failure == "connect":
            current.connect = failed_connect
        else:
            current.request = uncertain_send
        return current

    @contextmanager
    def lifetime(_request):
        yield

    monkeypatch.setattr(gateway_module, "_upstream_connection", connection)
    with _recording_upstream() as (upstream, _observed):
        with serve_runner_owned_provider_gateway(upstream_base_url=upstream,
                upstream_bearer_token="fixture-credential", request_lifetime=lifetime,
                observe_request=events.append) as gateway:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(gateway.base_url + "/responses", data=b"{}"), timeout=5)
            assert error.value.code == 502
    if failure == "connect":
        assert len(events) == 1 and events[0]["forwarded"] is False
    else:
        assert [event["event"] for event in events] == ["dispatch", "finished"]
        assert all(event["forwarded"] is None for event in events)


def test_guarded_send_cannot_silently_reconnect_under_the_authority_lock(monkeypatch):
    original_connection = gateway_module._upstream_connection
    connection_ref = []
    connects = []

    def connection(*args, **kwargs):
        current = original_connection(*args, **kwargs)
        connect = current.connect

        def observed_connect():
            connects.append(True)
            return connect()

        current.connect = observed_connect
        connection_ref.append(current)
        return current

    @contextmanager
    def lifetime(_request):
        # Simulate the established connection disappearing before the send.
        connection_ref[0].close()
        yield

    monkeypatch.setattr(gateway_module, "_upstream_connection", connection)
    with _recording_upstream() as (upstream, observed):
        with serve_runner_owned_provider_gateway(upstream_base_url=upstream,
                upstream_bearer_token="fixture-credential", request_lifetime=lifetime) as gateway:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(gateway.base_url + "/responses", data=b"{}"), timeout=5)
            assert error.value.code == 502
        assert observed == {}
    assert connects == [True]


@pytest.mark.parametrize("partial_write", [False, True], ids=["headers-and-body", "partial-write-progress"])
def test_guarded_headers_and_body_share_one_send_budget(monkeypatch, partial_write):
    # Model two writes which each need 0.75s. A per-write 1s timeout would let
    # both succeed; the complete request is allowed only 1s, including headers.
    now = [0.0]
    timeouts = []

    class SlowSocket:
        timeout = 600.0

        def gettimeout(self):
            return self.timeout

        def settimeout(self, seconds):
            self.timeout = seconds

        def send(self, data):
            timeouts.append(self.timeout)
            if self.timeout < 0.75:
                now[0] += self.timeout
                raise TimeoutError("synthetic send backpressure")
            now[0] += 0.75
            return 1 if partial_write else len(data)

    monkeypatch.setattr(gateway_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    connection = http.client.HTTPConnection("unused.invalid", timeout=600)
    connection.sock = SlowSocket()
    with pytest.raises(TimeoutError):
        with gateway_module._bounded_request_send(connection, 1.0):
            connection.request("POST", "/responses", body=b"{}")
    assert timeouts == [1.0, 0.25]
    assert now[0] == 1.0
    assert connection.sock.gettimeout() == 600.0


def test_guarded_response_stream_can_outlive_the_send_budget():
    streaming, release, lifetime_exited, done = (threading.Event() for _ in range(4))
    result, events = {}, []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":')
            self.wfile.flush()
            streaming.set()
            if release.wait(10):
                self.wfile.write(b'true}')

    @contextmanager
    def lifetime(_request):
        try:
            yield
        finally:
            lifetime_exited.set()

    def read(gateway):
        try:
            with urllib.request.urlopen(urllib.request.Request(gateway.base_url + "/responses",
                    data=b"{}"), timeout=10) as response:
                result["body"] = json.load(response)
        except Exception as error:
            result["error"] = type(error).__name__
        finally:
            done.set()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        reader = None
        try:
            with serve_runner_owned_provider_gateway(
                    upstream_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    upstream_bearer_token="fixture-credential", request_timeout_sec=10,
                    request_lifetime=lifetime, observe_request=events.append) as gateway:
                reader = threading.Thread(target=read, args=(gateway,))
                reader.start()
                assert streaming.wait(5) and lifetime_exited.wait(5)
                assert not done.wait(gateway_module._GUARDED_SEND_TIMEOUT_SEC + 0.25)
                release.set()
                reader.join(timeout=10)
                assert not reader.is_alive() and result == {"body": {"ok": True}}
        finally:
            release.set()
            if reader is not None:
                reader.join(timeout=10)
            server.shutdown()
            server_thread.join(timeout=5)
    assert events[-1]["event"] == "finished" and events[-1]["forwarded"] is True


@pytest.mark.parametrize(
    "upstream",
    (
        "",
        "file:///tmp/provider",
        "http://provider.invalid/v1",
        "https://user:pass@provider.invalid/v1",
        "https://provider.invalid/v1?secret=query",
    ),
)
def test_runner_owned_gateway_rejects_unsafe_upstream_urls(upstream: str) -> None:
    with pytest.raises(ValueError):
        with serve_runner_owned_provider_gateway(
            upstream_base_url=upstream,
            upstream_bearer_token="fixture-upstream-secret",
        ):
            pass
