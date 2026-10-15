"""The public update-done caller over real canonical providers and validation."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, list_goal_todos


def fixture(tmp_path: Path, provider: str, *, validation: str | None = None) -> tuple[Path, Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## User Todo\n\n## Agent Todo\n\n## Completed Work Archive\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": "goal-a", "status": "active", "repo": str(project), "state_file": state.name,
                   "coordination": {"registered_agents": ["agent-a"]}}]}), encoding="utf-8")
    dependent = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Continue after the observed outcome", task_class="advancement_task",
        status="blocked", claimed_by="agent-a")
    added = add_goal_todo(registry_path=registry, goal_id="goal-a", role="user",
        text="Record the observed outcome", task_class="user_action", validation_command=validation,
        unblocks_todo_id=dependent["todo_id"])
    todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    projection = build_todo_runtime_shadow_projection(goal_id="goal-a", todos=todos, handoff_mode="soft_claim")
    initialize_canonical_authority(tmp_path / "runtime", "goal-a", projection, state_path=state, provider=provider)
    return registry, state, str(added["todo_id"])


def update(registry: Path, todo_id: str, *args: str, ok: bool = True) -> dict:
    process = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
        "todo", "update", "--goal-id", "goal-a", "--todo-id", todo_id,
        "--agent-id", "agent-a", "--status", "done", *args], capture_output=True, text=True, timeout=45)
    assert process.stdout, process.stderr
    result = json.loads(process.stdout)
    assert (process.returncode == 0) is ok, (result, process.stderr)
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_user_update_done_uses_canonical_transaction(tmp_path: Path, provider: str) -> None:
    registry, state, todo_id = fixture(tmp_path, provider)
    state.unlink()
    result = update(registry, todo_id, "--text", "Observed outcome", "--note", "Reviewed", "--no-follow-up")
    assert result["ok"] is True
    assert result["source_authority"] == f"{provider}_v0"
    assert result["legacy_fallback_used"] is False
    todo = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]
    assert (todo["status"], todo["text"], todo["note"]) == ("done", "[P1] Observed outcome", "Reviewed")
    assert state.is_file()


def validation_command(source: str) -> str:
    import shlex
    return shlex.join([sys.executable, "-c", source])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_validation_failure_preview_and_replay(tmp_path: Path, provider: str) -> None:
    marker = tmp_path / "calls"
    gate = tmp_path / "allow"
    command = validation_command(f"from pathlib import Path; p=Path({str(marker)!r}); "
        f"p.write_text(p.read_text()+'x' if p.exists() else 'x'); raise SystemExit(0 if Path({str(gate)!r}).exists() else 1)")
    registry, state, todo_id = fixture(tmp_path, provider, validation=command)
    before = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    preview = update(registry, todo_id, "--dry-run", "--note", "Validated")
    assert preview["status"] == "planned"
    assert not marker.exists()
    failed = update(registry, todo_id, "--note", "Validated", ok=False)
    assert failed["validation_blocked_completion"] is True
    assert marker.read_text() == "x"
    assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"] == before
    gate.touch()
    first = update(registry, todo_id, "--note", "Validated", "--update-operation-id", "complete-observation")
    assert first["status"] == "applied"
    assert marker.read_text() == "xx"
    completed = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]
    state.unlink()
    from loopx.control_plane.todos.completion_validation_store import completion_validation_declaration_path
    completion_validation_declaration_path(runtime_root=tmp_path / "runtime", goal_id="goal-a", todo_id=todo_id).unlink()
    replay = update(registry, todo_id, "--note", "Validated", "--update-operation-id", "complete-observation")
    assert replay["status"] == "replayed"
    assert marker.read_text() == "xx"
    annotation = update(registry, todo_id, "--note", "Later acknowledgement")
    assert annotation["status"] == "applied"
    after = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]
    assert after["note"] == "Later acknowledgement"
    assert after["completed_at"] == completed["completed_at"]
    assert marker.read_text() == "xx"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_revocation_during_real_validation_cannot_commit(tmp_path: Path, provider: str) -> None:
    registry_path = tmp_path / "registry.json"
    command = validation_command("import json; from pathlib import Path; "
        f"p=Path({str(registry_path)!r}); r=json.loads(p.read_text()); "
        "r['goals'][0]['coordination']['registered_agents']=[]; p.write_text(json.dumps(r))")
    registry, _state, todo_id = fixture(tmp_path, provider, validation=command)
    result = update(registry, todo_id, ok=False)
    assert result["error_code"] == "authority_source_changed"
    assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_invalid_input_and_permission_do_not_run_validation(tmp_path: Path, provider: str) -> None:
    marker = tmp_path / "called"
    registry, _state, todo_id = fixture(tmp_path, provider, validation=validation_command(
        f"from pathlib import Path; Path({str(marker)!r}).touch()"))
    invalid = update(registry, todo_id, "--successor-todo-id", "todo_missing", ok=False)
    assert invalid["error_code"] == "todo_successor_not_found"
    unauthorized = update(registry, todo_id, "--agent-id", "not-registered", ok=False)
    assert unauthorized["error_code"] == "actor_not_registered"
    assert not marker.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("failure_boundary", ["display", "action_receipt"])
def test_reviewed_chat_completion_recovers_projection(tmp_path: Path, provider: str, monkeypatch: pytest.MonkeyPatch, failure_boundary: str) -> None:
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService
    from loopx.control_plane.todos import provider_projection

    registry, state, todo_id = fixture(tmp_path, provider)
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry)
    proposal = service.preview({"action_kind": "todo.update", "summary": "Record an observed outcome",
        "normalized_parameters": {"goal_id": "goal-a", "todo_id": todo_id,
            "agent_id": "agent-a", "operation": "complete", "note": "Reviewed observation"},
        "context": {}, "idempotency_key": "user-completion"})
    with monkeypatch.context() as patch:
        def failed_projection(**kwargs):
            raise OSError("Synthetic projection interruption")
        if failure_boundary == "display":
            patch.setattr(provider_projection, "project_current_canonical_todos", failed_projection)
            failed = service.apply(proposal["proposal_id"])["proposal"]
            assert failed["status"] == "failed"
            assert failed["receipt"] is None
        else:
            def lost_response(*args, **kwargs):
                raise ConnectionError("Synthetic response loss after canonical commit")
            patch.setattr(service.store, "apply", lost_response)
            with pytest.raises(ConnectionError):
                service.apply(proposal["proposal_id"])
    assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]["status"] == "done"
    state.unlink()
    recovered = service.apply(proposal["proposal_id"])["proposal"]
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["outcome"] == "todo_completed"
    assert state.exists()
    dependent = next(row for row in list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"] if row["role"] == "agent")
    assert (dependent["status"], dependent["claimed_by"]) == ("open", "agent-a")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_chat_user_validation_failure_never_creates_success_receipt(tmp_path: Path, provider: str) -> None:
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService

    registry, _state, todo_id = fixture(tmp_path, provider, validation=validation_command("raise SystemExit(1)"))
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry)
    proposal = service.preview({"action_kind": "todo.update", "summary": "Record a validation outcome",
        "normalized_parameters": {"goal_id": "goal-a", "todo_id": todo_id, "agent_id": "agent-a",
            "operation": "complete", "note": "Owner acknowledgement"}, "context": {}, "idempotency_key": "validated-close"})
    failed = service.apply(proposal["proposal_id"])["proposal"]
    assert failed["status"] == "failed"
    assert failed["receipt"] is None
    assert failed["failure"]["error_code"] == "canonical_update_validation_failed"
    assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("passed", [True, False])
def test_packaged_chat_http_user_completion(tmp_path: Path, provider: str, passed: bool) -> None:
    from http.client import HTTPConnection
    from threading import Thread
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir

    registry, _state, todo_id = fixture(tmp_path, provider,
        validation=validation_command(f"raise SystemExit({0 if passed else 1})"))
    store = ChatActionStore(tmp_path / "actions")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.assets_dir = default_chat_assets_dir()
    server.action_store = store
    server.action_service = ChatActionService(store=store, registry_path=registry)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=45)
    try:
        connection.request("GET", "/chat")
        response = connection.getresponse()
        assert response.status == 200
        assert b"<script" in response.read()
        body = {"action_kind": "todo.update", "summary": "Complete the observed action", "context": {},
            "idempotency_key": "http-completion", "normalized_parameters": {"goal_id": "goal-a", "todo_id": todo_id,
                "agent_id": "agent-a", "operation": "complete", "note": "Owner acknowledgement"}}
        connection.request("POST", "/api/actions/preview", body=json.dumps(body), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        preview = json.loads(response.read())
        assert response.status == 201, preview
        proposal_id = preview["proposal"]["proposal_id"]
        connection.request("POST", f"/api/actions/{proposal_id}/apply", body="{}", headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200, result
        assert result["proposal"]["status"] == ("applied" if passed else "failed")
        assert (result["proposal"]["receipt"] is not None) is passed
        rows = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
        assert next(row for row in rows if row["todo_id"] == todo_id)["status"] == ("done" if passed else "open")
        dependent = next(row for row in rows if row["role"] == "agent")
        assert dependent["status"] == ("open" if passed else "blocked")
        assert dependent["claimed_by"] == "agent-a"
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
