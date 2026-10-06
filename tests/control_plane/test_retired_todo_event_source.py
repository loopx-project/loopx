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
from loopx.control_plane.todos.todo_index import build_todo_index
from loopx.control_plane.runtime.public_safety import public_safe_compact_text
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import restart_effect_runtime


@pytest.mark.parametrize("status", ["open", "blocked", "deferred", "done"])
def test_rollout_audit_cannot_overwrite_current_todo_state_or_claim(tmp_path, status):
    current = {"todo_id": "todo_current", "text": "Current authority work",
        "status": status, "done": status in ("deferred", "done"), "claimed_by": None}
    events = [{"goal_id": "goal-a", "todo_id": "todo_current", "event_kind": "todo_update",
        "status": "open" if status != "open" else "done", "agent_id": "old-actor",
        "summary": "Historical update", "recorded_at": "2026-01-01T00:00:00Z"}]
    result = build_todo_index(queue={"items": [{"goal_id": "goal-a",
        "agent_todos": {"items": [current]}}]}, history={"goals": [{"id": "goal-a"}]},
        runtime_root=tmp_path, public_safe_compact_text=public_safe_compact_text,
        events_for_goal=lambda goal_id, **kwargs: events)
    row = result["items"][0]
    assert row["status"] == status
    assert row["done"] is current["done"]
    assert row.get("agent_id") is None
    assert row["latest_event_status"] == events[0]["status"]
    assert row["latest_event_summary"] == "Historical update"
    assert row["event_count"] == 1

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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_directory_lifecycle_uses_canonical_state_despite_stale_audit(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime),
        "goals": [{"id": "goal-a", "repo": str(tmp_path), "state_file": str(state),
            "domain": "software", "status": "active",
            "adapter": {"kind": "read_only_project_map_v0", "status": "connected"},
            "coordination": {"registered_agents": ["agent-a", "agent-b", "agent-c"]}}]}))
    initialize_canonical_authority(runtime, "goal-a",
        build_todo_runtime_shadow_projection(goal_id="goal-a", todos=[], handoff_mode="hard_lease"),
        state_path=state, provider=provider)

    def cli(*args, success=True):
        code, payload = run_json_cli_result(*args, "--goal-id", "goal-a", registry_path=registry)
        if success:
            assert code == 0 and payload.get("ok") is True, payload
        return payload

    def readback(expected, lease_status=None, *, agent="agent-a", target=None):
        target = target or todo_id
        before = cli("todo", "list")
        current = next(row for row in before["todos"] if row["todo_id"] == target)
        assert current["status"] == expected
        payload = cli("status")
        indexed = next(row for row in payload["todo_index"]["items"] if row["todo_id"] == target)
        assert indexed["status"] == expected
        directory = cli("agent-directory", "--agent-id", agent)
        work = next(row for row in directory["rows"] if row["agent_id"] == agent)["work"]
        if expected == "done":
            assert work is None
        else:
            assert work["todo_id"] == target and work["todo_status"] == expected
            assert work["claimed_by"] == agent
            assert work.get("lease_status") == lease_status
        after = cli("todo", "list")
        assert before["authority_read"]["provider_revision"] == after["authority_read"]["provider_revision"]
        assert before["todos"] == after["todos"]

    try:
        added = cli("todo", "add", "--role", "agent", "--text", "Long canonical task " + "work " * 140,
            "--claimed-by", "agent-a")
        todo_id = added["todo_id"]
        lease = cli("task-lease", "acquire", "--todo-id", todo_id, "--owner", "agent-a",
            "--idempotency-key", "directory-live-lease")
        readback("open", "active")
        cli("task-lease", "release", "--todo-id", todo_id, "--owner", "agent-a",
            "--idempotency-key", "directory-live-lease", "--expected-version", str(lease["lease"]["version"]))
        before = cli("todo", "list")
        cli("todo", "update", "--todo-id", todo_id, "--agent-id", "agent-a",
            "--status", "blocked", "--reason", "Fixture lifecycle transition",
            "--update-operation-id", "directory-blocked", "--clear-resume-when",
            "--update-expected-provider-revision", before["authority_read"]["provider_revision"])
        readback("blocked", "released")
        deferred = cli("todo", "add", "--role", "agent", "--text", "Wait for an explicit decision",
            "--status", "deferred", "--resume-when", "todo_done:" + todo_id, "--claimed-by", "agent-b")
        readback("deferred", agent="agent-b", target=deferred["todo_id"])
        finished = cli("todo", "add", "--role", "agent", "--text", "Validate the terminal readback",
            "--claimed-by", "agent-c")
        terminal_lease = cli("task-lease", "acquire", "--todo-id", finished["todo_id"], "--owner", "agent-c",
            "--idempotency-key", "directory-terminal-lease")
        cli("todo", "complete", "--todo-id", finished["todo_id"], "--agent-id", "agent-c",
            "--evidence", "validation://directory-lifecycle", "--no-follow-up",
            "--task-lease-idempotency-key", "directory-terminal-lease", "--task-lease-expected-version",
            str(terminal_lease["lease"]["version"]))
        readback("done", agent="agent-c", target=finished["todo_id"])
        # A readable stale display and rollout history cannot rescue a lost provider.
        state.write_text("## Agent Todo\n- [ ] Old work\n"
            f"  <!-- loopx:todo todo_id={todo_id} status=open claimed_by=agent-a -->\n")
        authority = runtime / "authority" / f"{provider}-v0"
        unavailable = runtime / "unavailable-provider"
        authority.rename(unavailable)
        for command in ("status", "agent-directory"):
            failure = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                "--runtime-root", str(runtime), "--format", "json", command, "--goal-id", "goal-a"],
                capture_output=True, text=True, timeout=60)
            if failure.stdout.strip():
                failed = json.loads(failure.stdout)
                if command == "status":
                    assert not failed["ok"] and not failed.get("todo_index", {}).get("items")
                else:
                    assert all(row["work"] is None for row in failed.get("rows", []))
            else:
                # Some source-loss paths raise the owning unavailable error.
                # That failure is not a successful empty or legacy work view.
                assert failure.returncode != 0
                assert "LocalCoordinationAuthorityUnavailable:" in failure.stderr
        unavailable.rename(authority)
        readback("blocked", "released")
        readback("deferred", agent="agent-b", target=deferred["todo_id"])
        readback("done", agent="agent-c", target=finished["todo_id"])
    finally:
        restart_effect_runtime()
