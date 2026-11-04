"""Worker returns use the original Core-bound private App, including recovery."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from test_native_steward_private import steward, finish  # noqa: F401

from loopx.capabilities.manager_context import POLICY_SCHEMA, _root, _write, deliver
from loopx.chat_store import _atomic_write_json, _read_json
from loopx.extensions.lark.manager_returns import LarkManagerReturnTransport
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.manager_context import acknowledge
from loopx.capabilities.manager_context.roundtrip import drain, report, reply_status
from loopx.control_plane.collaboration.turn_context_observation import read_delegation_turn_context


@pytest.fixture(params=["shared", "separate"])
def return_root(steward, request):  # noqa: F811
    store, runtime, _, transport, _, _, _ = steward
    root = transport.runtime_root if request.param == "shared" else store.root.parent / "coordination"
    registry = json.loads(runtime.registry_path.read_text())
    registry["common_runtime_root"] = str(root)
    runtime.registry_path.write_text(json.dumps(registry))
    runtime.coordination_runtime_root = root
    return root


@pytest.fixture(params=["native", "legacy"])
def private_return(steward, request, return_root, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, binding, _, _ = steward
    if request.param == "legacy":
        # Exercise historical provider provenance through the ingress writer,
        # without rewriting a current receipt or changing the audience.
        from loopx.capabilities import manager_context
        register = manager_context.register_ingress

        def legacy_ingress(root, **kwargs):
            if kwargs["message"] == "Ask the authorized worker to assess the constraint":
                kwargs["source_id"] = "lark:om_original"
            register(root, **kwargs)

        monkeypatch.setattr(manager_context, "register_ingress", legacy_ingress)
    transport.admit("steward-app", provider.event("steward-app", "delegate", "/delegate --tokens 12000 Inspect README"))
    transport.reconcile()
    proposal = transport.core.actions.store.list()[0]
    transport.admit("steward-app", provider.event("steward-app", "confirm", "/confirm " + proposal["proposal_id"]))
    transport.reconcile()
    applied = transport.core.actions.load(proposal["proposal_id"])
    resources = applied["receipt"]["resource_ids"]
    assert finish(runtime, resources)["status"] == "completed"
    transport.reconcile()
    event = provider.event("steward-app", "original", "Ask the authorized worker to assess the constraint")
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    row = next(r for r in transport.core.pending() if r["message"] == event["content"])
    assert finish(runtime, row)["status"] == "completed"
    transport.reconcile()
    session = store.load_session(row["session_id"])
    turn = store.load_turn(row["session_id"], row["turn_id"])
    target = {"goal_id": resources["goal_id"], "agent_id": "codex"}
    _write(_root(return_root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {
        session["channel_id"]: {"local_delivery_scope": "selected", "sender_ids": [binding["operator_ref"]], "targets": [target]}}})
    source_id = row["request_ref"] if request.param == "native" else "lark:" + event["message_id"]
    # Mirror Chat handoff: the trusted source store pins the original host.
    delivered = deliver(return_root, runtime.registry_path, session=session, turn=turn,
                        request=target, source_store=store)
    route = {**target, "request_id": delivered["request_id"], "session_id": session["session_id"], "source_id": source_id}
    original_runner = provider.__call__
    replies = []

    def runner(args, cwd=None, timeout=None):
        if "+messages-reply" not in args and not ("+messages-send" in args and "--content" in args):
            return original_runner(args, cwd, timeout)
        provider.calls.append(list(args))
        assert args[args.index("--profile") + 1] == "steward-app"
        if "--message-id" in args:
            assert args[args.index("--message-id") + 1] == event["message_id"]
        else:
            assert args[args.index("--chat-id") + 1] == event["chat_id"]
        content = args[args.index("--content") + 1]
        if "--dry-run" in args:
            data = {"api": [{"body": {"msg_type": "post", "content": content}}]}
            if getattr(provider, "revoke_before_send", False):
                _write(_root(return_root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {}})
        else:
            replies.append(list(args))
            ref = f"om_out_worker_{len(replies)}"
            provider.messages[ref] = {"message_id": ref, "msg_type": "post", "body": {"content": content}}
            data = {"data": {"message_id": ref}}
        return {"returncode": 0, "stdout": json.dumps(data)}

    transport.runner = runner
    server = SimpleNamespace(registry_path=runtime.registry_path, lark_private_conversations=transport,
        lark_goal_topic_runtime=SimpleNamespace(snapshot_provider=lambda: pytest.fail("private return cannot use legacy Goal bindings")))
    sender = LarkManagerReturnTransport(server, return_root)
    # The production transport uses this same profile-aware CLI runner.
    yield sender, session, turn, route, row, provider, transport, replies


def test_original_private_result_and_saved_attempt_recovery(private_return):
    sender, session, turn, route, row, provider, transport, replies = private_return
    provider.verify_replies = False
    attempts = []
    result = sender.send_with_attempt(route, session, turn, "Concrete worker conclusion", attempts.append)
    assert not result["reply_verified"] and len(replies) == 1 and len(attempts) == 1
    # A later ordinary conversation does not redirect the saved worker result.
    transport.admit("steward-app", provider.event("steward-app", "new", "/new"))
    provider.verify_replies = True
    assert sender.verify(route, session, turn, "Concrete worker conclusion", attempts[0])["reply_verified"]
    assert len(replies) == 1
    assert not any("chats" in call or "+chat-members-list" in call for call in provider.calls)
    assert transport.core.read_request(row["request_ref"])["session_id"] == session["session_id"]


def test_private_return_rechecks_grant_after_preview(private_return):
    sender, session, turn, route, _, provider, _, replies = private_return
    provider.revoke_before_send = True
    with pytest.raises(ValueError):
        sender(route, session, turn, "Must not send after revocation")
    assert replies == []


def test_private_return_recovery_rechecks_binding_without_resending(private_return):
    sender, session, turn, route, row, provider, transport, replies = private_return
    provider.verify_replies = False
    attempts = []
    assert not sender.send_with_attempt(route, session, turn, "Worker conclusion", attempts.append)["reply_verified"]
    transport.bindings.disconnect(row["binding_id"], expected_revision=transport.bindings.read()["revision"])
    with pytest.raises(ValueError):
        sender.verify(route, session, turn, "Worker conclusion", attempts[0])
    assert len(replies) == 1


@pytest.mark.parametrize("fault", ["binding", "app", "workspace", "source", "native_turn", "transport_turn", "scope", "policy", "receipt"])
def test_private_return_rejects_changed_authority_or_original_source(private_return, monkeypatch, fault):
    sender, session, turn, route, row, provider, transport, replies = private_return
    if fault == "binding":
        transport.bindings.disconnect(row["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault == "app":
        observe = transport.bindings.observe
        monkeypatch.setattr(transport.bindings, "observe", lambda p: {**observe(p), "provider_ref": "f" * 24})
    elif fault == "workspace":
        monkeypatch.setattr(transport.bindings.projects, "available", lambda: [])
    elif fault == "source":
        record = _read_json(transport.root / f"{row['request_ref']}.json")
        provider.messages[record["event"]["message_id"]]["sender"]["id"] = "ou_other"
    elif fault in {"native_turn", "transport_turn"}:
        root = transport.core.root if fault == "native_turn" else transport.root
        path = root / f"{row['request_ref']}.json"
        value = _read_json(path)
        value["turn_id"] = "another-turn"
        _atomic_write_json(path, value)
    elif fault == "scope":
        binding = transport._binding("steward-app")
        transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id=binding["executor_endpoint_id"], context_kind="steward", goal_scope="selected")
        # An explicit selected scope no longer includes the commissioned Goal.
        # Empty goal_ids alone does not revoke the current all_registered default.
        value = transport.bindings.read()
        next(b for b in value["bindings"] if b["binding_id"] == row["binding_id"])["goal_ids"] = []
        _atomic_write_json(transport.bindings.path, value)
    elif fault == "policy":
        _write(_root(sender.root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {}})
    else:
        config, _ = transport.return_inbox(route=route, session=session, turn=turn)
        from loopx.extensions.lark.event_inbox import load_lark_event_inbox_config
        inbox = load_lark_event_inbox_config(project=transport.runtime_root, config_path=config)["inbox_path"]
        (inbox / "processed.json").unlink()
    try:
        result = sender(route, session, turn, "Must not reach another audience")
        assert not result["reply_verified"]
    except ValueError:
        pass
    assert replies == []


@pytest.mark.parametrize("conclusion", [
    "Verified worker result for the original private audience",
    "Verified worker result: contact @contributor.\\nDetails: <at unsupported>quoted</at>",
])
def test_production_pump_returns_to_original_private_source_after_restart(private_return, conclusion):
    sender, session, turn, route, _, provider, transport, replies = private_return
    controller = transport.core.controller
    store = controller.store
    before_turns = sorted((store.root / "sessions" / session["session_id"] / "turns").glob("*.json"))
    capture = store.root.parent.parent / "requests.jsonl"
    before_host_calls = capture.read_bytes()
    rid = route["request_id"]
    acknowledge(sender.root, route["goal_id"], route["agent_id"], rid, "adopt", "Receiver independently accepts this scope")
    report(sender.root, route["goal_id"], route["agent_id"], rid, "conclusion", conclusion)
    provider.verify_replies = False
    drain(sender.root, controller.registry_path, store, sender)
    assert len(replies) == 1
    assert reply_status(sender.root, route)[0]["status"] == "verification_required"
    provider.verify_replies = True
    # Reload the actual Chat store; the persisted attempt drives read-only recovery.
    recovered = ChatSessionStore(store.root.parent)
    drain(sender.root, controller.registry_path, recovered, sender, now=datetime.now(timezone.utc) + timedelta(minutes=10))
    assert reply_status(sender.root, route)[0]["status"] == "delivered"
    assert len(replies) == 1
    returned = [r for r in recovered.messages(session["session_id"]) if r.get("origin") == "manager_followup"]
    assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
    assert conclusion in returned[0]["text"]
    if "@contributor" in conclusion:
        content = replies[0][replies[0].index("--content") + 1]
        assert "＠contributor" in content and "@contributor" not in content
        assert "‹at unsupported>quoted‹/at>" in content
        assert not any("+chat-members-list" in call for call in provider.calls)
    assert "Receiver independently" not in returned[0]["text"]
    assert sorted((store.root / "sessions" / session["session_id"] / "turns").glob("*.json")) == before_turns
    assert capture.read_bytes() == before_host_calls


@pytest.mark.parametrize("revoke_before_recovery", [False, True])
def test_partial_return_survives_saved_attempt_recovery_until_final_delivery(private_return, revoke_before_recovery):
    sender, session, turn, route, _, provider, transport, replies = private_return
    controller = transport.core.controller
    store = controller.store
    capture = store.root.parent.parent / "requests.jsonl"
    before_host_calls = capture.read_bytes()
    original_turn = store._turn_path(session["session_id"], turn["turn_id"]).read_bytes()
    turns = sorted((store.root / "sessions" / session["session_id"] / "turns").glob("*.json"))
    remainder = {"summary": "Validate the installed repair", "resume_when": "The isolated package is available"}
    rid = route["request_id"]
    acknowledge(sender.root, route["goal_id"], route["agent_id"], rid, "adopt", "Repair and validate")
    report(sender.root, route["goal_id"], route["agent_id"], rid, "conclusion",
           "The source repair is ready.", remaining_work=remainder)
    partial = _root(sender.root) / "replies" / rid / "conclusion.json"
    original_result = partial.read_bytes()
    provider.verify_replies = False
    drain(sender.root, controller.registry_path, store, sender)
    assert len(replies) == 1
    assert reply_status(sender.root, route)[0]["status"] == "verification_required"
    partial_content = replies[0][replies[0].index("--content") + 1]
    assert all(value in partial_content for value in remainder.values())

    # Reload the saved attempt. Revocation still wins over that historical proof.
    recovered = ChatSessionStore(store.root.parent)
    resumed_sender = LarkManagerReturnTransport(sender.server, sender.root)
    provider.verify_replies = True
    clock = datetime.now(timezone.utc) + timedelta(minutes=10)
    if revoke_before_recovery:
        policy_path = _root(sender.root) / "policy.json"
        policy = policy_path.read_bytes()
        _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {}})
        drain(sender.root, controller.registry_path, recovered, resumed_sender, now=clock)
        status = reply_status(sender.root, route)[0]
        assert (status["status"], status["error"]) == ("explicit_unverified", "return_authorization_unavailable")
        assert len(replies) == 1
        policy_path.write_bytes(policy)
        # Restoring access does not automatically resume a stopped publication.
        clock += timedelta(minutes=10)
    drain(sender.root, controller.registry_path, recovered, resumed_sender, now=clock)
    partial_status = "explicit_unverified" if revoke_before_recovery else "delivered"
    assert reply_status(sender.root, route)[0]["status"] == partial_status
    assert len(replies) == 1
    agenda = read_delegation_turn_context(sender.root, controller.registry_path,
                                          route["goal_id"], route["agent_id"])["unfinished_delegations"]
    assert [(item["request_id"], item["step"], item.get("remaining_work")) for item in agenda["items"]] == [
        (rid, "review_request_work", remainder)]

    report(sender.root, route["goal_id"], route["agent_id"], rid, "conclusion",
           "The isolated repair passed the agreed validation.", update_id="qualified-result")
    drain(sender.root, controller.registry_path, recovered, resumed_sender, now=clock + timedelta(minutes=20))
    drain(sender.root, controller.registry_path, ChatSessionStore(store.root.parent), resumed_sender,
          now=clock + timedelta(minutes=30))
    statuses = [item["status"] for item in reply_status(sender.root, route)]
    agenda = read_delegation_turn_context(sender.root, controller.registry_path,
        route["goal_id"], route["agent_id"])["unfinished_delegations"]["items"]
    returned = [item for item in recovered.messages(session["session_id"])
                if item.get("origin") == "manager_followup"]
    if revoke_before_recovery:
        # A newer result cannot overtake an uncertain predecessor, even after
        # access returns. Preserve the exact failure instead of resending it.
        assert len(replies) == 1 and statuses == ["explicit_unverified", "queued"]
        assert [(item["request_id"], item["step"], item["pending_return_count"]) for item in agenda] == [
            (rid, "await_return_delivery", 2)]
        assert len(returned) == 1
    else:
        assert len(replies) == 2 and statuses == ["delivered", "delivered"]
        assert agenda == []
        assert len(returned) == 2
        final_content = replies[1][replies[1].index("--content") + 1]
        assert "The isolated repair passed" in final_content
        assert "The isolated repair passed" in returned[1]["text"]
        assert remainder["summary"] not in returned[1]["text"]
    assert all(item["turn_id"] == turn["turn_id"] for item in returned)
    assert all(value in returned[0]["text"] for value in remainder.values())
    assert partial.read_bytes() == original_result
    assert store._turn_path(session["session_id"], turn["turn_id"]).read_bytes() == original_turn
    assert sorted((store.root / "sessions" / session["session_id"] / "turns").glob("*.json")) == turns
    assert capture.read_bytes() == before_host_calls


@pytest.fixture
def web_return(private_return):
    """Use the shipped HTTP input, File store, return pump and Lark outbox."""
    import http.client
    import threading
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.extensions.lark.conversation_identity import identity_ref
    from test_lark_private_conversations import _drain_app_projection

    sender, session, _, original_route, _, provider, transport, _ = private_return
    runtime, store = transport.core.controller, transport.core.controller.store
    transport.runner = provider
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.runtime_root, server.registry_path = transport.runtime_root, runtime.registry_path
    server.selected_goal_id, server.verbose = None, False
    server.lark_private_conversations = transport
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET" if body is None else "POST", path,
                None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        finally:
            connection.close()

    try:
        path = f"/api/chat/sessions/{session['session_id']}"
        status, accepted = request(path + "/turns", {
            "message": "Ask the same worker from the App",
            "client_turn_id": "d9bf2f43-4c6c-48d0-a8c1-baf000000001"})
        assert status == 202
        turn = finish(runtime, {"session_id": session["session_id"], "turn_id": accepted["turn_id"]})
        assert turn["status"] == "completed" and turn["origin"] == "web"
        session = store.load_session(session["session_id"])
        target = {key: original_route[key] for key in ("goal_id", "agent_id")}
        result = deliver(sender.root, runtime.registry_path, session=session, turn=turn,
                         request=target, source_store=store)
        route = _read_json(_root(sender.root) / "roundtrips" / (result["request_id"] + ".json"))
        assert route["source_id"].startswith("web:")
        acknowledge(sender.root, target["goal_id"], target["agent_id"], route["request_id"], "adopt", "Inspect this request")
        mirror = transport.root / "web-projections" / (
            identity_ref(session["steward_context"]["binding_id"], session["session_id"]) + ".json")

        def flush(message_id=None):
            transport._web_projection_scan_at = 0
            ids = [message_id] if message_id else [item["message_id"] for item in store.messages(session["session_id"])
                                                   if item["turn_id"] == turn["turn_id"]]
            _drain_app_projection(transport, lambda: mirror.exists() and all(
                _read_json(mirror)["deliveries"].get("web." + ref, {}).get("verified") for ref in ids))

        flush()
        yield sender, session, turn, route, provider, transport, mirror, flush, request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("long_result", [False, True])
def test_web_result_adopts_original_mirror_and_recovers_readback_without_send(web_return, monkeypatch, long_result):
    sender, session, turn, route, provider, transport, mirror, flush, request = web_return
    runtime, store = transport.core.controller, transport.core.controller.store
    capture = store.root.parent.parent / "requests.jsonl"
    starts = capture.read_bytes()
    frozen_turn = store._turn_path(session["session_id"], turn["turn_id"]).read_bytes()
    remainder = {"summary": "Validate installation", "resume_when": "The same worker has the original installation proof"}
    body = ("完整结果与独立证据。\n" * 1200 if long_result else "Concrete original result.") + "UNIQUE_RESULT_END"
    report(sender.root, route["goal_id"], route["agent_id"], route["request_id"], "conclusion", body,
           remaining_work=remainder)
    writes = list(provider.writes)
    drain(sender.root, runtime.registry_path, store, sender)
    assert provider.writes == writes
    assert reply_status(sender.root, route)[0]["error"] == "initial_delivery_receipt_unavailable"
    message = next(item for item in store.messages(session["session_id"])
                   if item.get("origin") == "manager_followup" and item["turn_id"] == turn["turn_id"])
    flush(message["message_id"])
    original_mirror = mirror.read_bytes()
    phase = _read_json(mirror)["deliveries"]["web." + message["message_id"]]
    writes = list(provider.writes)
    runner = transport.runner

    def unavailable(args, cwd=None, timeout=None):
        if "+messages-mget" in args and args[args.index("--message-ids") + 1].startswith("om_out_"):
            return {"returncode": 1, "stdout": ""}
        return runner(args, cwd, timeout)

    monkeypatch.setattr(transport, "runner", unavailable)
    clock = datetime.now(timezone.utc) + timedelta(minutes=10)
    drain(sender.root, runtime.registry_path, store, sender, now=clock)
    state = _read_json(_root(sender.root) / "replies" / route["request_id"] / "conclusion.delivery.json")
    assert state["status"] == "verification_required" and state["attempt"] == phase["attempt"]
    assert provider.writes == writes
    monkeypatch.setattr(transport, "runner", runner)
    recovered = ChatSessionStore(store.root.parent)
    restarted = LarkManagerReturnTransport(sender.server, sender.root)
    drain(sender.root, runtime.registry_path, recovered, restarted, now=clock + timedelta(minutes=10))
    drain(sender.root, runtime.registry_path, recovered, restarted, now=clock + timedelta(minutes=20))
    status = reply_status(sender.root, route)[0]
    assert status["status"] == "delivered"
    assert provider.writes == writes and mirror.read_bytes() == original_mirror
    assert capture.read_bytes() == starts
    assert store._turn_path(session["session_id"], turn["turn_id"]).read_bytes() == frozen_turn
    returned = [item for item in recovered.messages(session["session_id"])
                if item.get("origin") == "manager_followup" and item["turn_id"] == turn["turn_id"]]
    assert len(returned) == 1 and all(value in returned[0]["text"] for value in remainder.values())
    agenda = read_delegation_turn_context(sender.root, runtime.registry_path,
        route["goal_id"], route["agent_id"])["unfinished_delegations"]["items"]
    assert next(item for item in agenda if item["request_id"] == route["request_id"])["remaining_work"] == remainder
    code, snapshot = request(f"/api/chat/sessions/{session['session_id']}")
    assert code == 200
    actual = next(item for item in snapshot["messages"] if item["message_id"] == message["message_id"])
    assert actual["text"] == returned[0]["text"] and actual["return_delivery"]["status"] == "delivered"


@pytest.mark.parametrize("fault", ["unknown", "revoked", "revoked-after-read", "scope", "scope-after-read", "audience", "body", "receipt"])
def test_web_result_keeps_unknown_or_revoked_mirror_without_duplicate_delivery(web_return, monkeypatch, fault):
    sender, session, turn, route, provider, transport, mirror, flush, _ = web_return
    store, registry = transport.core.controller.store, transport.core.controller.registry_path
    report(sender.root, route["goal_id"], route["agent_id"], route["request_id"], "conclusion", "Frozen worker result")
    drain(sender.root, registry, store, sender)
    message = next(item for item in store.messages(session["session_id"])
                   if item.get("origin") == "manager_followup" and item["turn_id"] == turn["turn_id"])
    if fault == "unknown":
        provider.lose_next_reply = True
        transport._web_projection_scan_at = 0
        transport.reconcile()
        phase = _read_json(mirror)["deliveries"]["web." + message["message_id"]]
        assert phase["started"] and not phase.get("attempt")
    else:
        flush(message["message_id"])
        phase = _read_json(mirror)["deliveries"]["web." + message["message_id"]]

        def narrow_scope():
            value = transport.bindings.read()
            binding = next(item for item in value["bindings"] if item["binding_id"] == session["steward_context"]["binding_id"])
            binding.update(goal_scope="selected", goal_ids=[])
            _atomic_write_json(transport.bindings.path, value)

        if fault == "revoked":
            transport.bindings.disconnect(session["steward_context"]["binding_id"],
                expected_revision=transport.bindings.read()["revision"])
        elif fault == "scope":
            narrow_scope()
        elif fault in {"revoked-after-read", "scope-after-read"}:
            runner = transport.runner

            def revoked_after_read(args, cwd=None, timeout=None):
                result = runner(args, cwd, timeout)
                if "+messages-mget" in args and args[args.index("--message-ids") + 1] == phase["attempt"]["message_ref"]:
                    if fault == "scope-after-read":
                        narrow_scope()
                    else:
                        transport.bindings.disconnect(session["steward_context"]["binding_id"],
                            expected_revision=transport.bindings.read()["revision"])
                return result

            monkeypatch.setattr(transport, "runner", revoked_after_read)
        elif fault == "receipt":
            row = _read_json(mirror)
            row["deliveries"]["web." + message["message_id"]]["attempt"] = next(
                item["attempt"] for key, item in row["deliveries"].items()
                if key != "web." + message["message_id"] and item.get("verified"))
            _atomic_write_json(mirror, row)
        else:
            target = provider.messages[phase["attempt"]["message_ref"]]
            if fault == "audience":
                target["chat_id"] = "oc_another_audience"
            else:
                target["body"] = {"content": json.dumps({"zh_cn": {"title": "", "content": [[{"tag": "text", "text": "Different result"}]]}})}
    writes = list(provider.writes)
    for minutes in [10, 20]:
        transport.reconcile()
        drain(sender.root, registry, ChatSessionStore(store.root.parent),
              LarkManagerReturnTransport(sender.server, sender.root),
              now=datetime.now(timezone.utc) + timedelta(minutes=minutes))
    assert provider.writes == writes
    assert reply_status(sender.root, route)[0]["status"] != "delivered"
    assert len([item for item in store.messages(session["session_id"])
                if item.get("origin") == "manager_followup" and item["turn_id"] == turn["turn_id"]]) == 1


def test_web_result_identical_updates_keep_distinct_mirrors_and_final_scope(web_return):
    sender, session, turn, route, provider, transport, mirror, flush, _ = web_return
    runtime, store = transport.core.controller, transport.core.controller.store
    capture = store.root.parent.parent / "requests.jsonl"
    starts = capture.read_bytes()
    remainder = {"summary": "Installation remains", "resume_when": "Original installation evidence is available"}
    ids, attempts = [], []
    for index, update in enumerate([None, "same-observation", "qualified-final"]):
        body = "Repeated original observation" if index < 2 else "Original installation independently verified"
        report(sender.root, route["goal_id"], route["agent_id"], route["request_id"], "conclusion", body,
               remaining_work=remainder if index < 2 else None, update_id=update)
        clock = datetime.now(timezone.utc) + timedelta(minutes=10 * (index + 1))
        drain(sender.root, runtime.registry_path, store, sender, now=clock)
        returned = [item for item in store.messages(session["session_id"])
                    if item.get("origin") == "manager_followup" and item["turn_id"] == turn["turn_id"]]
        assert len(returned) == index + 1
        message = returned[-1]
        ids.append(message["message_id"])
        flush(message["message_id"])
        writes = list(provider.writes)
        drain(sender.root, runtime.registry_path, store, sender, now=clock + timedelta(minutes=5))
        assert all(item["status"] == "delivered" for item in reply_status(sender.root, route))
        assert provider.writes == writes
        attempts.append(_read_json(mirror)["deliveries"]["web." + message["message_id"]]["attempt"]["provider_receipt"])
    assert len(set(ids)) == len(set(attempts)) == 3
    assert len([body for _, body in provider.writes if "Repeated original observation" in body]) == 2
    agenda = read_delegation_turn_context(sender.root, runtime.registry_path,
        route["goal_id"], route["agent_id"])["unfinished_delegations"]["items"]
    assert not any(item["request_id"] == route["request_id"] for item in agenda)
    assert capture.read_bytes() == starts
