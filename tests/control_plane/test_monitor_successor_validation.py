"""Monitor routing errors must be rejected before any observation writeback."""
import pytest

from test_monitor_followthrough_contract import _write_fixture, _add_monitor, GOAL_ID, AGENT_ID
from loopx.control_plane.scheduler.monitor_poll_writeback import write_monitor_poll_todo_state
from loopx.control_plane.testing.canary_harness import run_json_cli


@pytest.mark.parametrize("invalid", [
    {"next_required_capabilities": ["filesystem-write", "not/a/capability"]},
    {"next_claimed_by": "invalid/actor"},
    {"material_change": False},
    {"next_agent_priority": "P5"},
    {"next_agent_priority": "P0", "next_agent_todo": "[P1] Conflicting task"},
    {"next_user_priority": "P0"},
])
def test_invalid_successor_intent_does_not_partially_update_monitor(tmp_path, invalid):
    registry, runtime, state = _write_fixture(tmp_path)
    monitor = _add_monitor(registry, text="Watch a public target.", target_key="public-target")
    before = state.read_bytes()
    with pytest.raises(ValueError):
        write_monitor_poll_todo_state(registry_path=registry, runtime_root=runtime,
            goal_id=GOAL_ID, todo_id=monitor["todo_id"], execute=True,
            generated_at="2026-09-09T12:00:00Z", result_hash="revision-a", agent_id=AGENT_ID,
            next_action_kind="validate",
            **{"material_change": True, "next_agent_todo": "Validate the material change.", **invalid})
    assert state.read_bytes() == before


def test_public_monitor_poll_accepts_repository_and_action_aliases(tmp_path):
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(registry, text="Watch a public target.", target_key="public-target",
        next_due_at="2000-01-01T00:00:00Z")
    result = run_json_cli("quota", "monitor-poll", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--runtime-profile", "generic_cli",
        "--todo-id", monitor["todo_id"], "--result-hash", "revision-a", "--material-change",
        "--next-agent-todo", "Validate the transition.", "--next-action-kind", "VALIDATE",
        "--next-task-repository", "https://github.com/example/repo.git",
        "--next-required-capability", "file--write", "--execute",
        registry_path=registry, runtime_root=runtime)
    successor = result["todo_writeback"]["next_todos"][0]
    assert successor["action_kind"] == "validate"
    assert successor["task_repository"] == "git:github.com/example/repo"
    assert successor["required_capabilities"] == ["file__write"]
    assert successor["task_class"] == "advancement_task"
    assert successor["unblocks_todo_id"] == monitor["todo_id"]


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("explicit", [False, True])
def test_monitor_priority_cli_and_readback(tmp_path, monkeypatch, provider, explicit):
    from canonical_authority_fixture import isolate_sqlite_runtime
    from test_native_monitor_poll import _canonical
    from loopx.todos import update_goal_todo, list_goal_todos

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    if provider == "legacy":
        registry, runtime, _ = _write_fixture(tmp_path)
        monitor = _add_monitor(registry, text="Watch ordinary changes", target_key="public-target",
            next_due_at="2000-01-01T00:00:00Z")
    else:
        registry, runtime, _, monitor = _canonical(tmp_path, provider=provider)
    update_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
        todo_id=monitor["todo_id"], agent_id=AGENT_ID, priority="P0")
    flags = ["--next-agent-priority", "P2", "--next-user-priority", "P3"] if explicit else []
    result = run_json_cli("quota", "monitor-poll", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--runtime-profile", "generic_cli",
        "--todo-id", monitor["todo_id"], "--result-hash", "priority-change", "--material-change",
        "--next-agent-todo", "Validate the transition", "--next-action-kind", "validate",
        "--next-user-todo", "Review the result", "--next-user-task-class", "user_action",
        *flags, "--execute", registry_path=registry, runtime_root=runtime)
    writeback = result["todo_writeback"]
    expected = {"agent": "P2", "user": "P3"} if explicit else {"agent": "P1", "user": "P1"}
    for created, receipt in zip(writeback["next_todos"], writeback["successor_receipts"], strict=True):
        priority = expected[created["role"]]
        assert created["priority"] == receipt["priority"] == priority
        current = list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime),
            goal_id=GOAL_ID, role=created["role"])["todos"]
        assert next(row for row in current if row["todo_id"] == created["todo_id"])["priority"] == priority


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_monitor_priority_retry_keeps_original_intent(tmp_path, monkeypatch, provider):
    from canonical_authority_fixture import isolate_sqlite_runtime
    from test_native_monitor_poll import _canonical

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    if provider == "legacy":
        registry, runtime, _ = _write_fixture(tmp_path)
        monitor = _add_monitor(registry, text="Watch changes", target_key="public-target")
    else:
        registry, runtime, _, monitor = _canonical(tmp_path, provider=provider)
    args = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
        todo_id=monitor["todo_id"], agent_id=AGENT_ID, execute=True,
        generated_at="2026-09-09T12:00:00Z", result_hash="priority-a", material_change=True,
        monitor_effect_id="priority-once", next_agent_todo="Repair transition",
        next_action_kind="repair", next_agent_priority="P0")
    first = write_monitor_poll_todo_state(**args)
    replay = write_monitor_poll_todo_state(**args)
    assert replay["provider_replayed"] is True
    fields = ("todo_id", "role", "todo", "priority", "action_kind", "target_key")
    assert [{k: row.get(k) for k in fields} for row in replay["next_todos"]] == [
        {k: row.get(k) for k in fields} for row in first["next_todos"]]
    assert first["next_todos"][0]["priority"] == "P0"
    with pytest.raises((ValueError, RuntimeError), match="identity|mismatch|different coordination request"):
        write_monitor_poll_todo_state(**{**args, "next_agent_priority": "P4"})
