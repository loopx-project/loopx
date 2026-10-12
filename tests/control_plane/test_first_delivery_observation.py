"""Historical observation faults cannot become cross-lane admission failures."""
from __future__ import annotations

import hashlib
import json

import pytest

from loopx.control_plane.goals import checkpoint_context_io as context_io
from loopx.control_plane.quota.settlement import SettlementIdentity
from tests.control_plane.test_checkpoint_provider_fence import fixture
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _run_cli


def _status(project, runtime, registry, agent=AGENT_ID):
    rc, payload = _run_cli(registry, runtime, "status", "--goal-id", GOAL_ID,
        "--agent-id", agent, cwd=project)
    assert rc == 0 and payload["ok"], payload
    return payload["attention_queue"]["items"][0]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("corruption", ["{", "[]", '{"purpose":"first_delivery","identity":[]}'])
def test_unenrolled_lane_retains_status_and_scoped_unavailable_evidence(tmp_path, monkeypatch, provider, corruption):
    project, runtime, registry, state, read, _ = fixture(tmp_path, monkeypatch, provider)
    read()  # Existing supplement receipts remain ordinary feature-off history.
    before = _status(project, runtime, registry)
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    prior = (state.read_bytes(), index.read_bytes())
    directory = runtime / "goals" / GOAL_ID / "checkpoint-contexts"
    damaged = directory / (hashlib.sha256(b"historical-context").hexdigest() + ".json")
    damaged.write_text(corruption, encoding="utf-8")
    after = _status(project, runtime, registry)
    assert after["recommended_action"] == before["recommended_action"]
    progress = after["first_delivery_progress"]
    assert progress["stage"] == "observation_unavailable"
    assert progress["observation_errors"][0]["scope"] == "goal"
    assert progress["observation_errors"][0]["receipt"] == damaged.stem
    assert "settlement_identity" not in progress
    assert "result_committed" not in progress  # Unavailable does not assert absence.
    assert progress["goal_completion_certified"] is False
    assert (state.read_bytes(), index.read_bytes()) == prior
    assert damaged.read_text(encoding="utf-8") == corruption


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_other_agent_observation_does_not_override_current_lane(tmp_path, monkeypatch, provider):
    project, runtime, registry, _, read, _ = fixture(tmp_path, monkeypatch, provider, first_delivery=True)
    read()
    peer = "independent-peer"
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["coordination"]["registered_agents"].append(peer)
    registry.write_text(json.dumps(registration), encoding="utf-8")
    before = _status(project, runtime, registry, peer)
    identity = SettlementIdentity(goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)
    receipt = context_io._receipt_path(runtime, identity)
    receipt.write_text("{", encoding="utf-8")
    after = _status(project, runtime, registry, peer)
    assert after["recommended_action"] == before["recommended_action"]
    assert after["first_delivery_progress"]["stage"] == "observation_unavailable"
    # An intact other-Agent binding can be excluded even if its body is invalid.
    receipt.write_text(json.dumps({"purpose": "first_delivery", "identity": identity.as_dict()}), encoding="utf-8")
    assert context_io.pending_first_delivery_progress(runtime, GOAL_ID, peer) is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_enrolled_corruption_stays_unknown_and_cannot_admit_another_write(tmp_path, monkeypatch, provider):
    project, runtime, registry, _, read, _ = fixture(tmp_path, monkeypatch, provider, first_delivery=True)
    read()
    result = context_io.read_checkpoint_context(registry_path=registry, runtime_root_override=str(runtime),
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID,
        purpose="delivery_result")
    rc, completed = _run_cli(registry, runtime, "todo", "complete", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
        "--delivery-read-context", result["read_context_id"],
        "--task-lease-idempotency-key", f"checkpoint-{TODO_ID}", "--task-lease-expected-version", "1",
        "--note", "Validated the selected output.", cwd=project)
    assert rc == 0, json.dumps(completed)
    identity = SettlementIdentity(goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)
    damaged = context_io._receipt_path(runtime, identity)
    damaged.write_text("{", encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    progress = _status(project, runtime, registry)["first_delivery_progress"]
    assert progress["stage"] == "operation_unknown"
    assert progress["result_committed"] is True
    assert progress["settlement_identity"] == identity.as_dict()
    with pytest.raises(context_io.CheckpointReadContextRejected) as rejected:
        read()
    assert rejected.value.code == "checkpoint_commit_unknown"
    assert index.read_bytes() == before
    assert damaged.read_text(encoding="utf-8") == "{"
