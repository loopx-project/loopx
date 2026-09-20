from __future__ import annotations

import errno
import http.client
import json
import tempfile
import threading
from pathlib import Path

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.effect_runtime import (
    EffectRuntimePermanentIOError,
    EffectRuntimeStartupError,
    EffectRuntimeTransientIOError,
)
from loopx.extensions.lark.cli_resolution import LarkCliResolution


def _start_server() -> tuple[ChatHTTPServer, threading.Thread]:
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.selected_goal_id = None
    server.registry_path = Path("/tmp/loopx-test-registry.json")
    server.runtime_root_override = None
    # The capabilities readback quotes the steward channel's Session, so a
    # fixture server carries the store the real startup always installs.
    server.chat_store = ChatSessionStore(Path(tempfile.mkdtemp()) / "runtime")
    server.scan_roots = []
    server.limit = 20
    server.runtime_controller = _RuntimeController()
    server.lark_cli_resolution = LarkCliResolution(
        command=None,
        available=False,
        source="missing",
        version=None,
        error_code="lark_cli_not_installed",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _RuntimeController:
    def capabilities(self) -> list[dict[str, object]]:
        return []

    def close(self) -> None:
        return None


def _request(
    port: int,
    *,
    method: str,
    origin: str | None,
    path: str = "/api/chat/capabilities",
    body: bytes | None = None,
) -> http.client.HTTPResponse:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Origin": origin} if origin else {}
    connection.request(method, path, body=body, headers=headers)
    return connection.getresponse()


def test_chat_json_echoes_loopback_cors_origin() -> None:
    server, thread = _start_server()
    try:
        origin = "http://127.0.0.1:49152"
        response = _request(
            server.server_address[1],
            method="GET",
            origin=origin,
        )
        response.read()

        assert response.status == 200
        assert response.getheader("Access-Control-Allow-Origin") == origin
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_chat_capabilities_expose_public_runtime_identity() -> None:
    server, thread = _start_server()
    try:
        response = _request(
            server.server_address[1],
            method="GET",
            origin=None,
        )
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["runtime_identity"]["schema_version"] == (
            "loopx_runtime_identity_v1"
        )
        assert set(payload["runtime_identity"]) == {
            "schema_version",
            "package_version",
            "release_id",
            "source_revision",
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_chat_json_rejects_foreign_cors_origin() -> None:
    server, thread = _start_server()
    try:
        response = _request(
            server.server_address[1],
            method="GET",
            origin="https://evil.example",
        )
        response.read()

        assert response.status == 200
        assert response.getheader("Access-Control-Allow-Origin") is None
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_chat_options_exposes_loopback_preflight_only() -> None:
    server, thread = _start_server()
    try:
        origin = "http://127.0.0.1:49152"
        response = _request(
            server.server_address[1],
            method="OPTIONS",
            origin=origin,
        )
        response.read()

        assert response.status == 204
        assert response.getheader("Access-Control-Allow-Origin") == origin
        assert response.getheader("Access-Control-Allow-Methods") == (
            "GET, POST, DELETE, OPTIONS"
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_chat_post_rejects_non_finite_json_numbers(number: str) -> None:
    server, thread = _start_server()
    try:
        response = _request(
            server.server_address[1],
            method="POST",
            origin=None,
            path="/api/ssh-source/ensure",
            body=f'{{"host_alias":"","local_port":{number}}}'.encode(),
        )
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 400
        assert "request body must be strict JSON" in payload["error"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_chat_action_context_cannot_persist_or_emit_overflowed_float(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"schema_version": "0.1", "goals": [{"id": "goal-one"}]}),
        encoding="utf-8",
    )
    action_store = ChatActionStore(tmp_path / "actions")
    server, thread = _start_server()
    server.action_store = action_store
    server.action_service = ChatActionService(
        store=action_store,
        registry_path=registry_path,
    )
    try:
        response = _request(
            server.server_address[1],
            method="POST",
            origin=None,
            path="/api/actions/preview",
            body=(
                b'{"action_kind":"goal.lifecycle","summary":"Stop goal",'
                b'"normalized_parameters":{"goal_id":"goal-one","operation":"stop"},'
                b'"context":{"nested":{"overflow":1e309}},'
                b'"idempotency_key":"stop-goal-one"}'
            ),
        )
        response_body = response.read()

        assert response.status == 400
        assert b"Infinity" not in response_body
        assert json.loads(response_body)["error_code"] == "invalid_action_preview"
        assert action_store.list() == []
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "action", ["snapshot", "apply", "cancel", "regenerate", "reject", "defer"]
)
def test_missing_action_returns_the_same_http_error(
    tmp_path: Path, action: str
) -> None:
    server, thread = _start_server()
    server.action_store = ChatActionStore(tmp_path / "actions")
    server.action_service = ChatActionService(
        store=server.action_store, registry_path=tmp_path / "registry.json"
    )
    try:
        response = _request(
            server.server_address[1],
            method="GET" if action == "snapshot" else "POST",
            origin=None,
            path="/api/actions/missing"
            + ("" if action == "snapshot" else f"/{action}"),
            body=None if action == "snapshot" else b"{}",
        )
        assert response.status == 404
        assert json.loads(response.read()) == {
            "ok": False,
            "error": "typed Chat action proposal was not found",
            "error_code": "action_not_found",
        }
        assert server.action_store.list() == []
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_chat_status_forwards_valid_goal_activation_scope(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_collect_status(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "scope": kwargs.get("activation_state_filter")}

    monkeypatch.setattr("loopx.chat_status_api.collect_status", fake_collect_status)
    server, thread = _start_server()
    server.goal_subagent_configuration_enabled = True
    try:
        response = _request(
            server.server_address[1],
            method="GET",
            origin=None,
            path="/status.json?goal_activation=active",
        )
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload == {"ok": True, "scope": "active"}
        assert calls[0]["activation_state_filter"] == "active"
        assert calls[0]["include_goal_subagent_configuration"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_chat_status_rejects_invalid_goal_activation_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "loopx.chat_status_api.collect_status",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must fail before collection")),
    )
    server, thread = _start_server()
    try:
        response = _request(
            server.server_address[1],
            method="GET",
            origin=None,
            path="/status.json?goal_activation=active&goal_activation=stopped",
        )
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 400
        assert payload["error_code"] == "invalid_goal_activation"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_workspace_directory_skips_expensive_projection_and_respects_scope(monkeypatch) -> None:
    registry = {"goals": [{"id": "alpha", "display_name": "Alpha"}, {"id": "beta"}]}
    monkeypatch.setattr("loopx.chat_status_api.load_registry", lambda _: registry)
    monkeypatch.setattr("loopx.chat_status_api.collect_status", lambda **_: (_ for _ in ()).throw(AssertionError("directory must not collect status")))
    server, thread = _start_server()
    server.selected_goal_id = "alpha"
    try:
        response = _request(server.server_address[1], method="GET", origin=None, path="/status.json?view=workspace-directory")
        payload = json.loads(response.read())
        assert response.status == 200
        assert [goal["id"] for goal in payload["goals"]] == ["alpha"]
        assert payload["schema_version"] == "loopx_workspace_directory_v1"
        for query in ("goal_id=beta", "goal_id=alpha&goal_id=beta", "goal_id=", "view=unknown", "view=workspace-directory&view=workspace-directory"):
            response = _request(server.server_address[1], method="GET", origin=None, path="/status.json?" + query)
            response.read()
            assert response.status == 400
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_workspace_scoped_status_revision_and_membership_fences(monkeypatch) -> None:
    registry = {"goals": [{"id": "alpha"}, {"id": "beta"}]}
    monkeypatch.setattr("loopx.chat_status_api.load_registry", lambda _: registry)
    calls = []
    def collect(**kwargs):
        calls.append(kwargs["goal_id"])
        return {"ok": True}
    monkeypatch.setattr("loopx.chat_status_api.collect_status", collect)
    server, thread = _start_server()
    try:
        response = _request(server.server_address[1], method="GET", origin=None, path="/status.json?goal_id=alpha")
        payload = json.loads(response.read())
        assert response.status == 200
        assert calls == ["alpha"]
        assert payload["workspace_registry_revision"]
        response = _request(server.server_address[1], method="GET", origin=None, path="/status.json?goal_id=missing")
        response.read()
        assert response.status == 404
        assert calls == ["alpha"]
        def racing_collect(**kwargs):
            registry["goals"].pop()
            return {"ok": True}
        monkeypatch.setattr("loopx.chat_status_api.collect_status", racing_collect)
        response = _request(server.server_address[1], method="GET", origin=None, path="/status.json?goal_id=alpha")
        response.read()
        assert response.status == 409
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _status_failure(kind: str) -> BaseException:
    private_detail = "synthetic confidential diagnostic"
    if kind.startswith("winerror_"):
        error = PermissionError(errno.EACCES, private_detail)
        native = kind.removeprefix("winerror_")
        error.winerror = {"malformed": "5", "bool": True}.get(native, int(native) if native.isdigit() else None)
        # A non-access native error must not borrow a permission cause.
        error.__cause__ = PermissionError(private_detail)
        return error
    if kind in {"direct", "eperm"}:
        return PermissionError(errno.EPERM if kind == "eperm" else errno.EACCES, private_detail)
    if kind in {"remote_access", "remote_other", "remote_wrong_type"}:
        error_type = EffectRuntimeTransientIOError if kind == "remote_wrong_type" else EffectRuntimePermanentIOError
        error = error_type(
            private_detail,
            diagnostic_code="io_space_exhausted" if kind == "remote_other" else "io_permission_denied",
        )
        error.__cause__ = PermissionError(private_detail)
        return error
    if kind in {"node_unavailable", "runtime_startup_timeout", "runtime_launch_failed"}:
        return EffectRuntimeStartupError(private_detail, diagnostic_code=kind)
    if kind == "os_other":
        return OSError(errno.ENOSPC, private_detail)
    if kind in {"wrapped", "implicit", "suppressed", "launch_implicit"}:
        error = EffectRuntimeStartupError(private_detail, diagnostic_code=(
            "runtime_launch_failed" if kind == "launch_implicit" else "runtime_request_failed"
        ))
    else:
        error = RuntimeError(private_detail)
    if kind in {"wrapped", "explicit", "implicit", "suppressed", "unrelated_context", "launch_implicit"}:
        error.__context__ = PermissionError(private_detail)
        if kind in {"wrapped", "explicit"}:
            error.__cause__ = error.__context__
        error.__suppress_context__ = kind == "suppressed"
    elif kind == "cause_precedence":
        error.__cause__ = ValueError(private_detail)
        error.__context__ = PermissionError(private_detail)
    elif kind == "forged_code":
        error.diagnostic_code = "io_permission_denied"
    elif kind == "cycle":
        error.__cause__ = error
    elif kind in {"depth_8", "depth_9"}:
        error = PermissionError(private_detail)
        for _ in range(int(kind.split("_")[1]) - 1):
            wrapper = RuntimeError(private_detail)
            wrapper.__cause__ = error
            error = wrapper
    return error


@pytest.mark.parametrize(
    ("kind", "access"),
    [(kind, True) for kind in (
        "direct", "eperm", "wrapped", "explicit", "implicit", "remote_access",
        "winerror_5", "winerror_65", "winerror_none", "launch_implicit", "depth_8",
    )] + [(kind, False) for kind in (
        "node_unavailable", "runtime_startup_timeout", "runtime_launch_failed",
        "os_other", "remote_other", "remote_wrong_type", "unknown", "forged_code", "suppressed",
        "unrelated_context", "cause_precedence", "cycle", "depth_9",
        "winerror_0", "winerror_19", "winerror_32", "winerror_33", "winerror_malformed", "winerror_bool",
    )],
)
def test_workspace_status_access_error_contract(monkeypatch, kind: str, access: bool) -> None:
    monkeypatch.setattr("loopx.chat_status_api.load_registry", lambda _: {"goals": [{"id": "alpha"}]})
    failure = _status_failure(kind)

    def fail(**_kwargs):
        raise failure

    monkeypatch.setattr("loopx.chat_status_api.collect_status", fail)
    server, thread = _start_server()
    try:
        response = _request(server.server_address[1], method="GET", origin=None, path="/status.json?goal_id=alpha")
        expected = {"ok": False, "error": "LoopX status could not be projected for the workspace."}
        if access:
            expected["error_code"] = "workspace_status_access_denied"
        assert response.status == 500
        assert json.loads(response.read()) == expected
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("query", ["", "?view=workspace-directory", "?view=workspace-directory&goal_id=alpha"])
def test_workspace_access_code_does_not_change_other_status_routes(monkeypatch, query: str) -> None:
    def fail(*_args, **_kwargs):
        raise PermissionError("synthetic confidential diagnostic")

    monkeypatch.setattr("loopx.chat_status_api.load_registry", fail)
    monkeypatch.setattr("loopx.chat_status_api.collect_status", fail)
    server, thread = _start_server()
    try:
        response = _request(server.server_address[1], method="GET", origin=None, path="/status.json" + query)
        assert response.status == 500
        assert json.loads(response.read()) == {
            "ok": False, "error": "LoopX status could not be projected for the workspace.",
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
