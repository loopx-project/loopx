from __future__ import annotations

from loopx.capabilities.explore.todo_branch_plan import build_explore_todo_branch_plan
from loopx.capabilities.explore.worker_branch_plan import (
    _affinity_key,
    build_explore_worker_branch_plan,
)


ORCHESTRATION = {
    "spawn_allowed": True,
    "max_children": 4,
    "explore_harness": {"enabled": True},
}


def test_affinity_key_ignores_prose_and_cjk_free_text() -> None:
    assert _affinity_key({"text": "[P0] Audit current result graph and evidence closure"}) == "topic:general"
    assert _affinity_key({"text": "[P1] Review recent candidates against matched comparators"}) == "topic:general"
    assert _affinity_key({"text": "[P0] 审计当前结果图并验证收敛指标"}) == "topic:general"


def test_affinity_key_uses_typed_structured_fields_in_precedence_order() -> None:
    assert (
        _affinity_key(
            {
                "text": "[P0] 审计行为",
                "required_write_scopes": ["loopx/capabilities/explore/**"],
                "required_capabilities": ["analysis"],
                "target_key": "frontier_alpha",
                "task_domain": "planning",
                "action_kind": "audit",
            }
        )
        == "scope:loopx/capabilities"
    )
    assert (
        _affinity_key(
            {
                "text": "[P0] 审计行为",
                "required_capabilities": ["analysis"],
                "target_key": "frontier_alpha",
            }
        )
        == "capability:analysis"
    )
    assert _affinity_key({"text": "[P0] 审计行为", "target_key": "frontier_alpha"}) == "target:frontier_alpha"
    assert _affinity_key({"text": "[P0] 审计行为", "capability_binding_ref": "bind_1"}) == "binding:bind_1"
    assert _affinity_key({"text": "[P0] 审计行为", "task_domain": "planning"}) == "domain:planning"
    assert _affinity_key({"text": "[P0] 审计行为", "action_kind": "run_probe"}) == "action:run_probe"
    assert _affinity_key({"text": "[P0] 审计行为", "task_repository": "owner/repo"}) == "repo:owner/repo"


def test_todo_and_worker_branch_plan_match_cjk_todos_via_typed_frontier_refs() -> None:
    projection = {
        "frontier": [
            {
                "node_id": "node_frontier_cjk",
                "title": "验证中文探索分支与结构化匹配",
                "target_key": "target_eval_loop",
                "task_domain": "evaluation",
                "required_capabilities": ["eval_runner"],
            }
        ],
        "stuck": [],
    }
    typed_todo = {
        "todo_id": "todo_typed_cjk",
        "index": 1,
        "status": "open",
        "priority": "P1",
        "task_class": "advancement_task",
        "text": "[P1] 使用结构化引用验证目标路径",
        "explore_result_node_refs": ["node_frontier_cjk"],
        "target_key": "target_eval_loop",
        "task_domain": "evaluation",
        "required_capabilities": ["eval_runner"],
    }
    prose_only_todo = {
        "todo_id": "todo_prose_only",
        "index": 2,
        "status": "open",
        "priority": "P1",
        "task_class": "advancement_task",
        "text": "[P1] 验证中文探索分支与结构化匹配 frontier node_frontier_cjk",
    }

    todo_plan = build_explore_todo_branch_plan(
        goal_id="goal-typed-explore",
        todos=[prose_only_todo, typed_todo],
        projection=projection,
        orchestration=ORCHESTRATION,
        width=3,
    )
    branches = {item["todo_id"]: item for item in todo_plan["selected_branches"]}
    assert "explore_frontier_overlap" in branches["todo_typed_cjk"]["reason_codes"]
    assert "explore_frontier_overlap" not in branches["todo_prose_only"]["reason_codes"]
    assert branches["todo_typed_cjk"]["score"] > branches["todo_prose_only"]["score"]

    worker_plan = build_explore_worker_branch_plan(
        goal_id="goal-typed-explore",
        todos=[typed_todo, prose_only_todo],
        projection=projection,
        orchestration=ORCHESTRATION,
        worker_width=3,
    )
    selected = worker_plan["selected_worker_branches"]
    assert any(branch["affinity_key"] == "capability:eval_runner" for branch in selected)
    assert any(branch["affinity_key"] == "topic:general" for branch in selected)
