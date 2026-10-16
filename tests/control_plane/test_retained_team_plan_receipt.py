"""Recover a fixed 1.x receipt through the current public adapter."""
import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.work_items.team_plan_adapter import apply_team_plan
from loopx.todos import add_goal_todo, list_goal_todos


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_1x_team_receipt_survives_canonical_later_writes(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    history = json.loads((Path(__file__).parent / "fixtures/team_plan_1x.json").read_text())
    assert history["source_revision"] == "60d833b13c85135587b725013b247e827663ffb9"
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    state.write_text(history["source"])
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "repo": str(tmp_path), "state_file": "state.md",
        "coordination": {"registered_agents": ["alpha"], "agent_model": "peer_v1"}}]}))

    def replay(**overrides):
        return apply_team_plan(registry_path=registry, goal_id="goal-a",
            agent_id=None, proposal=history["plan"], expected_state_fingerprint=history["basis"], **overrides)

    original = state.read_bytes()
    assert replay()["action"] == "reused"
    assert state.read_bytes() == original
    rows = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    initialize_canonical_authority(runtime, "goal-a",
        build_todo_runtime_shadow_projection(goal_id="goal-a", todos=rows, handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent", text="Later accepted work", agent_id="alpha")
    before, display = list_goal_todos(registry_path=registry, goal_id="goal-a"), state.read_bytes()
    recovered = replay()
    assert recovered["action"] == "reused"
    assert recovered["lane_todo_ids"] == history["result"]["lane_todo_ids"]
    with pytest.raises(Exception, match="identity mismatch"):
        apply_team_plan(registry_path=registry, goal_id="goal-a", agent_id=None,
            proposal={**history["plan"], "objective": "Changed original intent"},
            expected_state_fingerprint=history["basis"])
    assert list_goal_todos(registry_path=registry, goal_id="goal-a") == before
    assert state.read_bytes() == display
