"""The App's needs-you drawer handles a User action through existing typed actions.

These are the exact payloads the drawer previews; the canonical Todo owner
decides the effects. Completing resumes only the work the action unblocks,
deferring keeps it waiting, and "no longer needed" closes only the reminder.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_todo_decision_scope_lifecycle import AGENT_ID, GOAL_ID, _write_fixture
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, list_goal_todos

PROVIDERS = [("legacy", "soft_claim"), ("file", "hard_lease"), ("sqlite", "soft_claim")]


def _goal(tmp_path: Path, monkeypatch, provider: str, handoff_mode: str):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _, state, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["common_runtime_root"] = str(tmp_path / "runtime")
    registry.write_text(json.dumps(config))
    target = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Continue once the owner has created the sessions.", status="blocked",
        task_class="advancement_task", claimed_by=AGENT_ID)
    action = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="user",
        text="Create the remaining role sessions in the desktop App.", task_class="user_action", bound_agent=AGENT_ID,
        unblocks_todo_id=target["todo_id"])
    if provider != "legacy":
        config["goals"][0]["coordination"]["handoff_mode"] = handoff_mode
        registry.write_text(json.dumps(config))
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, handoff_mode=handoff_mode,
            todos=list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"])
        initialize_canonical_authority(tmp_path / "runtime", GOAL_ID, projection,
                                       state_path=state, provider=provider)
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry)
    return registry, service, target["todo_id"], action["todo_id"]


def _rows(registry: Path) -> dict[str, dict]:
    return {row["todo_id"]: row for row in list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]}


def _apply(service: ChatActionService, action_kind: str, parameters: dict, key: str) -> dict:
    proposal = service.preview({"action_kind": action_kind, "summary": "Handle the request", "context": {},
        "normalized_parameters": {"goal_id": GOAL_ID, "agent_id": AGENT_ID, **parameters}, "idempotency_key": key})
    assert proposal["status"] == "preview_ready", proposal
    applied = service.apply(proposal["proposal_id"])["proposal"]
    assert applied["status"] == "applied", applied
    return applied["receipt"]


@pytest.mark.parametrize(("provider", "handoff_mode"), PROVIDERS)
def test_done_completes_the_action_and_resumes_its_dependent(tmp_path, monkeypatch, provider, handoff_mode):
    registry, service, target_id, action_id = _goal(tmp_path, monkeypatch, provider, handoff_mode)
    receipt = _apply(service, "todo.update", {"todo_id": action_id, "operation": "complete"}, "done")
    assert receipt["outcome"] == "todo_completed"
    rows = _rows(registry)
    assert rows[action_id]["status"] == "done"
    assert rows[target_id]["status"] == "open"


# Hard-lease Goals currently reject a planning edit of a User Todo at preview
# (no lease execution proof); the drawer surfaces that error without writing.
@pytest.mark.parametrize(("provider", "handoff_mode"), [p for p in PROVIDERS if p[1] != "hard_lease"])
def test_defer_keeps_the_action_and_its_dependent_waiting(tmp_path, monkeypatch, provider, handoff_mode):
    registry, service, target_id, action_id = _goal(tmp_path, monkeypatch, provider, handoff_mode)
    resume_when = "resume_at:2099-01-02T09:00:00+08:00"
    _apply(service, "todo.update", {"todo_id": action_id, "operation": "defer", "resume_when": resume_when}, "defer")
    rows = _rows(registry)
    # The owner stores the same instant normalized to UTC.
    assert (rows[action_id]["status"], rows[action_id]["resume_when"]) == ("deferred", "resume_at:2099-01-02T01:00:00Z")
    assert rows[target_id]["status"] == "blocked"


@pytest.mark.parametrize(("provider", "handoff_mode"), PROVIDERS)
def test_no_longer_needed_closes_only_the_reminder(tmp_path, monkeypatch, provider, handoff_mode):
    registry, service, target_id, action_id = _goal(tmp_path, monkeypatch, provider, handoff_mode)
    receipt = _apply(service, "gate.resolve", {"todo_id": action_id, "decision": "cancel",
                                               "note": "The owner closed this request as no longer needed."}, "cancel")
    assert (receipt["decision_outcome"], receipt["unblock_resume_state"]) == ("cancel", "decision_cancelled")
    rows = _rows(registry)
    assert rows[action_id]["status"] == "done"
    assert rows[target_id]["status"] == "blocked", "closing a reminder must not resume or approve other work"


@pytest.mark.parametrize("actor", ["codex", "codex-review"])
@pytest.mark.parametrize("operation", ["complete", "cancel"])
def test_wrong_actor_is_rejected_without_effects(tmp_path, monkeypatch, actor, operation):
    registry, service, target_id, action_id = _goal(tmp_path, monkeypatch, "file", "hard_lease")
    before = _rows(registry)
    parameters = {"goal_id": GOAL_ID, "agent_id": actor, "todo_id": action_id}
    if operation == "cancel":
        action_kind = "gate.resolve"
        parameters["decision"] = "cancel"
    else:
        action_kind = "todo.update"
        parameters["operation"] = "complete"
    with pytest.raises((ValueError, LocalCoordinationAuthorityUnavailable)):
        service.preview({"action_kind": action_kind, "summary": "Wrong actor", "context": {},
                         "normalized_parameters": parameters, "idempotency_key": "wrong-actor"})
    assert _rows(registry) == before
    assert before[action_id]["status"] == "open" and before[target_id]["status"] == "blocked"
