"""The public update-done caller over real canonical providers and validation."""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, list_goal_todos


def fixture(
    tmp_path: Path, provider: str, *,
    validation: str | None = None,
    validation_argv: list[str] | None = None,
    validation_label: str | None = None,
) -> tuple[Path, Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## User Todo\n\n## Agent Todo\n\n## Completed Work Archive\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": "goal-a", "status": "active", "repo": str(project), "state_file": state.name,
                   "coordination": {"registered_agents": ["agent-a"]}}]}), encoding="utf-8")
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=[], handoff_mode="soft_claim"
    )
    initialize_canonical_authority(
        tmp_path / "runtime",
        "goal-a",
        projection,
        state_path=state,
        provider=provider,
    )
    dependent = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Continue after the observed outcome", task_class="advancement_task",
        status="blocked", claimed_by="agent-a")
    added = add_goal_todo(registry_path=registry, goal_id="goal-a", role="user",
        text="Record the observed outcome", task_class="user_action", validation_command=validation,
        validation_command_json=json.dumps(validation_argv) if validation_argv is not None else None,
        validation_label=validation_label, unblocks_todo_id=dependent["todo_id"])
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
    return shlex.join([sys.executable, "-c", source])


def blocking_validator(root: Path) -> str:
    script = root / "check_completion.py"
    script.write_text(
        "import os, time\n"
        "from pathlib import Path\n"
        "root = Path(__file__).parent\n"
        "(root / f'validation-{os.getpid()}').touch()\n"
        "deadline = time.monotonic() + 15\n"
        "while not (root / 'release').exists():\n"
        "    if time.monotonic() >= deadline: raise SystemExit(2)\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    return validation_command(f"import runpy; runpy.run_path({str(script)!r})")


def wait_for_validation(root: Path) -> None:
    deadline = time.monotonic() + 10
    while not list(root.glob("validation-*")):
        assert time.monotonic() < deadline, "completion did not start the validator"
        time.sleep(0.02)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(("first", "second"), [
    ("complete", "complete"), ("update", "update"),
    ("complete", "update"), ("update", "complete"),
])
def test_concurrent_completion_does_not_repeat_the_validator(
    tmp_path: Path, provider: str, first: str, second: str,
) -> None:
    registry, _state, todo_id = fixture(tmp_path, provider, validation=blocking_validator(tmp_path))
    before = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    target = ["--goal-id", "goal-a", "--todo-id", todo_id, "--agent-id", "agent-a"]
    cli = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry), "todo"]
    commands = {
        "complete": [*cli, "complete", *target, "--evidence", "evidence:checked"],
        "update": [*cli, "update", *target, "--status", "done",
                   "--update-operation-id", "concurrent-completion"],
    }
    with subprocess.Popen(commands[first], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as original:
        try:
            wait_for_validation(tmp_path)
            duplicate = subprocess.run(commands[second], capture_output=True, text=True, timeout=10)
            busy = json.loads(duplicate.stdout)
            assert duplicate.returncode == 1 and busy["error_code"] == "completion_validation_in_progress", busy
            assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"] == before
            assert len(list(tmp_path.glob("validation-*"))) == 1
        finally:
            (tmp_path / "release").touch()
        stdout, stderr = original.communicate(timeout=20)
    completed = json.loads(stdout)
    assert original.returncode == 0 and completed["changed"] is True, (completed, stderr)
    replay = json.loads(subprocess.check_output(commands[first], text=True, timeout=20))
    assert (replay.get("provider_status") or replay["status"]) == "replayed", replay
    assert len(list(tmp_path.glob("validation-*"))) == 1
    todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    assert next(row for row in todos if row["todo_id"] == todo_id)["status"] == "done"
    assert next(row for row in todos if row["role"] == "agent")["status"] == "open"


@pytest.mark.skipif(os.name != "posix", reason="POSIX supervisor and process-group ownership")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("entrypoint", ["complete", "update"])
def test_killed_completion_owner_keeps_validation_fenced(
    tmp_path: Path, provider: str, entrypoint: str,
) -> None:
    from loopx.control_plane.todos.completion_validation_store import completion_validation_declaration_path
    from loopx.file_lock import lock_holder_path

    registry, _state, todo_id = fixture(tmp_path, provider, validation=blocking_validator(tmp_path))
    execution = completion_validation_declaration_path(
        runtime_root=tmp_path / "runtime", goal_id="goal-a", todo_id=todo_id,
    ).with_suffix(".execution")
    target = ["--goal-id", "goal-a", "--todo-id", todo_id, "--agent-id", "agent-a"]
    options = (["--evidence", "evidence:checked"] if entrypoint == "complete" else
               ["--status", "done", "--update-operation-id", "killed-owner-completion"])
    command = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
               "todo", entrypoint, *target, *options]
    # Pause cleanup after it observes owner EOF and before it kills the
    # validator. Inject only into the test CLI's private supervisor child.
    cleanup_barrier = """
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from loopx.extensions import process_runtime
cleanup = process_runtime.terminate_process_tree
root = Path(os.environ["VALIDATION_TEST_ROOT"])

def pause(process, grace_seconds):
    (root / "cleaning").touch()
    deadline = time.monotonic() + 12
    while not (root / "allow-cleanup").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    cleanup(process, grace_seconds)
    (root / "cleaned").touch()

process_runtime.terminate_process_tree = pause
"""
    instrumented = (
        "from loopx.cli import main; "
        "from loopx.control_plane.runtime import validation_command; "
        f"validation_command._SUPERVISOR_ENTRY = {cleanup_barrier!r} "
        "+ validation_command._SUPERVISOR_ENTRY; "
        "raise SystemExit(main())"
    )
    original = subprocess.Popen(
        [sys.executable, "-c", instrumented, *command[3:]],
        env={**os.environ, "VALIDATION_TEST_ROOT": str(tmp_path)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_validation(tmp_path)
        first = next(tmp_path.glob("validation-*"))
        pid = int(first.name.removeprefix("validation-"))
        original.kill()
        assert original.wait(timeout=5) == -signal.SIGKILL
        assert not json.loads(lock_holder_path(execution).read_text()).get("released_at")

        deadline = time.monotonic() + 5
        while not (tmp_path / "cleaning").exists():
            assert time.monotonic() < deadline, "supervisor did not observe its caller's exit"
            time.sleep(0.02)
        os.kill(pid, 0)
        busy = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert busy.returncode == 1, busy.stdout + busy.stderr
        assert json.loads(busy.stdout)["error_code"] == "completion_validation_in_progress"
        assert list(tmp_path.glob("validation-*")) == [first]

        (tmp_path / "allow-cleanup").touch()
        deadline = time.monotonic() + 5
        while not (tmp_path / "cleaned").exists():
            assert time.monotonic() < deadline, "validator cleanup did not finish"
            time.sleep(0.02)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        (tmp_path / "release").touch()
        deadline = time.monotonic() + 10
        while True:
            retry = subprocess.run(command, capture_output=True, text=True, timeout=10)
            completed = json.loads(retry.stdout)
            if completed.get("error_code") != "completion_validation_in_progress":
                break
            assert time.monotonic() < deadline, "supervisor did not retire its execution fence"
            time.sleep(0.02)
        assert retry.returncode == 0 and completed["changed"] is True, completed
        replay = json.loads(subprocess.check_output(command, text=True, timeout=20))
        assert (replay.get("provider_status") or replay["status"]) == "replayed"
        assert len(list(tmp_path.glob("validation-*"))) == 2
    finally:
        (tmp_path / "allow-cleanup").touch()
        (tmp_path / "release").touch()
        if original.poll() is None:
            original.kill()
        original.wait(timeout=5)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_packaged_chat_reports_concurrent_completion_without_failing_original(
    tmp_path: Path, provider: str,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from http.client import HTTPConnection
    from threading import Thread
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir

    registry, _state, todo_id = fixture(tmp_path, provider, validation=blocking_validator(tmp_path))
    store = ChatActionStore(tmp_path / "actions")
    service = ChatActionService(store=store, registry_path=registry)
    proposal = service.preview({"action_kind": "todo.update", "summary": "Complete the action",
        "idempotency_key": "concurrent-chat-completion", "context": {}, "normalized_parameters": {
            "goal_id": "goal-a", "todo_id": todo_id, "agent_id": "agent-a", "operation": "complete",
            "note": "Observed result",
        }})
    proposal_id = proposal["proposal_id"]
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.assets_dir = default_chat_assets_dir()
    server.action_store = store
    server.action_service = service
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def apply() -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        try:
            connection.request("POST", f"/api/actions/{proposal_id}/apply", body="{}",
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            original = pool.submit(apply)
            try:
                wait_for_validation(tmp_path)
                status, busy = apply()
                assert status == 409 and busy["error_code"] == "action_conflict", busy
                assert "already running" in busy["error"]
                assert store.load(proposal_id)["status"] == "applying"
                assert len(list(tmp_path.glob("validation-*"))) == 1
            finally:
                (tmp_path / "release").touch()
            status, result = original.result(timeout=15)
            assert status == 200 and result["proposal"]["status"] == "applied", json.dumps(result)
        assert result["proposal"]["receipt"]["outcome"] == "todo_completed"
        status, replay = apply()
        assert status == 200 and replay["proposal"]["receipt"] == result["proposal"]["receipt"]
        assert len(list(tmp_path.glob("validation-*"))) == 1
        rows = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
        assert next(row for row in rows if row["todo_id"] == todo_id)["status"] == "done"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("command", ["complete", "update"])
def test_unlaunchable_validator_returns_typed_failure(
    tmp_path: Path, provider: str, command: str,
) -> None:
    validator = tmp_path / "private validator.exe"
    validator.write_bytes(b"invalid executable format\n")
    validator.chmod(0o700)
    registry, state, todo_id = fixture(
        tmp_path, provider, validation=shlex.join([str(validator)]),
    )
    before = list_goal_todos(registry_path=registry, goal_id="goal-a")
    state_before = state.read_bytes()
    args = ["todo", command, "--goal-id", "goal-a", "--todo-id", todo_id, "--agent-id", "agent-a"]
    if command == "update":
        args.extend(["--status", "done"])
    process = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry), *args],
        capture_output=True, text=True, timeout=45,
    )
    assert process.returncode == 1, process.stdout + process.stderr
    assert validator.name not in process.stdout + process.stderr
    result = json.loads(process.stdout)
    assert result["validation_blocked_completion"] is True
    receipt = result["validation_failure"]["validation_receipt"]
    assert receipt["status"] == "command_not_run"
    assert receipt["exit_code"] is None and receipt["passed"] is False
    assert receipt["local_path_captured"] is False
    after = list_goal_todos(registry_path=registry, goal_id="goal-a")
    assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]
    assert after["todos"] == before["todos"]
    assert state.read_bytes() == state_before


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
@pytest.mark.parametrize("case", ["multiline_command", "multiline_argv", "failed"])
def test_packaged_chat_http_user_completion(tmp_path: Path, provider: str, case: str) -> None:
    from http.client import HTTPConnection
    from threading import Thread
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir

    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
    from loopx.control_plane.todos.completion_validation_projection import completion_validation_declaration

    passed = case != "failed"
    label = " Check  spacing \t "
    marker = tmp_path / "validator-output"
    validation_source = (
        "from pathlib import Path\n"
        "if __name__ == '__main__':\n"
        f"    with Path({str(marker)!r}).open('a') as output:\n"
        "        output.write('two  spaces % --><--')\n"
    )
    command = validation_command(validation_source if passed else "raise SystemExit(1)")
    argv = [sys.executable, "-c", validation_source]
    use_argv = case == "multiline_argv"
    registry, state, todo_id = fixture(
        tmp_path, provider,
        validation=None if use_argv else command,
        validation_argv=argv if use_argv else None,
        validation_label=label if passed else None,
    )
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
        if passed:
            displayed, _, _ = parse_todo_source(state.read_text(encoding="utf-8"))
            row = next(item for item in displayed["user"] if item["todo_id"] == todo_id)
            assert row["status"] == "done"
            declaration = completion_validation_declaration(row)
            assert declaration is not None
            assert declaration["validation_label"] == label
            if use_argv:
                assert declaration["validation_command_argv"] == argv
            else:
                assert declaration["validation_command"] == command

            connection.request("POST", f"/api/actions/{proposal_id}/apply", body="{}",
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            replay = json.loads(response.read())
            assert response.status == 200, replay
            assert replay["proposal"]["receipt"] == result["proposal"]["receipt"]
            assert marker.read_text(encoding="utf-8") == "two  spaces % --><--"
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
