"""External Todo previews use canonical creation and the original owner reply."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from test_chat_team_plan_action import GOAL_ID, _fixture, _todos
from test_native_steward_private import finish, steward  # noqa: F401


def prepare(components, tmp_path, monkeypatch, *, goal_id=GOAL_ID):
    store, runtime, provider, transport, binding, capture, _ = components
    project, registry, _ = _fixture(tmp_path / "canonical")
    payload = json.loads(registry.read_text())
    payload["common_runtime_root"] = str(store.root.parent)
    runtime.registry_path.write_text(json.dumps(payload))
    response = {"schema_version": "loopx_chat_agent_response_v0", "message": "待办预览；尚未写入。",
        "proposals": [{"kind": "todo", "goal_id": goal_id, "text": "Add reader comments later",
                       "priority": "P2", "rationale": "Record the request without starting implementation."}],
        "protected_action": None, "gate": None}
    monkeypatch.setattr("loopx.chat_runtime.CodexAppServerAdapter.start_turn", lambda *_args: response)
    transport.admit("steward-app", provider.event("steward-app", "record", "Record reader comments for later."))
    original = transport.core.pending()[0]
    assert finish(runtime, original)["status"] == "completed"
    transport.reconcile()
    return project, response, original


def test_original_owner_can_confirm_todo_once_without_creating_a_goal_or_execution(steward, tmp_path, monkeypatch):  # noqa: F811
    project, _, original = prepare(steward, tmp_path, monkeypatch)
    store, runtime, provider, transport, _, capture, _ = steward
    proposals = transport.core.actions.store.list()
    assert len(proposals) == 1
    preview = proposals[0]
    assert preview["action_kind"] == "todo.create" and preview["status"] == "preview_ready"
    assert "Add reader comments later" not in _todos(project)
    assert any(profile == "steward-app" and f"/confirm {preview['proposal_id']}" in text
               for profile, text in provider.writes)
    from loopx.chat_actions import ProtectedActionGate
    with pytest.raises(ProtectedActionGate):
        transport.core.actions.apply(preview["proposal_id"])
    sessions_before = len(store.list_sessions())
    confirm = provider.event("steward-app", "confirm", "/confirm " + preview["proposal_id"])
    transport.admit("steward-app", confirm)
    transport.reconcile()
    applied = transport.core.actions.load(preview["proposal_id"])
    assert applied["status"] == "applied", transport.core.pending()
    assert applied["receipt"]["resource_ids"]["goal_id"] == GOAL_ID
    assert _todos(project).count("Add reader comments later") == 1
    assert len(store.list_sessions()) == sessions_before
    assert len(json.loads(runtime.registry_path.read_text())["goals"]) == 1
    assert not capture.exists() or "thread/goal/set" not in capture.read_text()
    writes = list(provider.writes)
    transport.admit("steward-app", confirm)
    transport.reconcile()
    assert provider.writes == writes and _todos(project).count("Add reader comments later") == 1
    assert transport.core.read_request(original["request_ref"])["session_id"] == original["session_id"]


@pytest.mark.parametrize("goal_id", [None, "unknown-goal"])
def test_missing_or_ungranted_target_never_creates_an_action_or_todo(steward, tmp_path, monkeypatch, goal_id):  # noqa: F811
    project, _, _ = prepare(steward, tmp_path, monkeypatch, goal_id=goal_id)
    _, _, provider, transport, _, _, _ = steward
    assert transport.core.actions.store.list() == []
    assert "Add reader comments later" not in _todos(project)
    assert any("尚未写入" in text for _, text in provider.writes)
    assert not any("/confirm " in text for _, text in provider.writes)


@pytest.mark.parametrize("operation", ["cancel", "expired", "scope_removed", "stale"])
def test_confirmation_boundaries_leave_the_todo_unwritten(steward, tmp_path, monkeypatch, operation):  # noqa: F811
    project, _, _ = prepare(steward, tmp_path, monkeypatch)
    _, runtime, provider, transport, _, _, _ = steward
    proposal = transport.core.actions.store.list()[0]
    if operation == "expired":
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz or timezone.utc) + timedelta(minutes=16)
        monkeypatch.setattr("loopx.capabilities.native_chat.external_conversations.datetime", Later)
    elif operation == "scope_removed":
        payload = json.loads(runtime.registry_path.read_text())
        payload["goals"] = []
        runtime.registry_path.write_text(json.dumps(payload))
    elif operation == "stale":
        from loopx.todos import add_goal_todo
        add_goal_todo(registry_path=runtime.registry_path, goal_id=GOAL_ID, role="agent", text="Other independent task")
    command = "/cancel " if operation == "cancel" else "/confirm "
    transport.admit("steward-app", provider.event("steward-app", "boundary", command + proposal["proposal_id"]))
    transport.reconcile()
    assert "Add reader comments later" not in _todos(project)
    assert transport.core.actions.load(proposal["proposal_id"])["status"] != "applied"


def test_other_app_cannot_confirm_a_known_todo_preview(steward, tmp_path, monkeypatch):  # noqa: F811
    project, _, _ = prepare(steward, tmp_path, monkeypatch)
    _, runtime, provider, transport, _, _, _ = steward
    proposal = transport.core.actions.store.list()[0]
    command = "/confirm " + proposal["proposal_id"]
    transport.admit("notes-app", provider.event("notes-app", "other-app", command))
    other = next(row for row in transport.core.pending() if row["message"] == command)
    assert finish(runtime, other)["status"] == "completed"
    transport.reconcile()
    assert transport.core.actions.load(proposal["proposal_id"])["status"] == "preview_ready"
    assert "Add reader comments later" not in _todos(project)


def test_lost_preview_reply_reuses_the_original_preview_and_expiry(steward, tmp_path, monkeypatch):  # noqa: F811
    service = steward[3].core.actions
    real_preview = service.preview
    failed = []
    def lose_first_reply(request):
        proposal = real_preview(request)
        if proposal["action_kind"] == "todo.create" and not failed:
            failed.append(proposal)
            raise OSError("synthetic reply loss after preview persistence")
        return proposal
    monkeypatch.setattr(service, "preview", lose_first_reply)
    # The existing courier retains a failed reconcile; it must not complete an
    # error terminal and leave this known, idempotent preview unreachable.
    project, _, original = prepare(steward, tmp_path, monkeypatch)
    _, _, provider, transport, _, _, _ = steward
    transport.reconcile()
    proposals = transport.core.actions.store.list()
    assert len(proposals) == 1 and proposals[0]["proposal_id"] == failed[0]["proposal_id"]
    assert proposals[0]["context"]["expires_at"] == failed[0]["context"]["expires_at"]
    assert any(f"/confirm {proposals[0]['proposal_id']}" in text for _, text in provider.writes)
    assert "Add reader comments later" not in _todos(project)
    assert transport.core.read_request(original["request_ref"])["todo_confirmation_reply"]


def test_committed_todo_survives_lost_external_journal_reply_without_a_second_write(steward, tmp_path, monkeypatch):  # noqa: F811
    from loopx.capabilities.native_chat import external_conversations
    project, _, _ = prepare(steward, tmp_path, monkeypatch)
    _, _, provider, transport, _, _, _ = steward
    preview = transport.core.actions.store.list()[0]
    original_write = external_conversations._atomic_write_json
    lost = []
    def lose_committed_reply(path, value):
        if value.get("todo_receipt") and not lost:
            lost.append(value["todo_receipt"])
            raise OSError("synthetic external journal loss after canonical commit")
        return original_write(path, value)
    monkeypatch.setattr(external_conversations, "_atomic_write_json", lose_committed_reply)
    command = "/confirm " + preview["proposal_id"]
    transport.admit("steward-app", provider.event("steward-app", "confirmed-recovery", command))
    transport.reconcile()
    assert lost and _todos(project).count("Add reader comments later") == 1
    transport.reconcile()
    row = next(row for row in transport.core.pending() if row["message"] == command)
    assert row["status"] == "command_completed"
    assert row["todo_receipt"]["receipt_id"] == lost[0]["receipt_id"]
    assert _todos(project).count("Add reader comments later") == 1
    assert any("规范写入回执已读回" in text for _, text in provider.writes)
