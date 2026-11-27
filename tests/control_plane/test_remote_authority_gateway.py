"""The real local CLI does not admit remote work or alter default-off decisions."""

from __future__ import annotations

import json

import pytest

from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture
from loopx.control_plane.testing.canary_harness import run_json_cli


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_cli_ignores_disabled_and_other_goal_remote_binding(tmp_path, monkeypatch, provider):
    registry, runtime, _ = write_cli_fixture(tmp_path, scoped_agents=True, provider=provider)
    # Old environment-only canary inputs cannot activate an unconfigured target.
    monkeypatch.setenv("LOOPX_REMOTE_AUTHORITY_CANARY_ENABLED", "1")
    monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", "unused-synthetic-credential-" + "a" * 32)
    goal_id = "throttled-half"
    scope = ("--goal-id", goal_id, "--agent-id", SCOPED_AGENT_ID)

    def cli(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)

    def read():
        prompt = cli("heartbeat-prompt", "--thin", "--codex-app", *scope)
        assert "remote_authority" not in prompt
        decision = cli("quota", "should-run", "--runtime-profile", "outer_controller", *scope)
        assert "remote_gateway" not in decision
        assert decision["ok"] is True
        return tuple(decision.get(key) for key in ("decision", "effective_action", "should_run", "reason"))

    def read_local(goal):
        decision = cli(
            "quota", "should-run", "--runtime-profile", "outer_controller",
            "--goal-id", goal, "--agent-id", SCOPED_AGENT_ID,
        )
        assert "remote_gateway" not in decision
        return tuple(decision.get(key) for key in ("decision", "effective_action", "should_run"))

    local = read()
    assert local[:3] == ("skip", "throttled_skip", False), local
    assert read_local("half-speed") == ("run", "normal_run", True)
    document = json.loads(registry.read_text())
    cfg = {"enabled": True, "endpoint": "https://127.0.0.1:1", "tenant_id": "other-synthetic-tenant",
           "actor_id": SCOPED_AGENT_ID, "store_identity": "postgresql:" + "a" * 32, "probe_todo_id": "todo_probe"}
    for goal in document["goals"]:
        if goal["id"] == goal_id:
            goal["remote_authority"] = {**cfg, "enabled": False}
        if goal["id"] == "full-speed":
            goal["remote_authority"] = cfg
    registry.write_text(json.dumps(document), encoding="utf-8")
    assert read() == local
    assert read_local("half-speed") == ("run", "normal_run", True)

    other = cli("heartbeat-prompt", "--full", "--codex-app",
                "--goal-id", "full-speed", "--agent-id", SCOPED_AGENT_ID)
    assert other["remote_authority"]["status"] == "unverified"
    for key in ("endpoint", "tenant_id", "actor_id", "store_identity", "probe_todo_id"):
        assert key not in other["remote_authority"]
    assert cfg["endpoint"] not in other["task_body"] and cfg["tenant_id"] not in other["task_body"]


def test_remote_authority_gateway_probe_verifies_against_standard_https_mock(tmp_path, monkeypatch):
    import hashlib
    import http.server
    import shutil
    import ssl
    import subprocess
    import threading

    from loopx.control_plane.coordination.remote_authority_gateway import (
        check_remote_authority_gateway_status,
    )

    if shutil.which("openssl") is None:
        pytest.skip("openssl required for disposable TLS certificate")

    key_path = tmp_path / "key.pem"
    cert_path = tmp_path / "cert.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout", str(key_path), "-out", str(cert_path),
        ],
        check=True,
        capture_output=True,
    )
    credential = "synthetic-gateway-credential-" + "a" * 32

    class _MockHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v0/tasks/execute" or self.headers.get("Authorization") != f"Bearer {credential}":
                self.send_response(401)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            req_digest = hashlib.sha256(
                json.dumps({"mode": "execute", "request": req}, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            payload = {
                "schema_version": "loopx_remote_authority_response_v0",
                "request_sha256": req_digest,
                "status": "found",
                "provider_revision": f"{req['store_identity']}:1",
                "todo": {"todo_id": req["todo_id"], "status": "open"},
                "lease": None,
            }
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = http.server.HTTPServer(("127.0.0.1", 0), _MockHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = {
            "enabled": True,
            "endpoint": f"https://127.0.0.1:{server.server_port}",
            "tenant_id": "synthetic-tenant",
            "actor_id": SCOPED_AGENT_ID,
            "store_identity": "postgresql:" + "b" * 32,
            "probe_todo_id": "todo_probe",
            "ca_file": str(cert_path),
        }
        monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", credential)
        verified = check_remote_authority_gateway_status("full-speed", SCOPED_AGENT_ID, cfg)
        assert verified["status"] == "verified"
        assert "endpoint" not in verified and "tenant_id" not in verified

        monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", "wrong-synthetic-credential-" + "b" * 32)
        denied = check_remote_authority_gateway_status("full-speed", SCOPED_AGENT_ID, cfg)
        assert denied["status"] == "unavailable"
        assert denied["reason_code"] == "remote_authority_read_unverified"
    finally:
        server.shutdown()
        server.server_close()
