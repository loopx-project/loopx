"""Original steward requests, not inferred worker activity or a second ledger."""
from urllib.parse import urlencode

import pytest

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


def test_inventory_bounds_lifetime_guards_and_keeps_core_io_outside_them(api, monkeypatch):
    from contextlib import contextmanager
    from loopx.control_plane.collaboration import conversation_inventory as inventory
    from loopx.presentation import request_control_api

    _, _, route = _source(api)
    _, f, server = api
    session = server.chat_store.load_session(route["session_id"])
    for number in range(12):
        turn, _ = server.chat_store.create_turn(session["session_id"], client_turn_id=f"batch-{number}",
            message=f"Check artifact {number}", origin="web")
        delivered = deliver(f["runtime"], f["registry"], session=session, turn=turn,
            request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-a"}, source_store=server.chat_store)
        server.chat_store.update_turn(session["session_id"], turn["turn_id"], status="completed",
            response={"context_handoff_receipt": delivered})
        server.chat_store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    original_scope, original_work = inventory.collaboration_goal_scope, inventory.read_linked_work
    original_lock, original_receipts = inventory._request_lock, inventory.read_inbox_transaction
    original_classify = request_control_api.conversation_scope
    classifications = []

    def classify(source):
        classifications.append(source["session_id"])
        return original_classify(source)

    monkeypatch.setattr(request_control_api, "conversation_scope", classify)
    held, batches, locked, receipt_batches = [], [], set(), []

    @contextmanager
    def scope(*args, **kwargs):
        with original_scope(*args, **kwargs) as current:
            held.append(current)
            batches.append(0)
            try:
                yield current
            finally:
                held.pop()

    original_entry = inventory._entry
    def entry(*args, **kwargs):
        assert held, "A request must retain the Goal lifetime guard"
        batches[-1] += 1
        return original_entry(*args, **kwargs)

    def work(*args, **kwargs):
        assert not held, "Core IO must not nest under a Goal lifetime guard"
        return original_work(*args, **kwargs)

    @contextmanager
    def lock(root, request_id, *args, **kwargs):
        with original_lock(root, request_id, *args, **kwargs):
            locked.add(request_id)
            try:
                yield
            finally:
                locked.remove(request_id)

    def receipts(root, request, **kwargs):
        ids = {row["request_id"] for row in request["rows"]}
        assert held and ids <= locked, "Receipt versions need every corresponding request lock"
        receipt_batches.append(len(ids))
        return original_receipts(root, request, **kwargs)

    monkeypatch.setattr(inventory, "collaboration_goal_scope", scope)
    monkeypatch.setattr(inventory, "_entry", entry)
    monkeypatch.setattr(inventory, "read_linked_work", work)
    monkeypatch.setattr(inventory, "_request_lock", lock)
    monkeypatch.setattr(inventory, "read_inbox_transaction", receipts)
    code, value = _inventory(api, route["session_id"], view="history")
    assert code == 200 and value["matched"] == value["pending_count"] == 13
    assert value["coverage"] == {"scan_complete": True, "unreadable": 0, "diagnostics": []}
    assert sum(batches) == 13 and len(batches) == 2 and max(batches) <= 12
    assert sum(receipt_batches) == 13 and len(receipt_batches) == 2 and max(receipt_batches) <= 12
    assert not locked, "A completed read must release every request lock"
    # Two fresh boundaries and at most one additional pure projection for the
    # unchanged source, independently of row count and on each HTTP read.
    assert set(classifications) == {session["session_id"]} and 2 <= len(classifications) <= 3
    first_count = len(classifications)
    assert _inventory(api, route["session_id"], view="history")[0] == 200
    assert 2 <= len(classifications) - first_count <= 3


@pytest.mark.parametrize("failure", ["request_lock", "receipt"])
def test_inventory_isolates_one_failed_request_and_rereads_on_recovery(api, monkeypatch, failure):
    from contextlib import contextmanager
    from loopx.control_plane.collaboration import conversation_inventory as inventory

    _, _, route = _source(api)
    _, f, server = api
    store = server.chat_store
    session = store.load_session(route["session_id"])
    expected = {route["request_id"]: store.load_turn(route["session_id"], route["turn_id"])["message"]}
    for number in range(2):
        message = f"Review artifact {number}\n" + "Retain the complete source question.\n" * 80
        turn, _ = store.create_turn(session["session_id"], client_turn_id=f"isolated-{number}", message=message, origin="web")
        receipt = deliver(f["runtime"], f["registry"], session=session, turn=turn,
            request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-b"}, source_store=store)
        store.update_turn(session["session_id"], turn["turn_id"], status="completed", response={"context_handoff_receipt": receipt})
        store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
        expected[receipt["request_id"]] = message
    broken = route["request_id"]
    original_lock, original_receipts = inventory._request_lock, inventory.read_inbox_transaction

    @contextmanager
    def lock(root, request_id, *args, **kwargs):
        if request_id == broken:
            raise OSError("request lock unavailable")
        with original_lock(root, request_id, *args, **kwargs):
            yield

    def receipts(root, request, **kwargs):
        if any(row["request_id"] == broken for row in request["rows"]):
            raise OSError("request receipt unavailable")
        return original_receipts(root, request, **kwargs)

    monkeypatch.setattr(inventory, "_request_lock" if failure == "request_lock" else "read_inbox_transaction",
        lock if failure == "request_lock" else receipts)
    saved = {path: path.read_bytes() for path in _root(f["runtime"]).rglob("*.json")}
    code, partial = _inventory(api, session["session_id"], view="history")
    assert code == 200 and partial["matched"] == partial["pending_count"] == 2
    assert partial["coverage"] == {"scan_complete": True, "unreadable": 1, "diagnostics": []}
    assert {row["request_id"]: row["source_question"] for row in partial["rows"]} == {
        key: value for key, value in expected.items() if key != broken}
    assert {path: path.read_bytes() for path in saved} == saved
    monkeypatch.setattr(inventory, "_request_lock", original_lock)
    monkeypatch.setattr(inventory, "read_inbox_transaction", original_receipts)
    recovered = _inventory(api, session["session_id"], view="history")[1]
    assert recovered["coverage"]["unreadable"] == 0 and recovered["matched"] == 3
    assert {row["request_id"]: row["source_question"] for row in recovered["rows"]} == expected


def test_inventory_reclassifies_changed_facts_for_the_same_source_session(api, monkeypatch):
    _, _, route = _source(api)
    _, _, server = api
    original_load = server.chat_store.load_session
    reads = []

    def load(session_id):
        session = original_load(session_id)
        if session_id == route["session_id"]:
            reads.append(session_id)
            if len(reads) == 3:
                # The source changed after discovery, then recovered before
                # the closing read. Its row must still withhold the content.
                return {**session, "goal_id": None}
        return session

    monkeypatch.setattr(server.chat_store, "load_session", load)
    code, value = _inventory(api, route["session_id"], view="history")
    assert code == 200 and len(reads) >= 4
    assert value["rows"] == [] and value["pending_count"] == 0
    assert value["coverage"] == {"scan_complete": True, "unreadable": 1, "diagnostics": []}


def test_inventory_rereads_goal_lifetime_between_batches(api, monkeypatch):
    from contextlib import contextmanager
    from loopx.control_plane.collaboration import conversation_inventory as inventory
    from loopx.control_plane.goals.source_session_recreation import RecreateGoalRequest, recreate_goal_instance

    _, _, route = _source(api)
    _, f, server = api
    session = server.chat_store.load_session(route["session_id"])
    for number in range(12):
        turn, _ = server.chat_store.create_turn(session["session_id"], client_turn_id=f"replace-{number}",
            message=f"Check artifact {number}", origin="web")
        delivered = deliver(f["runtime"], f["registry"], session=session, turn=turn,
            request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-a"}, source_store=server.chat_store)
        server.chat_store.update_turn(session["session_id"], turn["turn_id"], status="completed",
            response={"context_handoff_receipt": delivered})
        server.chat_store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    original_scope = inventory.collaboration_goal_scope
    original_decide = inventory.decide_collaboration_lifecycle
    calls, modes = [], []

    @contextmanager
    def scope(*args, **kwargs):
        if calls:
            replaced = recreate_goal_instance(RecreateGoalRequest(registry_path=f["registry"],
                goal_id=f["ref"]["goal_id"], goal_instance_id=f["ref"]["goal_instance_id"], operation_id="replace-during-read"))
            assert replaced["ok"]
        with original_scope(*args, **kwargs) as current:
            calls.append(current.current_goal_ref)
            yield current

    def decide(*args, **kwargs):
        result = original_decide(*args, **kwargs)
        modes.append(result.get("mode"))
        return result

    monkeypatch.setattr(inventory, "collaboration_goal_scope", scope)
    monkeypatch.setattr(inventory, "decide_collaboration_lifecycle", decide)
    code, value = _inventory(api, route["session_id"], view="history")
    assert code == 200 and len(calls) == 2
    assert calls[0] == f["ref"] and calls[1] != f["ref"]
    # The existing typed owner permits proven historical inspection after a
    # replacement; it must observe the new lifetime rather than reuse the old.
    assert modes == ["current_instance"] * 12 + ["historical_read"]
    assert value["matched"] == 13 and value["coverage"] == {"scan_complete": True, "unreadable": 0, "diagnostics": []}

def test_conflicting_receipt_is_diagnosable_but_does_not_authorize_content_or_controls(api):
    control, _, route = _source(api)
    _, f, server = api
    store = server.chat_store
    turn = store.load_turn(route["session_id"], route["turn_id"])
    original_response = turn["response"]
    source = _root(f["runtime"])
    saved = {path: path.read_bytes() for path in source.rglob("*.json")}
    store.update_turn(route["session_id"], route["turn_id"], response={"context_handoff_receipt": {
        **original_response["context_handoff_receipt"], "request_id": "b" * 64, "agent_id": "agent-b"}})
    code, partial = _inventory(api, route["session_id"], view="history")
    assert code == 200 and partial["rows"] == [] and partial["pending_count"] == 0
    assert partial["coverage"] == {"scan_complete": True, "unreadable": 1, "diagnostics": [{
        "request_id": route["request_id"], "goal_id": f["ref"]["goal_id"], "agent_id": "agent-a",
        "code": "initial_delivery_unproved", "reason": "handoff_receipt_conflict"}]}
    assert turn["message"] not in str(partial) and "Check the current contract" not in str(partial)
    assert control()[0] == 400
    for operation in ["revise", "cancel", "recover"]:
        assert control({**route, "operation": operation, "execute": True, "parameters": {}})[0] == 400
    assert {path: path.read_bytes() for path in saved} == saved
    # Only restore the synthetic fixture's original source evidence. Inventory
    # itself cannot repair or invent that evidence, nor retain an old refusal.
    store.update_turn(route["session_id"], route["turn_id"], response=original_response)
    recovered = _inventory(api, route["session_id"], view="history")[1]
    assert recovered["coverage"] == {"scan_complete": True, "unreadable": 0, "diagnostics": []}
    assert recovered["rows"][0]["request_id"] == route["request_id"]
    assert partial["snapshot_digest"] != recovered["snapshot_digest"]


def test_a_later_handoff_does_not_cancel_the_conflicting_original_request(api):
    _, _, route = _source(api)
    _, f, server = api
    store = server.chat_store
    session = store.load_session(route["session_id"])
    turn = store.load_turn(route["session_id"], route["turn_id"])
    later = deliver(f["runtime"], f["registry"], session=session, turn=turn,
        request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-b"}, source_store=store)
    assert later["request_id"] != route["request_id"]
    store.update_turn(route["session_id"], route["turn_id"], response={"context_handoff_receipt": later})
    source = _root(f["runtime"])
    saved = {path: path.read_bytes() for path in source.rglob("*.json")}
    value = _inventory(api, route["session_id"], view="history")[1]
    assert [row["request_id"] for row in value["rows"]] == [later["request_id"]]
    assert value["pending_count"] == 1 and value["coverage"]["unreadable"] == 1
    assert value["coverage"]["diagnostics"][0]["request_id"] == route["request_id"]
    assert {path: path.read_bytes() for path in saved} == saved


def test_conflict_diagnostics_do_not_disclose_a_changed_source_or_scope(api, monkeypatch):
    from loopx.presentation.request_control_api import RequestControlMixin

    _, _, route = _source(api)
    _, f, server = api
    store = server.chat_store
    turn = store.load_turn(route["session_id"], route["turn_id"])
    store.update_turn(route["session_id"], route["turn_id"], response={"context_handoff_receipt": {
        **turn["response"]["context_handoff_receipt"], "request_id": "b" * 64}})
    scope_read = RequestControlMixin._request_inventory_scope
    reads = []
    def changed_scope(reader, session_id):
        observed, goals, core = scope_read(reader, session_id)
        reads.append(session_id)
        return (observed, [], core) if len(reads) == 2 else (observed, goals, core)
    monkeypatch.setattr(RequestControlMixin, "_request_inventory_scope", changed_scope)
    code, denied = _inventory(api, route["session_id"])
    assert code == 400 and "coverage" not in denied and route["request_id"] not in str(denied)
    monkeypatch.setattr(RequestControlMixin, "_request_inventory_scope", scope_read)
    from loopx.control_plane.collaboration.inbox import _read, _write
    path = next(_root(f["runtime"]).glob("entries/*/" + route["request_id"] + ".json"))
    _write(path, {**_read(path), "source_id": "web:another-source"})
    partial = _inventory(api, route["session_id"])[1]
    assert partial["coverage"]["unreadable"] == 1 and partial["coverage"]["diagnostics"] == []


def test_conflict_diagnostic_window_does_not_reduce_the_unreadable_count(api):
    _, _, route = _source(api)
    _, f, server = api
    store = server.chat_store
    session = store.load_session(route["session_id"])
    turns = [store.load_turn(route["session_id"], route["turn_id"])]
    for number in range(12):
        turn, _ = store.create_turn(session["session_id"], client_turn_id=f"conflict-{number}",
            message=f"Review retained export {number}", origin="web")
        receipt = deliver(f["runtime"], f["registry"], session=session, turn=turn,
            request={"goal_id": f["ref"]["goal_id"], "agent_id": "agent-a"}, source_store=store)
        store.update_turn(session["session_id"], turn["turn_id"], status="completed", response={"context_handoff_receipt": receipt})
        store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
        turns.append(store.load_turn(session["session_id"], turn["turn_id"]))
    for turn in turns:
        store.update_turn(session["session_id"], turn["turn_id"], response={"context_handoff_receipt": {
            **turn["response"]["context_handoff_receipt"], "request_id": "b" * 64}})
    value = _inventory(api, session["session_id"], view="history")[1]
    assert value["coverage"]["unreadable"] == 13 and len(value["coverage"]["diagnostics"]) == 12
    assert value["rows"] == [] and value["matched"] == value["pending_count"] == 0
    later_page = _inventory(api, session["session_id"], view="history", offset=1)[1]
    assert later_page["coverage"] == value["coverage"]
    assert later_page["snapshot_digest"] == value["snapshot_digest"]
