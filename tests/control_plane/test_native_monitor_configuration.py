"""Monitor configuration uses the public writer and actual local providers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from canonical_authority_fixture import isolate_sqlite_runtime
from test_native_monitor_poll import _canonical
from test_monitor_followthrough_contract import _write_fixture, _add_monitor, GOAL_ID, AGENT_ID
from loopx.control_plane.scheduler.monitor_poll_writeback import write_monitor_poll_todo_state
from loopx.control_plane.testing.canary_harness import run_json_cli
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.todos import add_goal_todo, list_goal_todos, update_goal_todo


OBSERVED_AT = "2030-01-01T00:00:00Z"
OBSERVATION_FIELDS = ("result_hash", "last_checked_at", "monitor_effect_id",
                      "material_change_generation", "consecutive_no_change")


def setup(tmp_path, provider):
    if provider == "legacy":
        registry, runtime, state = _write_fixture(tmp_path)
        monitor = _add_monitor(registry, text="Observe public changes", target_key="public-watch", next_due_at="2000-01-01T00:00:00Z")
        return registry, runtime, state, monitor
    return _canonical(tmp_path, provider=provider)


def _row(registry, todo_id):
    todo = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, todo_id=todo_id)["todo"]
    assert isinstance(todo, dict), todo
    return todo


def _observe(registry, runtime, monitor, result_hash="observed-before-configuration"):
    receipt = write_monitor_poll_todo_state(registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, execute=True, todo_id=monitor["todo_id"], agent_id=AGENT_ID,
        monitor_effect_id="configuration-observation", generated_at=OBSERVED_AT,
        result_hash=result_hash, material_change=True)
    assert receipt is not None
    return receipt


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_configuration_cli_and_clear_preserve_observation(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor = setup(tmp_path, provider)
    # Preserving an observation is only meaningful once one exists: write a real
    # poll result first, then edit configuration and compare those fields.
    _observe(registry, runtime, monitor)
    before = _row(registry, monitor["todo_id"])
    assert before["result_hash"] == "observed-before-configuration"
    assert before["monitor_effect_id"] == "configuration-observation"
    assert before["last_checked_at"]
    assert int(before["material_change_generation"]) >= 1
    if provider != "legacy":
        state.unlink()
    args = ("todo", "update", "--goal-id", GOAL_ID, "--todo-id", monitor["todo_id"],
            "--agent-id", AGENT_ID, "--cadence", "2h", "--next-due-at", "2099-01-01T02:00:00Z")
    identity = () if provider == "legacy" else ("--update-operation-id", "configuration-a")
    run_json_cli(*args, *identity, "--dry-run", registry_path=registry, runtime_root=runtime)
    if provider != "legacy":
        assert not state.exists()
    result = run_json_cli(*args, *identity, registry_path=registry, runtime_root=runtime)
    current = _row(registry, monitor["todo_id"])
    assert current["cadence"] == "2h"
    assert current["next_due_at"] == "2099-01-01T02:00:00Z"
    for field in OBSERVATION_FIELDS:
        assert current.get(field) == before.get(field), field
    if provider != "legacy":
        assert result["source_authority"] == ("file_v0" if provider == "file" else "sqlite_v0")
        replay = run_json_cli(*args, *identity, registry_path=registry, runtime_root=runtime)
        assert replay["status"] == "replayed"
    # Clearing watch_only needs another retained bound, and an expiry-only edit
    # must keep the due time the explicit cadence edit pinned.
    update_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
        todo_id=monitor["todo_id"], agent_id=AGENT_ID,
        monitor_metadata={"watch_only": None, "expires_at": "2099-02-01T00:00:00Z"})
    expiry_only = _row(registry, monitor["todo_id"])
    assert not expiry_only.get("watch_only")
    assert expiry_only["expires_at"] == "2099-02-01T00:00:00Z"
    assert expiry_only["next_due_at"] == "2099-01-01T02:00:00Z"
    # A cadence-only edit keeps the legacy schedule contract: the due time is
    # derived from the edit timestamp, not from the pinned schedule above.
    started = datetime.now(timezone.utc)
    update_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
        todo_id=monitor["todo_id"], agent_id=AGENT_ID, monitor_metadata={"cadence": "3h"})
    recadenced = _row(registry, monitor["todo_id"])
    assert recadenced["cadence"] == "3h"
    due = datetime.fromisoformat(str(recadenced["next_due_at"]).replace("Z", "+00:00"))
    assert started + timedelta(hours=2, minutes=59) <= due
    assert due <= datetime.now(timezone.utc) + timedelta(hours=3, minutes=1)
    for field in OBSERVATION_FIELDS:
        assert recadenced.get(field) == before.get(field), field


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_route_identity_target_key_is_not_schedule_configuration(tmp_path, monkeypatch, provider):
    """A Monitor successor keeps its routing target without becoming a Monitor."""
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = setup(tmp_path, provider)
    successor = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Validate the observed change", task_class="advancement_task",
        action_kind="validate", claimed_by=AGENT_ID, unblocks_todo_id=monitor["todo_id"],
        monitor_metadata={"target_key": "public-pr:42:successor"})
    before = _row(registry, successor["todo_id"])
    assert before["task_class"] == "advancement_task"
    assert before["target_key"] == "public-pr:42:successor"
    for metadata in ({"cadence": "1h"}, {"next_due_at": "2099-01-01T00:00:00Z"},
                     {"expires_at": "2099-01-01T00:00:00Z"}, {"watch_only": "true"}):
        with pytest.raises((ValueError, RuntimeError)):
            update_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
                todo_id=successor["todo_id"], agent_id=AGENT_ID, monitor_metadata=metadata)
        assert _row(registry, successor["todo_id"]) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_configuration_rejection_and_delivery_recovery(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor = setup(tmp_path, provider)
    args = dict(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
                todo_id=monitor["todo_id"], agent_id=AGENT_ID)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    for metadata in ({"material_change_generation": 100}, {"cadence": "never"}, {"watch_only": None}, {"unknown": "x"}):
        with pytest.raises((ValueError, RuntimeError)):
            update_goal_todo(**args, text="Must not partially commit", monitor_metadata=metadata)
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == before
    import loopx.control_plane.todos.provider_projection as delivery
    def unavailable(**kwargs):
        raise OSError("synthetic delivery outage")
    with monkeypatch.context() as m:
        m.setattr(delivery, "project_current_canonical_todos", unavailable)
        result = update_goal_todo(**args, monitor_metadata={"cadence": "3h"}, update_operation_id="recover-config")
        assert result["projection_delivery"] == "pending"
    replay = update_goal_todo(**args, monitor_metadata={"cadence": "3h"}, update_operation_id="recover-config")
    assert replay["status"] == "replayed"
    assert replay["projection_delivery"] == "delivered"


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_observed_target_cannot_be_repurposed_by_configuration(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = setup(tmp_path, provider)
    _observe(registry, runtime, monitor, result_hash="original-target-result")
    before = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
    for metadata in ({"target_key": "another-target"}, {"target_key": None}, {"result_hash": "invented"}):
        with pytest.raises((ValueError, RuntimeError)):
            update_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
                todo_id=monitor["todo_id"], agent_id=AGENT_ID, monitor_metadata=metadata)
        assert list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"] == before
