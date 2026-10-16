"""Authority facts must survive the real validation-effect/commit boundary."""
from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.todos import add_goal_todo
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from test_native_todo_planning_update import fixture, records


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("entrypoint", ["cli", "chat"])
def test_completion_rejects_registration_revoked_by_real_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    entrypoint: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, _state = fixture(tmp_path, provider)
    marker = tmp_path / "validation-ran"
    # The validation is real and succeeds. It revokes this synthetic actor
    # between admission and commit; no mock supplies the postcondition.
    script = (
        "import json,sys; from pathlib import Path; "
        "p=Path(sys.argv[1]); r=json.loads(p.read_text()); "
        "r['goals'][0]['coordination']['registered_agents'].remove('agent-a'); "
        "p.write_text(json.dumps(r)); Path(sys.argv[2]).write_text('executed')"
    )
    added = add_goal_todo(
        registry_path=registry, goal_id="goal-a", role="user", task_class="user_action",
        agent_id="agent-a", text="Validate the accepted work",
        validation_command_json=json.dumps([sys.executable, "-c", script, str(registry), str(marker)]),
        validation_label="registration-change", validation_timeout_seconds=5,
    )
    before = records(registry)
    if entrypoint == "cli":
        process = subprocess.run([
            sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            "todo", "complete", "--goal-id", "goal-a", "--todo-id", added["todo_id"],
            "--agent-id", "agent-a", "--no-follow-up", "--note", "Independent check closes this action",
        ], capture_output=True, text=True, timeout=45)
        assert marker.exists(), process.stdout + process.stderr
        assert process.returncode != 0, process.stdout
        assert "authority_source_changed" in process.stdout
    else:
        store = ChatActionStore(tmp_path / "actions")
        service = ChatActionService(store=store, registry_path=registry)
        proposal = service.preview({
            "action_kind": "todo.update", "summary": "Complete the validated action", "context": {},
            "idempotency_key": "source-revocation",
            "normalized_parameters": {"goal_id": "goal-a", "todo_id": added["todo_id"],
                "operation": "complete", "agent_id": "agent-a", "no_followup": True,
                "note": "Independent check closes this action"},
        })
        assert not marker.exists(), "preview must not execute validation"
        server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
        server.verbose = False
        server.action_store, server.action_service = store, service
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=45)
        try:
            connection.request("POST", f"/api/actions/{proposal['proposal_id']}/apply", body="{}",
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 409, payload
            assert payload["error_code"] == "action_stale"
            assert payload["write_attempted"] is False
            stored = store.load(proposal["proposal_id"])
            assert stored["status"] == "stale"
            assert not stored.get("receipt"), "Chat must not report canonical success"
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    assert marker.read_text() == "executed"
    assert records(registry) == before
