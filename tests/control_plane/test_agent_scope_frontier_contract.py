"""A frontier owns one action slot; quota actions cannot overwrite its domain."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from canonical_authority_fixture import advance_canonical_fixture_authority, read_fixture_todos
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

from examples.control_plane.quota_plan_fixtures import (
    SCOPED_AGENT_ID,
    write_cli_fixture,
)
from loopx.control_plane.agents.agent_scope_frontier import (
    AgentScopeFrontierAction,
    build_agent_scope_frontier_payload,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)
from loopx.control_plane.work_items.goal_route_hint import build_goal_route_hint
from loopx.quota import build_quota_should_run, render_quota_should_run_markdown


def _frontier(action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT, *, extra_fields=None):
    return build_agent_scope_frontier_payload(
        agent_id="frontier-fixture",
        action=action,
        quiet_noop_allowed=True,
        spend_policy="no spend while waiting",
        reason="no runnable candidate",
        recommended_action="wait for the prerequisite",
        candidate_counts={},
        extra_fields=extra_fields,
    )


@pytest.mark.parametrize("action", list(AgentScopeFrontierAction))
def test_frontier_has_one_canonical_action_slot(action):
    payload = _frontier(action)
    assert payload["schema_version"] == "agent_scope_frontier_v1"
    assert payload["action"] == action.value
    assert "effective_action" not in payload


@pytest.mark.parametrize(
    "extra",
    [
        {"action": "normal_run"},
        {"effective_action": "normal_run"},
        {"schema_version": "agent_scope_frontier_v0"},
        {"action": None},
        {"effective_action": "agent_scope_wait"},
        {"schema_version": "agent_scope_frontier_v1"},
    ],
)
def test_extra_fields_cannot_replace_the_frontier_domain(extra):
    with pytest.raises(ValueError, match="frontier.*reserved"):
        _frontier(extra_fields=extra)


def test_frontier_keeps_bounded_candidate_context():
    payload = _frontier(extra_fields={"priority_preemption": True})
    assert payload["priority_preemption"] is True


@pytest.mark.parametrize(
    "frontier, expected",
    [
        (
            {
                "schema_version": "agent_scope_frontier_v0",
                "effective_action": "agent_scope_wait",
            },
            "agent_scope_wait",
        ),
        (
            {
                "schema_version": "agent_scope_frontier_v0",
                "action": "agent_scope_wait",
                "effective_action": "agent_scope_wait",
            },
            "agent_scope_wait",
        ),
        (
            {
                "schema_version": "agent_scope_frontier_v1",
                "action": "successor_replan_required",
            },
            "successor_replan_required",
        ),
        (
            {
                "schema_version": "agent_scope_frontier_v1",
                "action": "successor_replan_required",
                "effective_action": "agent_scope_wait",
            },
            "successor_replan_required",
        ),
    ],
)
def test_goal_route_reads_canonical_action_first_and_legacy_as_fallback(
    frontier, expected
):
    hint = build_goal_route_hint(
        agent_identity={"agent_id": "frontier-fixture"},
        agent_todo_summary={},
        agent_lane_next_action=None,
        agent_scope_frontier=frontier,
        agent_lane_frontier_hint=None,
        active_state_next_action="Preserve the goal route.",
        latest_run_recommended_action=None,
        selected_recommended_action="Wait.",
    )
    assert hint["route_decision"] == expected
    assert hint["preserves_goal_next_action"] is True
    assert hint["goal_next_action_mutation"] == "none"


def test_live_should_run_and_markdown_use_the_v1_frontier():
    status = quota_status_payload(
        goal_id="frontier-fixture",
        status="active",
        recommended_action="Complete the claimed prerequisite.",
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": ["current-agent", "other-agent"],
        },
        agent_todo_items=[
            quota_todo_item(
                todo_id="todo_prerequisite",
                title="Complete the claimed prerequisite.",
                claimed_by="other-agent",
            )
        ],
    )
    guard = build_quota_should_run(
        status, goal_id="frontier-fixture", agent_id="current-agent"
    )
    frontier = guard["agent_scope_frontier"]
    assert frontier["schema_version"] == "agent_scope_frontier_v1"
    assert frontier["action"] == "reassignment_required"
    assert "effective_action" not in frontier
    assert guard["effective_action"] == "reassignment_required"
    assert guard["decision"] == "reassignment_required"
    assert guard["should_run"] is False
    assert guard["normal_delivery_allowed"] is False
    assert guard["goal_route_hint"]["route_decision"] == "reassignment_required"
    assert (
        "agent_scope_frontier: action=reassignment_required"
        in render_quota_should_run_markdown(guard)
    )


def test_cli_should_run_reads_disposable_state_and_emits_v1(tmp_path):
    registry, runtime, project = write_cli_fixture(tmp_path, scoped_agents=True)
    state = project / ".codex/goals/half-speed/ACTIVE_GOAL_STATE.md"
    with state.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n"
            "- [ ] [P0] Complete the claimed prerequisite.\n"
            "  <!-- loopx:todo todo_id=todo_prerequisite status=open "
            "task_class=advancement_task claimed_by=codex-main-control -->\n"
        )
    advance_canonical_fixture_authority(runtime, "half-speed",
        build_todo_runtime_shadow_projection(goal_id="half-speed", todos=read_fixture_todos(state),
                                             handoff_mode="soft_claim"),
        operation_id="source-prerequisite-fixture")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "quota",
            "should-run",
            "--goal-id",
            "half-speed",
            "--agent-id",
            SCOPED_AGENT_ID,
            "--runtime-profile",
            "outer_controller",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    )
    guard = json.loads(result.stdout)
    assert guard["effective_action"] == "reassignment_required"
    assert guard["should_run"] is False
    assert guard["agent_scope_frontier"]["schema_version"] == "agent_scope_frontier_v1"
    assert guard["agent_scope_frontier"]["action"] == "reassignment_required"
    assert "effective_action" not in guard["agent_scope_frontier"]
