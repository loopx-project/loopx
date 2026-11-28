"""Fenced takeover for a Todo completed under a reclaimed lease before guard reentry."""
from __future__ import annotations

import json
import shlex

import pytest

from loopx.control_plane.quota.execution_preparation import (
    RefreshTaskLeaseUnavailable,
    guard_first_writeback_execution,
)
from tests.control_plane.test_execution_loss_closeout_cli import (
    OWNER, TURN, _fixture, _guard_args, _readback, _receipt_lines, _spend_run_count,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_completed_todo_under_reclaimed_lease_requires_guard_takeover_then_settles_once(
    tmp_path, monkeypatch, capsys, provider,
):
    """When completion ran under a reclaimed generation before guard takeover,
    direct refresh-state still fails closed, while rerunning the original guard
    records a fenced completed_lease_takeover receipt and settles once."""
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    cli = context["cli"]
    code, completed = cli(
        "todo", "complete", "--goal-id", context["goal_id"],
        "--agent-id", OWNER, "--todo-id", context["todo"], "--turn-instance-id", TURN,
        "--claim-operation-id", context["current_claim"]["claim_operation_id"],
        "--evidence", "evidence:completed-before-takeover",
    )
    assert code == 0 and completed["ok"], completed
    assert (context["project"] / "completion-validator-ran").read_text() == "ran"

    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Settle completed work after fenced retired-lease takeover."},
        "path_delta": {"outcome": "continue", "prior_assumption": "Original lease expired mid-Turn.",
                       "observed_reality": "Completion committed under the owner's reclaimed lease.",
                       "retained": ["Original task"], "evidence_refs": ["evidence:completed-before-takeover"]},
    }))
    direct_refresh = [
        "refresh-state", "--goal-id", context["goal_id"], "--agent-id", OWNER,
        "--todo-id", context["todo"], "--turn-instance-id", TURN,
        "--classification", "revalidated_result", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--delivery-boundary", "semantic_closeout",
        "--agent-vision-json", str(vision), "--settle-turn", "--settlement-receipt",
        "--no-global-sync", "--suppress-external-sinks",
    ]
    # Before running the original guard, direct writeback stays fenced.
    code, denied = cli(*direct_refresh)
    assert code == 1 and denied["appended"] is False, denied
    assert denied["error_code"] == "refresh_task_lease_unavailable", denied
    assert _spend_run_count(context["runtime"]) == 0

    before_takeover_lines = _receipt_lines(context)
    code, decision = cli(*_guard_args(context))
    assert code == 0 and decision["ok"] is True, decision
    assert decision["unsettled_host_turn_recovery"]["repair"] == "durable_writeback", decision
    assert decision["heartbeat_receipt"]["status"] == "upgraded", decision["heartbeat_receipt"]
    assert decision["heartbeat_receipt"]["lease_takeover"]["original_lease_epoch"] == (
        context["original_lease"]["lease_epoch"]
    )
    assert decision["heartbeat_receipt"]["lease_takeover"]["current_lease_epoch"] == (
        context["current_lease"]["lease_epoch"]
    )
    assert decision["heartbeat_receipt"]["lease_takeover"]["completion_event_id"]

    readback = _readback(context)
    assert readback is not None
    assert readback.heartbeat_receipt["details"]["settlement_receipt_revision"] == "completed_lease_takeover"
    assert readback.prepared_lease["prepared_lease_epoch"] == context["current_lease"]["lease_epoch"]
    after_takeover_lines = _receipt_lines(context)
    assert after_takeover_lines[:len(before_takeover_lines)] == before_takeover_lines
    assert len(after_takeover_lines) == len(before_takeover_lines) + 1
    assert _spend_run_count(context["runtime"]) == 0

    # Replaying the guard is idempotent and appends no second takeover receipt.
    code, replayed_guard = cli(*_guard_args(context))
    assert code == 0 and replayed_guard["ok"] is True, replayed_guard
    assert replayed_guard["heartbeat_receipt"]["status"] == "replayed", replayed_guard["heartbeat_receipt"]
    assert _receipt_lines(context) == after_takeover_lines

    # Stale original-generation proof remains rejected even after completed takeover.
    stale_proof = {
        "idempotency_key": context["original_lease"]["idempotency_key"],
        "expected_version": context["original_lease"]["version"],
    }
    with pytest.raises(RefreshTaskLeaseUnavailable):
        with guard_first_writeback_execution(
            runtime_root=context["runtime"],
            registry_path=context["registry"],
            goal_id=context["goal_id"],
            todo_id=context["todo"],
            agent_id=OWNER,
            requested_proof=stale_proof,
            settlement=readback,
            locked_goal=None,
        ):
            pass

    plan = decision["interaction_contract"]["cli_channel"]["settlement_plan"]
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    tokens = shlex.split(writeback["semantic_closeout_command_template"])
    replacements = {"<validated_progress>": "revalidated_result", "<scale>": "implementation",
                    "<outcome>": "outcome_progress", "<file>": str(vision)}
    args = [replacements.get(token, token) for token in tokens[tokens.index("refresh-state"):]]
    args += ["--no-global-sync", "--suppress-external-sinks"]

    # The guard-returned writeback command settles the original Turn once.
    code, result = cli(*args)
    assert code == 0 and result["settlement_progress"]["state"] == "settled", result
    assert result["settlement_identity"] == plan["identity"]
    assert result["settlement_identity"]["turn_instance_id"] == TURN
    assert _spend_run_count(context["runtime"]) == 1

    code, replay = cli(*args)
    assert code == 0 and replay["idempotent_replay"], replay
    assert _spend_run_count(context["runtime"]) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unprepared_turn_cannot_bypass_admission_via_completed_lease_takeover(
    tmp_path, monkeypatch, capsys, provider,
):
    """A Turn that never obtained prepared_lease stays refused after completion."""
    context = _fixture(
        tmp_path, monkeypatch, capsys, provider, prepared=False, replace=False, source_profile=False,
    )
    cli = context["cli"]
    code, claimed = cli(
        "todo", "claim", "--goal-id", context["goal_id"],
        "--todo-id", context["todo"], "--agent-id", OWNER, "--claimed-by", OWNER,
    )
    assert code == 0 and claimed["ok"], claimed
    code, completed = cli(
        "todo", "complete", "--goal-id", context["goal_id"],
        "--agent-id", OWNER, "--todo-id", context["todo"], "--turn-instance-id", TURN,
        "--claim-operation-id", claimed["claim_operation_id"],
        "--evidence", "evidence:unprepared-turn-completion",
    )
    assert code == 0 and completed["ok"], completed

    before_lines = _receipt_lines(context)
    code, guard = cli(*_guard_args(context))
    assert code == 0 and guard["ok"] is True, guard
    assert _receipt_lines(context) == before_lines
    readback = _readback(context)
    assert readback is not None and readback.prepared_lease is None

    code, denied = cli(
        "refresh-state", "--goal-id", context["goal_id"], "--agent-id", OWNER,
        "--todo-id", context["todo"], "--turn-instance-id", TURN,
        "--classification", "unprepared_completion", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--settle-turn", "--settlement-receipt",
        "--no-global-sync", "--suppress-external-sinks",
    )
    assert code == 1 and denied["appended"] is False, denied
    assert denied["error_code"] == "refresh_task_lease_unavailable", denied
    assert _spend_run_count(context["runtime"]) == 0
