"""A raw refresh cannot authorize spend before its CLI receipt is committed."""

from __future__ import annotations

import json
from pathlib import Path

from loopx.control_plane.quota.effect_program import SettlementIdentity
from loopx.rollout_event_log import rollout_event_log_path
from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture
from loopx.state_refresh import refresh_state_run

GOAL_ID = "goal-owed-spend"
AGENT_ID = "agent-owed-spend"
TODO_ID = "todo_owed_spend"
TURN_ID = "turn-owed-spend"

STATE_TEXT = f"""# Active Goal State

## Agent Todo

- [ ] [P1] finish the owed spend signal
  <!-- loopx:todo status=open task_class=advancement_task claimed_by={AGENT_ID} todo_id={TODO_ID} -->
"""


def _append_guard_receipt(runtime_root: Path, identity: SettlementIdentity) -> None:
    path = rollout_event_log_path(runtime_root, GOAL_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_rollout_event_v0",
                "event_id": "event-owed-guard",
                "event_kind": "quota_should_run",
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "run_id": TURN_ID,
                "details": {
                    "todo_id": TODO_ID,
                    "settlement_effect_id": identity.effect_id,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    state_path = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(STATE_TEXT, encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_path.relative_to(project)),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
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
    initialize_canonical_source_fixture(tmp_path / "runtime", GOAL_ID, state_path=state_path)
    return registry_path, project, tmp_path / "runtime"


def test_raw_refresh_requires_receipt_before_offering_spend(tmp_path: Path) -> None:
    registry_path, project, runtime_root = _fixture(tmp_path)
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)
    _append_guard_receipt(runtime_root, identity)

    result = refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=GOAL_ID,
        project=project,
        state_file=None,
        classification="validated_progress",
        recommended_action=None,
        delivery_batch_scale="single_surface",
        delivery_outcome="outcome_progress",
        delivery_workspace_path=project,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
        agent_id=AGENT_ID,
        dry_run=False,
        sync_global=False,
    )

    assert result["ok"] is True
    assert result["appended"] is True
    assert result["settlement_progress"]["state"] == "writeback_receipt_required"
    assert result["settlement_progress"]["next_step"] == "durable_writeback"
    assert "settlement_owed" not in result
    assert "settlement_owed" not in refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=GOAL_ID,
        project=project,
        state_file=None,
        classification="validated_progress",
        recommended_action=None,
        delivery_batch_scale="single_surface",
        delivery_outcome="outcome_progress",
        delivery_workspace_path=project,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
        agent_id=AGENT_ID,
        dry_run=True,
        sync_global=False,
    )
