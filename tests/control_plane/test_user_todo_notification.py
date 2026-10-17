from typing import Any

import pytest

from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)
from loopx.control_plane.todos.user_gate import (
    apply_scoped_user_gate_fallback_projection,
    build_user_todo_notification,
)
from loopx.control_plane.work_items.interaction_contract import build_interaction_contract
from loopx.quota import build_quota_should_run


@pytest.mark.parametrize(
    ("admitted_work", "cooldown", "expected_mode", "expected_notify", "spend_after_validation"),
    [
        (False, False, "user_todo_blocker_push", "NOTIFY", False),
        (True, False, "bounded_delivery_with_user_notice", "NOTIFY", True),
        (False, True, "user_gate_cooldown_wait", "DONT_NOTIFY", False),
    ],
)
def test_open_todo_notice_does_not_override_admitted_work_or_settlement(
    admitted_work: bool, cooldown: bool, expected_mode: str,
    expected_notify: str, spend_after_validation: bool,
) -> None:
    contract = build_interaction_contract({
        "goal_id": "public-notification-fixture",
        "requires_user_action": True,
        "notify_user_on_open_todo": True,
        "user_gate_notification_cooldown": {"notification_suppressed": cooldown},
        "should_run": admitted_work,
        "execution_obligation": {
            "must_attempt_work": admitted_work,
            "delivery_allowed": admitted_work,
        },
    })

    assert contract["mode"] == expected_mode
    assert contract["user_channel"]["notify"] == expected_notify
    assert contract["agent_channel"]["must_attempt"] is admitted_work
    assert contract["agent_channel"]["delivery_allowed"] is admitted_work
    assert contract["cli_channel"]["spend_allowed_now"] is False
    assert contract["cli_channel"]["spend_after_validation"] is spend_after_validation


def _quota_guard_for_user_todos(
    user_todo_items: list[dict[str, Any]],
) -> dict[str, Any]:
    agent_id = "codex-fixture"
    status = quota_status_payload(
        goal_id="nonblocking-user-action-fixture",
        status="active",
        recommended_action="Advance the current delivery.",
        waiting_on="controller",
        agent_todo_items=[
            quota_todo_item(
                todo_id="todo_delivery",
                title="Advance the current delivery.",
                claimed_by=agent_id,
                required_capabilities=["shell"],
            )
        ],
        user_todo_items=user_todo_items,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [agent_id, "codex-peer"],
        },
    )
    return build_quota_should_run(
        status,
        goal_id="nonblocking-user-action-fixture",
        agent_id=agent_id,
        available_capabilities=["shell"],
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "codex_app_heartbeat"
        ),
    )


def test_user_gate_notification_repeats_until_resolved() -> None:
    summary = {
        "open_count": 1,
        "gate_open_items": [
            {
                "todo_id": "todo_gate",
                "task_class": "user_gate",
                "action_kind": "approve_release",
            }
        ],
    }

    assert build_user_todo_notification(
        summary,
        state="operator_gate",
        waiting_on="controller",
    ) == {
        "notify_user_on_gate": True,
        "open_todo_notify_reason": (
            "open user_gate todo requires owner decision before approve_release"
        ),
        "open_todo_notification_policy": "repeat_until_resolved",
    }


def test_waiting_user_todo_notification_does_not_force_repeat_policy() -> None:
    assert build_user_todo_notification(
        {"open_count": 1},
        state="waiting",
        waiting_on="controller",
    ) == {
        "notify_user_on_open_todo": True,
        "open_todo_notify_reason": "open user todo can resolve the user/controller blocker",
    }


def test_waiting_user_action_remains_a_nonblocking_notice() -> None:
    user_action = {
        "todo_id": "todo_read_reference",
        "status": "open",
        "task_class": "user_action",
        "text": "Read the optional reference.",
    }

    assert build_user_todo_notification(
        {
            "open_count": 1,
            "user_action_open_count": 1,
            "first_open_items": [user_action],
            "user_action_items": [user_action],
        },
        state="waiting",
        waiting_on="controller",
    ) == {}


def test_user_action_summary_fails_closed_when_a_gate_is_also_visible() -> None:
    user_action = {
        "todo_id": "todo_read_reference",
        "status": "open",
        "task_class": "user_action",
        "text": "Read the optional reference.",
    }
    user_gate = {
        "todo_id": "todo_approve_release",
        "status": "open",
        "task_class": "user_gate",
        "text": "Approve the release.",
    }

    notification = build_user_todo_notification(
        {
            "open_count": 1,
            "all_open_count": 2,
            "user_action_open_count": 1,
            "first_open_items": [user_action],
            "gate_open_items": [user_gate],
        },
        state="waiting",
        waiting_on="controller",
    )

    assert notification["notify_user_on_gate"] is True


def test_user_action_notice_does_not_preempt_live_agent_delivery() -> None:
    guard = _quota_guard_for_user_todos(
        [
            quota_todo_item(
                todo_id="todo_read_reference",
                role="user",
                title="Read the optional reference.",
                task_class="user_action",
                bound_agent="codex-fixture",
            )
        ]
    )

    assert guard["requires_user_action"] is False
    assert guard["selected_todo"]["todo_id"] == "todo_delivery"
    contract = guard["interaction_contract"]
    assert contract["mode"] == "bounded_delivery"
    assert contract["user_channel"]["non_blocking"] is True
    assert contract["user_channel"]["notify"] == "NOTIFY"
    assert contract["agent_channel"]["must_attempt"] is True
    assert contract["agent_channel"]["delivery_allowed"] is True
    assert "response_plan" not in contract


def test_other_agent_todos_do_not_turn_current_notice_into_a_gate() -> None:
    current_action = quota_todo_item(
        todo_id="todo_read_reference",
        index=1,
        role="user",
        title="Read the optional reference.",
        task_class="user_action",
        bound_agent="codex-fixture",
    )
    foreign_todos = [
        quota_todo_item(
            todo_id="todo_peer_read_reference",
            index=2,
            role="user",
            title="Read the peer reference.",
            task_class="user_action",
            bound_agent="codex-peer",
        ),
        quota_todo_item(
            todo_id="todo_peer_approve_release",
            index=2,
            role="user",
            title="Approve the peer release.",
            task_class="user_gate",
            action_kind="approve_release",
            blocks_agent="codex-peer",
        ),
    ]

    for foreign_todo in foreign_todos:
        guard = _quota_guard_for_user_todos([current_action, foreign_todo])

        assert guard["requires_user_action"] is False
        assert guard["selected_todo"]["todo_id"] == "todo_delivery"
        contract = guard["interaction_contract"]
        assert contract["mode"] == "bounded_delivery"
        assert contract["agent_channel"]["delivery_allowed"] is True


def test_current_agent_gate_still_requires_user_action() -> None:
    guard = _quota_guard_for_user_todos(
        [
            quota_todo_item(
                todo_id="todo_read_reference",
                index=1,
                role="user",
                title="Read the optional reference.",
                task_class="user_action",
                bound_agent="codex-fixture",
            ),
            quota_todo_item(
                todo_id="todo_approve_release",
                index=2,
                role="user",
                title="Approve the release.",
                task_class="user_gate",
                action_kind="approve_release",
                blocks_agent="codex-fixture",
            ),
        ]
    )

    assert guard["requires_user_action"] is True
    assert guard["interaction_contract"]["user_channel"]["action_required"] is True


def test_incomplete_action_summary_fails_closed() -> None:
    notification = build_user_todo_notification(
        {
            "open_count": 2,
            "user_action_open_count": 1,
            "first_open_items": [
                {
                    "todo_id": "todo_read_reference",
                    "status": "open",
                    "task_class": "user_action",
                    "text": "Read the optional reference.",
                }
            ],
        },
        state="waiting",
        waiting_on="controller",
    )

    assert notification["notify_user_on_open_todo"] is True


def test_heartbeat_repeat_reason_owns_repeat_notification_policy() -> None:
    assert build_user_todo_notification(
        {"open_count": 1},
        state="eligible",
        waiting_on="",
        repeat_notification_required=True,
        repeat_notification_reason="repeat this owner action",
    ) == {
        "notify_user_on_open_todo": True,
        "open_todo_notify_reason": "repeat this owner action",
        "open_todo_notification_policy": "repeat_until_resolved",
    }


def test_closed_user_todo_summary_has_no_notification() -> None:
    assert (
        build_user_todo_notification(
            {"open_count": 0},
            state="waiting",
            waiting_on="",
        )
        == {}
    )


def test_scoped_user_gate_fallback_projects_executable_bypass() -> None:
    payload = {
        "decision": "skip",
        "effective_action": "monitor_quiet_skip",
        "should_run": False,
        "execution_obligation": {"source": "base"},
    }
    fallback = {
        "recommended_action": "advance the non-gated todo",
        "reason": "the user gate has a narrower action scope",
    }

    projected = apply_scoped_user_gate_fallback_projection(
        payload,
        fallback=fallback,
        replan_decision_allowed=False,
    )

    assert payload["decision"] == "skip"
    assert projected["decision"] == "safe_bypass_user_gate_fallback"
    assert projected["effective_action"] == "scoped_user_gate_fallback"
    assert projected["should_run"] is True
    assert projected["scoped_user_gate_fallback"] == fallback
    assert projected["safe_bypass_allowed"] is True
    assert projected["safe_bypass_kind"] == "scoped_user_gate_fallback"
    assert projected["actionable_by_codex"] is True
    assert projected["execution_obligation"] == {
        "source": "base",
        "must_attempt_work": True,
        "kind": "scoped_user_gate_fallback",
        "minimum": "one_non_gated_fallback_segment_after_user_gate_notice",
        "delivery_allowed": True,
        "notify_is_execution_gate": False,
        "contract": "scoped_user_gate_fallback",
        "contract_obligation": "advance the non-gated todo",
        "reason": "the user gate has a narrower action scope",
    }


def test_scoped_user_gate_fallback_does_not_override_replan() -> None:
    payload = {
        "decision": "autonomous_replan_required",
        "effective_action": "autonomous_replan_required",
        "should_run": True,
    }

    projected = apply_scoped_user_gate_fallback_projection(
        payload,
        fallback={"recommended_action": "advance the non-gated todo"},
        replan_decision_allowed=True,
    )

    assert projected is payload
    assert "scoped_user_gate_fallback" not in projected
