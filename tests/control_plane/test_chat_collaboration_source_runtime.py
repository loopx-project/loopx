"""Original Chat reads shared receipts without borrowing another Chat host."""
# ruff: noqa: F811 - pytest injects the imported isolated provider fixture.
import http.client
import json
import shutil
import threading
from contextlib import contextmanager

import pytest

from test_local_delegation import service  # noqa: F401 - real File/SQLite authority

from loopx.capabilities.manager_context import _root, acknowledge, deliver
from loopx.capabilities.manager_context.roundtrip import drain, report, reply_status
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore


@contextmanager
def session_api(store, registry, workspace):
    controller = ChatRuntimeController(
        store=store, registry_path=registry,
        project_contexts=ChatProjectContexts([workspace]),
        codex_bin=str(workspace / "no-model-executable"),
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, controller
    server.runtime_root, server.registry_path = store.root.parent, registry
    server.selected_goal_id, server.verbose = None, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read(session_id, query=""):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET", f"/api/chat/sessions/{session_id}" + query)
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200, payload
            return payload
        finally:
            connection.close()

    try:
        yield read, controller
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture(params=[False, True], ids=["colocated", "separate-chat"])
def returned(service, request):
    workspace, delegation = service
    root, registry = delegation.root, delegation.registry
    store = ChatSessionStore(workspace / "private-chat" if request.param else root)
    session = store.create_session(goal_id="loopx-manager", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="original", channel_id="manager")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="original-question",
        message="Check the original requirement and return the result here.", origin="web")
    receipt = deliver(root, registry, session=session, turn=turn,
        request={"goal_id": delegation.goal_id, "agent_id": "analyst", "brief": {
            "schema_version": "collaboration_brief_v0", "purpose": "Check the original requirement",
            "context": "Keep the original conversation and recipient.", "constraints": ["No external actions"],
            "inputs": [], "acceptance": ["Return the checked result"],
            "return_requirement": "Return here without a second question",
        }}, source_store=store)
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
        response={"message": "The existing recipient will return here.", "context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    acknowledge(root, delegation.goal_id, "analyst", receipt["request_id"], "adopt", "Checked the source")
    report(root, delegation.goal_id, "analyst", receipt["request_id"], "conclusion", "The checked result is ready.")
    assert drain(root, registry, store, None) == 1
    assert reply_status(root, receipt)[0]["status"] == "delivered"
    if store.root.parent != root:
        # A plausible Chat-local copy must never replace registered receipt truth.
        shutil.copytree(_root(root), _root(store.root.parent))
        decoy = _root(store.root.parent) / "replies" / receipt["request_id"] / "conclusion.delivery.json"
        decoy.write_text(json.dumps({"status": "verification_required", "error": "provider_delivery_unverified"}))
    return workspace, root, registry, store, session, receipt


def stored_json(*roots):
    return {path: path.read_bytes() for root in roots for path in root.rglob("*.json")}


def test_http_keeps_original_return_and_request_readback(returned):
    workspace, root, registry, store, session, receipt = returned
    before = stored_json(_root(root), _root(store.root.parent), store.root)
    with session_api(store, registry, workspace) as (read, controller):
        assert controller.coordination_runtime_root == root
        snapshot = read(session["session_id"])
        result = next(m for m in snapshot["messages"] if m.get("origin") == "manager_followup")
        assert result["text"].endswith("The checked result is ready.")
        assert result["return_delivery"]["status"] == "delivered"
        commitment = next(m for m in snapshot["messages"] if m.get("collaboration"))
        assert commitment["collaboration"]["request_id"] == receipt["request_id"]
        # A later history page still carries this result's original identity.
        first = read(session["session_id"], "?message_limit=2")
        cursor = first["message_page"]["next_cursor"]
        assert cursor
        page = read(session["session_id"], "?message_limit=1&message_cursor=" + cursor)
        assert page["messages"][0]["message_id"] == result["message_id"]
        assert page["messages"][0]["return_delivery"]["status"] == "delivered"
    assert stored_json(_root(root), _root(store.root.parent), store.root) == before, "Reading must not execute or rewrite the original work"


def test_copied_session_cannot_borrow_original_host_receipts(returned):
    workspace, root, registry, store, session, _ = returned
    foreign = ChatSessionStore(workspace / "foreign-chat")
    shutil.copytree(store._session_dir(session["session_id"]), foreign._session_dir(session["session_id"]))
    before = stored_json(_root(root), store.root, foreign.root)
    with session_api(foreign, registry, workspace) as (read, _):
        snapshot = read(session["session_id"])
    assert any(m.get("origin") == "manager_followup" for m in snapshot["messages"])
    assert all("return_delivery" not in m and "collaboration" not in m for m in snapshot["messages"])
    assert stored_json(_root(root), store.root, foreign.root) == before


def test_missing_receipt_is_queued_and_recovers_without_reexecuting(returned):
    workspace, root, registry, store, session, receipt = returned
    path = _root(root) / "replies" / receipt["request_id"] / "conclusion.delivery.json"
    saved = path.read_bytes()
    path.unlink()
    with session_api(store, registry, workspace) as (read, _):
        pending = read(session["session_id"])
        result = next(m for m in pending["messages"] if m.get("origin") == "manager_followup")
        assert result["return_delivery"]["status"] == "queued"
        assert not path.exists(), "A read must not synthesize a delivery receipt"
        path.write_bytes(saved)
        recovered = read(session["session_id"])
        replies = [m for m in recovered["messages"] if m.get("origin") == "manager_followup"]
        assert len(replies) == 1 and replies[0]["message_id"] == result["message_id"]
        assert replies[0]["return_delivery"]["status"] == "delivered"


def test_unreadable_original_route_does_not_fall_back_to_chat_local_copy(returned):
    workspace, root, registry, store, session, receipt = returned
    route = _root(root) / "roundtrips" / (receipt["request_id"] + ".json")
    saved = route.read_bytes()
    route.write_text("invalid-json")
    with session_api(store, registry, workspace) as (read, _):
        unavailable = read(session["session_id"])
        assert all("return_delivery" not in m and "collaboration" not in m for m in unavailable["messages"])
        assert sum(m.get("origin") == "manager_followup" for m in unavailable["messages"]) == 1
        assert route.read_text() == "invalid-json"
        route.write_bytes(saved)
        recovered = read(session["session_id"])
        returned_message = next(m for m in recovered["messages"] if m.get("origin") == "manager_followup")
        assert returned_message["return_delivery"]["status"] == "delivered"
