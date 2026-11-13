"""Loopback Responses transport for the explicitly enabled API fallback.

Native login transactions stay in the packaged account owner. This transport holds only
upstream API keys, never subscription credentials, prompts or response logs.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from .native import NativeAccounts, atomic, private_read
from .native_host import HostError
from .openai_keys import NoRedirect, api_policy

SCHEMA = "codex_openai_api_relay_v1"
API_SLOT = "openai-api"
RUNTIME_SCHEMA = "codex_openai_api_relay_runtime_v1"
REQUEST_IDLE_TIMEOUT = 10
ERROR_BODY_LIMIT = 65536
# Loaded once. Replacing an installed wheel cannot make its old daemon claim
# the newly installed implementation. No credential or request data is hashed.
BUILD = hashlib.sha256(b"".join(Path(__file__).with_name(name).read_bytes() for name in (
    "openai_relay.py", "relay_lifecycle.py", "provider.py", "native.py", "native_host.py",
    "native_policy.ts", "native_policy_bridge.ts", "openai_keys.py",
    "openai_policy.ts", "openai_policy_bridge.ts", "native-extension.toml"))).hexdigest()


class _StreamOutcome:
    """Bounded, transient SSE framing; the existing TS owner classifies errors."""
    def __init__(self):
        self.buffer = b""
        self.discard = False
        self.finished = False

    def feed(self, chunk: bytes):
        if self.finished:
            return None
        self.buffer += chunk
        while match := re.search(rb"\r?\n\r?\n", self.buffer):
            frame, self.buffer = self.buffer[:match.start()], self.buffer[match.end():]
            skipped, self.discard = self.discard, False
            if skipped or len(frame) > 65536:
                continue
            data = b"\n".join(line[5:].removeprefix(b" ") for line in frame.splitlines()
                              if line.startswith(b"data:"))
            try:
                event = json.loads(data)
                # Ordinary token events need no policy subprocess or retained payload.
                if not isinstance(event, dict) or event.get("type") not in ("error", "response.failed"):
                    continue
                outcome = api_policy({"operation": "stream-outcome", "event": event})
            except (ValueError, OSError, HostError, subprocess.SubprocessError):
                continue  # Observation failure never changes the original response.
            if outcome is not None:
                self.finished = True
                self.buffer = b""
                return outcome
        if len(self.buffer) > 65536:
            # Keep a possible split delimiter while skipping this oversized event.
            self.buffer, self.discard = self.buffer[-3:], True
        return None


def route(owner: NativeAccounts) -> dict:
    value = private_read(owner.root / "api-relay.json", schema=SCHEMA)
    if type(value.get("port")) is not int or not 1024 <= value["port"] <= 65535 or not isinstance(value.get("token"), str) or len(value["token"]) < 32:
        raise HostError("api_relay_unavailable")
    return value


def consumer_token(value: dict, consumer: str) -> str:
    token = value.get("steward_token" if consumer == "steward" else "token")
    if not isinstance(token, str) or not token or (
        consumer == "steward" and (
            token == value.get("token") or any(not 33 <= ord(char) <= 126 for char in token)
        )
    ):
        raise HostError("api_relay_upgrade_required")
    return token


def healthy(value: dict, consumer: str = "worker") -> bool:
    prefix = "/steward" if consumer == "steward" else ""
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{value['port']}{prefix}/health", headers={
            "Authorization": "Bearer " + consumer_token(value, consumer)})
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=1) as response:
            return response.read(128) == b'{"ok":true}'
    except (OSError, HostError):
        return False


def ensure(owner: NativeAccounts, *, consumer: str = "worker") -> dict:
    if consumer not in ("worker", "steward"):
        raise HostError("invalid_policy")
    with owner._lock(name="api-relay-state.lock", wait_seconds=5):
        path = owner.root / "api-relay.json"
        if not path.exists():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            atomic(path, {"schema_version": SCHEMA, "port": port, "token": "sk-loopx-relay-" + secrets.token_urlsafe(32)})
        value = route(owner)
        if consumer == "steward" and "steward_token" not in value:
            # Independent random credentials: a worker token must not be able
            # to derive the steward's API-primary transport credential.
            value["steward_token"] = "sk-loopx-relay-" + secrets.token_urlsafe(32)
            atomic(path, value)
        from .relay_lifecycle import reconcile
        reconcile(owner, value, consumer=consumer)
        prefix = "/steward" if consumer == "steward" else ""
        return {"slot": API_SLOT, "api_key": consumer_token(value, consumer),
                "base_url": f"http://127.0.0.1:{value['port']}{prefix}/v1"}


class RelayServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, owner: NativeAccounts, value: dict, *, opener=None):
        self.owner, self.value = owner, value
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.instance_id = secrets.token_hex(16)
        self._admission_lock = threading.Lock()
        self._receiving: set[socket.socket] = set()
        self.draining, self.active_requests = False, 0
        self.started = time.monotonic()
        super().__init__(("127.0.0.1", value["port"]), RelayHandler)

    def process_request(self, request, client_address):
        with self._admission_lock:
            self._receiving.add(request)
        super().process_request(request, client_address)

    def shutdown_request(self, request):
        with self._admission_lock:
            self._receiving.discard(request)
        super().shutdown_request(request)

    def server_close(self):
        with self._admission_lock:
            self.draining = True
            # Stop reads on connections still outside model admission. Keep their
            # write half open for the control acknowledgement and error responses.
            for request in self._receiving:
                try:
                    request.shutdown(socket.SHUT_RD)
                except OSError:
                    pass
        super().server_close()

    def runtime(self):
        with self._admission_lock:
            return {"schema_version": RUNTIME_SCHEMA, "build": BUILD,
                    "instance_id": self.instance_id, "pid": os.getpid(),
                    "accepting": not self.draining, "active_requests": self.active_requests}

    def begin_drain(self, instance_id):
        with self._admission_lock:
            if instance_id != self.instance_id:
                return False
            if not self.draining:
                self.draining = True
                # shutdown must run outside serve_forever's serving thread.
                threading.Thread(target=self.shutdown, daemon=True).start()
        return True

    def service_actions(self):
        if time.monotonic() - self.started > 10:
            policy = self.owner._load()["policy"]
            if policy.get("steward_api_role") != "primary" and (
                    not policy.get("enabled") or not policy.get("api_fallback")):
                self.begin_drain(self.instance_id)


class RelayHandler(BaseHTTPRequestHandler):
    server: RelayServer
    timeout = REQUEST_IDLE_TIMEOUT

    def log_message(self, *_args):
        pass

    def _json(self, status: int, code: str):
        data = json.dumps({"error": {"type": "api_fallback_error", "code": code,
                                    "message": "OpenAI API fallback request could not complete."}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        self.consumer = "steward" if self.path.startswith("/steward/") else "worker"
        try:
            return not self.headers.get("Origin") and hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + consumer_token(self.server.value, self.consumer))
        except HostError:
            return False

    def _admitted(self):
        state = self.server.owner._load()
        policy = state["policy"]
        native = state.get("native", {})
        if self.consumer == "steward":
            role = policy.get("steward_api_role")
            if role == "primary":
                return True
            if role == "disabled":
                return False
        selecting_api = state.get("intent", {}).get("target") == API_SLOT if state.get("intent") else False
        return (policy.get("enabled") is True and policy.get("api_fallback") is True
                and (native.get("active_slot") == API_SLOT or selecting_api))

    def do_GET(self):
        if not self._authorized():
            self._json(401, "relay_auth_required")
            return
        if self.path in ("/runtime", "/steward/runtime"):
            data = json.dumps(self.server.runtime()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path in ("/health", "/steward/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "11")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self._json(404, "unsupported_route")

    def do_POST(self):
        if self.path == "/control/drain":
            token = self.server.value.get("control_token")
            if (self.headers.get("Origin") or not isinstance(token, str) or not token or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + token)):
                self._json(401, "relay_auth_required")
                return
            # No body is read: the pinned instance is a bounded header value.
            if not self.server.begin_drain(self.headers.get("X-LoopX-Instance")):
                self._json(409, "relay_instance_changed")
                return
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._authorized():
            self._json(401, "relay_auth_required")
            return
        with self.server._admission_lock:
            draining = self.server.draining
        if draining:
            self._json(503, "api_relay_draining")
            return
        upstream_path = self.path.removeprefix("/steward") if self.consumer == "steward" else self.path
        if upstream_path not in ("/v1/responses", "/v1/responses/compact"):
            self._json(404, "unsupported_route")
            return
        if not self._admitted():
            self._json(503, "api_fallback_disabled")
            return
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or "Transfer-Encoding" in self.headers:
                raise ValueError()
            declared = lengths[0].strip(" \t")
            if not re.fullmatch(r"[0-9]+", declared):
                raise ValueError()
            length = int(declared)
            if not 1 <= length <= 32 * 1024 * 1024:
                raise ValueError()
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError()
            value = json.loads(body)
            model = value["model"]
            if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", model) or model.startswith("sk-"):
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            self._json(400, "invalid_request")
            return
        with self.server._admission_lock:
            admitted = not self.server.draining
            if admitted:
                self.server._receiving.discard(self.connection)
                self.server.active_requests += 1
        if not admitted:
            self._json(503, "api_relay_draining")
            return
        try:
            # The request idle timeout applies only before model admission.
            self.connection.settimeout(None)
            self._forward(upstream_path, body, model)
        finally:
            with self.server._admission_lock:
                self.server.active_requests -= 1

    def _forward(self, upstream_path, body, model):
        keys = self.server.owner.api_keys()
        candidates = keys.candidates(model)
        last_status, last_code = 429, "api_keys_unavailable"
        for row in candidates:
            # Removing/disabling configuration before dispatch wins. A request
            # already admitted upstream is never cancelled or replayed here.
            if not self._admitted():
                self._json(503, "api_fallback_disabled")
                return
            current = next((r for r in keys._load()["keys"] if r["id"] == row["id"]), None)
            if current is None:
                continue
            request = urllib.request.Request("https://api.openai.com" + upstream_path, data=body, headers={
                "Authorization": "Bearer " + current["api_key"], "Content-Type": "application/json",
                "Accept": self.headers.get("Accept", "text/event-stream"),
            }, method="POST")
            try:
                response = self.server.opener.open(request, timeout=300)
            except urllib.error.HTTPError as error:
                with error:
                    try:
                        encoded = error.read(ERROR_BODY_LIMIT + 1)
                    except (OSError, HTTPException):
                        encoded = None
                    remaining = getattr(error.fp, "length", None)
                    # A bounded read can silently reach early EOF, and a
                    # body larger than our limit cannot be verified in full.
                    if encoded is None or len(encoded) > ERROR_BODY_LIMIT or (remaining or 0) > 0:
                        self._json(502, "upstream_unavailable")
                        return
                try:
                    code = json.loads(encoded).get("error", {}).get("code")
                    code = code if isinstance(code, str) and code.isascii() and code.replace("_", "").isalnum() else None
                except (TypeError, ValueError, AttributeError):
                    code = None
                headers = {k.lower(): v for k, v in error.headers.items()}
                status = error.code
                outcome = keys.record(row["id"], status=status, code=code, model=model, headers=headers)
                last_status, last_code = status, code or "upstream_rejected"
                if outcome["retry_next_key"]:
                    continue
                self._json(status, last_code)
                return
            except (OSError, HTTPException, HostError):
                # Unknown upstream effects never authorize trying another key.
                self._json(502, "upstream_unavailable")
                return
            with response:
                headers = {k.lower(): v for k, v in response.headers.items()}

                def observe(status: int, code: str | None = None):
                    try:
                        keys.record(row["id"], status=status, code=code, model=model, headers=headers)
                    except (OSError, ValueError, HostError, subprocess.SubprocessError):
                        # Once admitted, an observation failure must not
                        # replace or discard the upstream's response.
                        pass

                observe(response.status)
                # urllib removes the upstream chunk framing. Use the decoded
                # length when known; otherwise frame the HTTP/1.1 response
                # so a read failure cannot look like clean EOF.
                can_have_body = response.status not in (204, 304)
                length = getattr(response, "length", None)
                chunked = can_have_body and length is None and self.request_version == "HTTP/1.1"
                if chunked:
                    self.protocol_version = "HTTP/1.1"
                self.send_response(response.status)
                self.send_header("Content-Type", headers.get("content-type", "text/event-stream"))
                self.send_header("Cache-Control", "no-store")
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("Connection", "close")
                elif can_have_body and length is not None:
                    self.send_header("Content-Length", str(length))
                self.end_headers()
                if not can_have_body:
                    return
                # Once headers/output are admitted, disconnects and SSE errors
                # end this request. No key rotation, synthetic completion or job replay.
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                observer = _StreamOutcome() if content_type == "text/event-stream" else None
                try:
                    read = getattr(response, "read1", response.read)
                    while chunk := read(8192):
                        outcome = observer.feed(chunk) if observer is not None else None
                        if outcome is not None:
                            observe(outcome["status"], outcome["code"])
                        if chunked:
                            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        else:
                            self.wfile.write(chunk)
                        self.wfile.flush()
                    if chunked:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                except (OSError, HTTPException):
                    self.close_connection = True
                return
        self._json(last_status, last_code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    owner = NativeAccounts(parser.parse_args().runtime_root)
    try:
        with owner._lock(name="api-relay.lock"):
            server = RelayServer(owner, route(owner))
            try:
                server.serve_forever(poll_interval=5)
            except KeyboardInterrupt:
                pass  # A qualified legacy migration requests normal SIGINT.
            finally:
                # Drain already admitted requests; disabling never replays them.
                server.server_close()
    except (BlockingIOError, OSError, HostError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
