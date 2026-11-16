"""Settled MCP retries recover their original native completion fingerprint."""

from __future__ import annotations

import json

import pytest

from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.test_goal_mode_mcp_settlement import AGENT_ID, GOAL_ID, _control, _run_cli


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("repeat_decision", [False, True])
def test_settled_mcp_completion_replays_original_request(
    provider, repeat_decision, tmp_path, monkeypatch
):
    registry, state, todo_id = _fixture(provider, tmp_path, monkeypatch)
    control = _control(registry)
    guard = json.loads(control.should_run())
    identity = guard["delivery_result_context"]["settlement_identity"]
    intent = {"next_agent_todo": "Verify remaining fixture work."}
    first = json.loads(
        control.complete_task(todo_id, AGENT_ID, "Candidate verified.", **intent)
    )
    assert first["stage"] == "direction_pending"
    decision = {
        "read_context_id": first["context"]["read_context_id"],
        "vision_unchanged_reason": "The verified candidate leaves remaining fixture work open.",
    }
    settled = json.loads(
        _control(registry).complete_task(
            todo_id, AGENT_ID, "Candidate verified.", **intent, **decision
        )
    )
    assert settled["ok"] and settled["settlement_complete"]
    assert settled["settlement_identity"] == identity
    _, before = _run_cli(
        registry, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", todo_id
    )
    index = tmp_path / "runtime" / "goals" / GOAL_ID / "runs" / "index.jsonl"
    original_index = index.read_bytes()
    replay = json.loads(
        _control(registry).complete_task(
            todo_id,
            AGENT_ID,
            "Candidate verified.",
            **intent,
            **(decision if repeat_decision else {}),
        )
    )
    assert replay.get("ok") and replay.get("settlement_complete"), replay
    assert replay["settlement_identity"] == identity
    _, after = _run_cli(
        registry, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", todo_id
    )
    assert after["todo"] == before["todo"]
    assert index.read_bytes() == original_index
    assert (
        sum(
            json.loads(line).get("classification") == "quota_slot_spent"
            for line in index.read_text().splitlines()
        )
        == 1
    )
    foreign = json.loads(
        _control(registry).complete_task(
            todo_id, "foreign-agent", "Candidate verified.", **intent, **decision
        )
    )
    assert foreign.get("ok") is False
    assert index.read_bytes() == original_index
    if provider != "legacy":
        changed = json.loads(
            _control(registry).complete_task(
                todo_id, AGENT_ID, "Changed completion evidence.", **intent, **decision
            )
        )
        assert changed.get("ok") is False
        assert changed.get("error_code") == "coordination_operation_identity_mismatch"
        assert index.read_bytes() == original_index
        _, unchanged = _run_cli(
            registry, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", todo_id
        )
        assert unchanged["todo"] == before["todo"]
