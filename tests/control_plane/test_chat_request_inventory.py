"""Original steward requests, not inferred worker activity or a second ledger."""
from urllib.parse import urlencode

from test_chat_goal_amendment import api as api
from test_goal_amendment_authority import source_goal as source_goal
from test_chat_request_control import _source
from loopx.capabilities.manager_context import acknowledge, deliver
from loopx.capabilities.manager_context.roundtrip import drain, report
from loopx.control_plane.collaboration.inbox import _root


def _inventory(api, session_id, **query):
    call, _, server = api
    server.selected_goal_id = None
    return call(path="/api/chat/request-inventory?" + urlencode({"session_id": session_id, **query}))


def test_read_and_adoption_still_owe_conclusion_and_queued_return_stays_visible(api):
    _, _, route = _source(api)
    _, f, server = api
    rid = route["request_id"]
    root = _root(f["runtime"])
    saved = {path: path.read_bytes() for path in root.rglob("*.json")}
    code, first = _inventory(api, route["session_id"])
    assert code == 200 and first["pending_count"] == 1, first
    assert first["rows"][0]["source_turn_id"] == route["turn_id"]
    assert {path: path.read_bytes() for path in saved} == saved
    assert not list((root / "reads").glob("*.json"))
    acknowledge(f["runtime"], f["ref"]["goal_id"], "agent-a", rid, "adopt", "Check the actual artifact",
        registry=f["registry"], caller_goal_ref=f["ref"])
    accepted = _inventory(api, route["session_id"])[1]
    assert accepted["pending_count"] == 1 and accepted["rows"][0]["decision"] == "adopt"
    report(f["runtime"], f["ref"]["goal_id"], "agent-a", rid, "conclusion", "The requested artifact was checked; the result is retained.",
        registry=f["registry"], caller_goal_ref=f["ref"])
    queued = _inventory(api, route["session_id"])[1]
    assert queued["pending_count"] == 1 and queued["rows"][0]["agenda"]["step"] == "await_return_delivery", queued
    drain(f["runtime"], f["registry"], server.chat_store, lambda *_: None)
    returned = _inventory(api, route["session_id"])[1]
    assert returned["pending_count"] == 0 and returned["rows"] == []
    historical = _inventory(api, route["session_id"], view="history")[1]
    assert historical["rows"][0]["request_id"] == rid and not historical["rows"][0]["unfinished"]


def test_revision_and_cancellation_reuse_current_control_and_preserve_history(api):
    control, _, route = _source(api)
    current = control()[1]["view"]
    changed = {**current["brief"], "purpose": "Inspect the corrected artifact", "context": "Corrected source"}
    code, _ = control({**route, "operation": "revise", "execute": True, "parameters": {
        "operation_id": "inventory-revision", "expected_revision": current["revision"], "expected_digest": current["revision_digest"],
        "brief": changed, "reason": "The source was corrected"}})
    assert code == 200
    value = _inventory(api, route["session_id"])[1]
    assert value["rows"][0]["request_revision"] == 2 and value["rows"][0]["brief"] == changed
    current = control()[1]["view"]
    assert control({**route, "operation": "cancel", "execute": True, "parameters": {
        "operation_id": "inventory-cancel", "expected_revision": current["revision"], "expected_digest": current["revision_digest"],
        "reason": "This request is no longer needed"}})[0] == 200
    assert _inventory(api, route["session_id"])[1]["pending_count"] == 0
    historical = _inventory(api, route["session_id"], view="history")[1]["rows"][0]
    assert historical["brief"] == changed and not historical["unfinished"] and historical["control_state"] == "cancelled"


def test_earlier_source_session_survives_reopen_and_other_audiences_are_excluded(api):
    _, _, route = _source(api)
    _, f, server = api
    store = server.chat_store
    fresh = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="new-steward-session", channel_id="manager")
    code, value = _inventory(api, fresh["session_id"])
    assert code == 200 and value["pending_count"] == 1
    assert value["rows"][0]["source_session_id"] == route["session_id"]
    project = store.create_session(goal_id=f["ref"]["goal_id"], agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="project-owner", channel_id="goal." + f["ref"]["goal_id"])
    turn, _ = store.create_turn(project["session_id"], client_turn_id="other-conversation", message="Private project request", origin="web")
    deliver(f["runtime"], f["registry"], session=project, turn=turn,
        request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-b"}, source_store=store)
    assert "Private project request" not in str(_inventory(api, fresh["session_id"])[1])
    assert _inventory(api, project["session_id"])[1]["pending_count"] == 1
    external = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="another-user", channel_id="manager.external.foreign")
    assert _inventory(api, external["session_id"])[0] == 400
    assert _inventory(api, "missing-session")[0] == 400


def test_paging_and_unreadable_source_do_not_report_empty_or_mutate_receipts(api):
    _, _, route = _source(api)
    _, f, server = api
    session = server.chat_store.load_session(route["session_id"])
    for number in range(12):
        turn, _ = server.chat_store.create_turn(session["session_id"], client_turn_id=f"request-{number}",
            message=f"Inspect artifact {number}", origin="web")
        delivered = deliver(f["runtime"], f["registry"], session=session, turn=turn,
            request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-a"}, source_store=server.chat_store)
        server.chat_store.update_turn(session["session_id"], turn["turn_id"], status="completed", response={"context_handoff_receipt": delivered})
        server.chat_store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    code, first = _inventory(api, route["session_id"])
    assert code == 200 and first["pending_count"] == 13 and len(first["rows"]) == 12, first
    second = _inventory(api, route["session_id"], offset=first["next_offset"])[1]
    assert len(second["rows"]) == 1 and second["next_offset"] is None
    assert second["snapshot_digest"] == first["snapshot_digest"]
    assert not {row["request_id"] for row in first["rows"]} & {row["request_id"] for row in second["rows"]}
    source = _root(f["runtime"]) / "roundtrips" / (route["request_id"] + ".json")
    saved = source.read_bytes()
    source.write_text("{damaged")
    partial = _inventory(api, route["session_id"])[1]
    assert partial["coverage"]["unreadable"] == 1 and partial["pending_count"] == 12
    assert partial["snapshot_digest"] != first["snapshot_digest"]
    source.write_bytes(saved)
    assert _inventory(api, route["session_id"])[1]["pending_count"] == 13
    source.unlink()
    missing = _inventory(api, route["session_id"])[1]
    assert missing["coverage"]["unreadable"] == 1 and missing["pending_count"] == 12
    source.write_bytes(saved)
    assert _inventory(api, route["session_id"], offset=-1)[0] == 400
