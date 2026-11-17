"""Packaged-compatible HTTP transport with real cold source, backup and stores."""
from __future__ import annotations

import http.client
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import socket
import sys
import time

import pytest

from loopx.control_plane.effect_runtime import effect_runtime_result, restart_effect_runtime
from test_cold_source_import_cli import workspace


@pytest.fixture(params=[True, False], ids=["source-modules-present", "source-modules-absent"])
def source_modules_present(request):
    return request.param


@pytest.fixture(params=[False, True], ids=["physical-registry", "external-registry-parent-alias"])
def cold_api(tmp_path, monkeypatch, request, source_modules_present):
    _, state, _, body, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    assert not (
        receiver / "loopx/control_plane/coordination/runtime_shadow_writer_adapter.py"
    ).exists()
    if not source_modules_present:
        for name in ("todos.py", "bootstrap.py",
            "control_plane/coordination/local_authority_shadow_outbox.py"):
            # The consumer-retirement fixture can already have removed part
            # of this same set before the cold receiver is copied.
            (receiver / "loopx" / name).unlink(missing_ok=True)
    registry = tmp_path / "project/.loopx/registry.json"
    if request.param in ("global-source", "global-source-race"):
        # Keep Chat bound to the source runtime, but invoke it through the
        # global registry whose own runtime holds the machine index.
        global_root = tmp_path / "machine-runtime"
        global_root.mkdir()
        payload = json.loads(registry.read_text())
        payload["common_runtime_root"] = str(global_root)
        payload["registry_role"] = "global-local"
        payload["goals"][0]["source_registry"] = str(registry)
        registry = global_root / "registry.global.json"
        registry.write_text(json.dumps(payload))
    elif request.param:
        # The configured registry may live outside conventional Goal folders.
        # Only its explicit backup member can cover this observed source.
        external = tmp_path / "project/source-registry.json"
        registry.rename(external)
        alias = tmp_path / "project-route"
        alias.symlink_to(tmp_path / "project", target_is_directory=True)
        registry = alias / "source-registry.json"
    ready = tmp_path / "server-ready.json"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        requested_port = reservation.getsockname()[1]
    script = """
import json,sys,loopx
from pathlib import Path
from loopx.chat_server import serve_chat
registry, runtime, ready = map(Path, sys.argv[1:4])
port = int(sys.argv[4])
staged_ready = ready.with_suffix('.tmp')
staged_ready.write_text(json.dumps({'port': port, 'package': loopx.__file__}), encoding='utf-8')
staged_ready.replace(ready)
if len(sys.argv) > 5:
    import time
    import loopx.presentation.goal_storage_api as storage
    original_effect = storage.source_effect_runtime_result
    def paused_import(operation, payload, **kwargs):
        pause = runtime / 'pause-apply'
        if (operation == 'coordination.cold_source.import'
                and payload.get('action') == 'apply' and pause.exists()):
            pause.with_suffix('.selected').write_text(payload['expected_source_registry'])
            deadline = time.monotonic() + 20
            while not pause.with_suffix('.resume').exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('test did not resume its paused Apply')
                time.sleep(.01)
        return original_effect(operation, payload, **kwargs)
    storage.source_effect_runtime_result = paused_import
serve_chat(registry_path=registry, runtime_root_override=runtime,
    scan_roots=[Path(json.loads(registry.read_text())['goals'][0]['repo'])], host='127.0.0.1', port=port,
    goal_id='cold', open_browser=False, verbose=False)
"""
    server_arguments = [str(registry), str(runtime), str(ready), str(requested_port)]
    if request.param == "global-source-race":
        server_arguments.append("pause")
    error_path = tmp_path / "server-errors.txt"
    error_stream = error_path.open("w")
    server = subprocess.Popen([sys.executable, "-u", "-c", script,
        *server_arguments], cwd=tmp_path,
        env={**env, "LOOPX_USAGE_PING": "0"}, stdout=subprocess.DEVNULL, stderr=error_stream)

    def execute(script):
        child = subprocess.run([sys.executable, "-c",
            "import loopx; from pathlib import Path; "
            f"assert Path(loopx.__file__) == Path({str(receiver / 'loopx/__init__.py')!r});\n" + script],
            cwd=tmp_path, env={**env, "LOOPX_USAGE_PING": "0"},
            capture_output=True, text=True, timeout=90)
        assert child.returncode == 0, child.stdout + child.stderr
        return json.loads(child.stdout)

    def restart():
        nonlocal server, port
        server.terminate()
        server.wait(timeout=10)
        ready.unlink()
        server = subprocess.Popen([sys.executable, "-u", "-c", script,
            *server_arguments], cwd=tmp_path,
            env={**env, "LOOPX_USAGE_PING": "0"}, stdout=subprocess.DEVNULL, stderr=error_stream)
        port = wait_ready()

    def wait_ready():
        deadline = time.monotonic() + 30
        while True:
            assert server.poll() is None, error_path.read_text()
            assert time.monotonic() < deadline, "receiver App did not start"
            if ready.exists():
                startup = json.loads(ready.read_text())
                assert startup["package"] == str(receiver / "loopx/__init__.py")
                try:
                    client = http.client.HTTPConnection("127.0.0.1", startup["port"], timeout=1)
                    client.request("GET", "/chat/")
                    response = client.getresponse()
                    assert response.status == 200
                    assert b"<script" in response.read()
                    client.close()
                    return startup["port"]
                except (ConnectionError, OSError):
                    pass
            time.sleep(.01)

    def call(action="", payload=None, origin=None):
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=90)
        path = action if action.startswith("/") else "/api/chat/goal-storage" + (f"/import/{action}" if action else "?goal_id=cold")
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        client.request("POST" if payload is not None else "GET", path,
            body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = client.getresponse()
        value = json.loads(response.read())
        code = response.status
        client.close()
        public = json.dumps(value)
        assert str(tmp_path) not in public
        if not action.startswith("/"):
            assert body not in public
        assert "plan_path" not in value and "source_snapshot" not in value
        return code, value

    def read():
        return effect_runtime_result("coordination.local_authority.todo_list", {
            "schema_version": "loopx_local_coordination_todo_list_request_v0",
            "runtime_root": str(runtime), "goal_id": "cold", "role": None, "status": None,
            "todo_id": None, "agent_id": None, "limit": None})

    try:
        port = wait_ready()
        yield call, read, state, body, runtime, registry, execute, restart
    finally:
        if server.poll() is None:
            server.terminate()
        server.wait(timeout=10)
        error_stream.close()
        restart_effect_runtime()


def prepare(call, provider="sqlite", handoff_mode="hard_lease"):
    code, value = call("preview", {"goal_id": "cold", "provider": provider, "handoff_mode": handoff_mode})
    assert code == 200 and value["ok"], value
    return value


def carrier(plan):
    return {key: plan[key] for key in ("goal_id", "operation_id", "plan_sha256")}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cold_http_preview_reload_confirm_original_recovery(cold_api, provider):
    call, read, state, body, runtime, registry, execute, restart = cold_api
    original = state.read_bytes()
    assert call()[1]["current"]["canonical"] is False
    plan = prepare(call, provider, handoff_mode="soft_claim")
    assert plan["source_inventory"] == {"todo_count": 2, "archived_todo_count": 1,
        "lease_count": 0, "source_handoff_mode": "legacy"}
    assert plan["coordination_source_backup_verified"] is True
    assert plan["complete_goal_backup_verified"] is False
    assert plan["authority_changed"] is False and not plan["legacy_writer_fenced"]
    saved = carrier(plan)
    restart()
    code, observed = call("recover", saved)
    assert code == 200 and observed["status"] == "prepared", observed
    assert observed["operation_id"] == plan["operation_id"]
    assert not observed["legacy_writer_fenced"] and not observed["current"]["canonical"]
    assert state.read_bytes() == original
    assert not list(runtime.rglob("legacy-writer-fence-*.json"))
    code, refused = call("apply", {**saved, "writers_stopped": False})
    assert code == 409 and refused["reason_code"] == "cold_import_operator_stop_confirmation_required"
    code, applied = call("apply", {**saved, "writers_stopped": True})
    assert code == 200 and applied["status"] == "applied", applied
    assert applied["execution_authority_granted"] is False
    assert applied["current"]["provider"] == provider
    rows = read()["todos"]
    assert next(row for row in rows if row["todo_id"] == "todo_current")["text"] == body
    assert next(row for row in rows if row["todo_id"] == "todo_archived")["evidence"] == "original"
    def action(kind, parameters, key):
        code, preview = call("/api/actions/preview", {
            "action_kind": kind, "summary": "Review the imported Goal task",
            "normalized_parameters": {"goal_id": "cold", **parameters},
            "context": {}, "idempotency_key": key})
        assert code == 201, preview
        proposal = preview["proposal"]
        code, applied = call(f"/api/actions/{proposal['proposal_id']}/apply", {})
        assert code == 200 and applied["proposal"]["status"] == "applied", applied
        return applied["proposal"]["receipt"]

    created = action("todo.create", {"text": "Keep later write", "priority": "P3"}, "later-create")
    later_id = created["resource_ids"]["todo_id"]
    action("todo.update", {"todo_id": later_id, "operation": "edit", "priority": "P4",
        "text": "Keep the edited later write", "note": "Original metadata", "agent_id": "agent-a"}, "later-edit")
    action("todo.update", {"todo_id": later_id, "operation": "complete",
        "agent_id": "agent-a", "no_followup": True}, "later-complete")
    code, detail = call(f"/api/chat/todo/detail?goal_id=cold&todo_id={later_id}")
    assert code == 200 and detail["text"] == "[P4] Keep the edited later write", detail
    assert call("/api/chat/todo/detail?goal_id=cold&todo_id=todo_current")[1]["text"] == body
    assert call("/api/chat/todo/detail?goal_id=cold&todo_id=todo_archived")[1]["archive_state"] == "archive"
    rows = read()["todos"]
    target = next(row for row in rows if row["todo_id"] == later_id)
    assert target["status"] == "done" and target["priority"] == "P4"
    assert target["note"] == "Original metadata"
    assert target["text"] == "[P4] Keep the edited later write"
    later = read()
    state.unlink()
    restart()
    code, recovered = call("recover", saved)
    assert code == 200 and recovered["status"] == "replayed", recovered
    assert recovered["current"]["todo_count"] == 3
    assert read() == later
    assert call("apply", {**saved, "writers_stopped": True})[0] == 200
    assert read() == later


@pytest.mark.parametrize("cold_api", ["global-source"], indirect=True)
def test_cold_http_binds_global_mirror_to_the_registered_source(cold_api):
    call, read, state, body, runtime, registry, _, restart = cold_api
    original_state = state.read_bytes()
    original_mirror = registry.read_bytes()
    mirror = json.loads(original_mirror)
    source = runtime.parent / "project/.loopx/registry.json"

    code, observed = call()
    assert code == 200 and observed["current"]["canonical"] is False, observed
    assert observed["cold_source"]["active_todo_count"] == 1
    plan = prepare(call, handoff_mode="soft_claim")
    saved = carrier(plan)
    assert plan["coordination_source_backup_verified"] is True
    assert plan["source_inventory"]["archived_todo_count"] == 1
    restart()
    code, pending = call()
    assert code == 200 and pending["pending_preview"] == saved
    assert call("recover", saved)[1]["status"] == "prepared"
    code, refused = call("apply", {**saved, "writers_stopped": False})
    assert code == 409 and refused["reason_code"] == "cold_import_operator_stop_confirmation_required"

    # The saved plan still has an intact source file, the same Goal and the
    # same target store. An explicit rebinding must still invalidate Apply.
    other_registry = source.with_name("other-registry.json")
    other_registry.write_bytes(source.read_bytes())
    mirror["goals"][0]["source_registry"] = str(other_registry)
    registry.write_text(json.dumps(mirror))
    code, refused = call("apply", {**saved, "writers_stopped": True})
    assert code == 409 and refused["reason_code"] == "source_registry_changed_retry"
    assert not list(runtime.rglob("legacy-writer-fence-*.json"))

    registry.write_bytes(original_mirror)
    code, applied = call("apply", {**saved, "writers_stopped": True})
    assert code == 200 and applied["status"] == "applied", applied
    assert applied["current"]["provider"] == "sqlite"
    assert applied["execution_authority_granted"] is False
    assert next(row["text"] for row in read()["todos"] if row["todo_id"] == "todo_current") == body
    assert state.read_bytes() == original_state
    assert not list(registry.parent.rglob("legacy-writer-fence-*.json"))


@pytest.mark.parametrize("cold_api", ["global-source-race"], indirect=True)
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cold_http_source_rebind_is_ordered_with_cutover(cold_api, provider):
    from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path
    from loopx.file_lock import exclusive_cross_runtime_file_lock
    from loopx.global_registry import GlobalRegistryReduction, mutate_global_registry

    call, read, state, body, runtime, registry, _, _ = cold_api
    plan = prepare(call, provider, handoff_mode="soft_claim")
    original = state.read_bytes()
    source = runtime.parent / "project/.loopx/registry.json"
    replacement = source.with_name("replacement-registry.json")
    replacement.write_bytes(source.read_bytes())
    pause = runtime / "pause-apply"
    pause.touch()

    def rebind():
        def reduce(payload):
            payload["goals"][0]["source_registry"] = str(replacement)
            return GlobalRegistryReduction(payload, {"bound": "replacement"})
        return mutate_global_registry(registry, "test-source-rebind", reduce)

    with ThreadPoolExecutor(max_workers=2) as pool:
        applying = pool.submit(call, "apply", {**carrier(plan), "writers_stopped": True})
        deadline = time.monotonic() + 15
        try:
            while not pause.with_suffix(".selected").exists():
                assert not applying.done(), applying.result() if applying.done() else None
                assert time.monotonic() < deadline, "Apply did not reach its effect boundary"
                time.sleep(.01)
            assert pause.with_suffix(".selected").read_text() == str(source)
            # This is the existing registry writer's actual cross-runtime lock,
            # not a sleep-based guess that the competing thread is blocked.
            serialized = False
            try:
                with exclusive_cross_runtime_file_lock(registry, timeout_seconds=0):
                    pass
            except TimeoutError:
                serialized = True
            if serialized:
                writer = pool.submit(rebind)
            else:
                rebind()
        finally:
            pause.with_suffix(".resume").touch()
        code, result = applying.result(timeout=90)
        fence = legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="cold")
        if serialized:
            assert code == 200 and result["status"] == "applied", result
            assert writer.result(timeout=10)["wrote"]
            assert fence.exists()
            assert next(row["text"] for row in read()["todos"] if row["todo_id"] == "todo_current") == body
        else:
            # A writer that commits first invalidates the old selected source.
            # The original bug returned applied and fenced that old source.
            assert code == 409, {"response": result, "legacy_fence_exists": fence.exists()}
            assert result["authority_changed"] is False and not fence.exists()
            assert call()[1]["current"]["canonical"] is False
        assert state.read_bytes() == original
        assert json.loads(registry.read_text())["goals"][0]["source_registry"] == str(replacement)


def test_cold_http_changed_source_backup_and_untrusted_inputs(cold_api):
    call, read, state, _, runtime, _, _, _ = cold_api
    plan = prepare(call)
    saved = carrier(plan)
    assert call("apply", {**saved, "writers_stopped": True, "plan": "injected"})[0] == 400
    assert call("recover", {**saved, "operation_id": "../wrong"})[0] == 400
    assert call("recover", {**saved, "goal_id": "unknown"})[0] == 400
    assert call("preview", {"goal_id": "cold", "provider": "sqlite", "handoff_mode": "hard_lease"},
        "https://untrusted.example")[0] == 403
    assert call("preview", {"goal_id": "cold", "provider": "postgresql", "handoff_mode": "hard_lease"})[0] == 409
    assert call("preview", {"goal_id": "cold", "provider": "sqlite", "handoff_mode": "legacy"})[0] == 409
    state.write_text(state.read_text() + "\nChanged after preview\n")
    code, refused = call("apply", {**saved, "writers_stopped": True})
    assert code == 409 and refused["reason_code"] == "source_changed_retry", refused
    assert not list(runtime.rglob("legacy-writer-fence-*.json"))

    fresh = prepare(call)
    archives = list((runtime / "backups/cold-import").glob(f"*{fresh['operation_id']}*.tar.gz"))
    assert len(archives) == 1
    archives[0].write_bytes(b"Damaged after review")
    code, refused = call("apply", {**carrier(fresh), "writers_stopped": True})
    assert code == 409 and refused["reason_code"] == "cold_import_backup_changed", refused
    assert call()[1]["current"]["canonical"] is False
    assert not list(runtime.rglob("legacy-writer-fence-*.json"))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_prose_keeps_coordination_and_rejects_injected_todo(cold_api, provider):
    call, read, state, _, runtime, registry, execute, _ = cold_api
    plan = prepare(call, provider)
    assert call("apply", {**carrier(plan), "writers_stopped": True})[1]["status"] == "applied"
    state.write_text(state.read_text().replace("## Agent Todo", "## Progress Ledger\n\n## Agent Todo", 1))
    index = runtime / "goals/cold/runs/index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"generated_at": "2026-09-05T00:00:00Z",
        "json_path": "run.json", "markdown_path": "run.md", "classification": "continue"}) + "\n")
    before = read()
    result = execute(f"""
import json
from loopx.feedback import append_human_reward
from loopx.control_plane.coordination.legacy_writer_fence import ActiveStateAuthorityMutationError
registry = Path({str(registry)!r})
reward = {{'recorded_at': '2026-09-05T00:00:01Z', 'decision': 'continue',
    'reward': 'positive', 'reason_summary': 'Review accepted.'}}
args = dict(registry_path=registry, runtime_root_override=None, goal_id='cold',
    run_generated_at=None, reward=reward, actor_kind='owner', write_active_state_summary=True)
assert append_human_reward(**args)['appended'] is True
state, index = Path({str(state)!r}), Path({str(index)!r})
assert 'Review accepted.' in state.read_text()
original = state.read_bytes(), index.read_bytes(), registry.read_bytes()
reward['reason_summary'] = 'Review.\\n## Agent Todo\\n- [ ] Injected task.'
try:
    append_human_reward(**args)
except ActiveStateAuthorityMutationError as error:
    assert error.code == 'active_state_authority_mutation_forbidden'
else:
    raise AssertionError('prose writer accepted a Todo mutation')
assert (state.read_bytes(), index.read_bytes(), registry.read_bytes()) == original
print(json.dumps({{'accepted_prose': True, 'rejected_todo': True}}))
""")
    assert result == {"accepted_prose": True, "rejected_todo": True}
    assert read() == before
    assert not (runtime / "authority-shadow/outbox").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_registered_runtime_alias_import_preserves_original_operation(cold_api, provider):
    call, read, state, _, runtime, registry, execute, restart = cold_api
    alias = runtime.parent / "registered-runtime-alias"
    alias.symlink_to(runtime, target_is_directory=True)
    registered = json.loads(registry.read_text())
    registered["common_runtime_root"] = str(alias)
    registry.write_text(json.dumps(registered))
    original = state.read_bytes()

    assert call()[1]["current"]["canonical"] is False
    plan = prepare(call, provider)
    saved = carrier(plan)
    assert call("recover", saved)[1]["status"] == "prepared"
    assert not list(runtime.rglob("legacy-writer-fence-*.json"))
    assert call("apply", {**saved, "writers_stopped": False})[0] == 409
    code, applied = call("apply", {**saved, "writers_stopped": True})
    assert code == 200 and applied["status"] == "applied", applied
    assert applied["execution_authority_granted"] is False
    assert state.read_bytes() == original
    result = execute(f"""
import json
from loopx.control_plane.todos.mutation_api import add_goal_todo
print(json.dumps(add_goal_todo(registry_path=Path({str(registry)!r}), goal_id='cold',
    role='agent', text='Keep the write after aliased import', claimed_by='agent-a')))
""")
    assert result["added"] is True
    later = read()
    state.unlink()
    restart()
    code, recovered = call("recover", saved)
    assert code == 200 and recovered["status"] == "replayed", recovered
    assert read() == later


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_registered_runtime_alias_retarget_rejects_without_import(cold_api, provider):
    call, _, state, _, runtime, registry, _, _ = cold_api
    alias = runtime.parent / "registered-runtime-alias"
    alias.symlink_to(runtime, target_is_directory=True)
    registered = json.loads(registry.read_text())
    registered["common_runtime_root"] = str(alias)
    registry.write_text(json.dumps(registered))
    plan = prepare(call, provider)
    other = runtime.parent / "different-runtime"
    other.mkdir()
    alias.unlink()
    alias.symlink_to(other, target_is_directory=True)
    before = state.read_bytes()
    code, refused = call("apply", {**carrier(plan), "writers_stopped": True})
    assert code == 409 and refused["reason_code"] == "shadow_source_runtime_root_mismatch", refused
    assert not list(runtime.rglob("legacy-writer-fence-*.json"))
    assert not list(other.rglob("legacy-writer-fence-*.json"))
    assert state.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_app_copy_preserves_execution_and_provider_fences_without_legacy_fallback(cold_api, provider):
    call, read, state, _, runtime, _, _, _ = cold_api
    plan = prepare(call, provider)
    assert call("apply", {**carrier(plan), "writers_stopped": True})[1]["status"] == "applied"
    original_state, original_head = state.read_bytes(), read()
    code, preview = call("/api/actions/preview", {
        "action_kind": "todo.update", "summary": "Review an administrative edit",
        "normalized_parameters": {"goal_id": "cold", "todo_id": "todo_current",
            "operation": "edit", "text": "Reviewed imported work", "agent_id": "agent-a"},
        "context": {}, "idempotency_key": "administrative-copy"})
    assert code == 201 and preview["proposal"]["status"] == "preview_ready", preview
    assert read() == original_head and state.read_bytes() == original_state
    # Import and copy preview do not authorize completion of the imported work.
    code, refused = call("/api/actions/preview", {
        "action_kind": "todo.update", "summary": "Completion without execution proof",
        "normalized_parameters": {"goal_id": "cold", "todo_id": "todo_current",
            "operation": "complete", "agent_id": "agent-a", "no_followup": True},
        "context": {}, "idempotency_key": "missing-execution"})
    assert code == 400 and refused["error_code"] == "handoff_mode_requires_lease", refused
    assert read() == original_head and state.read_bytes() == original_state
    stores = list(runtime.rglob("authority-store-*.json" if provider == "file" else "authority-*.sqlite"))
    assert len(stores) == 1
    storage = stores[0]
    absent = storage.with_name(storage.name + ".absent")
    storage.rename(absent)
    try:
        code, refused = call("/api/actions/preview", {
            "action_kind": "todo.create", "summary": "A create without its selected provider",
            "normalized_parameters": {"goal_id": "cold", "text": "No Markdown fallback"},
            "context": {}, "idempotency_key": "missing-provider"})
        assert code == 400, refused
        assert refused["error_code"] != "action_preview_failed", refused
        assert not storage.exists()
        assert state.read_bytes() == original_state
    finally:
        absent.rename(storage)
    assert read() == original_head
