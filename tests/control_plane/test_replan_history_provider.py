"""Real canonical stores feed the public quota consumer, including retry history."""
from copy import deepcopy
import json

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_canonical_frontier_revision import _fixture
from test_goal_amendment_proposal import _write_fixture, _stall_runs, _ack_run, GOAL_ID
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.todos.todo_summary import structured_todo_item
from loopx.status import active_state_todo_fields, autonomous_replan_obligation_from_runs


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_complex_provider_snapshot_replan_and_quota_readback(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_fixture(tmp_path, runs=[])
    projection = _fixture()
    initialize_canonical_authority(paths["runtime"], GOAL_ID, projection,
                                   state_path=paths["state_file"], provider=provider)
    # The display is absent: the production reader must use persisted authority.
    paths["state_file"].unlink()
    goal = json.loads(paths["registry"].read_text())["goals"][0]
    summary = active_state_todo_fields(goal, runtime_root=paths["runtime"])["agent_todos"]
    history = list(reversed(_stall_runs()))
    current = autonomous_replan_obligation_from_runs(history, agent_todos=summary, agent_id="agent-a")
    assert current["triggers"][0]["kind"] == "typed_progress_repeat"
    assert autonomous_replan_obligation_from_runs([history[0]] * 8,
        agent_todos=summary, agent_id="agent-a") is None
    ack = _ack_run(current["obligation_id"])
    assert autonomous_replan_obligation_from_runs([ack, *history],
        agent_todos=summary, agent_id="agent-a") is None
    peer_ack = {**deepcopy(ack), "agent_id": "agent-b"}
    assert autonomous_replan_obligation_from_runs([peer_ack, *history],
        agent_todos=summary, agent_id="agent-a") == current
    index = paths["runtime"] / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("".join(json.dumps(row) + "\n" for row in reversed(history)))
    code, result = run_json_cli_result("quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", "agent-a", registry_path=paths["registry"])
    assert code == 0, result
    assert "typed_progress_repeat" in json.dumps(result)
    assert active_state_todo_fields(goal, runtime_root=paths["runtime"])["agent_todos"] == summary
    assert not paths["state_file"].exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_explicit_completed_todo_fallback_retains_twenty_record_threshold(
    tmp_path, monkeypatch, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    paths = _write_fixture(tmp_path, runs=[])
    registry = json.loads(paths["registry"].read_text())
    registry["goals"][0]["adapter"] = {"kind": "harness_self_improvement"}
    paths["registry"].write_text(json.dumps(registry))
    todos, _, sections = parse_todo_source(paths["state_file"].read_text())
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID,
        todos=[structured_todo_item(row, role=role, source_section=sections[role], text_limit=None)
               for role in ("user", "agent") for row in todos[role]],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(paths["runtime"], GOAL_ID, projection,
                                   state_path=paths["state_file"], provider=provider)
    paths["state_file"].unlink()
    index = paths["runtime"] / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)

    def call(*args):
        code, result = run_json_cli_result(*args, registry_path=paths["registry"])
        assert code == 0, result
        return result

    def obligation():
        status = call("status", "--limit", "30", "--scan-path", str(paths["project"]))
        return next((obligation for row in status["attention_queue"]["items"]
                     if row["goal_id"] == GOAL_ID
                     if (obligation := row.get("autonomous_replan_obligations_by_agent", {})
                         .get("agent-a")) is not None), None)

    def history(count, *, retry=False, peer=False):
        index.write_text("".join(json.dumps({
            "goal_id": GOAL_ID, "agent_id": "agent-b" if peer else "agent-a",
            "generated_at": f"2026-09-01T03:{n:02d}:00Z",
            "turn_instance_id": "one-retried-turn" if retry else f"turn-{n}",
            "classification": "bounded_iteration", "delivery_outcome": "outcome_progress",
        }) + "\n" for n in range(count)))

    history(20)
    assert obligation() is None, "unsettled history is not effective-Turn evidence"
    call("configure-goal", "--goal-id", GOAL_ID,
         "--execution-replan-after-todos", "5", "--execute")
    history(19)
    assert obligation() is None
    history(20, retry=True)
    assert obligation() is None, "retries cannot meet the twenty-Turn fallback"
    history(20, peer=True)
    assert obligation() is None, "a peer cannot trigger the current Agent's review"
    history(20)
    trigger = obligation()["triggers"][0]
    assert trigger["kind"] == "periodic_review_due"
    assert trigger["run_count"] == trigger["threshold"] == 20
    assert trigger["agent_id"] == "agent-a"
    call("configure-goal", "--goal-id", GOAL_ID,
         "--clear-execution-replan-after-todos", "--execute")
    assert obligation() is None
    assert not paths["state_file"].exists(), "readback must retain canonical authority"
