from __future__ import annotations

import json
from pathlib import Path

from loopx.control_plane.quota.effect_program import SettlementIdentity
from loopx.control_plane.work_items.refresh_recommendation import (
    RECOMMENDED_ACTION_SOURCE_DEFAULT,
    RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO,
    resolve_refresh_recommendation,
)
from loopx.rollout_event_log import rollout_event_log_path
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture
from loopx.state_refresh import (
    RECOMMENDED_ACTION_SOURCE_ACTIVE_NEXT_ACTION,
    RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO,
    refresh_state_run,
)

def _recommendation(state_text, *, agent_id=None, todo_fields):
    result = resolve_refresh_recommendation(
        state_text, agent_id=agent_id, todo_fields=todo_fields,
    )
    return result["recommended_action"], result["recommended_action_source"]


TWO_AGENT_STATE = """# Active Goal State

## Next Action

- Publish agent B's release checklist

## Agent Todo

- [ ] [P1] continue agent A scheduler coverage fix
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-a todo_id=todo_agent_a -->
- [ ] [P1] polish agent B release notes
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-b todo_id=todo_agent_b -->

## Completed Work Archive
"""


def test_agent_scoped_derivation_prefers_own_lane_todo() -> None:
    action, source = _recommendation(
        TWO_AGENT_STATE, agent_id="agent-a"
    , todo_fields=parse_active_state_todos(TWO_AGENT_STATE, item_limit=None))
    assert action == "[P1] continue agent A scheduler coverage fix"
    assert source == RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO


def test_peer_lane_never_shadows_own_selection() -> None:
    action, source = _recommendation(
        TWO_AGENT_STATE, agent_id="agent-b"
    , todo_fields=parse_active_state_todos(TWO_AGENT_STATE, item_limit=None))
    assert action == "[P1] polish agent B release notes"
    assert source == RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO


def test_agent_without_claimed_todo_falls_back_to_shared_section() -> None:
    action, source = _recommendation(
        TWO_AGENT_STATE, agent_id="agent-c"
    , todo_fields=parse_active_state_todos(TWO_AGENT_STATE, item_limit=None))
    assert action == "Publish agent B's release checklist"
    assert source == RECOMMENDED_ACTION_SOURCE_ACTIVE_NEXT_ACTION


def test_unscoped_derivation_keeps_shared_section_priority() -> None:
    action, source = _recommendation(TWO_AGENT_STATE, todo_fields=parse_active_state_todos(TWO_AGENT_STATE, item_limit=None))
    assert action == "Publish agent B's release checklist"
    assert source == RECOMMENDED_ACTION_SOURCE_ACTIVE_NEXT_ACTION


def test_blocked_p0_never_shadows_runnable_p1() -> None:
    state = """# Active Goal State

## Next Action

- Keep the shared fallback.

## Agent Todo

- [ ] [P0] blocked delivery
  <!-- loopx:todo status=blocked task_class=advancement_task claimed_by=agent-a todo_id=todo_blocked_delivery -->
- [ ] [P1] runnable delivery
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-a todo_id=todo_runnable_delivery -->
"""

    action, source = _recommendation(state, agent_id="agent-a", todo_fields=parse_active_state_todos(state, item_limit=None))

    assert action == "[P1] runnable delivery"
    assert source == RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO


def test_agent_scope_without_own_candidate_never_falls_into_peer_lane() -> None:
    state = """# Active Goal State

## Agent Todo

- [ ] [P0] peer-only delivery
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-b todo_id=todo_peer_delivery -->
"""

    action, source = _recommendation(state, agent_id="agent-a", todo_fields=parse_active_state_todos(state, item_limit=None))

    assert "peer-only" not in action
    assert source == RECOMMENDED_ACTION_SOURCE_DEFAULT


def test_exact_settlement_todo_outranks_newer_lane_priority() -> None:
    identity = SettlementIdentity(
        goal_id="goal-shared",
        agent_id="agent-a",
        todo_id="todo_agent_a",
        turn_instance_id="turn-agent-a",
    )
    state = TWO_AGENT_STATE.replace(
        "## Completed Work Archive",
        """- [ ] [P0] newer agent A work
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-a todo_id=todo_agent_a_new -->

## Completed Work Archive""",
    )

    result = resolve_refresh_recommendation(
        state,
        agent_id="agent-a",
        settlement_identity=identity.as_dict(),
     todo_fields=parse_active_state_todos(state, item_limit=None))

    assert (
        result["recommended_action"] == "[P1] continue agent A scheduler coverage fix"
    )
    assert (
        result["recommended_action_source"]
        == RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO
    )
    assert result["settlement_alignment"] == "exact"
    assert result["todo_id"] == "todo_agent_a"


def test_missing_settlement_todo_reports_typed_gap_before_lane_fallback() -> None:
    identity = SettlementIdentity(
        goal_id="goal-shared",
        agent_id="agent-a",
        todo_id="todo_removed_selection",
        turn_instance_id="turn-agent-a",
    )

    result = resolve_refresh_recommendation(
        TWO_AGENT_STATE,
        agent_id="agent-a",
        settlement_identity=identity.as_dict(),
     todo_fields=parse_active_state_todos(TWO_AGENT_STATE, item_limit=None))

    assert (
        result["recommended_action"] == "[P1] continue agent A scheduler coverage fix"
    )
    assert result["settlement_alignment"] == "unavailable"
    assert result["settlement_gap_reason"] == "candidate_missing"


def test_unclaimed_lane_candidate_exposes_claim_prerequisite() -> None:
    state = """# Active Goal State

## Next Action

- Shared compatibility fallback.

## Agent Todo

- [ ] [P1] claim and continue this slice
  <!-- loopx:todo status=open task_class=advancement_task todo_id=todo_unclaimed_slice -->
"""

    result = resolve_refresh_recommendation(state, agent_id="agent-a", todo_fields=parse_active_state_todos(state, item_limit=None))

    assert result["recommended_action"] == "[P1] claim and continue this slice"
    assert (
        result["recommended_action_source"]
        == RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO
    )
    assert result["claim_required_before_work"] is True


def _write_turn_receipt(
    runtime_root: Path,
    *,
    identity: SettlementIdentity,
) -> None:
    path = rollout_event_log_path(runtime_root, identity.goal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_rollout_event_v0",
                "event_id": "event-agent-a-turn",
                "event_kind": "quota_should_run",
                "goal_id": identity.goal_id,
                "agent_id": identity.agent_id,
                "run_id": identity.turn_instance_id,
                "details": {
                    "todo_id": identity.todo_id,
                    "settlement_effect_id": identity.effect_id,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_refresh_state_run_reuses_exact_turn_selection(tmp_path: Path) -> None:
    goal_id = "goal-shared-refresh"
    project = tmp_path / "project"
    state_path = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(TWO_AGENT_STATE, encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": goal_id,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_path.relative_to(project)),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["agent-a", "agent-b"],
                        },
                        "workspace_guard_policy": {
                            "peer_independent_worktree_required": False,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    initialize_canonical_source_fixture(runtime_root, goal_id, state_path=state_path)
    identity = SettlementIdentity(
        goal_id=goal_id,
        agent_id="agent-a",
        todo_id="todo_agent_a",
        turn_instance_id="turn-agent-a",
    )
    _write_turn_receipt(runtime_root, identity=identity)

    result = refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=goal_id,
        project=project,
        state_file=None,
        classification="validated_progress",
        recommended_action=None,
        delivery_batch_scale="single_surface",
        delivery_outcome="outcome_progress",
        delivery_workspace_path=project,
        todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id,
        agent_id=identity.agent_id,
        dry_run=True,
        sync_global=False,
    )

    assert (
        result["recommended_action"] == "[P1] continue agent A scheduler coverage fix"
    )
    assert (
        result["recommended_action_source"]
        == RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO
    )
    assert result["recommended_action_resolution"] == {
        "schema_version": "refresh_recommendation_v0",
        "recommended_action": "[P1] continue agent A scheduler coverage fix",
        "recommended_action_source": "settlement_bound_todo",
        "authority": "settlement",
        "settlement_alignment": "exact",
        "todo_id": "todo_agent_a",
        "selection_binding": "heartbeat_receipt",
        "claim_required_before_work": False,
    }
