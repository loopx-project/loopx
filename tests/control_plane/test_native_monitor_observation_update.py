"""Real Todo facade observation, immutable retry and lifecycle readback."""
from dataclasses import replace

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_native_monitor_poll import _canonical, GOAL_ID, AGENT_ID
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.monitor_metadata import MonitorPollObservation
from loopx.todos import update_goal_todo, complete_goal_todo


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native", [False, True])
def test_observation_completion_replay_and_same_hash_reactivation(tmp_path, monkeypatch, provider, native):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor = _canonical(tmp_path, native=native, provider=provider)
    state.unlink()
    arguments = dict(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
        todo_id=monitor["todo_id"], agent_id=AGENT_ID, role="agent")
    observation = MonitorPollObservation(generated_at="2030-01-01T00:00:00Z",
        result_hash="same-member-set", material_change=True, monitor_effect_id="observe-first", cadence="1h")
    first = update_goal_todo(**arguments, monitor_metadata=observation)
    assert first["status"] == "applied"
    assert first["monitor_poll_transition"]["material_change_generation"] == 1
    assert first["projection_delivery"] == "delivered"
    completed = complete_goal_todo(**arguments, no_followup=True)
    assert completed["changed"] is True
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    replay = update_goal_todo(**arguments, monitor_metadata=observation)
    assert replay["status"] == "replayed"
    assert replay["monitor_poll_transition"] == first["monitor_poll_transition"]
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == before
    revived = update_goal_todo(**arguments, status="open", no_followup=False,
        monitor_metadata=replace(observation, generated_at="2031-01-01T00:00:00Z", monitor_effect_id="observe-new-cycle"))
    assert revived["status"] == "applied"
    assert revived["monitor_poll_transition"]["material_change_generation"] == 2
    current = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    row = next(todo for todo in current["todos"] if todo["todo_id"] == monitor["todo_id"])
    assert row["status"] == "open" and row["done"] is False
    assert row["result_hash"] == "same-member-set"
    assert "completed_at" not in row and "completion_continuation" not in row
    assert revived["projection_delivery"] in {"delivered", "current"}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_observation_rejects_mixed_edits_and_unavailable_authority(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor = _canonical(tmp_path, provider=provider)
    arguments = dict(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
        todo_id=monitor["todo_id"], agent_id=AGENT_ID,
        monitor_metadata=MonitorPollObservation(generated_at="2030-01-01T00:00:00Z",
            result_hash="observed", material_change=True, monitor_effect_id="rejected-observation"))
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    display = state.read_bytes()
    for extra in ({"text": "Bundled copy"}, {"claimed_by": AGENT_ID}, {"status": "done"}):
        with pytest.raises((RuntimeError, ValueError)):
            update_goal_todo(**arguments, **extra)
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == before
        assert state.read_bytes() == display
