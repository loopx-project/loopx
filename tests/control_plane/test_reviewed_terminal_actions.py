"""Reviewed terminal actions must recover the business receipt before freshness."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, complete_goal_todo, list_goal_todos


def fixture(tmp_path: Path, provider: str, operation: str, *, validation: str | None = None):
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## User Todo\n\n## Agent Todo\n\n## Completed Work Archive\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": "goal-a", "status": "active", "repo": str(project), "state_file": state.name,
                   "coordination": {"registered_agents": ["agent-a"]}}]}))
    monitor = operation == "stop"
    added = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Observe the supported release" if monitor else "Deliver the bounded result",
        task_class="continuous_monitor" if monitor else "advancement_task", claimed_by="agent-a",
        validation_command=validation,
        **({"monitor_metadata": {"target_key": "release-status", "cadence": "1d", "watch_only": "true"}} if monitor else {}))
    todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    projection = build_todo_runtime_shadow_projection(goal_id="goal-a", todos=todos, handoff_mode="soft_claim")
    initialize_canonical_authority(tmp_path / "runtime", "goal-a", projection, state_path=state, provider=provider)
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry)
    proposal = service.preview({"action_kind": "monitor.update" if monitor else "todo.update",
        "summary": "Close the reviewed work", "normalized_parameters": {"goal_id": "goal-a",
        "todo_id": added["todo_id"], "agent_id": "agent-a", "operation": operation},
        "context": {}, "idempotency_key": "reviewed-terminal"})
    return registry, state, service, proposal


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("operation", ["complete", "stop"])
@pytest.mark.parametrize("failure_boundary", ["display", "action_receipt"])
def test_terminal_recovers_current_projection(tmp_path, monkeypatch, provider, operation, failure_boundary):
    from loopx.control_plane.todos import provider_projection

    registry, state, service, proposal = fixture(tmp_path, provider, operation)
    with monkeypatch.context() as patch:
        if failure_boundary == "display":
            def interrupted(**kwargs):
                raise OSError("Synthetic display interruption")
            patch.setattr(provider_projection, "project_current_canonical_todos", interrupted)
            failed = service.apply(proposal["proposal_id"])["proposal"]
            assert failed["status"] == "failed"
            assert failed["receipt"] is None
        else:
            def lost_response(*args, **kwargs):
                raise ConnectionError("Synthetic response loss after business commit")
            patch.setattr(service.store, "apply", lost_response)
            with pytest.raises(ConnectionError):
                service.apply(proposal["proposal_id"])
    rows = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    assert rows[0]["status"] == "done"
    state.unlink()
    # Simulate restarting Chat after the interrupted response.
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry)
    recovered = service.apply(proposal["proposal_id"])["proposal"]
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["outcome"] == ("monitor_stopped" if operation == "stop" else "todo_completed")
    assert recovered["receipt"]["projection_verified"] is True
    assert state.exists()
    assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"] == rows


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_completion_replay_does_not_need_private_validator(tmp_path, monkeypatch, provider):
    from loopx.control_plane.todos.completion_validation_store import completion_validation_declaration_path

    marker = tmp_path / "validation-count"
    command = shlex.join([sys.executable, "-c", "from pathlib import Path; "
        f"p=Path({str(marker)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')"])
    registry, state, service, proposal = fixture(tmp_path, provider, "complete", validation=command)
    assert not marker.exists(), "preview must not execute validation"
    with monkeypatch.context() as patch:
        def interrupted(*args, **kwargs):
            raise ConnectionError("Synthetic action receipt interruption")
        patch.setattr(service.store, "apply", interrupted)
        with pytest.raises(ConnectionError):
            service.apply(proposal["proposal_id"])
    assert marker.read_text() == "x"
    todo_id = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]["todo_id"]
    state.unlink()
    declaration_path = completion_validation_declaration_path(runtime_root=tmp_path / "runtime", goal_id="goal-a", todo_id=todo_id)
    saved_declaration = declaration_path.read_bytes()
    declaration_path.unlink()
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry)
    result = service.apply(proposal["proposal_id"])["proposal"]
    assert result["status"] == "failed"
    assert result["failure"]["error_code"] == "canonical_update_projection_pending"
    assert result["failure"]["details"]["canonical_committed"] is True
    assert marker.read_text() == "x"
    # The business receipt needs no argv; lossless Markdown still needs the
    # original private declaration. Restore it, never invent a replacement.
    declaration_path.write_bytes(saved_declaration)
    result = service.apply(proposal["proposal_id"])["proposal"]
    assert result["status"] == "applied"
    assert marker.read_text() == "x"
    assert state.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_completion_validation_cannot_cross_a_changed_provider_head(tmp_path, monkeypatch, provider):
    from loopx.control_plane.todos import provider_terminal_lifecycle
    from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable

    marker = tmp_path / "validated"
    command = shlex.join([sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
    registry, _state, _service, _proposal = fixture(tmp_path, provider, "complete", validation=command)
    todo_id = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]["todo_id"]
    execute = provider_terminal_lifecycle.execute_completion_validation_effects
    def concurrent_write(*args, **kwargs):
        result = execute(*args, **kwargs)
        add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent", text="Concurrent accepted work",
            task_class="advancement_task", claimed_by="agent-a")
        return result
    monkeypatch.setattr(provider_terminal_lifecycle, "execute_completion_validation_effects", concurrent_write)
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as caught:
        complete_goal_todo(registry_path=registry, goal_id="goal-a", todo_id=todo_id, agent_id="agent-a",
            no_followup=True, completion_turn_key="validation-race")
    assert caught.value.code == "provider_revision_mismatch"
    assert caught.value.payload["completion_validation_executed"] is True
    assert marker.exists()
    target = next(todo for todo in list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"] if todo["todo_id"] == todo_id)
    assert target["status"] == "open"
    assert not target.get("completed_at")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("operation", ["complete", "stop"])
def test_stale_review_never_closes_current_work(tmp_path, provider, operation):
    registry, _state, service, proposal = fixture(tmp_path, provider, operation)
    add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent", text="Newly accepted dependency",
        task_class="advancement_task", claimed_by="agent-a")
    result = service.apply(proposal["proposal_id"])["proposal"]
    assert result["status"] == "stale"
    assert result["receipt"] is None
    assert all(todo["status"] == "open" for todo in list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("operation", ["complete", "stop"])
def test_packaged_chat_http_recovers_terminal_action(tmp_path, monkeypatch, provider, operation):
    from http.client import HTTPConnection
    from threading import Thread
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir
    from loopx.control_plane.todos import provider_projection

    registry, _state, service, proposal = fixture(tmp_path, provider, operation)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.assets_dir = default_chat_assets_dir()
    server.action_store = service.store
    server.action_service = service
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=45)
    try:
        connection.request("GET", "/chat")
        response = connection.getresponse()
        assert response.status == 200
        assert b"<script" in response.read()
        with monkeypatch.context() as patch:
            def interrupted(**kwargs):
                raise OSError("Synthetic display interruption")
            patch.setattr(provider_projection, "project_current_canonical_todos", interrupted)
            connection.request("POST", f"/api/actions/{proposal['proposal_id']}/apply", body="{}",
                headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            failed = json.loads(response.read())
            assert response.status == 200, failed
            assert failed["proposal"]["status"] == "failed"
            assert failed["proposal"]["receipt"] is None
            assert failed["proposal"]["failure"]["retry_safe"] is True
        connection.request("POST", f"/api/actions/{proposal['proposal_id']}/apply", body="{}",
            headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200, result
        assert result["proposal"]["status"] == "applied"
        assert result["proposal"]["receipt"]["projection_verified"] is True
        assert result["proposal"]["receipt"]["canonical_status"] == "replayed"
        assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]["status"] == "done"
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("turn_key", [None, "named-completion"])
def test_monitor_completion_uses_explicit_identity_modes(tmp_path, monkeypatch, provider, turn_key):
    from loopx.control_plane.todos import provider_terminal_lifecycle

    registry, _, _, _ = fixture(tmp_path, provider, "stop")
    todo_id = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]["todo_id"]
    execute = provider_terminal_lifecycle.effect_runtime_result
    identities = []

    def capture(method, params, **kwargs):
        if method == "coordination.local_authority.todo_terminal":
            assert params["schema_version"] == "loopx_local_coordination_todo_terminal_lifecycle_request_v3"
            assert "operation_id" not in params
            identities.append(params["operation_identity"])
        return execute(method, params, **kwargs)

    monkeypatch.setattr(provider_terminal_lifecycle, "effect_runtime_result", capture)
    kwargs = dict(registry_path=registry, goal_id="goal-a", todo_id=todo_id,
                  agent_id="agent-a", no_followup=True, completion_turn_key=turn_key)
    completed = complete_goal_todo(**kwargs)
    assert completed["provider_status"] == "applied"
    replay = complete_goal_todo(**kwargs)
    assert replay["provider_status"] == "replayed"
    assert replay["original_receipt"] == completed["original_receipt"]
    assert identities
    if turn_key is None:
        assert all(identity == {"kind": "current_monitor_cycle"} for identity in identities)
    else:
        assert all(identity["kind"] == "explicit" and identity["operation_id"] for identity in identities)
    assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"][0]["status"] == "done"
