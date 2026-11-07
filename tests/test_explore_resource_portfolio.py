from __future__ import annotations

from copy import deepcopy

import pytest

from loopx.capabilities.explore.resource_portfolio import (
    normalize_resource_counts,
    parse_resource_counts,
)
from loopx.capabilities.explore.todo_branch_plan import build_explore_todo_branch_plan
from loopx.capabilities.explore.worker_branch_plan import build_explore_worker_branch_plan


ANALYSIS_ONLY = {"explore_harness": {"enabled": True}}
CAPACITIES = {"long_pool": 2, "short_pool": 3}
USAGE = {"long_pool": 1, "short_pool": 1}


def _todo(
    todo_id: str,
    *,
    index: int,
    priority: str,
    resource_lane: str,
    write_scope: str,
    depends_on: list[str] | None = None,
    task_class: str = "advancement_task",
) -> dict[str, object]:
    todo: dict[str, object] = {
        "todo_id": todo_id,
        "index": index,
        "status": "open",
        "priority": priority,
        "text": f"[{priority}] Evaluate synthetic {todo_id}",
        "task_class": task_class,
        "required_capabilities": [f"resource_lane:{resource_lane}"],
        "required_write_scopes": [write_scope],
    }
    if depends_on:
        todo["depends_on"] = depends_on
    if task_class == "continuous_monitor":
        todo["next_due_at"] = "2020-01-01T00:00:00Z"
    return todo


TODOS = [
    _todo(
        "todo_monitor",
        index=1,
        priority="P0",
        resource_lane="long_pool",
        write_scope="observations/**",
        task_class="continuous_monitor",
    ),
    _todo(
        "todo_long_blocked_dependency",
        index=2,
        priority="P0",
        resource_lane="long_pool",
        write_scope="variants/long-blocked/**",
        depends_on=["todo_missing_prerequisite"],
    ),
    _todo(
        "todo_short_primary",
        index=3,
        priority="P0",
        resource_lane="short_pool",
        write_scope="variants/short-shared/**",
    ),
    _todo(
        "todo_short_conflict",
        index=4,
        priority="P1",
        resource_lane="short_pool",
        write_scope="variants/short-shared/**",
    ),
    {
        **_todo(
            "todo_long_backfill",
            index=5,
            priority="P2",
            resource_lane="long_pool",
            write_scope="variants/long-ready/**",
        ),
        "explore_result_node_refs": ["node_long_ready"],
    },
    _todo(
        "todo_short_backfill",
        index=6,
        priority="P2",
        resource_lane="short_pool",
        write_scope="variants/short-ready/**",
    ),
]

PROJECTION = {
    "nodes": [
        {
            "node_id": "node_long_ready",
            "status": "exploring",
            "node_kind": "experiment",
            "title": "Synthetic long lane",
            "finding_count": 0,
        }
    ],
    "findings": [],
    "edges": [],
}


def _assert_resource_fill(plan: dict[str, object], *, selected_key: str) -> None:
    selected = plan[selected_key]
    selected_ids = {
        todo_id
        for branch in selected
        for todo_id in branch.get("todo_ids", [branch.get("todo_id")])
    }
    assert selected_ids == {
        "todo_long_backfill",
        "todo_short_primary",
        "todo_short_backfill",
    }
    portfolio = plan["resource_portfolio"]
    assert portfolio["analysis_only"] is True
    assert portfolio["score_delta"] == 0.0
    assert portfolio["continuous_monitor_consumes_capacity"] is False
    assert portfolio["selected_slot_count"] == 3
    assert portfolio["remaining_slot_count"] == 0
    assert portfolio["lanes"]["long_pool"]["selected_slots"] == 1
    assert portfolio["lanes"]["short_pool"]["selected_slots"] == 2
    assert all(not branch["suggested_commands"] for branch in selected)

    rejected = plan[
        "rejected_candidates" if selected_key == "selected_branches" else "rejected_worker_branches"
    ]
    statuses = {
        todo_id: item.get("selection_status")
        for item in rejected
        for todo_id in item.get("todo_ids", [item.get("todo_id")])
    }
    assert statuses["todo_monitor"] == "excluded_non_exploration_lane"
    assert statuses["todo_long_blocked_dependency"] == "invalidated_dependency"
    assert statuses["todo_short_conflict"] == "rejected_hazard"


def test_todo_plan_backfills_each_resource_lane_after_dependency_and_scope_rejections() -> None:
    plan = build_explore_todo_branch_plan(
        goal_id="synthetic-resource-portfolio",
        todos=TODOS,
        projection=PROJECTION,
        orchestration=ANALYSIS_ONLY,
        width=5,
        resource_capacities=CAPACITIES,
        resource_usage=USAGE,
    )

    assert plan["orchestration_gate"]["state"] == "analysis_only"
    assert plan["selection_budget"] == 5
    _assert_resource_fill(plan, selected_key="selected_branches")
    long_branch = next(
        branch for branch in plan["selected_branches"] if branch["todo_id"] == "todo_long_backfill"
    )
    assert long_branch["typed_evidence_audit"]["score_delta"] == 0.0


def test_worker_plan_backfills_each_resource_lane_after_dependency_and_scope_rejections() -> None:
    plan = build_explore_worker_branch_plan(
        goal_id="synthetic-resource-portfolio",
        todos=TODOS,
        projection=PROJECTION,
        orchestration=ANALYSIS_ONLY,
        worker_width=5,
        max_todos_per_branch=1,
        resource_capacities=CAPACITIES,
        resource_usage=USAGE,
    )

    assert plan["orchestration_gate"]["state"] == "analysis_only"
    assert plan["selection_budget"] == 5
    _assert_resource_fill(plan, selected_key="selected_worker_branches")


def test_legacy_unlaned_planning_remains_unconstrained_without_resource_inputs() -> None:
    todos = [
        {
            "todo_id": f"todo_legacy_{index}",
            "index": index,
            "status": "open",
            "priority": "P1",
            "text": f"[P1] Inspect legacy branch {index}",
            "task_class": "advancement_task",
            "required_write_scopes": [f"legacy/{index}/**"],
        }
        for index in range(1, 4)
    ]
    plan = build_explore_todo_branch_plan(
        goal_id="synthetic-legacy-plan",
        todos=todos,
        orchestration=ANALYSIS_ONLY,
        width=2,
    )

    assert plan["resource_portfolio"]["enabled"] is False
    assert plan["selection_budget"] == plan["verification_budget"]
    assert len(plan["selected_branches"]) == plan["verification_budget"]


def test_resource_count_cli_values_are_normalized_and_duplicates_fail() -> None:
    assert parse_resource_counts(
        ["long-pool=2", "short_pool=3"],
        flag_name="--resource-capacity",
    ) == {"long_pool": 2, "short_pool": 3}

    try:
        parse_resource_counts(
            ["long_pool=2", "long-pool=3"],
            flag_name="--resource-capacity",
        )
    except ValueError as exc:
        assert "repeats resource lane" in str(exc)
    else:
        raise AssertionError("duplicate resource lane should fail closed")


@pytest.mark.parametrize(
    "planner", [build_explore_todo_branch_plan, build_explore_worker_branch_plan]
)
@pytest.mark.parametrize("field", ["resource_capacities", "resource_usage"])
@pytest.mark.parametrize("alias", ["long-pool", "LONG_POOL", "long pool"])
@pytest.mark.parametrize("reverse", [False, True])
def test_resource_aliases_cannot_overwrite_capacity_or_usage(
    planner, field, alias, reverse
) -> None:
    entries = [("long_pool", 2), (alias, 0)]
    if reverse:
        entries.reverse()
    arguments = {
        "goal_id": "synthetic-resource-aliases",
        "todos": TODOS,
        "projection": PROJECTION,
        "orchestration": ANALYSIS_ONLY,
        "resource_capacities": CAPACITIES,
        "resource_usage": USAGE,
        field: dict(entries),
    }
    before = deepcopy(arguments)

    with pytest.raises(ValueError, match=f"{field} repeats resource lane 'long_pool'"):
        planner(**arguments)

    assert arguments == before


@pytest.mark.parametrize(
    "planner", [build_explore_todo_branch_plan, build_explore_worker_branch_plan]
)
@pytest.mark.parametrize("field", ["resource_capacities", "resource_usage"])
@pytest.mark.parametrize(
    "value", [True, False, 0.5, -0.5, -1, float("nan"), float("inf"), "1.5", None]
)
def test_invalid_resource_counts_fail_before_planning(planner, field, value) -> None:
    with pytest.raises(ValueError, match=rf"{field}\['long_pool'\].*non-negative integer"):
        planner(
            goal_id="synthetic-invalid-resource-counts",
            todos=TODOS,
            orchestration=ANALYSIS_ONLY,
            **{
                "resource_capacities": CAPACITIES,
                "resource_usage": USAGE,
                field: {"long_pool": value},
            },
        )


def test_valid_resource_counts_preserve_normalized_cli_and_api_inputs() -> None:
    normalized = normalize_resource_counts(
        {"LONG POOL": "2", "short-pool": 3.0, "idle_pool": 0},
        field_name="resource_capacities",
    )

    assert normalized == {"idle_pool": 0, "long_pool": 2, "short_pool": 3}
    assert normalized == parse_resource_counts(
        ["LONG POOL=2", "short-pool=3", "idle_pool=0"],
        flag_name="--resource-capacity",
    )
