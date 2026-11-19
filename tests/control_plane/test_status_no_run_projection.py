"""Missing Goal-level status is distinct from missing execution history."""
from __future__ import annotations

import json

import pytest

from canonical_authority_fixture import initialize_canonical_source_fixture
from loopx.status import collect_status, goal_attention, latest_run


GOAL_RUN = {
    "classification": "state_refreshed",
    "recommended_action": "Inspect the current deliverable.",
    "json_exists": True,
    "markdown_exists": True,
}
LANE_RUN = {**GOAL_RUN, "progress_scope": "agent_lane", "agent_id": "peer"}
CONNECTED_GOAL = {"id": "goal-a", "adapter_status": "connected-read-only"}


@pytest.mark.parametrize("history", [
    {"latest_status_run": GOAL_RUN, "latest_runs": [LANE_RUN]},
    {"latest_runs": [LANE_RUN, GOAL_RUN]},
])
def test_new_and_legacy_run_fields_keep_goal_status(history):
    goal = {**CONNECTED_GOAL, **history}
    assert latest_run(goal) == GOAL_RUN
    assert goal_attention(goal)["status"] == "state_refreshed"


@pytest.mark.parametrize("history", [
    {"latest_status_run": None, "latest_runs": [LANE_RUN], "unique_runs": 1},
    {"latest_runs": [LANE_RUN]},
    {"latest_runs": [{"classification": "quota_slot_spent"}]},
    {"latest_status_run": None, "latest_runs": [], "unique_runs": 4},
])
def test_history_without_goal_status_remains_unknown(history):
    goal = {**CONNECTED_GOAL, **history}
    assert latest_run(goal) is None
    item = goal_attention(goal)
    assert item["status"] == "unknown"
    assert item["source"] == "run_history"


@pytest.mark.parametrize("history", [
    {},
    {"latest_status_run": None, "latest_runs": [], "unique_runs": 0},
])
def test_genuinely_unrun_connected_goal_keeps_initial_tick(history):
    assert goal_attention({**CONNECTED_GOAL, **history})["status"] == "connected_without_run"


def test_unclassified_goal_run_stays_unknown():
    run = {"json_exists": True, "markdown_exists": True}
    item = goal_attention({**CONNECTED_GOAL, "latest_status_run": run})
    assert item["status"] == "unknown"
    assert item["source"] == "latest_run"


@pytest.mark.parametrize("role,expected", [
    (None, "unknown"),
    ("agent", "active_state_agent_todo"),
    ("user", "active_state_user_gate"),
])
def test_status_uses_canonical_work_without_promoting_agent_history(tmp_path, role, expected):
    registry, runtime, state = tmp_path / "registry.json", tmp_path / "runtime", tmp_path / "state.md"
    state.write_text(
        "# Goal\n\n## Agent Todo\n\n" + (
            ("## User Todo\n\n" if role == "user" else "") +
            "- [ ] Inspect the current deliverable.\n"
            "  <!-- loopx:todo todo_id=todo_work status=open " +
            ("task_class=user_gate" if role == "user" else "claimed_by=peer") + " -->\n"
            if role else ""
        ), encoding="utf-8",
    )
    registry.write_text(json.dumps({
        "schema_version": 1, "common_runtime_root": str(runtime),
        "goals": [{"id": "goal-a", "repo": str(tmp_path), "state_file": str(state),
                   "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
                   "coordination": {"registered_agents": ["peer"]}}],
    }), encoding="utf-8")
    initialize_canonical_source_fixture(runtime, "goal-a", state_path=state, provider="sqlite")
    runs = runtime / "goals/goal-a/runs"
    runs.mkdir(parents=True, exist_ok=True)
    artifact = runs / "lane.json"
    artifact.write_text("{}", encoding="utf-8")
    index = runs / "index.jsonl"
    index.write_text(json.dumps({
        **LANE_RUN, "goal_id": "goal-a", "generated_at": "2026-01-01T00:00:00Z",
        "json_path": str(artifact), "markdown_path": str(artifact),
    }) + "\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (registry, state, artifact, index)}

    payload = collect_status(registry_path=registry, runtime_root_override=str(runtime),
                             goal_id="goal-a", limit=1)

    item, = (row for row in payload["attention_queue"]["items"] if row["goal_id"] == "goal-a")
    assert item["status"] == expected
    assert item["source"] == ("active_state" if role else "run_history")
    if role == "user":
        assert item["waiting_on"] == "controller"
    assert payload["run_history"]["goals"][0]["latest_status_run"] is None
    assert {path: path.read_bytes() for path in before} == before
