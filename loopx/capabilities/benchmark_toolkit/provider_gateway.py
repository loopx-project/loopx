"""Runner-owned HTTP gateway for benchmark model-provider calls.

The gateway keeps the upstream bearer credential in the runner process and
exposes only a loopback Responses API endpoint to an isolated agent runtime.
It deliberately is not an agent sandbox: callers must place the app-server in
an OS authority boundary that cannot inspect the runner process or its files.
"""

from __future__ import annotations

import http.client
import json
import math
import secrets
import socket
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import SplitResult, urlsplit


@dataclass(frozen=True)
class RunnerOwnedProviderGateway:
    """Public-safe handle for one bounded loopback gateway."""

    base_url: str
    allowed_paths: tuple[str, ...]


_DEFAULT_ALLOWED_PATHS = ("/responses", "/responses/compact")
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_CLIENT_CREDENTIAL_HEADERS = {
    "api-key",
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
}


class ProviderGatewayRequestRejected(RuntimeError):
    """A caller's current authority refused this request before dispatch."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _validated_upstream(value: str, *, allow_http_upstream: bool = False) -> SplitResult:
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("upstream_base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("upstream_base_url must not contain credentials or query data")
    if not allow_http_upstream and parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise ValueError("non-loopback upstream_base_url must use HTTPS")
    return parsed


def _validated_allowed_paths(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("allowed_paths must be a sequence")
    normalized = tuple(dict.fromkeys(str(value).strip() for value in values))
    if not normalized or any(
        not value.startswith("/")
        or "?" in value
        or "#" in value
        or ".." in value.split("/")
        for value in normalized
    ):
        raise ValueError("allowed_paths must contain safe absolute URL paths")
    return normalized


def _upstream_connection(
    parsed: SplitResult, *, timeout_sec: float, proxy_url: str | None = None
) -> http.client.HTTPConnection:
    host = parsed.hostname
    if host is None:  # Defensive narrowing; _validated_upstream rejects this.
        raise ValueError("upstream_base_url must include a host")
    port = parsed.port
    if proxy_url is not None:
        proxy = urlsplit(proxy_url)
        if (parsed.scheme != "https" or proxy.scheme != "http" or not proxy.hostname
                or proxy.username or proxy.password or proxy.query or proxy.fragment
                or proxy.path not in {"", "/"}):
            raise ValueError("provider gateway requires a credential-free HTTP CONNECT proxy for HTTPS")
        connection = http.client.HTTPSConnection(proxy.hostname,
            proxy.port if proxy.port is not None else 80, timeout=timeout_sec)
        connection.set_tunnel(host, port or 443)
        return connection
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout_sec)
    return http.client.HTTPConnection(host, port, timeout=timeout_sec)


def _dispatch_bounded(connection: http.client.HTTPConnection, path: str, body: bytes,
                      headers: dict[str, str], deadline: float) -> None:
    """Bound the entire admitted send, including real TCP backpressure.

    The connection (DNS, CONNECT and TLS included) is prepared outside the
    caller's authority scope. No reconnect or automatic POST retry is allowed.
    """
    connected_socket = connection.sock
    if connected_socket is None:
        raise OSError("provider connection closed before dispatch")
    connection.auto_open = 0
    connection.putrequest("POST", path,
        skip_accept_encoding=any(key.lower() == "accept-encoding" for key in headers))
    for key, value in headers.items():
        connection.putheader(key, value)

    def remaining() -> None:
        budget = deadline - time.monotonic()
        if not math.isfinite(budget) or budget <= 0:
            raise TimeoutError("provider request dispatch deadline exceeded")
        connected_socket.settimeout(budget)

    remaining()
    connection.endheaders()
    # Recompute the absolute budget between writes; repeated successful writes
    # must not renew a socket timeout while a Goal writer is waiting.
    view = memoryview(body)
    for start in range(0, len(view), 64 * 1024):
        remaining()
        connection.send(view[start:start + 64 * 1024])


@contextmanager
def serve_runner_owned_provider_gateway(
    *,
    upstream_base_url: str,
    upstream_bearer_token: str,
    allowed_paths: Sequence[str] = _DEFAULT_ALLOWED_PATHS,
    request_timeout_sec: float = 600,
    max_request_bytes: int = 64 * 1024 * 1024,
    request_lifetime: Callable[[], AbstractContextManager[float]] | None = None,
    observe_request: Callable[[dict[str, object]], None] | None = None,
    upstream_proxy_url: str | None = None,
    allow_http_upstream: bool = False,
) -> Iterator[RunnerOwnedProviderGateway]:
    """Serve a loopback-only Responses API gateway for one benchmark run.

    The returned URL and request surface contain no upstream credential. The
    caller owns the stronger process/filesystem boundary between this gateway
    and the agent runtime.
    """

    upstream = _validated_upstream(upstream_base_url, allow_http_upstream=allow_http_upstream)
    token = str(upstream_bearer_token)
    if not token.strip():
        raise ValueError("upstream_bearer_token must be non-empty")
    if request_timeout_sec <= 0 or max_request_bytes <= 0:
        raise ValueError("gateway limits must be positive")
    admitted_paths = _validated_allowed_paths(allowed_paths)
    if upstream_proxy_url is not None:
        # Validate an explicit proxy before starting the native invocation.
        _upstream_connection(upstream, timeout_sec=request_timeout_sec,
            proxy_url=upstream_proxy_url).close()
    # Each guarded server owns one invocation. A fresh unguessable path also
    # prevents a stale URL from adopting a new invocation after port reuse.
    prefix = "/" + secrets.token_hex(24) if request_lifetime is not None else ""
    clients: set[socket.socket] = set()
    clients_lock = threading.Lock()

    class GatewayHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def setup(self) -> None:
            if request_lifetime is not None:
                self.request.settimeout(request_timeout_sec)
                with clients_lock:
                    clients.add(self.request)
            super().setup()

        def finish(self) -> None:
            try:
                super().finish()
            finally:
                with clients_lock:
                    clients.discard(self.request)

        def log_message(self, _format: str, *args: object) -> None:
            return

        def _json_error(self, status: int, reason: str) -> None:
            body = json.dumps(
                {"error": {"type": "loopx_provider_gateway", "code": reason}},
                sort_keys=True,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            requested = urlsplit(self.path)
            path = requested.path.removeprefix(prefix) if prefix else requested.path
            if (prefix and not requested.path.startswith(prefix + "/")) or path not in admitted_paths:
                self._json_error(404, "provider_gateway_path_not_admitted")
                return
            raw_length = self.headers.get("Content-Length")
            try:
                content_length = int(raw_length or "")
            except ValueError:
                content_length = -1
            if content_length < 0 or content_length > max_request_bytes:
                self._json_error(413, "provider_gateway_request_size_invalid")
                return
            body = self.rfile.read(content_length)
            if request_lifetime is not None and len(body) != content_length:
                self._json_error(400, "provider_gateway_incomplete_request")
                return
            upstream_path = f"{upstream.path.rstrip('/')}{path}"
            if requested.query:
                upstream_path = f"{upstream_path}?{requested.query}"
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower()
                not in {
                    *_HOP_BY_HOP_HEADERS,
                    *_CLIENT_CREDENTIAL_HEADERS,
                    "content-length",
                    "host",
                }
            }
            headers["Authorization"] = f"Bearer {token}"
            headers["Content-Length"] = str(len(body))
            connection = _upstream_connection(upstream, timeout_sec=request_timeout_sec,
                **({"proxy_url": upstream_proxy_url} if upstream_proxy_url is not None else {}))
            response_started = False
            observed: dict[str, object] = {"path": path, "forwarded": False}
            if observe_request is not None:
                observed["request_id"] = secrets.token_hex(16)

            def observe(event: str) -> None:
                if observe_request is not None:
                    observe_request({**observed, "event": event})

            try:
                observe("started")
                if request_lifetime is None:
                    connection.request("POST", upstream_path, body=body, headers=headers)
                else:
                    connection.connect()
                    with request_lifetime() as deadline:
                        observed["forwarded"] = None  # A partial send has unknown acceptance.
                        _dispatch_bounded(connection, upstream_path, body, headers, deadline)
                    if connection.sock is not None:
                        connection.sock.settimeout(request_timeout_sec)
                observed["forwarded"] = True  # Bytes sent, not a provider acceptance receipt.
                # Observation may write to disk. Keep it outside the admitted
                # send scope and record dispatch before waiting for a response.
                observe("dispatch")
                response = connection.getresponse()
                observed["response_status"] = response.status
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() not in {
                        *_HOP_BY_HOP_HEADERS,
                        "content-length",
                    }:
                        self.send_header(key, value)
                self.end_headers()
                response_started = True
                # Forward available stream bytes without waiting to fill the buffer.
                while chunk := response.read1(64 * 1024):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except ProviderGatewayRequestRejected as error:
                observed["refusal_code"] = error.code
                self._json_error(409, error.code)
            except (OSError, http.client.HTTPException):
                if not response_started and not self.wfile.closed:
                    self._json_error(502, "provider_gateway_upstream_unavailable")
                else:
                    self.close_connection = True
            finally:
                connection.close()
                observe("finished")

        def do_GET(self) -> None:
            self._json_error(405, "provider_gateway_method_not_admitted")

    server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        name="loopx-provider-gateway",
        daemon=True,
    )
    thread.start()
    try:
        host, port = server.server_address[:2]
        rendered_host = host.decode("ascii") if isinstance(host, bytes) else str(host)
        yield RunnerOwnedProviderGateway(
            base_url=f"http://{rendered_host}:{port}{prefix}",
            allowed_paths=admitted_paths,
        )
    finally:
        server.shutdown()
        server.server_close()
        if request_lifetime is not None:
            # Interrupt old inbound waits on retirement. A handler already
            # waiting for authority still sees its original retired owner.
            with clients_lock:
                for client in tuple(clients):
                    try:
                        client.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        thread.join(timeout=5)


__all__ = [
    "RunnerOwnedProviderGateway",
    "serve_runner_owned_provider_gateway",
]
