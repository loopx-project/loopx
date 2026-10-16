"""A bounded display must not strand an admitted lifecycle recovery."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from test_replan_successor_guard_reentry import AGENT, GOAL, _fixture, _guard


@pytest.mark.parametrize("new_work", ["after_closeout", "before_closeout", "bound_before_closeout"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("later_user_gate", [False, True])
@pytest.mark.parametrize("large_history", [False, True])
def test_hidden_completed_source_recovers_its_original_guard_after_new_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, later_user_gate: bool,
    large_history: bool, new_work: str,
) -> None:
    original_id = "todo_original_lifecycle"
    completion_key = "local_completion_" + sha256(
        f"loopx-local-todo-completion-v0\0{GOAL}\0{original_id}".encode()
    ).hexdigest()[:32]
    completed = {
        "schema_version": "todo_item_v0", "archive_state": "active", "source_section": "Agent Todo",
        "todo_id": original_id, "role": "agent", "index": 1,
        "text": "Record the independently accepted maintenance outcome.",
        "status": "done", "done": True, "task_class": "advancement_task",
        "claimed_by": AGENT, "last_actor_agent_id": AGENT,
        "note": "The finite maintenance outcome was independently accepted.",
        "completed_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z",
        "completion_turn_key": completion_key, "completion_continuation": "active_goal",
    }
    # Enough newer terminal rows to move the original outside display budgets.
    recent = [{**completed, "todo_id": f"todo_later_{i}", "index": i + 2,
        "text": f"Accepted independent source {i}.", "no_followup": True,
        "note": "Synthetic retained evidence. " + ("x" * 96_000 if large_history else ""),
        "completion_continuation": "no_followup", "completion_turn_key": f"later-{i}",
        "completed_at": "2026-08-02T00:00:00Z", "updated_at": "2026-08-02T00:00:00Z"}
        for i in range(24)]
    watch = {"schema_version": "todo_item_v0", "archive_state": "active", "source_section": "Agent Todo",
        "todo_id": "todo_future_watch", "role": "agent", "index": 30,
        "text": "Observe the pending independent source.", "status": "open", "done": False,
        "task_class": "continuous_monitor", "claimed_by": AGENT,
        "action_kind": "monitor", "target_key": "independent-source",
        "cadence": "30m", "next_due_at": "2099-01-01T00:00:00Z",
        "expires_at": "2099-01-02T00:00:00Z"}
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider,
        initial_todos=[completed, *recent, watch])
    index.write_text("")
    turn = "turn-admitted-lifecycle-recovery"
    admitted = _guard(call, turn)
    identity = admitted["heartbeat_receipt"]["settlement_identity"]
    obligation = admitted["autonomous_replan_obligation"]
    assert obligation["resolution_mode"] == "todo_lifecycle_settlement"
    assert [row["todo_id"] for row in obligation["triggers"]] == [original_id]

    # Independent new work cannot discharge the admitted lifecycle.
    def add_work():
        return call("todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--claimed-by", AGENT, "--text", "Validate the newly requested independent source.",
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--target-key", "new-independent-source",
        *(["--replan-obligation-id", obligation["obligation_id"]]
          if new_work == "bound_before_closeout" else []))
    later = None
    if new_work != "after_closeout":
        later = add_work()
        for _ in range(2):
            pending = _guard(call, turn)
            assert pending["heartbeat_receipt"]["settlement_identity"] == identity
            assert pending.get("selected_todo") is None
            assert pending["autonomous_replan_obligation"]["resolution_mode"] == "todo_lifecycle_settlement"
            assert pending["autonomous_replan_obligation"]["triggers"] == obligation["triggers"]
        denied = call("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
            "--codex-app", "--turn-instance-id", turn, "--replan-obligation-id",
            obligation["obligation_id"], "--source", "heartbeat", "--execute", expected_code=1)
        assert denied.get("appended") is not True
        rejected = call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", turn, "--replan-obligation-id", obligation["obligation_id"],
            "--classification", "pending_lifecycle_review", "--delivery-batch-scale", "single_surface",
            "--delivery-outcome", "outcome_progress", "--vision-unchanged-reason",
            "Independent work exists but the original lifecycle is still pending.",
            "--no-global-sync", "--suppress-external-sinks", expected_code=1)
        assert rejected.get("ok") is not True

    closed = call("todo", "complete", "--goal-id", GOAL, "--todo-id", original_id,
        "--agent-id", AGENT, "--completion-identity-key", completion_key,
        "--no-follow-up", "--reason", "The finite maintenance outcome was independently accepted.")
    assert closed["completion_recovery"] == "lifecycle_reentry_terminal_closeout"
    assert call("todo", "list", "--goal-id", GOAL, "--todo-id", original_id)["todo"]["note"] == completed["note"]
    if later is None:
        later = add_work()
    user_gate = None
    if later_user_gate:
        user_gate = call("todo", "add", "--goal-id", GOAL, "--role", "user",
            "--agent-id", AGENT, "--task-class", "user_gate", "--global-gate",
            "--text", "Authorize the separate newly requested source.")

    recovered = _guard(call, turn)
    assert recovered["heartbeat_receipt"]["settlement_identity"] == identity
    assert recovered.get("selected_todo") is None
    assert recovered["autonomous_replan_obligation"]["resolution_mode"] == "receipt_bound_replan_settlement"
    assert recovered["replan_action_packet"]["decision"] == "vision_outcome_checkpoint_required"
    assert recovered["replan_action_packet"]["semantic_delta"] is None
    if large_history:
        assert len(json.dumps(recent).encode()) > 2 * 1024 * 1024
    denied = call("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
        "--codex-app", "--turn-instance-id", turn, "--replan-obligation-id",
        obligation["obligation_id"], "--source", "heartbeat", "--execute", expected_code=1)
    assert denied.get("appended") is not True

    vision = tmp_path / "accepted-review.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed", "vision_patch": {
            "acceptance_summary": "Finite maintenance is verified; the independent new source remains open."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "Finite maintenance needed a terminal disposition.",
            "observed_reality": "Its original outcome and no-follow-up were verified; new work remains independent.",
            "retained": ["The newly requested source requires its own admission."],
            "evidence_refs": ["evidence:accepted-maintenance"]}}))
    refreshed = call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", turn, "--replan-obligation-id", obligation["obligation_id"],
        "--classification", "verified_lifecycle_outcome", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision),
        "--no-global-sync", "--suppress-external-sinks")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    spend = ("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
        "--codex-app", "--turn-instance-id", turn, "--replan-obligation-id",
        obligation["obligation_id"], "--source", "heartbeat", "--execute")
    assert call(*spend)["settlement_progress"]["state"] == "settled"
    assert call(*spend)["appended"] is False
    assert call("todo", "list", "--goal-id", GOAL, "--todo-id", later["todo_id"])["todo"]["status"] == "open"
    if user_gate:
        assert call("todo", "list", "--goal-id", GOAL, "--todo-id", user_gate["todo_id"])["todo"]["status"] == "open"
        fresh = _guard(call, "turn-independent-source-after-lifecycle")
        interaction = fresh["interaction_contract"]
        assert interaction["agent_channel"]["delivery_allowed"] is False
        assert interaction["agent_channel"]["must_attempt"] is False
        assert interaction["response_plan"]["decision"] == "ask_user"
    assert len([row for row in index.read_text().splitlines()
        if json.loads(row).get("classification") == "quota_slot_spent"]) == 1
