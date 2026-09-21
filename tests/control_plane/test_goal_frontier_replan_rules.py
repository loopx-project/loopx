from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from loopx.control_plane.goals.goal_frontier import (
    derive_goal_frontier_replan_obligation_from_summaries,
)
from loopx.control_plane.goals.goal_frontier.ack_policy import (
    autonomous_replan_ack_satisfies_obligation,
    replan_successor_transition_ack,
)
from loopx.control_plane.goals.goal_frontier.replan_rules import (
    GOAL_FRONTIER_REPLAN_RULE_ORDER,
    GoalFrontierReplanFacts,
    GoalFrontierReplanRule,
    select_goal_frontier_replan_rule,
)
from loopx.control_plane.todos.addition import require_replan_successor_scope
from loopx.control_plane.todos.frontier_revision import (
    TODO_FRONTIER_REVISION_INDEX_SCHEMA_VERSION,
    advancement_frontier_revision_from_index,
    build_advancement_frontier_revision_index,
)
from loopx.control_plane.todos.summary_item import compact_todo_summary_item
from loopx.control_plane.work_items.interaction_contract import (
    build_interaction_contract,
    interaction_next_cli_actions,
)
from loopx.control_plane.work_items.progress_observation import semantic_delta_from_writeback


@pytest.mark.parametrize(
    ("overrides", "expected_rule", "derives_obligation"),
    [
        (
            {"existing_replan_required": True, "blocking_handoff_gate_count": 1},
            GoalFrontierReplanRule.EXISTING_OBLIGATION,
            False,
        ),
        (
            {"blocking_handoff_gate_count": 1, "acceptance_gap_count": 1},
            GoalFrontierReplanRule.BLOCKING_HANDOFF_GATE,
            False,
        ),
        (
            {"ready_deferred_successor_count": 1},
            GoalFrontierReplanRule.READY_DEFERRED_SUCCESSOR,
            False,
        ),
        (
            {"blocking_user_open_count": 1, "acceptance_gap_count": 1},
            GoalFrontierReplanRule.OPEN_USER_TODO,
            False,
        ),
        (
            {"succession_gap_count": 1, "acceptance_gap_count": 1},
            GoalFrontierReplanRule.TODO_SUCCESSION_GAP,
            True,
        ),
        (
            {"acceptance_gap_count": 1},
            GoalFrontierReplanRule.VISION_ACCEPTANCE_GAP,
            True,
        ),
        (
            {
                "acceptance_gap_count": 1,
                "selectable_frontier_advancement": 1,
                "outcome_checkpoint_replan_required": True,
            },
            GoalFrontierReplanRule.VISION_ACCEPTANCE_GAP,
            True,
        ),
        (
            {
                "long_todo_chain_triggered": True,
                "selectable_frontier_advancement": 15,
            },
            GoalFrontierReplanRule.LONG_TODO_CHAIN,
            True,
        ),
        (
            {
                "current_agent_blocker_count": 1,
                "monitor_no_change_streak_triggered": True,
                "monitor_only_lane": True,
                "monitor_count": 1,
            },
            GoalFrontierReplanRule.CURRENT_AGENT_BLOCKER,
            False,
        ),
        (
            {
                "monitor_no_change_streak_triggered": True,
                "monitor_only_lane": True,
                "monitor_count": 1,
                "total_frontier_advancement": 1,
            },
            GoalFrontierReplanRule.MONITOR_NO_CHANGE_STREAK,
            True,
        ),
        ({}, GoalFrontierReplanRule.NOT_MONITOR_ONLY, False),
        (
            {"monitor_only_lane": True},
            GoalFrontierReplanRule.NO_OPEN_MONITOR,
            False,
        ),
        (
            {
                "monitor_only_lane": True,
                "monitor_count": 1,
                "agent_advancement_count": 1,
                "total_frontier_advancement": 1,
            },
            GoalFrontierReplanRule.ADVANCEMENT_REMAINS,
            False,
        ),
        (
            {
                "monitor_only_lane": True,
                "monitor_count": 1,
                "future_monitor_schedule_present": True,
            },
            GoalFrontierReplanRule.FUTURE_MONITOR_WAIT,
            False,
        ),
        (
            {
                "monitor_only_lane": True,
                "monitor_count": 1,
                "monitor_schedule_gap_count": 1,
            },
            GoalFrontierReplanRule.MONITOR_FRONTIER_EXHAUSTED,
            True,
        ),
        (
            {"monitor_only_lane": True, "monitor_count": 1},
            GoalFrontierReplanRule.MONITOR_FRONTIER_EXHAUSTED,
            True,
        ),
    ],
)
def test_goal_frontier_replan_decision_table(
    overrides: dict[str, object],
    expected_rule: GoalFrontierReplanRule,
    derives_obligation: bool,
) -> None:
    facts = replace(GoalFrontierReplanFacts(), **overrides)

    decision = select_goal_frontier_replan_rule(facts)

    assert decision.rule is expected_rule
    assert decision.derives_obligation is derives_obligation
    assert decision.to_payload()["rule_index"] == GOAL_FRONTIER_REPLAN_RULE_ORDER.index(
        expected_rule
    )


def _repeat_vision_gap() -> list[dict[str, object]]:
    return [
        {
            "kind": "vision_acceptance_gap",
            "acceptance_summary": "This agent must keep advancing its own stage.",
            "replan_trigger_summary": "No runnable work satisfies this agent's stage.",
            "advancement_policy": "repeat_until_closed",
        }
    ]


def _advancement(todo_id: str, claimed_by: str) -> dict[str, object]:
    return {
        "todo_id": todo_id,
        "status": "open",
        "task_class": "advancement_task",
        "claimed_by": claimed_by,
    }


def _long_chain_source_items(
    *, updated_at: str = "2026-08-22T09:00:00+08:00"
) -> list[dict[str, object]]:
    return [
        {
            **_advancement(f"todo_{index:012x}", "current-agent"),
            "updated_at": updated_at,
        }
        for index in range(15)
    ]


def _long_chain_summary(source_items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "open_count": len(source_items),
        "current_agent_claimed_open_count": len(source_items),
        "current_agent_claimed_advancement_count": len(source_items),
        "unclaimed_open_count": 0,
        "unclaimed_priority_open_items": [],
        "executable_backlog_items": source_items[:8],
        "claim_scope": {"other_agent_claimed_items": []},
    }


def _accepted_long_chain_ack(obligation: dict[str, object]) -> dict[str, object]:
    trigger = obligation["triggers"][0]
    return {
        "schema_version": "autonomous_replan_ack_v0",
        "recorded": True,
        "generated_at": "2026-08-22T09:05:00+08:00",
        "semantic_delta": {
            "schema_version": "replan_semantic_delta_v0",
            "accepted": True,
            "outcomes": ["new_runnable_successor"],
            "satisfying_outcomes": ["new_runnable_successor"],
            "trigger_kinds": ["long_todo_chain"],
            "trigger_checkpoints": [
                {
                    "kind": "long_todo_chain",
                    "frontier_revision": trigger["frontier_revision"],
                }
            ],
            "obligation_id": obligation["obligation_id"],
        },
    }


def _derive_long_chain(
    source_items: list[dict[str, object]],
    *,
    agent_todo_summary: dict[str, object] | None = None,
    latest_replan_ack: dict[str, object] | None = None,
    current_transition_replan_ack: dict[str, object] | None = None,
) -> dict[str, object] | None:
    return derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0},
        agent_todo_summary=agent_todo_summary or _long_chain_summary(source_items),
        agent_todo_source_items=source_items,
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        agent_id="current-agent",
        existing_replan_obligation=None,
        latest_replan_ack=latest_replan_ack,
        current_transition_replan_ack=current_transition_replan_ack,
        acceptance_gaps=[],
    )


def test_long_todo_chain_checkpoint_is_edge_triggered_and_rearms_on_change() -> None:
    source_items = _long_chain_source_items()
    original = _derive_long_chain(source_items)
    assert original is not None
    ack = _accepted_long_chain_ack(original)
    unrecorded_ack = deepcopy(ack)
    unrecorded_ack["recorded"] = False

    assert _derive_long_chain(source_items, latest_replan_ack=unrecorded_ack) is not None
    assert _derive_long_chain(source_items, latest_replan_ack=ack) is None

    maintenance_touch = deepcopy(source_items)
    maintenance_touch[-1]["updated_at"] = "2026-08-22T09:10:00+08:00"
    assert _derive_long_chain(maintenance_touch, latest_replan_ack=ack) is None

    changed_items = deepcopy(source_items)
    changed_items[-1]["updated_at"] = "2026-08-22T09:10:00+08:00"
    changed_items[-1]["priority"] = "P0"
    rearmed = _derive_long_chain(changed_items, latest_replan_ack=ack)
    assert rearmed is not None
    assert rearmed["obligation_id"] != original["obligation_id"]
    assert rearmed["rearmed_after_obligation_id"] == original["obligation_id"]
    assert rearmed["triggers"][0]["frontier_revision"] != (
        original["triggers"][0]["frontier_revision"]
    )


@pytest.mark.parametrize("change,rearms", [
    ("peer_claim", False), ("unclaimed_priority", False),
    ("owned_priority", True), ("maintenance_timestamp", False),
])
def test_writeback_ack_preserves_owned_material_basis(change: str, rearms: bool) -> None:
    items = [*_long_chain_source_items(), {
        **_advancement("todo_unclaimed", ""),
        "updated_at": "2026-08-22T09:00:00+08:00",
    }]

    def derive(rows, ack=None):
        owned = [row for row in rows if row.get("claimed_by") == "current-agent"]
        unclaimed = [row for row in rows if not row.get("claimed_by")]
        return _derive_long_chain(rows, latest_replan_ack=ack, agent_todo_summary={
            "open_count": len(rows), "current_agent_claimed_open_count": len(owned),
            "current_agent_claimed_advancement_count": len(owned),
            "unclaimed_open_count": len(unclaimed),
            "unclaimed_priority_open_items": unclaimed,
            "executable_backlog_items": owned + unclaimed,
            "claim_scope": {"other_agent_claimed_items": [
                row for row in rows if row not in owned + unclaimed]},
        })

    original = derive(items)
    assert original is not None
    delta = semantic_delta_from_writeback(obligation=original, progress_observation={
        "result_class": "advanced", "surface_id": "dependency-recovery",
        "evidence_ids": ["evidence:independent-acceptance"],
    })
    assert delta["accepted"]
    ack = {"recorded": True, "semantic_delta": delta}
    assert derive(items, ack) is None
    changed = deepcopy(items)
    if change == "peer_claim":
        changed[-1]["claimed_by"] = "peer-agent"
    elif change == "unclaimed_priority":
        changed[-1]["priority"] = "P0"
    elif change == "owned_priority":
        changed[0]["priority"] = "P0"
    else:
        changed[0]["updated_at"] = "2026-08-22T10:00:00+08:00"
    # The open Turn must keep its binding even before an ACK is recorded.
    assert (derive(changed)["obligation_id"] != original["obligation_id"]) is rearms
    assert (derive(changed, ack) is not None) is rearms


def test_frontier_revision_index_preserves_complete_agent_lane_semantics() -> None:
    source_items = [
        {
            **_advancement("todo_000000000001", "current-agent"),
            "updated_at": "2026-08-22T09:00:00+08:00",
        },
        {
            **_advancement("todo_000000000002", "other-agent"),
            "updated_at": "2026-08-22T09:00:00+08:00",
        },
        {
            "todo_id": "todo_000000000003",
            "status": "open",
            "task_class": "advancement_task",
            "updated_at": "2026-08-22T09:00:00+08:00",
        },
    ]
    original = build_advancement_frontier_revision_index(source_items)
    current_revision = advancement_frontier_revision_from_index(
        original,
        agent_id="current-agent",
    )
    unclaimed_revision = advancement_frontier_revision_from_index(
        original,
        agent_id="new-agent",
    )
    all_revision = advancement_frontier_revision_from_index(original, agent_id=None)
    assert current_revision is not None and current_revision[2] is True
    assert unclaimed_revision is not None and unclaimed_revision[2] is True
    assert all_revision is not None and all_revision[2] is True

    other_agent_change = deepcopy(source_items)
    other_agent_change[1]["priority"] = "P0"
    changed_other = build_advancement_frontier_revision_index(other_agent_change)
    assert advancement_frontier_revision_from_index(
        changed_other,
        agent_id="current-agent",
    ) == current_revision
    assert advancement_frontier_revision_from_index(
        changed_other,
        agent_id="new-agent",
    ) == unclaimed_revision
    assert advancement_frontier_revision_from_index(
        changed_other,
        agent_id=None,
    ) != all_revision

    unclaimed_change = deepcopy(source_items)
    unclaimed_change[2]["priority"] = "P0"
    changed_unclaimed = build_advancement_frontier_revision_index(unclaimed_change)
    assert advancement_frontier_revision_from_index(
        changed_unclaimed,
        agent_id="current-agent",
    ) != current_revision
    assert advancement_frontier_revision_from_index(
        changed_unclaimed,
        agent_id="new-agent",
    ) != unclaimed_revision


def test_frontier_revision_index_rejects_malformed_agent_rows() -> None:
    assert advancement_frontier_revision_from_index(
        {
            "schema_version": TODO_FRONTIER_REVISION_INDEX_SCHEMA_VERSION,
            "all": {"complete": False},
            "unclaimed": {"complete": False},
            "by_agent": "not-a-list",
        },
        agent_id="current-agent",
    ) == (None, None, False)


def test_todo_succession_gap_prefers_exact_lifecycle_settlement() -> None:
    completed_items = [
        {
            "todo_id": "todo_111111111111",
            "text": "Implement the bounded feature.",
            "status": "done",
            "task_class": "advancement_task",
            "claimed_by": "current-agent",
            "completion_turn_key": "turn-implement",
        },
        {
            "todo_id": "todo_222222222222",
            "text": "Validate the bounded feature.",
            "status": "done",
            "task_class": "advancement_task",
            "claimed_by": "current-agent",
            "completion_turn_key": "turn-validate",
        },
    ]
    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0},
        agent_todo_summary={
            "open_count": 0,
            "current_agent_claimed_advancement_count": 0,
            "todo_succession_warning": {
                "reason_code": "completed_advancement_without_successor",
                "count": len(completed_items),
                "items": completed_items,
            },
        },
        work_lane_contract=None,
        agent_id="current-agent",
        existing_replan_obligation=None,
        acceptance_gaps=[],
    )

    assert obligation is not None
    assert obligation["resolution_mode"] == "todo_lifecycle_settlement"
    assert obligation["guidance_actions"][0] == "record_no_followup"
    assert obligation["todo_actions"][0] == {
        "action": "settle",
        "role": "agent",
        "priority": "P0",
        "todo_ids": ["todo_111111111111", "todo_222222222222"],
        "text": (
            "settle the exact completed Todos through no-follow-up or link each "
            "to a real runnable successor"
        ),
    }
    assert "todo_111111111111, todo_222222222222" in obligation[
        "recommended_action"
    ]
    assert "host-projected coverage ledger" not in obligation["recommended_action"]
    assert obligation["replan_novelty_policy"]["writeback"] == (
        "todo_lifecycle_or_typed_successor"
    )
    assert [trigger["completion_turn_key"] for trigger in obligation["triggers"]] == [
        "turn-implement",
        "turn-validate",
    ]


def test_todo_succession_gap_cli_actions_do_not_require_semantic_refresh() -> None:
    actions = interaction_next_cli_actions(
        {
            "goal_id": "goal-example",
            "agent_identity": {"agent_id": "current-agent"},
            "autonomous_replan_obligation": {
                "resolution_mode": "todo_lifecycle_settlement",
                "triggers": [
                    {
                        "kind": "completed_advancement_without_successor",
                        "todo_id": "todo_111111111111",
                        "completion_turn_key": "turn-implement",
                    },
                    {
                        "kind": "completed_advancement_without_successor",
                        "todo_id": "todo_222222222222",
                    },
                ],
            },
        },
        mode="autonomous_replan",
        available_capabilities=["shell"],
    )

    assert "--todo-id todo_111111111111" in actions[0]
    assert "--turn-instance-id turn-implement" in actions[0]
    assert "--todo-id todo_222222222222" in actions[1]
    assert "--turn-instance-id" not in actions[1]
    assert all("refresh-state" not in action for action in actions)
    assert all("spend-slot" not in action for action in actions)
    assert actions[-1] == (
        "loopx --format json quota should-run --goal-id goal-example "
        "--agent-id current-agent --available-capability shell"
    )


def test_todo_succession_gap_cli_actions_preserve_runtime_root() -> None:
    actions = interaction_next_cli_actions(
        {
            "goal_id": "goal-example",
            "agent_identity": {"agent_id": "current-agent"},
            "autonomous_replan_obligation": {
                "resolution_mode": "todo_lifecycle_settlement",
                "triggers": [
                    {
                        "kind": "completed_advancement_without_successor",
                        "todo_id": "todo_111111111111",
                        "completion_turn_key": "turn-implement",
                    }
                ],
            },
        },
        mode="autonomous_replan",
        available_capabilities=["shell"],
        runtime_root="/tmp/loopx runtime",
    )

    assert actions[0].startswith(
        "when no real successor remains for completed Todo "
        "todo_111111111111: loopx --runtime-root '/tmp/loopx runtime' todo complete"
    )
    assert actions[-1] == (
        "loopx --runtime-root '/tmp/loopx runtime' --format json quota should-run "
        "--goal-id goal-example --agent-id current-agent --available-capability shell"
    )


def test_todo_succession_gap_interaction_contract_is_model_actionable() -> None:
    contract = build_interaction_contract(
        {
            "goal_id": "goal-example",
            "agent_identity": {"agent_id": "current-agent"},
            "execution_obligation": {
                "kind": "autonomous_replan_required",
                "must_attempt_work": True,
                "delivery_allowed": True,
            },
            "autonomous_replan_obligation": {
                "resolution_mode": "todo_lifecycle_settlement",
                "recommended_action": (
                    "settle the exact completed Todo(s) todo_111111111111 through "
                    "loopx todo complete --no-follow-up when no real work remains; "
                    "otherwise link a real runnable successor"
                ),
                "triggers": [
                    {
                        "kind": "completed_advancement_without_successor",
                        "todo_id": "todo_111111111111",
                        "completion_turn_key": "turn-implement",
                    }
                ],
            },
        },
        available_capabilities=["shell"],
    )

    assert contract["mode"] == "autonomous_replan"
    assert "todo_111111111111" in contract["agent_channel"]["primary_action"]
    assert "produce one typed outcome" not in contract["agent_channel"][
        "primary_action"
    ]
    assert contract["cli_channel"]["spend_after_validation"] is False
    assert contract["cli_channel"]["spend_policy"] == (
        "no spend for Todo lifecycle settlement; rerun quota after the transition"
    )
    assert "settlement_plan" not in contract["cli_channel"]
    actions = contract["cli_channel"]["next_cli_actions"]
    assert "--todo-id todo_111111111111" in actions[0]
    assert "--turn-instance-id turn-implement" in actions[0]
    assert all("refresh-state" not in action for action in actions)
    assert all("spend-slot" not in action for action in actions)


@pytest.mark.parametrize("other_agent_count", [1, 2, 8])
def test_other_agent_backlog_size_cannot_satisfy_scoped_vision_gap(
    other_agent_count: int,
) -> None:
    other_items = [
        _advancement(f"todo_peer_{index}", f"peer-{index}")
        for index in range(other_agent_count)
    ]

    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0},
        agent_todo_summary={
            "open_count": other_agent_count,
            "claimed_advancement_open_count": other_agent_count,
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_priority_open_items": [],
            "executable_backlog_items": [],
            "claim_scope": {"other_agent_claimed_items": other_items},
        },
        work_lane_contract=None,
        agent_id="current-agent",
        existing_replan_obligation=None,
        acceptance_gaps=_repeat_vision_gap(),
    )

    assert obligation is not None
    assert obligation["agent_id"] == "current-agent"
    assert obligation["triggers"][0]["kind"] == "vision_acceptance_gap"


def test_current_agent_advancement_satisfies_scoped_vision_frontier() -> None:
    current_item = _advancement("todo_current", "current-agent")

    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0},
        agent_todo_summary={
            "open_count": 1,
            "claimed_advancement_open_count": 1,
            "current_agent_claimed_advancement_count": 1,
            "unclaimed_priority_open_items": [],
            "executable_backlog_items": [current_item],
            "claim_scope": {"other_agent_claimed_items": []},
        },
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        agent_id="current-agent",
        existing_replan_obligation=None,
        acceptance_gaps=_repeat_vision_gap(),
    )

    assert obligation is None


def test_exact_replan_successor_uses_authoritative_todo_source() -> None:
    obligation_id = "replan-0123456789abcdef"
    frontier_identity = "progress:abcdef0123456789"
    successor = compact_todo_summary_item(
        {
            "todo_id": "todo_0123456789ab",
            "text": "Run the selected bounded experiment.",
            "status": "open",
            "task_class": "advancement_task",
            "claimed_by": "current-agent",
            "replan_obligation_id": obligation_id,
            "action_kind": "validate",
            "target_key": "experiment:selected-bounded-slice",
        }
    )
    unrelated_display_items = [
        _advancement(f"todo_{index:012x}", "current-agent") for index in range(3)
    ]

    ack = replan_successor_transition_ack(
        {"first_executable_items": unrelated_display_items},
        agent_id="current-agent",
        replan_obligation={
            "obligation_id": obligation_id,
            "frontier_identity": frontier_identity,
            "satisfying_semantic_outcomes": ["new_runnable_successor"],
        },
        agent_todo_items=[*unrelated_display_items, successor],
    )

    assert successor["replan_obligation_id"] == obligation_id
    assert ack is not None
    assert ack["frontier_identity"] == frontier_identity
    assert ack["semantic_delta"]["successor_todo_id"] == successor["todo_id"]
    assert ack["semantic_delta"]["outcomes"] == ["new_runnable_successor"]
    assert ack["semantic_delta"]["successor_binding"] == {
        "action_kind": "validate",
        "target_key": "experiment:selected-bounded-slice",
    }


def test_long_chain_successor_transition_is_bound_to_current_frontier() -> None:
    source_items = _long_chain_source_items()
    original = _derive_long_chain(source_items)
    assert original is not None
    changed_items = deepcopy(source_items)
    changed_items[-1]["updated_at"] = "2026-08-22T09:10:00+08:00"
    changed_items[-1]["priority"] = "P0"
    rearmed = _derive_long_chain(changed_items)
    assert rearmed is not None

    stale_successor = {
        **_advancement("todo_0123456789ab", "current-agent"),
        "replan_obligation_id": original["obligation_id"],
        "action_kind": "validate",
        "target_key": "experiment:stale-successor",
        "updated_at": "2026-08-22T09:05:00+08:00",
    }
    stale_ack = replan_successor_transition_ack(
        {"first_executable_items": [stale_successor]},
        agent_id="current-agent",
        replan_obligation=rearmed,
        agent_todo_items=[*changed_items, stale_successor],
    )
    fresh_successor = {
        **stale_successor,
        "todo_id": "todo_abcdef012345",
        "replan_obligation_id": rearmed["obligation_id"],
        "target_key": "experiment:fresh-successor",
        "updated_at": "2026-08-22T09:10:00+08:00",
    }
    fresh_ack = replan_successor_transition_ack(
        {"first_executable_items": [stale_successor, fresh_successor]},
        agent_id="current-agent",
        replan_obligation=rearmed,
        agent_todo_items=[*changed_items, stale_successor, fresh_successor],
    )
    post_successor_obligation = _derive_long_chain(
        [*changed_items, stale_successor, fresh_successor]
    )

    assert stale_ack is None
    assert fresh_ack is not None
    assert post_successor_obligation is not None
    assert post_successor_obligation["triggers"][0]["frontier_revision"] != (
        rearmed["triggers"][0]["frontier_revision"]
    )
    fresh_checkpoints = fresh_ack["semantic_delta"]["trigger_checkpoints"]
    assert [row["kind"] for row in fresh_checkpoints] == ["long_todo_chain"]
    assert fresh_checkpoints[0]["frontier_revision"] == (
        post_successor_obligation["triggers"][0]["frontier_revision"]
    )
    # The checkpoint also records the identity of the rows this agent owns, so
    # another lane taking over unclaimed work does not re-arm this ACK.
    assert fresh_checkpoints[0]["frontier_owned_identity"].startswith(
        "todo_frontier_revision_v0:owned:"
    )
    assert (
        _derive_long_chain(
            [*changed_items, stale_successor, fresh_successor],
            current_transition_replan_ack=fresh_ack,
        )
        is None
    )


def test_untyped_replan_successor_cannot_close_replan() -> None:
    obligation_id = "replan-0123456789abcdef"
    successor = {
        **_advancement("todo_0123456789ab", "current-agent"),
        "replan_obligation_id": obligation_id,
    }

    ack = replan_successor_transition_ack(
        {"first_executable_items": [successor]},
        agent_id="current-agent",
        replan_obligation={
            "obligation_id": obligation_id,
            "satisfying_semantic_outcomes": ["new_runnable_successor"],
        },
        agent_todo_items=[successor],
    )

    assert ack is None


def test_replan_successor_authoring_requires_typed_action_and_target() -> None:
    with pytest.raises(ValueError, match="typed action_kind"):
        require_replan_successor_scope(
            role="agent",
            task_class="advancement_task",
            claimed_by="current-agent",
            obligation_id="replan-0123456789abcdef",
            action_kind=None,
            target_key=None,
            explore_result_node_refs=None,
        )

    assert require_replan_successor_scope(
        role="agent",
        task_class="advancement_task",
        claimed_by="current-agent",
        obligation_id="replan-0123456789abcdef",
        action_kind="validate",
        target_key="experiment:selected-bounded-slice",
        explore_result_node_refs=None,
    ) == "replan-0123456789abcdef"

    with pytest.raises(ValueError, match="16 lowercase hex"):
        require_replan_successor_scope(
            role="agent",
            task_class="advancement_task",
            claimed_by="current-agent",
            obligation_id="replan-not-current",
            action_kind="validate",
            target_key="experiment:selected-bounded-slice",
            explore_result_node_refs=None,
        )


def test_unrelated_actionable_todo_cannot_close_replan() -> None:
    obligation_id = "replan-0123456789abcdef"

    ack = replan_successor_transition_ack(
        {"first_executable_items": []},
        agent_id="current-agent",
        replan_obligation={
            "obligation_id": obligation_id,
            "satisfying_semantic_outcomes": ["new_runnable_successor"],
        },
        agent_todo_items=[
            {
                **_advancement("todo_0123456789ab", "current-agent"),
                "replan_obligation_id": "replan-fedcba9876543210",
            }
        ],
    )

    assert ack is None


def test_empty_authoritative_todo_source_does_not_use_stale_display_item() -> None:
    obligation_id = "replan-0123456789abcdef"
    stale_display_item = {
        **_advancement("todo_0123456789ab", "current-agent"),
        "replan_obligation_id": obligation_id,
    }

    ack = replan_successor_transition_ack(
        {"first_executable_items": [stale_display_item]},
        agent_id="current-agent",
        replan_obligation={
            "obligation_id": obligation_id,
            "satisfying_semantic_outcomes": ["new_runnable_successor"],
        },
        agent_todo_items=[],
    )

    assert ack is None


def test_terminal_ack_cannot_hide_invalid_monitor_schedule() -> None:
    monitor = {
        "todo_id": "todo_monitor_gap",
        "status": "open",
        "task_class": "continuous_monitor",
        "claimed_by": "current-agent",
        "target_key": "bounded-monitor",
        "cadence": "1d",
    }
    terminal_ack = {
        "schema_version": "autonomous_replan_ack_v0",
        "recorded": True,
        "generated_at": "2026-08-13T09:00:00+08:00",
        "semantic_delta": {
            "accepted": True,
            "outcomes": ["coverage_backed_exploration_exhausted"],
        },
    }

    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0},
        agent_todo_summary={
            "open_count": 1,
            "claimed_advancement_open_count": 0,
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_priority_open_items": [],
            "executable_backlog_items": [],
            "claim_scope": {"other_agent_claimed_items": []},
            "monitor_open_items": [monitor],
            "monitor_due_count": 0,
            "monitor_schedule_gap_count": 1,
        },
        work_lane_contract={
            "lane": "continuous_monitor",
            "must_attempt_work": False,
        },
        agent_id="current-agent",
        existing_replan_obligation=None,
        latest_replan_ack=terminal_ack,
    )

    assert obligation is not None
    assert obligation["triggers"][0]["kind"] == "frontier_exhausted_monitor_lane"
    assert obligation["triggers"][0]["future_monitor_schedule_present"] is False


def _ack(generated_at: str, delta_kind: str = "goal_vision_patch") -> dict[str, object]:
    return {
        "generated_at": generated_at,
        "schema_version": "autonomous_replan_ack_v0",
        "recorded": True,
        "delta_contract": {
            "schema_version": "repair_delta_contract_v0",
            "required": True,
            "delta_present": True,
            "delta_kinds": [delta_kind],
        },
    }


def _vision_gap_with_generated_at(generated_at: str) -> list[dict[str, object]]:
    return [
        {
            "kind": "vision_successor_required",
            "acceptance_summary": "Write the next bounded agent vision.",
            "replan_trigger_summary": "stage vision closed while the goal remains active",
            "advancement_policy": "repeat_until_closed",
            "generated_at": generated_at,
        }
    ]


def test_fresh_replan_ack_settles_repeat_vision_gap_on_empty_frontier() -> None:
    gap_time = "2026-08-13T08:00:00+08:00"
    ack_time = "2026-08-13T09:00:00+08:00"

    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 1},
        agent_todo_summary={
            "open_count": 0,
            "claimed_advancement_open_count": 0,
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_priority_open_items": [],
            "executable_backlog_items": [],
            "claim_scope": {"other_agent_claimed_items": []},
        },
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        agent_id="current-agent",
        existing_replan_obligation=None,
        acceptance_gaps=_vision_gap_with_generated_at(gap_time),
        latest_replan_ack=_ack(ack_time),
    )

    assert obligation is None, "a fresh ack must settle the repeat vision gap"


def test_stale_ack_does_not_settle_newer_vision_gap() -> None:
    gap_time = "2026-08-13T09:00:00+08:00"
    ack_time = "2026-08-13T08:00:00+08:00"

    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 0},
        agent_todo_summary={
            "open_count": 0,
            "claimed_advancement_open_count": 0,
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_priority_open_items": [],
            "executable_backlog_items": [],
            "claim_scope": {"other_agent_claimed_items": []},
        },
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        agent_id="current-agent",
        existing_replan_obligation=None,
        acceptance_gaps=_vision_gap_with_generated_at(gap_time),
        latest_replan_ack=_ack(ack_time, delta_kind="successor_link"),
    )

    assert obligation is not None, "an older non-vision ack must not settle a newer gap"


def test_rearmed_vision_obligation_rotates_acked_generation_identity() -> None:
    """A newer vision gap must not reuse an already-settled obligation id."""

    summaries = {
        "user_todo_summary": {"open_count": 0},
        "agent_todo_summary": {
            "open_count": 0,
            "claimed_advancement_open_count": 0,
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_priority_open_items": [],
            "executable_backlog_items": [],
            "claim_scope": {"other_agent_claimed_items": []},
        },
        "work_lane_contract": {
            "lane": "advancement_task",
            "must_attempt_work": True,
        },
        "agent_id": "current-agent",
        "existing_replan_obligation": None,
    }
    original = derive_goal_frontier_replan_obligation_from_summaries(
        **summaries,
        acceptance_gaps=_vision_gap_with_generated_at(
            "2026-08-13T09:00:00+08:00"
        ),
    )
    assert original is not None
    original_id = original["obligation_id"]
    refreshed_without_ack = derive_goal_frontier_replan_obligation_from_summaries(
        **summaries,
        acceptance_gaps=_vision_gap_with_generated_at(
            "2026-08-13T09:30:00+08:00"
        ),
    )
    assert refreshed_without_ack is not None
    assert refreshed_without_ack["obligation_id"] == original_id
    prior_ack = _ack(
        "2026-08-13T10:00:00+08:00",
        delta_kind="successor_link",
    )
    prior_ack["semantic_delta"] = {
        "schema_version": "replan_semantic_delta_v0",
        "accepted": True,
        "outcomes": ["new_runnable_successor"],
        "satisfying_outcomes": ["new_runnable_successor"],
        "obligation_id": original_id,
    }

    def derive_rearmed() -> dict[str, object]:
        obligation = derive_goal_frontier_replan_obligation_from_summaries(
            **summaries,
            latest_replan_ack=prior_ack,
            acceptance_gaps=_vision_gap_with_generated_at(
                "2026-08-13T11:00:00+08:00"
            ),
        )
        assert obligation is not None
        return obligation

    rearmed = derive_rearmed()
    repeated_read = derive_rearmed()

    assert rearmed["obligation_id"] != original_id
    assert rearmed["obligation_id"] == repeated_read["obligation_id"]
    assert rearmed["rearmed_after_obligation_id"] == original_id
    assert rearmed["triggers"][0]["generated_at"] == (
        "2026-08-13T11:00:00+08:00"
    )
    assert not autonomous_replan_ack_satisfies_obligation(
        prior_ack,
        replan_obligation=rearmed,
        acceptance_gaps=_vision_gap_with_generated_at(
            "2026-08-13T11:00:00+08:00"
        ),
    )


def test_open_user_action_owns_empty_frontier_without_obligation() -> None:
    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={
            "open_count": 1,
            "gate_open_items": [],
        },
        agent_todo_summary={
            "open_count": 0,
            "claimed_advancement_open_count": 0,
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_priority_open_items": [],
            "executable_backlog_items": [],
            "claim_scope": {"other_agent_claimed_items": []},
        },
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        agent_id="current-agent",
        existing_replan_obligation=None,
    )

    assert obligation is None, "open user-owned work owns the empty frontier"

def test_vision_patch_ack_settles_newer_gap_from_the_ack_turn() -> None:
    gap_time = "2026-08-13T09:10:00+08:00"
    ack_time = "2026-08-13T09:00:00+08:00"

    obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary={"open_count": 1},
        agent_todo_summary={
            "open_count": 0,
            "claimed_advancement_open_count": 0,
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_priority_open_items": [],
            "executable_backlog_items": [],
            "claim_scope": {"other_agent_claimed_items": []},
        },
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        agent_id="current-agent",
        existing_replan_obligation=None,
        acceptance_gaps=_vision_gap_with_generated_at(gap_time),
        latest_replan_ack=_ack(ack_time, delta_kind="goal_vision_patch"),
    )

    assert obligation is None, (
        "a goal_vision_patch ack settles vision gaps written by the ack turn itself"
    )
