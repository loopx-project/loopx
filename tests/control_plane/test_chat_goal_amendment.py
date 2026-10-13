"""Packaged HTTP owner path with isolated, source-registered File/SQLite Goals."""
from __future__ import annotations

import copy
import http.client
import json
import threading
from urllib.parse import urlencode

import pytest

from test_goal_amendment_authority import source_goal, _root  # noqa: F401
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import restart_effect_runtime


@pytest.fixture
def api(source_goal, tmp_path):  # noqa: F811 - pytest fixture imported above.
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = source_goal["registry"]
    server.runtime_root = source_goal["runtime"]
    server.runtime_root_override = str(source_goal["runtime"])
    server.selected_goal_id = source_goal["ref"]["goal_id"]
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(body=None, *, query=None, path=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=90)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        if path is None:
            path = "/api/chat/goal-amendment"
            if body is None:
                path += "?" + urlencode(query or {"goal_id": source_goal["ref"]["goal_id"]})
        connection.request("POST" if body is not None else "GET", path,
                           body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        assert str(tmp_path) not in json.dumps(result)
        assert "work_items" not in result
        return response.status, result

    yield call, source_goal, server
    server.shutdown()
    thread.join(5)
    server.server_close()
    restart_effect_runtime()


def configuration(call, fixture):
    code, view = call()
    assert code == 200 and view["ok"], view
    full = _root(view)
    return {**fixture["ref"], "operation_id": "browser-root", "execute": False, "parameters": {
        "expected_provider_revision": full["expected_provider_revision"], "policy": full["policy"],
        "permission_scope": full["root_intent"]["permission_scope"],
        "terminal_conditions": full["root_intent"]["terminal_conditions"]}}


def test_http_freeze_preview_commit_replay_and_historical_receipt(api):
    call, fixture, _ = api
    request = configuration(call, fixture)
    before = call()[1]
    code, preview = call(request)
    assert code == 200 and preview["ok"] and preview["status"] == "planned", preview
    assert call()[1] == before
    request.update(execute=True, preview_digest=preview["intent_digest"])
    code, applied = call(request)
    assert code == 200 and applied["ok"] and applied["status"] == "applied", applied
    current = call()[1]
    assert current["intent"]["root_intent"]["objective"] == before["source_commitments"]["objective"]
    assert current["execution_authority_granted"] is False
    assert "work_items" not in applied["current_projection"]
    # A new connection after response loss reuses the exact identity and payload.
    code, replayed = call(request)
    assert code == 200 and replayed["status"] == "replayed", replayed
    assert replayed["original_receipt"] == applied["original_receipt"]
    assert call()[1] == current
    code, historical = call(query={**fixture["ref"], "operation_id": request["operation_id"]})
    assert code == 200 and historical["status"] == "found" and historical["historical_only"], historical
    assert historical["receipts"][0]["result"]["intent_digest"] == preview["intent_digest"]


def test_http_invalid_scope_origin_and_preview_do_not_mutate(api):
    call, fixture, server = api
    request = configuration(call, fixture)
    before = call()[1]
    for patch in ({"agent_id": "agent-a"}, {"operation": "commit"}, {"goal_id": []},
                  {"execute": "yes"}, {"parameters": {"root_intent": {"objective": "easier"}}}):
        assert call({**request, **patch})[0] == 400
    assert call(request, origin="https://foreign.example")[0] == 403
    stale = copy.deepcopy(request)
    stale["parameters"]["expected_provider_revision"] = "stale-provider"
    code, rejected = call(stale)
    assert code == 200 and rejected["reason_code"] == "goal_amendment_needs_rebase" and not rejected["ok"]
    preview = call(request)[1]
    changed = copy.deepcopy(request)
    changed.update(execute=True, preview_digest=preview["intent_digest"])
    changed["parameters"]["terminal_conditions"] = ["A different result"]
    assert call(changed)[0] == 400
    wrong = {**changed, "goal_instance_id": "ginst_" + "b" * 32}
    assert not call(wrong)[1]["ok"]
    assert call()[1] == before
    server.selected_goal_id = "other-workspace"
    assert call()[0] == 400
    assert call(request)[0] == 400
    server.selected_goal_id = fixture["ref"]["goal_id"]
    assert call()[1] == before


def test_source_discovery_exposes_metadata_but_preserves_full_runtime_refusal(api):
    call, fixture, _ = api
    code, directory = call(path="/status.json?view=workspace-directory")
    assert code == 200 and directory["schema_version"] == "loopx_workspace_directory_v1", directory
    assert [row["id"] for row in directory["goals"]] == [fixture["ref"]["goal_id"]]
    assert set(directory["goals"][0]) == {"id", "display_name", "activation_state", "registry_member"}
    code, refused = call(path="/status.json?goal_id=" + fixture["ref"]["goal_id"])
    assert code >= 400 and refused["ok"] is False


@pytest.mark.parametrize("source_goal", ["file"], indirect=True)
def test_http_preserves_owner_unavailability_and_original_storage_recovery(api):
    call, fixture, _ = api
    request = configuration(call, fixture)
    documents = list((fixture["runtime"] / "authority/file-v0").glob("authority-store-*.json"))
    assert len(documents) == 1
    saved = documents[0].read_bytes()
    documents[0].unlink()  # This isolated fixture only; no live Goal repair.
    try:
        for body in (None, request):
            code, unavailable = call(body)
            assert code == 200 and unavailable["ok"] is False, unavailable
            assert unavailable["reason_code"] == "goal_amendment_source_unavailable"
            assert "restore its original storage" in unavailable["reason"]
            assert not documents[0].exists()
    finally:
        documents[0].write_bytes(saved)
    assert call(request)[1]["ok"] is True
