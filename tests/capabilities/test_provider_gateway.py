from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

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


def test_runner_owned_gateway_injects_upstream_credential_and_forwards_json() -> (
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


@contextmanager
def _streaming_upstream(*, chunked: bool, disconnect: bool):
    first = b'data: {"delta":"first"}\n\n'
    second = b'data: {"delta":"' + b"x" * (128 * 1024) + b'"}\n\n'
    first_sent = threading.Event()
    release_rest = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *args: object) -> None:
            return

        def _write_event(self, event: bytes) -> None:
            if chunked:
                self.wfile.write(f"{len(event):x}\r\n".encode() + event + b"\r\n")
            else:
                self.wfile.write(event)
            self.wfile.flush()

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(len(first) + len(second)))
            self.end_headers()
            try:
                self._write_event(first)
                first_sent.set()
                if release_rest.wait(timeout=10) and not disconnect:
                    self._write_event(second)
                    if chunked:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # A failed client assertion can close the response early.
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/v1", first, second, first_sent, release_rest
    finally:
        release_rest.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("chunked", (False, True), ids=("content-length", "chunked"))
@pytest.mark.parametrize("disconnect", (False, True), ids=("complete", "disconnect"))
def test_runner_owned_gateway_forwards_events_before_upstream_completion(
    chunked: bool, disconnect: bool,
) -> None:
    with _streaming_upstream(chunked=chunked, disconnect=disconnect) as stream:
        upstream, first, second, first_sent, release_rest = stream
        with serve_runner_owned_provider_gateway(
            upstream_base_url=upstream,
            upstream_bearer_token="fixture-upstream-secret",
        ) as gateway:
            request = urllib.request.Request(
                f"{gateway.base_url}/responses",
                data=b'{"model":"fixture","stream":true}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    assert response.status == 200
                    assert response.headers["Content-Type"] == "text/event-stream"
                    assert "Transfer-Encoding" not in response.headers
                    assert first_sent.wait(timeout=5)
                    # The producer cannot complete until the client sees this event.
                    received = response.read(len(first))
                    assert received == first
                    assert not release_rest.is_set()
                    release_rest.set()
                    received += response.read()
                    assert received == first + (b"" if disconnect else second)
            finally:
                release_rest.set()


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
