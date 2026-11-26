"""Public quota fallback invariants, independent of wording or display order."""
from loopx.control_plane.agents.agent_scope import _scoped_user_gate_fallback


def select(gate=None, items=None, **options):
    gate = {"todo_id": "todo_gate", "task_class": "user_gate", "status": "open",
            "blocks_agent": "agent-a", "action_kind": "publish_report", "unblocks_todo_id": "todo_publish", **(gate or {})}
    items = items if items is not None else [
        {"todo_id": "todo_work", "task_class": "advancement_task", "status": "open",
         "action_kind": "inspect_report", "text": "Inspect report", "index": 1}]
    return _scoped_user_gate_fallback(
        {"gate_open_items": [gate]},
        {"executable_backlog_items": items, "claim_scope": {"agent_id": "agent-a"}},
        allow_unrelated_gate=True, **options,
    )


def test_distinct_action_labels_cannot_certify_safe_independence():
    assert select({"unblocks_todo_id": None}, items=[{"todo_id": "todo_work", "status": "open",
                  "action_kind": "compile_library"}]) is None


def test_missing_structural_scope_cannot_certify_safe_independence():
    assert select({"action_kind": None, "text": "Review something", "unblocks_todo_id": None}) is None


def test_exact_dependency_and_global_gate_win_over_distinct_action_labels():
    assert select({"unblocks_todo_id": "todo_work"}) is None
    assert select({"global_gate": True}) is None
    assert select({"action_kind": "inspect_report", "unblocks_todo_id": None}) is None


def test_explicit_independence_wins_over_identical_words():
    result = select({"unblocks_todo_id": "todo_other", "action_kind": "inspect_report"})
    assert result is not None


def test_excluded_and_bound_to_another_agent_are_not_fallback_candidates():
    for restriction in ({"excluded_agents": ["agent-a"]}, {"bound_agent": "agent-b"},
                        {"claimed_by": "agent-b"}):
        assert select(items=[{"todo_id": "todo_work", "status": "open",
                              "action_kind": "compile_library", **restriction}]) is None


def test_empty_capability_result_does_not_revive_backlog():
    assert select(capability_gate={"runnable_candidates": []}) is None


def test_finished_rows_in_stale_candidate_lanes_are_not_executable():
    for state in ({"status": "done"}, {"archive_state": "archive"}, {"status": "blocked"}):
        assert select(items=[{"todo_id": "todo_work", "action_kind": "compile_library", **state}]) is None


def test_cjk_todo_descriptions_use_typed_scope_without_stopwords():
    # Identical CJK prose on gate and fallback still admits when typed dependency scope is disjoint.
    independent = select(
        {
            "text": "审批发布生产配置",
            "action_kind": "release_publish",
            "unblocks_todo_id": "todo_other",
        },
        items=[
            {
                "todo_id": "todo_cn_work",
                "task_class": "advancement_task",
                "status": "open",
                "claimed_by": "agent-a",
                "action_kind": "release_publish",
                "text": "审批发布生产配置（先跑本地单元测试）",
                "index": 1,
            }
        ],
    )
    assert independent is not None
    assert independent["selected_executable"]["todo_id"] == "todo_cn_work"

    # Distinct CJK prose without structural scope fails closed.
    assert (
        select(
            {
                "text": "等待审批生产权限",
                "action_kind": None,
                "unblocks_todo_id": None,
            },
            items=[
                {
                    "todo_id": "todo_cn_work",
                    "task_class": "advancement_task",
                    "status": "open",
                    "claimed_by": "agent-a",
                    "action_kind": "run_local_tests",
                    "text": "修复中文任务调度",
                    "index": 1,
                }
            ],
        )
        is None
    )

    # CJK task claimed by another agent is never selected.
    assert (
        select(
            {"text": "审批发布生产配置", "unblocks_todo_id": "todo_other"},
            items=[
                {
                    "todo_id": "todo_cn_other",
                    "task_class": "advancement_task",
                    "status": "open",
                    "claimed_by": "agent-b",
                    "action_kind": "run_local_tests",
                    "text": "修复中文任务调度",
                    "index": 1,
                }
            ],
        )
        is None
    )
