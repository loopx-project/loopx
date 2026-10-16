"""Reviewed edits exercise the real Chat service and canonical providers."""
from __future__ import annotations

from pathlib import Path
import json
from datetime import datetime, timezone

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.todos import update_goal_todo
from loopx.control_plane.todos import provider_projection
from test_native_todo_planning_update import fixture, records, update


def service_fixture(tmp_path: Path, provider: str = "file"):
    registry, state = fixture(tmp_path, provider)
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"), registry_path=registry,
    )
    return registry, state, service


def preview(service: ChatActionService, **parameters):
    return service.preview({
        "action_kind": "todo.update", "summary": "Review a task edit",
        "normalized_parameters": {
            "goal_id": "goal-a", "todo_id": "todo_target",
            "agent_id": "agent-a", "text": "Reviewed task", **parameters,
        },
        "context": {}, "idempotency_key": "reviewed-edit",
    })


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_reviewed_edit_uses_canonical_authority_without_display(tmp_path: Path, provider: str):
    registry, state, service = service_fixture(tmp_path, provider)
    before = records(registry)
    proposal = preview(service)
    assert not state.exists()
    assert records(registry) == before
    applied = service.apply(proposal["proposal_id"])["proposal"]
    assert applied["status"] == "applied"
    assert applied["receipt"]["outcome"] == "todo_updated"
    assert records(registry)["todo_target"]["text"] == "Reviewed task"
    assert records(registry)["todo_other"] == before["todo_other"]
    assert service.apply(proposal["proposal_id"])["proposal"] == applied


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_review_freshness_ignores_display_but_fences_canonical_changes(tmp_path, provider):
    registry, state, service = service_fixture(tmp_path, provider)
    proposal = preview(service)
    # A display-only/narrative change cannot invalidate authority admission.
    state.write_text("# Display\n\nOwner narrative.\n\n## Agent Todo\n")
    assert service.apply(proposal["proposal_id"])["proposal"]["status"] == "applied"
    other = service.preview({
        "action_kind": "todo.update", "summary": "Review a second edit",
        "normalized_parameters": {"goal_id": "goal-a", "todo_id": "todo_target",
                                  "agent_id": "agent-a", "text": "Must become stale"},
        "context": {}, "idempotency_key": "second-edit",
    })
    old_display = state.read_bytes()
    update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_other",
                     agent_id="agent-b", text="Concurrent edit")
    state.write_bytes(old_display)  # Markdown alone still looks fresh.
    before = records(registry)
    assert service.apply(other["proposal_id"])["proposal"]["status"] == "stale"
    assert records(registry) == before


@pytest.mark.parametrize("failure_boundary", ["action_receipt", "display"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_retry_recovers_history_and_projects_current_state(tmp_path, monkeypatch, provider, failure_boundary):
    registry, state, service = service_fixture(tmp_path, provider)
    proposal = preview(service)
    with monkeypatch.context() as patch:
        if failure_boundary == "action_receipt":
            def lost_response(*args, **kwargs):
                raise ConnectionError("Simulated action receipt loss")
            patch.setattr(service.store, "apply", lost_response)
            with pytest.raises(ConnectionError):
                service.apply(proposal["proposal_id"])
        else:
            def unavailable_display(**kwargs):
                raise OSError("Simulated display failure")
            patch.setattr(provider_projection, "project_current_canonical_todos", unavailable_display)
            failed = service.apply(proposal["proposal_id"])["proposal"]
            assert failed["status"] == "failed"
            assert failed["receipt"] is None
            assert failed["failure"]["error_code"] == "canonical_update_projection_pending"
    assert records(registry)["todo_target"]["text"] == "Reviewed task"
    update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_target",
                     agent_id="agent-a", text="Later accepted work")
    # Historical recovery is still valid after registration changes; it must
    # not admit a fresh write under the old review basis.
    registration = json.loads(registry.read_text())
    registration["goals"][0]["coordination"]["registered_agents"].append("agent-c")
    registry.write_text(json.dumps(registration))
    state.unlink()
    before = records(registry)
    recovered = service.apply(proposal["proposal_id"])["proposal"]
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["outcome"] == "todo_updated"
    assert recovered["receipt"]["canonical_status"] == "replayed"
    assert recovered["receipt"]["projection_verified"] is True
    assert records(registry) == before
    assert "Later accepted work" in state.read_text()
    assert "Reviewed task" not in state.read_text()


def test_registry_change_invalidates_uncommitted_review(tmp_path):
    registry, _state, service = service_fixture(tmp_path)
    proposal = preview(service)
    registration = json.loads(registry.read_text())
    registration["goals"][0]["coordination"]["registered_agents"].append("agent-c")
    registry.write_text(json.dumps(registration))
    before = records(registry)
    assert service.apply(proposal["proposal_id"])["proposal"]["status"] == "stale"
    assert records(registry) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_reviewed_chat_block_uses_the_narrow_lifecycle_intent(tmp_path, provider):
    registry, state, service = service_fixture(tmp_path, provider)
    request = {"action_kind": "todo.update", "summary": "Pause research",
               "normalized_parameters": {"goal_id": "goal-a", "todo_id": "todo_target",
                                         "agent_id": "agent-a", "operation": "block",
                                         "note": "Owner paused this research lane"},
               "context": {}, "idempotency_key": "reviewed-pause"}
    before = records(registry)
    proposal = service.preview(request)
    assert records(registry) == before and not state.exists()
    applied = service.apply(proposal["proposal_id"])["proposal"]
    assert applied["status"] == "applied"
    todo = records(registry)["todo_target"]
    assert todo["status"] == "blocked"
    assert todo["reason"] == "Owner paused this research lane"
    assert todo.get("resume_when") is None
    assert todo["claimed_by"] == "agent-a"
    assert service.apply(proposal["proposal_id"])["proposal"] == applied


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_reason_is_evidence_not_a_grant_and_cas_is_recoverable(tmp_path, provider):
    registry, _state, service = service_fixture(tmp_path, provider)
    before = records(registry)
    with pytest.raises(RuntimeError):
        preview(service, agent_id="agent-b")
    assert records(registry) == before
    basis = service._canonical_update_basis("goal-a")
    args = ["--text", "Reviewed CLI edit", "--authority-reason", "Owner reviewed the edit",
            "--update-operation-id", "reviewed-cli", "--update-expected-provider-revision", basis["provider_revision"]]
    assert update(registry, *args)["status"] == "applied"
    assert update(registry, *args)["status"] == "replayed"
    failed = update(registry, "--text", "Stale attempt", "--update-expected-provider-revision",
                    basis["provider_revision"], ok=False)
    assert "revision" in json.dumps(failed)
    assert records(registry)["todo_target"]["text"] == "Reviewed CLI edit"


@pytest.mark.parametrize("operation", ["pause", "resume", "edit"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_monitor_preview_and_apply_share_the_real_update(tmp_path, operation, provider):
    registry, state, service = service_fixture(tmp_path, provider)
    update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_target", agent_id="agent-a",
                     task_class="continuous_monitor", monitor_metadata={"cadence": "1h", "watch_only": "true"})
    state.unlink()
    parameters = {"goal_id": "goal-a", "todo_id": "todo_target", "agent_id": "agent-a",
                  "operation": operation, **({"cadence": "2h"} if operation == "edit" else {})}
    request = {"action_kind": "monitor.update", "summary": "Review monitor configuration",
               "normalized_parameters": parameters, "context": {}, "idempotency_key": "monitor-review"}
    before = records(registry)
    proposal = service.preview(request)
    assert records(registry) == before and not state.exists()
    applied = service.apply(proposal["proposal_id"])["proposal"]
    assert applied["status"] == "applied"
    current = records(registry)["todo_target"]
    assert current["status"] == ("blocked" if operation == "pause" else "open")
    assert current["cadence"] == ("2h" if operation == "edit" else "1h")
    assert records(registry)["todo_other"] == before["todo_other"]
    with pytest.raises(RuntimeError):
        service.preview({**request, "idempotency_key": "wrong-owner",
                         "normalized_parameters": {**parameters, "agent_id": "agent-b"}})


def test_update_basis_cannot_be_silently_discarded_by_claim(tmp_path):
    registry, _state = fixture(tmp_path)
    before = records(registry)
    with pytest.raises(ValueError, match="cannot be used for todo claim"):
        update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_target",
                         agent_id="agent-a", claimed_by="agent-a", claim_only=True,
                         update_expected_provider_revision="stale-basis")
    assert records(registry) == before


def test_monitor_retry_keeps_intent_when_clock_moves(tmp_path, monkeypatch):
    registry, _state, service = service_fixture(tmp_path)
    update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_target", agent_id="agent-a",
                     task_class="continuous_monitor", monitor_metadata={"cadence": "1h", "watch_only": "true"})
    proposal = service.preview({
        "action_kind": "monitor.update", "summary": "Change monitor cadence",
        "normalized_parameters": {"goal_id": "goal-a", "todo_id": "todo_target",
                                  "agent_id": "agent-a", "operation": "edit", "cadence": "2h"},
        "context": {}, "idempotency_key": "monitor-clock",
    })
    with monkeypatch.context() as patch:
        patch.setattr(provider_projection, "project_current_canonical_todos",
                      lambda **kwargs: (_ for _ in ()).throw(OSError("display unavailable")))
        assert service.apply(proposal["proposal_id"])["proposal"]["status"] == "failed"
    before = records(registry)
    monkeypatch.setattr("loopx.chat_actions.now_utc", lambda: datetime(2040, 1, 1, tzinfo=timezone.utc))
    recovered = service.apply(proposal["proposal_id"])["proposal"]
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["canonical_status"] == "replayed"
    assert records(registry) == before
    assert records(registry)["todo_target"]["next_due_at"] == before["todo_target"]["next_due_at"]
