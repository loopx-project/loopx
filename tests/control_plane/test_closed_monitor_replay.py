"""A settled observation stays settled after its monitor leaves the open frontier."""
from __future__ import annotations

import json

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli, run_json_cli_result
from loopx.todos import add_goal_todo, list_goal_todos
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID, GOAL_ID, _add_monitor, _write_fixture,
)


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("material", [False, True])
@pytest.mark.parametrize("lifecycle", ["complete", "supersede"])
def test_closed_monitor_replays_original_no_spend_turn(
    tmp_path, monkeypatch, provider, material, lifecycle,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path)
    monitor = _add_monitor(registry, text="Observe a public target",
        target_key="public-target", next_due_at="2000-01-01T00:00:00Z")
    if provider != "legacy":
        todos = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
        projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID,
            todos=todos, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, GOAL_ID, projection,
            state_path=state, provider=provider)

    def call(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)

    turn = "closed-monitor-observation"
    guard = ["quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "codex_app_ssh_goal", "--turn-instance-id", turn,
        "--available-capability", "network", "--available-capability", "external_evidence_poll"]
    admitted = call(*guard)
    assert admitted["selected_todo"]["todo_id"] == monitor["todo_id"]
    identity = admitted["heartbeat_receipt"]["settlement_identity"]
    poll = call("quota", "monitor-poll", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--runtime-profile", "codex_app_ssh_goal",
        "--turn-instance-id", turn, "--todo-id", monitor["todo_id"],
        "--target-key", "public-target", "--result-hash", "observed-head",
        *(["--material-change"] if material else []), "--execute")
    assert poll["turn_continuation"]["current_turn_settled"] is True
    successor = add_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        role="agent", text="Validate the next independent artifact",
        task_class="advancement_task", claimed_by=AGENT_ID, agent_id=AGENT_ID)
    closed = call("todo", lifecycle, "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", monitor["todo_id"],
        *( ["--evidence", "Public target reached its observed terminal state.",
             "--successor-todo-id", successor["todo_id"]] if lifecycle == "complete"
           else ["--reason", "Observation finished; independent work remains."] ))
    assert closed["ok"] is True

    for selector in ([], ["--todo-id", monitor["todo_id"]]):
        replay = call(*guard, *selector)
        assert replay["heartbeat_receipt"]["settlement_identity"] == identity
        assert replay["should_run"] is False
        assert replay["effective_action"] == "heartbeat_settled_skip"
        assert not replay["interaction_contract"]["agent_channel"]["must_attempt"]
        assert replay.get("selected_todo", {}).get("todo_id") != successor["todo_id"]
    code, conflict = run_json_cli_result(*guard, "--todo-id", successor["todo_id"],
        registry_path=registry, runtime_root=runtime)
    assert code != 0 and conflict["ok"] is False
    assert conflict["error_code"] == "heartbeat_receipt_identity_conflict"
    fresh = call(*["next-observation-turn" if arg == turn else arg for arg in guard])
    assert fresh["selected_todo"]["todo_id"] == successor["todo_id"]
    rows = [json.loads(line) for line in
        (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text().splitlines()]
    assert sum(row["classification"] == "quota_monitor_poll" for row in rows) == 1
    assert not any(row["classification"] == "quota_slot_spent" for row in rows)
