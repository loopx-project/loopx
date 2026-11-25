"""Quota diagnostics preserve observations without granting execution authority."""

from copy import deepcopy

import pytest

from loopx.control_plane.quota.should_run_packet import (
    _attach_quota_supporting_projections,
)


@pytest.mark.parametrize("should_run,notify_gate", [(True, True), (False, False)])
def test_gate_observations_do_not_reopen_settled_execution(
    should_run: bool, notify_gate: bool,
) -> None:
    payload = {"should_run": should_run}
    item = {
        "operator_question": "Approve the next step?",
        "agent_command": "loopx status",
        "stale_latest_run_warning": {"reason": "stale observation"},
        "backlog_hygiene_warning": "invalid diagnostic shape",
        "next_handoff_condition": "Owner decision arrives",
    }
    before = deepcopy(item)
    _attach_quota_supporting_projections(
        payload, status_payload={}, item=item, project_asset={},
        goal_id="projection-fixture", selected_recommended_action="Wait for approval",
        state="operator_gate", user_todo_summary=None, should_run=should_run,
        state_action_projection_warning=None, next_action_warning=None,
        replan_obligation=None, notify_gate=notify_gate,
    )
    assert payload["should_run"] is should_run
    assert payload["gate_prompt"]
    assert payload.get("notify_user_on_gate", False) is notify_gate
    assert ("agent_command" in payload) is should_run
    assert payload["stale_latest_run_warning"] == {"reason": "stale observation"}
    assert "backlog_hygiene_warning" not in payload
    assert payload["next_handoff_condition"] == "Owner decision arrives"
    assert item == before


@pytest.mark.parametrize("action,goal_id,matches", [
    ("Retry obsolete endpoint", "projection-fixture", True),
    ("Validate fresh parser", "projection-fixture", False),
    ("Retry obsolete endpoint", "other-goal", False),
    (None, "projection-fixture", False),
])
def test_reward_warning_is_scoped_to_selected_action_and_goal(
    action: str | None, goal_id: str, matches: bool,
) -> None:
    status = {"run_history": {"goals": [{
        "id": "projection-fixture",
        "latest_runs": [{"human_reward": {"lesson": {
            "summary": "Use the current endpoint", "avoid": ["obsolete endpoint"],
        }}}],
    }]}}
    payload = {}
    _attach_quota_supporting_projections(
        payload, status_payload=status, item={}, project_asset={}, goal_id=goal_id,
        selected_recommended_action=action, state="eligible", user_todo_summary=None,
        should_run=False, state_action_projection_warning=None,
        next_action_warning=None, replan_obligation=None,
    )
    assert ("reward_lesson_projection_warning" in payload) is matches
    if matches:
        warning = payload["reward_lesson_projection_warning"]
        assert warning["goal_id"] == goal_id
        assert warning["match_count"] == 1
        assert warning["matches"][0]["avoid"] == "obsolete endpoint"
    assert "agent_command" not in payload
    assert "notify_user_on_gate" not in payload


def test_reward_warning_rejects_english_stopword_bag_overlap_and_matches_cjk_phrase() -> None:
    status = {
        "run_history": {
            "goals": [
                {
                    "id": "projection-fixture",
                    "latest_runs": [
                        {
                            "human_reward": {
                                "lesson": {
                                    "summary": "Avoid premature benchmark expansion",
                                    "avoid": ["deploy release pipeline", "跳过回归验证"],
                                }
                            }
                        }
                    ],
                }
            ]
        }
    }

    # Loose bag-of-words overlap ("deploy", "release") must NOT trigger a warning.
    english_non_match = {}
    _attach_quota_supporting_projections(
        english_non_match,
        status_payload=status,
        item={},
        project_asset={},
        goal_id="projection-fixture",
        selected_recommended_action="Check release notes and deploy docs",
        state="eligible",
        user_todo_summary=None,
        should_run=False,
        state_action_projection_warning=None,
        next_action_warning=None,
        replan_obligation=None,
    )
    assert "reward_lesson_projection_warning" not in english_non_match

    # Exact CJK phrase works natively even with a leading priority prefix.
    cjk_match = {}
    _attach_quota_supporting_projections(
        cjk_match,
        status_payload=status,
        item={},
        project_asset={},
        goal_id="projection-fixture",
        selected_recommended_action="[P0] 禁止跳过回归验证后再合并",
        state="eligible",
        user_todo_summary=None,
        should_run=False,
        state_action_projection_warning=None,
        next_action_warning=None,
        replan_obligation=None,
    )
    assert cjk_match["reward_lesson_projection_warning"]["match_count"] == 1
    assert cjk_match["reward_lesson_projection_warning"]["matches"][0]["avoid"] == "跳过回归验证"
    assert cjk_match["reward_lesson_projection_warning"]["matches"][0]["match_kind"] == "exact_phrase"


def test_reward_warning_matches_typed_structured_scope_for_cjk_todos() -> None:
    status = {
        "run_history": {
            "goals": [
                {
                    "id": "projection-fixture",
                    "latest_runs": [
                        {
                            "human_reward": {
                                "lesson": {
                                    "summary": "Re-verify capability route before CJK delivery",
                                    "avoid": ["不经核验直接推进中文待办"],
                                    "todo_id": "todo_cn_route",
                                    "claimed_by": "agent-a",
                                    "task_class": "advancement_task",
                                    "capability_id": "reward_memory",
                                    "work_lane": {"lane": "advancement_task", "obligation": "attempt_advancement"},
                                }
                            }
                        }
                    ],
                }
            ]
        }
    }

    # Matching structured scope fires even when recommended_action is CJK and omits the avoid phrase.
    matched_payload = {
        "agent_identity": {"agent_id": "agent-a"},
        "selected_todo": {
            "todo_id": "todo_cn_route",
            "claimed_by": "agent-a",
            "task_class": "advancement_task",
            "required_capabilities": ["reward_memory"],
            "text": "修复中文任务路由并补充测试",
        },
        "work_lane": {
            "lane": "advancement_task",
            "obligation": "attempt_advancement",
            "selected_todo_id": "todo_cn_route",
        },
    }
    _attach_quota_supporting_projections(
        matched_payload,
        status_payload=status,
        item={},
        project_asset={},
        goal_id="projection-fixture",
        selected_recommended_action="[P0] 修复中文任务路由并补充测试",
        state="eligible",
        user_todo_summary=None,
        should_run=True,
        state_action_projection_warning=None,
        next_action_warning=None,
        replan_obligation=None,
    )
    warning = matched_payload["reward_lesson_projection_warning"]
    assert warning["match_count"] == 1
    assert warning["matches"][0]["match_kind"] == "structured_scope"
    assert warning["matches"][0]["matched_scope"]["todo_id"] == "todo_cn_route"
    assert warning["matches"][0]["matched_scope"]["claimed_by"] == "agent-a"
    assert warning["matches"][0]["matched_scope"]["capability_id"] == "reward_memory"

    # Different claimed_by or todo_id must NOT false-trigger even if prose matches verbatim.
    for overrides in (
        {"agent_identity": {"agent_id": "agent-b"}, "selected_todo": {"todo_id": "todo_cn_route", "claimed_by": "agent-b", "task_class": "advancement_task", "required_capabilities": ["reward_memory"]}},
        {"agent_identity": {"agent_id": "agent-a"}, "selected_todo": {"todo_id": "todo_other", "claimed_by": "agent-a", "task_class": "advancement_task", "required_capabilities": ["reward_memory"]}, "work_lane": {"lane": "advancement_task", "obligation": "attempt_advancement", "selected_todo_id": "todo_other"}},
        {"agent_identity": {"agent_id": "agent-a"}, "selected_todo": {"todo_id": "todo_cn_route", "claimed_by": "agent-a", "task_class": "continuous_monitor", "required_capabilities": ["reward_memory"]}, "work_lane": {"lane": "continuous_monitor", "obligation": "attempt_due_monitor", "selected_todo_id": "todo_cn_route"}},
    ):
        non_matching_payload = dict(overrides)
        _attach_quota_supporting_projections(
            non_matching_payload,
            status_payload=status,
            item={},
            project_asset={},
            goal_id="projection-fixture",
            selected_recommended_action="不经核验直接推进中文待办",
            state="eligible",
            user_todo_summary=None,
            should_run=True,
            state_action_projection_warning=None,
            next_action_warning=None,
            replan_obligation=None,
        )
        assert "reward_lesson_projection_warning" not in non_matching_payload


@pytest.mark.parametrize(("lesson_scope", "expected"), [
    ({"decision_scope": {"kind": "write_scope", "granularity": "project", "scope_key": "reports-demo"}}, True),
    ({"decision_scope": "production:project:reports-demo"}, False),
    ({"decision_scope": "write_scope:action:reports-demo"}, False),
    ({"decision_scope": "production:action:reports-demo", "scope_key": "reports-demo"}, False),
    ({"decision_scope": "bad-scope"}, False),
    ({"scope_key": "reports-demo"}, True),
])
def test_quota_reward_respects_decision_scope_identity(lesson_scope, expected):
    from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item
    from loopx.feedback import compact_reward
    from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown
    from loopx.quota import build_quota_should_run
    from loopx.status import compact_human_reward

    action = "Verify the reports"
    reward = compact_reward(
        recorded_at="2026-11-18T00:00:00+00:00", decision="route_correction", reward="mixed",
        reason_summary=action, follow_up=None,
        lesson={"kind": "route", "summary": action, "avoid": [action], **lesson_scope},
    )
    todo = quota_todo_item(todo_id="todo_reports", title=action, claimed_by="agent-a",
                          required_decision_scopes=["write_scope:project:reports-demo"])
    status = quota_status_payload(
        goal_id="scope-fixture", status="active", recommended_action=action,
        coordination={"agent_model": "peer_v1", "registered_agents": ["agent-a"]},
        agent_todo_items=[todo], latest_runs=[{"human_reward": compact_human_reward(reward)}],
    )
    packet = build_quota_should_run(status, goal_id="scope-fixture", agent_id="agent-a")
    assert packet["selected_todo"]["todo_id"] == "todo_reports"
    assert packet["should_run"] is True
    warning = packet.get("reward_lesson_projection_warning")
    assert bool(warning) is expected
    assert ("- reward_lesson_match:" in render_quota_should_run_markdown(packet)) is expected
    if expected:
        key = "write_scope:project:reports-demo" if "decision_scope" in lesson_scope else "reports-demo"
        assert warning["matches"][0]["matched_scope"]["scope_key"] == key


@pytest.mark.parametrize("avoid", [[], ["  "], ["直接跳过中文核验"]], ids=["prefer-only", "blank-avoid", "mixed"])
def test_build_quota_should_run_projects_structured_cjk_reward_lesson_end_to_end(avoid: list[str]) -> None:
    from loopx.control_plane.testing.quota_fixtures import (
        quota_status_payload,
        quota_todo_item,
    )
    from loopx.feedback import compact_reward
    from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown
    from loopx.quota import build_quota_should_run
    from loopx.status import compact_human_reward

    reward = compact_human_reward(
        compact_reward(
            recorded_at="2026-11-18T00:00:00+00:00",
            decision="route_correction",
            reward="mixed",
            reason_summary="需要先验证中文待办路由后再推进。",
            follow_up=None,
            lesson={
                "kind": "route",
                "summary": "中文待办必须绑定能力并先跑核验",
                "avoid": avoid,
                "prefer": ["先验证中文待办路由"],
                "todo_id": "todo_cn_route",
                "claimed_by": "agent-a",
                "task_class": "advancement_task",
                "capability_id": "shell",
                "work_lane": {"lane": "advancement_task"},
            },
        )
    )
    assert reward is not None
    assert reward["lesson"]["todo_id"] == "todo_cn_route"
    assert reward["lesson"]["claimed_by"] == "agent-a"

    status_a = quota_status_payload(
        goal_id="projection-fixture",
        status="active",
        recommended_action="[P0] 修复中文路由并执行单元测试",
        coordination={"agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"]},
        agent_todo_items=[
            quota_todo_item(
                todo_id="todo_cn_route",
                title="修复中文路由并执行单元测试",
                claimed_by="agent-a",
                required_capabilities=["shell"],
            ),
            quota_todo_item(
                todo_id="todo_cn_other",
                index=2,
                title="清理其他中文模块",
                claimed_by="agent-b",
                required_capabilities=["shell"],
            ),
        ],
        latest_runs=[{"generated_at": "2026-11-18T00:00:00+00:00", "human_reward": reward}],
    )
    assert reward["lesson"]["prefer"] == ["先验证中文待办路由"]
    guard_a = build_quota_should_run(
        status_a,
        goal_id="projection-fixture",
        agent_id="agent-a",
        available_capabilities=["shell"],
    )
    assert guard_a["should_run"] is True
    assert guard_a["selected_todo"]["todo_id"] == "todo_cn_route"
    markdown = render_quota_should_run_markdown(guard_a)
    if "直接跳过中文核验" in avoid:
        warning = guard_a["reward_lesson_projection_warning"]
        assert warning["match_count"] == 1
        match = warning["matches"][0]
        assert (match["match_kind"], match["matched_scope"]["todo_id"]) == ("structured_scope", "todo_cn_route")
        assert match["avoid"] == "直接跳过中文核验"
        assert "avoid=直接跳过中文核验" in markdown
    else:
        assert "reward_lesson_projection_warning" not in guard_a
        assert "- reward_lesson_match:" not in markdown
        assert "- reward_lesson_projection_warning:" not in markdown

    guard_b = build_quota_should_run(
        status_a,
        goal_id="projection-fixture",
        agent_id="agent-b",
        available_capabilities=["shell"],
    )
    assert "reward_lesson_projection_warning" not in guard_b
