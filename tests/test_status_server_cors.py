from __future__ import annotations

import http.client
import json
import threading

import pytest

from loopx.status_server import (
    StatusHTTPServer,
    StatusRequestHandler,
    cors_response_headers,
    parse_strict_json_object,
)


def test_cors_response_headers_loopback_origin_is_echoed() -> None:
    headers = cors_response_headers("http://127.0.0.1:8123")

    assert headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8123"
    assert headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
    assert headers["Vary"] == "Origin"


def test_cors_response_headers_localhost_origin_is_echoed() -> None:
    headers = cors_response_headers("http://localhost:8123")

    assert headers["Access-Control-Allow-Origin"] == "http://localhost:8123"


def test_cors_response_headers_foreign_origin_is_rejected() -> None:
    assert cors_response_headers("https://evil.example") == {}


def test_cors_response_headers_missing_origin_needs_no_cors() -> None:
    assert cors_response_headers(None) == {}


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "http://127.0.0.1.evil.example",
        "http://localhost.evil.example",
        "http://localhost@evil.example",
        "http://[::1",
    ],
)
def test_cors_response_headers_spoofed_loopback_origins_are_rejected(origin: str) -> None:
    assert cors_response_headers(origin) == {}


def test_cors_response_headers_file_scheme_localhost_is_rejected() -> None:
    # Hostname alone is not enough: only http(s) loopback browser origins may
    # receive ACAO. A file:// localhost Origin must stay ACAO-free.
    assert cors_response_headers("file://localhost") == {}


def _start_server() -> tuple[StatusHTTPServer, threading.Thread]:
    server = StatusHTTPServer(("127.0.0.1", 0), StatusRequestHandler)
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(port: int, *, origin: str | None) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Origin": origin} if origin else {}
    conn.request("GET", "/healthz", headers=headers)
    return conn.getresponse()


def _options(port: int, *, origin: str | None) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Origin": origin} if origin else {}
    conn.request("OPTIONS", "/healthz", headers=headers)
    return conn.getresponse()


def test_healthz_without_origin_omits_acao() -> None:
    server, thread = _start_server()
    try:
        response = _get(server.server_address[1], origin=None)
        body = response.read()
        assert response.status == 200
        assert response.getheader("Access-Control-Allow-Origin") is None
        assert b'"ok": true' in body
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_healthz_with_foreign_origin_omits_acao() -> None:
    server, thread = _start_server()
    try:
        response = _get(server.server_address[1], origin="https://evil.example")
        response.read()
        assert response.status == 200
        assert response.getheader("Access-Control-Allow-Origin") is None
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_healthz_with_loopback_origin_echoes_acao() -> None:
    server, thread = _start_server()
    try:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        response = _get(server.server_address[1], origin=origin)
        response.read()
        assert response.status == 200
        assert response.getheader("Access-Control-Allow-Origin") == origin
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_options_preflight_with_foreign_origin_omits_acao() -> None:
    server, thread = _start_server()
    try:
        response = _options(server.server_address[1], origin="https://evil.example")
        response.read()
        assert response.status == 204
        assert response.getheader("Access-Control-Allow-Origin") is None
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_options_preflight_with_loopback_origin_echoes_acao() -> None:
    server, thread = _start_server()
    try:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        response = _options(server.server_address[1], origin=origin)
        response.read()
        assert response.status == 204
        assert response.getheader("Access-Control-Allow-Origin") == origin
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_status_post_rejects_non_finite_json_numbers(number: str) -> None:
    server, thread = _start_server()
    server.reward_dry_run_path = "/reward/dry-run"
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=5
        )
        connection.request(
            "POST",
            server.reward_dry_run_path,
            body=f'{{"unexpected":{number}}}'.encode(),
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 400
        assert "request body must be strict JSON" in payload["error"]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_strict_json_accepts_nested_finite_exponents() -> None:
    assert parse_strict_json_object(b'{"values":[1e308,{"small":-1e-308}]}') == {
        "values": [1e308, {"small": -1e-308}]
    }


@pytest.mark.parametrize(
    "disconnect_exc",
    [
        BrokenPipeError(32, "Broken pipe"),
        ConnectionResetError(54, "Connection reset by peer"),
        ConnectionAbortedError(53, "Software caused connection abort"),
    ],
)
@pytest.mark.parametrize("fail_during", ["headers", "chunk", "flush"])
def test_send_http_response_absorbs_client_disconnects_and_closes_connection(
    disconnect_exc: OSError, fail_during: str
) -> None:
    from types import SimpleNamespace
    from loopx.status_server import send_http_response

    writes: list[bytes] = []

    class BrokenWriter:
        def write(self, data: bytes) -> int:
            writes.append(data)
            if fail_during == "chunk" and len(writes) >= 2:
                raise disconnect_exc
            return len(data)

        def flush(self) -> None:
            if fail_during == "flush":
                raise disconnect_exc

    handler = SimpleNamespace(
        close_connection=False,
        wfile=BrokenWriter(),
        end_headers=lambda: (_ for _ in ()).throw(disconnect_exc) if fail_during == "headers" else None,
    )
    large_body = b"x" * 150_000
    assert send_http_response(handler, large_body) is False  # type: ignore[arg-type]
    assert handler.close_connection is True
    if fail_during == "chunk":
        assert len(writes) == 2


def test_send_http_response_re_raises_non_disconnect_oserror() -> None:
    import errno
    from types import SimpleNamespace
    from loopx.status_server import send_http_response

    class FailingWriter:
        def write(self, _data: bytes) -> int:
            raise OSError(errno.EIO, "Input/output error")

        def flush(self) -> None:
            pass

    handler = SimpleNamespace(close_connection=False, wfile=FailingWriter(), end_headers=lambda: None)
    with pytest.raises(OSError, match="Input/output error"):
        send_http_response(handler, b"{}")  # type: ignore[arg-type]
    assert handler.close_connection is False


def test_status_server_absorbs_live_early_client_disconnect_without_stderr_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import socket
    from loopx import status_server

    started = threading.Event()
    proceed = threading.Event()
    orig_send_http_response = status_server.send_http_response

    def gated_send(handler, body=None):
        started.set()
        proceed.wait(timeout=5)
        return orig_send_http_response(handler, body)

    monkeypatch.setattr(status_server, "send_http_response", gated_send)
    server, thread = _start_server()
    try:
        sock = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5)
        sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert started.wait(timeout=5)
        # Close peer socket cleanly with RST/FIN before server finishes writing response.
        sock.close()
        proceed.set()
        # Server remains healthy for subsequent requests and logs zero disconnect tracebacks.
        resp = _get(server.server_address[1], origin=None)
        assert resp.status == 200
        assert resp.read().endswith(b"}")
    finally:
        server.shutdown()
        thread.join(timeout=5)

    captured = capsys.readouterr()
    assert "BrokenPipeError" not in captured.err
    assert "ConnectionResetError" not in captured.err
    assert "Traceback" not in captured.err


def test_disconnect_safe_http_server_suppresses_handle_error_for_disconnects_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loopx.status_server import DisconnectSafeHTTPServer

    server = DisconnectSafeHTTPServer(("127.0.0.1", 0), StatusRequestHandler)
    try:
        try:
            raise ConnectionResetError(54, "Connection reset by peer")
        except ConnectionResetError:
            server.handle_error(None, ("127.0.0.1", 1234))
        assert capsys.readouterr().err == ""

        try:
            raise ValueError("unexpected bug")
        except ValueError:
            server.handle_error(None, ("127.0.0.1", 1234))
        assert "unexpected bug" in capsys.readouterr().err
    finally:
        server.server_close()
