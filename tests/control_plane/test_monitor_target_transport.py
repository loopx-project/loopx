"""Monitor target lookup retains full records without transporting their prose."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_monitor_followthrough_contract import AGENT_ID, GOAL_ID, _write_fixture
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable, read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, MAX_REQUEST_BYTES
from loopx.control_plane.quota.monitor_poll import resolve_due_monitor_candidate
from loopx.control_plane.scheduler import monitor_poll_writeback, monitor_todo
from loopx.control_plane.testing.canary_harness import run_json_cli


def _monitor(**fields):
    return {
        "schema_version": "todo_item_v0", "source_section": "Agent Todo", "index": 321,
        "todo_id": "todo_transport_watch", "role": "agent", "status": "open",
        "task_class": "continuous_monitor", "archive_state": "active", "done": False,
        "text": "Observe an external revision", "claimed_by": AGENT_ID,
        "target_key": "transport-watch", "cadence": "1h",
        "next_due_at": "2000-01-01T00:00:00Z", **fields,
    }


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("target_repetitions", [1, 40_000, 120_000])
def test_real_large_authority_returns_complete_target(tmp_path, monkeypatch, provider, target_repetitions):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path, canonical=False)
    note = "Measured evidence 演示 " * target_repetitions
    if target_repetitions == 120_000:
        assert len(note.encode()) > MAX_REQUEST_BYTES
    target = _monitor(note=note, evidence="source:retained-result",
                      required_capabilities=["network"], resume_when="todo_done:todo_input",
                      resume_ready=True, expires_at="2099-01-01T00:00:00Z")
    items = [
        {"schema_version": "todo_item_v0", "source_section": "Agent Todo", "index": i + 1,
         "todo_id": f"todo_history_{i}", "role": "agent", "status": "done",
         "done": True, "task_class": "advancement_task", "archive_state": "active",
         "text": "Retained task", "note": "evidence 字 " * 650}
        for i in range(320)
    ] + [target]
    items[0]["todo_id"] = "todo_input"
    assert len(json.dumps(items, ensure_ascii=False).encode()) > MAX_REQUEST_BYTES
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=items,
                                                      handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    state.unlink()  # No legacy Markdown fallback can satisfy the lookup.

    # The canonical source has its own per-record page ceiling. Keep that
    # refusal; this transport repair must not loosen it or call it "not due".
    if target_repetitions == 120_000:
        with pytest.raises(LocalCoordinationAuthorityUnavailable) as error:
            resolve_due_monitor_candidate(registry_path=registry, runtime_root=runtime,
                goal_id=GOAL_ID, todo_id=target["todo_id"], target_key=None)
        assert error.value.code == "canonical_snapshot_record_too_large"
        assert not state.exists()
        return

    selected = monitor_poll_writeback.resolve_monitor_todo_item(
        registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
        todo_id=target["todo_id"], target_key=target["target_key"],
    )
    for field in ("todo_id", "note", "evidence", "claimed_by", "required_capabilities",
                  "resume_when", "resume_ready", "next_due_at", "expires_at"):
        assert selected[field] == target[field]
    assert resolve_due_monitor_candidate(registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, todo_id=target["todo_id"], target_key=None) == {
            "due": True, "todo_id": target["todo_id"], "target_key": target["target_key"],
            "task_class": "continuous_monitor", "claimed_by": AGENT_ID,
        }
    assert not state.exists()
    if target_repetitions == 1:
        scope = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                 "--runtime-profile", "codex_app_ssh_goal",
                 "--turn-instance-id", "monitor-large-history"]
        guard = ["quota", "should-run", *scope,
                 "--available-capability", "network"]
        admitted = run_json_cli(*guard, registry_path=registry, runtime_root=runtime)
        assert admitted["selected_todo"]["todo_id"] == target["todo_id"]
        poll = ["quota", "monitor-poll", *scope, "--todo-id", target["todo_id"],
                "--target-key", target["target_key"], "--result-hash", "observed-v2",
                "--material-change", "--next-due-at", "2099-01-01T00:00:00Z", "--execute"]
        first = run_json_cli(*poll, registry_path=registry, runtime_root=runtime)
        assert first["turn_continuation"]["current_turn_settled"] is True
        replay = run_json_cli(*poll, registry_path=registry, runtime_root=runtime)
        assert replay["replayed"] is True and replay["appended"] is False
        closed = run_json_cli(*guard, registry_path=registry, runtime_root=runtime)
        assert closed["effective_action"] == "heartbeat_settled_skip"
        canonical = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        assert canonical["source_authority"] == ("sqlite_v0" if provider == "sqlite" else "file_v0")
        assert len(canonical["todos"]) == 321
        persisted = next(row for row in canonical["todos"] if row["todo_id"] == target["todo_id"])
        assert persisted["material_change_generation"] == 1
        assert persisted["note"] == note and persisted["result_hash"] == "observed-v2"
        events = [json.loads(line) for line in
                  (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text().splitlines()]
        assert sum(row["classification"] == "quota_monitor_poll" for row in events) == 1
        assert not any(row["classification"] == "quota_slot_spent" for row in events)


@pytest.mark.parametrize(("overrides", "message"), [
    ({"status": "done", "done": True}, "unfinished"),
    ({"role": "user"}, "not found"),
    ({"archive_state": "archive"}, "not found"),
    ({"task_class": "advancement_task"}, "continuous_monitor"),
])
def test_target_rejections_retain_native_rules(tmp_path, monkeypatch, overrides, message):
    source = [_monitor(**overrides)]
    monkeypatch.setattr(monitor_poll_writeback, "read_canonical_todos_if_promoted",
                        lambda **_: {"todos": source})
    with pytest.raises(ValueError, match=message):
        monitor_poll_writeback.resolve_monitor_todo_item(registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path, goal_id=GOAL_ID, todo_id="todo_transport_watch")


def test_duplicate_key_exact_identity_and_full_record_isolation(tmp_path, monkeypatch):
    source = [_monitor(), _monitor(todo_id="todo_other", note="second source",
                                   required_capabilities=["network"])]
    original = deepcopy(source)
    monkeypatch.setattr(monitor_poll_writeback, "read_canonical_todos_if_promoted",
                        lambda **_: {"todos": source})
    args = dict(registry_path=tmp_path / "registry.json", runtime_root=tmp_path, goal_id=GOAL_ID)
    with pytest.raises(ValueError, match="multiple"):
        monitor_poll_writeback.resolve_monitor_todo_item(**args, target_key="transport-watch")
    with pytest.raises(ValueError, match="resolves to"):
        monitor_poll_writeback.resolve_monitor_todo_item(**args, todo_id="todo_other", target_key="wrong")
    selected = monitor_poll_writeback.resolve_monitor_todo_item(**args, todo_id="todo_other",
                                                               target_key="transport-watch")
    assert selected == source[1]
    selected["required_capabilities"].append("synthetic-change")
    assert source == original


@pytest.mark.parametrize("overrides", [
    {"next_due_at": "2099-01-01T00:00:00Z"},
    {"expires_at": "2000-01-01T00:00:00Z"},
    {"resume_when": "todo_done:todo_input", "resume_ready": False},
    {"status": "blocked"}, {"done": True},
])
def test_original_schedule_and_wait_fields_still_control_due(tmp_path, monkeypatch, overrides):
    source = [_monitor(**overrides)]
    monkeypatch.setattr(monitor_poll_writeback, "read_canonical_todos_if_promoted",
                        lambda **_: {"todos": source})
    monkeypatch.setattr(monitor_todo, "now_utc", lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert resolve_due_monitor_candidate(registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path, goal_id=GOAL_ID, todo_id="todo_transport_watch", target_key=None) == {}


@pytest.mark.parametrize("code", ["request_too_large", "authentication_failed"])
def test_transport_rejection_is_not_a_not_due_result(tmp_path, monkeypatch, code):
    monkeypatch.setattr(monitor_poll_writeback, "read_canonical_todos_if_promoted",
                        lambda **_: {"todos": [_monitor()]})

    def unavailable(*_args, **_kwargs):
        raise EffectRuntimeRejected("Synthetic transport rejection", diagnostic_code=code)

    monkeypatch.setattr(monitor_poll_writeback, "effect_runtime_result", unavailable)
    with pytest.raises(EffectRuntimeRejected) as failure:
        resolve_due_monitor_candidate(registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path, goal_id=GOAL_ID, todo_id="todo_transport_watch", target_key=None)
    assert failure.value.diagnostic_code == code


def test_foreign_result_cannot_replace_the_source_record(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_poll_writeback, "read_canonical_todos_if_promoted",
                        lambda **_: {"todos": [_monitor()]})
    monkeypatch.setattr(monitor_poll_writeback, "effect_runtime_result", lambda *_: {
        "schema_version": "loopx_monitor_target_result_v0", "todo": _monitor(todo_id="todo_foreign"),
    })
    with pytest.raises(TypeError, match="source"):
        monitor_poll_writeback.resolve_monitor_todo_item(registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path, goal_id=GOAL_ID, todo_id="todo_transport_watch")
