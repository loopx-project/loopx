"""Public update invariants: a partial edit must not invalidate an armed wait."""
import pytest

from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from loopx.todos import add_goal_todo, update_goal_todo
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID, GOAL_ID,
)
from tests.control_plane.canonical_authority_fixture import promoted_create_fixture


def waiting_goal(tmp_path):
    registry, runtime, state = promoted_create_fixture(
        tmp_path,
        goal_id=GOAL_ID,
        handoff_mode="soft_claim",
        registered_agents=[AGENT_ID],
    )
    # Initialize historical evidence through the create/import boundary;
    # ordinary configuration cannot manufacture an observation generation.
    monitor = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Observe fixture", task_class="continuous_monitor", claimed_by=AGENT_ID,
        monitor_metadata={"target_key": "fixture", "cadence": "1h", "watch_only": "true",
                          "material_change_generation": "3"})
    def add(text):
        return add_goal_todo(registry_path=registry, goal_id=GOAL_ID,
            role="agent", task_class="advancement_task", text=text, claimed_by=AGENT_ID)
    waiting, successor = add("Await observation"), add("Independent work")
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=waiting["todo_id"], agent_id=AGENT_ID,
        resume_when=f"monitor_changed:{monitor['todo_id']}",
        successor_todo_ids=[successor["todo_id"]])
    return registry, runtime, state, waiting, successor


def current_todo(runtime, todo_id):
    source = read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL_ID,
    )
    return next(todo for todo in source["todos"] if todo["todo_id"] == todo_id)


@pytest.mark.parametrize("edit", [{"successor_todo_ids": []}, {"status": "blocked"},
                                    {"task_class": "continuous_monitor"}])
def test_partial_edit_cannot_invalidate_retained_monitor_wait(tmp_path, edit):
    registry, _, state, waiting, _ = waiting_goal(tmp_path)
    before = state.read_bytes()
    with pytest.raises(
        (ValueError, LocalCoordinationAuthorityUnavailable),
        match="external-wait",
    ):
        update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
            todo_id=waiting["todo_id"], agent_id=AGENT_ID, **edit)
    assert state.read_bytes() == before


def test_clear_condition_and_successors_together_is_valid(tmp_path):
    registry, runtime, state, waiting, _ = waiting_goal(tmp_path)
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=waiting["todo_id"], agent_id=AGENT_ID,
        clear_resume_when=True, successor_todo_ids=[])
    result = current_todo(runtime, waiting["todo_id"])
    assert result.get("resume_when") is None
    assert result.get("resume_monitor_generation") is None
    assert result.get("successor_todo_ids", []) == []


def test_copy_edit_preserves_existing_wait_fence(tmp_path):
    registry, runtime, _, waiting, _ = waiting_goal(tmp_path)
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=waiting["todo_id"], agent_id=AGENT_ID, note="Clarify context")
    result = current_todo(runtime, waiting["todo_id"])
    assert result["resume_when"].startswith("monitor_changed:")
    assert result["resume_monitor_generation"] == 3
    assert "external_wait_transition" not in result


def test_real_cli_rejects_partial_wait_edit_and_supports_explicit_repair(tmp_path):
    registry, runtime, state, waiting, _ = waiting_goal(tmp_path)
    common = ("todo", "update", "--goal-id", GOAL_ID, "--todo-id", waiting["todo_id"],
              "--agent-id", AGENT_ID, "--status", "blocked")
    before = state.read_bytes()
    code, rejected = run_json_cli_result(*common, registry_path=registry, runtime_root=runtime)
    assert code != 0, rejected
    assert state.read_bytes() == before
    code, accepted = run_json_cli_result(*common, "--clear-resume-when", "--dry-run",
        registry_path=registry, runtime_root=runtime)
    assert code == 0, accepted
    assert state.read_bytes() == before


def test_public_update_commits_through_canonical_transaction(tmp_path, monkeypatch):
    from loopx.control_plane.todos import authoring_scope, provider_update, resume_condition

    registry, _, _, waiting, successor = waiting_goal(tmp_path)
    actual = provider_update.effect_runtime_result
    calls = []

    def traced(method, params, **kwargs):
        calls.append(method)
        return actual(method, params, **kwargs)

    for module in (provider_update, authoring_scope, resume_condition):
        monkeypatch.setattr(module, "effect_runtime_result", traced)
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=waiting["todo_id"], agent_id=AGENT_ID,
        successor_todo_ids=[successor["todo_id"]])
    result = current_todo(tmp_path / "runtime", waiting["todo_id"])
    assert result["resume_monitor_generation"] == 3
    assert calls.count("coordination.local_authority.todo_update") == 1
    assert "todo.public_update.plan" not in calls
