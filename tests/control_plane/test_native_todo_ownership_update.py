"""Ownership edits use the ordinary public command, not a test-only authority API."""
import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_native_todo_planning_update import fixture, records, update
from loopx.todos import update_goal_todo


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_transfer_clear_exclusions_and_replay(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = fixture(tmp_path, provider)
    before = records(registry)
    transfer = ["--claimed-by", "Agent B", "--update-operation-id", "transfer-first"]
    update(registry, *transfer, "--dry-run")
    assert records(registry) == before
    update(registry, *transfer)
    assert records(registry)["todo_target"]["claimed_by"] == "agent-b"
    update(registry, "--text", "Former owner cannot write", ok=False)
    update(registry, "--agent-id", "agent-b", "--clear-claim")
    cleared = records(registry)
    assert not cleared["todo_target"].get("claimed_by")
    assert update(registry, *transfer)["status"] == "replayed"
    assert records(registry) == cleared
    update(registry, "--excluded-agent", "agent-b")
    excluded = records(registry)
    assert excluded["todo_target"]["excluded_agents"] == ["agent-b"]
    update(registry, "--agent-id", "agent-b", "--clear-excluded-agents", ok=False)
    assert records(registry) == excluded
    update(registry, "--clear-excluded-agents")
    assert not records(registry)["todo_target"].get("excluded_agents")
    assert records(registry)["todo_other"] == before["todo_other"]
    assert state.exists(), "canonical commit still delivers its permanent Markdown projection"


@pytest.mark.parametrize("intent", [
    {"claimed_by": "unregistered"}, {"excluded_agents": ["agent-b", "unregistered"]},
    {"claimed_by": "agent-b", "clear_claim": True},
    {"claimed_by": "agent-b", "excluded_agents": ["agent-b"]},
    {"excluded_agents": ["agent-a"]},
])
def test_invalid_ownership_intent_never_partially_writes_copy(tmp_path, intent):
    registry, state = fixture(tmp_path)
    before = records(registry)
    with pytest.raises((ValueError, RuntimeError)):
        update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_target",
                         agent_id="agent-a", text="Must not partially commit", **intent)
    assert records(registry) == before
    assert not state.exists()


def test_clear_claim_and_exclude_former_holder_in_one_edit(tmp_path):
    registry, _ = fixture(tmp_path)
    result = update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id="todo_target",
        agent_id="agent-a", clear_claim=True, excluded_agents=["agent-a"])
    assert result["ok"]
    todo = records(registry)["todo_target"]
    assert not todo.get("claimed_by")
    assert todo["excluded_agents"] == ["agent-a"]
