"""Runner-owned HTTP gateway for benchmark model-provider calls.

The gateway keeps the upstream bearer credential in the runner process and
exposes only a loopback Responses API endpoint to an isolated agent runtime.
It deliberately is not an agent sandbox: callers must place the app-server in
an OS authority boundary that cannot inspect the runner process or its files.
"""

from __future__ import annotations

import http.client
import json
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import SplitResult, urlsplit


@dataclass(frozen=True)
class RunnerOwnedProviderGateway:
    """Public-safe handle for one bounded loopback gateway."""

    base_url: str
    allowed_paths: tuple[str, ...]


_DEFAULT_ALLOWED_PATHS = ("/responses", "/responses/compact")
# An authority lifetime may hold the Goal writer lock (whose ordinary wait is
# five seconds). Bound its network send independently of long model responses;
# gateways without an authority lifetime retain their existing timeout policy.
_GUARDED_SEND_TIMEOUT_SEC = 1.0
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
    """The caller's existing admission owner refused this new provider effect."""


def _observed_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) else None


def _native_request_identity(headers):
    # Codex supplies Turn metadata separately from the Responses body. Decode
    # only the identity fields observed at the native request boundary; never
    # retain its installation, user, model, prompt or environment metadata.
    raw = headers.get("x-codex-turn-metadata", "")
    try:
        metadata = json.loads(raw) if len(raw) <= 8192 else {}
    except (ValueError, TypeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    def identity(*values):
        observed = {_observed_id(value) for value in values} - {None}
        return next(iter(observed)) if len(observed) == 1 else None
    return {
        "native_session_id": identity(headers.get("session-id"), headers.get("session_id"), metadata.get("session_id")),
        "native_thread_id": identity(headers.get("thread-id"), metadata.get("thread_id")),
        "native_turn_id": identity(headers.get("x-codex-turn-id"), metadata.get("turn_id")),
    }


def _validated_upstream(value: str) -> SplitResult:
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("upstream_base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("upstream_base_url must not contain credentials or query data")
    if parsed.scheme == "http" and parsed.hostname not in {
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
    parsed: SplitResult, *, timeout_sec: float, proxy: SplitResult | None = None
) -> http.client.HTTPConnection:
    host = parsed.hostname
    if host is None:  # Defensive narrowing; _validated_upstream rejects this.
        raise ValueError("upstream_base_url must include a host")
    port = parsed.port
    if proxy is not None:
        if parsed.scheme == "https":
            connection = http.client.HTTPSConnection(proxy.hostname, proxy.port or 80, timeout=timeout_sec)
            connection.set_tunnel(host, port or 443)
            return connection
        return http.client.HTTPConnection(proxy.hostname, proxy.port or 80, timeout=timeout_sec)
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout_sec)
    return http.client.HTTPConnection(host, port, timeout=timeout_sec)


@contextmanager
def _bounded_request_send(connection: http.client.HTTPConnection, timeout_sec: float):
    """Give headers and the in-memory body one shared transport deadline."""
    sock = connection.sock
    if sock is None:
        raise http.client.NotConnected()
    deadline = time.monotonic() + timeout_sec
    original_timeout, original_send = sock.gettimeout(), connection.send

    def send(data):
        # HTTPConnection supplies encoded headers and our in-memory bytes body.
        # Keep its audit event, but own the partial-write loop: SSLSocket's
        # sendall can otherwise give successive SSL writes a fresh timeout.
        sys.audit("http.client.send", connection, data)
        with memoryview(data) as view:
            offset = 0
            while offset < len(view):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("provider request send deadline exhausted")
                sock.settimeout(remaining)
                sent = sock.send(view[offset:])
                if sent <= 0:
                    raise ConnectionError("provider connection closed during send")
                offset += sent

    connection.send = send
    try:
        yield
    finally:
        connection.send = original_send
        if connection.sock is sock:
            sock.settimeout(original_timeout)


@contextmanager
def serve_runner_owned_provider_gateway(
    *,
    upstream_base_url: str,
    upstream_bearer_token: str,
    allowed_paths: Sequence[str] = _DEFAULT_ALLOWED_PATHS,
    request_timeout_sec: float = 600,
    max_request_bytes: int = 64 * 1024 * 1024,
    upstream_proxy_url: str | None = None,
    request_lifetime: Callable[[dict], AbstractContextManager] | None = None,
    observe_request: Callable[[dict], None] | None = None,
) -> Iterator[RunnerOwnedProviderGateway]:
    """Serve a loopback-only Responses API gateway for one benchmark run.

    The returned URL and request surface contain no upstream credential. The
    caller owns the stronger process/filesystem boundary between this gateway
    and the agent runtime.

    A request_lifetime factory captures the caller binding before connection
    setup; its context is entered only for the admitted POST send. The factory
    must not retain authority locks while DNS/TCP/CONNECT/TLS can block.
    Guarded headers and body share a one-second send budget; response reads
    still use request_timeout_sec, as do unguarded requests.
    """

    upstream = _validated_upstream(upstream_base_url)
    proxy = urlsplit(upstream_proxy_url) if upstream_proxy_url else None
    if proxy is not None and (proxy.scheme != "http" or not proxy.hostname or proxy.username or proxy.password
                             or proxy.query or proxy.fragment or proxy.path not in ("", "/")):
        raise ValueError("upstream proxy must be an explicit credential-free HTTP proxy")
    token = str(upstream_bearer_token)
    if not token.strip():
        raise ValueError("upstream_bearer_token must be non-empty")
    if request_timeout_sec <= 0 or max_request_bytes <= 0:
        raise ValueError("gateway limits must be positive")
    admitted_paths = _validated_allowed_paths(allowed_paths)

    class GatewayHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

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
            if requested.path not in admitted_paths:
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
            request_id = "request-" + uuid.uuid4().hex
            observed = {"request_id": request_id, "path": requested.path,
                # Only explicitly received transport identities; no inference
                # from timestamps, messages, tokens or request body contents.
                "client_request_id": _observed_id(self.headers.get("x-client-request-id") or self.headers.get("x-request-id")),
                **_native_request_identity(self.headers),
                "upstream_request_id": None, "forwarded": False, "status": None,
                "response_bytes": 0}
            started = time.monotonic()
            upstream_path = f"{upstream.path.rstrip('/')}{requested.path}"
            if requested.query:
                upstream_path = f"{upstream_path}?{requested.query}"
            if proxy is not None and upstream.scheme == "http":
                upstream_path = f"{upstream.scheme}://{upstream.netloc}{upstream_path}"
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
            if observe_request is not None:
                headers["X-LoopX-Request-Id"] = request_id
            connection = _upstream_connection(upstream, timeout_sec=request_timeout_sec, proxy=proxy)
            response_started = False
            try:
                lifetime = request_lifetime(observed) if request_lifetime is not None else nullcontext()
                if request_lifetime is not None:
                    # Pin the request's owner before any slow connection work,
                    # then leave DNS/TCP/CONNECT/TLS outside its authority lock.
                    # Reconnection during POST would reintroduce that lock hold.
                    connection.connect()
                    connection.auto_open = 0
                with lifetime:
                    # Keep the caller's original lifetime/authority through the
                    # actual send, not through the potentially long response.
                    observed["forwarded"] = None  # A send failure may still have reached the upstream.
                    if observe_request is not None:
                        observe_request({**observed, "event": "dispatch"})
                    send_lifetime = (_bounded_request_send(connection,
                        min(request_timeout_sec, _GUARDED_SEND_TIMEOUT_SEC))
                        if request_lifetime is not None else nullcontext())
                    with send_lifetime:
                        connection.request("POST", upstream_path, body=body, headers=headers)
                    observed["forwarded"] = True
                response = connection.getresponse()
                observed["status"] = response.status
                observed["upstream_request_id"] = _observed_id(response.getheader("x-request-id"))
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() not in {
                        *_HOP_BY_HOP_HEADERS,
                        "content-length",
                    }:
                        self.send_header(key, value)
                self.end_headers()
                response_started = True
                while chunk := response.read1(64 * 1024):
                    observed["response_bytes"] += len(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except ProviderGatewayRequestRejected:
                observed["status"] = 409
                self._json_error(409, "provider_gateway_current_admission_refused")
            except (OSError, http.client.HTTPException):
                if not response_started and not self.wfile.closed:
                    self._json_error(502, "provider_gateway_upstream_unavailable")
                else:
                    self.close_connection = True
            finally:
                connection.close()
                if observe_request is not None:
                    observe_request({**observed, "event": "finished", "elapsed_seconds": time.monotonic() - started})

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
            base_url=f"http://{rendered_host}:{port}",
            allowed_paths=admitted_paths,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


__all__ = [
    "RunnerOwnedProviderGateway",
    "serve_runner_owned_provider_gateway",
]
