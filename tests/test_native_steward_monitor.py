"""Actual native Todo/deadline/Chat worker/poll path; no model is needed to observe."""
from datetime import datetime, timezone
import time

import pytest

from test_native_steward_managed_mode import original, storage
from test_native_steward_private import steward as steward_fixture, finish
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from loopx.capabilities.native_chat.steward_monitor import StewardMonitor, pump_steward_monitors
from loopx.chat_loopx_mode import DelegationWakeService

steward = steward_fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_due_native_monitor_uses_original_worker_and_once_only_native_poll(steward, tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, capture, _ = steward
    sid, managed = original(steward)
    storage(runtime, provider)
    preview = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=preview["plan_id"], execute=True)["actor"]
    monitor = StewardMonitor(runtime)
    quota_before = spent_slots(monitor, actor)
    transport.reconcile()  # deliver the synthetic original request first
    original_returns = list(steward[2].writes)
    before = capture.read_bytes()
    plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
    enabled = monitor.configure(binding["binding_id"], operation="enable", execute=True,
        expected_plan_id=plan["plan_id"])
    todo = enabled["monitor"]
    assert todo["task_class"] == "continuous_monitor" and todo["target_key"] == "steward-portfolio-watch"
    assert capture.read_bytes() == before
    transport.reconcile()
    assert steward[2].writes == original_returns  # a watch never replies to business on behalf of the Bot
    # Exercise the actual production producer, not just the submit adapter.
    # Disconnecting the host's monitor hook makes this oracle fail.
    turns, start_worker = [], runtime._start_accepted_turn_worker
    def record_worker(**kwargs):
        turns.append(kwargs)
        return start_worker(**kwargs)
    monkeypatch.setattr(runtime, "_start_accepted_turn_worker", record_worker)
    service = DelegationWakeService(runtime, interval=.02).start()
    try:
        deadline = time.monotonic() + 10
        while not turns:
            assert time.monotonic() < deadline, "the production host never dispatched the due native monitor"
            time.sleep(.02)
        completed = finish(runtime, turns[0])
    finally:
        service.close()
    assert len(turns) == 1
    assert completed["status"] == "completed", completed
    native = completed["response"]["native_loopx"]
    assert native["status"] == "observed" and native["model_invoked"] is False and native["quota_spent"] is False
    after = monitor.current(actor)
    assert after["result_hash"] == native["monitor"]["result_hash"]
    assert datetime.fromisoformat(after["next_due_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
    assert capture.read_bytes() == before  # no resume/start/inject/model call
    assert not pump_steward_monitors(runtime)
    # A fresh host object reads the native deadline, not a shadow timer file.
    assert StewardMonitor(runtime).current(actor)["result_hash"] == after["result_hash"]
    pause = monitor.configure(binding["binding_id"], operation="pause")["plan"]
    monitor.configure(binding["binding_id"], operation="pause", execute=True,
        expected_plan_id=pause["plan_id"])
    assert monitor.current(actor)["status"] == "blocked"
    assert not pump_steward_monitors(runtime)
    assert capture.read_bytes() == before
    resume = monitor.configure(binding["binding_id"], operation="enable", cadence_minutes=45)["plan"]
    resumed = monitor.configure(binding["binding_id"], operation="enable", cadence_minutes=45,
        execute=True, expected_plan_id=resume["plan_id"])
    assert resumed["monitor"]["status"] == "open" and resumed["monitor"]["cadence"] == "45m"
    assert not pump_steward_monitors(runtime)
    due_window(monitor, actor)
    again = pump_steward_monitors(runtime)
    assert len(again) == 1
    assert finish(runtime, again[0])["status"] == "completed"
    assert monitor.current(actor)["result_hash"] == after["result_hash"]  # own bookkeeping is not a material change
    assert capture.read_bytes() == before
    transport.reconcile()
    assert steward[2].writes == original_returns
    assert spent_slots(monitor, actor) == quota_before
    assert spent_slots(monitor, actor) == quota_before


def prepared(steward, tmp_path, monkeypatch, provider="sqlite"):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _, runtime, _, _, binding, _, _ = steward
    sid, managed = original(steward)
    storage(runtime, provider)
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=plan["plan_id"], execute=True)["actor"]
    monitor = StewardMonitor(runtime)
    plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
    monitor.configure(binding["binding_id"], operation="enable", execute=True, expected_plan_id=plan["plan_id"])
    return sid, actor, monitor


@pytest.mark.parametrize("unreadable", ["session", "queued_turn"])
@pytest.mark.parametrize("storage_provider", ["file", "sqlite"])
def test_unreadable_conversation_does_not_starve_another_native_monitor(steward, tmp_path, monkeypatch, unreadable, storage_provider):
    store, runtime, provider, transport, binding, capture, _ = steward
    sid, actor, monitor = prepared(steward, tmp_path, monkeypatch, storage_provider)
    retained = monitor.current(actor)
    other = transport.bindings.configure(transport_ref="notes-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", context_kind="steward")
    message = "Keep this independent steward available."
    transport.admit("notes-app", provider.event("notes-app", "independent-steward", message))
    accepted = next(row for row in transport.core.pending() if row["message"] == message)
    assert finish(runtime, accepted)["status"] == "completed"
    preview = transport.managed_steward.prepare(other["binding_id"], session_id=accepted["session_id"])["plan"]
    other_actor = transport.managed_steward.prepare(other["binding_id"], session_id=accepted["session_id"],
        expected_plan_id=preview["plan_id"], execute=True)["actor"]
    plan = monitor.configure(other["binding_id"], operation="enable")["plan"]
    monitor.configure(other["binding_id"], operation="enable", execute=True, expected_plan_id=plan["plan_id"])
    assert [row["managed_actor"]["session_id"] for row in transport.bindings.read()["bindings"]] == [sid, accepted["session_id"]]
    if unreadable == "queued_turn":
        queued, _ = store.create_queued_turn(sid, client_turn_id="retained-request", message="Keep this request", origin="web")
        path = store._turn_path(sid, queued["turn_id"])
    else:
        path = store._session_path(sid)
    original_bytes, calls = path.read_bytes(), capture.read_bytes()
    path.write_bytes(b"\xff")
    try:
        turns = pump_steward_monitors(runtime)
        assert len(turns) == 1 and turns[0]["session_id"] == accepted["session_id"]
        completed = finish(runtime, turns[0])
        assert completed["status"] == "completed", completed
        assert completed["response"]["native_loopx"]["monitor"]["result_hash"] == monitor.current(other_actor)["result_hash"]
        assert monitor.current(actor) == retained and path.read_bytes() == b"\xff"
        assert capture.read_bytes() == calls
    finally:
        path.write_bytes(original_bytes)
    recovered = pump_steward_monitors(runtime)
    if unreadable == "session":
        assert len(recovered) == 1 and recovered[0]["session_id"] == sid
        assert finish(runtime, recovered[0])["status"] == "completed"
    else:
        assert not recovered and store.queued_turns(sid)[0]["turn_id"] == queued["turn_id"]
    assert capture.read_bytes() == calls


def due_window(monitor, actor, deadline="2001-01-01T00:00:00Z"):
    """Advance a synthetic window through the real native Todo/lease owners.

    No sleep for 30 minutes, fake scheduler answer or raw authority edit.
    """
    from uuid import uuid4
    from loopx.capabilities.native_chat.steward_commands import native_command
    todo = monitor.current(actor)
    key = "test-window." + uuid4().hex
    lease = monitor._acquire(actor, todo, key)["lease"]
    try:
        native_command(monitor.controller, ["todo", "update", "--goal-id", actor["goal_id"],
            "--agent-id", actor["agent_id"], "--todo-id", todo["todo_id"],
            "--next-due-at", deadline, "--update-operation-id", key,
            "--task-lease-idempotency-key", key, "--task-lease-expected-version", str(lease["version"]), "--execute"])
    finally:
        monitor._release(actor, todo, lease)
    getattr(monitor.controller, "_steward_monitor_next_check", {}).clear()


def spent_slots(monitor, actor):
    from loopx.capabilities.native_chat.steward_commands import native_command
    state = native_command(monitor.controller, ["quota", "status", "--goal-id", actor["goal_id"]])
    rows = [row for group in state["groups"].values() for row in group if row["goal_id"] == actor["goal_id"]]
    assert len(rows) == 1
    return rows[0]["quota"]["spent_slots"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_restarted_host_reads_paused_state_then_runs_one_fresh_native_generation(steward, tmp_path, monkeypatch, provider):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    _, runtime, _, _, binding, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
    plan = monitor.configure(binding["binding_id"], operation="pause")["plan"]
    monitor.configure(binding["binding_id"], operation="pause", execute=True, expected_plan_id=plan["plan_id"])
    retained, calls = monitor.current(actor), capture.read_bytes()
    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(runtime.store.root.parent), codex_bin=runtime.codex_bin,
        project_contexts=runtime.project_contexts, registry_path=runtime.registry_path)
    restarted.manager_scope_resolver = runtime.manager_scope_resolver
    try:
        monitor = StewardMonitor(restarted)
        assert monitor.current(actor) == retained and not pump_steward_monitors(restarted)
        plan = monitor.configure(binding["binding_id"], operation="enable")["plan"]
        monitor.configure(binding["binding_id"], operation="enable", execute=True, expected_plan_id=plan["plan_id"])
        assert not pump_steward_monitors(restarted)
        due_window(monitor, actor)
        turns = pump_steward_monitors(restarted)
        assert len(turns) == 1 and finish(restarted, turns[0])["status"] == "completed"
        assert not pump_steward_monitors(restarted) and capture.read_bytes() == calls
        assert monitor.current(actor)["todo_id"] == retained["todo_id"]
        assert len(restarted.store.list_sessions()) == 1
    finally:
        restarted.close()


def test_native_goal_stop_blocks_observation_and_owner_can_still_pause_the_monitor(steward, tmp_path, monkeypatch):
    from loopx.control_plane.goals.activation_service import set_goal_activation_state
    _, runtime, _, transport, binding, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls = monitor.current(actor), capture.read_bytes()
    stopped = set_goal_activation_state(registry_path=runtime.registry_path, goal_id=actor["goal_id"],
        state="stopped", reason="Original owner stopped this synthetic Goal.",
        runtime_root_override=str(runtime.coordination_runtime_root), actor_kind="owner", execute=True)
    assert stopped["ok"]
    assert not pump_steward_monitors(runtime)
    assert monitor.current(actor) == before and capture.read_bytes() == calls
    health = transport.managed_steward.health(binding["binding_id"])
    assert health["goal_active"] is False and health["scheduling_enabled"] is False
    with pytest.raises(ValueError):
        monitor.configure(binding["binding_id"], operation="enable")
    plan = monitor.configure(binding["binding_id"], operation="pause")["plan"]
    monitor.configure(binding["binding_id"], operation="pause", execute=True, expected_plan_id=plan["plan_id"])
    assert monitor.current(actor)["status"] == "blocked"


@pytest.mark.parametrize("busy", ["active", "queued"])
def test_due_watch_retains_deadline_behind_original_user_work(steward, tmp_path, monkeypatch, busy):
    store, runtime, _, _, _, capture, _ = steward
    sid, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls = monitor.current(actor), capture.read_bytes()
    if busy == "active":
        store.update_session(sid, active_turn_id="original-user-work")
    else:
        store.create_queued_turn(sid, client_turn_id="original-followup", message="Handle me first", origin="web")
    assert not pump_steward_monitors(runtime)
    assert monitor.current(actor) == before and capture.read_bytes() == calls


def test_unknown_accepted_watch_is_read_back_without_replay(steward, tmp_path, monkeypatch):
    _, runtime, _, _, _, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls, starts = monitor.current(actor), capture.read_bytes(), []
    def lost_worker_response(**kwargs):
        starts.append(kwargs)
        raise OSError("worker effect is unknown")
    monkeypatch.setattr(runtime, "_start_accepted_turn_worker", lost_worker_response)
    assert not pump_steward_monitors(runtime)
    runtime._steward_monitor_next_check.clear()  # a fresh process has no ephemeral hint
    assert not pump_steward_monitors(runtime)
    assert len(starts) == 1 and monitor.current(actor) == before
    assert capture.read_bytes() == calls


def test_owner_pause_fences_a_poll_which_already_read_the_portfolio(steward, tmp_path, monkeypatch):
    import loopx.capabilities.native_chat.steward_monitor as module
    _, runtime, _, _, binding, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls = monitor.current(actor), capture.read_bytes()
    commit, paused = module.StewardMonitor._commit_poll, []
    def pause_before_commit(self, host, request, turn, context, argv):
        if not paused:
            plan = monitor.configure(binding["binding_id"], operation="pause")["plan"]
            paused.append(monitor.configure(binding["binding_id"], operation="pause",
                execute=True, expected_plan_id=plan["plan_id"]))
        return commit(self, host, request, turn, context, argv)
    monkeypatch.setattr(module.StewardMonitor, "_commit_poll", pause_before_commit)
    turn = pump_steward_monitors(runtime)[0]
    assert finish(runtime, turn)["status"] == "failed"
    current = monitor.current(actor)
    assert paused and current["status"] == "blocked"
    assert current.get("result_hash") == before.get("result_hash")
    assert current["next_due_at"] == before["next_due_at"]
    assert not pump_steward_monitors(runtime) and capture.read_bytes() == calls


def test_pause_resume_cannot_let_old_collection_acquire_a_new_lease_generation(steward, tmp_path, monkeypatch):
    import loopx.capabilities.native_chat.steward_monitor as module
    _, runtime, _, _, binding, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls = monitor.current(actor), capture.read_bytes()
    command, replaced = module.native_command, []
    def change_after_guard(controller, argv):
        result = command(controller, argv)
        if argv[:2] == ["quota", "should-run"] and not replaced:
            replaced.append(True)
            for operation in ["pause", "enable"]:
                plan = monitor.configure(binding["binding_id"], operation=operation)["plan"]
                monitor.configure(binding["binding_id"], operation=operation, execute=True, expected_plan_id=plan["plan_id"])
            # Even an explicit owner restoring the same timestamp cannot
            # revive the old execution proof or its previously collected data.
            due_window(monitor, actor, before["next_due_at"])
        return result
    monkeypatch.setattr(module, "native_command", change_after_guard)
    turn = pump_steward_monitors(runtime)[0]
    result = finish(runtime, turn)
    assert result["status"] == "failed"
    assert "version" in str(result.get("error", "")).lower(), result
    assert monitor.current(actor).get("result_hash") == before.get("result_hash")
    assert capture.read_bytes() == calls


@pytest.mark.parametrize("change", ["disconnect", "selected-scope"])
def test_binding_change_after_collection_prevents_native_commit(steward, tmp_path, monkeypatch, change):
    import threading
    import loopx.capabilities.native_chat.steward_monitor as module
    _, runtime, _, transport, binding, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls = monitor.current(actor), capture.read_bytes()
    commit, revocations, errors = module.StewardMonitor._commit_poll, [], []
    def change_before_commit(self, host, request, turn, context, argv):
        if change == "disconnect":
            # The real owner disconnects from a different request thread; its
            # stop waits for this worker, so invoking it on this worker would
            # manufacture a self-wait instead of exercising revocation.
            def disconnect():
                try:
                    transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=disconnect)
            revocations.append(worker)
            worker.start()
            deadline = time.monotonic() + 5
            while any(row["binding_id"] == binding["binding_id"] and row.get("enabled") is True
                      for row in transport.bindings.read()["bindings"]):
                assert time.monotonic() < deadline
                time.sleep(.01)
        else:
            transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
                executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
        return commit(self, host, request, turn, context, argv)
    monkeypatch.setattr(module.StewardMonitor, "_commit_poll", change_before_commit)
    result = finish(runtime, pump_steward_monitors(runtime)[0])
    for worker in revocations:
        worker.join(5)
        assert not worker.is_alive()
    assert not errors
    assert result["status"] in {"failed", "interrupted"}, result
    current = monitor.current(actor)
    assert current.get("result_hash") == before.get("result_hash") and current["next_due_at"] == before["next_due_at"]
    if change == "disconnect":
        import json
        extra = capture.read_bytes()[len(calls):].decode().splitlines()
        assert not any(json.loads(line).get("method") in {"thread/start", "thread/resume", "turn/start"} for line in extra)
    else:
        assert capture.read_bytes() == calls


def test_unavailable_provider_retains_the_native_deadline_until_recovery(steward, tmp_path, monkeypatch):
    _, runtime, provider, _, _, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls = monitor.current(actor), capture.read_bytes()
    provider.owner_ready = False
    assert not pump_steward_monitors(runtime)
    assert monitor.current(actor) == before and capture.read_bytes() == calls
    provider.owner_ready = True
    runtime._steward_monitor_next_check.clear()
    turns = pump_steward_monitors(runtime)
    assert len(turns) == 1 and finish(runtime, turns[0])["status"] == "completed"
    assert capture.read_bytes() == calls


def test_fresh_scope_includes_a_new_stopped_goal_without_reusing_or_rewriting_model_context(steward, tmp_path, monkeypatch):
    from loopx.bootstrap import bootstrap_project
    from loopx.control_plane.goals.activation_service import set_goal_activation_state
    store, runtime, _, _, binding, capture, _ = steward
    sid, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    original_scope = store.load_session(sid)["manager_authorization_scope_id"]
    project = tmp_path / "another-authorized-goal"
    project.mkdir()
    created = bootstrap_project(project=project, registry_path=runtime.registry_path,
        runtime_root=runtime.coordination_runtime_root, goal_id="another-authorized-goal",
        objective="Retain this independently stopped synthetic work.", domain="test", role="primary",
        parent_goal_id=None, state_file=None, goal_doc=None, adapter_kind="generic_project_goal_v0",
        adapter_status="connected", next_probe=None, spawn_allowed=False, max_children=0,
        allowed_domains=[], write_scope=[], preserve_todos=True, force=False, dry_run=False, sync_global=False)
    assert created["ok"]
    stopped = set_goal_activation_state(registry_path=runtime.registry_path, goal_id="another-authorized-goal",
        state="stopped", reason="Keep this stopped work in the portfolio.",
        runtime_root_override=str(runtime.coordination_runtime_root), actor_kind="owner", execute=True)
    assert stopped["ok"]
    calls = capture.read_bytes()
    turn = pump_steward_monitors(runtime)[0]
    result = finish(runtime, turn)
    assert result["status"] == "completed", result
    observed = result["response"]["native_loopx"]["monitor"]
    assert observed["observed_goal_count"] == 2
    first_hash = observed["result_hash"]
    # A second real collection ignores projection clocks and our own poll.
    due_window(monitor, actor)
    second = pump_steward_monitors(runtime)[0]
    result = finish(runtime, second)
    assert result["status"] == "completed", result
    assert result["response"]["native_loopx"]["monitor"]["result_hash"] == first_hash
    assert not result["response"]["native_loopx"]["monitor"]["material_change"]
    assert capture.read_bytes() == calls and store.load_session(sid)["manager_authorization_scope_id"] == original_scope
    assert monitor.current(actor)["result_hash"] == first_hash
