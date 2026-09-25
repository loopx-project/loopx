"""Retirement never loses an event-owned Todo or executes its validation."""
import json
import pytest

from loopx.control_plane.goals.legacy_event_source import (
    RetiredTodoEventSourceError, require_no_legacy_todo_events,
)
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.control_plane.todos import completion_validation
from loopx.todos import complete_goal_todo

@pytest.mark.parametrize("alias", [None, "state_event_log", "state_events_file", "event_log"])
@pytest.mark.parametrize("contents", ["{broken\n", '{"event_type":"todo_added"}\n'])
def test_all_old_source_selectors_refuse_reads_and_writes_without_data_loss(tmp_path, alias, contents):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\n---\n\n## Agent Todo\n\n- [ ] [P1] Materialized work\n"
        "  <!-- loopx:todo todo_id=todo_existing status=open task_class=advancement_task -->\n")
    old = tmp_path / ("aliased.jsonl" if alias else "events.jsonl")
    old.write_text(contents)
    goal = {"id": "retired-fixture", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"agent_model": "peer_v1", "registered_agents": ["agent-a"]}}
    if alias:
        goal[alias] = old.name
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(tmp_path / "runtime"), "goals": [goal]}))
    before = state.read_bytes(), old.read_bytes()
    for args in [("todo", "list"),
        ("shared-goal-alignment", "--agent-id", "agent-a", "--project", str(tmp_path)), ("todo", "add", "--text", "New work", "--role", "agent"),
        ("todo", "complete", "--todo-id", "todo_existing", "--evidence", "validated")]:
        code, result = run_json_cli_result(*args, "--goal-id", goal["id"], registry_path=registry)
        assert code != 0 or result.get("ok") is False, result
        assert "legacy_todo_event_source_retired" in json.dumps(result), result
        assert (state.read_bytes(), old.read_bytes()) == before


def test_completion_rejects_before_any_validation_effect(tmp_path, monkeypatch):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("## Agent Todo\n- [ ] [P1] Work\n"
        "  <!-- loopx:todo todo_id=todo_existing status=open task_class=advancement_task -->\n")
    state.with_name("events.jsonl").write_text("retired\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "retired-fixture", "repo": str(tmp_path), "state_file": state.name}]}))
    def forbidden(**kwargs):
        raise AssertionError("a retired source executed a completion effect")
    monkeypatch.setattr(completion_validation, "run_declared_completion_validation_effect", forbidden)
    with pytest.raises(RetiredTodoEventSourceError):
        complete_goal_todo(registry_path=registry, goal_id="retired-fixture", todo_id="todo_existing",
            evidence="validated", no_followup=True)


def test_empty_file_is_not_an_event_authority_and_is_preserved(tmp_path):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    old = state.with_name("events.jsonl")
    old.touch()
    require_no_legacy_todo_events({}, state_path=state)
    assert old.exists() and old.read_bytes() == b""
