"""Exact File/SQLite HTTP journey: one Turn, independent requests and returns."""
import json
from urllib.parse import urlencode

from test_chat_goal_amendment import api as api
from test_goal_amendment_authority import source_goal as source_goal
from test_chat_multi_handoff import request
from loopx.capabilities.manager_context.execution import handoff_response
from loopx.capabilities.manager_context.roundtrip import drain
from loopx.chat import normalize_agent_response
from loopx.cli import main


def test_exact_batch_http_cli_return_and_receipt_identity_isolation(api, capsys):
    call, f, server = api
    store = server.chat_store
    original = f["state"].read_bytes(), f["registry"].read_bytes()
    session = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="multi-original", channel_id="manager")
    sid = session["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="ask-both", message="Ask both independent owners", origin="web")
    response = handoff_response(f["runtime"], f["registry"], session=session, turn=turn, source_store=store,
        response=normalize_agent_response({"context_handoff": [request(f["ref"]["goal_id"], a) for a in ("agent-a", "agent-b")]}),
        source_authorized=lambda: True, execution_allowed=lambda: True)
    receipts = response["context_handoff_receipts"]
    assert len(receipts) == 2 and all(r["goal_ref"] == f["ref"] for r in receipts)
    store.append_message(sid, role="assistant", text=response["message"], turn_id=turn["turn_id"])
    store.update_turn(sid, turn["turn_id"], status="completed", response=response)
    store.finalize_managed_turn_completion(sid, turn["turn_id"])
    def snapshot():
        code, view = call(path="/api/chat/sessions/" + sid)
        assert code == 200, view
        return view
    cards = next(m["collaborations"] for m in snapshot()["messages"] if m.get("collaborations"))
    assert [r["agent_id"] for r in cards] == ["agent-a", "agent-b"]
    # Both separately committed requests survive a lost originating response.
    store.update_turn(sid, turn["turn_id"], status="failed", response=None)
    recovered = next(m["collaborations"] for m in snapshot()["messages"] if m.get("collaborations"))
    assert {r["request_id"] for r in recovered} == {r["request_id"] for r in receipts}
    store.update_turn(sid, turn["turn_id"], status="completed", response=response)
    def cli(receipt, operation, *args):
        assert main(["--format", "json", "--registry", str(f["registry"]), "--runtime-root", str(f["runtime"]),
            "manager-inbox", operation, "--goal-id", receipt["goal_id"], "--agent-id", receipt["agent_id"], *args]) == 0
        return json.loads(capsys.readouterr().out)
    for receipt in reversed(receipts):
        assert receipt["request_id"] in str(cli(receipt, "read"))
        cli(receipt, "acknowledge", "--request-id", receipt["request_id"], "--decision", "adopt", "--reason", "Independent finding")
        cli(receipt, "report", "--request-id", receipt["request_id"], "--reply-text", "Finding from " + receipt["agent_id"])
        drain(f["runtime"], f["registry"], store, lambda *_: None)
    view = snapshot()
    cards = next(m["collaborations"] for m in view["messages"] if m.get("collaborations"))
    assert all(r["returns"][0]["status"] == "delivered" for r in cards)
    assert len([m for m in view["messages"] if m.get("origin") == "manager_followup"]) == 2
    # A conflicting receipt for B cannot certify B, or erase A's valid proof.
    store.update_turn(sid, turn["turn_id"], response={**response, "context_handoff_receipts": [
        receipts[0], {**receipts[1], "agent_id": "agent-a"}]})
    server.selected_goal_id = None
    code, inventory = call(path="/api/chat/request-inventory?" + urlencode({"session_id": sid, "view": "history"}))
    assert code == 200
    good = next(r for r in inventory["rows"] if r["agent_id"] == "agent-a")
    assert good["request_id"] == receipts[0]["request_id"]
    assert not any(r["agent_id"] == "agent-b" for r in inventory["rows"])
    assert any(warning.get("request_id") == receipts[1]["request_id"]
        and warning.get("reason") == "handoff_receipt_conflict" for warning in inventory["coverage"]["diagnostics"])
    assert "Independent source assessment: agent-b" not in json.dumps(inventory)
    route = {**f["ref"], "session_id": sid, "turn_id": turn["turn_id"],
        "agent_id": "agent-b", "request_id": receipts[1]["request_id"]}
    assert call(path="/api/chat/request-control?" + urlencode(route))[0] == 400
    assert (f["state"].read_bytes(), f["registry"].read_bytes()) == original
