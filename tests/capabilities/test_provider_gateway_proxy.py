from __future__ import annotations

from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import urlsplit

import pytest

from loopx.capabilities.benchmark_toolkit.provider_gateway import (
    ProviderGatewayRequestRejected,
    _upstream_connection,
    serve_runner_owned_provider_gateway,
)


@pytest.mark.parametrize("proxy_url,proxy_port", [
    ("http://proxy.invalid", 80),
    ("http://proxy.invalid:3128", 3128),
])
@pytest.mark.parametrize("upstream_url,target_port", [
    ("https://provider.invalid/v1", 443),
    ("https://provider.invalid:8443/v1", 8443),
])
def test_http_connect_proxy_port_does_not_change_https_tunnel_target(
    proxy_url, proxy_port, upstream_url, target_port,
):
    connection = _upstream_connection(
        urlsplit(upstream_url), timeout_sec=2, proxy_url=proxy_url,
    )
    try:
        assert isinstance(connection, http.client.HTTPSConnection)
        assert connection.host == "proxy.invalid"
        assert connection.port == proxy_port
        assert connection._tunnel_host == "provider.invalid"
        assert connection._tunnel_port == target_port
        assert connection.sock is None, "route validation must not connect"
    finally:
        connection.close()


@pytest.fixture
def blocked_connect_proxy():
    connected, release = threading.Event(), threading.Event()
    targets = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_CONNECT(self):  # noqa: N802 - BaseHTTPRequestHandler contract
            targets.append(self.path)
            connected.set()
            release.wait(timeout=10)
            # No public endpoint or TLS service is needed: exercise a real
            # blocked CONNECT, then return an ordinary proxy failure.
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", connected, release, targets
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_slow_connect_leaves_dispatch_lifetime_free_for_stop_writer(blocked_connect_proxy):
    proxy_url, connected, release, targets = blocked_connect_proxy
    authority_lock = threading.Lock()
    admission_entered, stop_committed = threading.Event(), threading.Event()
    response = {}

    @contextmanager
    def lifetime():
        # A transport-level oracle: the same lock protects dispatch admission
        # and its Stop writer. This does not substitute for canonical Goal tests.
        with authority_lock:
            admission_entered.set()
            if stop_committed.is_set():
                raise ProviderGatewayRequestRejected("fixture_stop_committed")
            yield time.monotonic() + 2

    def commit_stop():
        with authority_lock:
            stop_committed.set()

    with serve_runner_owned_provider_gateway(
        upstream_base_url="https://provider.invalid/v1",
        upstream_bearer_token="synthetic-upstream-credential",
        upstream_proxy_url=proxy_url,
        request_timeout_sec=5,
        request_lifetime=lifetime,
    ) as gateway:
        target = urlsplit(gateway.base_url)

        def request():
            client = http.client.HTTPConnection(target.hostname, target.port, timeout=6)
            try:
                client.request("POST", target.path + "/responses", body=b"{}",
                               headers={"Content-Type": "application/json"})
                result = client.getresponse()
                response.update(status=result.status, body=json.loads(result.read()))
            except Exception as error:
                response["error"] = error
            finally:
                client.close()

        requester = threading.Thread(target=request, daemon=True)
        writer = threading.Thread(target=commit_stop, daemon=True)
        requester.start()
        try:
            assert connected.wait(timeout=5), "the actual CONNECT did not reach the local proxy"
            writer.start()
            assert stop_committed.wait(timeout=2), "CONNECT held the dispatch authority lock"
            assert not release.is_set(), "Stop must commit before the proxy is released"
            assert not admission_entered.is_set()
            assert targets == ["provider.invalid:443"]
        finally:
            release.set()
            requester.join(timeout=6)
            if writer.ident is not None:
                writer.join(timeout=2)
        assert not requester.is_alive()
        assert not writer.is_alive()
        assert "error" not in response, response
        assert response["status"] == 502
        assert response["body"]["error"]["code"] == "provider_gateway_upstream_unavailable"
        assert not admission_entered.is_set(), "a failed CONNECT must never enter admission"
