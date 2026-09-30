"""A damaged result is an owed return, never silent completion or replay."""

import hashlib
import json
import subprocess
import sys
import threading
import urllib.request

import pytest

from loopx.capabilities.manager_context.roundtrip import report
from loopx.control_plane.collaboration.inbox import acknowledge, pending
from loopx.control_plane.collaboration.peers import read_inbox, request


@pytest.fixture
def exchange(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "delivery", "repo": str(tmp_path),
        "coordination": {"registered_agents": ["sender", "receiver"]}}]}))
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Draft a public survey",
        "context": "Return a draft to the original requester.",
        "constraints": ["Do not publish"], "inputs": [],
        "acceptance": ["Provide a readable draft"], "return_requirement": "Return the draft"}
    rid = request(tmp_path, registry, "delivery", "sender", "receiver", "survey", brief)["request_id"]
    healthy = request(tmp_path, registry, "delivery", "sender", "receiver", "other", brief)["request_id"]
    acknowledge(tmp_path, "delivery", "receiver", rid, "adopt", "Draft the survey")
    report(tmp_path, "delivery", "receiver", rid, "conclusion", "Draft ready; not published.")
    return tmp_path, registry, rid, healthy


@pytest.mark.parametrize("damage", ["json", "identity", "instance", "decision", "empty", "non_json_number"])
def test_unreadable_conclusion_remains_visible_and_recovers_without_reexecution(exchange, damage):
    root, registry, rid, healthy = exchange
    path = root / ".local/manager-context/replies" / rid / "conclusion.json"
    original = path.read_bytes()
    row = json.loads(original)
    if damage == "json":
        path.write_text("{damaged")
    else:
        row.update({"identity": {"source_id": "another-request"},
                    "instance": {"goal_ref": {"goal_id": "delivery", "goal_instance_id": "another"}},
                    "decision": {"decision": "reject"}, "empty": {"text": " "},
                    "non_json_number": {"created_at": float("nan")}}[damage])
        path.write_text(json.dumps(row))
    damaged = path.read_bytes()
    first = pending(root, "delivery", "receiver")
    assert {item["request_id"] for item in first["items"]} == {rid, healthy}
    blocked = next(item for item in first["items"] if item["request_id"] == rid)
    assert blocked["inbox_state"] == "receipt_unavailable"
    assert blocked["receiver_decision_recorded"]
    assert blocked["warnings"] == ["conclusion_unreadable_or_conflicting"]
    assert "do not repeat" in blocked["next_action"].lower()
    # Reading does not overwrite a decision/result or launch replacement work.
    assert path.read_bytes() == damaged
    path.write_bytes(original)
    assert [item["request_id"] for item in pending(root, "delivery", "receiver")["items"]] == [healthy]
    returned = read_inbox(root, registry, "delivery", "sender")["peer_returns"]["items"]
    assert [(item["request_id"], item["text"]) for item in returned] == [(rid, "Draft ready; not published.")]


def test_unreadable_decision_is_not_a_new_request_or_a_terminal_ack(exchange):
    root, _, rid, healthy = exchange
    path = root / ".local/manager-context/decisions" / (rid + ".json")
    original = path.read_bytes()
    path.write_text("{damaged")
    page = pending(root, "delivery", "receiver")
    assert {item["request_id"] for item in page["items"]} == {rid, healthy}
    blocked = next(item for item in page["items"] if item["request_id"] == rid)
    assert blocked["inbox_state"] == "receipt_unavailable"
    assert blocked["warnings"] == ["decision_unreadable_or_conflicting"]
    assert not blocked.get("receiver_decision_recorded")
    path.write_bytes(original)
    assert [item["request_id"] for item in pending(root, "delivery", "receiver")["items"]] == [healthy]


def test_legal_unicode_results_cannot_overflow_the_existing_receipt_bridge(exchange):
    from loopx.control_plane.collaboration.inbox import _write

    root, _, rid, healthy = exchange
    entry = next((root / ".local/manager-context/entries").glob(f"*/{rid}.json"))
    request_row = json.loads(entry.read_text())
    decision = json.loads((root / ".local/manager-context/decisions" / (rid + ".json")).read_text())
    result = json.loads((root / ".local/manager-context/replies" / rid / "conclusion.json").read_text())
    # Synthetic native-store fixtures: each reply is legal by the existing
    # 20,000-character contract, while their ASCII wire form exceeds 2 MiB.
    for number in range(32):
        request_id = hashlib.sha256(f"completed-{number}".encode()).hexdigest()
        _write(entry.parent / (request_id + ".json"), {**request_row, "request_id": request_id})
        _write(root / ".local/manager-context/decisions" / (request_id + ".json"),
            {**decision, "request_id": request_id})
        _write(root / ".local/manager-context/replies" / request_id / "conclusion.json",
            {**result, "request_id": request_id, "text": "😀" * 20000})
    assert [item["request_id"] for item in pending(root, "delivery", "receiver")["items"]] == [healthy]


def test_cli_reports_the_gap_without_rewriting_an_immutable_result(exchange):
    root, registry, rid, healthy = exchange
    path = root / ".local/manager-context/replies" / rid / "conclusion.json"
    path.write_text("{damaged")
    result = subprocess.run([
        sys.executable, "-c", "from loopx.entrypoint import main; raise SystemExit(main())",
        "--runtime-root", str(root), "--registry", str(registry),
        "manager-inbox", "read", "--goal-id", "delivery", "--agent-id", "receiver",
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    page = json.loads(result.stdout)
    assert {item["request_id"] for item in page["items"]} == {rid, healthy}
    assert next(item for item in page["items"] if item["request_id"] == rid)["inbox_state"] == "receipt_unavailable"
    assert path.read_text() == "{damaged"


@pytest.mark.parametrize("damaged_lane", ["conclusion.json", "conclusion.delivery.json"])
def test_original_app_conversation_retains_readback_and_recovers_over_real_http(tmp_path, damaged_lane):
    from loopx.capabilities.manager_context import deliver
    from loopx.capabilities.manager_context.roundtrip import drain
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "delivery", "repo": str(tmp_path),
        "coordination": {"registered_agents": ["receiver"]}}]}))
    store = ChatSessionStore(tmp_path)
    session = store.create_session(goal_id="loopx-manager", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="fixture", channel_id="manager")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="survey",
        message="给 LoopX 写份社区问卷草稿，先别发布。", origin="web")
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Draft a public survey",
        "context": "Return a draft to the original requester.", "constraints": ["Do not publish"],
        "inputs": [], "acceptance": ["Provide a readable draft"], "return_requirement": "Return the draft"}
    receipt = deliver(tmp_path, registry, session=session, turn=turn,
        request={"goal_id": "delivery", "agent_id": "receiver", "brief": brief})
    store.update_turn(session["session_id"], turn["turn_id"], status="completed",
        response={"message": "The existing receiver has the request.", "context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    store.append_message(session["session_id"], role="agent", turn_id=turn["turn_id"],
        text="The existing receiver has the request.", origin="web")
    rid = receipt["request_id"]
    acknowledge(tmp_path, "delivery", "receiver", rid, "adopt", "Draft without publishing")
    report(tmp_path, "delivery", "receiver", rid, "conclusion", "Draft ready; not published.")
    path = tmp_path / ".local/manager-context/replies" / rid / damaged_lane
    original = path.read_bytes() if path.exists() else None
    path.write_text("{damaged")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_root, server.registry_path = store, tmp_path, registry
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def snapshot():
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/chat/sessions/{session['session_id']}", timeout=10) as response:
            return json.load(response)

    try:
        before = snapshot()
        message = next(item for item in before["messages"] if item.get("collaboration"))
        assert message["text"] == "The existing receiver has the request."
        assert message["collaboration"]["agent_id"] == "receiver"
        assert message["collaboration"]["returns"] == [{"phase": "conclusion", "status": "explicit_unverified",
            "created_at": None, "delivered_at": None, "error": "delivery_state_unreadable"}]
        assert path.read_text() == "{damaged"
        if original is None:
            path.unlink()
        else:
            path.write_bytes(original)
        def no_external_send(*args, **kwargs):
            raise AssertionError("an App return cannot send an external message")
        drain(tmp_path, registry, ChatSessionStore(tmp_path), no_external_send)
        drain(tmp_path, registry, ChatSessionStore(tmp_path), no_external_send)
        after = snapshot()
        returned = [item for item in after["messages"] if item.get("origin") == "manager_followup"]
        assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
        assert returned[0]["return_delivery"]["status"] == "delivered"
        assert len(list((store.sessions_root / session["session_id"] / "turns").glob("*.json"))) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
