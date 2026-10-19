"""Native steward journeys exercise the same Core, action store and provider.

Synthetic transport/model fixtures never certify phone or real-model acceptance.
"""
import json
import time
from pathlib import Path
import runpy

import pytest

from test_lark_private_conversations import connect
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService, ProtectedActionGate
from loopx.chat_manager_context import collect_manager_turn_context, manager_turn_context
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.extensions.lark.conversation_identity import identity_ref


@pytest.fixture
def steward(tmp_path):
    workspace = tmp_path / "fresh-workspace"
    workspace.mkdir()
    contexts = ChatProjectContexts([workspace])
    store = ChatSessionStore(tmp_path / "runtime")
    capture, fake = tmp_path / "requests.jsonl", tmp_path / "codex"
    source = runpy.run_path(str(Path(__file__).parents[1] / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
    source = source.replace('    method = request.get("method")',
        f'    with open({str(capture)!r}, "a") as output:\n        output.write(json.dumps(request) + "\\n")\n'
        '    method = request.get("method")')
    fake.write_text(source)
    fake.chmod(0o700)
    runtime = ChatRuntimeController(store=store, codex_bin=str(fake), project_contexts=contexts,
                                    registry_path=tmp_path / "registry.json")
    runtime.registry_path.write_text(json.dumps({"schema_version": "0.1", "runtime_root": str(store.root.parent), "goals": []}))
    (workspace / ".git").mkdir()
    source = fake.read_text().replace('active_turn = None', 'active_turn = None\nnative_goal = None', 1)
    begin = source.index('    elif method in {"thread/goal/set", "thread/goal/get"}:')
    end = source.index('    elif method == "turn/start":', begin)
    source = source[:begin] + source[end:]
    source = source.replace('    elif method == "turn/start":', '''    elif method == "thread/goal/get":
        result = {"goal": native_goal}
    elif method == "thread/goal/set":
        native_goal = {**(native_goal or {}), "threadId": "durable-thread", "tokensUsed": 120, "timeUsedSeconds": 1, **request["params"]}
        print(json.dumps({"id": request_id, "result": {"goal": native_goal}}), flush=True)
        if native_goal["status"] == "active":
            turn = "native-first"
            active_turn = turn
            turn_status[turn] = "inProgress"
            print(json.dumps({"method": "turn/started", "params": {"threadId": "durable-thread", "turn": {"id": turn}}}), flush=True)
            if "wait for interrupt" in str(native_goal.get("objective")):
                continue
            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": turn, "delta": "Verified synthetic result: the workspace has one README and no inherited portfolio."}}), flush=True)
            native_goal["status"] = "complete"
            turn_status[turn] = "completed"
            print(json.dumps({"method": "turn/completed", "params": {"threadId": "durable-thread", "turn": {"id": turn, "status": "completed"}}}), flush=True)
        continue
    elif method == "turn/start":''')
    fake.write_text(source)
    store, runtime, provider, transport = connect((store, runtime, contexts, None, capture, fake, workspace))
    ref = contexts.available()[0]["project_ref"]
    binding = transport.bindings.configure(transport_ref="steward-app", project_ref=ref,
                                          executor_endpoint_id="codex", context_kind="steward")
    runtime.manager_scope_resolver = transport.bindings.steward_scope
    transport.core.actions = ChatActionService(store=ChatActionStore(store.root / "actions"),
        registry_path=runtime.registry_path, chat_store=store, runtime_controller=runtime, workspace_roots=[workspace])
    yield store, runtime, provider, transport, binding, capture, workspace
    runtime.close()


def finish(runtime, row):
    return runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)


@pytest.mark.parametrize("case", ["enabled", "disabled", "missing", "authority-unavailable"])
def test_recovery_discovery_never_observes_provider_but_admission_still_does(steward, monkeypatch, case):
    store, runtime, provider, transport, binding, capture, _ = steward
    fake = Path(runtime.codex_bin)
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "contextWindowExceeded"')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    event = provider.event("steward-app", "discovery-only", "typed terminal failure")
    transport.admit("steward-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    row = transport.core.read_request(ref)
    assert finish(runtime, row)["status"] == "failed"
    store.update_session(row["session_id"], manager_context_version=20)
    assert transport.reconcile() == 1
    if case == "disabled":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif case == "missing":
        current = transport.bindings.read()
        current["bindings"] = []
        transport.bindings.path.write_text(json.dumps(current))
    before_calls, before_requests, before_writes = len(provider.calls), capture.read_bytes(), list(provider.writes)
    with monkeypatch.context() as discovery:
        discovery.setattr(transport.bindings, "observe", lambda *_a, **_kw: pytest.fail("provider IO in discovery"))
        assert transport.core.context_recovery_candidate(ref) is (case not in {"disabled", "missing"})
    assert len(provider.calls) == before_calls  # Includes no hidden auth/status probe.
    if case == "authority-unavailable":
        provider.owner_ready = False
        # A local hint is not permission: the normal admission owner must
        # freshly reject unavailable authority before native observation/work.
        with pytest.raises(ValueError):
            transport.core.recover_failed_request(ref, source_observation=lambda _selected: pytest.fail("source IO after rejection"))
        assert not transport.core.read_request(ref).get("context_recovery")
    assert transport.core.read_request(ref)["turn_id"] == row["turn_id"]
    assert capture.read_bytes() == before_requests and provider.writes == before_writes


@pytest.mark.parametrize("fault", [None, "revoked", "source-changed", "native-effect", "unknown-native",
    "reservation-crash", "post-enqueue-crash", "transport-sync-crash", "existing-return",
    "foreign-native-thread", "wrong-native-turn", "policy-native-proof", "steered", "native-goal", "current-context"])
def test_existing_scanner_automatically_continues_legacy_failure_and_returns_original(steward, monkeypatch, fault):
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx.chat_manager import MANAGER_CONTEXT_VERSION

    store, runtime, provider, transport, binding, capture, _ = steward
    fake = Path(runtime.codex_bin)
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "contextWindowExceeded"')
    # The demonstrated failure precedes any model output or tool. The native
    # protocol inventory below is independently covered in test_chat_agent.
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    event = provider.event("steward-app", "legacy-overfull", "typed terminal failure")
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    row = next(r for r in transport.core.pending() if r.get("request_ref") == identity_ref(binding["provider_ref"], event["message_id"]))
    failed = finish(runtime, row)
    assert failed["status"] == "failed" and failed["delta_count"] == 0
    assert failed["error_code"] == "context_window_exceeded"
    failed_path = store._turn_path(row["session_id"], row["turn_id"])
    failed_bytes = failed_path.read_bytes()
    store.update_session(row["session_id"], manager_context_version=20)
    assert transport.reconcile() == 1  # the error notice, never business completion
    path = transport.root / (row["request_ref"] + ".json")
    prior = json.loads(path.read_text())
    assert prior["status"] == "delivered"
    failure_notice = dict(prior["deliveries"]["terminal"])
    runtime.adapters.pop(row["session_id"]).close_session()
    fake.write_text(source.replace('"durable-thread"', '"recovered-thread"')
        .replace('if "typed terminal failure" in json.dumps(request.get("params") or {}):', 'if False:'))
    proof = {"thread_id": "durable-thread", "turn_id": failed["upstream_turn_id"],
        "only_original_user_item": True, "codex_error_info": "contextWindowExceeded"}
    if fault == "foreign-native-thread":
        proof["thread_id"] = "foreign-thread"
    elif fault == "wrong-native-turn":
        proof["turn_id"] = "foreign-turn"
    elif fault == "policy-native-proof":
        proof["codex_error_info"] = "cyberPolicy"
    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(
        lambda **_: None if fault in {"native-effect", "unknown-native"} else proof), raising=False)
    if fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault == "source-changed":
        provider.messages[event["message_id"]]["content"] = "Changed original requirement"
    elif fault == "existing-return":
        provider.messages[event["message_id"]]["thread_replies"] = [{"message_id": "om_existing",
            "sender": {"id": provider.profile_apps["steward-app"], "sender_type": "app"},
            "content": "Prior verified completion; preserve it instead of repeating effects."}]
    elif fault == "steered":
        store.append_event(row["session_id"], row["turn_id"], kind="turn.steered", payload={"client_ingress_id": "correction"})
    elif fault == "native-goal":
        from loopx.chat_codex_goal import UPSTREAM_MODE
        store.update_session(row["session_id"], upstream_mode=UPSTREAM_MODE)
    elif fault == "current-context":
        store.update_session(row["session_id"], manager_context_version=MANAGER_CONTEXT_VERSION)
    if fault in {"reservation-crash", "post-enqueue-crash"}:
        enqueue = runtime.enqueue_turn
        def crash(**kwargs):
            if fault == "post-enqueue-crash":
                enqueue(**kwargs)
            raise OSError("simulated admission crash")
        monkeypatch.setattr(runtime, "enqueue_turn", crash)
        with pytest.raises(OSError, match="admission"):
            transport.reconcile()
        monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
    elif fault == "transport-sync-crash":
        recover = transport.core.recover_failed_request
        def crash_sync(*args, **kwargs):
            recover(*args, **kwargs)
            raise OSError("simulated transport sync crash")
        monkeypatch.setattr(transport.core, "recover_failed_request", crash_sync)
        with pytest.raises(OSError, match="transport sync"):
            transport.reconcile()
    # A fresh instance models restart: no manual enqueue, new event or source
    # mutation is supplied to make the old delivered record visible again.
    from loopx.extensions.lark.private_conversations import LarkPrivateConversations
    restored = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
        runner=provider, cli_bin="lark-cli")
    held = {"revoked", "source-changed", "native-effect", "unknown-native", "foreign-native-thread",
        "wrong-native-turn", "policy-native-proof", "steered", "native-goal", "current-context"}
    if fault in held:
        restored.reconcile()
    else:
        import threading
        from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

        service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=store.root.parent,
            runtime_controller=runtime, private_conversations=restored)
        worker = threading.Thread(target=service._reconcile_private)
        worker.start()
        try:
            deadline = time.monotonic() + 12
            while not json.loads(path.read_text()).get("deliveries", {}).get(
                    f"terminal.recovery.{MANAGER_CONTEXT_VERSION}", {}).get("verified"):
                assert time.monotonic() < deadline
                time.sleep(.01)
        finally:
            service._closed.set()
            service._delivery_wakeup.set()
            worker.join(5)
        assert not worker.is_alive() and not service._workers  # no new event consumer
    current = restored.core.read_request(row["request_ref"])
    if fault in held:
        assert current["turn_id"] == row["turn_id"] and not current.get("context_recovery")
        assert json.loads(path.read_text()) == prior
        assert failed_path.read_bytes() == failed_bytes
        return
    assert current["turn_id"] != row["turn_id"] and current["session_id"] == row["session_id"]
    assert finish(runtime, current)["status"] == "completed"
    assert restored.reconcile_request(path) == 0  # scanner already delivered it
    delivered = json.loads(path.read_text())
    assert delivered["status"] == "delivered" and delivered["deliveries"]["terminal"] == failure_notice
    assert delivered["deliveries"][f"terminal.recovery.{MANAGER_CONTEXT_VERSION}"]["verified"]
    assert restored.core.read_request(row["request_ref"])["delivery_verified"]
    assert failed_path.read_bytes() == failed_bytes and len(store.list_sessions()) == 1
    before_writes = list(provider.writes)
    restored.reconcile_request(path)
    restored.reconcile()
    assert provider.writes == before_writes
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(r["method"] == "turn/start" for r in requests) == 2
    assert any(r["method"] == "thread/inject_items" for r in requests)
    assert "request_recovery" in capture.read_text() and "legacy-overfull" in capture.read_text()
    if fault == "existing-return":
        assert "Prior verified completion" in capture.read_text()


@pytest.mark.parametrize("fault", [None, "native-effect", "policy", "failure-again"])
def test_current_context_host_failure_gets_one_proven_continuation(steward, monkeypatch, fault):
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx.chat_manager import MANAGER_CONTEXT_VERSION

    store, runtime, provider, transport, binding, capture, _ = steward
    fake = Path(runtime.codex_bin)
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "other"')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    event = provider.event("steward-app", "host-startup", "typed terminal failure")
    transport.admit("steward-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    row = transport.core.read_request(ref)
    failed = finish(runtime, row)
    assert failed["status"] == "failed" and failed["error_code"] == "host_gate" and failed["delta_count"] == 0
    assert store.load_session(row["session_id"])["manager_context_version"] == MANAGER_CONTEXT_VERSION
    failed_path = store._turn_path(row["session_id"], row["turn_id"])
    failed_bytes = failed_path.read_bytes()
    transport.reconcile()
    path = transport.root / (ref + ".json")
    notice = json.loads(path.read_text())["deliveries"]["terminal"]
    runtime.adapters.pop(row["session_id"]).close_session()
    if fault != "failure-again":
        fake.write_text(source.replace('if "typed terminal failure" in json.dumps(request.get("params") or {}):', 'if False:'))
    proof = {"thread_id": "durable-thread", "turn_id": failed["upstream_turn_id"],
        "only_original_user_item": fault != "native-effect", "codex_error_info": "cyberPolicy" if fault == "policy" else "other"}
    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(lambda **_: proof))
    transport.reconcile()
    current = transport.core.read_request(ref)
    if fault in {"native-effect", "policy"}:
        assert current["turn_id"] == row["turn_id"] and not current.get("context_recovery")
        expected_starts = 1
    else:
        assert current["turn_id"] != row["turn_id"] and current["session_id"] == row["session_id"]
        assert finish(runtime, current)["status"] == ("failed" if fault == "failure-again" else "completed")
        transport.reconcile()
        assert json.loads(path.read_text())["deliveries"][f"terminal.recovery.{MANAGER_CONTEXT_VERSION}"]["verified"]
        expected_starts = 2
    # A persistent provider failure is surfaced once, never an infinite replay.
    transport.reconcile()
    transport.reconcile()
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(r["method"] == "turn/start" for r in requests) == expected_starts
    assert not any(r["method"] == "thread/inject_items" for r in requests)
    assert failed_path.read_bytes() == failed_bytes
    assert json.loads(path.read_text())["deliveries"]["terminal"] == notice
    assert len(store.list_sessions()) == 1


def test_personal_steward_uses_registered_portfolio_and_configured_inbox_root(steward, tmp_path):
    from loopx.capabilities.manager_context.discovery import agent_page
    from loopx.capabilities.manager_context import configure_delivery_scope
    from loopx.capabilities.native_chat.project_context import coordination_runtime_root
    store, runtime, _, transport, binding, _, workspace = steward
    scope = {"source_ref": "a" * 24, "sender_ref": binding["operator_ref"], "private_human_message": True}
    saved = transport.bindings.resolve(binding_id=binding["binding_id"], **scope)["context"]
    canonical = tmp_path / "canonical-runtime"
    registry = {"common_runtime_root": str(canonical), "goals": [
        {"id": "maintenance", "coordination": {"registered_agents": ["reviewer", "builder"]}},
        {"id": "notes", "coordination": {"registered_agents": ["curator"]}},
    ]}
    runtime.registry_path.write_text(json.dumps(registry))
    assert coordination_runtime_root(runtime.registry_path, store.root.parent) == canonical
    fresh = transport.bindings.session_context(saved)
    assert fresh["context"]["goal_ids"] == ["maintenance", "notes"]
    assert fresh["context"]["source_ref"] == saved["source_ref"]
    assert agent_page(runtime.registry_path, goal_ids=fresh["context"]["goal_ids"], limit=100)["matched"] == 3
    preview = configure_delivery_scope(canonical, channel=fresh["channel_id"],
        local_delivery_scope="all_registered", sender_id=binding["operator_ref"])
    assert preview["would_change"] and not (canonical / ".local/manager-context/policy.json").exists()
    applied = configure_delivery_scope(canonical, channel=fresh["channel_id"],
        local_delivery_scope="all_registered", sender_id=binding["operator_ref"], execute=True)
    assert applied["readback_verified"]
    assert not configure_delivery_scope(canonical, channel=fresh["channel_id"],
        local_delivery_scope="all_registered", execute=True)["would_change"]
    legacy = dict(binding)
    legacy.pop("goal_scope")
    transport.bindings.path.write_text(json.dumps({"schema_version": "loopx_chat_conversation_bindings_v0", "revision": 20, "bindings": [legacy]}))
    assert transport.bindings.session_context(saved)["context"]["goal_ids"] == []
    retained = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", context_kind="steward")
    assert "goal_scope" not in retained
    upgraded = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", context_kind="steward", goal_scope="all_registered")
    assert upgraded["binding_id"] == binding["binding_id"]
    assert transport.bindings.session_context(saved)["context"]["goal_ids"] == ["maintenance", "notes"]
    assert not (workspace / "ACTIVE_GOAL_STATE.md").exists()


def test_legacy_delivery_only_receipt_does_not_block_steward_conversation(steward):
    store, runtime, provider, transport, _, capture, _ = steward
    orphan = transport.core.root / ("f" * 24 + ".json")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text(json.dumps({"delivery_verified": True}))
    before = orphan.read_bytes()
    event = provider.event("steward-app", "after_legacy_receipt", "Check the authorized portfolio")
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    row = next(r for r in transport.core.pending() if r.get("request_ref") == identity_ref(
        transport.bindings.read()["bindings"][1]["provider_ref"], event["message_id"]))
    assert finish(runtime, row)["status"] == "completed"
    session = store.load_session(row["session_id"])
    assert transport.core.commission_evidence(session) == []
    selection = provider.event("notes-app", "select_after_legacy_receipt", "/project")
    assert transport.admit("notes-app", selection)["status"] == "command_recorded"
    assert orphan.read_bytes() == before
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(p["method"] == "turn/start" for p in packets) == 1


def test_verified_empty_steward_is_not_missing_authorization_and_does_not_inherit(steward):
    store, runtime, provider, transport, binding, _, _ = steward
    transport.admit("steward-app", provider.event("steward-app", "empty", "What new work do you manage?"))
    row = transport.core.pending()[0]
    assert finish(runtime, row)["status"] == "completed"
    session = store.load_session(row["session_id"])
    context = collect_manager_turn_context(runtime.registry_path, session, store.root.parent, runtime.manager_scope_resolver)
    assert context["goals"] == [] and context["bound_steward"] == {"authorized": True, "goal_count": 0, "empty": True}
    assert context["coverage"]["discovered"] == 0 and "external_authorization_unavailable" not in context["warnings"]
    assert "empty_inventory" in context["warnings"]
    assert json.loads(runtime.registry_path.read_text())["goals"] == []
    missing = manager_turn_context(runtime.registry_path, {"goal_id": "loopx-manager", "channel_id": "manager.external.unbound"}, store.root.parent, authorized_goal_ids=[])
    assert missing["warnings"] == ["external_authorization_unavailable"]
    transport.admit("notes-app", provider.event("notes-app", "notes", "ordinary notes conversation"))
    notes = next(r for r in transport.core.pending() if r["message"] == "ordinary notes conversation")
    assert finish(runtime, notes)["status"] == "completed"
    assert store.load_session(notes["session_id"])["goal_id"] is None
    assert notes["session_id"] != row["session_id"]
    assert "ordinary notes conversation" not in str(store.messages(row["session_id"]))
    transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    assert collect_manager_turn_context(runtime.registry_path, session, store.root.parent, runtime.manager_scope_resolver)["warnings"] == ["external_authorization_unavailable"]
    assert transport.admit("steward-app", provider.event("steward-app", "revoked", "more"))["status"] == "audience_rejected"


def test_steward_reads_every_registration_across_pages_and_future_goals(steward):
    from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME
    store, runtime, _, transport, binding, _, _ = steward
    source = {"source_ref": "a" * 24, "sender_ref": binding["operator_ref"], "private_human_message": True}
    saved = transport.bindings.resolve(binding_id=binding["binding_id"], **source)["context"]
    goals = [{"id": f"work-{i:03d}", "coordination": {"registered_agents": ["builder", "reviewer"]}}
             for i in range(150)]
    goals[-1]["activation_state"] = "stopped"
    runtime.registry_path.write_text(json.dumps({"goals": goals}))
    inspection = ManagerInspection(context={"scope": "external_authorized", "goals": []},
        registry_path=runtime.registry_path, runtime_root=getattr(runtime, "coordination_runtime_root", store.root.parent),
        owner_scope=False, scope_valid=lambda: True, record=lambda result: None,
        channel_id=transport.bindings.session_context(saved)["channel_id"],
        discovery_scope=lambda: transport.bindings.session_context(saved)["context"]["goal_ids"])

    def read_all(include_stopped=False):
        rows, offset = [], 0
        while True:
            page = inspection.read(TOOL_NAME, {"view": "agents", "limit": 12, "offset": offset,
                                    "include_stopped": include_stopped})
            assert page["ok"] and not page["unknown"]
            rows.extend(page["rows"])
            if page["next_offset"] is None:
                return {(row["goal_id"], row["agent_id"]) for row in rows}
            offset = page["next_offset"]

    expected = {(goal["id"], agent) for goal in goals for agent in ["builder", "reviewer"]}
    assert read_all(True) == expected
    assert read_all() == {(goal, agent) for goal, agent in expected if goal != "work-149"}
    goals.append({"id": "future-work", "coordination": {"registered_agents": ["new-worker"]}})
    runtime.registry_path.write_text(json.dumps({"goals": goals}))
    assert read_all(True) == expected | {("future-work", "new-worker")}
    assert transport.bindings.read()["bindings"][1]["goal_ids"] == []
    assert store.list_sessions() == []
    with pytest.raises(ValueError, match="audience"):
        transport.bindings.resolve(binding_id=binding["binding_id"], **{**source, "sender_ref": "b" * 24})


def test_steward_admission_configures_delivery_once_and_explicit_upgrade_preserves_blocks(steward):
    from loopx.capabilities.manager_context import configure_delivery_scope
    from loopx.control_plane.collaboration.inbox import _root
    store, runtime, provider, transport, binding, _, _ = steward
    transport.admit("steward-app", provider.event("steward-app", "scope-before", "/status"))
    policy_path = _root(runtime.coordination_runtime_root) / "policy.json"
    policy = json.loads(policy_path.read_text())
    channel = next(iter(policy["sources"]))
    assert policy["sources"][channel]["local_delivery_scope"] == "all_registered"
    policy["sources"][channel]["blocked_targets"] = [{"goal_id": "maintenance", "agent_id": "reviewer"}]
    policy_path.write_text(json.dumps(policy))
    configure_delivery_scope(runtime.coordination_runtime_root, channel=channel,
        local_delivery_scope="selected", execute=True)
    transport.admit("steward-app", provider.event("steward-app", "scope-repeat", "/status"))
    assert json.loads(policy_path.read_text())["sources"][channel]["local_delivery_scope"] == "selected"
    transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", context_kind="steward", goal_scope="all_registered")
    upgraded = json.loads(policy_path.read_text())["sources"][channel]
    assert upgraded["local_delivery_scope"] == "all_registered"
    assert upgraded["blocked_targets"] == [{"goal_id": "maintenance", "agent_id": "reviewer"}]
    transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
    assert json.loads(policy_path.read_text())["sources"][channel]["local_delivery_scope"] == "selected"
    assert json.loads(policy_path.read_text())["sources"][channel]["blocked_targets"] == upgraded["blocked_targets"]
    with pytest.raises(ValueError, match="scope"):
        configure_delivery_scope(runtime.coordination_runtime_root, channel=channel,
            local_delivery_scope="invalid", execute=True)
    before = transport.bindings.path.read_bytes()
    policy_path.write_text("{}")
    with pytest.raises(ValueError, match="invalid manager policy"):
        transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="codex", context_kind="steward", goal_scope="all_registered")
    assert transport.bindings.path.read_bytes() == before


@pytest.mark.parametrize("failure_at", ["first_policy", "second_policy", "binding"])
def test_scope_upgrade_io_failure_keeps_old_binding_and_explicit_retry_converges(steward, monkeypatch, failure_at):
    from copy import deepcopy
    from loopx.capabilities import manager_context
    from loopx.capabilities.native_chat import conversation_bindings
    store, runtime, provider, transport, binding, _, _ = steward
    transport.admit("steward-app", provider.event("steward-app", "before-upgrade", "/status"))
    transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
    policy_path = manager_context._root(runtime.coordination_runtime_root) / "policy.json"
    policy = json.loads(policy_path.read_text())
    channel = next(iter(policy["sources"]))
    second = transport.bindings.resolve(binding_id=binding["binding_id"], source_ref="b" * 24,
        sender_ref=binding["operator_ref"], private_human_message=True)["channel_id"]
    source = policy["sources"][channel]
    source["blocked_targets"] = [{"goal_id": "maintenance", "agent_id": "reviewer"}]
    policy["sources"][second] = deepcopy(source)
    policy_path.write_text(json.dumps(policy))
    before_binding = transport.bindings.path.read_bytes()
    unrelated = deepcopy(transport.bindings.read()["bindings"][0])
    original_policy, original_binding = manager_context._write, conversation_bindings._atomic_write_json
    writes = 0

    def write_policy(path, value):
        nonlocal writes
        writes += 1
        if writes == {"first_policy": 1, "second_policy": 2}.get(failure_at):
            raise OSError("synthetic policy publication failure")
        original_policy(path, value)

    def write_binding(path, value):
        if path == transport.bindings.path and failure_at == "binding":
            raise OSError("synthetic binding publication failure")
        original_binding(path, value)

    def upgrade():
        return transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="codex", context_kind="steward", goal_scope="all_registered")

    with monkeypatch.context() as fault:
        fault.setattr(manager_context, "_write", write_policy)
        fault.setattr(conversation_bindings, "_atomic_write_json", write_binding)
        with pytest.raises(OSError, match="publication failure"):
            upgrade()
    assert transport.bindings.path.read_bytes() == before_binding
    # Normal admission retains the old, honest scope and never rewrites a
    # source policy to compensate for a failed operator configuration command.
    transport.admit("steward-app", provider.event("steward-app", "after-failure", "/status"))
    assert transport.bindings.read()["bindings"][1]["goal_scope"] == "selected"
    if failure_at == "first_policy":
        assert all(row["local_delivery_scope"] == "selected"
                   for row in json.loads(policy_path.read_text())["sources"].values())
    upgraded = upgrade()
    assert upgraded["binding_id"] == binding["binding_id"] and upgraded["goal_scope"] == "all_registered"
    for row in json.loads(policy_path.read_text())["sources"].values():
        assert row["local_delivery_scope"] == "all_registered"
        assert row["blocked_targets"] == source["blocked_targets"]
        assert row["sender_ids"] == source["sender_ids"]
    assert transport.bindings.read()["bindings"][0] == unrelated
    state = transport.bindings.read()
    assert upgrade() == upgraded and transport.bindings.read() == state
    assert store.list_sessions() == []


def test_confirmed_commission_runs_native_goal_returns_result_and_extends_same_session(steward):
    store, runtime, provider, transport, binding, capture, _ = steward
    transport.admit("steward-app", provider.event("steward-app", "before", "Check the empty portfolio"))
    initial = transport.core.pending()[0]
    assert finish(runtime, initial)["status"] == "completed"
    event = provider.event("steward-app", "delegate", "/delegate --tokens 12000 Inspect the authorized README and report findings")
    started = time.monotonic()
    assert transport.admit("steward-app", event)["status"] == "command_recorded"
    assert time.monotonic() - started < 3
    assert json.loads(runtime.registry_path.read_text())["goals"] == []
    transport.reconcile()
    preview = transport.core.actions.store.list()[0]
    assert preview["status"] == "preview_ready" and preview["normalized_parameters"]["heartbeat"] == {"enabled": False}
    with pytest.raises(ProtectedActionGate):
        transport.core.actions.apply(preview["proposal_id"])
    # The other App cannot consume even a known, exact proposal id.
    text = "/confirm " + preview["proposal_id"]
    transport.admit("notes-app", provider.event("notes-app", "wrong_app", text))
    wrong_app = next(r for r in transport.core.pending() if r["message"] == text)
    assert finish(runtime, wrong_app)["status"] == "completed"
    assert transport.core.actions.load(preview["proposal_id"])["status"] == "preview_ready"
    confirm = provider.event("steward-app", "confirm", text)
    transport.admit("steward-app", confirm)
    transport.reconcile()
    applied = transport.core.actions.load(preview["proposal_id"])
    assert applied["status"] == "applied", transport.core.pending()
    resources = applied["receipt"]["resource_ids"]
    turn = finish(runtime, resources)
    assert turn["status"] == "completed", turn
    assert "Verified synthetic result" in turn["response"]["message"]
    assert "Codex Goal: complete" in turn["response"]["message"]
    transport.reconcile()
    assert any(profile == "steward-app" and "Verified synthetic result" in text for profile, text in provider.writes)
    assert not any(profile == "notes-app" and "Verified synthetic result" in text for profile, text in provider.writes)
    count = len(provider.writes)
    transport.admit("steward-app", confirm)
    transport.reconcile()
    assert len(provider.writes) == count
    assert len(json.loads(runtime.registry_path.read_text())["goals"]) == 1
    assert transport.bindings.read()["bindings"][1]["goal_ids"] == [resources["goal_id"]]
    transport.admit("steward-app", provider.event("steward-app", "after", "Report the new commission status"))
    after = next(r for r in transport.core.pending() if r["message"] == "Report the new commission status")
    assert finish(runtime, after)["status"] == "completed"
    assert after["session_id"] == initial["session_id"]
    rotated = store.load_session(after["session_id"])["upstream_thread_id"]
    context = collect_manager_turn_context(runtime.registry_path, store.load_session(after["session_id"]), store.root.parent, runtime.manager_scope_resolver)
    assert [g["goal_id"] for g in context["goals"]] == [resources["goal_id"]]
    facts = transport.core.commission_evidence(store.load_session(after["session_id"]))
    assert facts[0]["native_execution"]["status"] == "complete"
    assert facts[0]["result_delivery_verified"] is True
    assert facts[0]["canonical_acceptance_attested"] is False
    registry_bytes = runtime.registry_path.read_bytes()
    registry_payload = json.loads(registry_bytes)
    registry_payload["goals"][0]["creation_operation_id"] = "replacement-operation"
    runtime.registry_path.write_text(json.dumps(registry_payload))
    assert transport.core.commission_evidence(
        store.load_session(after["session_id"])
    ) == []
    runtime.registry_path.write_bytes(registry_bytes)
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len([r for r in requests if r.get("method") == "thread/start"]) == 4
    assert any(r.get("method") == "thread/goal/set" for r in requests)
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent), registry_path=runtime.registry_path,
        project_contexts=runtime.project_contexts, codex_bin=runtime.codex_bin,
        manager_scope_resolver=transport.bindings.steward_scope)
    try:
        current, resumed = restarted.open_session(goal_id=None, agent_id="codex", work_dir=runtime.registry_path.parent,
            objective="ignored", mode="resume_latest", conversation_binding_id=binding["binding_id"],
            source_context=initial["source"])
        assert resumed and current["session_id"] == initial["session_id"] and current["upstream_thread_id"] == rotated
    finally:
        restarted.close()


@pytest.mark.parametrize("fault", ["adoption", "adoption_readback", "creation_owner_crash"])
def test_applied_commission_recovers_after_io_owner_restart_without_recreation(steward, monkeypatch, fault):
    from loopx.extensions.lark.private_conversations import LarkPrivateConversations

    class InterruptedOwner(BaseException):
        pass

    store, runtime, provider, transport, binding, capture, _ = steward
    transport.admit("steward-app", provider.event("steward-app", "delegate", "/delegate --tokens 12000 Inspect README"))
    transport.reconcile()
    preview = transport.core.actions.store.list()[0]
    text = "/confirm " + preview["proposal_id"]
    transport.admit("steward-app", provider.event("steward-app", "confirm", text))
    request = next(r for r in transport.core.pending() if r["message"] == text)
    original_adopt = transport.bindings.adopt_created_goal
    original_apply = transport.core.actions.apply
    failed = False

    def adopt(**kwargs):
        nonlocal failed
        if failed:
            return original_adopt(**kwargs)
        failed = True
        if fault == "adoption_readback":
            original_adopt(**kwargs)
        raise OSError("temporary adoption IO failure")

    def apply(*args, **kwargs):
        nonlocal failed
        result = original_apply(*args, **kwargs)
        if not failed:
            failed = True
            raise InterruptedOwner()
        return result

    if fault == "creation_owner_crash":
        monkeypatch.setattr(transport.core.actions, "apply", apply)
        with pytest.raises(InterruptedOwner):
            transport.reconcile()
    else:
        monkeypatch.setattr(transport.bindings, "adopt_created_goal", adopt)
        transport.reconcile()
    applied = transport.core.actions.load(preview["proposal_id"])
    resources = applied["receipt"]["resource_ids"]
    assert applied["status"] == "applied"
    assert finish(runtime, resources)["status"] == "completed"
    pending = transport.core.read_request(request["request_ref"])
    assert pending["status"] == "command_queued" and not pending.get("delivery_verified")
    assert not any("管家操作未完成" in message for _, message in provider.writes)
    if fault != "creation_owner_crash":
        assert pending["commission_resources"] == resources and pending["commission_adoption_pending"]
        with pytest.raises(ValueError, match="adoption is pending"):
            transport.core.record_delivery(request["request_ref"], session_id=pending["session_id"], turn_id=pending["turn_id"])
    monkeypatch.setattr(transport.core.actions, "apply", original_apply)
    monkeypatch.setattr(transport.bindings, "adopt_created_goal", original_adopt)
    threads_before = sum(json.loads(line).get("method") == "thread/start" for line in capture.read_text().splitlines())
    recovered = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                         runner=provider, cli_bin=transport.cli_bin)
    recovered.core.actions = transport.core.actions
    recovered.reconcile()
    result = recovered.core.read_request(request["request_ref"])
    assert result["commission_resources"] == resources and result["delivery_verified"]
    assert not result.get("commission_adoption_pending")
    assert transport.bindings.read()["bindings"][1]["goal_ids"] == [resources["goal_id"]]
    assert len(json.loads(runtime.registry_path.read_text())["goals"]) == 1
    assert recovered.core.actions.load(preview["proposal_id"])["receipt"]["resource_ids"] == resources
    assert sum(profile == "steward-app" and "Verified synthetic result" in message for profile, message in provider.writes) == 1
    writes = len(provider.writes)
    recovered.reconcile()
    assert len(provider.writes) == writes
    assert sum(json.loads(line).get("method") == "thread/start" for line in capture.read_text().splitlines()) == threads_before


def test_post_commit_adoption_cannot_outlive_original_private_authority(steward, monkeypatch):
    _, runtime, provider, transport, binding, _, _ = steward
    transport.admit("steward-app", provider.event("steward-app", "delegate", "/delegate --tokens 12000 Inspect README"))
    transport.reconcile()
    preview = transport.core.actions.store.list()[0]
    text = "/confirm " + preview["proposal_id"]
    transport.admit("steward-app", provider.event("steward-app", "confirm", text))
    original = transport.bindings.adopt_created_goal
    def unavailable(**kwargs):
        raise OSError("temporary adoption IO failure")
    monkeypatch.setattr(transport.bindings, "adopt_created_goal", unavailable)
    transport.reconcile()
    resources = transport.core.actions.load(preview["proposal_id"])["receipt"]["resource_ids"]
    assert finish(runtime, resources)["status"] == "completed"
    transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    monkeypatch.setattr(transport.bindings, "adopt_created_goal", original)
    writes = len(provider.writes)
    transport.reconcile()
    row = next(r for r in transport.core.pending() if r["message"] == text)
    assert row["commission_adoption_pending"] and not row.get("delivery_verified")
    assert row["commission_resources"] == resources
    assert len(provider.writes) == writes
    assert len(json.loads(runtime.registry_path.read_text())["goals"]) == 1


def test_expired_preview_and_wrong_source_cannot_create_a_goal(steward):
    _, runtime, provider, transport, _, _, _ = steward
    transport.admit("steward-app", provider.event("steward-app", "delegate", "/delegate --tokens 1000 Read only"))
    transport.reconcile()
    preview = transport.core.actions.store.list()[0]
    text = "/confirm " + preview["proposal_id"]
    wrong = provider.event("steward-app", "foreign", text)
    wrong["chat_id"] = "oc_other_source"
    provider.messages[wrong["message_id"]]["chat_id"] = wrong["chat_id"]
    transport.admit("steward-app", wrong)
    transport.reconcile()
    assert json.loads(runtime.registry_path.read_text())["goals"] == []
    # Simulate a confirmation received beyond the immutable preview expiry.
    event = provider.event("steward-app", "expired", text)
    transport.admit("steward-app", event)
    binding = transport.bindings.read()["bindings"][1]
    request_ref = identity_ref(binding["provider_ref"], event["message_id"])
    row = transport.core.read_request(request_ref)
    path = transport.core.root / f"{row['request_ref']}.json"
    row["created_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(row))
    transport.reconcile()
    assert json.loads(runtime.registry_path.read_text())["goals"] == []
    assert transport.core.actions.load(preview["proposal_id"])["status"] == "preview_ready"


def test_exact_commission_stop_keeps_other_app_running_and_replay_keeps_target(steward):
    store, runtime, provider, transport, _, _, _ = steward
    transport.admit("steward-app", provider.event("steward-app", "delegate", "/delegate --tokens 12000 wait for interrupt"))
    transport.reconcile()
    preview = transport.core.actions.store.list()[0]
    transport.admit("steward-app", provider.event("steward-app", "confirm", "/confirm " + preview["proposal_id"]))
    transport.reconcile()
    resources = transport.core.actions.load(preview["proposal_id"])["receipt"]["resource_ids"]
    deadline = time.monotonic() + 10
    while not any(e["kind"] == "turn.started" for e in store.events_after(resources["session_id"], resources["turn_id"], None)):
        assert time.monotonic() < deadline
        time.sleep(.01)
    transport.admit("notes-app", provider.event("notes-app", "independent", "ordinary independent answer"))
    other = next(r for r in transport.core.pending() if r["message"] == "ordinary independent answer")
    assert finish(runtime, other)["status"] == "completed"
    stop = provider.event("steward-app", "stop_exact", "/stop-commission " + preview["proposal_id"])
    transport.admit("steward-app", stop)
    transport.reconcile()
    assert finish(runtime, resources)["status"] == "interrupted"
    transport.admit("steward-app", stop)
    transport.reconcile()
    assert store.load_turn(other["session_id"], other["turn_id"])["status"] == "completed"
    assert transport.core.actions.load(preview["proposal_id"])["receipt"]["resource_ids"] == resources
