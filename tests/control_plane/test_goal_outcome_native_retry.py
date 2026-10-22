"""Native outcome recovery retains provenance without bypassing live admission."""

import json

from loopx.chat_loopx_mode import TOOL
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.goals import acceptance
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from test_goal_acceptance_prepared_action import conversation_check as conversation_check


def outcome_request(adapter):
    before = adapter.session.read_tool_handler(TOOL["name"], {"action": "outcomes"})
    return {
        "action": "refine_outcome", "operation_id": "retain-tour-request",
        "expected_provider_revision": before["provider_revision"],
        "document": {
            "outcome_id": "tour", "objective": "Deliver the requested tour",
            "requirements": [{"id": "opening", "description": "Keep the introduction"}],
            "source_summary": "The owner requested a tour retaining the introduction",
        },
    }


def test_committed_lost_reply_replays_original_envelope_after_restart_and_new_turn(
    conversation_check, monkeypatch,
):
    project, cli, controller, sid, _, _, adapter = conversation_check
    request = outcome_request(adapter)
    original_refine = acceptance.refine_goal_outcome

    def lose_reply(**arguments):
        original_refine(**arguments)
        raise OSError("response lost after canonical commit")

    monkeypatch.setattr(acceptance, "refine_goal_outcome", lose_reply)
    assert adapter.session.read_tool_handler(TOOL["name"], request)["ok"] is False
    monkeypatch.setattr(acceptance, "refine_goal_outcome", original_refine)
    committed = cli("inspect")[1]
    outcomes = committed["goal_acceptance_contract"]["outcome_requirements"]
    assert outcomes["revision"] == 1
    source = outcomes["outcomes"][0]["sources"][0]
    assert source["ref"].startswith("turn:admitted-tool-turn:")

    # Recreate both caller objects so recovery can only use durable Session data.
    restarted = ChatRuntimeController(store=ChatSessionStore(project.parent / "runtime"),
        codex_bin="unused", registry_path=controller.registry_path)
    try:
        resumed = adapter.prepare_turn("later-tool-turn", runtime_controller=restarted)
        handler = resumed.session.read_tool_handler
        replay = handler(TOOL["name"], request)
        assert replay["ok"] and replay["status"] == "replayed", replay
        retained = replay["goal_acceptance_contract"]["outcome_requirements"]
        assert retained["revision"] == 1
        assert retained["outcomes"][0]["sources"] == [source]
        assert cli("inspect")[1] == committed

        for changed in (
            {**request, "document": {**request["document"], "source_summary": "A different request"}},
            {**request, "expected_provider_revision": committed["provider_revision"]},
            {**request, "document": {**request["document"], "source": source}},
            {**request, "document": {**request["document"], "owner_agent_id": "agent-a"}},
        ):
            assert handler(TOOL["name"], changed)["error"] == "collaboration_request_rejected"
        assert cli("inspect")[1] == committed

        # Possession of a saved operation never substitutes for live admission.
        mode = restarted.store.load_session(sid)["loopx_mode"]
        restarted.store.update_session(sid, loopx_mode={**mode, "paused": True})
        assert handler(TOOL["name"], request)["error"] == "conversation_execution_inactive"
        restarted.store.update_session(sid, loopx_mode=mode)
        (project / ".loopx/config/delegations.json").unlink()
        assert handler(TOOL["name"], request)["error"] == "collaboration_request_rejected"
        assert cli("inspect")[1] == committed
    finally:
        restarted.close()


def test_another_session_or_actor_cannot_reuse_saved_outcome_operation(conversation_check):
    project, cli, controller, sid, _, _, adapter = conversation_check
    request = outcome_request(adapter)
    first = adapter.session.read_tool_handler(TOOL["name"], request)
    assert first["ok"], first
    committed = cli("inspect")[1]
    mode = controller.store.load_session(sid)["loopx_mode"]
    other = controller.store.create_session(goal_id="goal-acceptance", agent_id="codex",
        channel_id="goal.goal-acceptance", upstream_thread_id="another-thread",
        upstream_mode="chat", adapter_kind="codex_app_server")
    controller.store.update_session(other["session_id"], loopx_mode=mode)
    # Even an identical opaque Turn ID in another Session cannot inherit source.
    foreign = adapter.prepare_turn("admitted-tool-turn", session_id=other["session_id"])
    assert foreign.session.read_tool_handler(TOOL["name"], request)["error"] == "collaboration_request_rejected"

    registry = controller.registry_path
    payload = json.loads(registry.read_text())
    payload["goals"][0]["coordination"]["registered_agents"].append("other-coordinator")
    registry.write_text(json.dumps(payload))
    config = project / ".loopx/config/delegations.json"
    bindings = json.loads(config.read_text())
    bindings["bindings"][0]["requesters"].append("other-coordinator")
    config.write_text(json.dumps(bindings))
    controller.store.update_session(sid, loopx_mode={**mode,
        "settings": {**mode["settings"], "agent_id": "other-coordinator"}})
    changed_actor = foreign.prepare_turn("new-actor-turn", session_id=sid)
    result = changed_actor.session.read_tool_handler(TOOL["name"], request)
    assert result["error"] == "collaboration_request_rejected", result
    assert cli("inspect")[1] == committed


def test_outcome_observations_never_replace_or_emit_member_deliveries(conversation_check):
    _, _, controller, sid, _, _, adapter = conversation_check
    deliveries = [{"operation_id": "member-delivery", "agent_id": "agent-a",
        "todo_id": "todo_export", "status": "accepted"}]
    controller.store.update_session(sid, loopx_deliveries=deliveries)
    request = outcome_request(adapter)
    handler = adapter.session.read_tool_handler
    for arguments in (request, request, {"action": "outcomes"}):
        assert handler(TOOL["name"], arguments)["ok"]
        assert controller.store.load_session(sid)["loopx_deliveries"] == deliveries
    assert not any(kind == "collaboration.execution" for kind, _ in adapter.events)


def test_native_review_omits_declaration_source_and_recovers_lost_reply(conversation_check, monkeypatch):
    _, cli, _, _, _, _, adapter = conversation_check
    handler = adapter.session.read_tool_handler
    created = handler(TOOL["name"], outcome_request(adapter))
    assert created["ok"], created
    origins = created["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["sources"]
    request = {"action": "refine_outcome", "operation_id": "review-tour",
        "expected_provider_revision": created["provider_revision"], "document": {"outcome_id": "tour",
            "review": [{"id": "opening", "conclusion": "met", "evidence_refs": ["artifact:tour-v2"],
                "note": "Read the artifact and retained opening"}]}}
    original = acceptance.refine_goal_outcome

    def lose_reply(**arguments):
        original(**arguments)
        raise OSError("response lost after review commit")

    monkeypatch.setattr(acceptance, "refine_goal_outcome", lose_reply)
    assert handler(TOOL["name"], request)["ok"] is False
    monkeypatch.setattr(acceptance, "refine_goal_outcome", original)
    replay = handler(TOOL["name"], request)
    assert replay["ok"] and replay["status"] == "replayed", replay
    outcome = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert outcome["revision"] == 2 and outcome["sources"] == origins
    assert outcome["requirements"][0]["agent_review"]["evidence_refs"] == ["artifact:tour-v2"]
    assert outcome["requirements"][0]["status"] == "unverified"
    before = cli("inspect")[1]
    for document in [
        {"outcome_id": "tour", "requirements": [{"id": "extra", "description": "New obligation"}]},
        {"outcome_id": "new", "objective": "New outcome", "requirements": [{"id": "extra", "description": "New obligation"}]},
    ]:
        rejected = handler(TOOL["name"], {**request, "operation_id": "missing-origin-" + document["outcome_id"],
            "expected_provider_revision": before["provider_revision"], "document": document})
        assert rejected["ok"] is False
        assert cli("inspect")[1] == before


def test_native_capacity_detail_guides_new_intent_without_rewriting_envelope(conversation_check):
    _, cli, _, _, _, _, adapter = conversation_check
    handler = adapter.session.read_tool_handler
    request = outcome_request(adapter)
    request["document"]["requirements"] = [
        {"id": f"r{i}", "description": "保留证据😀" * 60} for i in range(32)
    ]
    created = handler(TOOL["name"], request)
    assert created["ok"], created
    original = cli("inspect")[1]
    review = {"action": "refine_outcome", "operation_id": "oversized-review",
        "expected_provider_revision": original["provider_revision"],
        "document": {"outcome_id": "tour", "review": [
            {"id": f"r{i}", "conclusion": "uncertain", "note": "n" * 512,
                "evidence_refs": ["e" * 500] * 8} for i in range(32)]}}
    rejected = handler(TOOL["name"], review)
    assert rejected["ok"] is False and rejected["error"] == "goal_acceptance_invalid_request", rejected
    assert "all outcomes in the Goal, not the source count" in rejected["error_detail"]
    assert "outcome review capacity exceeded" in rejected["error_detail"]
    assert "new operation id for changed intent" in rejected["next_action"]
    assert "保留证据" not in str(rejected)
    assert handler(TOOL["name"], review) == rejected
    assert cli("inspect")[1] == original
    compact = {**review, "document": {"outcome_id": "tour", "review": [
        {**item, "note": "Further validation needed", "evidence_refs": ["artifact:full-report"]}
        for item in review["document"]["review"]]}}
    assert handler(TOOL["name"], compact)["error"] == "collaboration_request_rejected"
    assert cli("inspect")[1] == original, "reserved native intent cannot be silently replaced"
    compact["operation_id"] = "compact-review"
    applied = handler(TOOL["name"], compact)
    assert applied["ok"] and applied["status"] == "applied", applied
    assert handler(TOOL["name"], compact)["status"] == "replayed"
    outcome = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert outcome["sources"] == original["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["sources"]
    assert [{"id": r["id"], "description": r["description"]} for r in outcome["requirements"]] == request["document"]["requirements"]
