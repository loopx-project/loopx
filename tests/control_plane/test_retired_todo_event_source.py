"""Retirement never loses an event-owned Todo or executes its validation."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from loopx.control_plane.goals.legacy_event_source import (
    RetiredTodoEventSourceError, require_no_legacy_todo_events,
)
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.control_plane.todos import completion_validation
from loopx.todos import complete_goal_todo

@pytest.mark.parametrize("alias", [None, "state_event_log", "state_events_file", "event_log"])
@pytest.mark.parametrize("contents", ["{broken\n", '{"event_type":"todo_added"}\n'])
def test_all_old_source_selectors_refuse_reads_and_writes_without_data_loss(tmp_path, alias, contents):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\n---\n\n## Agent Todo\n\n- [ ] [P1] Materialized work\n"
        "  <!-- loopx:todo todo_id=todo_existing status=open task_class=advancement_task -->\n")
    old = tmp_path / ("aliased.jsonl" if alias else "events.jsonl")
    old.write_text(contents)
    goal = {"id": "retired-fixture", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"agent_model": "peer_v1", "registered_agents": ["agent-a"]}}
    if alias:
        goal[alias] = old.name
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(tmp_path / "runtime"), "goals": [goal]}))
    before = state.read_bytes(), old.read_bytes()
    for args in [("todo", "list"),
        ("shared-goal-alignment", "--agent-id", "agent-a", "--project", str(tmp_path)), ("todo", "add", "--text", "New work", "--role", "agent"),
        ("todo", "complete", "--todo-id", "todo_existing", "--evidence", "validated")]:
        code, result = run_json_cli_result(*args, "--goal-id", goal["id"], registry_path=registry)
        assert code != 0 or result.get("ok") is False, result
        assert "legacy_todo_event_source_retired" in json.dumps(result), result
        assert (state.read_bytes(), old.read_bytes()) == before


def test_completion_rejects_before_any_validation_effect(tmp_path, monkeypatch):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("## Agent Todo\n- [ ] [P1] Work\n"
        "  <!-- loopx:todo todo_id=todo_existing status=open task_class=advancement_task -->\n")
    state.with_name("events.jsonl").write_text("retired\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "retired-fixture", "repo": str(tmp_path), "state_file": state.name}]}))
    def forbidden(**kwargs):
        raise AssertionError("a retired source executed a completion effect")
    monkeypatch.setattr(completion_validation, "run_declared_completion_validation_effect", forbidden)
    with pytest.raises(RetiredTodoEventSourceError):
        complete_goal_todo(registry_path=registry, goal_id="retired-fixture", todo_id="todo_existing",
            evidence="validated", no_followup=True)


def test_empty_file_is_not_an_event_authority_and_is_preserved(tmp_path):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    old = state.with_name("events.jsonl")
    old.touch()
    require_no_legacy_todo_events({}, state_path=state)
    assert old.exists() and old.read_bytes() == b""


def _portfolio_fixture(root: Path, alias: str | None = None):
    goals = []
    for goal_id in ("retired", "healthy"):
        area = root / goal_id
        area.mkdir()
        state = area / "ACTIVE_GOAL_STATE.md"
        state.write_text("---\nstatus: active\nhandoff_mode: legacy\n---\n"
            "## Objective\nObserve independent work.\n"
            "## Next Action\nContinue independent work.\n"
            "## Agent Todo\n- [ ] [P1] Independent work\n"
            f"  <!-- loopx:todo todo_id=todo_{goal_id} status=open task_class=advancement_task -->\n")
        goal = {"id": goal_id, "repo": str(area), "state_file": str(state),
            "status": "active", "domain": "software",
            "adapter": {"kind": "read_only_project_map_v0", "status": "connected"}}
        if goal_id == "retired" and alias:
            goal[alias] = "aliased.jsonl"
        goals.append(goal)
    runtime = root / "runtime"
    registry = root / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": goals}))
    old = root / "retired" / ("aliased.jsonl" if alias else "events.jsonl")
    return registry, runtime, goals, old


def _assert_portfolio_refusal(payload):
    rows = {item["goal_id"]: item for item in payload["attention_queue"]["items"]}
    assert rows["retired"]["status"] == "legacy_todo_event_source_retired"
    assert rows["retired"]["todo_source"] == "unavailable"
    assert rows["retired"]["waiting_on"] == "user_or_controller"
    assert "agent_todos" not in rows["retired"]
    assert rows["healthy"]["agent_todos"]["open_count"] == 1
    index = payload["todo_index"]
    assert index["complete"] is False
    assert index["unavailable_goal_ids"] == ["retired"]
    assert {item["goal_id"] for item in index["items"]} == {"healthy"}


@pytest.mark.parametrize("alias", [None, "state_event_log", "state_events_file", "event_log"])
def test_retired_source_is_scoped_and_later_healthy_goal_remains_visible(tmp_path, alias):
    registry, runtime, goals, old = _portfolio_fixture(tmp_path, alias)
    old.write_bytes(b"retired source bytes\n")
    before = old.read_bytes()
    # Historical rollout records cannot become fallback work for the refused source.
    events = runtime / "goals" / "retired" / "rollout-event-log.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"schema_version": "loopx_rollout_event_v0",
        "goal_id": "retired", "todo_id": "todo_retired",
        "event_kind": "todo_add", "status": "open", "summary": "Historical work"}) + "\n")
    data = json.loads(registry.read_text())
    data["goals"] = goals[:1]
    registry.write_text(json.dumps(data))
    code, first = run_json_cli_result("status", registry_path=registry)
    assert code == 0, first
    assert first["attention_queue"]["items"][0]["status"] == "legacy_todo_event_source_retired"
    data["goals"] = goals
    registry.write_text(json.dumps(data))
    for rewrite_annotation in (False, True):
        if rewrite_annotation:
            state = Path(goals[0]["state_file"])
            state.write_text(state.read_text().replace("task_class=advancement_task", "task_class=maintenance_task"))
        code, payload = run_json_cli_result("status", registry_path=registry)
        assert code == 0, payload
        _assert_portfolio_refusal(payload)
        assert old.read_bytes() == before
    code, mutation = run_json_cli_result("todo", "add", "--goal-id", "retired",
        "--role", "agent", "--text", "Must refuse", registry_path=registry)
    assert code != 0 or mutation.get("ok") is False
    assert "legacy_todo_event_source_retired" in json.dumps(mutation)
    assert old.read_bytes() == before
    old.unlink()  # Explicit owner recovery in a disposable synthetic fixture.
    code, recovered = run_json_cli_result("status", registry_path=registry)
    assert code == 0, recovered
    assert {item["goal_id"] for item in recovered["todo_index"]["items"]} == {"healthy", "retired"}
    assert "unavailable_goal_ids" not in recovered["todo_index"]


def test_http_status_preserves_healthy_goal_when_retired_source_appears(tmp_path):
    registry, runtime, _, old = _portfolio_fixture(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "serve-status", "--host", "127.0.0.1", "--port", str(port),
        "--path", "/status", "--scan-path", str(tmp_path / "healthy" / "ACTIVE_GOAL_STATE.md")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1).close()
                break
            except urllib.error.URLError:
                assert server.poll() is None and time.monotonic() < deadline
                time.sleep(0.1)
        for present in (False, True, False):
            if present:
                old.write_bytes(b"old source preserved\n")
            elif old.exists():
                old.unlink()
            with urllib.request.urlopen(base + "/status", timeout=90) as response:
                assert response.status == 200
                payload = json.load(response)
            if present:
                _assert_portfolio_refusal(payload)
                assert old.read_bytes() == b"old source preserved\n"
            else:
                assert {item["goal_id"] for item in payload["todo_index"]["items"]} == {"healthy", "retired"}
    finally:
        server.terminate()
        server.wait(timeout=10)
