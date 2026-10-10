"""Ordinary JSON inventory removes copies, not obligations or source facts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.todos.markdown import render_todo_markdown
from loopx.todos import list_goal_todos
from tests.control_plane.test_todo_list_agent_lane_projection import AGENT_ID, GOAL_ID, _write_fixture

REPO = Path(__file__).resolve().parents[2]


def expand_references(value, root):
    """Consumer oracle: resolve response-local pointers, without selecting by id."""
    if isinstance(value, list):
        return [expand_references(child, root) for child in value]
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            assert value["$ref"].startswith("#/")
            target = root
            for token in value["$ref"][2:].split("/"):
                key = token.replace("~1", "/").replace("~0", "~")
                target = target[int(key)] if isinstance(target, list) else target[key]
            return expand_references(target, root)
        return {key: expand_references(child, root) for key, child in value.items()}
    return value


def cli(registry, runtime, flags, output_format="json"):
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
         "--runtime-root", str(runtime), "--format", output_format,
         "todo", "list", "--goal-id", GOAL_ID, *flags],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout) if output_format == "json" else result.stdout


def test_projection_failure_returns_the_standard_cli_error_without_source_changes(tmp_path, monkeypatch, capsys):
    from loopx.cli import main
    from loopx.cli_commands import todo as todo_command

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = _write_fixture(tmp_path)
    before = state.read_bytes()

    def unavailable(operation, payload, **kwargs):
        assert operation == "todo.context.page" and payload["list_payload"]["ok"]
        raise RuntimeError("Todo projection unavailable")

    monkeypatch.setattr(todo_command, "effect_runtime_result", unavailable)
    result = main(["--registry", str(registry), "todo", "list", "--format", "json",
                   "--goal-id", GOAL_ID])
    error = json.loads(capsys.readouterr().out)
    assert result == 1 and not error["ok"]
    assert error["error"] == "Todo projection unavailable"
    assert not error.get("todos") and "todo_list_record_references" not in error
    assert state.read_bytes() == before


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_real_cli_references_reconstruct_all_scoped_lanes_and_counts(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = _write_fixture(tmp_path)
    runtime = Path(json.loads(registry.read_text())["common_runtime_root"])
    if provider != "legacy":
        goal = json.loads(registry.read_text())["goals"][0]
        active, archived, _ = parse_todo_source(state.read_text(), goal=goal, state_path=state)
        rows = [{"schema_version": "todo_item_v0", **row}
                for row in [*active["user"], *active["agent"], *archived]]
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, handoff_mode="legacy", leases=[], todos=rows,
        )
        initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
        state.write_text(state.read_text().replace("Execute the current agent advancement.", "Stale display"))
    before = state.read_bytes()
    for options, flags in [
        ({}, []),
        ({"agent_id": AGENT_ID}, ["--agent-id", AGENT_ID]),
        ({"agent_id": AGENT_ID, "limit": 1}, ["--agent-id", AGENT_ID, "--limit", "1"]),
        ({"role": "agent", "status": "done"}, ["--role", "agent", "--status", "done"]),
    ]:
        native = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, **options)
        wire = cli(registry, runtime, flags)
        disclosure = wire.pop("todo_list_record_references")
        assert disclosure["replaced_view_records"] > 0
        assert expand_references(wire, wire) == native
        assert wire["todos"] == native["todos"]
        assert len(json.dumps(wire)) < len(json.dumps(native))
        # Native readers used by task-map/manager/Explore retain full records.
        assert all(isinstance(row.get("text"), str) for row in native["todos"])
        assert cli(registry, runtime, flags, "markdown") == render_todo_markdown(native) + "\n"
    thin = cli(registry, runtime, ["--thin"])
    assert "todo_list_record_references" not in thin
    assert thin == list_goal_todos(registry_path=registry, goal_id=GOAL_ID, thin=True)
    exact = cli(registry, runtime, ["--todo-id", "todo_agent_current"])
    assert "todo_list_record_references" not in exact
    assert exact["todo"]["text"] == "Execute the current agent advancement."
    assert state.read_bytes() == before
    if provider == "file":
        snapshot = next((runtime / "authority/file-v0").glob("authority-store-*.json"))
        saved = snapshot.read_bytes()
        try:
            snapshot.write_text("{invalid canonical source")
            failed = subprocess.run(
                [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                 "--runtime-root", str(runtime), "--format", "json", "todo", "list", "--goal-id", GOAL_ID],
                cwd=REPO, capture_output=True, text=True, timeout=60,
            )
            assert failed.returncode == 1
            error = json.loads(failed.stdout)
            assert not error["ok"] and not error.get("todos")
            assert "todo_list_record_references" not in error
        finally:
            snapshot.write_bytes(saved)
        assert cli(registry, runtime, ["--agent-id", AGENT_ID])["ok"]
