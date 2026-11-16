"""Closing a Monitor cannot orphan the review Turn which admitted its closeout."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from loopx.control_plane.testing.quota_fixtures import quota_todo_item
from test_replan_successor_guard_reentry import AGENT, GOAL, TURN, _fixture


def _guard(call, turn: str = TURN):
    return call("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("first", ["guard", "writeback"])
def test_monitor_closeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, first: str) -> None:
    monitor = quota_todo_item(
        todo_id="todo_monitor", title="Observe the external gate", archive_state="active",
        task_class="continuous_monitor", claimed_by=AGENT, action_kind="monitor",
        target_key="external-gate", cadence="30m", consecutive_no_change="5",
        next_due_at="2099-01-01T00:00:00Z", expires_at="2099-01-02T00:00:00Z",
    )
    call, _, index = _fixture(tmp_path, monkeypatch, provider, initial_todos=[monitor])
    # Only the live Monitor's current streak admits this review, not run history.
    index.write_text("")
    state = tmp_path / "project" / "ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text() + "\n## User Todo / Owner Review Reading Queue\n")
    admitted = _guard(call)
    assert admitted["decision"] == "autonomous_replan_required"
    assert admitted["autonomous_replan_obligation"]["triggers"][0]["kind"] == "monitor_no_change_streak"
    identity = admitted["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    actions = admitted["interaction_contract"]["cli_channel"]["next_cli_actions"]

    claim = call("todo", "claim", "--goal-id", GOAL, "--todo-id", monitor["todo_id"],
        "--agent-id", AGENT, "--claimed-by", AGENT)
    completed = call("todo", "complete", "--goal-id", GOAL, "--todo-id", monitor["todo_id"],
        "--agent-id", AGENT, "--claim-operation-id", claim["claim_operation_id"],
        "--evidence", "The declared external gate is covered.", "--no-follow-up")
    assert completed["ok"] is True
    listed = call("todo", "list", "--goal-id", GOAL, "--todo-id", monitor["todo_id"], "--include-archived")
    assert listed["todo"]["status"] == "done"

    if first == "guard":
        pending = _guard(call)
        assert pending["heartbeat_receipt"]["settlement_identity"] == identity
        assert pending["autonomous_replan_obligation"]["obligation_id"] == identity["replan_obligation_id"]
        assert pending["normal_delivery_allowed"] is False

    # A lifecycle transition is not itself the semantic review or a debit.
    premature = call(*shlex.split(actions[1])[1:], expected_code=1)
    assert premature.get("appended") is not True
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "no_followup",
        "vision_patch": {"acceptance_summary": "The independently accepted source outcome is covered."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "stop",
            "prior_assumption": "The external gate still needs observation.",
            "observed_reality": "The declared gate is covered and no successor remains.",
            "retained": ["the independently accepted source contract"],
            "evidence_refs": ["evidence:gate-coverage"]},
    }))
    refresh = shlex.split(actions[0])[1:]
    values = {
        "--progress-result-class": "no_followup", "--progress-surface-id": "gate-coverage",
        "--progress-hypothesis-id": "covered-gate", "--progress-probe-kind": "closeout-review",
        "--progress-evidence-id": "evidence:gate-coverage",
    }
    for flag, value in values.items():
        refresh[refresh.index(flag) + 1] = value
    refreshed = call(*refresh, "--agent-vision-json", str(vision),
        "--progress-coverage-scope-id", "external-gate", "--progress-coverage-complete",
        "--no-global-sync", "--suppress-external-sinks")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    pending = _guard(call)
    assert pending["heartbeat_receipt"]["settlement_identity"] == identity
    assert pending["settlement_progress"]["state"] == "spend_required"
    spend = pending["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert len(spend) == 1
    spent = call(*shlex.split(spend[0])[1:])
    assert spent["appended"] is True
    assert spent["settlement_progress"]["state"] == "settled"
    assert call(*shlex.split(spend[0])[1:])["appended"] is False
    assert _guard(call)["effective_action"] == "heartbeat_settled_skip"
    next_turn = _guard(call, "turn-after-monitor-review")
    assert next_turn["ok"] is True
    assert next_turn["effective_action"] == "terminal_no_followup"
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
