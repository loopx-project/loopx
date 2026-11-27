"""The App's explicit read uses the same typed inspection owner as the CLI."""

import json
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from tests.control_plane.remote_authority_inspection_fixture import (
    CREDENTIAL, bind_remote_authority, remote_authority_service,
)

PATH = "/api/chat/goal-configuration/remote-authority/inspect"


@contextmanager
def inspection_api(registry, runtime, *, assets=None):
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.verbose = registry, runtime, False
    if assets is not None:
        server.assets_dir = assets
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def inspect(origin, body, *, browser_origin=None):
    request = Request(origin + PATH, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json", "Origin": browser_origin or origin})
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def test_app_inspects_bound_actor_without_mutation_and_recovers(tmp_path, monkeypatch):
    registry, runtime, _ = write_cli_fixture(tmp_path, scoped_agents=True)
    monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", CREDENTIAL)
    with remote_authority_service(tmp_path, SCOPED_AGENT_ID) as (cfg, control), inspection_api(registry, runtime) as origin:
        bind_remote_authority(registry, "full-speed", cfg)
        before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        for mode, reason in (("found", "read_verified"), ("denied", "scope_denied"), ("found", "read_verified")):
            control["mode"] = mode
            status, result = inspect(origin, {"goal_id": "full-speed"})
            assert status == 200
            assert result["goal_id"] == "full-speed"
            assert result["inspection"]["reason_code"] == f"remote_authority_{reason}"
            assert result["inspection"]["execution_admitted"] is False
            assert CREDENTIAL not in json.dumps(result) and cfg["endpoint"] not in json.dumps(result)
            assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        assert len(control["requests"]) == 3
        assert all(req[1]["actor_id"] == SCOPED_AGENT_ID for req in control["requests"])
        _, disabled = inspect(origin, {"goal_id": "half-speed"})
        assert disabled["inspection"]["reason_code"] == "remote_authority_disabled"
        assert len(control["requests"]) == 3


@pytest.mark.parametrize("extra", [{"endpoint": "https://example.invalid"}, {"credential": "secret"},
                                  {"agent_id": "other"}, {"registry_path": "private-path"}])
def test_app_rejects_binding_overrides_and_foreign_origins(tmp_path, monkeypatch, extra):
    registry, runtime, _ = write_cli_fixture(tmp_path, scoped_agents=True)
    monkeypatch.setenv("LOOPX_AUTHORITY_CREDENTIAL", CREDENTIAL)
    with remote_authority_service(tmp_path, SCOPED_AGENT_ID) as (cfg, control), inspection_api(registry, runtime) as origin:
        bind_remote_authority(registry, "full-speed", cfg)
        status, _ = inspect(origin, {"goal_id": "full-speed", **extra})
        assert status == 400
        status, _ = inspect(origin, {"goal_id": "full-speed"}, browser_origin="https://example.invalid")
        assert status == 403
        assert control["requests"] == []
