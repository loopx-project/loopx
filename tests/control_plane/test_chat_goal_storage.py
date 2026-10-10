"""Real HTTP -> existing migration owner -> durable File/SQLite, no active Goals."""

import http.client
import json
import threading

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.todos import add_goal_todo
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)


@pytest.fixture(params=["file", "sqlite"])
def api(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    rows = []
    for goal in ["example", "other"]:
        state = tmp_path / f"{goal}.md"
        state.write_text("---\nhandoff_mode: legacy\n---\n\n## Agent Todo\n")
        rows.append(
            {
                "id": goal,
                "repo": str(tmp_path),
                "state_file": state.name,
                "coordination": {"registered_agents": ["agent-a"]},
            }
        )
        projection = build_todo_runtime_shadow_projection(
            goal_id=goal, handoff_mode="legacy", todos=[]
        )
        initialize_canonical_authority(
            runtime, goal, projection, state_path=state, provider=request.param
        )
    registry.write_text(
        json.dumps({"common_runtime_root": str(runtime), "goals": rows})
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_root = runtime
    server.registry_path = registry
    server.runtime_root_override = str(runtime)
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(action="", body=None, origin=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=90
        )
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        path = "/api/chat/goal-storage" + (
            f"/{action}" if action else "?goal_id=example"
        )
        connection.request(
            "POST" if body is not None else "GET",
            path,
            body=json.dumps(body) if body is not None else None,
            headers=headers,
        )
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        # No local source or backup filenames, exception text or complete state.
        assert str(tmp_path) not in json.dumps(value)
        return response.status, value

    yield call, runtime, registry
    server.shutdown()
    thread.join(5)
    server.server_close()
    restart_effect_runtime()


def preview(call, provider):
    code, result = call("preview", {"goal_id": "example", "provider": provider})
    assert code == 200 and result["ok"], result
    return result


def operate(call, plan, action="apply", **overrides):
    return call(action, {"goal_id": "example", "preview_id": plan["preview_id"],
        "plan_sha256": plan["plan_sha256"], **overrides})


def read(runtime, goal="example"):
    return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal, include_leases=True)


def test_storage_switch_retains_new_metadata_and_history_after_restart(api):
    call, runtime, registry = api
    initial = call()[1]["current"]["provider"]
    target = "sqlite" if initial == "file" else "file"
    task = add_goal_todo(registry_path=registry, goal_id="example", role="agent", text="Before cutover",
        priority="P1", claimed_by="agent-a", note="Nested evidence retained", task_class="advancement_task")
    before = read(runtime)
    plan = preview(call, target)
    assert plan["reviewed_source"]["provider"] == initial
    assert call()[1]["current"]["provider"] == initial and read(runtime) == before
    code, applied = operate(call, plan)
    assert code == 200 and applied["reviewed_source"]["provider"] == initial
    assert applied["target_provider"] == target
    assert call()[1]["current"]["provider"] == target
    assert read(runtime)["todos"] == before["todos"]
    later = add_goal_todo(registry_path=registry, goal_id="example", role="agent", text="New data after migration",
        priority="P2", claimed_by="agent-a", note="Retain on return", task_class="advancement_task")
    written = read(runtime)
    restart_effect_runtime()
    code, recovered = operate(call, plan, "recover")
    assert code == 200 and recovered["recovery"]["phase"] == "completed", recovered
    assert recovered["current"]["provider"] == target and recovered["current"]["todo_count"] == 2
    code, replay = operate(call, plan)
    assert code == 200 and replay["status"] == "already_applied", replay
    assert read(runtime) == written
    back = preview(call, initial)
    assert operate(call, back)[0] == 200
    assert read(runtime)["todos"] == written["todos"]
    assert {row["todo_id"] for row in written["todos"]} == {task["todo_id"], later["todo_id"]}
    final = read(runtime)
    # Original completion remains historical; it cannot silently re-select the target.
    code, old = operate(call, plan, "recover")
    assert code == 200 and old["recovery"]["phase"] == "completed"
    assert old["target_provider"] == target and old["current"]["provider"] == initial
    assert operate(call, plan)[0] == 409 and read(runtime) == final


def test_source_conflicts_foreign_handles_and_backup_corruption_cannot_switch(api):
    call, runtime, registry = api
    initial = call()[1]["current"]["provider"]
    target = "sqlite" if initial == "file" else "file"
    plan = preview(call, target)
    before, other = read(runtime), read(runtime, "other")
    assert operate(call, plan, goal_id="other")[0] == 409
    assert operate(call, plan, plan_sha256="0" * 64)[0] == 409
    assert operate(call, plan, "recover", goal_id="other")[0] == 409
    assert read(runtime) == before and read(runtime, "other") == other
    add_goal_todo(registry_path=registry, goal_id="example", role="agent", text="Source changed", claimed_by="agent-a")
    changed = read(runtime)
    assert operate(call, plan)[0] == 409 and read(runtime) == changed
    fresh = preview(call, target)
    assert operate(call, fresh)[0] == 200
    applied = read(runtime)
    archive = runtime / "authority-transition/local-provider" / fresh["plan_sha256"] / "source.archive.jsonl"
    archive.write_text("damaged backup")
    assert operate(call, fresh, "recover")[0] == 409
    assert operate(call, fresh)[0] == 409 and read(runtime) == applied


def test_no_automatic_migration_or_caller_paths_and_active_lease_rejected(api):
    call, runtime, registry = api
    initial = call()[1]["current"]["provider"]
    target = "sqlite" if initial == "file" else "file"
    before = read(runtime)
    assert call("preview", {"goal_id": "example", "provider": target, "plan": "/tmp/injected"})[0] == 400
    assert call("apply", {"goal_id": "example", "preview_id": "../injected", "plan_sha256": "a" * 64})[0] == 400
    assert call("preview", {"goal_id": "example", "provider": target}, "https://untrusted.example")[0] == 403
    assert call("preview", {"goal_id": "example", "provider": "postgresql"})[0] == 409
    assert read(runtime) == before and call()[1]["current"]["provider"] == initial
    from loopx.control_plane.work_items.task_lease import acquire_task_lease
    task = add_goal_todo(registry_path=registry, goal_id="example", role="agent", text="Host still running", claimed_by="agent-a")
    acquired = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id="example",
        todo_id=task["todo_id"], owner="agent-a", idempotency_key="host", ttl_seconds=1)
    assert acquired["ok"], acquired
    import time
    time.sleep(1.1)
    leased = read(runtime)
    assert call()[1]["current"]["unsettled_lease_count"] == 1
    assert call("preview", {"goal_id": "example", "provider": target})[0] == 409
    assert read(runtime) == leased and call()[1]["current"]["provider"] == initial


def test_original_receipt_is_readable_when_live_provider_is_unavailable(api):
    call, runtime, _ = api
    initial = call()[1]["current"]["provider"]
    target = "sqlite" if initial == "file" else "file"
    plan = preview(call, target)
    assert operate(call, plan)[0] == 200
    directory = runtime / "authority" / f"{target}-v0"
    unavailable = directory.with_name(directory.name + ".unavailable")
    directory.rename(unavailable)
    try:
        assert call()[0] == 409
        code, original = operate(call, plan, "recover")
        assert code == 200 and original["recovery"]["phase"] == "completed", original
        assert original["current"] is None and original["execution_authority_granted"] is False
        assert operate(call, plan)[0] == 409
    finally:
        unavailable.rename(directory)


@pytest.fixture(params=[False, True], ids=["cold", "retained-capture"])
def old_api(tmp_path, monkeypatch, request):
    from tests.control_plane.test_cold_source_inspection import cold_workspace
    from tests.control_plane.shadow_e2e_fixture import workspace

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    ws = workspace(tmp_path) if request.param else cold_workspace(tmp_path)
    if request.param:
        ws.add("Private original history must stay on disk")
        outbox = ws.runtime / "authority-shadow" / "outbox" / ws.goal / "todos"
        (outbox / "original-unrecognized.json").write_bytes(b"{malformed original outbox")
    directory = ws.runtime / "goals" / ws.goal / "task-leases"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "removed.json").write_text(json.dumps({
        "schema_version": "task_lease_v0", "goal_id": ws.goal, "todo_id": "removed",
        "owner": "agent-a", "status": "active", "idempotency_key": "old-work",
        "version": 4, "lease_epoch": 2, "expires_at": "2000-01-01T00:00:00Z", "write_scopes": [],
    }))
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_root, server.registry_path = ws.runtime, ws.registry
    server.runtime_root_override, server.verbose = str(ws.runtime), False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call():
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=90)
        connection.request("GET", f"/api/chat/goal-storage?goal_id={ws.goal}")
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        encoded = json.dumps(result)
        assert str(tmp_path) not in encoded and "Private original history" not in encoded
        assert "old-work" not in encoded and "malformed original outbox" not in encoded
        return response.status, result

    yield call, ws, request.param
    server.shutdown()
    thread.join(5)
    server.server_close()
    restart_effect_runtime()


def test_registered_old_source_read_failure_is_unavailable_and_recovers(old_api):
    call, ws, _ = old_api
    source = ws.state.read_bytes()
    ws.state.write_bytes(b"\xff")
    code, failed = call()
    assert code == 503 and failed["error_code"] == "goal_storage_unavailable"
    assert "cold_source" not in failed and "current" not in failed
    ws.state.write_bytes(source)
    code, result = call()
    assert code == 200 and result["cold_source"]["import_ready"] is False


def test_old_source_http_inventory_keeps_history_and_does_not_grant_import(old_api):
    from tests.control_plane.test_cold_source_inspection import capture_bytes

    call, ws, captured = old_api
    before = capture_bytes(ws)
    source = ws.state.read_bytes(), ws.registry.read_bytes()
    for _ in range(2):
        code, result = call()
        assert code == 200 and result["ok"], result
        assert result["current"]["canonical"] is False and result["current"]["provider"] is None
        old = result["cold_source"]
        assert old["active_todo_count"] == 1
        assert old["archived_todo_count"] == (0 if captured else 1)
        # Even an orphan, expired active lease needs settlement; not Host stop.
        assert old["unsettled_lease_count"] == 1
        assert old["capture_artifacts_present"] is captured
        assert old["outbox_files_present"] is captured
        assert old["writer_stop_verified"] is old["outbox_reconciliation_verified"] is old["import_ready"] is False
        assert result["authority_changed"] is result["execution_authority_granted"] is False
        assert "outbox_review" not in json.dumps(result)
    assert before == capture_bytes(ws)
    assert source == (ws.state.read_bytes(), ws.registry.read_bytes())
    assert not (ws.runtime / "authority").exists()


def test_old_source_http_refuses_canonical_presence_and_recovers_fresh_observation(old_api):
    import hashlib

    call, ws, _ = old_api
    marker = ws.runtime / "authority" / ("provider-" + hashlib.sha256(ws.goal.encode()).hexdigest() + ".json")
    marker.parent.mkdir(parents=True)
    marker.write_text('{"provider":"sqlite"}')
    code, result = call()
    assert code == 409 and result["ok"] is False and result["current"] is None, result
    assert result["reason_code"] == "cold_source_canonical_authority_present"
    assert "cold_source" not in result
    assert marker.read_text() == '{"provider":"sqlite"}'
    marker.unlink()
    assert call()[0] == 200
