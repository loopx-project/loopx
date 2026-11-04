"""Multiple recipients share an ingress, never authority, effects or completion."""
import json

import pytest

from loopx.chat import normalize_agent_response
from loopx.capabilities.manager_context.execution import handoff_response
from loopx.capabilities.manager_context import POLICY_SCHEMA, _root, _write, acknowledge, register_ingress
from loopx.capabilities.manager_context.roundtrip import drain, report, project_chat_session_snapshot
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.collaboration.peers import read_inbox


def request(goal, agent):
    return {"goal_id": goal, "agent_id": agent, "brief": {
        "schema_version": "collaboration_brief_v0", "purpose": "Independent source assessment: " + agent,
        "context": "Check your own source; do not share another recipient's private notes.",
        "constraints": ["No external effects"], "inputs": [], "acceptance": ["Return your finding"],
        "return_requirement": "Return to this original conversation"}}


@pytest.mark.parametrize("bad", ["not-an-array", [request("a", "one"), request("a", "one")],
    [request("a", "one"), {"goal_id": "a", "agent_id": "two"}], [request("a", f"worker-{i}") for i in range(33)],
    [request("../foreign", "one")]])
def test_batch_rejects_ambiguous_duplicate_incomplete_or_unbounded_intent(bad):
    with pytest.raises(ValueError):
        normalize_agent_response({"context_handoff": bad})


def test_single_compatibility_and_same_named_agents_in_distinct_goals():
    one = request("a", "worker")
    assert normalize_agent_response({"context_handoff": one})["context_handoff"] == one
    assert normalize_agent_response({"context_handoff": [one, request("b", "worker")]})["context_handoff"][1]["goal_id"] == "b"
    assert "context_handoff" not in normalize_agent_response({"context_handoff": []})
    assert "goal_draft" not in normalize_agent_response({"context_handoff": [one], "goal_draft": {
        "objective": "Unrelated", "completion_criteria": "", "execution_boundary": "", "question": "", "options": []}})


@pytest.mark.parametrize("partial", [False, True])
def test_cross_goal_batch_independent_returns_replay_and_partial_revocation(tmp_path, partial):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": goal, "repo": str(tmp_path),
        "coordination": {"registered_agents": ["worker"]}} for goal in ("analysis", "implementation")]}))
    before = registry.read_bytes()
    store = ChatSessionStore(tmp_path)
    session = store.create_session(goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="original", channel_id="manager.external.synthetic")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="one-request", message="Ask both owners", origin="lark")
    source = {"sender_ids": ["owner"], "local_delivery_scope": "selected",
        "targets": [{"goal_id": goal, "agent_id": "worker"} for goal in ("analysis", "implementation")]}
    policy = {"schema_version": POLICY_SCHEMA, "sources": {session["channel_id"]: source}}
    _write(_root(tmp_path) / "policy.json", policy)
    register_ingress(tmp_path, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
        channel=session["channel_id"], sender_id="owner", message=turn["message"], source_id="synthetic:original")
    response = normalize_agent_response({"message": "Checking both sources.", "context_handoff": [
        request("analysis", "worker"), request("unregistered", "worker"), request("implementation", "worker")]})
    checks = []
    def authorized():
        checks.append(True)
        if partial and len(checks) == 2:
            source["targets"] = source["targets"][:1]
            _write(_root(tmp_path) / "policy.json", policy)
        return True
    def send():
        return handoff_response(tmp_path, registry, session=session, turn=turn, response=response,
            source_store=store, source_authorized=authorized, execution_allowed=lambda: True)
    saved = send()
    assert saved["message"].count("Checking both sources.") == 1
    receipts = saved["context_handoff_receipts"]
    assert len(receipts) == (1 if partial else 2)
    assert saved["context_handoff_failures"] == [{"goal_id": "unregistered", "agent_id": "worker"}] + ([{"goal_id": "implementation", "agent_id": "worker"}] if partial else [])
    assert all(not row["submitted"] for row in saved["context_executions"])
    replay = send()
    assert [r["request_id"] for r in replay["context_handoff_receipts"]] == [r["request_id"] for r in receipts]
    assert all(r["replayed"] for r in replay["context_handoff_receipts"])
    store.append_message(session["session_id"], role="assistant", text=saved["message"], turn_id=turn["turn_id"])
    store.update_turn(session["session_id"], turn["turn_id"], status="completed", response=saved)
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    for receipt in reversed(receipts):
        rows = read_inbox(tmp_path, registry, receipt["goal_id"], "worker")["items"]
        assert [r["request_id"] for r in rows] == [receipt["request_id"]]
        acknowledge(tmp_path, receipt["goal_id"], "worker", receipt["request_id"], "adopt", "Independent assessment")
        report(tmp_path, receipt["goal_id"], "worker", receipt["request_id"], "conclusion", "Finding from " + receipt["goal_id"])
    drain(tmp_path, registry, ChatSessionStore(tmp_path), lambda *_: {"reply_verified": True, "external_write_performed": True})
    drain(tmp_path, registry, ChatSessionStore(tmp_path), lambda *_: {"reply_verified": True, "external_write_performed": True})
    snapshot = project_chat_session_snapshot(tmp_path, ChatSessionStore(tmp_path), session["session_id"], registry=registry)
    # An external/group surface must not gain owner-private brief cards.
    assert not any(row.get("collaborations") or row.get("collaboration") for row in snapshot["messages"])
    results = [row for row in snapshot["messages"] if row.get("origin") == "manager_followup"]
    assert len(results) == len(receipts)
    assert {row["text"].split("Finding from ")[-1] for row in results} == {r["goal_id"] for r in receipts}
    assert registry.read_bytes() == before


def test_runtime_routes_one_turn_to_both_without_a_relay_or_second_model(tmp_path, monkeypatch):
    from loopx.chat_runtime import ChatRuntimeController
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "research", "repo": str(tmp_path),
        "coordination": {"registered_agents": ["analyst", "builder"]}}]}))
    store = ChatSessionStore(tmp_path)
    controller = ChatRuntimeController(store=store, codex_bin="unused", registry_path=registry)
    calls = []
    class Model:
        upstream_thread_id = "synthetic-model"
        def healthcheck(self): return True
        def close_session(self): pass
        def start_turn(self, message, sink):
            calls.append(message)
            return normalize_agent_response({"message": "Ask both directly.", "context_handoff": [
                request("research", "analyst"), request("research", "builder")]})
    monkeypatch.setattr(controller, "capabilities", lambda: [{"agent_id": "codex", "available": True,
        "adapter_kind": "codex_app_server"}])
    monkeypatch.setattr(controller, "_start_adapter", lambda **_: Model())
    monkeypatch.setattr("loopx.chat_manager_context.collect_manager_turn_context", lambda *_, **__: {"goals": [], "coverage": {}})
    try:
        session, _ = controller.open_session(goal_id="research", agent_id="codex", work_dir=tmp_path,
            objective="Independent findings", mode="new", channel_id="manager")
        args = dict(session_id=session["session_id"], client_turn_id="one-question", message="Ask analyst and builder",
            work_dir=tmp_path, objective="Independent findings")
        turn, _ = controller.submit_turn(**args)
        done = controller.wait_for_turn(session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=30)
        assert done["status"] == "completed", done
        assert {r["agent_id"] for r in done["response"]["context_handoff_receipts"]} == {"analyst", "builder"}
        assert controller.submit_turn(**args)[1] is False
        assert len(calls) == 1
    finally:
        controller.close()


def test_no_handoff_keeps_ordinary_response_free_of_collaboration_runtime(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise AssertionError("An ordinary answer must not start collaboration IO")
    monkeypatch.setattr("loopx.control_plane.effect_runtime.effect_runtime_result", unavailable)
    assert normalize_agent_response({"message": "Ordinary answer", "context_handoff": None})["message"] == "Ordinary answer"


def test_batch_shape_is_admitted_before_the_first_delivery(tmp_path, monkeypatch):
    from loopx.capabilities import manager_context
    writes = []
    def publish(*_args, **_kwargs):
        writes.append(True)
        return {"goal_id": "a", "agent_id": "one", "request_id": "a" * 64, "status": "delivered"}
    monkeypatch.setattr(manager_context, "deliver", publish)
    with pytest.raises(ValueError):
        handoff_response(tmp_path, tmp_path / "registry.json", session={}, turn={},
            response={"context_handoff": [request("a", "one"), {"goal_id": "a", "agent_id": "two"}]},
            source_authorized=lambda: True, execution_allowed=lambda: True)
    assert writes == []
    assert len(normalize_agent_response({"context_handoff": [request("a", f"worker-{i}") for i in range(32)]})["context_handoff"]) == 32
