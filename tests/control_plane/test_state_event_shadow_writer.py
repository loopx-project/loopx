"""Managed event transactions through real source files and the native provider."""
from __future__ import annotations

from pathlib import Path

import pytest

from shadow_e2e_fixture import workspace
from loopx.event_sourced_state import AppendOnlyStateEventStore, TODO_ADDED, make_state_event
from loopx.control_plane.goals.state_event_writer import StateEventWriteContext
from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox


def event(goal: str, number: int, *, role: str = "agent") -> dict:
    return make_state_event(event_id=f"event-{number}", goal_id=goal, event_type=TODO_ADDED,
        recorded_at="2026-09-01T00:00:00Z", refs={"todo_id": f"todo_event_{number}"}, payload={"role": role,
        "title": f"Source-owned work {number}", "task_class": "advancement_task"})


def managed(w) -> AppendOnlyStateEventStore:
    return AppendOnlyStateEventStore(w.state.with_name("events.jsonl"),
        write_context=StateEventWriteContext(w.registry, w.runtime, w.goal, w.state))


def test_event_batch_is_captured_once_and_replay_does_not_advance_candidate(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    store = managed(w)
    batch = [event(w.goal, 1), event(w.goal, 2, role="user")]
    store.append_many(batch)
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    [entry] = outbox.list_entries(directory)
    assert entry.committed_path is not None
    assert entry.prepared["source"]["kind"] == "state_event_log"
    assert entry.prepared["source"]["previous_bytes_digest"] is None
    assert len(entry.prepared["projection"]["todos"]) == 2
    assert w.drain()["ok"] is True
    assert w.cli("coordination-shadow", "inspect")["inspection"]["parity_matches"] is True
    store.append_many(batch)
    assert outbox.list_entries(directory) == []
    assert outbox.read_cursor(directory)["last_seq"] == 1


def test_existing_mixed_sources_bootstrap_uses_the_canonical_overlay(tmp_path: Path) -> None:
    w = workspace(tmp_path, bootstrap=False)
    w.add("Markdown-owned work")
    AppendOnlyStateEventStore(w.state.with_name("events.jsonl")).append(event(w.goal, 1))
    result = w.cli("coordination-shadow", "bootstrap", "--execute")
    assert result["bootstrap"]["status"] == "applied"
    managed(w).append(event(w.goal, 2))
    assert w.drain()["ok"] is True
    result = w.cli("coordination-shadow", "inspect")
    assert result["inspection"]["parity_matches"] is True


@pytest.mark.parametrize("window", ["before_publish", "after_publish"])
def test_real_process_death_preserves_event_source_proof_until_drain(tmp_path: Path, window: str) -> None:
    import select
    import subprocess
    import sys
    from shadow_e2e_fixture import REPO
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError
    w = workspace(tmp_path)
    worker = r'''
import json, pathlib, sys, time
from loopx.event_sourced_state import AppendOnlyStateEventStore, TODO_ADDED, make_state_event
from loopx.control_plane.goals.state_event_writer import StateEventWriteContext
from loopx.control_plane.todos import active_state_editing
from loopx.control_plane.coordination.local_authority_shadow_outbox import TodoPartitionCapture
registry, runtime, state = map(pathlib.Path, sys.argv[1:4])
window = sys.argv[4]
def barrier(*args, **kwargs):
    print("BARRIER", flush=True)
    time.sleep(120)
if window == "before_publish":
    active_state_editing.atomic_write_state_text = barrier
else:
    TodoPartitionCapture.committed = barrier
store = AppendOnlyStateEventStore(state.with_name("events.jsonl"),
    write_context=StateEventWriteContext(registry, runtime, "goal-e2e", state))
store.append(make_state_event(event_id="crash-event", goal_id="goal-e2e", event_type=TODO_ADDED,
    refs={"todo_id":"todo_crash"}, payload={"role":"agent", "title":"Recover the original transaction", "task_class":"advancement_task"}))
'''
    child = subprocess.Popen([sys.executable, "-c", worker, str(w.registry), str(w.runtime), str(w.state), window],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([child.stdout], [], [], 30)
        assert ready
        assert child.stdout.readline().strip() == "BARRIER"
        child.kill()
        child.communicate(timeout=10)
        assert child.returncode == -9
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    store = managed(w)
    before = store.path.read_bytes() if store.path.exists() else None
    with pytest.raises(ShadowManagementError, match="drain the unresolved"):
        store.append(event(w.goal, 2))
    from loopx.event_sourced_state import REFRESH_RECORDED
    with pytest.raises(ShadowManagementError, match="drain the unresolved"):
        store.append(make_state_event(event_id="no-todo-change", goal_id=w.goal, event_type=REFRESH_RECORDED))
    assert (store.path.read_bytes() if store.path.exists() else None) == before
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    [pending] = outbox.list_entries(directory)
    assert pending.committed_path is None
    result = w.drain()
    assert result["ok"] is True, result
    assert result["entries"][0]["resolution"] == (
        "abandoned" if window == "before_publish" else "committed_proven_by_readback")
    store.append(event(w.goal, 2))
    assert w.drain()["ok"] is True
    assert w.cli("coordination-shadow", "inspect")["inspection"]["parity_matches"] is True


def test_public_complete_captures_completion_and_successors_in_one_batch(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    store = managed(w)
    store.append(event(w.goal, 1))
    assert w.drain()["ok"] is True
    w.cli("task-lease", "acquire", "--todo-id", "todo_event_1", "--owner", "agent-a",
        "--idempotency-key", "event-completion", "--ttl-seconds", "3600")
    result = w.cli("todo", "complete", "--todo-id", "todo_event_1", "--agent-id", "agent-a", "--evidence", "Source transaction verified",
        "--task-lease-idempotency-key", "event-completion", "--task-lease-expected-version", "1",
        "--next-agent-todo", "Verify the durable successor", "--next-task-class", "advancement_task")
    assert result["completed"] is True
    assert result["coordination_runtime_shadow"]["entry"]["entry_id"]
    entries = outbox.list_entries(outbox.partition_directory(w.runtime, w.goal, "todos"))
    assert len(entries) == 1
    assert w.drain()["ok"] is True
    assert w.cli("coordination-shadow", "inspect")["inspection"]["parity_matches"] is True


def test_late_batch_conflict_and_stale_checksum_publish_nothing(tmp_path: Path) -> None:
    from loopx.event_sourced_state import StateEventConflictError, StateEventSourceChangedError
    w = workspace(tmp_path)
    store = managed(w)
    store.append(event(w.goal, 1))
    assert w.drain()["ok"] is True
    before = store.path.read_bytes()
    conflict = event(w.goal, 1)
    conflict["payload"]["title"] = "Conflicting identity"
    with pytest.raises(StateEventConflictError):
        store.append_many([event(w.goal, 2), conflict])
    with pytest.raises(StateEventSourceChangedError):
        store.append_many([event(w.goal, 2)], expected_checksum="stale-source")
    assert store.path.read_bytes() == before
    assert outbox.list_entries(outbox.partition_directory(w.runtime, w.goal, "todos")) == []


def test_raw_event_edit_still_invalidates_candidate_parity(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    AppendOnlyStateEventStore(w.state.with_name("events.jsonl")).append(event(w.goal, 1))
    result = w.cli("coordination-shadow", "inspect", success=False)
    assert result["inspection"]["parity_matches"] is False
    assert result["inspection"]["status"] == "drifted"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_mixed_event_cutover_routes_native_writes_and_fences_old_writer(tmp_path: Path, provider: str) -> None:
    import json
    from test_reviewed_promotion_cli import prepare
    from loopx.control_plane.coordination.legacy_writer_fence import LegacyCoordinationWriterFenced
    w, saved, _ = prepare(tmp_path, provider)
    store = managed(w)
    for index in range(3):
        store.append(event(w.goal, index))
        assert w.drain()["ok"] is True
    preview = w.cli("coordination-shadow", "promote")
    assert preview["promotion"]["status"] == "preview_ready"
    saved.write_text(json.dumps(preview))
    applied = w.cli("coordination-shadow", "promote", "--reviewed-plan", str(saved), "--execute")
    assert applied["promotion"]["canonical_authority"] == f"{provider}_v0"
    before = store.path.read_bytes()
    with pytest.raises(LegacyCoordinationWriterFenced):
        store.append(event(w.goal, 4))
    assert store.path.read_bytes() == before
    created = w.add("Native work after event migration")
    readback = w.cli("todo", "list", "--todo-id", created["todo_id"])
    assert readback["authority_read"]["source_authority"] == f"{provider}_v0"
    migrated = w.cli("todo", "list", "--todo-id", "todo_event_1")
    assert migrated["authority_read"]["source_authority"] == f"{provider}_v0"
    archive = tmp_path / "recovery.ndjson"
    exported = w.cli("authority-archive", "export", "--archive", str(archive))
    assert exported["status"] == "exported"
    restored = w.cli("authority-archive", "restore", "--archive", str(archive),
        "--archive-sha256", exported["archive"]["archive_sha256"], "--provider", provider,
        "--destination", str(tmp_path / "isolated-recovery"), "--execute")
    assert restored["status"] == "restored" and restored["destination_is_active"] is False


def test_prepare_io_failure_preserves_event_source(tmp_path: Path) -> None:
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError
    w = workspace(tmp_path)
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    directory.write_text("Not a writable outbox directory")
    store = managed(w)
    with pytest.raises(ShadowManagementError):
        store.append(event(w.goal, 1))
    assert not store.path.exists()
    assert directory.read_text() == "Not a writable outbox directory"


def test_source_alias_change_requires_fresh_binding(tmp_path: Path) -> None:
    import json
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError
    w = workspace(tmp_path)
    value = json.loads(w.registry.read_text())
    value["goals"][0]["state_event_log"] = "other-events.jsonl"
    w.registry.write_text(json.dumps(value))
    with pytest.raises(ShadowManagementError, match="event_source_rebootstrap_required"):
        managed(w).append(event(w.goal, 1))
    assert not w.state.with_name("events.jsonl").exists()


def test_wrong_goal_and_unbound_prepared_source_never_mutate_candidate(tmp_path: Path) -> None:
    import json
    from loopx.event_sourced_state import StateEventError
    w = workspace(tmp_path)
    store = managed(w)
    with pytest.raises(StateEventError, match="goal differs"):
        store.append(event("another-goal", 1))
    store.append(event(w.goal, 1))
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    [pending] = outbox.list_entries(directory)
    changed = json.loads(pending.prepared_path.read_text())
    changed["source"]["event_log_path"] = str(tmp_path / "not-a-source.jsonl")
    pending.prepared_path.write_text(json.dumps(changed))
    before = pending.prepared_path.read_bytes()
    result = w.drain()
    assert result["ok"] is False and result["reason_code"] == "event_source_binding_invalid", result
    assert pending.prepared_path.read_bytes() == before


def test_concurrent_managed_processes_preserve_source_and_partition_continuity(tmp_path: Path) -> None:
    import subprocess
    import sys
    from shadow_e2e_fixture import REPO
    w = workspace(tmp_path)
    worker = r'''
import pathlib, sys
from loopx.control_plane.goals.state_event_writer import StateEventWriteContext
from loopx.event_sourced_state import AppendOnlyStateEventStore, TODO_ADDED, make_state_event
reg, root, state = map(pathlib.Path, sys.argv[1:4])
identity=sys.argv[4]
store=AppendOnlyStateEventStore(state.with_name("events.jsonl"),
    write_context=StateEventWriteContext(reg, root, "goal-e2e", state))
store.append(make_state_event(event_id=identity, goal_id="goal-e2e", event_type=TODO_ADDED,
    refs={"todo_id":"todo_"+identity}, payload={"role":"agent", "title":identity, "task_class":"advancement_task"}))
'''
    children = [subprocess.Popen([sys.executable, "-c", worker, str(w.registry), str(w.runtime), str(w.state), f"process_{i}"],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(2)]
    try:
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, (stdout, stderr)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
    assert [e["append_sequence"] for e in managed(w).load()] == [1, 2]
    entries = outbox.list_entries(outbox.partition_directory(w.runtime, w.goal, "todos"))
    assert [e.seq for e in entries] == [1, 2]
    assert w.drain()["ok"] is True
    assert w.cli("coordination-shadow", "inspect")["inspection"]["parity_matches"] is True


def test_managed_symlink_writes_the_bound_file_not_the_alias(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    target = w.state.with_name("events.jsonl")
    alias = tmp_path / "event-alias.jsonl"
    alias.symlink_to(target)
    writer = StateEventWriteContext(w.registry, w.runtime, w.goal, w.state)
    AppendOnlyStateEventStore(alias, write_context=writer).append(event(w.goal, 1))
    assert alias.is_symlink() and target.exists()
    assert w.drain()["ok"] is True
    assert w.cli("coordination-shadow", "inspect")["inspection"]["parity_matches"] is True
