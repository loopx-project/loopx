"""Current dispatch suggestions must obey the existing Todo readiness contract."""
from copy import deepcopy

import pytest

from loopx.capabilities.explore.todo_branch_plan import build_explore_todo_branch_plan
from loopx.capabilities.explore.worker_branch_plan import build_explore_worker_branch_plan


ORCHESTRATION = {
    "spawn_allowed": True, "max_children": 4, "explore_harness": {"enabled": True},
}


def todo(todo_id, **fields):
    return {
        "todo_id": todo_id, "text": "Inspect a synthetic artifact", "status": "open",
        "priority": "P0", "task_class": "advancement_task", "claimed_by": "worker",
        "required_write_scopes": [f"artifacts/{todo_id}/**"], **fields,
    }


@pytest.fixture(params=["todo", "worker"])
def planner(request):
    def build(todos, *, orchestration=ORCHESTRATION, **kwargs):
        common = dict(goal_id="readiness", agent_id="worker", todos=todos,
                      orchestration=orchestration, scheduler_load=0, **kwargs)
        if request.param == "todo":
            plan = build_explore_todo_branch_plan(width=4, **common)
            return plan, plan["selected_branches"], plan["rejected_candidates"]
        plan = build_explore_worker_branch_plan(worker_width=4, **common)
        return plan, plan["selected_worker_branches"], plan["rejected_worker_branches"]
    return build


def selected_ids(branches):
    return {todo_id for branch in branches
            for todo_id in branch.get("todo_ids", [branch["todo_id"]])}


@pytest.mark.parametrize("condition", ["todo_done:todo_prepare", "monitor_changed:todo_monitor"])
@pytest.mark.parametrize("ready", [False, None])
def test_unready_todo_is_diagnostic_only_then_becomes_selectable(planner, condition, ready):
    waiting = todo("todo_waiting", resume_when=condition)
    if ready is not None:
        waiting["resume_ready"] = ready
    original = deepcopy(waiting)
    plan, selected, rejected = planner([waiting])
    assert selected == []
    assert "No actionable" in plan["next_action"]
    assert rejected[0]["todo_id"] == "todo_waiting"
    assert rejected[0]["selection_status"] == "not_actionable_open"
    assert rejected[0]["resume_when"] == condition
    assert not rejected[0].get("suggested_commands")
    assert waiting == original

    _, selected, rejected = planner([{**waiting, "resume_ready": True}])
    assert selected_ids(selected) == {"todo_waiting"}
    assert selected[0]["suggested_commands"]
    assert rejected == []


@pytest.mark.parametrize("shared_scope", [False, True])
def test_waiting_todo_uses_neither_worker_bundle_nor_resource_capacity(planner, shared_scope):
    waiting = todo("todo_waiting", resume_when="todo_done:todo_prepare", resume_ready=False)
    ready = todo("todo_ready", priority="P2")
    if shared_scope:
        waiting["required_write_scopes"] = ready["required_write_scopes"]
    for item in (waiting, ready):
        item["required_capabilities"] = ["resource_lane:compute"]
    plan, selected, rejected = planner([waiting, ready], resource_capacities={"compute": 2})
    assert selected_ids(selected) == {"todo_ready"}
    assert plan["resource_portfolio"]["selected_slot_count"] == 1
    assert plan["resource_portfolio"]["remaining_slot_count"] == 1
    assert next(row for row in rejected if row["todo_id"] == "todo_waiting")["resume_ready"] is False


@pytest.mark.parametrize("fields", [
    {"status": "done", "done": True},
    {"status": "blocked"},
    {"goal_acceptance_guard": {"allowed": False}},
])
def test_other_canonical_ineligibility_is_not_just_a_score_penalty(planner, fields):
    _, selected, rejected = planner([todo("todo_unavailable", **fields)])
    assert selected == []
    assert rejected[0]["selection_status"] == "not_actionable_open"


def test_lineage_and_other_agent_ownership_do_not_become_completion_dependencies(planner):
    successor = todo("todo_successor", successor_todo_id="todo_other")
    foreign = todo("todo_other", claimed_by="peer")
    _, selected, _ = planner([successor, foreign])
    assert selected_ids(selected) == {"todo_successor"}


def test_analysis_only_and_disabled_gates_keep_their_authority(planner):
    waiting = todo("todo_waiting", resume_when="todo_done:todo_prepare", resume_ready=False)
    ready = todo("todo_ready")
    _, selected, rejected = planner(
        [waiting, ready], orchestration={"explore_harness": {"enabled": True}},
    )
    assert selected_ids(selected) == {"todo_ready"}
    assert all(not row.get("suggested_commands") for row in [*selected, *rejected])
    plan, selected, rejected = planner([waiting, ready], orchestration={})
    assert plan["enabled"] is False
    assert selected == rejected == []
