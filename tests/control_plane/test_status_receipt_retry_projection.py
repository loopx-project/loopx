"""Status transports receipt-owned waits without creating Todo authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
from urllib.request import urlopen

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.quota.blocked_retry import overlay_status_turn_retries
from loopx.status import active_state_todo_fields
from loopx.status_server import (
    DEFAULT_CONFIGURE_GOAL_APPLY_PATH,
    DEFAULT_CONFIGURE_GOAL_DRY_RUN_PATH,
    DEFAULT_REWARD_APPEND_PATH,
    DEFAULT_REWARD_DRY_RUN_PATH,
    StatusHTTPServer,
    StatusRequestHandler,
)


def fixture(observed: str = "2030-01-01T00:00:00Z", due: str = "2030-01-01T00:05:00Z"):
    todo = {"schema_version": "todo_item_v0", "todo_id": "todo_work", "index": 1,
            "role": "agent", "status": "open", "done": False, "archive_state": "active",
            "source_section": "Agent Todo", "task_class": "advancement_task",
            "claimed_by": "agent-a", "text": "Validate the independent result."}
    retry = {"schema_version": "quota_blocked_retry_v0", "source": "turn_settlement",
             "todo_id": todo["todo_id"], "observed_at": observed, "due_at": due,
             "resume_when": f"resume_at:{due}"}
    run = {"goal_id": "goal-a", "agent_id": "agent-a", "todo_id": todo["todo_id"],
           "turn_instance_id": "original-attempt", "classification": "fixture_blocked",
           "generated_at": observed, "delivery_outcome": "outcome_gap",
           "progress_observation": {"result_class": "blocked"}, "blocked_retry": retry}
    history = {"goals": [{"id": "goal-a", "latest_runs": [run]}]}
    queue = {"items": [{"goal_id": "goal-a", "agent_todos": {"items": [todo]},
                        "project_asset": {"agent_todos": {"items": [todo]}}}]}
    return todo, run, history, queue


@pytest.mark.parametrize("observed_at", ["2030-01-01T00:02:00Z", "2030-01-01T00:05:00Z", "2030-01-01T00:05:01Z"])
def test_status_retains_original_clock_and_disappears_at_due(observed_at):
    _, run, history, queue = fixture()
    before = deepcopy((history, queue))
    projected = overlay_status_turn_retries(history, queue, observed_at=observed_at)
    row = projected["items"][0]["agent_todos"]["items"][0]
    if observed_at < run["blocked_retry"]["due_at"]:
        assert row["resume_ready"] is False
        assert row["blocked_retry"]["turn_instance_id"] == "original-attempt"
        assert row["blocked_retry"]["observed_at"] == run["blocked_retry"]["observed_at"]
        assert row["blocked_retry"]["due_at"] == run["blocked_retry"]["due_at"]
        assert row["resume_condition"]["blocked_retry"] == row["blocked_retry"]
        assert projected["items"][0]["project_asset"]["agent_todos"]["items"][0] == row
    else:
        assert "blocked_retry" not in row
        assert "resume_when" not in row
    assert (history, queue) == before


@pytest.mark.parametrize("change", ["agent", "goal", "newer_run", "canonical_wait", "done"])
def test_status_does_not_borrow_foreign_or_superseded_retries(change):
    todo, run, history, queue = fixture()
    if change == "agent":
        todo["claimed_by"] = "agent-b"
    elif change == "goal":
        queue["items"][0]["goal_id"] = "goal-b"
    elif change == "newer_run":
        history["goals"][0]["latest_runs"].insert(0, {**run, "delivery_outcome": "outcome_progress"})
    elif change == "canonical_wait":
        todo["resume_when"] = "todo_done:todo_dependency"
    else:
        todo["status"] = "done"
    projected = overlay_status_turn_retries(history, queue, observed_at="2030-01-01T00:02:00Z")
    assert projected is queue


def test_status_uses_retained_semantic_runs_before_display_compaction():
    _, run, history, queue = fixture()
    history["goals"][0]["latest_runs"] = []
    history["goals"][0]["semantic_history"] = {"active_blocked_retry_runs": [run]}
    projected = overlay_status_turn_retries(history, queue, observed_at="2030-01-01T00:02:00Z")
    assert projected["items"][0]["agent_todos"]["items"][0]["blocked_retry"]["turn_instance_id"] == "original-attempt"


def test_status_preserves_microsecond_boundary_and_historical_clock_fence():
    observed, due = "2030-01-01T00:00:00.123456+00:00", "2030-01-01T00:05:00.123456+00:00"
    _, run, history, queue = fixture(observed, due)
    projected = overlay_status_turn_retries(history, queue, observed_at="2030-01-01T00:05:00.123455Z")
    row = projected["items"][0]["agent_todos"]["items"][0]
    assert row["blocked_retry"]["due_at"] == due
    assert row["resume_condition"]["scheduled_for"] == "2030-01-01T00:05:00.123456Z"
    assert row["resume_condition"]["satisfied"] is False
    assert overlay_status_turn_retries(history, queue, observed_at=due) is queue
    # Python's historical receipt reader accepts a basic date-time spelling;
    # reading it does not expand the stricter Todo authoring grammar.
    run["blocked_retry"].update(observed_at="20300101T000000+0000", due_at="20300101T000500+0000",
                                resume_when="resume_at:20300101T000500+0000")
    historical = overlay_status_turn_retries(history, queue, observed_at="2030-01-01T00:02:00Z")
    row = historical["items"][0]["agent_todos"]["items"][0]
    assert row["resume_condition"]["satisfied"] is False
    assert row["blocked_retry"]["due_at"] == "20300101T000500+0000"
    assert "clock_provider" not in row["resume_condition"]
    assert overlay_status_turn_retries(history, queue, observed_at="2030-01-01T00:05:00Z") is queue


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("channel", ["cli", "http"])
def test_real_provider_status_projects_wait_without_writes(tmp_path: Path, monkeypatch, provider, channel):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    now = datetime.now(UTC).replace(microsecond=123456)
    observed, due = now.isoformat(), (now + timedelta(minutes=5)).isoformat()
    todo, run, _, _ = fixture(observed, due)
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n")
    runtime = tmp_path / "runtime"
    goal = {"id": "goal-a", "repo": str(tmp_path), "state_file": str(state),
            "domain": "software", "adapter": {"kind": "read_only_project_map_v0"}}
    projection = build_todo_runtime_shadow_projection(goal_id=goal["id"], todos=[todo])
    initialize_canonical_authority(runtime, goal["id"], projection, state_path=state, provider=provider)
    before = active_state_todo_fields(goal, runtime_root=runtime)
    runs = runtime / "goals" / goal["id"] / "runs"
    runs.mkdir(parents=True)
    body = runs / "blocked.json"
    body.write_text(json.dumps(run))
    index = runs / "index.jsonl"
    index.write_text(json.dumps({**run, "json_path": str(body)}) + "\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [goal]}))
    frozen = {p: p.read_bytes() for p in [state, body, index, registry]}
    try:
        if channel == "cli":
            result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                                     "--format", "json", "status", "--goal-id", goal["id"]],
                                    capture_output=True, text=True, timeout=60)
            payload = json.loads(result.stdout)
            assert result.returncode == 0, payload
        else:
            server = StatusHTTPServer(("127.0.0.1", 0), StatusRequestHandler)
            server.registry_path, server.runtime_root_override = registry, str(runtime)
            server.scan_roots, server.limit, server.status_path = [tmp_path], 80, "/status.json"
            server.control_plane_write_enabled = server.reward_write_enabled = False
            server.goal_subagent_configuration_enabled = server.verbose = False
            server.reward_dry_run_path, server.reward_append_path = DEFAULT_REWARD_DRY_RUN_PATH, DEFAULT_REWARD_APPEND_PATH
            server.configure_goal_dry_run_path, server.configure_goal_apply_path = DEFAULT_CONFIGURE_GOAL_DRY_RUN_PATH, DEFAULT_CONFIGURE_GOAL_APPLY_PATH
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/status.json?goal_id=goal-a", timeout=30) as response:
                    payload = json.load(response)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
        assert payload["ok"] is True
        row = next(row for item in payload["attention_queue"]["items"]
                   for row in item.get("agent_todos", {}).get("items", []) if row.get("todo_id") == todo["todo_id"])
        assert row["resume_condition"]["blocked_retry"]["due_at"] == due
        assert row["blocked_retry"]["turn_instance_id"] == run["turn_instance_id"]
        assert active_state_todo_fields(goal, runtime_root=runtime) == before
        assert all(p.read_bytes() == content for p, content in frozen.items())
        assert not list(runtime.rglob("*spend*"))
    finally:
        restart_effect_runtime()
