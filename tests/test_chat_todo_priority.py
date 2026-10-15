"""Reviewed Chat priority edits carry structured intent through the real writer."""
import pytest
from test_chat_team_plan_action import _fixture, _todos, GOAL_ID


@pytest.mark.parametrize("clear", [False, True])
def test_chat_priority_preview_and_apply(tmp_path, clear):
    project, registry, service = _fixture(tmp_path)
    from loopx.todos import add_goal_todo
    todo_id = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Validate priority through Chat", priority="P2")["todo_id"]
    preview = service.preview({"action_kind": "todo.update", "summary": "Update task priority",
        "normalized_parameters": {"goal_id": GOAL_ID, "todo_id": todo_id, "operation": "edit",
            **({"clear_priority": True} if clear else {"priority": "P4"})},
        "context": {}, "idempotency_key": "priority-preview"})
    assert "[P2] Validate priority through Chat" in _todos(project)
    assert service.apply(preview["proposal_id"])["proposal"]["status"] == "applied"
    state = _todos(project)
    assert ("[P4] Validate priority through Chat" in state) is (not clear)
    assert "[P2] Validate priority through Chat" not in state
    assert "Validate priority through Chat" in state


def test_chat_create_keeps_priority_and_rejects_conflicting_marker(tmp_path):
    project, _, service = _fixture(tmp_path)
    request = {"action_kind": "todo.create", "summary": "Create task",
        "normalized_parameters": {"goal_id": GOAL_ID, "text": "Task with P0 prose", "priority": "P3"},
        "context": {}, "idempotency_key": "priority-create"}
    preview = service.preview(request)
    assert service.apply(preview["proposal_id"])["proposal"]["status"] == "applied"
    assert "[P3] Task with P0 prose" in _todos(project)
    with pytest.raises(ValueError, match="conflict"):
        service.preview({**request, "idempotency_key": "priority-conflict",
            "normalized_parameters": {"goal_id": GOAL_ID, "text": "[P1] Task", "priority": "P0"}})


def test_chat_create_defaults_without_overriding_reviewed_priority(tmp_path):
    project, _, service = _fixture(tmp_path)
    request = {"action_kind": "todo.create", "summary": "Create ordinary task",
        "normalized_parameters": {"goal_id": GOAL_ID, "text": "An ordinary task"},
        "context": {}, "idempotency_key": "priority-default"}
    preview = service.preview(request)
    assert service.apply(preview["proposal_id"])["proposal"]["status"] == "applied"
    assert "[P1] An ordinary task" in _todos(project)
