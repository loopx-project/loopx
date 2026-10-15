"""Caller-supplied execution proof must reach the existing terminal owner."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.work_items.task_lease import acquire_task_lease, inspect_task_lease, release_task_lease
from loopx.todos import add_goal_todo, list_goal_todos


def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, *,
            ttl_seconds: int = 600, validation: bool = False):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    state = tmp_path / "state.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n## Completed Work Archive\n")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"], "handoff_mode": "hard_lease"},
    }]}))
    marker = tmp_path / "validation-count"
    command = shlex.join([sys.executable, "-c", "from pathlib import Path; "
        f"p=Path({str(marker)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')"])
    # These transport tests start with canonical authority; fixture creation
    # must not depend on an unpromoted Markdown writer before the first lease.
    initialize_canonical_authority(runtime, "goal-a",
        build_todo_runtime_shadow_projection(goal_id="goal-a", todos=[], handoff_mode="hard_lease"),
        state_path=state, provider=provider)
    todo_id = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Complete independently validated work", claimed_by="agent-a",
        validation_command=command if validation else None)["todo_id"]
    lease = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id="goal-a",
        todo_id=todo_id, owner="agent-a", idempotency_key="original-execution",
        ttl_seconds=ttl_seconds, expected_version=0)["lease"]
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=registry)
    request = {"action_kind": "todo.update", "summary": "Complete the original work", "context": {},
        "idempotency_key": "original-completion", "normalized_parameters": {
            "goal_id": "goal-a", "todo_id": todo_id, "operation": "complete", "agent_id": "agent-a",
            "task_lease_idempotency_key": lease["idempotency_key"],
            "task_lease_expected_version": lease["version"],
        }}
    return registry, runtime, state, todo_id, service, request


def rows(registry: Path):
    return list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_proof_completes_and_recovers_lost_response(tmp_path, monkeypatch, provider):
    registry, runtime, state, todo_id, service, request = fixture(tmp_path, monkeypatch, provider, validation=True)
    before = rows(registry)
    preview = service.preview(request)
    assert rows(registry) == before
    assert not (tmp_path / "validation-count").exists()
    assert preview["normalized_parameters"] == request["normalized_parameters"]
    with monkeypatch.context() as patch:
        def lost_response(*args, **kwargs):
            raise ConnectionError("Synthetic response loss after canonical completion")
        patch.setattr(service.store, "apply", lost_response)
        with pytest.raises(ConnectionError):
            service.apply(preview["proposal_id"])
    after = rows(registry)
    assert after[0]["status"] == "done"
    assert (tmp_path / "validation-count").read_text() == "x"
    lease = inspect_task_lease(registry_path=registry, runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)["lease"]
    assert lease["status"] == "released"
    state.unlink()
    recovered = service.apply(preview["proposal_id"])["proposal"]
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["outcome"] == "todo_completed"
    assert state.exists()
    assert rows(registry) == after
    assert (tmp_path / "validation-count").read_text() == "x"
    assert service.apply(preview["proposal_id"])["proposal"]["receipt"] == recovered["receipt"]
    assert inspect_task_lease(registry_path=registry, runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)["lease"] == lease


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["missing", "key", "version", "actor"])
def test_missing_or_foreign_proof_never_changes_work(tmp_path, monkeypatch, provider, change):
    registry, runtime, state, todo_id, service, request = fixture(tmp_path, monkeypatch, provider)
    parameters = request["normalized_parameters"]
    if change == "missing":
        parameters.pop("task_lease_idempotency_key")
        parameters.pop("task_lease_expected_version")
    elif change == "key":
        parameters["task_lease_idempotency_key"] = "different-execution"
    elif change == "version":
        parameters["task_lease_expected_version"] += 1
    else:
        parameters["agent_id"] = "agent-b"
    before, display = rows(registry), state.read_bytes()
    lease = inspect_task_lease(registry_path=registry, runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)["lease"]
    with pytest.raises(ValueError):
        service.preview(request)
    assert rows(registry) == before
    assert state.read_bytes() == display
    assert inspect_task_lease(registry_path=registry, runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)["lease"] == lease
    assert service.store.list() == []


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_proof_revoked_between_preview_and_apply_preserves_work(tmp_path, monkeypatch, provider):
    registry, runtime, _state, todo_id, service, request = fixture(tmp_path, monkeypatch, provider)
    proposal = service.preview(request)
    release_task_lease(registry_path=registry, runtime_root=runtime, goal_id="goal-a",
        todo_id=todo_id, owner="agent-a", idempotency_key="original-execution", expected_version=1)
    before = rows(registry)
    result = service.apply(proposal["proposal_id"])["proposal"]
    assert result["status"] != "applied"
    assert result["receipt"] is None
    assert rows(registry) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_expired_explicit_proof_does_not_acquire_a_new_execution(tmp_path, monkeypatch, provider):
    import time

    registry, runtime, state, todo_id, service, request = fixture(tmp_path, monkeypatch, provider, ttl_seconds=1)
    time.sleep(1.2)
    before, display = rows(registry), state.read_bytes()
    lease = inspect_task_lease(registry_path=registry, runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)["lease"]
    with pytest.raises(ValueError):
        service.preview(request)
    assert rows(registry) == before
    assert state.read_bytes() == display
    assert inspect_task_lease(registry_path=registry, runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)["lease"] == lease


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_chat_http_transports_original_proof(tmp_path, monkeypatch, provider):
    from http.client import HTTPConnection
    from threading import Thread
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    registry, _runtime, _state, _todo_id, service, request = fixture(tmp_path, monkeypatch, provider)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose, server.action_service, server.action_store = False, service, service.store
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=45)
    try:
        connection.request("POST", "/api/actions/preview", body=json.dumps(request),
            headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        preview = json.loads(response.read())
        assert response.status == 201, preview
        proposal = preview["proposal"]
        assert proposal["normalized_parameters"] == request["normalized_parameters"]
        assert rows(registry)[0]["status"] == "open"
        for _ in range(2):
            connection.request("POST", f"/api/actions/{proposal['proposal_id']}/apply", body="{}",
                headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            result = json.loads(response.read())
            assert response.status == 200, result
            assert result["proposal"]["status"] == "applied"
        assert rows(registry)[0]["status"] == "done"
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("parameters", [
    {"task_lease_idempotency_key": "original-execution"},
    {"task_lease_expected_version": 1},
    {"task_lease_idempotency_key": "original-execution", "task_lease_expected_version": True},
    {"task_lease_idempotency_key": "original-execution", "task_lease_expected_version": -1},
    {"task_lease_idempotency_key": "original-execution", "task_lease_expected_version": 1, "operation": "edit", "note": "Reviewed"},
])
def test_proof_is_a_complete_terminal_transport_input(tmp_path, monkeypatch, parameters):
    registry, _runtime, _state, _todo_id, service, request = fixture(tmp_path, monkeypatch, "file")
    base = request["normalized_parameters"]
    base.pop("task_lease_idempotency_key")
    base.pop("task_lease_expected_version")
    base.update(parameters)
    before = rows(registry)
    with pytest.raises(ValueError):
        service.preview(request)
    assert rows(registry) == before
