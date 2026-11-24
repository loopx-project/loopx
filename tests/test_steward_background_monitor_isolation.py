"""Background Steward monitor carrier isolation and legacy recovery tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.capabilities.native_chat.steward_monitor import (
    FOLLOWUP_TARGET_PREFIX,
    StewardMonitor,
    monitor_turn_id,
    pump_steward_monitors,
)
from loopx.capabilities.native_chat.steward_turn import (
    StewardTurnHost,
    native_steward_client_id,
)
from loopx.chat_lark_api import manager_source_scope_resolver
from loopx.control_plane.collaboration import conversation_scope
from test_native_steward_managed_mode import original, storage
from test_native_steward_monitor import edit_local_watch, local_watch_row
from test_native_steward_private import finish, steward as steward_fixture
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

steward = steward_fixture


def _prepare_steward_monitor(steward_env, tmp_path: Path, monkeypatch) -> tuple[str, dict, StewardMonitor, str]:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, _, workspace = steward_env
    payload = json.loads(runtime.registry_path.read_text(encoding="utf-8"))
    payload["goals"] = [
        *list(payload.get("goals") or []),
        {
            "id": "peer-goal",
            "repo": str(workspace),
            "coordination": {"registered_agents": ["peer-worker"]},
        },
    ]
    runtime.registry_path.write_text(json.dumps(payload), encoding="utf-8")

    sid, managed = original(steward_env)
    storage(runtime, "sqlite")
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(
        binding["binding_id"],
        session_id=sid,
        expected_plan_id=plan["plan_id"],
        execute=True,
    )["actor"]
    transport.reconcile()
    monitor = StewardMonitor(runtime)
    enable_plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
    enabled = monitor.configure(
        binding["binding_id"],
        operation="enable",
        expected_plan_id=enable_plan["plan_id"],
        execute=True,
    )
    return sid, actor, monitor, enabled["monitor"]["todo_id"]


def test_due_monitor_runs_in_background_session_while_human_session_is_busy_and_queued(
    steward, tmp_path, monkeypatch,
) -> None:
    store, runtime, provider, transport, binding, capture, _ = steward
    sid, actor, monitor, todo_id = _prepare_steward_monitor(steward, tmp_path, monkeypatch)
    binding_id = binding["binding_id"]
    bg_sid = StewardTurnHost.background_session_id(binding_id)

    # Wire the production HTTP server scope resolver so background carrier scope resolution is exercised end-to-end.
    fake_server = SimpleNamespace(
        runtime_controller=runtime,
        registry_path=runtime.registry_path,
        runtime_root_override=str(runtime.coordination_runtime_root),
    )
    runtime.manager_scope_resolver = manager_source_scope_resolver(
        fake_server,
        registry_path=runtime.registry_path,
        executor_runtime_root=runtime.coordination_runtime_root,
    )

    # Simulate an active long-running turn + queued human follow-up on the foreground Steward session.
    store.update_session(sid, status="busy", active_turn_id="foreground-active-turn")
    queued_human, _ = store.create_queued_turn(
        sid,
        client_turn_id="foreground-queued-user-msg",
        message="请帮我跟进线上状态。",
        origin="lark",
    )
    human_messages_before = store.messages(sid)
    calls_before = capture.read_bytes()
    writes_before = list(provider.writes)

    getattr(runtime, "_steward_monitor_next_check", {}).clear()
    dispatched = pump_steward_monitors(runtime)
    assert len(dispatched) == 1
    bg_turn = dispatched[0]
    assert bg_turn["session_id"] == bg_sid
    assert bg_turn["session_id"] != sid

    completed = finish(runtime, bg_turn)
    assert completed["status"] == "completed", completed
    native = completed["response"]["native_loopx"]
    assert native["ok"] is True
    assert native["status"] == "observed"
    assert native["model_invoked"] is False
    assert native["quota_spent"] is False

    # Carrier session invariants: external_audience, steward_context=None, steward_background_context populated.
    carrier = store.load_session(bg_sid)
    parent = store.load_session(sid)
    assert carrier is not None and parent is not None
    assert carrier["channel_id"] == f"manager.external.steward.background.{binding_id}"
    assert carrier.get("steward_context") is None
    assert carrier.get("project_context") is None
    assert carrier["steward_background_context"] == parent["steward_context"]
    assert conversation_scope(carrier) == {
        "kind": "external_audience",
        "goal_ids": [],
        "private_conversation": False,
    }

    # Tampered background session metadata is rejected by manager_source_scope_resolver.
    with pytest.raises(ValueError, match="bound steward background audience changed"):
        runtime.manager_scope_resolver({**carrier, "channel_id": "manager.external.other"})
    with pytest.raises(ValueError, match="bound steward background audience changed"):
        runtime.manager_scope_resolver({**carrier, "session_id": "a" * 32})

    # Human session discovery & transcript remain 100% isolated from the background carrier.
    assert [row["session_id"] for row in transport.bindings.local_sessions(binding_id)] == [sid]
    assert store.messages(sid) == human_messages_before
    assert store.load_session(sid)["active_turn_id"] == "foreground-active-turn"
    assert [row["turn_id"] for row in store.queued_turns(sid)] == [queued_human["turn_id"]]
    assert capture.read_bytes() == calls_before
    assert provider.writes == writes_before
    assert local_watch_row(runtime, actor, todo_id).get("result_hash") == native["monitor"]["result_hash"]


def test_advancement_followup_waits_for_human_idle_then_dispatches_without_throttle_delay(
    steward, tmp_path, monkeypatch,
) -> None:
    from loopx.capabilities.native_chat.steward_commands import native_command

    store, runtime, _, _, binding, _, _ = steward
    sid, actor, _, todo_id = _prepare_steward_monitor(steward, tmp_path, monkeypatch)

    # Create a runnable Steward monitor follow-up advancement Todo.
    adv = native_command(
        runtime,
        [
            "todo",
            "add",
            "--goal-id",
            actor["goal_id"],
            "--role",
            "agent",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "triage",
            "--continuation-policy",
            "same_agent_non_delivery",
            "--claimed-by",
            actor["agent_id"],
            "--target-key",
            f"{FOLLOWUP_TARGET_PREFIX}{todo_id}",
            "--text",
            "[steward-portfolio-watch] 信号变更跟进：检查新信号",
            "--operation-id",
            "test-adv-followup-1",
            "--execute",
        ],
    )
    adv_todo_id = adv["todo_id"]

    # Make the watch due while human session is busy.
    edit_local_watch(
        runtime,
        actor,
        todo_id,
        "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
    )
    store.update_session(sid, status="busy", active_turn_id="busy-human-turn")
    getattr(runtime, "_steward_monitor_next_check", {}).clear()

    # Pump 1 while human is busy: dispatches ONLY the background watch, NOT the advancement task.
    first_batch = pump_steward_monitors(runtime)
    assert len(first_batch) == 1
    assert first_batch[0]["loopx_request"]["work_kind"] == "monitor"
    assert first_batch[0]["session_id"] == StewardTurnHost.background_session_id(binding["binding_id"])
    assert finish(runtime, first_batch[0])["status"] == "completed"

    # Pump 2 while human is still busy: advancement task remains gated on human_idle.
    getattr(runtime, "_steward_monitor_next_check", {}).clear()
    assert pump_steward_monitors(runtime) == []

    # Release human session without clearing _steward_monitor_next_check:
    # foreground idle transition must immediately dispatch the advancement task without a 60s delay!
    store.update_session(sid, status="ready", active_turn_id=None)
    with monkeypatch.context() as hold_worker:
        hold_worker.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: None)
        second_batch = pump_steward_monitors(runtime)
    assert len(second_batch) == 1
    assert second_batch[0]["session_id"] == sid
    assert second_batch[0]["loopx_request"]["work_kind"] == "native_turn"
    assert second_batch[0]["loopx_request"]["todo_id"] == adv_todo_id


@pytest.mark.parametrize("legacy_pre_upgrade", [False, True])
def test_monitor_restart_recovery_recovers_background_and_legacy_watches_while_human_busy(
    steward, tmp_path, monkeypatch, legacy_pre_upgrade: bool,
) -> None:
    store, runtime, _, _, binding, _, _ = steward
    sid, actor, monitor, todo_id = _prepare_steward_monitor(steward, tmp_path, monkeypatch)
    binding_id = binding["binding_id"]
    bg_sid = StewardTurnHost.background_session_id(binding_id)
    todo = local_watch_row(runtime, actor, todo_id)
    turn_instance = monitor_turn_id(todo)
    client_id = native_steward_client_id(actor, turn_instance)

    if legacy_pre_upgrade:
        # Simulate a pre-upgrade stopped monitor turn recorded on actor["session_id"].
        request = {
            "operation": "native_turn",
            "host": "codex-app-server",
            "actor": actor,
            "turn_instance_id": turn_instance,
            "todo_id": todo_id,
            "work_kind": "monitor",
            "validation_command": None,
            "monitor_deadline": todo["next_due_at"],
            "monitor_lease_version": 0,
        }
        accepted = store.accept_managed_turn(
            sid,
            client_turn_id=client_id,
            message="Advance the currently admitted native LoopX work.",
            display_message="检查授权工作状态。",
            origin="native_loopx",
            loopx_execution=True,
            loopx_request=request,
        )
        original_turn = accepted.turn
        original_sid = sid
    else:
        getattr(runtime, "_steward_monitor_next_check", {}).clear()
        with monkeypatch.context() as stopped_worker:
            stopped_worker.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: None)
            original_turn = pump_steward_monitors(runtime)[0]
        original_sid = bg_sid
        assert original_turn["session_id"] == bg_sid

    # Mark the unstarted watch as failed due to server_restarted.
    store.update_turn(
        original_sid,
        original_turn["turn_id"],
        status="failed",
        error_code="server_restarted",
        error="The original host stopped before observation.",
        response=None,
        upstream_turn_id=None,
    )
    store.append_event(
        original_sid,
        original_turn["turn_id"],
        kind="turn.failed",
        payload={"error_code": "server_restarted"},
    )
    store.update_session(original_sid, active_turn_id=None, status="ready")

    # Make the foreground human session busy AND queued during restart recovery!
    store.update_session(sid, status="busy", active_turn_id="foreground-human-turn")
    store.create_queued_turn(
        sid,
        client_turn_id="foreground-queued-during-recovery",
        message="继续讨论当前方案。",
        origin="lark",
    )

    getattr(runtime, "_steward_monitor_next_check", {}).clear()
    recovered = pump_steward_monitors(runtime)
    assert len(recovered) == 1
    assert recovered[0]["session_id"] == bg_sid
    assert recovered[0]["loopx_request"]["monitor_restart_recovery"] == {
        "original_turn_id": original_turn["turn_id"],
    }
    done = finish(runtime, recovered[0])
    assert done["status"] == "completed", done
    assert done["response"]["native_loopx"]["status"] == "observed"

    # Subsequent pump sees the completed recovery on bg_sid and does not re-dispatch!
    getattr(runtime, "_steward_monitor_next_check", {}).clear()
    assert pump_steward_monitors(runtime) == []
