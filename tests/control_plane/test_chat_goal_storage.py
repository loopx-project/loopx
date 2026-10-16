"""Real HTTP -> existing migration owner -> durable File/SQLite, no active Goals."""

import http.client
import json
import threading

import pytest

from test_chat_cold_source_import import cold_api, source_modules_present  # noqa: F401 - shared real-server fixtures

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
    provider, handoff_mode = request.param if isinstance(request.param, tuple) else (request.param, "hard_lease")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    rows = []
    for goal in ["example", "other"]:
        state = tmp_path / f"{goal}.md"
        state.write_text(f"---\nhandoff_mode: {handoff_mode}\n---\n\n## Agent Todo\n")
        rows.append(
            {
                "id": goal,
                "repo": str(tmp_path),
                "state_file": state.name,
                "coordination": {"registered_agents": ["agent-a"]},
            }
        )
        projection = build_todo_runtime_shadow_projection(
            goal_id=goal, handoff_mode=handoff_mode, todos=[]
        )
        initialize_canonical_authority(
            runtime, goal, projection, state_path=state, provider=provider
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

    def call(action="", body=None, origin=None, goal="example"):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=90
        )
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        path = "/api/chat/goal-storage" + (
            f"/{action}" if action else f"?goal_id={goal}"
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

    def restart(selected_registry=registry, selected_runtime=runtime):
        nonlocal server, thread
        previous_port = server.server_port
        server.shutdown()
        thread.join(5)
        server.server_close()
        server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
        server.runtime_root = selected_runtime
        server.registry_path = selected_registry
        server.runtime_root_override = str(selected_runtime)
        server.verbose = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        assert server.server_port != previous_port

    call.restart = restart
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


def carrier(plan):
    return {key: plan[key] for key in ("goal_id", "preview_id", "plan_sha256")}


def test_pending_preview_survives_new_server_origin_without_applying(api):
    call, runtime, _ = api
    initial = call()[1]["current"]["provider"]
    plan = preview(call, "sqlite" if initial == "file" else "file")
    original = carrier(plan)
    before = read(runtime)
    call.restart()
    code, current = call()
    assert code == 200 and current["pending_preview"] == original
    assert current["current"]["provider"] == initial and read(runtime) == before
    assert operate(call, original, "recover")[0] == 200
    assert read(runtime) == before, "restoring the editor must never apply its preview"


def test_saved_preview_is_registry_and_goal_scoped(api, tmp_path):
    call, runtime, registry = api
    initial = call()[1]["current"]["provider"]
    plan = preview(call, "sqlite" if initial == "file" else "file")
    original = carrier(plan)
    assert call(goal="other")[1].get("pending_preview") is None
    other_registry = tmp_path / "another-registry.json"
    other_registry.write_bytes(registry.read_bytes())
    call.restart(other_registry)
    assert call()[1].get("pending_preview") is None
    call.restart(registry)
    assert call()[1]["pending_preview"] == original
    assert read(runtime)["todos"] == []


def test_saved_preview_is_runtime_scoped(api, tmp_path):
    call, runtime, _ = api
    initial = call()[1]["current"]["provider"]
    original = carrier(preview(call, "sqlite" if initial == "file" else "file"))
    separate = tmp_path / "other-runtime"
    initialize_canonical_authority(separate, "example",
        build_todo_runtime_shadow_projection(goal_id="example", handoff_mode="legacy", todos=[]),
        state_path=tmp_path / "example.md", provider=initial)
    call.restart(selected_runtime=separate)
    code, observed = call()
    assert code == 200 and observed["pending_preview"] is None
    call.restart()
    assert call()[1]["pending_preview"] == original


def test_pre_upgrade_cold_apply_retains_the_exact_original_intent(cold_api):  # noqa: F811 - imported pytest fixture
    call, read_todos, state, _, runtime, _, _, _ = cold_api
    code, plan = call("preview", {"goal_id": "cold", "provider": "sqlite", "handoff_mode": "hard_lease"})
    assert code == 200 and plan["ok"], plan
    original = {key: plan[key] for key in ("goal_id", "operation_id", "plan_sha256")}
    # A preview prepared by a pre-journal server has only its original handle.
    records = list((runtime / "chat" / "storage-previews").glob("*.json"))
    assert len(records) == 1
    records[0].unlink()
    source = state.read_bytes()
    code, applied = call("apply", {**original, "writers_stopped": True})
    assert code == 200 and applied["status"] == "applied", applied
    assert call()[1]["pending_preview"] == original
    assert len(read_todos()["todos"]) == 2 and state.read_bytes() == source


def test_discard_is_exact_and_old_clients_cannot_resurrect_a_preview(api):
    call, runtime, _ = api
    initial = call()[1]["current"]["provider"]
    target = "sqlite" if initial == "file" else "file"
    old = preview(call, target)
    current = preview(call, target)
    assert old["preview_id"] != current["preview_id"]
    before = read(runtime)
    assert operate(call, old, "discard")[0] == 409
    assert operate(call, old)[0] == 409
    assert call()[1]["pending_preview"] == carrier(current)
    assert operate(call, old, "recover")[0] == 200
    assert call()[1]["pending_preview"] == carrier(current)
    assert operate(call, current, "discard")[0] == 200
    call.restart()
    assert call()[1]["pending_preview"] is None
    assert operate(call, current, "recover")[0] == 200
    assert call()[1]["pending_preview"] is None
    assert operate(call, current)[0] == 409
    assert read(runtime) == before


def test_completed_preview_restart_retains_original_receipt_and_later_writes(api):
    call, runtime, registry = api
    initial = call()[1]["current"]["provider"]
    plan = preview(call, "sqlite" if initial == "file" else "file")
    assert operate(call, plan)[0] == 200
    add_goal_todo(registry_path=registry, goal_id="example", role="agent",
                  text="Acknowledged after cutover", claimed_by="agent-a")
    written = read(runtime)
    call.restart()
    assert call()[1]["pending_preview"] == carrier(plan)
    code, original = operate(call, plan, "recover")
    assert code == 200 and original["recovery"]["phase"] == "completed"
    assert operate(call, plan)[1]["status"] == "already_applied"
    assert read(runtime) == written


def test_original_browser_only_handle_remains_explicitly_recoverable(api):
    call, runtime, _ = api
    initial = call()[1]["current"]["provider"]
    plan = preview(call, "sqlite" if initial == "file" else "file")
    before = read(runtime)
    # Pre-upgrade servers persisted immutable plans, without an editor record.
    journal, = (runtime / "chat" / "storage-previews").glob("*.json")
    journal.unlink()
    call.restart()
    assert call()[1]["pending_preview"] is None
    assert operate(call, plan, "recover")[0] == 200
    assert call()[1]["pending_preview"] == carrier(plan)
    assert read(runtime) == before


def test_corrupt_or_unsafe_editor_record_cannot_create_or_apply_new_intent(api, tmp_path):
    call, runtime, _ = api
    initial = call()[1]["current"]["provider"]
    target = "sqlite" if initial == "file" else "file"
    plan = preview(call, target)
    journal, = (runtime / "chat" / "storage-previews").glob("*.json")
    original = journal.read_bytes()
    before = read(runtime)
    for corrupted in [b"{broken", b"x" * 5000, json.dumps({"carrier": {
            **carrier(plan), "goal_id": "other"}}).encode()]:
        journal.write_bytes(corrupted)
        assert call()[0] == 503
        assert call("preview", {"goal_id": "example", "provider": target})[0] == 503
        assert operate(call, plan)[0] == 503
        assert operate(call, plan, "recover")[0] == 200
        assert journal.read_bytes() == corrupted and read(runtime) == before
    journal.write_bytes(original)
    if hasattr(journal, "symlink_to"):
        outside = tmp_path / "unrelated-record.json"
        outside.write_bytes(original)
        journal.unlink()
        try:
            journal.symlink_to(outside)
        except OSError:  # Windows may require a separate symlink privilege.
            journal.write_bytes(original)
        else:
            assert call()[0] == 503
            assert operate(call, plan, "discard")[0] == 503
            assert outside.read_bytes() == original and read(runtime) == before
            journal.unlink()
            journal.write_bytes(original)
    assert call()[1]["pending_preview"] == carrier(plan)


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
    from datetime import datetime, timezone
    assert datetime.fromisoformat(acquired["lease"]["expires_at"]) <= datetime.now(timezone.utc)
    assert leased["leases"][0]["status"] == "active", "Expiry does not settle retained execution"
    assert call()[1]["current"]["unsettled_lease_count"] == 1
    assert call("preview", {"goal_id": "example", "provider": target})[0] == 409
    assert read(runtime) == leased and call()[1]["current"]["provider"] == initial


@pytest.mark.parametrize("api", [
    pytest.param(("file", "legacy"), id="file-legacy"),
    pytest.param(("sqlite", "legacy"), id="sqlite-legacy"),
], indirect=True)
def test_retired_policy_remains_inspectable_but_cannot_grant_host_execution(api):
    from loopx.control_plane.work_items.task_lease import acquire_task_lease, TaskLeaseError

    call, runtime, registry = api
    initial = call()[1]["current"]["provider"]
    task = add_goal_todo(registry_path=registry, goal_id="example", role="agent",
                        text="Retained legacy ownership", claimed_by="agent-a")
    before = read(runtime)
    display = (runtime.parent / "example.md").read_bytes()
    with pytest.raises(TaskLeaseError) as rejected:
        acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id="example",
            todo_id=task["todo_id"], owner="agent-a", idempotency_key="retired-host", ttl_seconds=60)
    assert rejected.value.code == "handoff_mode_retired"
    assert read(runtime) == before and before["leases"] == []
    assert (runtime.parent / "example.md").read_bytes() == display
    assert call()[1]["current"]["provider"] == initial


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


def test_a_preview_cannot_bypass_a_later_host_lease_and_requires_a_fresh_plan(api):
    from loopx.control_plane.work_items.task_lease import (
        acquire_task_lease,
        release_task_lease,
    )

    call, runtime, registry = api
    initial = call()[1]["current"]["provider"]
    target = "sqlite" if initial == "file" else "file"
    task = add_goal_todo(registry_path=registry, goal_id="example", role="agent",
                        text="Work admitted after preview", claimed_by="agent-a")
    plan = preview(call, target)
    acquired = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id="example",
        todo_id=task["todo_id"], owner="agent-a", idempotency_key="after-preview", ttl_seconds=3600)
    assert acquired["ok"], acquired
    leased = read(runtime)
    restart_effect_runtime()
    # Preview is evidence of its original source, not a reservation or a grant
    # to copy a Host's newer active work. Recovery reads must not execute it.
    assert operate(call, plan)[0] == 409
    code, observed = operate(call, plan, "recover")
    assert code == 200 and observed["recovery"] is None
    assert observed["current"]["provider"] == initial
    assert observed["current"]["unsettled_lease_count"] == 1
    assert read(runtime) == leased
    assert not (runtime / "authority-transition/local-provider" / plan["plan_sha256"]).exists()

    released = release_task_lease(registry_path=registry, runtime_root=runtime, goal_id="example",
        todo_id=task["todo_id"], owner="agent-a", idempotency_key="after-preview",
        expected_version=acquired["lease"]["version"])
    assert released["ok"] and released["lease"]["status"] == "released", released
    settled = read(runtime)
    # Settlement changed the source revision. It does not make the old preview
    # current or erase that Host's lease lineage.
    assert operate(call, plan)[0] == 409 and read(runtime) == settled
    fresh = preview(call, target)
    assert operate(call, fresh)[0] == 200
    copied = read(runtime)
    assert copied["todos"] == settled["todos"]
    assert copied["leases"] == settled["leases"]
    assert operate(call, fresh)[1]["status"] == "already_applied"
    assert read(runtime) == copied


@pytest.fixture
def old_api(tmp_path, monkeypatch):
    from tests.control_plane.shadow_e2e_fixture import workspace

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    ws = workspace(tmp_path, bootstrap=False)
    registry = json.loads(ws.registry.read_text())
    registry["goals"][0]["coordination"].pop("runtime_shadow")
    ws.registry.write_text(json.dumps(registry))
    ws.state.write_text(
        "---\ngoal_id: goal-e2e\nhandoff_mode: soft_claim\n---\n"
        "## Agent Todo\n- [ ] Keep active\n"
        "  <!-- loopx:todo todo_id=todo_active role=agent status=open "
        "claimed_by=agent-a -->\n"
        "## Todo Archive\n- [x] Keep archived evidence\n"
        "  <!-- loopx:todo todo_id=todo_archived role=agent status=done -->\n"
    )
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

    yield call, ws, False
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
    call, ws, captured = old_api
    source = ws.state.read_bytes(), ws.registry.read_bytes()
    leases = {
        path: path.read_bytes()
        for path in (ws.runtime / "goals" / ws.goal / "task-leases").glob("*.json")
    }
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
    assert source == (ws.state.read_bytes(), ws.registry.read_bytes())
    assert leases == {path: path.read_bytes() for path in leases}
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
