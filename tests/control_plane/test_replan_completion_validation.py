"""Semantic replans must not be mistaken for Todo completion.

Controller-declared completion validation protects the terminal Todo
transition.  A qualified, evidence-linked path replan keeps that Todo open and
must still be able to settle its exact Turn; otherwise the path change needed
to reach validation is circularly blocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.quota.settlement_validation import (
    completion_validation_spend_error,
)
from loopx.control_plane.todos.completion_validation_accountability import (
    require_accountable_completion_validation,
)
from loopx.control_plane.turn_driver.delivery_continuity import (
    DELIVERY_BOUNDARY_SEMANTIC_CLOSEOUT,
)
from test_quota_settlement_cli import (
    AGENT_ID,
    ALTERNATIVE_TODO_ID,
    GOAL_ID,
    TODO_ID,
    _configure_completion_validation_todo,
    _configure_selectable_alternative,
    _run_cli,
    _spend_run_count,
    _write_fixture,
)


def _validation_todo() -> dict[str, object]:
    return {
        "todo_id": TODO_ID,
        "status": "open",
        "claimed_by": AGENT_ID,
        "completion_validation_required": True,
    }


def _vision_path(path: Path) -> Path:
    packet = {
        "schema_version": "goal_vision_replan_contract_v0",
        "agent_id": AGENT_ID,
        "state": "active",
        "vision_patch": {
            "vision_summary": "Use the evidence-backed successor path.",
            "acceptance_summary": "Validate the successor before Todo completion.",
            "replan_trigger_summary": "The prior path cannot reach validation.",
        },
        "todo_delta": [],
        "path_delta": {
            "schema_version": "goal_path_delta_v0",
            "outcome": "replan",
            "prior_assumption": "The selected path could reach terminal validation.",
            "observed_reality": "A control-plane fence blocks that path before validation.",
            "retained": ["Controller-declared terminal completion validation."],
            "changed": ["Use the independently validated successor path."],
            "stopped": ["Retrying the fenced writeback without a path change."],
            "evidence_refs": ["evidence:replan-validation-fence"],
        },
    }
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path


@pytest.mark.parametrize("waiting", [False, True])
def test_qualified_path_replan_settles_without_completing_validation_todo(
    tmp_path: Path,
    waiting: bool,
) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    state_path = _configure_completion_validation_todo(project)
    _configure_selectable_alternative(project)
    initial_state = state_path.read_text(encoding="utf-8")
    turn_id = "turn-validation-bearing-replan"

    guard_rc, guard = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_id,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID

    refresh_rc, refresh = _run_cli(
        registry,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "evidence_linked_path_replan",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-boundary",
        DELIVERY_BOUNDARY_SEMANTIC_CLOSEOUT,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_id,
        "--autonomous-replan-recorded",
        "--repair-delta-kind",
        "goal_vision_patch",
        "--agent-vision-json",
        str(_vision_path(tmp_path / "vision.json")),
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert refresh_rc == 0, refresh
    assert refresh["settlement_result"]["ok"] is True
    persisted = json.loads(
        Path(refresh["json_path"]).read_text(encoding="utf-8")
    )
    assert persisted["autonomous_replan_ack"]["recorded"] is True
    assert persisted["autonomous_replan_ack"]["delta_contract"][
        "delta_present"
    ] is True
    assert persisted["agent_vision"]["path_delta"]["outcome"] == "replan"
    assert state_path.read_text(encoding="utf-8") == initial_state

    spend_args = (
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_id,
        "--scan-path",
        str(project),
    )
    spend_rc, spend = _run_cli(registry, runtime, *spend_args)
    assert spend_rc == 0, spend
    assert spend["appended"] is True
    assert spend["settlement_result"]["ok"] is True
    replay_rc, replay = _run_cli(registry, runtime, *spend_args)
    assert replay_rc == 0, replay
    assert replay["idempotent_replay"] is True
    assert _spend_run_count(runtime) == 1

    if waiting:
        rc, updated = _run_cli(
            registry, runtime, "todo", "update", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--status", "open",
            "--resume-when", "resume_at:2099-01-01T00:00:00Z",
            "--successor-todo-id", ALTERNATIVE_TODO_ID,
            "--reason", "Wait for independent evidence on the revised path.",
        )
        assert rc == 0, updated

    guard_args = (
        "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--scan-path", str(project),
    )
    for selection in ((), ("--todo-id", TODO_ID)):
        rc, replay = _run_cli(
            registry, runtime, *guard_args,
            "--turn-instance-id", turn_id, *selection,
        )
        assert rc == 0, replay
        assert replay["effective_action"] == "heartbeat_settled_skip", replay
        assert replay["should_run"] is False
        assert replay["execution_obligation"]["must_attempt_work"] is False
        assert replay["interaction_contract"]["cli_channel"]["spend_after_validation"] is False

    rc, fresh = _run_cli(
        registry, runtime, *guard_args, "--turn-instance-id", f"{turn_id}-next",
    )
    assert rc == 0, fresh
    assert fresh["should_run"] is True
    assert fresh["effective_action"] != "unsettled_host_turn_recovery", fresh
    assert fresh["selected_todo"]["todo_id"] == (ALTERNATIVE_TODO_ID if waiting else TODO_ID)
    rc, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    todo = next(item for item in listed["todos"] if item["todo_id"] == TODO_ID)
    assert todo["status"] == "open"
    assert todo["completion_validation_required"] is True
    assert _spend_run_count(runtime) == 1


def test_unqualified_primary_outcome_keeps_completion_and_spend_fences() -> None:
    todo_fields = {"agent_todos": {"items": [_validation_todo()]}}
    with pytest.raises(ValueError, match="completion validation"):
        require_accountable_completion_validation(
            "",
            todo_id=TODO_ID,
            agent_id=AGENT_ID,
            todo_fields=todo_fields,
            delivery_boundary=DELIVERY_BOUNDARY_SEMANTIC_CLOSEOUT,
            delivery_outcome="primary_goal_outcome",
            semantic_replan_recorded=False,
        )

    zero_effect_run = {
        "todo_id": TODO_ID,
        "settlement_identity": {"todo_id": TODO_ID},
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": False,
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": False,
            },
        },
    }
    wrong_identity_run = {
        "todo_id": "todo_other_settlement",
        "settlement_identity": {"todo_id": "todo_other_settlement"},
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
            },
        },
    }
    expected = (
        "quota spend is blocked until controller-declared completion "
        f"validation durably completes todo {TODO_ID}"
    )
    for delivery_run in (zero_effect_run, wrong_identity_run):
        assert completion_validation_spend_error(
            {"attention_queue": {"items": []}},
            goal_id=GOAL_ID,
            todo_id=TODO_ID,
            agent_id=AGENT_ID,
            selected_todo=_validation_todo(),
            delivery_run=delivery_run,
        ) == expected
