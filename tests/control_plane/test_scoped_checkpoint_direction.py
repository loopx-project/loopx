"""Receipt-first scoped adapter recovery, without enabling scoped writers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.goals.checkpoint_context_io import _receipt_path
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.control_plane.turn_driver.checkpoint_continuation import CurrentAgentCheckpoint
from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.control_plane.test_checkpoint_provider_fence import INSTANCE_A, _replace_with_source_registry
from tests.test_goal_mode_mcp_settlement import AGENT_ID, _control


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unmarked_scoped_direction_preserves_original_result(provider, tmp_path, monkeypatch):
    from tests.control_plane.test_checkpoint_provider_fence import _historical_source_context
    from loopx.control_plane.projects.registry_codec import load_project_registry

    runtime, registry, goal_ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "first_delivery", marked=False)
    goal = load_project_registry(registry)["goals"][0]
    def unexpected(_context):
        pytest.fail("An unmarked Turn must not inherit a new direction obligation")
    result = {"summary": "Original result", "vision_unchanged_reason": "Original Host metadata"}
    driver = CurrentAgentCheckpoint(registry, runtime, Path(goal["repo"]) / goal["state_file"],
        identity, unexpected, goal_ref=goal_ref)
    assert driver.continue_direction(result) == result
    assert not _receipt_path(runtime, identity).exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("receipt_loss", ["missing", "damaged"])
def test_scoped_committed_direction_uses_original_index_before_read_loss(provider, receipt_loss, tmp_path, monkeypatch):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    control = _control(registry)
    json.loads(control.should_run())
    first = json.loads(control.complete_task(todo, AGENT_ID, "Candidate verified.", next_agent_todo="Verify remaining work."))
    identity = SettlementIdentity.from_runtime_payload(first["settlement_identity"])
    def decide(context):
        return {"read_context_id": context["read_context_id"],
            "vision_unchanged_reason": "Current evidence preserves the original route."}
    decided = CurrentAgentCheckpoint(registry, runtime, state, identity, decide).continue_direction({})
    committed = json.loads(control.complete_task(todo, AGENT_ID, "Candidate verified.",
        next_agent_todo="Verify remaining work.", read_context_id=decided["checkpoint_read_context_id"],
        vision_unchanged_reason=decided["vision_unchanged_reason"]))
    assert committed["settlement_complete"] is True, committed

    # Synthetic historical source fixture: retain the real committed artifacts
    # and stamp their original scope consistently. This is not a live migration.
    goal_ref = {"goal_id": identity.goal_id, "goal_instance_id": INSTANCE_A}
    runs = runtime / "goals" / identity.goal_id / "runs"
    index = runs / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    for row in rows:
        row["goal_ref"] = goal_ref
        record = Path(row["json_path"])
        payload = json.loads(record.read_text())
        payload["goal_ref"] = goal_ref
        record.write_text(json.dumps(payload) + "\n")
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    event_log = runs.parent / "rollout-event-log.jsonl"
    events = [json.loads(line) for line in event_log.read_text().splitlines()]
    event_log.write_text("".join(json.dumps({**event, "goal_ref": goal_ref}) + "\n" for event in events))
    _replace_with_source_registry(registry, runtime, INSTANCE_A)
    path = _receipt_path(runtime, identity)
    if receipt_loss == "missing":
        path.unlink()
    else:
        path.write_text("{")
    original = index.read_bytes(), event_log.read_bytes(), registry.read_bytes()
    def unexpected(_context):
        pytest.fail("Committed scoped direction cannot dispatch a new model")
    recovered = CurrentAgentCheckpoint(registry, runtime, state, identity, unexpected,
        goal_ref=goal_ref).continue_direction({"summary": "Original result."})
    assert recovered["checkpoint_read_context_id"] == decided["checkpoint_read_context_id"]
    assert "agent_vision" not in recovered and "vision_unchanged_reason" not in recovered
    assert (index.read_bytes(), event_log.read_bytes(), registry.read_bytes()) == original
