"""Actual CLI -> quota -> canonical transaction -> projection -> quota receipt."""
from __future__ import annotations

import pytest
import hashlib
import json

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_monitor_followthrough_contract import _write_fixture, GOAL_ID, AGENT_ID
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.scheduler.monitor_poll_writeback import write_monitor_poll_todo_state
from loopx.control_plane.testing.canary_harness import run_json_cli


def _canonical(
    tmp_path,
    native=False,
    provider="file",
    lease=None,
    monitor_status="open",
):
    registry, runtime, state = _write_fixture(tmp_path, canonical=False)
    monitor = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_public_watch",
        "role": "agent",
        "status": monitor_status,
        "done": monitor_status == "done",
        "text": "Observe a public target",
        "task_class": "continuous_monitor",
        "archive_state": "active",
        "source_section": "Agent Todo",
        "index": 1,
        "claimed_by": AGENT_ID,
        "target_key": "public-watch",
        "cadence": "1h",
        "next_due_at": "2000-01-01T00:00:00Z",
        "watch_only": "true",
    }
    items = [monitor]
    leases = []
    if lease is not None:
        leases = [{"schema_version": "task_lease_v0", "goal_id": GOAL_ID,
            "todo_id": monitor["todo_id"], "owner": AGENT_ID, "write_scopes": [], **lease}]
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=items, leases=leases,
        handoff_mode="hard_lease" if lease is not None else "soft_claim")
    if native:
        from loopx.control_plane.coordination.coordination_state_contract import TODO_DOMAIN_RECORD_FIELDS
        from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
        records = [{**{k: v for k, v in item.items() if k in TODO_DOMAIN_RECORD_FIELDS},
            "schema_version": "todo_domain_record_v0"} for item in projection["todos"]]
        projection["todos"] = records
        projection["todo_read_model"] = {"schema_version": "loopx_todo_domain_read_record_v0",
            "todo_count": len(records), "records_sha256": hashlib.sha256(canonical_bytes(records)).hexdigest(),
            "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS)}
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    return registry, runtime, state, monitor


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native", [False, True])
def test_public_cli_native_monitor_settles_with_independent_successor(tmp_path, monkeypatch, native, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor = _canonical(tmp_path, native=native, provider=provider)
    # No Markdown business source survives cutover. The existing renderer may
    # recreate its Todo-only view, but neither preflight nor commit requires it.
    state.unlink()
    result = run_json_cli("quota", "monitor-poll", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--runtime-profile", "generic_cli",
        "--todo-id", monitor["todo_id"], "--result-hash", "revision-a", "--material-change",
        "--next-agent-todo", "Validate the observed change", "--next-action-kind", "validate",
        "--next-task-repository", "https://github.com/example/repo.git", "--execute",
        registry_path=registry, runtime_root=runtime)
    writeback = result["todo_writeback"]
    assert writeback["material_change_generation"] == 1
    successor = writeback["next_todos"][0]
    assert successor["task_class"] == "advancement_task"
    assert successor["unblocks_todo_id"] == monitor["todo_id"]
    assert successor["continuation_policy"] == "independent_handoff"
    readback = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    assert readback["source_authority"] == ("sqlite_v0" if provider == "sqlite" else "file_v0")
    assert len(readback["todos"]) == 2
    assert state.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_monitor_retry_and_projection_failure_do_not_repeat_business(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor = _canonical(tmp_path, provider=provider)
    import loopx.control_plane.todos.provider_projection as delivery

    def unavailable(**kwargs):
        raise OSError("synthetic renderer unavailable")

    monkeypatch.setattr(delivery, "project_current_canonical_todos", unavailable)
    before = state.read_bytes()
    args = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID, execute=True,
        todo_id=monitor["todo_id"], agent_id=AGENT_ID, monitor_effect_id="isolated-poll-a",
        generated_at="2026-09-01T00:00:00Z", result_hash="revision-a", material_change=True,
        next_agent_todo="Validate observed change", next_action_kind="validate")
    first = write_monitor_poll_todo_state(**args)
    repeated = write_monitor_poll_todo_state(**args)
    assert first["source_authority"] == ("sqlite_v0" if provider == "sqlite" else "file_v0")
    assert first["projection_delivery"] == "pending"
    assert repeated["provider_replayed"] is True
    assert repeated["next_todos"] == first["next_todos"]
    assert state.read_bytes() == before
    assert len(read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)["todos"]) == 2


@pytest.mark.parametrize("role", ["agent", "user"])
def test_native_monitor_later_same_evidence_cannot_create_more_work(tmp_path, role):
    registry, runtime, _state, monitor = _canonical(tmp_path)
    args = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID, execute=True,
        todo_id=monitor["todo_id"], agent_id=AGENT_ID, result_hash="revision-a", material_change=True)
    write_monitor_poll_todo_state(**args, monitor_effect_id="generation-a", generated_at="2026-09-01T00:00:00Z")
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    followup = {"next_agent_todo": "Duplicate", "next_action_kind": "validate"} if role == "agent" else {
        "next_user_todo": "Duplicate", "next_user_task_class": "user_action"}
    with pytest.raises(RuntimeError, match="new material-change generation"):
        write_monitor_poll_todo_state(**args, **followup, monitor_effect_id="generation-b", generated_at="2026-09-01T01:00:00Z")
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == before


def test_cli_preserves_pending_display_diagnostic_after_business_settlement(tmp_path, monkeypatch, capsys):
    from loopx.cli import main
    import loopx.control_plane.todos.provider_projection as delivery

    registry, runtime, _state, monitor = _canonical(tmp_path, native=True)

    def unavailable(**kwargs):
        raise OSError("synthetic renderer unavailable")

    monkeypatch.setattr(delivery, "project_current_canonical_todos", unavailable)
    code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "quota", "monitor-poll", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--todo-id", monitor["todo_id"],
        "--result-hash", "revision-a", "--material-change", "--next-agent-todo", "Validate observation",
        "--next-action-kind", "validate", "--execute"])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["todo_writeback"]["projection_delivery"] == "pending"
    assert result["todo_writeback"]["projection_outbox"]["retry_business_mutation"] is False
    assert len(read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)["todos"]) == 2
