"""Real loopback HTTP and canonical stores; only provider transport is synthetic."""
import http.client
import json
import threading

import pytest

from test_room_work import setup as setup, snapshot, claim
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.extensions.lark.goal_channel_contracts import write_goal_channel_binding
from loopx.extensions.lark import goal_channel_work as work


@pytest.fixture
def api(setup):
    args, room, binding = setup
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = args["registry_path"]
    server.runtime_root = args["runtime_root"]
    server.runtime_root_override = str(args["runtime_root"])
    server.lark_runner = room
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(body=None, *, query="goal_id=room-fixture&connection_id=agent-a", origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        headers = {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        try:
            conn.request("POST" if body is not None else "GET", "/api/chat/lark/room-work" + ("" if body is not None else "?" + query),
                body=None if body is None else json.dumps(body), headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()
    try:
        yield request, args, room, binding
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def intent(readback):
    return {"goal_id": "room-fixture", "connection_id": "agent-a", "scope_revision": readback["scope_revision"],
        "todo_id": readback["projection"]["selected_todo"]["todo_id"],
        "expected_revision": readback["projection"]["source_revision"], "idempotency_key": "http-room-claim"}


def test_http_claim_retry_and_independent_current_owner(api):
    request, args, room, _ = api
    status, preview = request()
    assert status == 200 and preview["ok"] and not room.messages
    assert preview["projection_envelope"] == preview["projection"]["projection_envelope"]
    assert preview["projection_envelope"]["fresh"] and preview["projection_envelope"]["complete"]
    assert "PRIVATE_" not in json.dumps(preview)
    body = intent(preview)
    status, accepted = request(body)
    assert status == 200 and accepted["canonical_claim_accepted"] and accepted["readback_verified"]
    assert accepted["execution_authority_granted"] is False
    assert accepted["claim_available"] is True
    assert "inspected_todo" not in accepted["projection"], "the established claim projection remains unchanged"
    revision = snapshot(args)["provider_revision"]
    _, retry = request(body)
    assert retry["status"] == "already_applied" and len(room.messages) == 1
    assert snapshot(args)["provider_revision"] == revision
    _, current = request(query="goal_id=room-fixture&connection_id=agent-a&todo_id=" + body["todo_id"])
    assert current["projection"]["inspected_todo"]["current_owner"] == "agent-a"
    assert current["projection"]["counts"]["unclaimed"] == 0


def test_http_slow_source_does_not_become_fresh_at_assembly(api, monkeypatch):
    request, _, room, _ = api
    clock = {"at": "2026-01-01T00:00:00Z"}
    monkeypatch.setattr(work, "now_utc_iso", lambda: clock["at"])
    read = work.read_canonical_todos_if_promoted
    def slow_read(**kwargs):
        result = read(**kwargs)
        clock["at"] = "2026-01-01T00:10:00Z"
        return result
    monkeypatch.setattr(work, "read_canonical_todos_if_promoted", slow_read)
    status, result = request()
    envelope = result["projection_envelope"]
    assert status == 200 and result["ok"] and not result["claim_available"]
    assert envelope["observed_at"] == "2026-01-01T00:10:00Z"
    assert not envelope["fresh"] and envelope["complete"] and envelope["alert"]
    assert all(row["last_read_at"] == "2026-01-01T00:00:00Z" for row in envelope["sources"])
    assert "stale_sources" in envelope["alert_reasons"] and not room.messages


def test_http_unreadable_authority_has_no_whole_current_projection(api, monkeypatch):
    request, _, room, _ = api
    def unavailable(**_):
        raise OSError("synthetic authority read failure")
    monkeypatch.setattr(work, "read_canonical_todos_if_promoted", unavailable)
    status, result = request()
    envelope = result["projection_envelope"]
    assert status == 200 and not result["ok"] and "projection" not in result
    assert not envelope["fresh"] and not envelope["complete"] and envelope["alert"]
    assert "unreadable_sources" in envelope["alert_reasons"]
    assert envelope["coverage"]["included_count"] == 0 and not room.messages


def test_http_revoked_actor_discloses_the_uncovered_scope(api):
    request, args, room, _ = api
    registry = json.loads(args["registry_path"].read_text())
    registry["goals"][0]["coordination"]["registered_agents"] = ["agent-b"]
    args["registry_path"].write_text(json.dumps(registry))
    _, result = request()
    envelope = result["projection_envelope"]
    assert not result["ok"] and not result["claim_available"] and not envelope["complete"]
    assert envelope["coverage"]["expected_count"] == 1
    assert envelope["coverage"]["omitted"] == [{"reason": "room_scope_unavailable", "count": 1, "refs": []}]
    assert "incomplete_coverage" in envelope["alert_reasons"] and not room.messages


def test_claim_replay_rereads_sources_but_a_saved_copy_retains_its_age(api, monkeypatch):
    from loopx.control_plane.projection_envelope_facts import serve_projection_envelope
    request, _, room, _ = api
    clock = {"at": "2026-01-01T00:00:00Z"}
    monkeypatch.setattr(work, "now_utc_iso", lambda: clock["at"])
    _, preview = request()
    body = intent(preview)
    _, accepted = request(body)
    clock["at"] = "2026-01-01T00:10:00Z"
    _, replayed = request(body)
    assert replayed["receipt"]["receipt_id"] == accepted["receipt"]["receipt_id"] and len(room.messages) == 1
    assert replayed["projection_envelope"]["fresh"]
    assert replayed["projection_envelope"]["observed_at"] == clock["at"]
    assert replayed["projection_envelope"]["served_from_cache"] is False
    saved_copy = serve_projection_envelope(accepted["projection_envelope"], served_at=clock["at"])
    assert saved_copy["observed_at"] == "2026-01-01T00:00:00Z"
    assert saved_copy["served_at"] == clock["at"] and saved_copy["served_from_cache"]
    assert saved_copy["alert"] and not saved_copy["fresh"]


def test_http_unknown_delivery_keeps_exact_request_recoverable(api):
    request, args, room, _ = api
    _, preview = request()
    body = intent(preview)
    room.fail_send = True
    _, first = request(body)
    assert first["canonical_claim_accepted"] and not first["ok"]
    revision = snapshot(args)["provider_revision"]
    _, replay = request(body)
    assert replay["ok"] and replay["status"] == "already_applied" and len(room.messages) == 1
    assert snapshot(args)["provider_revision"] == revision


def test_http_stale_revision_and_changed_scope_cannot_claim(api):
    request, args, room, binding = api
    _, preview = request()
    body = intent(preview)
    assert claim(args, "agent-b", body["expected_revision"], "peer-claim")["ok"]
    revision = snapshot(args)["provider_revision"]
    _, stale = request(body)
    assert stale["status"] == "conflict" and not stale["canonical_claim_accepted"]
    assert snapshot(args)["provider_revision"] == revision
    binding["bindings"]["room-fixture"]["connections"]["agent-a"]["channel"]["chat_id"] = "oc_another_fixture"
    write_goal_channel_binding(args["binding_path"], binding)
    assert request(body)[0] == 409
    assert snapshot(args)["provider_revision"] == revision and len(room.messages) == 1


def test_http_client_cannot_supply_actor_or_foreign_connection(api):
    request, args, room, _ = api
    _, preview = request()
    body = intent(preview)
    before = snapshot(args)["provider_revision"]
    assert request({**body, "actor_id": "agent-b"})[0] == 400
    assert request(query="goal_id=room-fixture&connection_id=missing")[0] == 400
    assert request(body, origin="https://foreign.example")[0] == 403
    room.authenticated = False
    _, denied = request(body)
    assert not denied["canonical_claim_accepted"] and not room.messages
    assert snapshot(args)["provider_revision"] == before


def test_inactive_extension_preserves_read_only_view(api, monkeypatch):
    from loopx.extensions.lark import goal_channel_work
    request, args, room, _ = api
    def disabled(*args, **kwargs):
        raise ValueError("fixture extension is disabled")
    monkeypatch.setattr(goal_channel_work, "resolve_extension_activation", disabled)
    before = snapshot(args)["provider_revision"]
    status, preview = request()
    assert status == 200 and preview["ok"] and preview["claim_available"] is False
    _, attempted = request(intent(preview))
    assert not attempted["canonical_claim_accepted"] and not room.messages
    assert snapshot(args)["provider_revision"] == before


def test_binding_change_after_http_scope_check_is_rechecked_by_work_owner(api, monkeypatch):
    from loopx.extensions.lark import goal_channel_work
    request, args, room, binding = api
    _, preview = request()
    body = intent(preview)
    before = snapshot(args)["provider_revision"]
    def retarget(*a, **k):
        binding["bindings"]["room-fixture"]["connections"]["agent-a"]["channel"]["chat_id"] = "oc_retargeted_fixture"
        write_goal_channel_binding(args["binding_path"], binding)
        return {"enabled": True}
    monkeypatch.setattr(goal_channel_work, "resolve_extension_activation", retarget)
    _, result = request(body)
    assert not result["canonical_claim_accepted"] and not room.messages
    assert snapshot(args)["provider_revision"] == before


def test_manager_connection_cannot_be_used_as_a_worker_route(api):
    request, args, room, binding = api
    before = snapshot(args)["provider_revision"]
    binding["bindings"]["room-fixture"]["connections"]["agent-a"]["routing"] = {"conversation_kind": "manager"}
    write_goal_channel_binding(args["binding_path"], binding)
    assert request()[0] == 400
    assert snapshot(args)["provider_revision"] == before and not room.messages
