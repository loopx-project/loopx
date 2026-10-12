"""Caller-authored direction, real CLI recovery, and one original settlement."""
from __future__ import annotations

import json

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo
from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.test_goal_mode_mcp_settlement import GOAL_ID, AGENT_ID, _write_fixture, _control, _run_cli


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("new_obligation", [False, True])
def test_mcp_first_delivery_recovers_stale_direction_without_repeating_result(tmp_path, monkeypatch, provider, new_obligation):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = _write_fixture(tmp_path)
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["adapter"] = {"kind": "fixture_v0", "status": "connected-delivery"}
    registry.write_text(json.dumps(registration), encoding="utf-8")
    added = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Verify the bounded output.", task_class="advancement_task",
        claimed_by=AGENT_ID, continuation_policy="same_agent_non_delivery")
    todo_id = added["todo_id"]
    runtime = tmp_path / "runtime"
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode="soft_claim", todos=todos["agent_todos"]["items"]),
        state_path=state, provider=provider)
    rc, baseline = _run_cli(registry, "refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--vision-summary", "Verify the bounded output.", "--vision-acceptance", "Output verification passes.",
        "--no-global-sync", "--suppress-external-sinks")
    assert rc == 0, json.dumps(baseline)
    control = _control(registry)
    intent = dict(todo_id=todo_id, agent_id=AGENT_ID, evidence="The bounded output passed verification.",
        next_agent_todo="Verify the remaining bounded output.", first_delivery=True)
    first = json.loads(control.complete_task(**intent))
    assert first["ok"] and first["stage"] == "result_review_pending", first
    result_id = first["context"]["read_context_id"]
    second = json.loads(control.complete_task(**intent, delivery_read_context_id=result_id))
    assert second["ok"] and second["stage"] == "direction_pending", second
    assert second["context"]["basis"]["todo"]["status"] == "done"
    assert second["settlement_complete"] is False
    rc, status = _run_cli(registry, "status", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    assert rc == 0, status
    observed = status["attention_queue"]["items"][0]["first_delivery_progress"]
    assert observed["stage"] == "direction_pending" and observed["result_committed"] is True
    assert observed["goal_completion_certified"] is False
    assert status["attention_queue"]["items"][0]["recommended_action"] == observed["next_action"]
    from loopx.status import collect_status

    # Dashboard and Lark projections call the shared collector, not the CLI's
    # Agent decorator. An unscoped read must retain the same recovery message.
    shared = collect_status(registry_path=registry, runtime_root_override=str(runtime),
        scan_roots=[state.parent], limit=10, goal_id=GOAL_ID, include_public_boundary_scan=False)
    shared_item = shared["attention_queue"]["items"][0]
    assert shared_item["first_delivery_progress"]["stage"] == "direction_pending"
    assert shared_item["recommended_action"] == observed["next_action"]
    direction_id = second["context"]["read_context_id"]
    run_cli = control.run_cli
    injected = []

    def provider_call(args, **kwargs):
        if new_obligation and args[:1] == ["refresh-state"] and not injected:
            # Change the relevant basis before the native direction commit.
            # The original result is already durable; only direction is retried.
            state.write_text(state.read_text(encoding="utf-8") + "\n## Acceptance update\nVerify an additional bounded output.\n", encoding="utf-8")
            injected.append(True)
        return run_cli(args, **kwargs)

    control.run_cli = provider_call
    decision = dict(delivery_read_context_id=result_id, read_context_id=direction_id,
        vision_unchanged_reason="The bounded output passed; the current frontier was reviewed.")
    final = json.loads(control.complete_task(**intent, **decision))
    assert final["ok"] is (not new_obligation), json.dumps(final)
    if new_obligation:
        assert final["settlement"]["failed_stage"] == "durable_writeback", final
        assert "checkpoint_read_context_stale" in final["settlement"]["reason"], final
        control.run_cli = run_cli
        reread = json.loads(control.review_task_vision(todo_id, AGENT_ID, first_delivery=True))
        assert reread["ok"], reread
        decision["read_context_id"] = reread["read_context_id"]
        decision["vision_unchanged_reason"] = "The updated acceptance was reviewed; the successor retains the additional work."
        final = json.loads(control.complete_task(**intent, **decision))
        assert final["ok"], final
    replay = json.loads(control.complete_task(**intent, **decision))
    assert replay["ok"] and replay["settlement_identity"] == final["settlement_identity"], replay
    rows = [json.loads(line) for line in (runtime / f"goals/{GOAL_ID}/runs/index.jsonl").read_text().splitlines()]
    assert sum(row["classification"] == "quota_slot_spent" for row in rows) == 1
    assert sum(bool(row.get("vision_checkpoint", {}).get("read_context")) for row in rows) == 1
