from __future__ import annotations

from urllib.parse import urlencode

from test_chat_goal_amendment import api as api
from test_goal_amendment_authority import source_goal as source_goal
from test_request_amendment_recovery import BRIEF
from loopx.capabilities.manager_context import deliver


def _source(api):
    call, f, server = api
    store = server.chat_store
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


def test_revised_request_interim_return_is_independent_of_old_results(api, capsys):
    from test_request_amendment_recovery import _inbox
    from loopx.capabilities.manager_context.roundtrip import drain
    from loopx.control_plane.collaboration.inbox import _root

    request, call, route = _source(api)
    _, f, server = api
    rid = route["request_id"]
    assert _inbox(f, capsys, "acknowledge", "--request-id", rid,
        "--decision", "adopt", "--reason", "Original assessment")[0] == 0
    assert _inbox(f, capsys, "report", "--request-id", rid, "--phase", "decision",
        "--reply-text", "Checking original evidence")[0] == 0
    original = _root(f["runtime"]) / "replies" / rid / "decision.json"
    original_bytes = original.read_bytes()
    assert _inbox(f, capsys, "report", "--request-id", rid,
        "--reply-text", "Original conclusion")[0] == 0

    def revise(number):
        current = request()[1]["view"]
        body = {**route, "operation": "revise", "execute": True, "parameters": {
            "operation_id": f"correction-{number}", "expected_revision": current["revision"],
            "expected_digest": current["revision_digest"], "brief": {**BRIEF, "context": f"New evidence {number}"},
            "reason": "Source correction"}}
        code, result = request(body)
        assert code == 200, result
        current = result["view"]
        assert _inbox(f, capsys, "acknowledge", "--request-id", rid, "--decision", "adopt",
            "--reason", "Read revised evidence", "--request-revision", str(number),
            "--request-digest", current["revision_digest"])[0] == 0

    def report(number, text, phase="decision"):
        code, result = _inbox(f, capsys, "report", "--request-id", rid, "--phase", phase,
            "--reply-text", text, "--request-revision", str(number))
        assert code == 0, result
        return result

    def returned():
        drain(f["runtime"], f["registry"], server.chat_store, lambda *_: None)
        code, snapshot = call(path="/api/chat/sessions/" + route["session_id"])
        assert code == 200
        return [row for row in snapshot["messages"] if row.get("origin") == "manager_followup"]

    revise(2)
    second = report(2, "Checking corrected evidence")
    assert report(2, "Checking corrected evidence")["result_key"] == second["result_key"]
    messages = returned()
    current = next(row for row in messages if "Checking corrected evidence" in row["text"])
    assert current["return_delivery"]["request_revision"] == 2
    assert current["return_delivery"]["phase"] == "decision"
    assert all("Checking original evidence" not in row["text"] for row in messages)
    assert original.read_bytes() == original_bytes
    report(2, "Corrected conclusion", "conclusion")
    returned()
    revise(3)
    report(3, "An interim update superseded by its own result")
    report(3, "Third revision conclusion", "conclusion")
    messages = returned()
    assert all("An interim update superseded" not in row["text"] for row in messages)
    assert sum("Third revision conclusion" in row["text"] for row in messages) == 1
    assert len(returned()) == len(messages)
