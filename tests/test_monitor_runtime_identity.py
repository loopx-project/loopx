"""Changing a monitor's executable host must preserve its durable task actor."""
import json

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService, ProtectedActionGate
from loopx.todos import add_goal_todo, list_goal_todos


class Runtime:
    def __init__(self, healthy=True):
        self.healthy = healthy

    def capabilities(self):
        return [{"agent_id": "claude-code", "available": self.healthy,
                 "tool_calls": True, "trust_scope": "read_only"}]


def service(tmp_path, *, registered=("business-worker",), healthy=True):
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## User Todo\n\n## Agent Todo\n\n## Completed Work Archive\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": "goal-a", "repo": str(project), "state_file": state.name,
                   "coordination": {"registered_agents": list(registered)}}]}))
    todo = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Check the synthetic service", task_class="continuous_monitor", claimed_by="business-worker",
        monitor_metadata={"target_key": "fixture-service", "cadence": "4h", "watch_only": "true"})
    actions = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry,
                               runtime_controller=Runtime(healthy))
    return actions, registry, todo["todo_id"]


def preview(actions, todo_id, actor="business-worker"):
    return actions.preview({"action_kind": "monitor.update", "summary": "Check once",
        "context": {"kind": "schedule", "goal_id": "goal-a"}, "idempotency_key": "fixture-check",
        "normalized_parameters": {"goal_id": "goal-a", "todo_id": todo_id,
            "agent_id": actor, "endpoint_id": "claude-code", "operation": "run_now"}})


def test_explicit_registered_actor_survives_runtime_selection(tmp_path):
    actions, registry, todo_id = service(tmp_path)
    before = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    result = preview(actions, todo_id)
    assert result["normalized_parameters"]["agent_id"] == "business-worker"
    assert result["normalized_parameters"]["endpoint_id"] == "claude-code"
    assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"] == before
    assert json.loads(registry.read_text())["goals"][0]["coordination"]["registered_agents"] == ["business-worker"]


def test_unregistered_actor_still_requires_binding(tmp_path):
    actions, _, todo_id = service(tmp_path)
    with pytest.raises(ProtectedActionGate) as caught:
        preview(actions, todo_id, actor="unregistered")
    assert caught.value.gate["kind"] == "agent_binding_required"


def test_runtime_health_is_still_enforced(tmp_path):
    actions, _, todo_id = service(tmp_path, healthy=False)
    with pytest.raises(ValueError, match="not healthy"):
        preview(actions, todo_id)
