"""Worker returns use the original Core-bound private App, including recovery."""
import json
from types import SimpleNamespace

import pytest
from test_native_steward_private import steward, finish  # noqa: F401

from loopx.capabilities.manager_context import POLICY_SCHEMA, _root, _write, deliver
from loopx.chat_store import _atomic_write_json, _read_json
from loopx.extensions.lark.manager_returns import LarkManagerReturnTransport
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.manager_context import acknowledge
from loopx.capabilities.manager_context.roundtrip import drain, report, reply_status


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
            ref = "om_out_worker"
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
    from datetime import datetime, timedelta, timezone
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
