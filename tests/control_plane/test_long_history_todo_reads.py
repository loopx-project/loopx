"""Synthetic long-history shapes, without copying any live text or identities."""
from pathlib import Path

import pytest

from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos, parse_todo_source
from loopx.control_plane.todos.goal_todo_projection import todo_summaries_from_fields
from loopx.control_plane.todos.todo_summary import compact_evaluated_todo_group


@pytest.mark.parametrize("history_count", [0, 4096])
def test_public_list_preserves_completed_dependency_after_archive(tmp_path: Path, history_count: int) -> None:
    state, runtime, registry = tmp_path / "STATE.md", tmp_path / "runtime", tmp_path / "registry.json"
    source = "# Synthetic goal\n\n## Agent Todo\n\n- [x] Completed prerequisite\n" + (
        "  <!-- loopx:todo todo_id=todo_dependency status=done task_class=advancement_task -->\n"
        "- [ ] Deliver the successor\n"
        "  <!-- loopx:todo todo_id=todo_successor status=open task_class=advancement_task claimed_by=agent-a resume_when=todo_done:todo_dependency -->\n\n"
        "## Completed Work Archive\n\n"
    )
    source += "".join(f"- [x] Historical completion {i}\n  <!-- loopx:todo todo_id=todo_history_{i:05d} status=done task_class=advancement_task -->\n" for i in range(history_count))
    state.write_text(source, encoding="utf-8")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry, goal_id="goal-a",
        domain="long-history", adapter_kind="generic_project_goal_v0", state_file=str(state),
        registered_agents=["agent-a"], quota_allowed_slots=None)
    code, archived = run_json_cli_result("todo", "archive-completed", "--goal-id", "goal-a",
        "--role", "agent", "--max-active-done", "0", "--execute", registry_path=registry, runtime_root=runtime)
    assert code == 0, archived
    assert archived["moved_count"] == 1
    after = state.read_bytes()
    _, archived_rows, _ = parse_todo_source(after.decode())
    assert next(item for item in archived_rows if item["todo_id"] == "todo_dependency")["role"] == "agent"
    fields = parse_active_state_todos(after.decode(), item_limit=None)
    assert fields["agent_todos"]["items"][0]["resume_ready"] is True
    for limit in [None, 1]:
        projected = todo_summaries_from_fields(fields=fields, source="markdown_active_state",
             rollout_events=[], roles=["agent"], status="open", todo_id="todo_successor",
            agent_id="agent-a", limit=limit)
        assert projected.todos[0]["resume_ready"] is True
        if limit is None:
            assert projected.todos[0]["resume_condition"]["target_status"] == "done"
    code, result = run_json_cli_result("todo", "list", "--goal-id", "goal-a", "--role", "agent",
        "--todo-id", "todo_successor", "--agent-id", "agent-a", registry_path=registry, runtime_root=runtime)
    assert code == 0, result
    assert result["todos"][0]["resume_ready"] is True
    assert state.read_bytes() == after


@pytest.mark.parametrize("condition", [None, {},
    {"schema_version": "todo_resume_condition_v0", "resume_when": "todo_done:todo_dependency"},
    {"schema_version": "todo_resume_condition_v0", "resume_when": "todo_done:todo_dependency", "satisfied": "true"},
    {"schema_version": "todo_resume_condition_v0", "resume_when": "todo_done:todo_other", "satisfied": True}])
def test_display_cannot_bypass_full_source_resume_evaluation(condition: dict | None) -> None:
    with pytest.raises(ValueError, match="full-source resume evaluation"):
        compact_evaluated_todo_group([{"todo_id": "todo_waiting", "resume_when": "todo_done:todo_dependency",
            "resume_ready": True, "resume_condition": condition}], role="agent", source_section="Agent Todo")


def test_capture_preserves_archived_dependency_for_real_canonical_cli_read(tmp_path: Path) -> None:
    from loopx.history import load_registry
    from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
    from loopx.control_plane.coordination.local_authority_shadow_adapter import todo_partition_projector
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    state, runtime, registry = tmp_path / "STATE.md", tmp_path / "runtime", tmp_path / "registry.json"
    state.write_text("# Goal\n\n## Agent Todo\n\n- [ ] Waiting work\n"
        "  <!-- loopx:todo todo_id=todo_waiting status=open task_class=advancement_task resume_when=todo_done:todo_history -->\n\n"
        "## Completed Work Archive\n\n- [x] Historical prerequisite\n"
        "  <!-- loopx:todo todo_id=todo_history status=done task_class=advancement_task -->\n", encoding="utf-8")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry, goal_id="goal-a",
        domain="long-history", adapter_kind="generic_project_goal_v0", state_file=str(state),
        registered_agents=["agent-a"], quota_allowed_slots=None)
    original = state.read_bytes()
    goal = next(goal for goal in load_registry(registry)["goals"] if goal["id"] == "goal-a")
    projection, _ = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime,
        state_path=state, registry_path=registry)
    writer_projection = todo_partition_projector(goal, state_path=state)(state.read_text())
    assert writer_projection["todos"] == projection["todos"]
    assert state.read_bytes() == original
    records = {item["todo_id"]: item for item in projection["todos"]}
    assert records["todo_history"]["archive_state"] == "archive"
    assert records["todo_history"]["status"] == "done"
    assert records["todo_history"]["role"] == "agent"
    initialize_canonical_authority(runtime, "goal-a", projection, state_path=state)
    state.unlink()
    code, result = run_json_cli_result("todo", "list", "--goal-id", "goal-a", "--role", "agent",
        "--todo-id", "todo_waiting", registry_path=registry, runtime_root=runtime)
    assert code == 0, result
    assert result["todos"][0]["resume_ready"] is True
    code, completed = run_json_cli_result("todo", "list", "--goal-id", "goal-a", "--role", "agent",
        "--status", "done", registry_path=registry, runtime_root=runtime)
    assert code == 0, completed
    assert completed["todos"] == []
    assert not state.exists()
