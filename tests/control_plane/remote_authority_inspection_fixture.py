"""Disposable HTTPS peer; independently encode correlated, synthetic read replies."""

from __future__ import annotations

import hashlib
import http.server
import json
import shutil
import ssl
import subprocess
import threading
from contextlib import contextmanager

import pytest

CREDENTIAL = "synthetic-inspection-credential-" + "a" * 32


@contextmanager
def remote_authority_service(tmp_path, actor_id):
    if shutil.which("openssl") is None:
        pytest.skip("openssl required for disposable TLS certificate")
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
         "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True,
    )
    requests = []
    control = {"mode": "found", "requests": requests}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_POST(self):  # noqa: N802
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, req))
            assert req["operation"] == "read"
            assert self.headers["Authorization"] == f"Bearer {CREDENTIAL}"
            if control["mode"] == "http":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(CREDENTIAL.encode())
                return
            digest = hashlib.sha256(json.dumps(
                {"mode": "execute", "request": req}, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            packet = {
                "schema_version": "loopx_remote_authority_scoped_read_response_v1",
                "request_sha256": digest, "status": "found",
                "goal_ref": {"goal_id": req["goal_id"], "goal_instance_id": "ginst_" + "a" * 32},
                "todo": {"todo_id": req["todo_id"], "status": "open"}, "lease": None,
            }
            if control["mode"] == "denied":
                packet = {
                    "schema_version": "loopx_remote_authority_response_v0", "request_sha256": digest,
                    "status": "rejected", "reason_code": "remote_authority_scope_denied",
                }
            body = json.dumps(packet).encode() if control["mode"] != "invalid" else CREDENTIAL.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {
        "enabled": True, "endpoint": f"https://127.0.0.1:{server.server_port}",
        "tenant_id": "synthetic-inspection-tenant", "actor_id": actor_id,
        "store_identity": "postgresql:" + "b" * 32, "probe_todo_id": "todo_probe",
        "ca_file": str(cert),
    }
    try:
        yield config, control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def bind_remote_authority(registry, goal_id, config):
    document = json.loads(registry.read_text())
    next(goal for goal in document["goals"] if goal["id"] == goal_id)["remote_authority"] = config
    registry.write_text(json.dumps(document), encoding="utf-8")
