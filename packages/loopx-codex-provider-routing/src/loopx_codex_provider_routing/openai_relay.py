"""Loopback Responses transport for the explicitly enabled API fallback.

Native login transactions stay in the packaged account owner. This transport holds only
upstream API keys, never subscription credentials, prompts or response logs.
"""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .native import NativeAccounts, atomic, private_read
from .native_host import HostError
from .openai_keys import NoRedirect

SCHEMA = "codex_openai_api_relay_v1"
API_SLOT = "openai-api"


def route(owner: NativeAccounts) -> dict:
    value = private_read(owner.root / "api-relay.json", schema=SCHEMA)
    if type(value.get("port")) is not int or not 1024 <= value["port"] <= 65535 or not isinstance(value.get("token"), str) or len(value["token"]) < 32:
        raise HostError("api_relay_unavailable")
    return value


def consumer_token(value: dict, consumer: str) -> str:
    token = value.get("steward_token" if consumer == "steward" else "token")
    if not isinstance(token, str) or not token:
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
        if not healthy(value):
            # Independent of a Chat/App process that the handoff may restart.
            subprocess.Popen([sys.executable, "-m", "loopx_codex_provider_routing.openai_relay",
                              "--runtime-root", str(owner.root.parents[1])],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not healthy(value):
                time.sleep(.1)
            if not healthy(value):
                raise HostError("api_relay_unavailable")
        if consumer == "steward" and not healthy(value, consumer):
            raise HostError("api_relay_upgrade_required")
        prefix = "/steward" if consumer == "steward" else ""
        return {"slot": API_SLOT, "api_key": consumer_token(value, consumer),
                "base_url": f"http://127.0.0.1:{value['port']}{prefix}/v1"}


class RelayServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, owner: NativeAccounts, value: dict, *, opener=None):
        self.owner, self.value = owner, value
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        super().__init__(("127.0.0.1", value["port"]), RelayHandler)


class RelayHandler(BaseHTTPRequestHandler):
    server: RelayServer

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
        if self.path in ("/health", "/steward/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "11")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self._json(404, "unsupported_route")

    def do_POST(self):
        if not self._authorized():
            self._json(401, "relay_auth_required")
            return
        upstream_path = self.path.removeprefix("/steward") if self.consumer == "steward" else self.path
        if upstream_path not in ("/v1/responses", "/v1/responses/compact"):
            self._json(404, "unsupported_route")
            return
        if not self._admitted():
            self._json(503, "api_fallback_disabled")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 32 * 1024 * 1024:
                raise ValueError()
            body = self.rfile.read(length)
            value = json.loads(body)
            model = value["model"]
            if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", model) or model.startswith("sk-"):
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            self._json(400, "invalid_request")
            return
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
                encoded = error.read(65537)
                try:
                    code = json.loads(encoded).get("error", {}).get("code") if len(encoded) <= 65536 else None
                    code = code if isinstance(code, str) and code.isascii() and code.replace("_", "").isalnum() else None
                except (TypeError, ValueError, AttributeError):
                    code = None
                headers = {k.lower(): v for k, v in error.headers.items()}
                status = error.code
                error.close()
                outcome = keys.record(row["id"], status=status, code=code, model=model, headers=headers)
                last_status, last_code = status, code or "upstream_rejected"
                if outcome["retry_next_key"]:
                    continue
                self._json(status, last_code)
                return
            except (OSError, HostError):
                # Unknown upstream effects never authorize trying another key.
                self._json(502, "upstream_unavailable")
                return
            with response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                keys.record(row["id"], status=response.status, code=None, model=model, headers=headers)
                self.send_response(response.status)
                self.send_header("Content-Type", headers.get("content-type", "text/event-stream"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                # Once headers/output are admitted, disconnects and SSE errors
                # end this request. No key rotation, synthetic completion or job replay.
                try:
                    read = getattr(response, "read1", response.read)
                    while chunk := read(8192):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (OSError, TimeoutError):
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
            server.timeout = 5
            started = time.monotonic()
            try:
                while True:
                    server.handle_request()
                    policy = owner._load()["policy"]
                    if time.monotonic() - started > 10 and policy.get("steward_api_role") != "primary" and (
                            not policy.get("enabled") or not policy.get("api_fallback")):
                        break
            finally:
                # Drain already admitted requests; disabling never replays them.
                server.server_close()
    except (BlockingIOError, OSError, HostError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
