"""Verified native sources can reach the existing, separately granted inbox."""
import json

import pytest

from test_native_steward_private import steward, finish  # noqa: F401
from loopx.capabilities.manager_context import (
    POLICY_SCHEMA, _hash, _root, _write, authority, configure_delivery_target,
    configure_evidence_scope, deliver, pending,
)


def ingress_path(store, row):
    return _root(store.root.parent) / "ingress" / (
        _hash([row["session_id"], "external-" + row["request_ref"]]) + ".json")


@pytest.mark.parametrize("revoke_during_read", [False, True])
@pytest.mark.parametrize("revoke", ["binding", "provider", "scope"])
@pytest.mark.parametrize("scoped_reader", [False, True])
def test_request_inventory_keeps_native_source_scope_and_holds_revoked_binding(steward, monkeypatch, revoke_during_read, revoke, scoped_reader):  # noqa: F811
    from types import SimpleNamespace
    from urllib.parse import urlencode
    from loopx.presentation.request_control_api import RequestControlMixin, bound_steward_inventory_scope

    store, runtime, provider, transport, binding, capture, workspace = steward
    target = {"goal_id": "registered-work", "agent_id": "worker"}
    registry = json.loads(runtime.registry_path.read_text())
    registry["goals"] = [{"id": target["goal_id"], "repo": str(workspace),
        "coordination": {"registered_agents": [target["agent_id"]]}}]
    runtime.registry_path.write_text(json.dumps(registry))
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **_kwargs: None)
    transport.admit("steward-app", provider.event("steward-app", "inventory-source", "Inspect the existing artifact"))
    accepted = transport.core.pending()[0]
    session = store.load_session(accepted["session_id"])
    turn = store.load_turn(accepted["session_id"], accepted["turn_id"])
    configure_evidence_scope(runtime.coordination_runtime_root, runtime.registry_path, channel=session["channel_id"],
        goal_ids=[target["goal_id"]], execute=True)
    configure_delivery_target(runtime.coordination_runtime_root, runtime.registry_path, channel=session["channel_id"],
        **target, grant=True, execute=True)
    receipt = deliver(runtime.coordination_runtime_root, runtime.registry_path, session=session, turn=turn,
        request=target, source_store=store)
    calls = capture.read_bytes() if capture.exists() else b""

    class Reader(RequestControlMixin):
        server = SimpleNamespace(runtime_controller=runtime, chat_store=store, registry_path=runtime.registry_path,
            selected_goal_id=None)
        def _send_json(self, value, *, status=200):
            self.result = (status, value)
        def _send_error(self, message, **kwargs):
            self.result = (kwargs.get("status", 400), {"error": message})
        def read(self):
            self.path = "/api/chat/request-inventory?" + urlencode({"session_id": session["session_id"]})
            self._request_inventory_read()
            return self.result

    reader = Reader()
    if scoped_reader:
        reader.server.request_inventory_scope_resolver = bound_steward_inventory_scope(
            runtime, manager_scope_resolver=runtime.manager_scope_resolver)
    observations = []
    observe = transport.bindings._observe_owner
    def observe_owner(ref):
        observations.append(ref)
        return observe(ref)
    monkeypatch.setattr(transport.bindings, "_observe_owner", observe_owner)
    code, value = reader.read()
    assert code == 200 and value["pending_count"] == 1, value
    assert value["rows"][0]["request_id"] == receipt["request_id"]
    assert len(observations) == (2 if scoped_reader else 4)
    # A fresh source read cannot turn a revoked personal binding into local-owner
    # scope, nor use cached brief content after the authorization disappears.
    def withdraw_scope():
        if revoke == "binding":
            transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        elif revoke == "provider":
            provider.owner_ready = False
        else:
            runtime.manager_scope_resolver = lambda _: []
    if revoke_during_read:
        from loopx.control_plane.collaboration import conversation_inventory
        read_requests = conversation_inventory.read_conversation_requests
        def read_then_revoke(*args, **kwargs):
            result = read_requests(*args, **kwargs)
            withdraw_scope()
            return result
        monkeypatch.setattr(conversation_inventory, "read_conversation_requests", read_then_revoke)
    else:
        withdraw_scope()
    code, refused = reader.read()
    if revoke == "scope" and not revoke_during_read:
        assert code == 200 and refused["rows"] == [] and refused["pending_count"] == 0
    else:
        assert code == 400 and "rows" not in refused
    assert (capture.read_bytes() if capture.exists() else b"") == calls


@pytest.mark.parametrize("backend", ["file", "sqlite"])
def test_native_inventory_http_keeps_long_history_and_fresh_provider_checks(steward, monkeypatch, backend):  # noqa: F811
    import hashlib
    import http.client
    import threading
    from urllib.parse import urlencode
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.control_plane.collaboration.links import link
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.mutation_api import add_goal_todo
    from loopx.extensions.lark.conversation_identity import identity_ref
    from loopx.presentation.request_control_api import bound_steward_inventory_scope
    from loopx.chat_lark_api import manager_source_scope_resolver

    store, runtime, provider, transport, binding, capture, workspace = steward
    goal = "registered-work"
    state = workspace / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## User Todo\n\n## Agent Todo\n\n## Completed Work Archive\n")
    root = runtime.coordination_runtime_root
    registry = json.loads(runtime.registry_path.read_text())
    registry["common_runtime_root"] = str(root)
    registry["goals"] = [{"id": goal, "repo": str(workspace), "state_file": str(state),
        "coordination": {"registered_agents": ["worker", "reviewer"]}}]
    runtime.registry_path.write_text(json.dumps(registry))
    initialize_canonical_authority(root, goal, build_todo_runtime_shadow_projection(goal_id=goal, todos=[]),
        state_path=state, provider=backend)
    tasks = {}
    for agent in ("worker", "reviewer"):
        created = add_goal_todo(registry_path=runtime.registry_path, runtime_root_arg=str(root), goal_id=goal,
            role="agent", text=f"Inspect the retained artifact for {agent}", claimed_by=agent)
        assert created["ok"] is True
        tasks[agent] = created["todo_id"]
    for number in range(43):
        event = provider.event("steward-app", f"history-{number}",
            f"Record {number}: " + "保留来源身份、当前版本和核验结果。" * 100)
        transport.admit("steward-app", event)
        accepted = transport.core.read_request(identity_ref(binding["provider_ref"], event["message_id"]))
        finish(runtime, accepted)
        session = store.load_session(accepted["session_id"])
        turn = store.load_turn(accepted["session_id"], accepted["turn_id"])
        if number == 0:
            configure_evidence_scope(root, runtime.registry_path, channel=session["channel_id"], goal_ids=[goal], execute=True)
            for agent in ("worker", "reviewer"):
                configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"],
                    goal_id=goal, agent_id=agent, grant=True, execute=True)
        agent = "reviewer" if number % 2 else "worker"
        delivered = deliver(root, runtime.registry_path, session=session, turn=turn,
            request={"goal_id": goal, "agent_id": agent}, source_store=store)
        if number < 2:
            link(root, runtime.registry_path, goal, agent, delivered["request_id"], [tasks[agent]], [])

    sources = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in _root(root).rglob("*.json")}
    model_calls = capture.read_bytes() if capture.exists() else b""
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.chat_store = runtime.registry_path, store.root.parent, store
    server.runtime_root_override, server.runtime_controller = str(store.root.parent), runtime
    server.selected_goal_id, server.verbose = None, False
    runtime.manager_scope_resolver = manager_source_scope_resolver(server,
        registry_path=runtime.registry_path, executor_runtime_root=store.root.parent)
    server.request_inventory_scope_resolver = bound_steward_inventory_scope(
        runtime, manager_scope_resolver=runtime.manager_scope_resolver)
    observations = []
    observe = transport.bindings._observe_owner
    def observe_owner(ref):
        observations.append(ref)
        return observe(ref)
    monkeypatch.setattr(transport.bindings, "_observe_owner", observe_owner)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def read(**query):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        before = len(observations)
        connection.request("GET", "/api/chat/request-inventory?" + urlencode({"session_id": session["session_id"], **query}))
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body, len(observations) - before
    try:
        offset, ids, snapshot, linked = 0, [], None, []
        while offset is not None:
            code, page, checks = read(view="history", offset=offset)
            assert code == 200 and checks == 2 and page["matched"] == page["pending_count"] == 43
            assert page["coverage"] == {"scan_complete": True, "unreadable": 0, "diagnostics": []}
            assert all(len(row["source_question"]) > 1000 for row in page["rows"])
            snapshot = snapshot or page["snapshot_digest"]
            assert page["snapshot_digest"] == snapshot
            ids.extend(row["request_id"] for row in page["rows"])
            linked.extend(todo for row in page["rows"] for todo in row["linked_todos"])
            offset = page["next_offset"]
        assert len(ids) == len(set(ids)) == 43
        assert {todo["todo_id"] for todo in linked} == set(tasks.values())
        assert all(todo["status"] == "open" and "Inspect the retained artifact" in todo["title"] for todo in linked)
        assert {row["agent_id"] for row in page["rows"]} <= {"worker", "reviewer"}
        code, searched, checks = read(view="history", search=ids[-1])
        assert code == 200 and checks == 2 and searched["matched"] == 1
        provider.owner_ready = False
        code, refused, _ = read(view="history")
        assert code == 400 and "rows" not in refused and "保留来源身份" not in str(refused)
        provider.owner_ready = True
        assert read(view="history")[1]["matched"] == 43
        assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources} == sources
        assert (capture.read_bytes() if capture.exists() else b"") == model_calls
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_manager_source_scope_keeps_source_and_executor_routes_distinct(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from loopx import chat_lark_api
    from loopx.extensions.lark import manager_routing
    from loopx.chat_lark_api import manager_source_scope_resolver

    source, frozen_registry, executor = tmp_path / "source", tmp_path / "registry", tmp_path / "executor"
    server = SimpleNamespace(registry_path=source, runtime_root_override="current-source-override",
        runtime_controller=SimpleNamespace(coordination_runtime_root=tmp_path / "coordination"))
    observed = []
    def snapshot(**kwargs):
        observed.append(kwargs)
        return {"source": "current"}
    def authorize(value, session, **kwargs):
        assert value == {"source": "current"} and session == {"channel_id": "legacy-manager"}
        observed.append(kwargs)
        return ["permitted-goal"]
    monkeypatch.setattr(chat_lark_api, "build_lark_goal_topic_runtime_snapshot", snapshot)
    monkeypatch.setattr(manager_routing, "authorized_manager_goal_ids", authorize)
    read = manager_source_scope_resolver(server, registry_path=frozen_registry, executor_runtime_root=executor)
    assert read({"channel_id": "legacy-manager"}) == ["permitted-goal"]
    assert observed == [{"registry_path": source, "runtime_root_override": "current-source-override"}, {
        "runtime_root": tmp_path / "coordination", "registry_path": frozen_registry, "executor_runtime_root": executor}]


@pytest.mark.parametrize("separate_coordination_root", [False, True])
def test_native_ingress_uses_coordination_owner_with_legacy_fallback(steward, monkeypatch, tmp_path, separate_coordination_root):  # noqa: F811
    store, runtime, provider, transport, binding, _, workspace = steward
    root = tmp_path / "coordination" if separate_coordination_root else store.root.parent
    if separate_coordination_root:
        runtime.coordination_runtime_root = root
    target = {"goal_id": "registered-work", "agent_id": "worker"}
    registry = json.loads(runtime.registry_path.read_text())
    registry["runtime_root"] = str(root)
    registry["goals"] = [{"id": target["goal_id"], "repo": str(workspace),
                          "coordination": {"registered_agents": [target["agent_id"]]}}]
    runtime.registry_path.write_text(json.dumps(registry))
    # Observe provenance at the actual enqueue boundary, before model execution.
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **kwargs: None)
    enqueue = runtime.enqueue_turn
    saved = []

    def observe(**kwargs):
        path = _root(root) / "ingress" / (_hash([kwargs["session_id"], kwargs["client_turn_id"]]) + ".json")
        saved.append(path.read_bytes())
        return enqueue(**kwargs)

    monkeypatch.setattr(runtime, "enqueue_turn", observe)
    event = provider.event("steward-app", "canonical", "Original request for the registered worker")
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    session = store.load_session(row["session_id"])
    turn = store.load_turn(row["session_id"], row["turn_id"])
    assert turn["status"] == "queued" and len(saved) == 1
    if separate_coordination_root:
        assert not ingress_path(store, row).exists()
    policy_path = _root(root) / "policy.json"
    _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {session["channel_id"]: {
        "sender_ids": [binding["operator_ref"]], "local_delivery_scope": "selected", "targets": [],
    }}})
    assert authority(root, runtime.registry_path, session, turn)["targets"] == []
    configure_evidence_scope(root, runtime.registry_path, channel=session["channel_id"],
                             goal_ids=[target["goal_id"]], execute=True)
    configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"],
                              **target, grant=True, execute=True)
    receipt = deliver(root, runtime.registry_path, session=session, turn=turn, request=target)
    assert pending(root, **target)["items"][0]["message"] == event["content"]
    assert deliver(root, runtime.registry_path, session=session, turn=turn,
                   request=target) == {**receipt, "replayed": True}
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    assert len(saved) == 1 and len(store.list_sessions()) == 1
    # A sibling App's project source cannot acquire manager provenance or policy.
    monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
    transport.admit("notes-app", provider.event("notes-app", "other", "Other audience"))
    notes = next(item for item in transport.core.pending() if item["message"] == "Other audience")
    assert not authority(root, runtime.registry_path, store.load_session(notes["session_id"]),
                         store.load_turn(notes["session_id"], notes["turn_id"]))["targets"]
    configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"],
                              **target, grant=False, execute=True)
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, runtime.registry_path, session=session, turn=turn, request=target)


def test_verified_ingress_precedes_model_launch_and_survives_admission_replay(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, binding, _, _ = steward
    enqueue = runtime.enqueue_turn
    observations = []

    def interrupted(**kwargs):
        row = transport.core.pending()[0]
        saved = json.loads(ingress_path(store, row).read_text())
        assert saved["client_turn_id"] == kwargs["client_turn_id"]
        assert saved["sender_id"] == binding["operator_ref"]
        assert saved["source_id"] == row["request_ref"]
        assert saved["message_digest"] == _hash(kwargs["message"])
        observations.append(saved)
        enqueue(**kwargs)
        raise OSError("owner interrupted after canonical admission")

    event = provider.event("steward-app", "first", "Original owner context")
    monkeypatch.setattr(runtime, "enqueue_turn", interrupted)
    with pytest.raises(OSError):
        transport.admit("steward-app", event)
    prepared = transport.core.pending()[0]
    before = ingress_path(store, prepared).read_bytes()
    turn = store.turn_for_client(prepared["session_id"], "external-" + prepared["request_ref"])
    assert finish(runtime, {**prepared, "turn_id": turn["turn_id"]})["status"] == "completed"
    monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    accepted = transport.core.pending()[0]
    assert accepted["session_id"] == prepared["session_id"] and accepted["turn_id"] == turn["turn_id"]
    assert ingress_path(store, accepted).read_bytes() == before and len(observations) == 1
    assert len(store.list_sessions()) == 1
    # A regular project source cannot create manager provenance or a Goal.
    transport.admit("notes-app", provider.event("notes-app", "notes", "Private project context"))
    notes = next(row for row in transport.core.pending() if row["message"] == "Private project context")
    assert finish(runtime, notes)["status"] == "completed"
    assert not ingress_path(store, notes).exists()


def test_native_steward_uses_separate_sender_target_grants_and_exact_return_route(steward):  # noqa: F811
    store, runtime, provider, transport, binding, _, workspace = steward
    target = {"goal_id": "fresh-commission", "agent_id": "operations"}
    registry = json.loads(runtime.registry_path.read_text())
    registry["goals"] = [{"id": target["goal_id"], "repo": str(workspace),
                          "coordination": {"registered_agents": [target["agent_id"]]}}]
    runtime.registry_path.write_text(json.dumps(registry))
    registry_before = runtime.registry_path.read_bytes()
    event = provider.event("steward-app", "source", "Read this source; return private findings, never publish.")
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    assert finish(runtime, row)["status"] == "completed"
    session = store.load_session(row["session_id"])
    turn = store.load_turn(row["session_id"], row["turn_id"])
    root = store.root.parent
    # The verified owner's default covers registered recipients; an explicit
    # selected source still needs a separate delivery grant below.
    assert authority(root, runtime.registry_path, session, turn)["targets"] == [target]
    policy = {"schema_version": POLICY_SCHEMA, "sources": {session["channel_id"]: {
        "sender_ids": [binding["operator_ref"]], "local_delivery_scope": "selected", "targets": [],
    }}}
    policy_path = _root(root) / "policy.json"
    _write(policy_path, policy)
    # Reading a portfolio does not confer delivery authority.
    configure_evidence_scope(root, runtime.registry_path, channel=session["channel_id"],
                             goal_ids=[target["goal_id"]], execute=True)
    assert authority(root, runtime.registry_path, session, turn)["targets"] == []
    preview = configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"], **target, grant=True)
    assert preview["would_change"] and not preview["executed"]
    assert authority(root, runtime.registry_path, session, turn)["targets"] == []
    applied = configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"], **target, grant=True, execute=True)
    assert applied["readback_verified"]
    assert authority(root, runtime.registry_path, session, turn)["targets"] == [target]
    assert not authority(root, runtime.registry_path, session, {**turn, "message": "forged"})["targets"]
    assert not authority(root, runtime.registry_path, session, {**turn, "origin": "web"})["targets"]
    receipt = deliver(root, runtime.registry_path, session=session, turn=turn, request=target)
    assert not receipt["todo_created"] and not receipt["execution_interrupted"]
    assert deliver(root, runtime.registry_path, session=session, turn=turn, request=target) == {**receipt, "replayed": True}
    assert pending(root, **target)["items"][0]["message"] == event["content"]
    assert runtime.registry_path.read_bytes() == registry_before
    assert policy_path.stat().st_mode & 0o777 == 0o600
    # The independently verified second App cannot use this channel's grant.
    transport.admit("notes-app", provider.event("notes-app", "notes", "Other private audience"))
    notes = next(row for row in transport.core.pending() if row["message"] == "Other private audience")
    assert finish(runtime, notes)["status"] == "completed"
    assert not authority(root, runtime.registry_path, store.load_session(notes["session_id"]),
                         store.load_turn(notes["session_id"], notes["turn_id"]))["targets"]
    policy["sources"][session["channel_id"]]["targets"] = [target]
    policy["sources"][session["channel_id"]]["sender_ids"] = ["another-App-owner"]
    _write(policy_path, policy)
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, runtime.registry_path, session=session, turn=turn, request=target)


@pytest.mark.parametrize("channel", ["manager.external.native", "manager.external.native." + "a" * 24,
                                    "manager.external.native." + "a" * 24 + ".*",
                                    "manager.external." + "a" * 23])
def test_configuration_rejects_partial_or_wildcard_audiences(tmp_path, channel):
    with pytest.raises(ValueError, match="exact external"):
        configure_evidence_scope(tmp_path, tmp_path / "missing.json", channel=channel, goal_ids=[])
    with pytest.raises(ValueError, match="exact external"):
        configure_delivery_target(tmp_path, tmp_path / "missing.json", channel=channel,
                                  goal_id="fresh-commission", agent_id="operations", grant=True)


def test_inbox_bound_does_not_truncate_or_reject_ordinary_native_chat(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, _, _, _ = steward
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **kwargs: None)
    message = "x" * 32001
    assert transport.admit("steward-app", provider.event("steward-app", "large", message))["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    turn = store.load_turn(row["session_id"], row["turn_id"])
    assert turn["message"] == message and turn["status"] == "queued"
    assert not ingress_path(store, row).exists()
    assert not authority(store.root.parent, runtime.registry_path, store.load_session(row["session_id"]), turn)["targets"]
