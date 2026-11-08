"""The adapter snapshots compatibility facts; one native batch owns routing."""
from copy import deepcopy

import pytest

from loopx.control_plane.work_items import action_portfolio as adapter
from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item
from loopx.quota import build_quota_should_run


def _context(**kwargs):
    return adapter.capture_goal_route_context(**{
        "agent_identity": {"agent_id": "route-agent"}, "agent_todo_summary": {},
        "agent_lane_next_action": None, "agent_scope_frontier": None,
        "agent_lane_frontier_hint": None, "active_state_next_action": "Durable step",
        "latest_run_recommended_action": None, "selected_recommended_action": "Durable step",
        "goal_route_hint_override": None, **kwargs,
    })


def _packet(**kwargs):
    return adapter.build_quota_planning_packet(**{
        "projection_enabled": False, "include_detail": False, "goal_id": "route-fixture",
        "selected": None, "agent_id": "route-agent", "agent_todo_summary": {},
        "agent_todo_source_items": [], "capability_gate": None,
        "blocked_priority_fallback": None, "goal_frontier_projection": None, **kwargs,
    })


def test_route_only_uses_one_batch_without_inventory_or_goal_context(monkeypatch):
    from loopx.control_plane.work_items import replan_context_codec

    def forbidden(*args, **kwargs):
        pytest.fail("route-only must not read inventory or Goal context")

    monkeypatch.setattr(adapter, "build_quota_planning_inventory_request", forbidden)
    monkeypatch.setattr(replan_context_codec, "goal_context_facts", forbidden)
    native = adapter.effect_runtime_result
    calls = []

    def observed(operation, request):
        calls.append((operation, deepcopy(request)))
        return native(operation, request)

    monkeypatch.setattr(adapter, "effect_runtime_result", observed)
    assert _packet() == {}
    assert calls == []
    result = _packet(goal_route_context=_context(), source_status={"registry": {"irrelevant": True}})
    assert result["goal_route_hint"]["route_decision"] == "durable_goal_route"
    assert len(calls) == 1
    assert calls[0][0] == "work_item.action_portfolio.project"
    assert calls[0][1]["planning"] is None


def test_capture_preserves_empty_sources_all_rows_and_snapshot_identity():
    text = "\u2003long🙂 " * 100
    summary = {
        "executable_backlog_items": [],
        "unclaimed_priority_open_items": [{"text": text}] * 5,
        "current_agent_handoff_gates": [], "handoff_gates": "invalid",
    }
    frontier = {}
    override = {"kind": "task_orchestration", "peer_lane_count": 2}
    context = _context(agent_todo_summary=summary, agent_scope_frontier=frontier,
                       goal_route_hint_override=override)
    facts = context["agent_todo_summary"]
    assert facts["executable_backlog_items"] == []
    assert facts["current_agent_handoff_gates"] == []
    assert facts["handoff_gates"] is None
    assert facts["claimed_by_others_items"] is None
    assert len(facts["unclaimed_priority_open_items"]) == 5
    assert len(facts["unclaimed_priority_open_items"][0]["display_text"]) <= 120
    assert context["agent_scope_frontier"] == {"action": "", "effective_action": "", "reason": ""}
    assert context["actions_projection_aligned"] is True
    summary["executable_backlog_items"].append({"text": "later mutation"})
    override["peer_lane_count"] = 99
    assert facts["executable_backlog_items"] == []
    assert context["goal_route_hint_override"]["peer_lane_count"] == 2


def test_invalid_agent_does_not_create_override_only_route():
    assert _context(agent_identity={}, goal_route_hint_override={"kind": "task_orchestration"}) is None


def test_monitor_only_removes_native_batch_route_before_public_readback():
    status = quota_status_payload(
        goal_id="route-fixture", status="active", recommended_action="Complete the deliverable",
        coordination={"registered_agents": ["route-agent"],
                      "agent_work_modes": {"route-agent": "monitor_only"}},
        agent_todo_items=[quota_todo_item(todo_id="todo_route_work", title="Complete the deliverable",
                                         claimed_by="route-agent")],
    )
    result = build_quota_should_run(status, goal_id="route-fixture", agent_id="route-agent")
    assert result["should_run"] is False
    assert result["agent_work_mode"] == "monitor_only"
    assert "goal_route_hint" not in result


def test_planning_and_route_share_one_existing_rpc(monkeypatch):
    native = adapter.effect_runtime_result
    calls = []

    def observed(operation, request):
        calls.append((operation, deepcopy(request)))
        return native(operation, request)

    monkeypatch.setattr(adapter, "effect_runtime_result", observed)
    candidate = {"todo_id": "todo_route_work", "text": "Complete the deliverable",
                 "status": "open", "task_class": "advancement_task"}
    result = _packet(projection_enabled=True, selected=candidate,
                     goal_route_context=_context(agent_lane_next_action=candidate))
    assert result["action_portfolio"]
    assert result["goal_route_hint"]["route_decision"] == "run_current_agent_lane"
    assert len(calls) == 1
    assert calls[0][1]["planning"]["planning_inventory_request"]


def test_compatibility_text_and_gate_identity_use_python_owner():
    text = "\u001c\u2003" + "🙂" * 240 + "\u00a0tail"
    context = _context(agent_todo_summary={"handoff_gates": [{
        "index": 0, "text": text, "todo_id": None, "excluded_agents": ("route-agent",),
        "gate_state": "blocking",
    }]}, selected_recommended_action=False)
    row = context["agent_todo_summary"]["handoff_gates"][0]
    assert row["gate_identity"] == text
    assert row["text"] == "🙂" * 219 + "…"
    assert row["display_text"] == "🙂" * 119 + "…"
    assert row["excluded_agents"] == ["route-agent"]
    assert context["selected_recommended_action"] == ""
