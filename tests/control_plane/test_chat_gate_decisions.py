"""Owner decisions recorded from the App use the canonical User completion owner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_todo_decision_scope_lifecycle import (
    AGENT_ID, GOAL_ID, PUBLISH_SCOPE, _add_target_and_gate, _write_fixture,
)
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService, ProtectedActionGate
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, list_goal_todos

RESUME_STATE = {"approve": "resumed", "reject": "decision_rejected", "cancel": "decision_cancelled"}


def _goal(tmp_path: Path, monkeypatch, provider: str, handoff_mode: str = "soft_claim"):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _, state, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["common_runtime_root"] = str(tmp_path / "runtime")
    registry.write_text(json.dumps(config))
    target, gate = _add_target_and_gate(registry, required_scopes=[PUBLISH_SCOPE], target_status="blocked")
    if provider != "legacy":
        config["goals"][0]["coordination"]["handoff_mode"] = handoff_mode
        registry.write_text(json.dumps(config))
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, handoff_mode=handoff_mode,
            todos=list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"])
        initialize_canonical_authority(tmp_path / "runtime", GOAL_ID, projection,
                                       state_path=state, provider=provider)
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry)
    return registry, service, target["todo_id"], gate["todo_id"]


def _preview(service: ChatActionService, todo_id: str, decision: str, key: str = "decide",
             agent_id: str | None = AGENT_ID) -> dict:
    parameters = {"goal_id": GOAL_ID, "todo_id": todo_id, "decision": decision}
    if agent_id:
        parameters["agent_id"] = agent_id
    return service.preview({"action_kind": "gate.resolve", "summary": "Decide the release request",
        "normalized_parameters": parameters, "context": {}, "idempotency_key": f"{key}-{decision}"})


def _rows(registry: Path) -> dict[str, dict]:
    return {row["todo_id"]: row for row in list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]}


@pytest.mark.parametrize(("provider", "handoff_mode"),
                         [("legacy", "soft_claim"), ("file", "hard_lease"), ("sqlite", "soft_claim")])
@pytest.mark.parametrize("decision", ["approve", "reject", "cancel"])
def test_app_decision_commits_through_user_completion(tmp_path, monkeypatch, provider, handoff_mode, decision):
    registry, service, target_id, gate_id = _goal(tmp_path, monkeypatch, provider, handoff_mode)
    proposal = _preview(service, gate_id, decision)
    assert proposal["status"] == "preview_ready"
    basis = proposal.get("canonical_update_basis")
    assert (basis or {}).get("schema_version") == (
        None if provider == "legacy" else "loopx_chat_canonical_terminal_basis_v0")
    assert _rows(registry)[gate_id]["status"] != "done", "preview must not write"

    applied = service.apply(proposal["proposal_id"])["proposal"]
    assert applied["status"] == "applied", applied
    receipt = applied["receipt"]
    assert (receipt["outcome"], receipt["decision_outcome"], receipt["unblock_resume_state"]) == (
        "gate_resolved", decision, RESUME_STATE[decision])
    rows = _rows(registry)
    assert rows[gate_id]["status"] == "done"
    assert rows[target_id]["status"] == ("open" if decision == "approve" else "blocked")
    assert rows[target_id]["claimed_by"] == AGENT_ID
    assert service.apply(proposal["proposal_id"])["proposal"]["receipt"] == receipt
    assert _rows(registry) == rows


@pytest.mark.parametrize("provider", ["legacy", "sqlite"])
def test_changed_goal_turns_decision_stale_without_writing(tmp_path, monkeypatch, provider):
    registry, service, target_id, gate_id = _goal(tmp_path, monkeypatch, provider)
    proposal = _preview(service, gate_id, "approve")
    add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
                  text="Unrelated concurrent work", claimed_by=AGENT_ID)
    result = service.apply(proposal["proposal_id"])["proposal"]
    assert result["status"] == "stale"
    assert result["receipt"] in (None, {})
    rows = _rows(registry)
    assert (rows[gate_id]["status"], rows[target_id]["status"]) == ("open", "blocked")
    regenerated = service.regenerate(proposal["proposal_id"])
    assert service.apply(regenerated["proposal_id"])["proposal"]["status"] == "applied"
    assert _rows(registry)[target_id]["status"] == "open"


def test_decision_preview_rejects_requests_that_carry_no_decision(tmp_path, monkeypatch):
    registry, service, target_id, _gate_id = _goal(tmp_path, monkeypatch, "sqlite")
    action = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="user",
        text="Read the observation", task_class="user_action", bound_agent=AGENT_ID,
        unblocks_todo_id=target_id)
    with pytest.raises(ValueError, match="decision_outcome is only valid"):
        _preview(service, action["todo_id"], "approve")
    with pytest.raises(ValueError):
        _preview(service, "todo_missing_request", "approve")
    assert service.store.list(goal_id=GOAL_ID) == []


def test_hard_lease_decision_requires_an_attributed_agent(tmp_path, monkeypatch):
    registry, service, target_id, gate_id = _goal(tmp_path, monkeypatch, "file", "hard_lease")
    with pytest.raises(Exception, match="handoff_mode_requires_lease"):
        _preview(service, gate_id, "approve", agent_id=None)
    with pytest.raises(Exception, match="handoff_mode_requires_lease"):
        _preview(service, gate_id, "approve", key="stranger", agent_id="codex-unregistered")
    rows = _rows(registry)
    assert (rows[gate_id]["status"], rows[target_id]["status"]) == ("open", "blocked")


def test_defer_and_pre_upgrade_proposals_never_record_a_decision(tmp_path, monkeypatch):
    registry, service, target_id, gate_id = _goal(tmp_path, monkeypatch, "sqlite")
    deferred = _preview(service, gate_id, "defer")
    with pytest.raises(ProtectedActionGate) as held:
        service.apply(deferred["proposal_id"])
    assert held.value.gate["kind"] == "decision_outcome_required"
    # A proposal stored before this contract carried no canonical basis.
    legacy = service.store.create_preview(
        action_kind="gate.resolve", summary="Older decision", context={},
        normalized_parameters={"goal_id": GOAL_ID, "todo_id": gate_id, "decision": "approve"},
        expected_state_fingerprint=service._registry_fingerprint(), permission_classification="durable_write",
        validation_evidence=["older"], available_transitions=["apply", "cancel"], idempotency_key="older")
    assert service.apply(legacy["proposal_id"])["proposal"]["status"] == "stale"
    rows = _rows(registry)
    assert (rows[gate_id]["status"], rows[target_id]["status"]) == ("open", "blocked")


def test_packaged_chat_http_records_decision(tmp_path, monkeypatch):
    from http.client import HTTPConnection
    from threading import Thread
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir

    registry, service, target_id, gate_id = _goal(tmp_path, monkeypatch, "sqlite")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.assets_dir = default_chat_assets_dir()
    server.action_store = service.store
    server.action_service = service
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=45)
    headers = {"Content-Type": "application/json"}
    try:
        body = {"action_kind": "gate.resolve", "summary": "Approve the release", "context": {},
                "idempotency_key": "http-decision",
                "normalized_parameters": {"goal_id": GOAL_ID, "todo_id": gate_id, "decision": "approve"}}
        connection.request("POST", "/api/actions/preview", body=json.dumps(body), headers=headers)
        response = connection.getresponse()
        preview = json.loads(response.read())
        assert response.status == 201, preview
        proposal_id = preview["proposal"]["proposal_id"]
        connection.request("POST", f"/api/actions/{proposal_id}/apply", body="{}", headers=headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200, result
        assert result["proposal"]["receipt"]["unblock_resume_state"] == "resumed"
        assert _rows(registry)[target_id]["status"] == "open"
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
