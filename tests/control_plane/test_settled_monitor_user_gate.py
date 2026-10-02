"""Exact monitor closeout wins over an unrelated scoped user gate."""
from __future__ import annotations

import json

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID, GOAL_ID, _add_monitor, _write_fixture,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli
from loopx.todos import add_goal_todo, list_goal_todos
from loopx.control_plane.effect_program import ReceiptBoundMonitorPhase
from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload, quota_todo_item, quota_todo_summary,
)


def test_incomplete_frontier_cannot_erase_settled_monitor_receipt():
    monitor = quota_todo_item(todo_id="todo_monitor", title="Observe a public target",
        task_class="continuous_monitor", claimed_by="agent-a",
        next_due_at="2099-01-01T00:00:00Z", watch_only=True)
    summary = quota_todo_summary([monitor], claim_scope_agent_id="agent-a")
    # An incomplete legacy source cannot establish a monitor-only schedule.
    # This changes aggregate coverage, not the exact persisted poll receipt.
    summary["work_counts"]["complete"] = False
    status = quota_status_payload(goal_id="compact-monitor", status="active",
        quota_state="operator_gate", recommended_action="Wait for an owner decision",
        agent_todos=summary, user_todo_items=[quota_todo_item(
            todo_id="todo_peer_gate", title="Choose peer destination", role="user",
            task_class="user_gate", blocks_agent="agent-b")],
        coordination={"agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"]})
    replay = build_quota_should_run(status, goal_id="compact-monitor", agent_id="agent-a",
        receipt_bound_todo_id="todo_monitor",
        receipt_bound_monitor_phase=ReceiptBoundMonitorPhase.SETTLED)
    assert replay["selected_todo"]["todo_id"] == "todo_monitor"
    assert replay["should_run"] is False
    assert replay["effective_action"] == "heartbeat_settled_skip"
    assert replay["safe_bypass_allowed"] is False
    assert replay["execution_obligation"]["must_attempt_work"] is False
    assert replay["interaction_contract"]["agent_channel"]["delivery_allowed"] is False


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("material", [False, True])
def test_scoped_gate_cannot_reopen_an_exact_settled_monitor(tmp_path, monkeypatch, provider, material):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path)
    gated = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Work awaiting an owner decision", task_class="advancement_task",
        status="blocked", claimed_by=AGENT_ID, agent_id=AGENT_ID)
    add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="user",
        text="Approve the gated work", task_class="user_gate", bound_agent=AGENT_ID,
        blocks_agent=AGENT_ID, unblocks_todo_id=gated["todo_id"], agent_id=AGENT_ID)
    monitor = _add_monitor(registry, text="Observe an independent public target",
        target_key="public-target", next_due_at="2000-01-01T00:00:00Z")
    if provider != "legacy":
        todos = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
        projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID,
            todos=todos, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    turn = "monitor-scoped-gate-turn"
    guard_args = ["quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn,
        "--available-capability", "network", "--available-capability", "external_evidence_poll"]
    def call(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    admitted = call(*guard_args)
    assert admitted["selected_todo"]["todo_id"] == monitor["todo_id"]
    assert admitted["execution_obligation"]["must_attempt_work"] is True
    poll_args = ["quota", "monitor-poll", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn,
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        "--todo-id", monitor["todo_id"], "--target-key", "public-target", "--result-hash", "observed-head",
        *(["--material-change"] if material else []), "--execute"]
    poll = call(*poll_args)
    assert poll["turn_continuation"]["current_turn_settled"] is True
    successor = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Validate an independent result", task_class="advancement_task",
        claimed_by=AGENT_ID, agent_id=AGENT_ID)
    replay = call(*guard_args)
    assert replay["work_lane_contract"]["obligation"] == "finish_settled_receipt_bound_monitor_turn"
    assert replay["agent_lane_next_action"]["receipt_bound_monitor_phase"] == "settled"
    assert replay["selected_todo"]["todo_id"] == monitor["todo_id"]
    assert replay["should_run"] is False
    assert replay["safe_bypass_allowed"] is False
    assert replay["execution_obligation"]["must_attempt_work"] is False
    interaction = replay["interaction_contract"]
    assert interaction["agent_channel"]["must_attempt"] is False
    assert interaction["agent_channel"]["delivery_allowed"] is False
    assert not any(action.startswith("loopx ") for action in interaction["cli_channel"].get("next_cli_actions", []))
    assert interaction["mode"] == "heartbeat_settled_skip"
    assert interaction["cli_channel"].get("spend_after_validation") is not True
    assert replay["automation_liveness"]["automation_action"] == "keep_active_quiet"
    duplicate = call(*poll_args)
    assert duplicate["replayed"] is True and duplicate["appended"] is False
    next_args = ["next-monitor-scoped-gate-turn" if x == turn else x for x in guard_args]
    fresh = call(*next_args)
    assert fresh["selected_todo"]["todo_id"] == successor["todo_id"]
    assert fresh["execution_obligation"]["must_attempt_work"] is True
    assert fresh["interaction_contract"]["mode"] == "scoped_user_gate_fallback"
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert sum(row["classification"] == "quota_monitor_poll" for row in rows) == 1
    assert not any(row["classification"] in {"state_refreshed", "quota_slot_spent"} for row in rows)
