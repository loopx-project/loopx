"""Actual native Todo/deadline/Chat worker/poll path; no model is needed to observe."""
from datetime import datetime, timedelta, timezone
import time

import pytest

from test_native_steward_managed_mode import original, storage
from test_native_steward_private import steward as steward_fixture, finish
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from loopx.capabilities.native_chat.steward_monitor import StewardMonitor, pump_steward_monitors
from loopx.chat_loopx_mode import DelegationWakeService

steward = steward_fixture


def local_watch(steward, tmp_path, monkeypatch, provider="sqlite"):
    from test_native_steward_turn_host import managed_fixture, cli
    service, session, actor, _, _, capture = managed_fixture(steward, tmp_path, monkeypatch, provider)
    runtime = steward[1]
    added = cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
        "--text", "Observe this Goal's native work without running a business task.",
        "--claimed-by", actor["agent_id"], "--task-class", "continuous_monitor",
        "--action-kind", "local_runtime_observe", "--continuation-policy", "same_agent_non_delivery",
        "--required-capability", "filesystem", "--target-key", "managed-session-watch." + session["session_id"],
        "--watch-only", "--cadence", "30m", "--next-due-at",
        (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(), "--execute")
    return service, session, actor, added["todo_id"], capture


def local_watch_row(runtime, actor, todo_id):
    from loopx.control_plane.todos.list_readback import list_goal_todos
    return list_goal_todos(registry_path=runtime.registry_path,
        runtime_root_arg=str(runtime.coordination_runtime_root), goal_id=actor["goal_id"],
        todo_id=todo_id, role="agent")["todo"]


def edit_local_watch(runtime, actor, todo_id, *changes):
    from loopx.capabilities.native_chat.steward_commands import native_command
    monitor = StewardMonitor(runtime)
    todo = local_watch_row(runtime, actor, todo_id)
    key = "edit-watch." + str(time.time_ns())
    if "--status" in changes:
        native_command(runtime, ["todo", "update", "--goal-id", actor["goal_id"], "--todo-id", todo_id,
            "--role", "agent", "--agent-id", actor["agent_id"], "--update-operation-id", key,
            "--reason", "Owner changed the native watch lifecycle.", *changes, "--execute"])
        runtime._steward_monitor_next_check = {}
        return
    lease = monitor._acquire(actor, todo, key)["lease"]
    try:
        native_command(runtime, ["todo", "update", "--goal-id", actor["goal_id"],
            "--todo-id", todo_id, "--agent-id", actor["agent_id"],
            "--task-lease-idempotency-key", key, "--task-lease-expected-version", str(lease["version"]),
            *changes, "--execute"])
    finally:
        monitor._release(actor, todo, lease)
    runtime._steward_monitor_next_check = {}  # a lost hint is never the deadline authority


def test_registration_and_a_managed_session_do_not_implicitly_enable_a_watch(steward, tmp_path, monkeypatch):
    from test_native_steward_turn_host import managed_fixture
    _, session, actor, _, _, capture = managed_fixture(steward, tmp_path, monkeypatch)
    store, runtime, *_ = steward
    runtime.project_contexts.conversation_bindings = None
    before, history = capture.read_bytes(), store.messages(session["session_id"])
    todos = StewardMonitor(runtime).todos(actor)
    assert not pump_steward_monitors(runtime)
    assert capture.read_bytes() == before and store.messages(session["session_id"]) == history
    assert StewardMonitor(runtime).todos(actor) == todos


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_managed_watch_uses_existing_idle_worker_without_private_binding(steward, tmp_path, monkeypatch, provider):
    from loopx.chat_store import ChatSessionStore
    from loopx.chat_runtime import ChatRuntimeController

    service, session, actor, todo_id, capture = local_watch(steward, tmp_path, monkeypatch, provider)
    store, runtime, _, transport, *_ = steward
    # No private source/provider is needed for this local Goal execution path.
    runtime.project_contexts.conversation_bindings = None
    before, history = capture.read_bytes(), store.messages(session["session_id"])
    quota = spent_slots(StewardMonitor(runtime), actor)
    turns, original_worker = [], runtime._start_accepted_turn_worker
    def record(**kw):
        turns.append(kw)
        return original_worker(**kw)
    monkeypatch.setattr(runtime, "_start_accepted_turn_worker", record)
    wake = DelegationWakeService(runtime, interval=.02).start()
    try:
        deadline = time.monotonic() + 15
        while not turns:
            assert time.monotonic() < deadline, "the existing production pump did not continue the local native watch"
            time.sleep(.02)
        result = finish(runtime, turns[0])
    finally:
        wake.close()
    assert result["status"] == "completed", result
    native = result["response"]["native_loopx"]
    assert native["status"] == "observed" and not native["model_invoked"] and not native["quota_spent"]
    assert native["monitor"]["observed_task_count"] >= 1  # unrelated P1 validation work remains runnable
    after = local_watch_row(runtime, actor, todo_id)
    assert after["result_hash"] == native["monitor"]["result_hash"]
    assert datetime.fromisoformat(after["next_due_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
    assert store.messages(session["session_id"])[:len(history)] == history
    assert capture.read_bytes() == before and spent_slots(StewardMonitor(runtime), actor) == quota
    assert len(turns) == 1 and not pump_steward_monitors(runtime)
    # Exact accepted operation readback remains non-executing, even though its
    # completed poll has advanced both the deadline and native lease generation.
    request = result["loopx_request"]
    old = store.load_turn(session["session_id"], result["turn_id"])
    same, created = service.submit(session["session_id"], agent_id=actor["agent_id"],
        todo_id=todo_id, operation_id=request["turn_instance_id"].removeprefix("managed-session."),
        monitor_deadline=request["monitor_deadline"])
    assert not created and same == old and capture.read_bytes() == before
    # Actual controller restart observes the existing future native deadline.
    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent),
        codex_bin=runtime.codex_bin, registry_path=runtime.registry_path, project_contexts=runtime.project_contexts)
    try:
        assert not pump_steward_monitors(restarted)
        # Distinct native poll effects require strictly increasing observation
        # time. Advance to the next real scheduler tick before authoring this
        # synthetic second window; do not retry a rejected/unknown operation.
        from loopx.control_plane.runtime.time import now_utc
        wait_for_next_observation_tick(after)
        edit_local_watch(restarted, actor, todo_id, "--next-due-at",
            now_utc().isoformat())
        second = pump_steward_monitors(restarted)
        current_watch = local_watch_row(restarted, actor, todo_id)
        from loopx.control_plane.scheduler.monitor_todo import monitor_todo_is_due
        current_session = restarted.store.load_session(session["session_id"])
        assert len(second) == 1, {"session_status": current_session["status"], "active": current_session["active_turn_id"],
            "due": monitor_todo_is_due(current_watch), "watch": {k: current_watch.get(k) for k in
                ["status", "next_due_at", "resume_when", "resume_ready", "watch_only", "claimed_by"]}}
        complete = finish(restarted, second[0])
        assert complete["status"] == "completed", complete
        assert not complete["response"]["native_loopx"]["monitor"]["material_change"]
        assert local_watch_row(restarted, actor, todo_id)["result_hash"] == after["result_hash"]
        assert capture.read_bytes() == before
        edit_local_watch(restarted, actor, todo_id, "--status", "blocked", "--clear-resume-when")
        assert not pump_steward_monitors(restarted)
    finally:
        restarted.close()


def test_a_failed_accepted_local_watch_is_read_back_without_replaying_after_restart(steward, tmp_path, monkeypatch):
    from loopx.chat_store import ChatSessionStore
    from loopx.chat_runtime import ChatRuntimeController
    _, session, actor, todo_id, capture = local_watch(steward, tmp_path, monkeypatch)
    store, runtime, *_ = steward
    runtime.project_contexts.conversation_bindings = None
    before, watch = capture.read_bytes(), local_watch_row(runtime, actor, todo_id)
    def unavailable(*_args):
        raise OSError("Synthetic source became unavailable before native commit")
    with monkeypatch.context() as mutation:
        mutation.setattr(StewardMonitor, "_commit_poll", unavailable)
        turns = pump_steward_monitors(runtime)
        assert len(turns) == 1
        failed = finish(runtime, turns[0])
        assert failed["status"] == "failed"
    history = store.messages(session["session_id"])
    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent),
        codex_bin=runtime.codex_bin, registry_path=runtime.registry_path, project_contexts=runtime.project_contexts)
    try:
        assert not pump_steward_monitors(restarted)
        assert restarted.store.load_turn(session["session_id"], failed["turn_id"]) == failed
        assert restarted.store.messages(session["session_id"]) == history and capture.read_bytes() == before
        assert local_watch_row(restarted, actor, todo_id) == watch
    finally:
        restarted.close()


@pytest.mark.parametrize("fault", ["session", "queued_turn"])
def test_unreadable_local_conversation_does_not_starve_an_independent_watch(steward, tmp_path, monkeypatch, fault):
    _, session, actor, todo_id, capture = local_watch(steward, tmp_path, monkeypatch)
    store, runtime, *_ = steward
    runtime.project_contexts.conversation_bindings = None
    original = store.load_session(session["session_id"])
    other = store.create_session(goal_id=actor["goal_id"], goal_instance_id=actor["goal_instance_id"],
        managed_agent_id=actor["agent_id"], managed_workspace_path=original["managed_workspace_path"],
        agent_id="codex", adapter_kind="codex_app_server", upstream_thread_id="independent-original",
        channel_id=original["channel_id"])
    if fault == "queued_turn":
        queued, _ = store.create_queued_turn(other["session_id"], client_turn_id="user-first",
            message="Retain this original request.", origin="web")
        path = store._turn_path(other["session_id"], queued["turn_id"])
    else:
        path = store._session_path(other["session_id"])
    saved, before = path.read_bytes(), capture.read_bytes()
    path.write_bytes(b"\xff")
    try:
        turns = pump_steward_monitors(runtime)
        assert len(turns) == 1 and turns[0]["session_id"] == session["session_id"]
        assert finish(runtime, turns[0])["status"] == "completed"
        assert path.read_bytes() == b"\xff" and capture.read_bytes() == before
        assert local_watch_row(runtime, actor, todo_id).get("last_checked_at")
    finally:
        path.write_bytes(saved)


@pytest.mark.parametrize("fault", ["not_due", "paused", "busy", "queued", "revoked", "stopped", "lifetime", "wrong_session", "not_watch"])
def test_local_native_watch_denials_keep_original_deadline_and_invoke_no_model(steward, tmp_path, monkeypatch, fault):
    import json
    from datetime import timedelta
    service, session, actor, todo_id, capture = local_watch(steward, tmp_path, monkeypatch)
    store, runtime, *_ = steward
    if fault in {"not_due", "paused", "wrong_session", "not_watch"}:
        changes = {"not_due": ["--next-due-at", (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()],
            "paused": ["--status", "blocked", "--clear-resume-when"],
            "wrong_session": ["--target-key", "managed-session-watch." + "0"*32],
            "not_watch": ["--action-kind", "different_source"]}[fault]
        edit_local_watch(runtime, actor, todo_id, *changes)
    elif fault in {"revoked", "stopped", "lifetime"}:
        data = json.loads(runtime.registry_path.read_text())
        goal = next(g for g in data["goals"] if g["id"] == actor["goal_id"])
        if fault == "revoked":
            goal["coordination"]["registered_agents"] = ["peer"]
        elif fault == "stopped":
            goal["status"] = "stopped"
        else:
            goal["goal_instance_id"] = "replacement"
        runtime.registry_path.write_text(json.dumps(data))
    elif fault == "busy":
        store.update_session(session["session_id"], active_turn_id="original-busy")
    else:
        store.create_queued_turn(session["session_id"], client_turn_id="user-first", message="Keep this first", origin="web")
    before, watch = capture.read_bytes(), local_watch_row(runtime, actor, todo_id)
    history = store.messages(session["session_id"])
    assert not pump_steward_monitors(runtime)
    assert capture.read_bytes() == before and local_watch_row(runtime, actor, todo_id) == watch
    assert store.messages(session["session_id"]) == history


@pytest.mark.parametrize("fault", ["pause", "revoke", "stop"])
def test_local_watch_rechecks_native_authority_after_collection_before_poll(steward, tmp_path, monkeypatch, fault):
    import json
    from loopx.capabilities.native_chat.steward_commands import native_command
    _, session, actor, todo_id, capture = local_watch(steward, tmp_path, monkeypatch)
    store, runtime, *_ = steward
    before, watch = capture.read_bytes(), local_watch_row(runtime, actor, todo_id)
    original_commit = StewardMonitor._commit_poll
    def changed(monitor, host, request, turn, context, argv):
        if fault == "pause":
            native_command(runtime, ["todo", "update", "--goal-id", actor["goal_id"],
                "--todo-id", todo_id, "--agent-id", actor["agent_id"], "--update-operation-id", "pause-original-watch",
                "--role", "agent", "--reason", "Owner paused the original native watch.",
                "--status", "blocked", "--clear-resume-when", "--execute"])
        else:
            data = json.loads(runtime.registry_path.read_text())
            goal = next(g for g in data["goals"] if g["id"] == actor["goal_id"])
            if fault == "revoke":
                goal["coordination"]["registered_agents"] = ["peer"]
            else:
                goal["status"] = "stopped"
            runtime.registry_path.write_text(json.dumps(data))
        return original_commit(monitor, host, request, turn, context, argv)
    monkeypatch.setattr(StewardMonitor, "_commit_poll", changed)
    turns = pump_steward_monitors(runtime)
    assert len(turns) == 1
    result = finish(runtime, turns[0])
    assert result["status"] == "failed", result
    after = local_watch_row(runtime, actor, todo_id)
    for key in ["result_hash", "last_checked_at", "next_due_at", "material_change_generation"]:
        assert after.get(key) == watch.get(key)
    assert not [event for event in store.events_after(session["session_id"], result["turn_id"], None)
        if event["kind"] == "native_loopx.monitor_poll"]
    assert capture.read_bytes() == before


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


def stopped_watch(steward, tmp_path, monkeypatch, provider, audience):
    store, runtime, _, transport, _, capture, _ = steward
    if audience == "managed":
        _, session, actor, todo_id, _ = local_watch(steward, tmp_path, monkeypatch, provider)
        sid = session["session_id"]
    else:
        sid, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
        todo_id = monitor.current(actor)["todo_id"]
    transport.reconcile()  # deliver setup's original request, before the watch
    # A crash after acceptance, before an observation or native poll. The
    # original failed request remains immutable; only the host pump may recover.
    with monkeypatch.context() as stopped_worker:
        stopped_worker.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: None)
        original_turn = pump_steward_monitors(runtime)[0]
    store.update_turn(sid, original_turn["turn_id"], status="failed", error_code="server_restarted",
        error="The original host stopped before observation.", response=None, upstream_turn_id=None)
    store.append_event(sid, original_turn["turn_id"], kind="turn.failed", payload={"error_code": "server_restarted"})
    store.update_session(sid, active_turn_id=None, status="ready")
    return sid, actor, todo_id, original_turn


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("audience", ["steward", "managed"])
@pytest.mark.parametrize("released_original_lease", [False, True])
def test_restart_before_observation_recovers_the_original_due_watch_once(steward, tmp_path, monkeypatch, provider, audience, released_original_lease):
    import json
    store, runtime, _, transport, _, capture, _ = steward
    sid, actor, todo_id, original_turn = stopped_watch(steward, tmp_path, monkeypatch, provider, audience)
    calls, writes = capture.read_bytes(), list(steward[2].writes)
    used = spent_slots(StewardMonitor(runtime), actor)
    if released_original_lease:
        monitor = StewardMonitor(runtime)
        watch = local_watch_row(runtime, actor, todo_id)
        old_lease = monitor._acquire(actor, watch, original_turn["loopx_request"]["turn_instance_id"])["lease"]
        monitor._release(actor, watch, old_lease)
    retained = store.load_turn(sid, original_turn["turn_id"])
    watch = local_watch_row(runtime, actor, todo_id)
    runtime._steward_monitor_next_check.clear()
    recovered = pump_steward_monitors(runtime)
    assert len(recovered) == 1, "a proven pre-observation restart must not strand a due watch"
    assert recovered[0]["turn_id"] != retained["turn_id"]
    assert recovered[0]["loopx_request"]["turn_instance_id"] == retained["loopx_request"]["turn_instance_id"]
    completed = finish(runtime, recovered[0])
    assert completed["status"] == "completed", completed
    assert completed["response"]["native_loopx"]["status"] == "observed"
    after = local_watch_row(runtime, actor, todo_id)
    assert after["next_due_at"] != watch["next_due_at"] and after.get("result_hash")
    assert store.load_turn(sid, retained["turn_id"]) == retained
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    transport.reconcile()
    assert steward[2].writes == writes and capture.read_bytes() == calls
    assert spent_slots(StewardMonitor(runtime), actor) == used
    # Native once-only semantics, not just the ephemeral pump cache.
    rows = [json.loads(line) for line in (runtime.coordination_runtime_root / "goals" / actor["goal_id"] / "runs" / "index.jsonl").read_text().splitlines()]
    assert len([row for row in rows if row.get("classification") == "quota_monitor_poll"
        and row.get("turn_instance_id") == retained["loopx_request"]["turn_instance_id"]]) == 1
    wait_for_next_observation_tick(after)
    edit_local_watch(runtime, actor, todo_id, "--next-due-at", (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat())
    following = pump_steward_monitors(runtime)
    assert len(following) == 1
    next_observation = finish(runtime, following[0])
    assert next_observation["status"] == "completed", next_observation
    assert "monitor_restart_recovery" not in following[0]["loopx_request"]
    assert store.load_turn(sid, retained["turn_id"]) == retained and capture.read_bytes() == calls
    assert spent_slots(StewardMonitor(runtime), actor) == used


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("audience", ["steward", "managed"])
@pytest.mark.parametrize("fault", ["unknown_error", "native_started", "delta", "unknown_event", "broken_history",
    "pending_poll", "legacy_poll", "queued", "worker_busy", "active_lease", "retry_fails"])
def test_stopped_watch_recovery_preserves_unknown_effects_user_priority_and_one_attempt(steward, tmp_path, monkeypatch, provider, audience, fault):
    from contextlib import nullcontext
    from loopx.control_plane.quota.monitor_poll import _monitor_poll_effect_id, _monitor_poll_receipt_path
    from loopx.file_lock import exclusive_file_lock
    store, runtime, _, transport, _, capture, _ = steward
    sid, actor, todo_id, original_turn = stopped_watch(steward, tmp_path, monkeypatch, provider, audience)
    request = original_turn["loopx_request"]
    root = runtime.coordination_runtime_root
    if fault in {"unknown_error", "native_started", "delta"}:
        changes = {"unknown_error": {"error_code": "runtime_error"},
            "native_started": {"upstream_turn_id": "unknown-native-turn"}, "delta": {"delta_count": 1}}[fault]
        store.update_turn(sid, original_turn["turn_id"], **changes)
    elif fault == "unknown_event":
        store.append_event(sid, original_turn["turn_id"], kind="native_loopx.monitor_poll", payload={"unknown": True})
    elif fault == "broken_history":
        index = root / "goals" / actor["goal_id"] / "runs" / "index.jsonl"
        index.parent.mkdir(parents=True, exist_ok=True)
        with index.open("ab") as output:
            output.write(b'\n{"unknown":')
    elif fault in {"pending_poll", "legacy_poll"}:
        effect = _monitor_poll_effect_id(runtime_root=root, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
            turn_instance_id=request["turn_instance_id"], todo_id=None if fault == "legacy_poll" else todo_id,
            target_key=None, goal_ref=None)
        receipt = _monitor_poll_receipt_path(root, actor["goal_id"], effect)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text('{"status":"provider_pending"}')  # even incomplete evidence must hold
    elif fault == "queued":
        store.create_queued_turn(sid, client_turn_id="new-user-work", message="Handle my current request first.", origin="web")
    elif fault == "active_lease":
        monitor = StewardMonitor(runtime)
        monitor._acquire(actor, local_watch_row(runtime, actor, todo_id), "independent-watch-owner")
    elif fault == "retry_fails":
        def unavailable(*_args, **_kwargs):
            raise OSError("Synthetic source failed before the new poll.")
        monkeypatch.setattr(StewardMonitor, "_commit_poll", unavailable)
    original = store.load_turn(sid, original_turn["turn_id"])
    watch, calls, writes = local_watch_row(runtime, actor, todo_id), capture.read_bytes(), list(steward[2].writes)
    runtime._steward_monitor_next_check.clear()
    guard = (exclusive_file_lock(store.turn_execution_lock_path(sid, original_turn["turn_id"]), timeout_seconds=0)
        if fault == "worker_busy" else nullcontext())
    with guard:
        recovered = pump_steward_monitors(runtime)
    if fault == "retry_fails":
        assert len(recovered) == 1 and finish(runtime, recovered[0])["status"] == "failed"
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)
    else:
        assert recovered == []
    assert store.load_turn(sid, original_turn["turn_id"]) == original
    assert local_watch_row(runtime, actor, todo_id) == watch
    transport.reconcile()
    assert capture.read_bytes() == calls and steward[2].writes == writes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("audience", ["steward", "managed"])
def test_reopened_controller_recovers_only_the_persisted_untouched_watch(steward, tmp_path, monkeypatch, provider, audience):
    from loopx.chat_store import ChatSessionStore
    from loopx.chat_runtime import ChatRuntimeController
    store, runtime, _, _, _, capture, _ = steward
    sid, actor, todo_id, original = stopped_watch(steward, tmp_path, monkeypatch, provider, audience)
    before, retained = capture.read_bytes(), store.load_turn(sid, original["turn_id"])
    runtime.close()
    reopened = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=runtime.codex_bin,
        registry_path=runtime.registry_path, project_contexts=runtime.project_contexts)
    reopened.manager_scope_resolver = runtime.manager_scope_resolver
    try:
        turns = pump_steward_monitors(reopened)
        assert len(turns) == 1 and finish(reopened, turns[0])["status"] == "completed"
        assert local_watch_row(reopened, actor, todo_id).get("last_checked_at")
        assert reopened.store.load_turn(sid, original["turn_id"]) == retained
        assert not pump_steward_monitors(reopened) and capture.read_bytes() == before
    finally:
        reopened.close()


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


def wait_for_next_observation_tick(todo):
    """A new keyed effect needs a newer observation, not only another due ID."""
    from loopx.control_plane.runtime.time import now_utc
    if not todo.get("last_checked_at"):
        return
    checked = datetime.fromisoformat(todo["last_checked_at"].replace("Z", "+00:00"))
    limit = time.monotonic() + 2
    while now_utc() <= checked:
        assert time.monotonic() < limit, "the native observation clock did not advance"
        time.sleep(.02)


def due_window(monitor, actor, deadline="2001-01-01T00:00:00Z"):
    """Advance a synthetic window through the real native Todo/lease owners.

    No sleep for 30 minutes, fake scheduler answer or raw authority edit.
    """
    from uuid import uuid4
    from loopx.capabilities.native_chat.steward_commands import native_command
    todo = monitor.current(actor)
    wait_for_next_observation_tick(todo)
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
def test_due_monitor_observes_without_selecting_it_over_open_advancement(steward, tmp_path, monkeypatch, provider):
    from loopx.capabilities.native_chat.steward_commands import native_command

    _, runtime, _, _, _, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
    added = native_command(runtime, ["todo", "add", "--goal-id", actor["goal_id"],
        "--role", "agent", "--text", "[P1] Retain this independently executable work.",
        "--claimed-by", actor["agent_id"], "--action-kind", "inspect",
        "--continuation-policy", "same_agent_non_delivery", "--execute"])
    work_id = added.get("todo_id") or added["todo"]["todo_id"]
    before, used = capture.read_bytes(), spent_slots(monitor, actor)
    accepted = pump_steward_monitors(runtime)
    assert len(accepted) == 1
    done = finish(runtime, accepted[0])
    assert done["status"] == "completed", done
    assert done["response"]["native_loopx"]["status"] == "observed"
    assert monitor.current(actor).get("result_hash")
    assert next(row for row in monitor.todos(actor) if row["todo_id"] == work_id)["status"] == "open"
    assert spent_slots(monitor, actor) == used and capture.read_bytes() == before
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", [None, "different_error", "native_started", "delta", "interrupted", "retry_fails"])
def test_original_monitor_recovers_only_the_known_pre_poll_rejection_once(steward, tmp_path, monkeypatch, provider, fault):
    import copy
    from loopx.capabilities.native_chat.steward_commands import native_command

    store, runtime, _, _, _, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
    native_command(runtime, ["todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
        "--text", "[P1] Keep this independent work open.", "--claimed-by", actor["agent_id"],
        "--continuation-policy", "same_agent_non_delivery", "--execute"])
    observe = StewardMonitor.observe

    def legacy(self, *, host, request, turn, context, todo):
        return native_command(runtime, ["quota", "should-run", "--goal-id", actor["goal_id"],
            "--agent-id", actor["agent_id"], "--todo-id", todo["todo_id"],
            "--turn-instance-id", request["turn_instance_id"], "--runtime-profile", "codex_app_server",
            "--available-capability", "filesystem", "--available-capability", "external_evidence_poll"])

    monkeypatch.setattr(StewardMonitor, "observe", legacy)
    calls, used = capture.read_bytes(), spent_slots(monitor, actor)
    first = pump_steward_monitors(runtime)[0]
    failed = finish(runtime, first)
    assert failed["status"] == "failed" and "auxiliary_monitor_not_selectable" in failed["error"]
    if fault and fault != "retry_fails":
        change = {"different_error": {"error": "An unknown observation failed."},
            "native_started": {"upstream_turn_id": "unknown-native-turn"},
            "delta": {"delta_count": 1}, "interrupted": {"status": "interrupted"}}[fault]
        store.update_turn(first["session_id"], first["turn_id"], **change)
    retained = copy.deepcopy(store.load_turn(first["session_id"], first["turn_id"]))
    monkeypatch.setattr(StewardMonitor, "observe", legacy if fault == "retry_fails" else observe)
    runtime._steward_monitor_next_check.clear()
    recovered = pump_steward_monitors(runtime)
    if fault and fault != "retry_fails":
        assert recovered == []
    else:
        assert len(recovered) == 1 and recovered[0]["turn_id"] != failed["turn_id"]
        assert recovered[0]["loopx_request"]["turn_instance_id"] == failed["loopx_request"]["turn_instance_id"]
        done = finish(runtime, recovered[0])
        if fault == "retry_fails":
            assert done["status"] == "failed"
        else:
            assert done["response"]["native_loopx"]["status"] == "observed"
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert store.load_turn(first["session_id"], first["turn_id"]) == retained
    assert spent_slots(monitor, actor) == used and capture.read_bytes() == calls


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
    acquire, replaced = module.StewardMonitor._acquire, []
    def change_after_collection(self, actor, todo, key, *, expected_version=None):
        if expected_version is not None and not replaced:
            replaced.append(True)
            for operation in ["pause", "enable"]:
                plan = monitor.configure(binding["binding_id"], operation=operation)["plan"]
                monitor.configure(binding["binding_id"], operation=operation, execute=True, expected_plan_id=plan["plan_id"])
            # Even an explicit owner restoring the same timestamp cannot
            # revive the old execution proof or its previously collected data.
            due_window(monitor, actor, before["next_due_at"])
        return acquire(self, actor, todo, key, expected_version=expected_version)
    monkeypatch.setattr(module.StewardMonitor, "_acquire", change_after_collection)
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
