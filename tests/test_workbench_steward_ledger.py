"""End-to-end Python Steward S1 tests over an authenticated AF_UNIX Workbench socket."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socketserver
import threading
from typing import Any

import pytest

from loopx.capabilities.manager_context.inspection import (
    ManagerInspection,
    READ_TOOL,
    STEWARD_LEDGER_READ_TOOL_NAME,
    STEWARD_LEDGER_TOOLS,
    STEWARD_SESSION_READ_TOOL_NAME,
    STEWARD_SESSIONS_FIND_TOOL_NAME,
    TOOL_NAME,
)
from loopx.capabilities.manager_context.workbench_ledger import (
    WORKBENCH_STEWARD_CREDENTIAL_ENV,
)


class _UnixStreamServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _start_fake_workbench_socket(sock_dir: Path, expected_token: str):
    socket_path = sock_dir / "calli.sock"
    calls: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:  # noqa: N802
            auth = self.headers.get("Authorization", "")
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            calls.append({"auth": auth, "payload": payload})
            if auth != f"Bearer {expected_token}":
                body = json.dumps({"ok": False, "error": "unauthorized_caller_role"}).encode("utf-8")
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            method = payload.get("method")
            params = payload.get("params") or {}
            if method == "steward_ledger_read":
                result = {
                    "revision": "sha256:test",
                    "ledgerCapture": True,
                    "sourceHealth": {
                        "workbenchThreads": {"status": "ok", "itemCount": 1},
                        "localSessions": {"status": "ok", "itemCount": 1},
                    },
                    "coverage": {"workbenchThreads": "complete", "localSessions": "complete"},
                    "counts": {"running": 1, "needs_user": 1, "blocked": 0, "idle_open": 0, "done_recent": 0},
                    "items": [
                        {
                            "id": "thread:th-pay",
                            "kind": "workbench_thread",
                            "phase": "needs_user",
                            "title": "支付网关重构",
                            "agent": "codex",
                            "cwd": "/repo/pay",
                            "goalId": None,
                        }
                    ],
                    "nextCursor": None,
                }
            elif method == "steward_sessions_find":
                q = params.get("query", "")
                result = {
                    "query": q,
                    "revision": "sha256:test",
                    "indexStatus": "ready",
                    "hits": [
                        {
                            "id": "local:codex:ext-cat",
                            "kind": "local_session",
                            "title": "排查黑猫告警",
                            "agent": "codex",
                            "cwd": "/repo/cat",
                            "phase": "running",
                            "snippet": f"matched:{q}",
                        }
                    ],
                }
            elif method == "steward_session_read":
                result = {
                    "id": f"local:codex:{params.get('id')}",
                    "kind": params.get("kind"),
                    "title": "排查黑猫告警",
                    "imported": False,
                    "chunks": [{"index": 0, "role": "user", "text": "帮我查黑猫 OOM"}],
                    "nextCursor": None,
                }
            else:
                result = {}

            body = json.dumps({"ok": True, "result": result}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = _UnixStreamServer(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, socket_path, calls


def _make_inspector(
    tmp_path: Path,
    *,
    owner_scope: bool = True,
    scope: str = "owner_portfolio",
):
    records: list[dict[str, Any]] = []
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "alpha"}]}), encoding="utf-8")
    inspector = ManagerInspection(
        context={"snapshot_id": "snap-1", "scope": scope, "goals": [{"goal_id": "alpha"}]},
        registry_path=registry,
        runtime_root=tmp_path,
        owner_scope=owner_scope,
        scope_valid=lambda: True,
        record=records.append,
    )
    return inspector, records


def test_private_owner_steward_reads_ledger_search_and_transcript_over_unix_socket(monkeypatch, tmp_path):
    sock_dir = Path("/tmp") / f"wb-steward-{os.getpid()}"
    sock_dir.mkdir(parents=True, exist_ok=True)
    server, socket_path, calls = _start_fake_workbench_socket(sock_dir, "secret-sup-token")
    cred_path = tmp_path / "credential.json"
    cred_path.write_text(
        json.dumps({"socketPath": str(socket_path), "token": "secret-sup-token"}),
        encoding="utf-8",
    )
    os.chmod(cred_path, 0o600)
    monkeypatch.setenv(WORKBENCH_STEWARD_CREDENTIAL_ENV, str(cred_path))

    try:
        inspector, records = _make_inspector(tmp_path, owner_scope=True, scope="owner_portfolio")

        ledger = inspector.read(STEWARD_LEDGER_READ_TOOL_NAME, {"limit": 10})
        assert ledger["ok"] is True
        assert ledger["available"] is True
        assert ledger["view"] == "workbench_ledger"
        assert ledger["items"][0]["title"] == "支付网关重构"

        found = inspector.read(STEWARD_SESSIONS_FIND_TOOL_NAME, {"query": "猫", "limit": 5})
        assert found["ok"] is True
        assert found["hits"][0]["snippet"] == "matched:猫"

        transcript = inspector.read(
            STEWARD_SESSION_READ_TOOL_NAME,
            {"kind": "local_session", "id": "ext-cat", "agent": "codex", "mode": "recent_turns"},
        )
        assert transcript["ok"] is True
        assert transcript["imported"] is False
        assert transcript["chunks"][0]["text"] == "帮我查黑猫 OOM"

        assert len(calls) == 3
        assert len(records) == 3

        # Core Goal portfolio read via loopx_manager_read remains 100% intact.
        assert [t["name"] for t in [READ_TOOL, *STEWARD_LEDGER_TOOLS]] == [
            TOOL_NAME,
            STEWARD_LEDGER_READ_TOOL_NAME,
            STEWARD_SESSIONS_FIND_TOOL_NAME,
            STEWARD_SESSION_READ_TOOL_NAME,
        ]
    finally:
        server.shutdown()
        server.server_close()
        if socket_path.exists():
            socket_path.unlink()
        sock_dir.rmdir()


@pytest.mark.parametrize(
    "owner_scope, scope",
    [
        (False, "external_audience"),
        (True, "owner_goal"),
        (False, "owner_goal"),
    ],
)
def test_non_owner_and_goal_scoped_conversations_fail_closed_without_touching_socket(
    monkeypatch, tmp_path, owner_scope, scope
):
    monkeypatch.setenv(WORKBENCH_STEWARD_CREDENTIAL_ENV, str(tmp_path / "must-not-read.json"))
    inspector, records = _make_inspector(tmp_path, owner_scope=owner_scope, scope=scope)

    for tool_name, args in [
        (STEWARD_LEDGER_READ_TOOL_NAME, {}),
        (STEWARD_SESSIONS_FIND_TOOL_NAME, {"query": "猫"}),
        (STEWARD_SESSION_READ_TOOL_NAME, {"kind": "workbench_thread", "id": "th-1"}),
    ]:
        res = inspector.read(tool_name, args)
        assert res == {"ok": False, "error": "workbench_sessions_require_private_owner_scope"}
    assert not records


def test_forged_socket_token_returns_403_and_stopped_workbench_returns_explicit_unavailable(
    monkeypatch, tmp_path
):
    inspector, records = _make_inspector(tmp_path, owner_scope=True, scope="owner_portfolio")

    # 1. Stopped / unconfigured Workbench returns honest unavailable source_health, never empty-as-complete.
    monkeypatch.delenv(WORKBENCH_STEWARD_CREDENTIAL_ENV, raising=False)
    monkeypatch.delenv("LOOCHAT_DATA_DIR", raising=False)
    monkeypatch.delenv("WORKBENCH_STATE_DIR", raising=False)
    offline = inspector.read(STEWARD_LEDGER_READ_TOOL_NAME, {})
    assert offline["ok"] is True
    assert offline["available"] is False
    assert offline["source_health"]["workbench"]["status"] == "unavailable"
    assert offline["coverage"]["local_sessions"] == "unavailable"

    # 2. Bad bearer token gets 403 unauthorized_caller_role from Workbench socket.
    sock_dir = Path("/tmp") / f"wb-steward-acl-{os.getpid()}"
    sock_dir.mkdir(parents=True, exist_ok=True)
    server, socket_path, _ = _start_fake_workbench_socket(sock_dir, "real-token")
    cred_path = tmp_path / "forged-cred.json"
    cred_path.write_text(
        json.dumps({"socketPath": str(socket_path), "token": "wrong-worker-token"}),
        encoding="utf-8",
    )
    monkeypatch.setenv(WORKBENCH_STEWARD_CREDENTIAL_ENV, str(cred_path))
    try:
        denied = inspector.read(STEWARD_LEDGER_READ_TOOL_NAME, {})
        assert denied == {
            "ok": False,
            "view": "workbench_ledger",
            "error": "unauthorized_caller_role",
        }
    finally:
        server.shutdown()
        server.server_close()
        if socket_path.exists():
            socket_path.unlink()
        sock_dir.rmdir()


def test_invalid_steward_ledger_arguments_return_mechanical_repair_hints(tmp_path):
    inspector, records = _make_inspector(tmp_path, owner_scope=True, scope="owner_portfolio")
    bad = inspector.read(STEWARD_SESSIONS_FIND_TOOL_NAME, {"limit": 999, "extra": 1})
    assert bad["ok"] is False
    assert bad["error"] == "invalid_arguments"
    assert "unknown_argument:extra" in bad["rejected_arguments"]
    assert "query:required" in bad["rejected_arguments"]
    assert "limit:must_be_an_integer_between_1_and_50" in bad["rejected_arguments"]
    assert not records
