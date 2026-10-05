"""Regression tests for the heartbeat notify/execution-obligation wording.

The short notification rule used to read "DONT_NOTIFY=quiet", which agents can
misread as "do nothing". The rule must qualify DONT_NOTIFY as an output-level
signal only and keep `execution_obligation.must_attempt_work` as the authority
for whether work must run. Compact wording must preserve the repair action,
not just diagnose a missing user action, without turning repair into a prompt.
"""

from __future__ import annotations

import re

from loopx.control_plane.heartbeat.rules import (
    HEARTBEAT_NOTIFICATION_RULE_SHORT,
)
from loopx.control_plane.heartbeat.task_body import (
    render_ark_managed_agent_goal_task_body,
    render_brief_heartbeat_task_body,
    render_compact_heartbeat_task_body,
    render_heartbeat_task_body,
    render_thin_heartbeat_task_body,
    render_traex_visible_goal_task_body,
    render_visible_goal_task_body,
)
from loopx.control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.quota import build_quota_should_run


GOAL_ID = "heartbeat-notify-obligation-fixture"
AGENT_ID = "codex-notify-agent"
LANGUAGE_POLICY = (
    "Language=user; fallback=English; mix only if asked/scoped-bilingual."
)
LANGUAGE_POLICY_THIN = "Lang=user; default=en; mix=asked/scoped."


def test_short_rule_qualifies_dont_notify_as_output_only() -> None:
    rule = HEARTBEAT_NOTIFICATION_RULE_SHORT
    assert "DONT_NOTIFY" in rule
    assert "OUTPUT only" in rule
    # The old ambiguous mapping must be gone.
    assert "DONT_NOTIFY=quiet." not in rule
    assert "heartbeat_recommendation.agent_must_attempt" in rule
    assert "execution_obligation.must_attempt_work" in rule


def test_shared_rule_keeps_projection_repair_and_quiet_boundary() -> None:
    rule = HEARTBEAT_NOTIFICATION_RULE_SHORT
    assert "Missing NOTIFY action: user Todo unprojected" in rule
    assert "repair LoopX state projection" in rule
    assert "under DONT_NOTIFY repair internally" in rule
    assert "true=work+writeback" in rule
    assert "only false permits no-op" in rule


def test_rendered_task_bodies_keep_execution_obligation_authority() -> None:
    kwargs = dict(
        goal_id="fixture-goal",
        active_state="active",
        cli_preflight="",
        pr_review_pre_quota_command="",
        quota_guard_command="loopx quota should-run",
        quota_spend_command="",
        refresh_state_command="",
        progress_refresh_state_command="",
        material_queue_rule="",
        permission_rule="",
        cli_bin="loopx",
        agent_scope_instruction="",
        expanded_prompt_command="",
        compact_prompt_command="",
        brief_prompt_command="",
        thin_prompt_command="",
    )
    for renderer in (render_thin_heartbeat_task_body, render_brief_heartbeat_task_body):
        body = renderer(**kwargs)
        assert "heartbeat_recommendation.agent_must_attempt" in body
        assert "execution_obligation.must_attempt_work" in body
        assert "OUTPUT only" in body
        assert "repair LoopX state projection" in body
        assert "under DONT_NOTIFY repair internally" in body
        # A bare "DONT_NOTIFY=quiet" no-op mapping must never appear in the prompt.
        assert "DONT_NOTIFY=quiet." not in body
        assert "Only after committed writeback: same-turn checkpoint-context" in body
        assert "Missing vision: same-turn checkpoint-context" not in body


def test_generic_task_bodies_follow_user_language_without_forcing_chinese() -> None:
    kwargs = dict(
        goal_id="fixture-goal",
        active_state="active",
        cli_preflight="",
        pr_review_pre_quota_command="",
        quota_guard_command="loopx quota should-run",
        quota_spend_command="loopx quota spend-slot",
        refresh_state_command="loopx refresh-state",
        progress_refresh_state_command="loopx refresh-state --classification delivery",
        material_queue_rule="",
        permission_rule="",
        cli_bin="loopx",
        agent_scope_instruction="",
        expanded_prompt_command="loopx heartbeat-prompt",
        compact_prompt_command="loopx heartbeat-prompt --compact",
        brief_prompt_command="loopx heartbeat-prompt --brief",
        thin_prompt_command="loopx heartbeat-prompt --thin",
    )
    renderers = (
        render_heartbeat_task_body,
        render_compact_heartbeat_task_body,
        render_brief_heartbeat_task_body,
        render_thin_heartbeat_task_body,
        render_visible_goal_task_body,
        render_traex_visible_goal_task_body,
        render_ark_managed_agent_goal_task_body,
    )

    for renderer in renderers:
        body = renderer(**kwargs)
        expected_policy = (
            LANGUAGE_POLICY_THIN
            if renderer in (render_brief_heartbeat_task_body, render_thin_heartbeat_task_body)
            else LANGUAGE_POLICY
        )
        assert expected_policy in body, renderer.__name__
        assert "Chinese action" not in body, renderer.__name__
        assert "concrete Chinese" not in body, renderer.__name__
        assert re.search(r"[\u3400-\u9fff]", body) is None, renderer.__name__


def test_language_policy_preserves_blocker_and_native_goal_continuation() -> None:
    kwargs = dict(
        goal_id="fixture-goal",
        active_state="active",
        cli_preflight="",
        pr_review_pre_quota_command="",
        quota_guard_command="loopx quota should-run",
        quota_spend_command="loopx quota spend-slot",
        refresh_state_command="loopx refresh-state",
        progress_refresh_state_command="loopx refresh-state --classification delivery",
        material_queue_rule="",
        permission_rule="",
        cli_bin="loopx",
        agent_scope_instruction="",
        expanded_prompt_command="loopx heartbeat-prompt",
        compact_prompt_command="loopx heartbeat-prompt --compact",
        brief_prompt_command="loopx heartbeat-prompt --brief",
        thin_prompt_command="loopx heartbeat-prompt --thin",
    )
    compact = render_compact_heartbeat_task_body(**kwargs)
    assert "Goal-owned blocker: stop its path" in compact
    assert "blocker-push in the user's language" in compact
    assert "Dependency/sibling todos: record; continue audit" in compact
    assert "must_attempt_work=true` requires work even with" in compact

    brief = " ".join(render_brief_heartbeat_task_body(**kwargs).split())
    assert "exact identity/effect order" in brief
    assert "External wait: open + monitor_changed + successor" in brief
    assert "Finish only on terminal no-follow-up" in brief

    for renderer in (
        render_visible_goal_task_body,
        render_traex_visible_goal_task_body,
        render_ark_managed_agent_goal_task_body,
    ):
        body = renderer(**kwargs)
        assert "After settlement recheck quota" in body, renderer.__name__
        assert "should_run=false` + terminal no-follow-up" in body, renderer.__name__
        assert "otherwise obey the next action or wait guidance" in body, renderer.__name__


def test_heartbeat_recommendation_mirrors_execution_obligation_in_replan() -> None:
    obligation = {
        "schema_version": "autonomous_replan_obligation_v0",
        "required": True,
        "stall_threshold": 2,
        "trigger_count": 1,
        "triggers": [
            {
                "kind": "periodic_review_due",
                "source": "run_history",
                "agent_id": AGENT_ID,
            }
        ],
        "stop_condition": (
            "stop after one bounded replan slice writes back a concrete frontier delta"
        ),
    }
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Advance the current lane.",
        agent_todos=quota_todo_summary(
            [
                quota_todo_item(
                    todo_id="todo_advance",
                    index=1,
                    title="Advance the current lane.",
                    task_class="advancement_task",
                    claimed_by=AGENT_ID,
                )
            ]
        ),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
        project_asset_extra={"autonomous_replan_obligation": obligation},
    )
    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )
    assert guard["decision"] == "autonomous_replan_required"
    heartbeat = guard["heartbeat_recommendation"]
    obligation = guard["execution_obligation"]
    assert heartbeat.get("notify") == "NOTIFY"
    user_channel = (
        (guard.get("interaction_contract") or {}).get("user_channel") or {}
    )
    assert user_channel.get("notify") == "NOTIFY"
    assert heartbeat.get("agent_must_attempt") is True
    assert heartbeat["agent_must_attempt"] is bool(
        obligation.get("must_attempt_work")
    )


def test_scheduler_guidance_does_not_freeze_bootstrap_cadence() -> None:
    from loopx.control_plane.heartbeat.rules import (
        SCHEDULER_HINT_APPLICATION_RULE, SCHEDULER_HINT_COMPACT_RULE, SCHEDULER_HINT_THIN_RULE,
    )

    for rule in (SCHEDULER_HINT_APPLICATION_RULE, SCHEDULER_HINT_COMPACT_RULE, SCHEDULER_HINT_THIN_RULE):
        assert "floor" in rule and "backoff" in rule
        assert "automation_update" in rule and "ack" in rule
