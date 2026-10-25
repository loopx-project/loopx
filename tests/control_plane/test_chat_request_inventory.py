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


def test_inventory_pins_code_revision_but_rereads_original_source(api, monkeypatch):
    from loopx.control_plane import effect_runtime

    _, _, route = _source(api)
    _, fixture, _ = api
    original = effect_runtime._runtime_fingerprint
    scans = []
    def fingerprint():
        scans.append(True)
        return original()
    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", fingerprint)
    first = _inventory(api, route["session_id"])
    assert first[0] == 200 and first[1]["pending_count"] == 1
    assert len(scans) == 1
    source = _root(fixture["runtime"]) / "roundtrips" / (route["request_id"] + ".json")
    source.write_text("{damaged")
    second = _inventory(api, route["session_id"])
    assert second[0] == 200 and second[1]["coverage"]["unreadable"] == 1
    assert second[1]["pending_count"] == 0
    assert len(scans) == 2, "Each HTTP read must qualify its own runtime revision"


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
    assert _inventory(api, route["session_id"], search="corrected artifact")[1]["rows"] == []
    searched = _inventory(api, route["session_id"], view="history", search="  CORRECTED artifact  ")[1]
    assert searched["matched"] == 1 and searched["rows"][0] == historical
    assert searched["pending_count"] == 0
    assert _inventory(api, route["session_id"], view="history", search="Corrected source")[1]["matched"] == 1


def test_earlier_source_session_survives_reopen_and_other_audiences_are_excluded(api, monkeypatch):
    from loopx.presentation.request_control_api import RequestControlMixin

    _, _, route = _source(api)
    _, f, server = api
    store = server.chat_store
    fresh = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="new-steward-session", channel_id="manager")
    scope_read = RequestControlMixin._request_inventory_scope
    verified = []
    def observe_scope(reader, session_id):
        verified.append(session_id)
        return scope_read(reader, session_id)
    monkeypatch.setattr(RequestControlMixin, "_request_inventory_scope", observe_scope)
    code, value = _inventory(api, fresh["session_id"])
    assert code == 200 and value["pending_count"] == 1
    assert value["rows"][0]["source_session_id"] == route["session_id"]
    assert verified.count(fresh["session_id"]) == 2
    assert verified.count(route["session_id"]) == 2, "Older sources need their own closing authorization check"
    project = store.create_session(goal_id=f["ref"]["goal_id"], agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="project-owner", channel_id="goal." + f["ref"]["goal_id"])
    turn, _ = store.create_turn(project["session_id"], client_turn_id="other-conversation", message="Private project request", origin="web")
    deliver(f["runtime"], f["registry"], session=project, turn=turn,
        request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-b"}, source_store=store)
    assert "Private project request" not in str(_inventory(api, fresh["session_id"])[1])
    scoped = _inventory(api, fresh["session_id"], search="Private project request")[1]
    assert scoped["matched"] == 0 and scoped["rows"] == [] and scoped["pending_count"] == 1
    assert _inventory(api, project["session_id"])[1]["pending_count"] == 1
    external = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="another-user", channel_id="manager.external.foreign")
    assert _inventory(api, external["session_id"])[0] == 400
    assert _inventory(api, "missing-session")[0] == 400


def test_paging_and_unreadable_source_do_not_report_empty_or_mutate_receipts(api, monkeypatch):
    import loopx.chat_store as chat_store
    from loopx.control_plane.collaboration import source_chat_observation
    from loopx.presentation.request_control_api import RequestControlMixin

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
    scope_read = RequestControlMixin._request_inventory_scope
    verified = []
    def observe_scope(reader, session_id):
        verified.append(session_id)
        return scope_read(reader, session_id)
    monkeypatch.setattr(RequestControlMixin, "_request_inventory_scope", observe_scope)
    read_json = source_chat_observation._read_json
    turn_reads = []
    def counted_read(path):
        if path.parent.name == "turns" and not path.name.endswith(".events.json"):
            turn_reads.append(path)
        return read_json(path)
    monkeypatch.setattr(source_chat_observation, "_read_json", counted_read)
    monkeypatch.setattr(chat_store, "_read_json", counted_read)
    code, first = _inventory(api, route["session_id"])
    assert code == 200 and first["pending_count"] == 13 and len(first["rows"]) == 12, first
    assert len(turn_reads) <= 3 * 13, "One inventory must not rescan full Turn history for every request"
    assert len(verified) == 2, "Provider scope verification must not grow with rows from one source"
    second = _inventory(api, route["session_id"], offset=first["next_offset"])[1]
    assert len(verified) == 4, "A new HTTP read must verify the source independently"
    assert len(second["rows"]) == 1 and second["next_offset"] is None
    assert second["snapshot_digest"] == first["snapshot_digest"]
    assert not {row["request_id"] for row in first["rows"]} & {row["request_id"] for row in second["rows"]}
    # Locate the oldest retained row without loading or filtering the first page.
    oldest = second["rows"][0]
    searched = _inventory(api, route["session_id"], search=oldest["request_id"])[1]
    assert searched["rows"] == [oldest] and searched["matched"] == 1
    assert searched["pending_count"] == 13 and searched["next_offset"] is None
    assert _inventory(api, route["session_id"], search="AGENT-A")[1]["matched"] == 13
    assert _inventory(api, route["session_id"], search=f["ref"]["goal_id"])[1]["matched"] == 13
    assert _inventory(api, route["session_id"], search="   ")[1]["rows"] == first["rows"]
    missing_a = _inventory(api, route["session_id"], search="missing-A")[1]
    missing_b = _inventory(api, route["session_id"], search="missing-B")[1]
    assert missing_a["rows"] == missing_b["rows"] == []
    assert missing_a["snapshot_digest"] != missing_b["snapshot_digest"]
    assert _inventory(api, route["session_id"], search="x" * 201)[0] == 400
    source = _root(f["runtime"]) / "roundtrips" / (route["request_id"] + ".json")
    saved = source.read_bytes()
    source.write_text("{damaged")
    partial = _inventory(api, route["session_id"])[1]
    assert partial["coverage"]["unreadable"] == 1 and partial["pending_count"] == 12
    assert partial["snapshot_digest"] != first["snapshot_digest"]
    no_match = _inventory(api, route["session_id"], search="nothing-matches")[1]
    assert no_match["matched"] == 0 and no_match["coverage"]["unreadable"] == 1
    source.write_bytes(saved)
    assert _inventory(api, route["session_id"])[1]["pending_count"] == 13
    source.unlink()
    missing = _inventory(api, route["session_id"])[1]
    assert missing["coverage"]["unreadable"] == 1 and missing["pending_count"] == 12
    source.write_bytes(saved)
    assert _inventory(api, route["session_id"], offset=-1)[0] == 400


def test_turn_locator_rereads_content_and_rejects_lost_identity(api):
    import json
    from loopx.control_plane.collaboration.source_chat_observation import original_turn_reader

    _, _, route = _source(api)
    _, _, server = api
    store = server.chat_store
    read = original_turn_reader(store, route["session_id"])
    original = read("request-source")
    assert original["turn_id"] == route["turn_id"]
    response = {**original["response"], "message": "Current result"}
    store.update_turn(route["session_id"], route["turn_id"], response=response)
    assert read("request-source")["response"] == response
    path = store._turn_path(route["session_id"], route["turn_id"])
    current = path.read_bytes()
    path.write_text(json.dumps({**original, "client_turn_id": "different-source"}))
    assert read("request-source") is None
    assert _inventory(api, route["session_id"])[1]["coverage"]["unreadable"] == 1
    path.unlink()
    assert read("request-source") is None
    path.write_bytes(current)
    assert _inventory(api, route["session_id"])[1]["pending_count"] == 1


def test_inventory_withholds_all_content_when_closing_source_scope_changes(api, monkeypatch):
    from loopx.presentation.request_control_api import RequestControlMixin

    _, _, route = _source(api)
    scope_read = RequestControlMixin._request_inventory_scope
    verified = []
    def observe_scope(reader, session_id):
        observed, goals, core = scope_read(reader, session_id)
        verified.append(session_id)
        return (observed, [], core) if len(verified) == 2 else (observed, goals, core)
    monkeypatch.setattr(RequestControlMixin, "_request_inventory_scope", observe_scope)
    code, denied = _inventory(api, route["session_id"])
    assert code == 400 and denied["error_code"] == "request_inventory_unavailable"
    assert "rows" not in denied and "Check the current contract" not in str(denied)
