from __future__ import annotations

from urllib.parse import urlencode

from test_chat_goal_amendment import api as api
from test_goal_amendment_authority import source_goal as source_goal
from test_request_amendment_recovery import BRIEF
from loopx.capabilities.manager_context import deliver
from loopx.chat_store import ChatSessionStore


def _source(api):
    call, f, server = api
    store = ChatSessionStore(f["runtime"])
    server.chat_store = store
    session = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="request-control-fixture", channel_id="manager")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="request-source", message="Check the current contract", origin="web")
    delivered = deliver(f["runtime"], f["registry"], session=session, turn=turn,
        request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-a", "brief": BRIEF}, source_store=store)
    store.update_turn(session["session_id"], turn["turn_id"], status="completed", response={"context_handoff_receipt": delivered})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    store.append_message(session["session_id"], role="user", text=turn["message"], turn_id=turn["turn_id"], origin="web")
    route = {**f["ref"], "agent_id": "agent-a", "request_id": delivered["request_id"],
        "session_id": session["session_id"], "turn_id": turn["turn_id"]}

    def request(body=None, routing=None):
        return call(body, path="/api/chat/request-control" + ("?" + urlencode(routing or route) if body is None else ""))
    return request, call, route


def test_original_conversation_controls_revision_and_replays_unknown_reply(api):
    request, call, route = _source(api)
    code, first = request()
    assert code == 200 and first["view"]["revision"] == 1, first
    body = {**route, "operation": "revise", "execute": False, "parameters": {
        "operation_id": "browser-correction", "expected_revision": 1, "expected_digest": first["view"]["revision_digest"],
        "brief": {**BRIEF, "context": "Use the corrected evidence"}, "reason": "Source correction"}}
    assert request(body)[1]["planned"] is True
    assert request()[1]["view"]["revision"] == 1
    body["execute"] = True
    code, applied = request(body)
    assert code == 200 and applied["view"]["revision"] == 2, applied
    assert request(body)[1]["replayed"] is True
    stale = {**body, "parameters": {**body["parameters"], "operation_id": "stale-source"}}
    code, refusal = request(stale)
    assert code == 409 and refusal["error_code"] == "request_basis_changed", refusal
    code, snapshot = call(path="/api/chat/sessions/" + route["session_id"])
    assert code == 200
    card = next(message["collaboration"] for message in snapshot["messages"] if message.get("collaboration"))
    assert card["control_route"]["available"] is True and card["request_revision"] == 2
    assert card["brief"]["context"] == "Use the corrected evidence" and card["decision"] == "pending"
    current = request()[1]["view"]
    cancel = {**route, "operation": "cancel", "execute": True, "parameters": {"operation_id": "browser-cancel",
        "expected_revision": current["revision"], "expected_digest": current["revision_digest"], "reason": "Stop this request"}}
    assert request(cancel)[1]["view"]["control_state"] == "cancelled"


def test_browser_cannot_rebind_source_or_write_a_receiver_observation(api):
    request, _, route = _source(api)
    before = request()[1]["view"]
    code, _ = request(routing={**route, "session_id": "another-session"})
    assert code == 400
    code, _ = request({**route, "operation": "assess", "execute": True, "parameters": {"decision": "adopt"}})
    assert code == 400
    code, _ = request({**route, "operation": "cancel", "execute": True, "parameters": {}, "actor_agent_id": "agent-a"})
    assert code == 400
    assert request()[1]["view"] == before
