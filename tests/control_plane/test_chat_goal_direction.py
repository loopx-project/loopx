"""Operator UI transport to real source/authority; cannot write peer usage."""
from __future__ import annotations

import http.client
import json
import threading
from urllib.parse import urlencode

import pytest

from test_goal_amendment_authority import source_goal  # noqa: F401
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import restart_effect_runtime


@pytest.fixture
def api(source_goal, tmp_path):  # noqa: F811 - imported pytest fixture.
    f = source_goal
    material = f["registry"].parent.parent / "direction.md"
    material.write_text("# Direction\nPreserve the original Goal.\n")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root = f["registry"], f["runtime"]
    server.runtime_root_override = str(f["runtime"])
    server.selected_goal_id = f["ref"]["goal_id"]
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(body=None, *, query=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=90)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        path = "/api/chat/goal-direction"
        if body is None:
            path += "?" + urlencode(query or {"goal_id": f["ref"]["goal_id"], "agent_id": "agent-a"})
        connection.request("POST" if body else "GET", path, body=json.dumps(body) if body else None, headers=headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        assert str(tmp_path) not in json.dumps(result) and material.read_text() not in json.dumps(result)
        return response.status, result

    yield call, f, material, server
    server.shutdown()
    thread.join(5)
    server.server_close()
    restart_effect_runtime()


def configuration(call, f):
    view = call()[1]
    return {**f["ref"], "operation": "configure", "operation_id": "ui-declare", "execute": False,
        "parameters": {"expected_provider_revision": view["provider_revision"], "materials": [{"material_id": "contract", "source_path": "direction.md",
            "purpose": "Preserve direction", "topics": ["architecture"], "boundary": "local_private", "required": True}]}}


def test_operator_declaration_preview_freshness_replay_and_disable(api):
    call, f, material, _ = api
    request = configuration(call, f)
    code, preview = call(request)
    assert code == 200 and preview["ok"] and preview["status"] == "planned", preview
    assert call()[1]["enabled"] is False
    material.write_text("# Updated\nPreserve all requirements and new dependency.\n")
    stale = {**request, "execute": True, "preview_digest": preview["authority_digest"]}
    code, refused = call(stale)
    assert code == 200 and refused["ok"] is False, refused
    assert call()[1]["enabled"] is False
    preview = call(request)[1]
    applied_request = {**request, "execute": True, "preview_digest": preview["authority_digest"]}
    code, applied = call(applied_request)
    assert code == 200 and applied["ok"] and applied["status"] == "applied", applied
    current = call()[1]
    assert current["projection"]["direction_state"] == "blocked"
    assert current["projection"]["items"][0]["state"] == "required_unread"
    replay = call(applied_request)[1]
    assert replay["status"] == "replayed" and replay["original_receipt"] == applied["original_receipt"]
    historical = call(query={**f["ref"], "operation_id": request["operation_id"]})[1]
    assert historical["status"] == "found" and historical["historical_only"]
    assert historical["receipts"][0]["request_sha256"] == preview["reviewed_request_sha256"]
    disabled = call({**f["ref"], "operation": "disable", "operation_id": "disable", "execute": True,
        "parameters": {"expected_provider_revision": current["provider_revision"]}})[1]
    assert disabled["ok"]
    final = call()[1]
    assert final["projection"] is None and final["authority_registry"] == current["authority_registry"]


def test_operator_cannot_acknowledge_for_peer_or_change_source_route(api):
    call, f, _, server = api
    request = configuration(call, f)
    before = call()[1]["provider_revision"]
    for patch in [{"agent_id": "agent-a"}, {"operation": "acknowledge"}, {"operation": "read"},
                  {"registry_path": "/tmp/foreign"}, {"goal_instance_id": "ginst_" + "b" * 32}]:
        assert call({**request, **patch})[0] == 400
    assert call(request, origin="https://foreign.example")[0] == 403
    server.selected_goal_id = "another"
    assert call(request)[0] == 400 and call()[0] == 400
    server.selected_goal_id = f["ref"]["goal_id"]
    assert call()[1]["provider_revision"] == before
