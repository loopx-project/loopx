"""A CLI supplement cannot qualify retained progress after a hard Host stop."""
import json
from pathlib import Path

import pytest

from loopx.control_plane.goals import checkpoint_context_io as context_io
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.control_plane.turn_driver.checkpoint_continuation import CurrentAgentCheckpoint
from tests.control_plane.test_retained_result_binding import direction
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, _projected_cli_args, _spend_run_count,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_cannot_qualify_retained_progress_after_hard_stop(provider, tmp_path, monkeypatch):
    runtime, call, prior, turn, context, supplement = direction(provider, tmp_path, monkeypatch)
    project = runtime.parent / "project"
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    identity = SettlementIdentity.from_runtime_payload(prior["settlement_identity"])
    artifacts = [Path(context["retained_result_artifacts"][key]) for key in ("json_path", "markdown_path")]
    work = context_io._receipt_path(runtime, identity, purpose="delivery_result")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    original = {path: path.read_bytes() for path in [index, work, *artifacts]}

    def decide(current):
        assert current["retained_result_binding"] == context["retained_result_binding"]
        assert current["read_context_id"] == context["read_context_id"]
        return {"result_kind": "iteration_failed", "stop_reason": "retained_result_requires_revision"}

    with pytest.raises(ValueError, match="result retained"):
        CurrentAgentCheckpoint(project / ".loopx/registry.json", runtime, state, identity,
                               decide).continue_direction({}, purpose="supplement_checkpoint")
    receipt = context_io._receipt_path(runtime, identity)
    assert json.loads(receipt.read_text(encoding="utf-8"))["direction_stopped"] is True
    stopped = receipt.read_bytes()
    bind = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", SELECTED_REPLAN_TODO_ID,
            "--turn-instance-id", turn)
    rc, observed = call("status", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    assert rc == 0, observed
    stage = next(row["first_delivery_progress"]["stage"] for row in observed["attention_queue"]["items"]
                 if row["goal_id"] == GOAL_ID)
    assert stage == "direction_stopped"

    def assert_rejected(command):
        rc, rejected = call(*_projected_cli_args(command, turn_instance_id=turn))
        assert (rc, rejected.get("error_code")) == (1, "checkpoint_direction_stopped"), rejected
        assert rejected["appended"] is False
        assert rejected["checkpoint_read_context"]["reread_required"] is False
        assert "settlement_owed" not in rejected

    assert_rejected(supplement)
    assert receipt.read_bytes() == stopped

    # Relevant requirements changed. The current read is useful for inspection
    # but cannot turn the same stopped R into qualified progress.
    with state.open("a", encoding="utf-8") as source:
        source.write("\nFinal delivery requires an additional independent check.\n")
    rc, reread = call("checkpoint-context", *bind, "--purpose", "supplement_checkpoint")
    assert rc == 0 and reread["read_context_id"] != context["read_context_id"], reread
    assert "goal" in reread["current_progress_qualification"]["changed_work_components"]
    assert reread["retained_result_binding"] == context["retained_result_binding"]
    after_read = receipt.read_bytes()
    assert json.loads(after_read)["direction_stops"] == json.loads(stopped)["direction_stops"]
    assert_rejected(supplement.replace(context["read_context_id"], reread["read_context_id"]))
    spend_rc, spend = call("quota", "spend-slot", *bind, "--source", "heartbeat", "--execute")
    assert spend_rc == 1 and spend["appended"] is False, spend
    assert _spend_run_count(runtime) == 0
    assert receipt.read_bytes() == after_read
    assert all(path.read_bytes() == content for path, content in original.items())
