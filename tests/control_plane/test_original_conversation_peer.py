"""Original project recipients use the real shared owners, FIFO and native adapter.

Only the model transport is synthetic. Source/Chat roots, File/SQLite state,
binding/source admission, request/revision/result stores and HTTP are real.
"""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import test_chat_ordinary_project as ordinary_fixtures
from test_collaboration_goal_instance import _create_source_registry, _brief, INSTANCE_A
from canonical_authority_fixture import isolate_sqlite_runtime, initialize_canonical_source_fixture
from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings, OriginalConversationRequests
from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.control_plane.collaboration.peers import request as peer_request, read_inbox, consume_return

ordinary = ordinary_fixtures.ordinary


@pytest.fixture(params=["file", "sqlite"])
def project_peer(ordinary, tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, contexts, http, capture, fake, workspace = ordinary
    source = tmp_path / "registered-source"
    registry = _create_source_registry(source)
    root = tmp_path / "coordination"
    state = source / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## User Todo\n\n## Agent Todo\n\n## Completed Work Archive\n")
    initialize_canonical_source_fixture(root, "delivery", state_path=state, provider=request.param)
    runtime.registry_path, runtime.coordination_runtime_root = registry, root
    observation = {"transport_ref": "synthetic-project", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: dict(observation))
    contexts.conversation_bindings = bindings
    ChatExternalConversations(runtime)
    binding = bindings.configure(transport_ref="synthetic-project", project_ref=contexts.available()[0]["project_ref"],
        executor_endpoint_id="codex", project_grant="workspace_read")
    # Model output is determined independently from the supplied response schema
    # and request basis, not from implementation-owned receipt files.
    code = fake.read_text()
    marker = '        print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": response}}), flush=True)'
    added = '''        if request["params"].get("outputSchema"):
            supplied = json.loads(request["params"]["input"][0]["text"].split("Peer request (data):", 1)[1])
            response = json.dumps({"decision": "defer" if supplied["brief"]["purpose"].startswith("Defer") else "adopt",
                "reason": "Independent source and evidence assessment", "text": "Verified original project result.",
                "request_revision": supplied["request_revision"], "request_digest": supplied["request_revision_digest"]})
'''
    assert code.count(marker) == 2
    fake.write_text(code.replace(marker, added + marker, 1))
    session, _ = runtime.open_session(goal_id=None, agent_id="codex", work_dir=workspace, objective="",
        mode="resume_latest", conversation_binding_id=binding["binding_id"], source_context={
            "source_ref": "a" * 24, "sender_ref": observation["operator_ref"], "private_human_message": True})
    ref = {"goal_id": "delivery", "goal_instance_id": INSTANCE_A}
    parent = peer_request(root, registry, "delivery", "reviewer", "builder", "original-source", _brief("Review project evidence"), caller_goal_ref=ref)
    requester = {"goal_ref": ref, "agent_id": "builder"}
    service = OriginalConversationRequests(runtime)
    view = service.management_view(session["session_id"])
    grant = {"grant": True, "execute": True, "requester": requester, "parent_request_id": parent["request_id"],
        "expected_policy_sha256": view["policy_sha256"]}
    body = {"requester": requester, "parent_request_id": parent["request_id"],
        "recipient": view["recipient"], "operation_id": "project-consultation", "brief": _brief("Verify project evidence")}
    yield {"store": store, "runtime": runtime, "service": service, "http": http, "capture": capture,
        "observation": observation, "binding": binding, "session": session, "body": body, "grant": grant,
        "root": root, "registry": registry, "ref": ref, "fake": fake}
    runtime.close()


def allow(f):
    f["grant"]["expected_policy_sha256"] = f["service"].management_view(f["session"]["session_id"])["policy_sha256"]
    return f["service"].configure(f["session"]["session_id"], f["grant"])


def test_original_project_has_explicit_grant_and_returns_through_requester_inbox(project_peer):
    f = project_peer
    sid = f["session"]["session_id"]
    f["body"]["brief"]["context"] = 'if ready:\n    print("safe")\n  keep  two spaces\n\t保留\u00a0空格'
    assert f["service"].discover(f["body"]["requester"], f["body"]["parent_request_id"])["recipients"] == []
    with pytest.raises(ValueError, match="explicit grant"):
        f["service"].publish(f["body"])
    allow(f)
    discovered = f["service"].discover(f["body"]["requester"], f["body"]["parent_request_id"])
    assert discovered["recipients"][0]["recipient"] == f["body"]["recipient"]
    first = f["service"].publish(f["body"])
    terminal = f["runtime"].wait_for_turn(session_id=sid, turn_id=first["turn_id"], timeout_sec=15)
    assert terminal["status"] == "completed", json.dumps(terminal, ensure_ascii=False)
    saved = f["store"].load_session(sid)
    assert saved["goal_id"] is None and saved["upstream_thread_id"] == f["session"]["upstream_thread_id"]
    peer_messages = [row for row in f["store"].messages(sid) if row.get("origin") == "peer"]
    assert [row["text"] for row in peer_messages] == [f["body"]["brief"]["purpose"]]
    result = read_inbox(f["root"], f["registry"], "delivery", "builder", caller_goal_ref=f["ref"])
    assert result["peer_returns"]["items"][0]["text"] == "Verified original project result."
    assert result["peer_returns"]["items"][0]["decision"] == "adopt"
    assert consume_return(f["root"], "delivery", "builder", first["request_id"], registry=f["registry"], caller_goal_ref=f["ref"])["consumed"]
    assert "peer_returns" not in read_inbox(f["root"], f["registry"], "delivery", "builder", caller_goal_ref=f["ref"])
    again = f["service"].publish(f["body"])
    assert again["turn_id"] == first["turn_id"] and again["replayed"]
    packets = [json.loads(line) for line in f["capture"].read_text().splitlines()]
    assert sum(p.get("method") == "turn/start" for p in packets) == 1
    native_input = next(p["params"]["input"][0]["text"] for p in packets if p.get("method") == "turn/start")
    supplied = json.loads(native_input.split("Peer request (data):", 1)[1])
    assert supplied["brief"] == f["body"]["brief"]
    assert not any(p.get("method", "").startswith("thread/goal") for p in packets)
    assert not any("dynamicTools" in p.get("params", {}) for p in packets)


def test_queue_recovery_and_concurrent_retry_preserve_fifo_and_original_session(project_peer, monkeypatch):
    f = project_peer
    sid = f["session"]["session_id"]
    allow(f)
    monkeypatch.setattr(f["runtime"], "resume_session_queue", lambda **_: None)
    human, _ = f["runtime"].enqueue_turn(session_id=sid, client_turn_id="human-first", message="Human input", origin="web", work_dir=None, objective="")
    enqueue = f["store"].create_queued_turn
    with monkeypatch.context() as patch:
        patch.setattr(f["store"], "create_queued_turn", lambda *_, **__: (_ for _ in ()).throw(OSError("synthetic publication/queue loss")))
        with pytest.raises(OSError, match="queue loss"):
            f["service"].publish(f["body"])
    # A new adapter instance observes the original request, route and FIFO.
    service = OriginalConversationRequests(f["runtime"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.publish(f["body"]), range(4)))
    assert len({r["turn_id"] for r in results}) == 1
    queued = f["store"].queued_turns(sid)
    assert [row["turn_id"] for row in queued] == [human["turn_id"], results[0]["turn_id"]]
    assert [row["origin"] for row in queued] == ["web", "peer"]
    for change in [{"brief": _brief("Changed payload")}, {"operation_id": "project-consultation", "parent_request_id": "b" * 64}]:
        with pytest.raises((ValueError, FileNotFoundError)):
            service.publish({**f["body"], **change})
    assert len(f["store"].queued_turns(sid)) == 2
    assert enqueue is not None


@pytest.mark.parametrize("change", ["revoke", "provider", "operator", "closed"])
def test_worker_rechecks_current_source_and_original_recipient(project_peer, monkeypatch, change):
    f = project_peer
    allow(f)
    monkeypatch.setattr(f["runtime"], "resume_session_queue", lambda **_: None)
    first = f["service"].publish(f["body"])
    turn = f["store"].load_turn(f["session"]["session_id"], first["turn_id"])
    f["service"].validate_turn(f["session"]["session_id"], turn)
    if change == "revoke":
        view = f["service"].management_view(f["session"]["session_id"])
        f["service"].configure(f["session"]["session_id"], {**view["grants"][0], "grant": False, "execute": True,
            "expected_policy_sha256": view["policy_sha256"]})
    elif change in {"provider", "operator"}:
        f["observation"][change + "_ref"] = "e" * 24
    else:
        f["store"].update_session(f["session"]["session_id"], status="closed")
    with pytest.raises(ValueError):
        f["service"].validate_turn(f["session"]["session_id"], turn)
    assert f["store"].turn_for_client(f["session"]["session_id"], "peer-" + first["request_id"])["turn_id"] == first["turn_id"]


def test_source_revision_cancel_and_receiver_defer_are_independent(project_peer, monkeypatch):
    f = project_peer
    allow(f)
    original_resume = f["runtime"].resume_session_queue
    monkeypatch.setattr(f["runtime"], "resume_session_queue", lambda **_: None)
    first = f["service"].publish(f["body"])
    view = f["service"]._view(f["service"]._entry(first["request_id"], f["body"]["recipient"]))
    control = {"requester": f["body"]["requester"], "recipient": f["body"]["recipient"], "request_id": first["request_id"], "control": "revise",
        "parameters": {"operation_id": "correction", "expected_revision": view["revision"], "expected_digest": view["revision_digest"],
            "brief": _brief("Defer until independent evidence exists"), "reason": "Correct source evidence"}}
    changed = f["service"].control(control)
    assert changed["request_control"]["revision"] == 2
    with pytest.raises(ValueError, match="identity conflict"):
        f["service"].control({**control, "parameters": {**control["parameters"], "reason": "Different correction"}})
    monkeypatch.setattr(f["runtime"], "resume_session_queue", original_resume)
    resumed = f["service"].publish(f["body"])
    result = f["runtime"].wait_for_turn(session_id=f["session"]["session_id"], turn_id=resumed["turn_id"], timeout_sec=15)
    assert result["status"] == "completed", json.dumps(result, ensure_ascii=False)
    returned = read_inbox(f["root"], f["registry"], "delivery", "builder", caller_goal_ref=f["ref"])["peer_returns"]["items"][0]
    assert returned["decision"] == "defer" and returned["request_revision"] == 2
    current = changed["request_control"]
    cancelled = f["service"].control({**control, "control": "cancel", "parameters": {"operation_id": "cancel-original",
        "expected_revision": current["revision"], "expected_digest": current["revision_digest"], "reason": "No further work"}})
    assert cancelled["request_control"]["control_state"] == "cancelled"
    with pytest.raises(ValueError, match="cancelled"):
        f["service"].publish(f["body"])


def test_saved_model_output_recovers_a_lost_return_without_reexecution(project_peer, monkeypatch):
    from loopx.control_plane.collaboration import inbox
    f = project_peer
    allow(f)
    write = inbox._write
    failed = False
    def lose_result(path, value):
        nonlocal failed
        if path.parent.parent.name == "replies" and not failed:
            failed = True
            raise OSError("synthetic return write loss")
        return write(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(inbox, "_write", lose_result)
        first = f["service"].publish(f["body"])
        terminal = f["runtime"].wait_for_turn(session_id=f["session"]["session_id"], turn_id=first["turn_id"], timeout_sec=15)
    assert terminal["status"] == "failed" and terminal["collaboration_answer"]["text"] == "Verified original project result."
    recovered = f["service"].publish(f["body"])
    assert recovered["turn_id"] == first["turn_id"] and recovered["recovered_result"]["ok"]
    packets = [json.loads(line) for line in f["capture"].read_text().splitlines()]
    assert sum(p.get("method") == "turn/start" for p in packets) == 1
    assert read_inbox(f["root"], f["registry"], "delivery", "builder", caller_goal_ref=f["ref"])["peer_returns"]["items"][0]["text"] == "Verified original project result."
    with pytest.raises(ValueError, match="immutable"):
        f["store"].update_turn(f["session"]["session_id"], first["turn_id"], collaboration_answer={**terminal["collaboration_answer"], "text": "Replacement"})


def test_completed_request_correction_survives_queue_loss_restart_and_retry(project_peer, monkeypatch):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.control_plane.collaboration import inbox
    from loopx.control_plane.collaboration.inbox import _root, result_paths
    f = project_peer
    sid = f["session"]["session_id"]
    allow(f)
    first = f["service"].publish(f["body"])
    original = f["runtime"].wait_for_turn(session_id=sid, turn_id=first["turn_id"], timeout_sec=15)
    assert original["status"] == "completed"
    assert read_inbox(f["root"], f["registry"], "delivery", "builder", caller_goal_ref=f["ref"])["peer_returns"]["items"][0]["request_revision"] == 1
    assert consume_return(f["root"], "delivery", "builder", first["request_id"], registry=f["registry"], caller_goal_ref=f["ref"])["consumed"]
    retained = {path: path.read_bytes() for path in result_paths(_root(f["root"]) / "replies" / first["request_id"])}
    view = f["service"]._view(f["service"]._entry(first["request_id"], f["body"]["recipient"]))
    control = {"requester": f["body"]["requester"], "recipient": f["body"]["recipient"],
        "request_id": first["request_id"], "control": "revise", "parameters": {"operation_id": "after-answer",
            "expected_revision": view["revision"], "expected_digest": view["revision_digest"],
            "brief": _brief("Verify corrected project evidence"), "reason": "The source changed after the first answer"}}
    # Commit a real correction, then lose only its FIFO write. Retrying the
    # original correction must recover that revision, not mutate its predecessor.
    with monkeypatch.context() as patch:
        patch.setattr(f["store"], "create_queued_turn", lambda *_, **__: (_ for _ in ()).throw(OSError("synthetic corrected queue loss")))
        with pytest.raises(OSError, match="corrected queue loss"):
            f["service"].control(control)
    assert f["service"]._view(f["service"]._entry(first["request_id"], f["body"]["recipient"]))["revision"] == 2
    monkeypatch.setattr(f["runtime"], "resume_session_queue", lambda **_: None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: f["service"].control(control), range(4)))
    queued = f["store"].queued_turns(sid)
    assert len(queued) == 1 and queued[0]["turn_id"] != first["turn_id"]
    current = f["service"].management_view(sid)["requests"][0]
    assert current["queue_status"] == "queued"
    assert current["request_control_view"]["assessment"] is None
    assert [reply["request_revision"] for reply in current["returns"]] == [1]
    f["runtime"].close()
    restarted = ChatRuntimeController(store=f["store"], codex_bin=str(f["fake"]), project_contexts=f["runtime"].project_contexts,
        registry_path=f["registry"])
    restarted.coordination_runtime_root = f["root"]
    ChatExternalConversations(restarted)
    try:
        service = OriginalConversationRequests(restarted)
        write = inbox._write
        def lose_corrected_result(path, value):
            if path.parent.parent.name == "replies":
                raise OSError("synthetic corrected return loss")
            return write(path, value)
        with monkeypatch.context() as patch:
            patch.setattr(inbox, "_write", lose_corrected_result)
            resumed = service.publish(f["body"])
            assert resumed["turn_id"] == queued[0]["turn_id"]
            done = restarted.wait_for_turn(session_id=sid, turn_id=resumed["turn_id"], timeout_sec=15)
        assert done["status"] == "failed" and done["collaboration_answer"]["request_revision"] == 2
        assert service.publish(f["body"])["recovered_result"]["ok"]
        returned = read_inbox(f["root"], f["registry"], "delivery", "builder", caller_goal_ref=f["ref"])["peer_returns"]["items"]
        assert [reply["request_revision"] for reply in returned] == [2]
        assert returned[0]["decision"] == "adopt"
        service.control(control)
        service.publish(f["body"])
        packets = [json.loads(line) for line in f["capture"].read_text().splitlines()]
        assert sum(p.get("method") == "turn/start" for p in packets) == 2
        assert f["store"].load_turn(sid, first["turn_id"])["collaboration_answer"] == original["collaboration_answer"]
        assert all(path.read_bytes() == value for path, value in retained.items())
        assert f["store"].load_session(sid)["goal_id"] is None
        assert f["store"].load_session(sid)["upstream_thread_id"] == f["session"]["upstream_thread_id"]
    finally:
        restarted.close()


def test_correction_at_native_write_does_not_execute_twice(project_peer, monkeypatch):
    f = project_peer
    allow(f)
    sid = f["session"]["session_id"]
    native = f["runtime"].adapters[sid].session
    send = native.send
    corrected = False
    def correct_then_send(*args, **kwargs):
        nonlocal corrected
        if not corrected:
            corrected = True
            row = f["service"].management_view(sid)["requests"][0]
            current = row["request_control_view"]
            f["service"].control({"requester": f["body"]["requester"], "recipient": f["body"]["recipient"],
                "request_id": row["request_id"], "control": "revise", "parameters": {"operation_id": "before-input",
                    "expected_revision": current["revision"], "expected_digest": current["revision_digest"],
                    "brief": _brief("Verify changed evidence"), "reason": "Changed before native input"}})
        return send(*args, **kwargs)
    monkeypatch.setattr(native, "send", correct_then_send)
    first = f["service"].publish(f["body"])
    f["runtime"].wait_for_turn(session_id=sid, turn_id=first["turn_id"], timeout_sec=15)
    current = f["service"].publish(f["body"])
    done = f["runtime"].wait_for_turn(session_id=sid, turn_id=current["turn_id"], timeout_sec=15)
    assert done["status"] == "completed", json.dumps(done, ensure_ascii=False)
    assert done["collaboration_answer"]["request_revision"] == 2
    packets = [json.loads(line) for line in f["capture"].read_text().splitlines()]
    assert sum(p.get("method") == "turn/start" for p in packets) == 1


def test_loopback_http_requires_exact_runtime_and_explicit_source_selection(project_peer):
    f = project_peer
    sid = f["session"]["session_id"]
    path = f"/api/chat/sessions/{sid}/collaboration"
    status, view = f["http"](path)
    assert status == 200 and view["available"] and not view["grants"]
    status, granted = f["http"](path, {**f["grant"], "expected_policy_sha256": view["policy_sha256"]})
    assert status == 200 and granted["executed"]
    status, wrong = f["http"]("/api/chat/collaboration", {**f["body"], "operation": "request", "coordination_runtime_root": str(f["root"].parent / "other")})
    assert status == 409 and not wrong["ok"]
    assert not f["store"].queued_turns(sid)
    status, received = f["http"]("/api/chat/collaboration", {**f["body"], "operation": "request", "coordination_runtime_root": str(f["root"])})
    assert status == 202 and received["recipient"]["session_id"] == sid
    assert f["runtime"].wait_for_turn(session_id=sid, turn_id=received["turn_id"], timeout_sec=15)["status"] == "completed"
    status, final = f["http"](path)
    assert status == 200 and final["requests"][0]["returns"][0]["text"] == "Verified original project result."


def test_existing_thread_resumes_without_new_tools_or_goal_mode(project_peer):
    from loopx.chat_runtime import ChatRuntimeController
    f = project_peer
    sid = f["session"]["session_id"]
    human, _ = f["runtime"].enqueue_turn(session_id=sid, client_turn_id="prior-human", message="A prior human question",
        work_dir=None, objective="", origin="web")
    assert f["runtime"].wait_for_turn(session_id=sid, turn_id=human["turn_id"], timeout_sec=10)["status"] == "completed"
    original = f["store"].load_session(sid)
    allow(f)
    f["runtime"].close()
    restarted = ChatRuntimeController(store=f["store"], codex_bin=str(f["fake"]), project_contexts=f["runtime"].project_contexts,
        registry_path=f["registry"])
    restarted.coordination_runtime_root = f["root"]
    ChatExternalConversations(restarted)
    try:
        first = OriginalConversationRequests(restarted).publish(f["body"])
        final = restarted.wait_for_turn(session_id=sid, turn_id=first["turn_id"], timeout_sec=15)
        assert final["status"] == "completed", json.dumps(final)
        assert f["store"].load_session(sid)["upstream_thread_id"] == original["upstream_thread_id"]
        assert f["store"].load_session(sid)["goal_id"] is None
        packets = [json.loads(line) for line in f["capture"].read_text().splitlines()]
        assert sum(p.get("method") == "thread/start" for p in packets) == 1
        resumes = [p for p in packets if p.get("method") == "thread/resume"]
        assert len(resumes) == 1 and resumes[0]["params"]["threadId"] == original["upstream_thread_id"]
        assert not any("dynamicTools" in p.get("params", {}) for p in packets)
        turns = [p for p in packets if p.get("method") == "turn/start"]
        assert len(turns) == 2 and "outputSchema" not in turns[0]["params"] and "outputSchema" in turns[1]["params"]
    finally:
        restarted.close()


def test_revocation_at_native_write_guard_prevents_model_input(project_peer, monkeypatch):
    f = project_peer
    allow(f)
    sid = f["session"]["session_id"]
    native = f["runtime"].adapters[sid].session
    send = native.send
    def revoke_then_send(*args, **kwargs):
        view = f["service"].management_view(sid)
        f["service"].configure(sid, {**view["grants"][0], "grant": False, "execute": True,
            "expected_policy_sha256": view["policy_sha256"]})
        return send(*args, **kwargs)
    monkeypatch.setattr(native, "send", revoke_then_send)
    first = f["service"].publish(f["body"])
    final = f["runtime"].wait_for_turn(session_id=sid, turn_id=first["turn_id"], timeout_sec=15)
    assert final["status"] == "failed"
    assert not any(json.loads(line).get("method") == "turn/start" for line in f["capture"].read_text().splitlines())


def test_original_native_thread_requires_one_canonical_session(project_peer):
    f = project_peer
    allow(f)
    original = f["session"]
    other = f["store"].create_session(goal_id=None, agent_id="codex",
        adapter_kind=original["adapter_kind"], upstream_thread_id=original["upstream_thread_id"],
        channel_id=original["channel_id"], project_context=original["project_context"])
    with pytest.raises(ValueError, match="ambiguous Session ownership"):
        f["service"].publish(f["body"])
    assert not f["store"].queued_turns(original["session_id"])
    # Closing the duplicate restores availability without changing the original.
    f["store"].update_session(other["session_id"], status="closed")
    assert f["service"].management_view(original["session_id"])["available"]


def test_original_return_route_is_checked_before_model_input_and_requester_read(project_peer, monkeypatch):
    from loopx.control_plane.collaboration.inbox import _read, _root, _write
    f = project_peer
    sid = f["session"]["session_id"]
    allow(f)
    original_resume = f["runtime"].resume_session_queue
    monkeypatch.setattr(f["runtime"], "resume_session_queue", lambda **_: None)
    first = f["service"].publish(f["body"])
    turn = f["store"].load_turn(sid, first["turn_id"])
    path = _root(f["root"]) / "roundtrips" / (first["request_id"] + ".json")
    original = _read(path)
    _write(path, {**original, "source_id": "peer:another-source"})
    with pytest.raises(ValueError, match="return route identity"):
        f["service"].validate_turn(sid, turn)
    assert not any(json.loads(line).get("method") == "turn/start" for line in f["capture"].read_text().splitlines())
    _write(path, original)
    f["service"].validate_turn(sid, turn)
    monkeypatch.setattr(f["runtime"], "resume_session_queue", original_resume)
    f["service"].publish(f["body"])
    assert f["runtime"].wait_for_turn(session_id=sid, turn_id=first["turn_id"], timeout_sec=15)["status"] == "completed"
    _write(path, {**original, "source_id": "peer:another-source"})
    with pytest.raises(ValueError, match="return route identity"):
        read_inbox(f["root"], f["registry"], "delivery", "builder", caller_goal_ref=f["ref"])
