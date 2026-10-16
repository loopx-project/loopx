"""Ordinary task intent uses the real canonical CLI and Chat action owner."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.todos.mutation_api import add_goal_todo


@pytest.fixture(params=["file", "sqlite"])
def journey(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(
        tmp_path, provider=request.param, handoff_mode="hard_lease", registered_agents=["agent-a", "agent-b"])
    task = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Validate the original task", claimed_by="agent-a", agent_id="agent-a",
        task_class="advancement_task", action_kind="implement")
    todo_id = task["todo_id"]

    def cli(*args, ok=True):
        child = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(runtime), *args,
            "--goal-id", "goal-a", "--todo-id", todo_id], capture_output=True, text=True, timeout=60)
        payload = json.loads(child.stdout)
        assert (child.returncode == 0) is ok, (payload, child.stderr)
        return payload

    return cli, todo_id, registry, runtime


def test_cli_copy_pause_resume_defer_without_lease_bookkeeping(journey):
    cli, _, registry, runtime = journey
    lease = cli("task-lease", "acquire", "--owner", "agent-a", "--idempotency-key", "original")["lease"]
    original = cli("todo", "list")["todo"]
    assert cli("todo", "update", "--agent-id", "agent-a", "--note", "Administrative note")["ok"]
    assert cli("task-lease", "inspect")["lease"] == lease
    pause = ("todo", "update", "--agent-id", "agent-a", "--status", "blocked", "--reason", "Owner paused",
        "--note", "Keep the original acceptance", "--evidence", "evidence:owner-pause", "--update-operation-id", "pause-once")
    assert cli(*pause, "--dry-run")["status"] == "planned"
    assert cli("task-lease", "inspect")["lease"] == lease
    applied = cli(*pause)
    assert applied["blocked_lifecycle_transition"]["lease_retirement"] == "released"
    assert cli(*pause)["status"] == "replayed"
    paused = cli("todo", "list")["todo"]
    assert paused["status"] == "blocked" and paused["done"] is False
    assert paused["note"] == "Keep the original acceptance"
    assert paused["evidence"] == "evidence:owner-pause"
    assert paused["text"] == original["text"] and paused["claimed_by"] == "agent-a"
    stale = cli("todo", "update", "--agent-id", "agent-a", "--note", "Stale execution",
        "--task-lease-idempotency-key", "original", "--task-lease-expected-version", str(lease["version"]), ok=False)
    assert stale["ok"] is False
    inspect = stale["recovery"]["inspect"]["cli_args"]
    assert f"--registry={registry}" in inspect and f"--runtime-root={runtime}" in inspect
    inspected = subprocess.run([sys.executable, "-m", "loopx.cli", *inspect], capture_output=True, text=True, timeout=60)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["lease"]["status"] == "released"
    resumed = cli("todo", "update", "--agent-id", "agent-a", "--status", "open", "--reason", "Owner resumed", "--note", "Resume context")
    assert resumed["blocked_lifecycle_transition"]["execution_authority_granted"] is False
    assert cli("task-lease", "inspect")["lease"]["status"] == "released"
    deferred = cli("todo", "update", "--agent-id", "agent-a", "--status", "deferred", "--reason", "Wait for owner",
        "--resume-when", "resume_at:2099-01-01T00:00:00Z", "--note", "Deferral context", "--evidence", "evidence:wait")
    assert deferred["deferred_transition"]["execution_authority_granted"] is False
    assert cli("todo", "list")["todo"]["status"] == "deferred"
    resumed = cli("todo", "update", "--agent-id", "agent-a", "--status", "open", "--note", "Explicit resume", "--reason", "Owner resumed")
    assert resumed["deferred_resume_transition"]["execution_authority_granted"] is False
    readback = cli("todo", "list")["todo"]
    assert readback["status"] == "open" and readback.get("resume_when") is None
    refused = cli("todo", "update", "--agent-id", "agent-a", "--status", "blocked", "--reason", "Pause",
        "--text", "Replace the acceptance while pausing", ok=False)
    assert refused["recovery"]["action"] == "resolve_lifecycle_edit"
    assert "--text" in refused["recovery"]["retry"]["remove_flags"]
    assert cli("todo", "list")["todo"] == readback


def test_chat_uses_same_atomic_owner_and_refuses_changed_review_basis(journey, tmp_path):
    cli, todo_id, registry, runtime = journey
    cli("task-lease", "acquire", "--owner", "agent-a", "--idempotency-key", "chat-execution")
    cli("todo", "update", "--agent-id", "agent-a", "--note", "Original task context")
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry,
        runtime_root_override=str(runtime))

    def preview(key, **parameters):
        return service.preview({"action_kind": "todo.update", "summary": "Owner task operation", "idempotency_key": key,
            "context": {"kind": "todo", "goal_id": "goal-a", "todo_id": todo_id},
            "normalized_parameters": {"goal_id": "goal-a", "todo_id": todo_id, "agent_id": "agent-a", **parameters}})

    before = cli("task-lease", "inspect")["lease"]
    paused = preview("chat-pause", operation="block", note="Pause with context")
    assert cli("task-lease", "inspect")["lease"] == before
    result = service.apply(paused["proposal_id"])
    assert result["proposal"]["status"] == "applied"
    assert cli("task-lease", "inspect")["lease"]["status"] == "released"
    paused_todo = cli("todo", "list")["todo"]
    assert paused_todo["note"] == "Original task context" and paused_todo["reason"] == "Pause with context"
    resume = preview("chat-resume", operation="edit", status="open")
    assert service.apply(resume["proposal_id"])["proposal"]["status"] == "applied"
    resumed_todo = cli("todo", "list")["todo"]
    assert resumed_todo["status"] == "open" and resumed_todo["note"] == "Original task context"
    priority = preview("chat-priority", operation="edit", priority="P2")
    cli("todo", "update", "--agent-id", "agent-a", "--note", "Intervening task change")
    assert service.apply(priority["proposal_id"])["proposal"]["status"] == "stale"
    assert cli("todo", "list")["todo"].get("priority") != "P2"
