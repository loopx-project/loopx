"""Exact source grants, task-bound dispatch, revocation and truthful fallback.

The governed integration uses the existing explicit fixture host. Live model,
provider delivery and routing quality require separate product qualification.
"""

import json
from pathlib import Path

import pytest

from loopx.capabilities.manager_context import (
    POLICY_SCHEMA, _root, _write, deliver, register_ingress,
)
from loopx.capabilities.manager_context import execution
from loopx.chat import normalize_agent_response
from loopx.chat_agent import _turn_prompt
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.manager_context.inspection import manager_index
from test_local_delegation import brief, wait, service as delegation_service  # noqa: F401
from test_independent_delegation_validation import independent_binding


def source(root, registry, *, goal_id, agent_id, requester, binding, source_id="lark:exact-message", semantic_brief=None, chat_root=None):
    store = ChatSessionStore(root if chat_root is None else chat_root)
    session = store.create_session(goal_id="loopx-manager", agent_id="codex",
                                   adapter_kind="codex_app_server", upstream_thread_id="original",
                                   channel_id="manager.external.test")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="current-request",
                                message="Do the authorized bounded review and return here.", origin="lark")
    grant = {"goal_id": goal_id, "agent_id": agent_id, "requester_agent_id": requester, "binding_id": binding}
    policy = {"schema_version": POLICY_SCHEMA, "sources": {session["channel_id"]: {
        "sender_ids": ["owner"], "execution_bindings": [grant],
    }}}
    _write(_root(root) / "policy.json", policy)
    register_ingress(root, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
                     channel=session["channel_id"], sender_id="owner", message=turn["message"],
                     source_id=source_id)
    request = {"goal_id": goal_id, "agent_id": agent_id, "execution_binding_id": binding, "brief": brief() if semantic_brief is None else semantic_brief}
    receipt = deliver(root, registry, session=session, turn=turn, request=request)
    return store, session, turn, request, receipt, policy


@pytest.fixture
def flow(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "research", "repo": str(tmp_path),
        "spawn_policy": {"execution_config": ".loopx/config/delegations.json"},
        "coordination": {"registered_agents": ["lead", "worker"]}}]}))
    config = tmp_path / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}")
    started = []

    class BoundService:
        def __init__(self, *args):
            pass

        def binding(self, binding_id, **kwargs):
            assert binding_id == "review"
            return {"id": binding_id, "agent_id": "worker", "todo_id": "todo_current"}

        def path(self, operation):
            return tmp_path / operation

        def inspect(self, binding_id):
            return {"state": "launchable", "turn_eligible": True, "acceptance_ready": True, "authority_ready": True}

        def start(self, binding_id, operation, semantic_brief, **kwargs):
            started.append((binding_id, operation, semantic_brief, kwargs))
            self.path(operation).write_text("prepared")
            return {"status": "prepared"}

        def read(self, operation):
            return {"status": "accepted"}

    monkeypatch.setattr(execution, "Delegations", BoundService)
    values = source(tmp_path, registry, goal_id="research", agent_id="worker", requester="lead", binding="review")
    return tmp_path, registry, values, started


def dispatch(flow, **changes):
    root, registry, (_, session, turn, request, receipt, _), _ = flow
    return execution.dispatch(root, registry, session=session, turn=turn, request=request, receipt=receipt,
                              execution_allowed=lambda: True, **changes)


def test_exact_catalog_and_launch_keep_original_conversation_and_operation(flow):
    root, registry, (_, session, turn, request, receipt, _), started = flow
    assert execution.catalog(root, registry, session, turn) == {"available": True, "bindings": [
        {"goal_id": "research", "agent_id": "worker", "binding_id": "review", "todo_id": "todo_current"}]}
    normalized = normalize_agent_response({"context_handoff": request})
    assert normalized["context_handoff"]["execution_binding_id"] == "review"
    assert dispatch(flow)["status"] == "prepared"
    assert started[0][1] == "context-" + receipt["request_id"]
    assert started[0][3]["conversation"] == {"session_id": session["session_id"], "turn_id": turn["turn_id"]}
    assert started[0][2] == request["brief"]
    assert started[0][3]["source_request_id"] == receipt["request_id"]
    assert dispatch(flow)["replayed"]
    assert len(started) == 1


def test_model_receives_authorized_task_choices_after_manager_context_compaction(flow):
    root, registry, (_, session, turn, _, _, _), _ = flow
    choices = execution.catalog(root, registry, session, turn)
    prompt = _turn_prompt(turn["message"], context_summary=json.dumps(manager_index({
        "context_execution": choices,
    })))
    # The production prompt projection must retain the operator's exact task
    # choice. Registration/route discovery cannot substitute for this grant.
    assert '"binding_id": "review"' in prompt and '"todo_id": "todo_current"' in prompt
    assert str(root) not in prompt and "delegations.json" not in prompt
    disabled = manager_index({})
    assert "context_execution" not in disabled
    assert "execution_binding_id" not in _turn_prompt(turn["message"], context_summary=json.dumps(disabled))
    assert manager_index({"context_execution": {"available": True, "bindings": []}}) == disabled
    unavailable = manager_index({"context_execution": {"available": False, "bindings": [],
                                                        "reason": "execution_bindings_unavailable"}})
    assert unavailable["context_execution"]["available"] is False
    assert "execution_binding_id" not in _turn_prompt(turn["message"], context_summary=json.dumps(unavailable))


def test_handoff_response_preserves_receipt_and_separate_execution_status(flow):
    root, registry, (_, session, turn, request, _, _), started = flow
    response = execution.handoff_response(root, registry, session=session, turn=turn,
        response={"context_handoff": request, "message": "Unverified model completion claim.",
                  "proposals": [{"kind": "unused"}], "gate": {"kind": "unused"}},
        source_authorized=lambda: True, execution_allowed=lambda: True)
    assert response["context_handoff_receipt"]["status"] == "delivered"
    assert response["context_execution"]["status"] == "prepared"
    assert "受理不代表完成" in response["message"]
    assert response["proposals"] == [] and response["gate"] is None
    assert len(started) == 1


def test_handoff_scope_revocation_stops_before_inbox_delivery(flow, monkeypatch):
    root, registry, (_, session, turn, request, _, _), started = flow
    from loopx.capabilities import manager_context
    def forbidden_delivery(*args, **kwargs):
        pytest.fail("revoked manager scope must not publish an inbox request")
    monkeypatch.setattr(manager_context, "deliver", forbidden_delivery)
    response = execution.handoff_response(root, registry, session=session, turn=turn,
        response={"context_handoff": request}, source_authorized=lambda: False,
        execution_allowed=lambda: True)
    assert "尚未转交" in response["message"]
    assert "context_handoff_receipt" not in response and not started


def test_lifecycle_only_registry_cannot_authorize_host_execution(flow):
    from loopx.control_plane.projects.registry_codec import SOURCE_SESSION_PROFILE_ID
    root, registry, (_, session, turn, _, _, _), started = flow
    data = json.loads(registry.read_text())
    data["profile_id"] = SOURCE_SESSION_PROFILE_ID
    registry.write_text(json.dumps(data))
    assert execution.catalog(root, registry, session, turn)["available"] is False
    assert execution.catalog(root, None, session, turn)["available"] is False
    assert dispatch(flow)["submitted"] is False
    assert not started


@pytest.mark.parametrize("change", ["sender", "body", "channel", "revoke", "blocked", "requester", "stopped", "binding"])
def test_no_launch_after_source_or_registration_changes(flow, change):
    root, registry, (_, session, turn, _, _, policy), started = flow
    if change == "sender":
        policy["sources"][session["channel_id"]]["sender_ids"] = ["someone-else"]
    elif change == "body":
        turn["message"] = "Different input"
    elif change == "channel":
        session["channel_id"] = "manager.external.other-app"
    elif change == "binding":
        # A newly configured choice in the same Goal/Agent is not covered by
        # the existing source's exact binding consent.
        flow[2][3]["execution_binding_id"] = "new-task-choice"
    elif change == "revoke":
        policy["sources"][session["channel_id"]].pop("execution_bindings")
    elif change == "blocked":
        policy["sources"][session["channel_id"]]["blocked_targets"] = [{"goal_id": "research"}]
    else:
        data = json.loads(registry.read_text())
        if change == "requester":
            data["goals"][0]["coordination"]["registered_agents"] = ["worker"]
        else:
            data["goals"][0]["activation_state"] = "stopped"
        registry.write_text(json.dumps(data))
    _write(_root(root) / "policy.json", policy)
    assert not dispatch(flow)["submitted"]
    assert not started


@pytest.mark.parametrize("state", ["turn_blocked", "acceptance_unavailable", "runtime_unavailable"])
def test_preflight_never_becomes_a_launch_permit(flow, monkeypatch, state):
    monkeypatch.setattr(execution.Delegations, "inspect", lambda *_: {"state": state})
    result = dispatch(flow)
    assert result["preflight"]["state"] == state and not result["submitted"]
    assert not flow[3]


def test_stop_during_preview_and_context_only_selection_do_not_launch(flow, monkeypatch):
    root, registry, (_, session, turn, request, receipt, _), started = flow
    active = [True]
    def preview(*_):
        active[0] = False
        return {"state": "launchable", "turn_eligible": True, "acceptance_ready": True, "authority_ready": True}
    monkeypatch.setattr(execution.Delegations, "inspect", preview)
    result = execution.dispatch(root, registry, session=session, turn=turn, request=request, receipt=receipt,
                                execution_allowed=lambda: active[0])
    assert not result["submitted"] and not started
    request.pop("execution_binding_id")
    assert dispatch(flow) == {"submitted": False}
    assert "尚未启动执行" in execution.handoff_message(receipt, {"submitted": False})


def budget_brief(boundary):
    semantic = brief()
    if boundary == "return_field":
        semantic["return_requirement"] = "r" * 2000
    else:
        semantic["context"] = "c" * 6000
        semantic["constraints"] = ["x" * 1000] * 8
        semantic["return_requirement"] = "r" * 1000
        current = len(json.dumps(semantic, ensure_ascii=False, separators=(",", ":")).encode())
        semantic["constraints"].append("x" * (15800 - current - 3))
        assert len(json.dumps(semantic, ensure_ascii=False, separators=(",", ":")).encode()) == 15800
    return semantic


@pytest.mark.parametrize("boundary", ["short", "return_field", "encoded_total"])
def test_governed_worker_adopts_original_request_and_returns_without_another_model_turn(delegation_service, boundary):  # noqa: F811
    root, service = delegation_service
    independent_binding(delegation_service)
    registry = service.registry
    data = json.loads(registry.read_text())
    project = Path(data["goals"][0]["repo"])
    config = project / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(service.config.read_bytes())
    data["goals"][0]["spawn_policy"] = {"execution_config": ".loopx/config/delegations.json"}
    registry.write_text(json.dumps(data))
    store, session, turn, request, receipt, _ = source(service.root, registry, goal_id=service.goal_id,
        agent_id="analyst", requester="lead", binding="analysis", semantic_brief=budget_brief(boundary))
    # The fixture receiver (not the Chat caller) reads, decides and returns the
    # original owner request as well as its separately accepted peer result.
    host = root / "fixture-host.py"
    content = host.read_text()
    anchor = "print(json.dumps(build_result"
    statement = (
        "from loopx.control_plane.collaboration.peers import read_inbox\n"
        "from loopx.capabilities.manager_context.roundtrip import report\n"
        "source_id = delegation['source_request_id']\n"
        "items = read_inbox(root / 'runtime', root / 'registry.json', envelope['goal_id'], actor)['items']\n"
        "assert any(item['request_id'] == source_id for item in items)\n"
        "for item in items:\n"
        "    if item['request_id'] == source_id:\n"
        "        acknowledge(root / 'runtime', envelope['goal_id'], actor, item['request_id'], 'adopt', 'Receiver independently read the original scope.')\n"
        "        report(root / 'runtime', envelope['goal_id'], actor, item['request_id'], 'conclusion', 'Independent fixture result returned to the original request.')\n"
    )
    host.write_text(content.replace(anchor, statement + anchor))
    launched = execution.dispatch(service.root, registry, session=session, turn=turn, request=request, receipt=receipt,
                                   execution_allowed=lambda: True)
    assert launched["submitted"], launched
    assert launched["runtime_readiness"] == "runtime_unverified"
    worker = execution._service(service.root, registry, {"goal_id": service.goal_id, "agent_id": "analyst",
        "requester_agent_id": "lead", "binding_id": "analysis"})[0]
    result = wait(worker, launched["operation_id"])
    assert result["status"] == "accepted", result
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
                      response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    from loopx.capabilities.manager_context.roundtrip import drain
    sends = []
    def transport(route, *_args, **_kwargs):
        sends.append(route["session_id"])
        return {"message_id": "provider-return", "reply_verified": True}
    drain(service.root, registry, store, transport)
    assert sends == [session["session_id"]]
    assert worker.read(launched["operation_id"])["status"] == "accepted"
    assert (Path(worker.binding("analysis")["workspace"]) / "host-invocations").read_text() == "1"
    # A new user request cannot reactivate the completed task merely because
    # its source grant and registered Agent still exist.
    _, new_session, new_turn, new_request, new_receipt, _ = source(service.root, registry,
        goal_id=service.goal_id, agent_id="analyst", requester="lead", binding="analysis", source_id="lark:second-message")
    refused = execution.dispatch(service.root, registry, session=new_session, turn=new_turn,
        request=new_request, receipt=new_receipt, execution_allowed=lambda: True)
    assert not refused["submitted"] and refused["reason"] == "execution_not_launchable", refused
    assert (Path(worker.binding("analysis")["workspace"]) / "host-invocations").read_text() == "1"


@pytest.mark.parametrize("boundary", ["return_field", "encoded_total"])
@pytest.mark.parametrize("separate_chat_store", [False, True])
def test_legal_brief_budget_survives_real_chat_dispatch(delegation_service, monkeypatch, boundary, separate_chat_store):  # noqa: F811
    """Internal return routing must not consume a caller's semantic budget."""
    from loopx.control_plane.collaboration.inbox import normalize_request, _read

    root, service = delegation_service
    independent_binding(delegation_service)
    data = json.loads(service.registry.read_text())
    project = Path(data["goals"][0]["repo"])
    config = project / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(service.config.read_bytes())
    data["goals"][0]["spawn_policy"] = {"execution_config": ".loopx/config/delegations.json"}
    if separate_chat_store:
        data["common_runtime_root"] = str(service.root)
    service.registry.write_text(json.dumps(data))
    semantic = budget_brief(boundary)
    semantic = normalize_request({"goal_id": service.goal_id, "agent_id": "analyst", "brief": semantic})["brief"]
    store, session, turn, request, _, _ = source(service.root, service.registry,
        goal_id=service.goal_id, agent_id="analyst", requester="lead", binding="analysis", semantic_brief=semantic,
        chat_root=service.root / "private-chat" if separate_chat_store else None)
    # Let the production Chat adapter deliver and call the real start owner,
    # but keep this admission oracle separate from the actual worker test.
    started = []
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda _, operation: started.append(operation))
    from loopx.chat_runtime import ChatRuntimeController
    import loopx.chat_manager_context as manager_context
    controller = ChatRuntimeController(store=store, codex_bin="codex", registry_path=service.registry,
                                       manager_scope_resolver=lambda _: [service.goal_id])
    assert controller.coordination_runtime_root == service.root
    if separate_chat_store:
        assert store.root.parent != service.root
    scope_id = manager_context.manager_authorization_scope_id_for_registry(
        service.registry,
        [service.goal_id],
        runtime_root=service.root,
        channel_id=session["channel_id"],
    )
    assert scope_id is not None
    store.update_session(
        session["session_id"],
        manager_authorization_scope_id=scope_id,
    )
    monkeypatch.setattr(manager_context, "collect_manager_turn_context", lambda *_, **__: {
        "coverage": {}, "goals": [], "authorization_scope_id": scope_id})
    model_calls = []
    class Adapter:
        upstream_thread_id = "fixture-upstream"
        def start_turn(self, message, sink):
            assert "context_execution" in message and '"binding_id": "analysis"' in message
            model_calls.append(message)
            return {"context_handoff": request, "proposals": [], "gate": None, "message": "Preparing work"}
        def close_session(self):
            pass
    try:
        controller._run_turn(session_id=session["session_id"], turn_id=turn["turn_id"],
                             message=turn["message"], attachments=[], adapter=Adapter())
        completed = store.load_turn(session["session_id"], turn["turn_id"])
        assert completed["status"] == "completed", completed
        response = completed["response"]
        assert len(model_calls) == 1
    finally:
        controller.close()
    assert response["context_handoff_receipt"]["status"] == "delivered"
    assert response["context_execution"]["submitted"], response
    worker, binding = execution._service(service.root, service.registry, {"goal_id": service.goal_id,
        "agent_id": "analyst", "requester_agent_id": "lead", "binding_id": "analysis"})
    operation = response["context_execution"]["operation_id"]
    row = _read(worker.path(operation))
    bootstrap = worker._delegation_bootstrap(row, binding)
    assert bootstrap["brief"] == request["brief"]
    assert response["context_handoff_receipt"]["request_id"] in bootstrap["instruction"]
    assert started == [operation]
    assert bootstrap["source_request_id"] == response["context_handoff_receipt"]["request_id"]
    before = worker.path(operation).read_bytes()
    # Another legitimate request with the same brief cannot replace the cause
    # of this operation; invalid/mismatched references fail before dispatch.
    _, _, _, _, other, _ = source(service.root, service.registry, goal_id=service.goal_id,
        agent_id="analyst", requester="lead", binding="analysis", source_id="lark:other", semantic_brief=semantic)
    with pytest.raises(ValueError, match="identity conflict"):
        worker.start("analysis", operation, semantic, source_request_id=other["request_id"])
    with pytest.raises(ValueError, match="invalid context request id"):
        worker.start("analysis", "invalid-source", semantic, source_request_id="../outside")
    with pytest.raises(ValueError, match="source request brief"):
        worker.start("analysis", "mismatched-source", brief(), source_request_id=other["request_id"])
    assert worker.path(operation).read_bytes() == before
    assert started == [operation]
    assert not worker.path("invalid-source").exists() and not worker.path("mismatched-source").exists()
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
