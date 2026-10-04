"""Allowlisted CONNECT relay for OAuth Codex behind a host HTTP proxy.

The task container only reaches this listener. TLS remains end-to-end; request
bodies and credentials are never decoded or logged here.
"""

from __future__ import annotations

import argparse
import base64
import os
import select
import socket
import socketserver
from urllib.parse import urlsplit


def connect_target(line: bytes, allowed_hosts: frozenset[str]) -> str | None:
    try:
        method, target, version = line.decode("ascii").strip().split(" ")
        hostname, port = target.rsplit(":", 1)
    except (ValueError, UnicodeError):
        return None
    if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"}:
        return None
    if hostname.lower() not in allowed_hosts or port != "443":
        return None
    return hostname.lower() + ":443"


class ConnectProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, *, allowed_hosts, upstream):
        self.allowed_hosts = frozenset(allowed_hosts)
        self.upstream = urlsplit(upstream)
        if self.upstream.scheme != "http" or not self.upstream.hostname:
            raise ValueError("An explicit HTTP upstream proxy is required")
        super().__init__(address, ConnectHandler)


class ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(30)
        header = bytearray()
        try:
            while b"\r\n\r\n" not in header and len(header) < 16384:
                chunk = self.request.recv(1)
                if not chunk:
                    return
                header.extend(chunk)
            target = connect_target(bytes(header).split(b"\r\n", 1)[0], self.server.allowed_hosts)
            if target is None or not header.endswith(b"\r\n\r\n"):
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            upstream = self.server.upstream
            with socket.create_connection((upstream.hostname, upstream.port or 80), timeout=30) as remote:
                auth = ""
                if upstream.username is not None:
                    value = base64.b64encode(f"{upstream.username}:{upstream.password or ''}".encode()).decode()
                    auth = f"Proxy-Authorization: Basic {value}\r\n"
                remote.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{auth}\r\n".encode())
                response = bytearray()
                while b"\r\n\r\n" not in response and len(response) < 16384:
                    part = remote.recv(1)
                    if not part:
                        break
                    response.extend(part)
                first_line = bytes(response).split(b"\r\n", 1)[0].split()
                if (not response.endswith(b"\r\n\r\n") or
                        len(first_line) < 2 or first_line[1] != b"200"):
                    self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                    return
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                self.request.settimeout(None)
                remote.settimeout(None)
                while True:
                    ready, _, _ = select.select([self.request, remote], [], [], 300)
                    if not ready:
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (remote if source is self.request else self.request).sendall(data)
        except OSError:
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--allow-host", action="append", required=True)
    args = parser.parse_args()
    upstream = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not upstream:
        parser.error("Set HTTPS_PROXY or HTTP_PROXY for the host upstream")
    with ConnectProxy((args.host, args.port), allowed_hosts=args.allow_host, upstream=upstream) as proxy:
        proxy.serve_forever()


if __name__ == "__main__":
    main()
