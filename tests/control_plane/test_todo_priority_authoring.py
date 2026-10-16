"""Priority intent survives real CLI/provider/Markdown round trips."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_quota_settlement_cli import _write_fixture, AGENT_ID, GOAL_ID, TODO_ID

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_priority_authoring_round_trip(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = next(project.rglob("ACTIVE_GOAL_STATE.md"))
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode="soft_claim", leases=[], todos=[{
        "schema_version": "todo_item_v0", "todo_id": TODO_ID, "role": "agent", "status": "open", "done": False,
        "text": "[P0] Existing higher priority", "priority": "P0", "title": "Existing higher priority",
        "archive_state": "active", "source_section": "Agent Todo", "index": 1, "task_class": "advancement_task",
    }])
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)

    def cli(*args, success=True):
        run = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args, "--goal-id", GOAL_ID],
            cwd=REPO, capture_output=True, text=True, timeout=60)
        assert (run.returncode == 0) == success, run.stdout + run.stderr
        return json.loads(run.stdout)

    def read(todo_id):
        packet = cli("todo", "list")
        # Native and compatibility public lists expose the same role collections.
        def rows(value):
            if isinstance(value, dict):
                if value.get("todo_id") == todo_id and "text" in value:
                    yield value
                for item in value.values():
                    yield from rows(item)
            elif isinstance(value, list):
                for item in value:
                    yield from rows(item)
        return next(rows(packet))

    created = cli("todo", "add", "--role", "agent", "--priority", "P3", "--text", "Validate P0 parser prose")
    todo_id = created["todo_id"]
    assert read(todo_id)["priority"] == "P3"
    assert "[P3] Validate P0 parser prose" in state.read_text()
    cli("todo", "update", "--todo-id", todo_id, "--text", "New task title")
    assert read(todo_id)["priority"] == "P3"
    edit = ["todo", "update", "--todo-id", todo_id, "--priority", "P4",
            "--update-operation-id", "priority-change-once"]
    changed = cli(*edit)
    assert changed["ok"]
    replay = cli(*edit)
    assert replay["status"] == "replayed"
    cli("todo", "update", "--todo-id", todo_id, "--priority", "P0",
        "--update-operation-id", "priority-change-once", success=False)
    assert read(todo_id)["priority"] == "P4"
    before = state.read_bytes()
    cli("todo", "update", "--todo-id", todo_id, "--priority", "P0", "--text", "[P1] conflicting declarations", success=False)
    assert state.read_bytes() == before
    assert read(todo_id)["priority"] == "P4"
    cli("todo", "update", "--todo-id", todo_id, "--clear-priority")
    assert read(todo_id).get("priority") is None
    assert "[P4] New task title" not in state.read_text()
    ordinary = cli("todo", "add", "--role", "agent", "--text", "Investigate P0 prose without declaring priority")
    assert read(ordinary["todo_id"])["priority"] == "P1"
    selected = cli("quota", "should-run", "--codex-app", "--agent-id", AGENT_ID,
        "--todo-id", ordinary["todo_id"], "--turn-instance-id", "priority-selection", "--scan-path", str(project))
    assert selected["should_run"] is True
    cli("todo", "update", "--todo-id", todo_id, "--text", "Still explicitly unranked")
    assert read(todo_id).get("priority") is None
    create = ["todo", "add", "--role", "agent", "--text", "Default priority receipt",
              "--operation-id", "default-priority-once"]
    first = cli(*create)
    assert read(first["todo_id"])["priority"] == "P1"
    cli("todo", "update", "--todo-id", first["todo_id"], "--priority", "P4")
    assert cli(*create)["status"] == "replayed"
    assert read(first["todo_id"])["priority"] == "P4"
    cli(*create, "--priority", "P0", success=False)
