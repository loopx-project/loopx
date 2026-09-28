"""Exercise worker states through the public projection and real peer admission."""
from datetime import datetime, timedelta, timezone

import pytest

from loopx.control_plane.agents import management_projection as projection
from loopx.control_plane.quota.task_orchestration import apply_task_orchestration_contract

NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def build_projection(monkeypatch, *, age=None, binding=False, status="open",
                     task_class="advancement_task", has_todo=True, extra_todos=()):
    monkeypatch.setattr(projection, "now_utc", lambda: NOW)
    todo = {"todo_id": "todo_peer", "goal_id": "test-goal", "role": "agent",
            "claimed_by": "peer", "status": status, "task_class": task_class,
            "action_kind": "inspect", "text": "Inspect the public contract."}
    if age is not None:
        todo["updated_at"] = (NOW - timedelta(hours=age)).isoformat()
    todos = ([todo] if has_todo else []) + list(extra_todos)
    payload = {"goal_filter": "test-goal", "run_history": {"goals": [{
        "id": "test-goal", "coordination": {
            "registered_agents": ["peer"],
            "thread_agent_bindings": [{"agent_id": "peer", "thread_id": "thread-peer",
                                        "host_surface": "codex-app"}] if binding else [],
        }}]}, "todo_index": {"items": todos}}
    return projection.build_agent_management_projection(payload), todo


@pytest.mark.parametrize("kwargs,expected", [
    ({"has_todo": False}, "registered"),
    ({"has_todo": False, "binding": True}, "addressable"),
    ({"status": "done"}, "registered"),
    ({"status": "done", "binding": True}, "addressable"),
    ({}, "launchable"),
    ({"binding": True}, "bound"),
    ({"age": 1}, "executing"),
    ({"age": 8}, "executing"),
    ({"age": 8.01}, "launchable"),
    ({"age": 9, "binding": True}, "bound"),
    ({"age": -1}, "launchable"),
    ({"age": 48}, "launchable"),
    ({"status": "blocked", "age": 1, "binding": True}, "blocked"),
    ({"task_class": "blocker", "age": 1}, "blocked"),
    ({"task_class": "continuous_monitor", "age": 1}, "monitoring"),
    ({"task_class": "continuous_monitor", "age": 48}, "monitoring"),
    ({"status": "deferred", "age": 1}, "waiting"),
])
def test_projected_state(monkeypatch, kwargs, expected):
    packet, _ = build_projection(monkeypatch, **kwargs)
    row = packet["agents"][0]
    assert row["state"] == expected
    assert "lifecycle_state" not in row
    assert ("session_binding_candidates" in row) == kwargs.get("binding", False)
    assert packet["truth_contract"]["projection_is_writable"] is False


def test_unrelated_blocked_activity_does_not_change_current_work(monkeypatch):
    other = {"todo_id": "todo_blocked", "goal_id": "test-goal", "role": "agent",
             "claimed_by": "peer", "status": "blocked", "task_class": "blocker",
             "updated_at": NOW.isoformat()}
    packet, _ = build_projection(monkeypatch, extra_todos=[other])
    row = packet["agents"][0]
    assert row["current_todo"]["todo_id"] == "todo_peer"
    assert row["blocked_on"]["todo_id"] == "todo_blocked"
    assert row["state"] == "launchable"


@pytest.mark.parametrize("age,binding,capability,resume_ready,reason", [
    (1, True, True, True, None),
    (1, False, True, True, None),
    (9, True, True, True, None),
    (9, False, True, True, None),
    (48, True, True, True, "peer_runtime_stale"),
    (None, True, True, True, "peer_runtime_stale"),
    (1, True, False, True, "peer_agent_activation_unavailable"),
    (1, True, True, False, "peer_lane_not_resume_ready"),
])
def test_real_projection_to_peer_admission(monkeypatch, age, binding, capability,
                                           resume_ready, reason):
    packet, todo = build_projection(monkeypatch, age=age, binding=binding)
    todo.update(resume_when="todo_done:todo_dependency", resume_ready=resume_ready)
    summary = {"items": [todo]}
    contract, lane = apply_task_orchestration_contract(
        fallback_work_lane_contract={"lane": "advancement_task"},
        goal_boundary={"peer_task_coordination": {
            "enabled": True, "coordinator_agent_id": "coordinator"}},
        agent_identity={"agent_id": "coordinator", "registered_agents": ["coordinator", "peer"]},
        agent_todo_summary=summary, raw_agent_todo_summary=summary,
        available_capabilities=["peer_agent_activation"] if capability else [],
        agent_management_projection=packet,
    )
    assert contract is not None
    assert contract["execution_state"] == ("blocked" if reason else "ready")
    if reason:
        assert contract["eligible_peer_lanes"] == []
        assert contract["blocked_peer_lanes"][0]["reason_codes"] == [reason]
    else:
        assert contract["eligible_peer_lanes"][0]["todo_id"] == "todo_peer"
        assert lane["lane"] == "task_orchestration"
