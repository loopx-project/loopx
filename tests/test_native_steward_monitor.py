"""Actual native Todo/deadline/Chat worker/poll path; no model is needed to observe."""
from datetime import datetime, timedelta, timezone
import json
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


def stop_watch_goal(runtime, actor):
    from loopx.control_plane.goals.activation_service import set_goal_activation_state
    stopped = set_goal_activation_state(registry_path=runtime.registry_path, goal_id=actor["goal_id"],
        state="stopped", reason="Original owner stopped this synthetic Goal.",
        runtime_root_override=str(runtime.coordination_runtime_root), actor_kind="owner", execute=True)
    assert stopped["ok"] and stopped["written"] and stopped["readback"]["verified"], stopped
    return stopped


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
    elif fault in {"revoked", "lifetime"}:
        data = json.loads(runtime.registry_path.read_text())
        goal = next(g for g in data["goals"] if g["id"] == actor["goal_id"])
        if fault == "revoked":
            goal["coordination"]["registered_agents"] = ["peer"]
        else:
            goal["goal_instance_id"] = "replacement"
        runtime.registry_path.write_text(json.dumps(data))
    elif fault == "busy":
        store.update_session(session["session_id"], active_turn_id="original-busy")
    elif fault == "queued":
        store.create_queued_turn(session["session_id"], client_turn_id="user-first", message="Keep this first", origin="web")
    before, watch = capture.read_bytes(), local_watch_row(runtime, actor, todo_id)
    history = store.messages(session["session_id"])
    if fault == "stopped":
        stop_watch_goal(runtime, actor)
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
    stop_receipt = None
    def changed(monitor, host, request, turn, context, argv):
        nonlocal stop_receipt
        if fault == "pause":
            native_command(runtime, ["todo", "update", "--goal-id", actor["goal_id"],
                "--todo-id", todo_id, "--agent-id", actor["agent_id"], "--update-operation-id", "pause-original-watch",
                "--role", "agent", "--reason", "Owner paused the original native watch.",
                "--status", "blocked", "--clear-resume-when", "--execute"])
        elif fault == "stop":
            stop_receipt = stop_watch_goal(runtime, actor)
        else:
            data = json.loads(runtime.registry_path.read_text())
            goal = next(g for g in data["goals"] if g["id"] == actor["goal_id"])
            goal["coordination"]["registered_agents"] = ["peer"]
            runtime.registry_path.write_text(json.dumps(data))
        return original_commit(monitor, host, request, turn, context, argv)
    monkeypatch.setattr(StewardMonitor, "_commit_poll", changed)
    turns = pump_steward_monitors(runtime)
    assert len(turns) == 1
    result = finish(runtime, turns[0])
    assert result["status"] == "failed", result
    if fault == "stop":
        assert stop_receipt is not None, result
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("entry", ["resume", "adapter", "http"])
def test_session_resume_preserves_live_model_free_watch_after_client_restart(steward, tmp_path, monkeypatch, provider, entry):
    import threading
    import loopx.chat_manager_context as contexts

    store, runtime, _, _, _, capture, _ = steward
    foreground_id, _, _ = prepared(steward, tmp_path, monkeypatch, provider)
    # A restored host can run a model-free watch before ever opening Codex.
    runtime.adapters.pop(foreground_id).close_session()
    entered, released = threading.Event(), threading.Event()
    collect = contexts.collect_manager_turn_context

    def slow_collection(*args, **kwargs):
        entered.set()
        assert released.wait(10), "portfolio fixture was not released"
        return collect(*args, **kwargs)

    monkeypatch.setattr(contexts, "collect_manager_turn_context", slow_collection)
    calls = capture.read_bytes()
    turn = pump_steward_monitors(runtime)[0]
    sid = turn["session_id"]
    assert sid != foreground_id
    server, result = None, None
    try:
        assert entered.wait(10)
        original = store.load_turn(sid, turn["turn_id"])
        assert original["status"] == "starting"
        if entry == "resume":
            restored = runtime.resume_session(session_id=sid, work_dir=tmp_path, objective="Open the existing conversation")
        elif entry == "adapter":
            with pytest.raises(ValueError, match="worker is still active"):
                runtime._ensure_adapter(store.load_session(sid), work_dir=tmp_path, objective="Open the existing conversation")
            restored = store.load_session(sid)
        else:
            import http.client
            import json
            from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

            server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
            server.runtime_controller, server.chat_store, server.verbose = runtime, store, False
            threading.Thread(target=server.serve_forever, daemon=True).start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request("POST", f"/api/chat/sessions/{sid}/resume", "{}",
                    headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                payload = json.loads(response.read())
                assert response.status == 200, payload
                restored = payload["session"]
            finally:
                connection.close()
        assert restored["active_turn_id"] == turn["turn_id"]
        # The live event buffer may publish phase timing while HTTP is open.
        # Resume must retain the operation/state, not freeze its progress.
        timing = {"first_event_at", "last_activity_at"}
        assert {k: v for k, v in store.load_turn(sid, turn["turn_id"]).items() if k not in timing} == {
            k: v for k, v in original.items() if k not in timing}
        assert capture.read_bytes() == calls  # Opening history must not start or resume an unrelated model client.
    finally:
        released.set()
        if server is not None:
            try:
                result = finish(runtime, turn)
            finally:
                # ChatHTTPServer owns its controller. Closing it while the
                # watch is still collecting would test an intentional stop.
                server.shutdown()
                server.server_close()
    result = result or finish(runtime, turn)
    assert result["status"] == "completed", result
    assert result["response"]["native_loopx"]["status"] == "observed"
    assert not any(row["kind"] == "turn.failed" for row in store.events_after(sid, turn["turn_id"], None))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["pause", "binding_scope"])
def test_monitor_identity_io_leaves_owner_changes_available_and_rechecks_them(steward, tmp_path, monkeypatch, provider, change):
    import threading
    from loopx.file_lock import exclusive_file_lock

    store, runtime, identity, transport, binding, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
    before, calls = monitor.current(actor), capture.read_bytes()
    entered, released = threading.Event(), threading.Event()
    commit, observe, committing = StewardMonitor._commit_poll, transport.bindings.observe, []

    def at_commit(self, *args):
        committing.append(threading.get_ident())
        return commit(self, *args)

    def blocked_observation(*args, **kwargs):
        if threading.get_ident() in committing:
            entered.set()
            assert released.wait(10), "monitor identity fixture was not released"
        return observe(*args, **kwargs)

    monkeypatch.setattr(StewardMonitor, "_commit_poll", at_commit)
    monkeypatch.setattr(transport.bindings, "observe", blocked_observation)
    turn = pump_steward_monitors(runtime)[0]
    available = False
    try:
        assert entered.wait(10)
        available = runtime.lock.acquire(timeout=.5)
        assert available, "provider verification prevented the owner from changing the monitor"
        with exclusive_file_lock(transport.bindings.path, timeout_seconds=.5):
            pass
        if change == "pause":
            plan = monitor.configure(binding["binding_id"], operation="pause")["plan"]
            monitor.configure(binding["binding_id"], operation="pause", expected_plan_id=plan["plan_id"], execute=True)
        else:
            transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
                executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
    finally:
        if available:
            runtime.lock.release()
        else:
            identity.owner_ready = False
        released.set()
    result = finish(runtime, turn)
    assert result["status"] == "failed", result
    current = monitor.current(actor)
    assert current.get("result_hash") == before.get("result_hash")
    assert current["next_due_at"] == before["next_due_at"]
    assert not any(row["kind"] == "native_loopx.monitor_poll" for row in store.events_after(turn["session_id"], turn["turn_id"], None))
    assert capture.read_bytes() == calls


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
    assert (original_turn["session_id"] == sid) == (audience == "managed")
    interrupt_unstarted_watch(store, original_turn)
    return sid, actor, todo_id, original_turn


def interrupt_unstarted_watch(store, turn):
    sid = turn["session_id"]
    store.update_turn(sid, turn["turn_id"], status="failed", error_code="server_restarted",
        error="The original host stopped before observation.", response=None, upstream_turn_id=None)
    store.append_event(sid, turn["turn_id"], kind="turn.failed", payload={"error_code": "server_restarted"})
    store.update_session(sid, active_turn_id=None, status="ready")


def append_watch_event_before_failure(store, turn, kind, payload):
    session_id, turn_id = turn["session_id"], turn["turn_id"]
    journal = store._event_path(session_id, turn_id)
    prefix, terminal = journal.read_bytes().rstrip(b"\n").rsplit(b"\n", 1)
    failed_event = json.loads(terminal)
    assert failed_event["kind"] == "turn.failed"
    journal.write_bytes(prefix + b"\n")
    store.append_event(session_id, turn_id, kind=kind, payload=payload)
    store.append_event(session_id, turn_id, kind="turn.failed", payload=failed_event["payload"])


def scope_refused_watch(steward, tmp_path, monkeypatch, provider):
    import loopx.chat_manager_context as context_owner
    _, runtime, _, transport, *_ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
    transport.reconcile()
    def changed_snapshot(*_args, **_kwargs):
        # Inject the collector's typed scope refusal into the real watch worker.
        return context_owner.unavailable_manager_context("external_authorization_changed")

    with monkeypatch.context() as changed:
        changed.setattr(context_owner, "collect_manager_turn_context", changed_snapshot)
        original_turn = pump_steward_monitors(runtime)[0]
        failed = finish(runtime, original_turn)
    assert failed["status"] == "failed"
    assert failed["error"] == "the portfolio observation omitted authorized Goals"
    return original_turn["session_id"], actor, monitor.current(actor)["todo_id"], failed


def identity_refused_watch(steward, tmp_path, monkeypatch, provider):
    import loopx.chat_manager_context as context_owner
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations

    store, runtime, source, transport, *_ = steward
    foreground_id, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
    transport.reconcile()

    def fail_during_collection(*_args, **_kwargs):
        # Reproduce an identity refusal before the collector publishes context.
        source.bot_ready = False
        return ChatExternalConversations(runtime).commission_evidence(store.load_session(foreground_id))

    try:
        with monkeypatch.context() as changed:
            changed.setattr(context_owner, "collect_manager_turn_context", fail_during_collection)
            original_turn = pump_steward_monitors(runtime)[0]
            failed = finish(runtime, original_turn)
    finally:
        source.bot_ready = True
    assert failed["status"] == "failed"
    assert failed["error"] == "the selected App identity could not be verified"
    sid = original_turn["session_id"]
    events = store.events_after(sid, failed["turn_id"], None)
    assert [row["kind"] for row in events] == ["turn.queued", "agent.phase", "turn.failed"]
    return sid, actor, monitor.current(actor)["todo_id"], failed


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("failure", ["authorization", "identity"])
def test_refused_watch_is_automatically_reobserved_once_after_host_reopen(steward, tmp_path, monkeypatch, provider, failure):
    import json
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    store, runtime, _, transport, _, capture, _ = steward
    refused = scope_refused_watch if failure == "authorization" else identity_refused_watch
    sid, actor, todo_id, failed = refused(steward, tmp_path, monkeypatch, provider)
    retained = store._turn_path(sid, failed["turn_id"]).read_bytes()
    before = local_watch_row(runtime, actor, todo_id)
    calls, writes, used = capture.read_bytes(), list(steward[2].writes), spent_slots(StewardMonitor(runtime), actor)
    runtime.close()
    reopened = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=runtime.codex_bin,
        registry_path=runtime.registry_path, project_contexts=runtime.project_contexts)
    reopened.manager_scope_resolver = runtime.manager_scope_resolver
    wake = DelegationWakeService(reopened, interval=.02).start()
    try:
        deadline = time.monotonic() + 15
        while local_watch_row(reopened, actor, todo_id).get("last_checked_at") == before.get("last_checked_at"):
            assert time.monotonic() < deadline, "the existing host must continue the refused observation"
            time.sleep(.02)
        after = local_watch_row(reopened, actor, todo_id)
        wake.close()
        turns = [json.loads(path.read_text()) for path in (reopened.store._session_dir(sid) / "turns").glob("*.json")]
        turns = [row for row in turns if (row.get("loopx_request") or {}).get("monitor_restart_recovery")]
        assert len(turns) == 1
        done = finish(reopened, turns[0])
        assert done["status"] == "completed" and done["response"]["native_loopx"]["status"] == "observed"
        assert done["loopx_request"]["turn_instance_id"] == failed["loopx_request"]["turn_instance_id"]
        assert done["loopx_request"]["monitor_deadline"] == before["next_due_at"]
        assert after["next_due_at"] != before["next_due_at"] and after.get("result_hash")
        assert reopened.store._turn_path(sid, failed["turn_id"]).read_bytes() == retained
        reopened._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(reopened)
        rows = [json.loads(line) for line in (reopened.coordination_runtime_root / "goals" / actor["goal_id"] / "runs" / "index.jsonl").read_text().splitlines()]
        assert len([row for row in rows if row.get("classification") == "quota_monitor_poll"
            and row.get("turn_instance_id") == failed["loopx_request"]["turn_instance_id"]]) == 1
        assert capture.read_bytes() == calls and steward[2].writes == writes
        assert spent_slots(StewardMonitor(reopened), actor) == used
        wait_for_next_observation_tick(after)
        edit_local_watch(reopened, actor, todo_id, "--next-due-at", (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat())
        next_turns = pump_steward_monitors(reopened)
        assert len(next_turns) == 1 and finish(reopened, next_turns[0])["status"] == "completed"
        assert "monitor_restart_recovery" not in next_turns[0]["loopx_request"]
        assert capture.read_bytes() == calls and spent_slots(StewardMonitor(reopened), actor) == used
    finally:
        wake.close()
        reopened.close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["missing_phase", "different_phase", "duplicate_phase", "context_present",
    "different_error", "provider_unavailable", "retry_fails"])
def test_identity_refusal_requires_original_collector_evidence_and_current_provider(steward, tmp_path, monkeypatch, provider, fault):
    import json
    store, runtime, source, _, _, capture, _ = steward
    sid, actor, todo_id, failed = identity_refused_watch(steward, tmp_path, monkeypatch, provider)
    path = store._event_path(sid, failed["turn_id"])
    if fault in {"missing_phase", "different_phase", "duplicate_phase"}:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        phase = next(row for row in rows if row["kind"] == "agent.phase")
        if fault == "missing_phase":
            rows.remove(phase)
        elif fault == "different_phase":
            phase["payload"]["phase"] = "model_start"
        else:
            rows.append(phase)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    elif fault == "context_present":
        store.append_event(sid, failed["turn_id"], kind="manager.context", payload={"unknown": True})
    elif fault == "different_error":
        store.update_turn(sid, failed["turn_id"], error="Unknown failure before context.")
    elif fault == "provider_unavailable":
        source.bot_ready = False
    elif fault == "retry_fails":
        def unavailable(*_args, **_kwargs):
            raise OSError("Source unavailable in the one continuation.")
        monkeypatch.setattr(StewardMonitor, "_commit_poll", unavailable)
    retained = store._turn_path(sid, failed["turn_id"]).read_bytes()
    watch, calls, writes = local_watch_row(runtime, actor, todo_id), capture.read_bytes(), list(source.writes)
    runtime._steward_monitor_next_check.clear()
    continued = pump_steward_monitors(runtime)
    if fault == "retry_fails":
        assert len(continued) == 1 and finish(runtime, continued[0])["status"] == "failed"
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)
    else:
        assert continued == []
    assert store._turn_path(sid, failed["turn_id"]).read_bytes() == retained
    assert local_watch_row(runtime, actor, todo_id) == watch
    assert capture.read_bytes() == calls and source.writes == writes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["missing_context", "different_context", "duplicate_context", "different_error",
    "model_started", "poll_receipt", "broken_history", "worker_busy", "queued", "paused", "revoked", "retry_fails"])
def test_scope_refusal_retains_unknown_effects_and_current_authority(steward, tmp_path, monkeypatch, provider, fault):
    from contextlib import nullcontext
    from loopx.control_plane.quota.monitor_poll import _monitor_poll_effect_id, _monitor_poll_receipt_path
    from loopx.file_lock import exclusive_file_lock
    store, runtime, _, transport, binding, capture, _ = steward
    sid, actor, todo_id, failed = scope_refused_watch(steward, tmp_path, monkeypatch, provider)
    path = store._event_path(sid, failed["turn_id"])
    if fault in {"missing_context", "different_context", "duplicate_context"}:
        import json
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        context = next(row for row in rows if row["kind"] == "manager.context")
        if fault == "missing_context":
            rows.remove(context)
        elif fault == "different_context":
            context["payload"]["warnings"] = ["source_unavailable"]
        else:
            rows.append(context)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    elif fault in {"different_error", "model_started"}:
        store.update_turn(sid, failed["turn_id"], **({"error": "Unknown observation failure."}
            if fault == "different_error" else {"upstream_turn_id": "unknown-model-turn"}))
    elif fault == "poll_receipt":
        request = failed["loopx_request"]
        effect = _monitor_poll_effect_id(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"],
            agent_id=actor["agent_id"], turn_instance_id=request["turn_instance_id"], todo_id=todo_id,
            target_key=None, goal_ref=None)
        receipt = _monitor_poll_receipt_path(runtime.coordination_runtime_root, actor["goal_id"], effect)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text('{"status":"provider_pending"}')
    elif fault == "broken_history":
        with path.open("ab") as output:
            output.write(b'\n{"unknown":')
    elif fault == "queued":
        store.create_queued_turn(sid, client_turn_id="scope-refusal-user-priority", message="Do my current task first.", origin="web")
    elif fault == "paused":
        edit_local_watch(runtime, actor, todo_id, "--status", "blocked")
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault == "retry_fails":
        def unavailable(*_args, **_kwargs):
            raise OSError("Source unavailable in the one continuation.")
        monkeypatch.setattr(StewardMonitor, "_commit_poll", unavailable)
    retained = store._turn_path(sid, failed["turn_id"]).read_bytes()
    watch, calls, writes = local_watch_row(runtime, actor, todo_id), capture.read_bytes(), list(steward[2].writes)
    runtime._steward_monitor_next_check.clear()
    guard = exclusive_file_lock(store.turn_execution_lock_path(sid, failed["turn_id"]), timeout_seconds=0) if fault == "worker_busy" else nullcontext()
    with guard:
        resumed = pump_steward_monitors(runtime)
    if fault == "retry_fails":
        assert len(resumed) == 1 and finish(runtime, resumed[0])["status"] == "failed"
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)
    else:
        assert resumed == []
    assert store._turn_path(sid, failed["turn_id"]).read_bytes() == retained
    assert local_watch_row(runtime, actor, todo_id) == watch
    assert capture.read_bytes() == calls and steward[2].writes == writes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("audience", ["steward", "managed"])
@pytest.mark.parametrize("released_original_lease", [False, True])
@pytest.mark.parametrize("stop_recovery", [False, True], ids=["single-restart", "repeated-restart"])
def test_restart_before_observation_recovers_the_original_due_watch_once(
    steward, tmp_path, monkeypatch, provider, audience, released_original_lease, stop_recovery,
):
    import json
    store, runtime, _, transport, _, capture, _ = steward
    _, actor, todo_id, original_turn = stopped_watch(steward, tmp_path, monkeypatch, provider, audience)
    sid = original_turn["session_id"]
    calls, writes = capture.read_bytes(), list(steward[2].writes)
    used = spent_slots(StewardMonitor(runtime), actor)
    if released_original_lease:
        monitor = StewardMonitor(runtime)
        watch = local_watch_row(runtime, actor, todo_id)
        old_lease = monitor._acquire(actor, watch, original_turn["loopx_request"]["turn_instance_id"])["lease"]
        monitor._release(actor, watch, old_lease)
    retained = store.load_turn(sid, original_turn["turn_id"])
    watch = local_watch_row(runtime, actor, todo_id)
    stopped_recovery = None
    if stop_recovery:
        runtime._steward_monitor_next_check.clear()
        with monkeypatch.context() as stopped_worker:
            stopped_worker.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: None)
            first_recovery, = pump_steward_monitors(runtime)
        interrupt_unstarted_watch(store, first_recovery)
        stopped_recovery = store.load_turn(sid, first_recovery["turn_id"])
        assert local_watch_row(runtime, actor, todo_id) == watch
    runtime._steward_monitor_next_check.clear()
    recovered = pump_steward_monitors(runtime)
    assert len(recovered) == 1, "a proven pre-observation restart must not strand a due watch"
    assert recovered[0]["turn_id"] != retained["turn_id"]
    if stopped_recovery is not None:
        assert recovered[0]["turn_id"] != stopped_recovery["turn_id"]
    assert recovered[0]["loopx_request"]["turn_instance_id"] == retained["loopx_request"]["turn_instance_id"]
    completed = finish(runtime, recovered[0])
    assert completed["status"] == "completed", completed
    assert completed["response"]["native_loopx"]["status"] == "observed"
    after = local_watch_row(runtime, actor, todo_id)
    assert after["next_due_at"] != watch["next_due_at"] and after.get("result_hash")
    assert store.load_turn(sid, retained["turn_id"]) == retained
    if stopped_recovery is not None:
        assert store.load_turn(sid, stopped_recovery["turn_id"]) == stopped_recovery
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


@pytest.mark.parametrize("journal_state", ["recorded_poll", "missing_poll", "retained_at_compaction"])
def test_typed_monitor_restart_requires_original_event_evidence(steward, tmp_path, monkeypatch, journal_state):
    store, runtime, _, _, _, capture, _ = steward
    _, actor, todo_id, original = stopped_watch(steward, tmp_path, monkeypatch, "sqlite", "steward")
    session_id, turn_id = original["session_id"], original["turn_id"]
    journal = store._event_path(session_id, turn_id)
    if journal_state == "retained_at_compaction":
        append_watch_event_before_failure(store, original, "agent.phase", {"phase": "manager_context"})
        store.update_turn(session_id, turn_id, completed_at="2000-01-01T00:00:00Z")
        original_bytes = journal.read_bytes()
        store.compact_completed_events(older_than_hours=24)
        assert journal.read_bytes() == original_bytes
    else:
        append_watch_event_before_failure(store, original, "native_loopx.monitor_poll", {})
        if journal_state == "missing_poll":
            prefix, poll, terminal = journal.read_bytes().rstrip(b"\n").rsplit(b"\n", 2)
            assert json.loads(poll)["kind"] == "native_loopx.monitor_poll"
            journal.write_bytes(prefix + b"\n" + terminal + b"\n")
            assert [json.loads(line)["sequence"] for line in journal.read_bytes().splitlines()] == [1, 3]

    retained = store.load_turn(session_id, turn_id)
    watch = local_watch_row(runtime, actor, todo_id)
    calls = capture.read_bytes()
    runtime._steward_monitor_next_check.clear()
    recovered = pump_steward_monitors(runtime)
    if journal_state == "retained_at_compaction":
        carrier, = recovered
        assert carrier["loopx_request"]["monitor_restart_recovery"]["original_turn_id"] == turn_id
        assert finish(runtime, carrier)["response"]["native_loopx"]["status"] == "observed"
    else:
        assert recovered == []
        assert local_watch_row(runtime, actor, todo_id) == watch
    assert store.load_turn(session_id, turn_id) == retained
    assert capture.read_bytes() == calls


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("audience", ["steward", "managed"])
@pytest.mark.parametrize("fault", ["unknown_error", "native_started", "delta", "unknown_event", "broken_history",
    "pending_poll", "legacy_poll", "queued", "worker_busy", "active_lease", "retry_fails", "recovery_native_started"])
def test_stopped_watch_recovery_preserves_unknown_effects_user_priority_and_one_attempt(steward, tmp_path, monkeypatch, provider, audience, fault):
    from contextlib import nullcontext
    from loopx.control_plane.quota.monitor_poll import _monitor_poll_effect_id, _monitor_poll_receipt_path
    from loopx.file_lock import exclusive_file_lock
    store, runtime, _, transport, _, capture, _ = steward
    foreground_sid, actor, todo_id, original_turn = stopped_watch(steward, tmp_path, monkeypatch, provider, audience)
    sid = original_turn["session_id"]
    request = original_turn["loopx_request"]
    root = runtime.coordination_runtime_root
    stopped_recovery = None
    queued_request = None
    if fault == "recovery_native_started":
        runtime._steward_monitor_next_check.clear()
        with monkeypatch.context() as stopped_worker:
            stopped_worker.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: None)
            first_recovery, = pump_steward_monitors(runtime)
        interrupt_unstarted_watch(store, first_recovery)
        stopped_recovery = store.update_turn(sid, first_recovery["turn_id"], upstream_turn_id="unknown-native-turn")
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
        queued_request, _ = store.create_queued_turn(foreground_sid, client_turn_id="new-user-work",
            message="Handle my current request first.", origin="web")
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
    background_recovered = fault == "queued" and audience == "steward"
    if background_recovered:
        assert len(recovered) == 1 and recovered[0]["session_id"] == sid
        assert finish(runtime, recovered[0])["status"] == "completed"
        assert local_watch_row(runtime, actor, todo_id)["next_due_at"] != watch["next_due_at"]
    elif fault == "retry_fails":
        assert len(recovered) == 1 and finish(runtime, recovered[0])["status"] == "failed"
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)
    else:
        assert recovered == []
    assert store.load_turn(sid, original_turn["turn_id"]) == original
    if not background_recovered:
        assert local_watch_row(runtime, actor, todo_id) == watch
    if queued_request is not None:
        assert store.queued_turns(foreground_sid) == [queued_request]
    if stopped_recovery is not None:
        assert store.load_turn(sid, stopped_recovery["turn_id"]) == stopped_recovery
    if background_recovered:
        # The owning App conversation still mirrors the queued user input.
        # The background observation must not add a provider message of its own.
        transport._web_projection_scan_at = 0
        writes.append(("steward-app", "**你（App）**\n\n" + queued_request["message"]))
    transport.reconcile()
    transport.reconcile()
    assert capture.read_bytes() == calls and steward[2].writes == writes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("audience", ["steward", "managed"])
def test_reopened_controller_recovers_only_the_persisted_untouched_watch(steward, tmp_path, monkeypatch, provider, audience):
    from loopx.chat_store import ChatSessionStore
    from loopx.chat_runtime import ChatRuntimeController
    store, runtime, _, _, _, capture, _ = steward
    _, actor, todo_id, original = stopped_watch(steward, tmp_path, monkeypatch, provider, audience)
    sid = original["session_id"]
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
    from loopx.capabilities.native_chat.steward_monitor import steward_background_session_id

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
        assert len(turns) == 1 and turns[0]["session_id"] == steward_background_session_id(other["binding_id"])
        completed = finish(runtime, turns[0])
        assert completed["status"] == "completed", completed
        assert completed["response"]["native_loopx"]["monitor"]["result_hash"] == monitor.current(other_actor)["result_hash"]
        assert monitor.current(actor) == retained and path.read_bytes() == b"\xff"
        assert capture.read_bytes() == calls
    finally:
        path.write_bytes(original_bytes)
    recovered, = pump_steward_monitors(runtime)
    assert recovered["session_id"] == steward_background_session_id(binding["binding_id"])
    assert finish(runtime, recovered)["status"] == "completed"
    if unreadable == "queued_turn":
        assert store.queued_turns(sid)[0]["turn_id"] == queued["turn_id"]
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
@pytest.mark.parametrize("fault", [
    None, "different_error", "native_started", "delta", "interrupted", "retry_fails",
    "recorded_poll", "damaged_poll", "missing_poll", "missing_journal",
])
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
    turn_changes = {
        "different_error": {"error": "An unknown observation failed."},
        "native_started": {"upstream_turn_id": "unknown-native-turn"},
        "delta": {"delta_count": 1},
        "interrupted": {"status": "interrupted"},
    }
    session_id, turn_id = first["session_id"], first["turn_id"]
    if fault in turn_changes:
        store.update_turn(session_id, turn_id, **turn_changes[fault])
    if fault in {"recorded_poll", "damaged_poll", "missing_poll", "missing_journal"}:
        journal = store._event_path(session_id, turn_id)
        if fault == "missing_journal":
            journal.unlink()
        else:
            append_watch_event_before_failure(store, first, "native_loopx.monitor_poll", {})
            if fault in {"damaged_poll", "missing_poll"}:
                prefix, poll, terminal = journal.read_bytes().rstrip(b"\n").rsplit(b"\n", 2)
                assert json.loads(poll)["kind"] == "native_loopx.monitor_poll"
                malformed = poll[:-1] + b"\n" if fault == "damaged_poll" else b""
                journal.write_bytes(prefix + b"\n" + malformed + terminal + b"\n")
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
    from loopx.capabilities.native_chat.steward_monitor import steward_background_session_id
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    store, runtime, _, _, binding, capture, _ = steward
    foreground_id, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
    foreground_messages = store.messages(foreground_id)
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
        background_id = steward_background_session_id(binding["binding_id"])
        assert turns[0]["session_id"] == background_id
        assert not pump_steward_monitors(restarted) and capture.read_bytes() == calls
        assert monitor.current(actor)["todo_id"] == retained["todo_id"]
        assert {row["session_id"] for row in restarted.store.list_sessions()} == {foreground_id, background_id}
        assert restarted.store.messages(foreground_id) == foreground_messages
    finally:
        restarted.close()


def test_native_goal_stop_blocks_observation_and_owner_can_still_pause_the_monitor(steward, tmp_path, monkeypatch):
    _, runtime, _, transport, binding, capture, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls = monitor.current(actor), capture.read_bytes()
    stop_watch_goal(runtime, actor)
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
def test_due_background_watch_preserves_original_user_work(steward, tmp_path, monkeypatch, busy):
    store, runtime, _, _, _, capture, _ = steward
    sid, actor, monitor = prepared(steward, tmp_path, monkeypatch)
    before, calls = monitor.current(actor), capture.read_bytes()
    if busy == "active":
        store.update_session(sid, active_turn_id="original-user-work")
    else:
        queued, _ = store.create_queued_turn(
            sid, client_turn_id="original-followup", message="Handle me first", origin="web",
        )
    turn, = pump_steward_monitors(runtime)
    assert turn["session_id"] != sid
    completed = finish(runtime, turn)
    assert completed["status"] == "completed"
    assert completed["response"]["native_loopx"]["status"] == "observed"
    assert monitor.current(actor)["next_due_at"] != before["next_due_at"]
    if busy == "active":
        assert store.load_session(sid)["active_turn_id"] == "original-user-work"
    else:
        assert store.queued_turns(sid)[0]["turn_id"] == queued["turn_id"]
    assert capture.read_bytes() == calls


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


@pytest.mark.parametrize("scope", ["repository-only", "combined", "rejected-poll", "lost-commit-reply"])
def test_repository_unavailability_preserves_poll_and_recovery_scope(
    steward, tmp_path, monkeypatch, scope,
):
    from test_steward_generic_monitor_todo import _set_signal_source_policy
    from loopx.capabilities.native_chat.steward_commands import native_command
    from loopx.capabilities.native_chat.steward_monitor_turns import (
        steward_monitor_snapshot_path,
        write_steward_monitor_scope,
    )

    store, runtime, _, transport, binding, _, _ = steward
    sid, actor, monitor = prepared(steward, tmp_path, monkeypatch, "sqlite")
    session = store.load_session(sid)
    _set_signal_source_policy(runtime, session, binding, goal_id=actor["goal_id"], agent_id=actor["agent_id"])

    # Push the default portfolio watch into the future so our two custom monitors control due ordering.
    default_watch = monitor.current(actor)
    edit_local_watch(
        runtime,
        actor,
        default_watch["todo_id"],
        "--next-due-at",
        (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )

    target = "steward-stalled-pr-monitor"
    first_due = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    second_due = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    added_first = native_command(
        runtime,
        [
            "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
            "--text", "Monitor open PR signals",
            "--claimed-by", actor["agent_id"],
            "--task-class", "continuous_monitor",
            "--action-kind", "local_runtime_observe",
            "--continuation-policy", "same_agent_non_delivery",
            "--required-capability", "filesystem",
            "--target-key", target,
            "--task-repository", "git:github.com/org/repo",
            "--watch-only",
            "--cadence", "30m",
            "--next-due-at", first_due,
            "--execute",
        ],
    )
    added_second = native_command(
        runtime,
        [
            "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
            "--text", "Monitor secondary inbox state",
            "--claimed-by", actor["agent_id"],
            "--task-class", "continuous_monitor",
            "--action-kind", "local_runtime_observe",
            "--continuation-policy", "same_agent_non_delivery",
            "--required-capability", "filesystem",
            "--target-key", "steward-secondary-watch",
            "--watch-only",
            "--cadence", "30m",
            "--next-due-at", second_due,
            "--execute",
        ],
    )
    pr_todo_id = added_first["todo_id"]
    secondary_todo_id = added_second["todo_id"]
    root = runtime.coordination_runtime_root
    snapshot_path = steward_monitor_snapshot_path(root, actor["goal_id"], pr_todo_id)
    if scope == "repository-only":
        write_steward_monitor_scope(root, actor["goal_id"], target, {
            "schema_version": "steward_signal_monitor_scope_v0",
            "include_portfolio": False,
            "include_inbox": False,
            "pr_filter": {"exclude_drafts": True},
        })
    transport.reconcile()

    def poll_events(turn):
        return [event for event in store.events_after(turn["session_id"], turn["turn_id"], None)
                if event["kind"] == "native_loopx.monitor_poll"]

    verified_repository = {"status": "ok", "repository": "org/repo", "items": [{"number": 42}]}
    if scope != "repository-only":
        from loopx.control_plane.collaboration.inbox import ENTRY_SCHEMA, _hash, _root, _write, plan_request_publication

        monkeypatch.setattr("loopx.capabilities.native_chat.steward_monitor._observe_github_repository_prs",
                            lambda *_args, **_kwargs: verified_repository)
        runtime._steward_monitor_next_check.clear()
        seed_turns = pump_steward_monitors(runtime)
        assert len(seed_turns) == 1 and seed_turns[0]["loopx_request"]["todo_id"] == pr_todo_id
        seed = finish(runtime, seed_turns[0])
        assert seed["status"] == "completed" and len(poll_events(seed)) == 1
        assert json.loads(snapshot_path.read_bytes())["repository_snapshot"] == verified_repository
        trusted_repository = verified_repository
        if scope in {"rejected-poll", "lost-commit-reply"}:
            candidate_repository = {**verified_repository, "items": [{"number": 43}]}
            verified_snapshot = snapshot_path.read_bytes()
            committed = local_watch_row(runtime, actor, pr_todo_id)
            candidate_due = (datetime.fromisoformat(first_due) - timedelta(seconds=1)).isoformat()
            edit_local_watch(runtime, actor, pr_todo_id, "--next-due-at", candidate_due)
            native_poll = StewardMonitor._native_poll

            def lose_poll_response(owner, argv, **kwargs):
                if scope == "lost-commit-reply":
                    assert native_poll(owner, argv, **kwargs)["ok"] is True
                raise RuntimeError("synthetic fault at the native poll response boundary")

            with monkeypatch.context() as fault:
                fault.setattr("loopx.capabilities.native_chat.steward_monitor._observe_github_repository_prs",
                              lambda *_args, **_kwargs: candidate_repository)
                fault.setattr(StewardMonitor, "_native_poll", lose_poll_response)
                runtime._steward_monitor_next_check.clear()
                attempts = pump_steward_monitors(runtime)
                assert len(attempts) == 1 and attempts[0]["loopx_request"]["todo_id"] == pr_todo_id
                failed_attempt = finish(runtime, attempts[0])
            assert failed_attempt["status"] == "failed" and not poll_events(failed_attempt)
            assert snapshot_path.read_bytes() == verified_snapshot
            after_attempt = local_watch_row(runtime, actor, pr_todo_id)
            if scope == "rejected-poll":
                assert after_attempt["result_hash"] == committed["result_hash"]
                assert after_attempt["last_checked_at"] == committed["last_checked_at"]
            else:
                assert after_attempt["result_hash"] != committed["result_hash"]
                trusted_repository = candidate_repository
        outage_due = (datetime.fromisoformat(first_due) - timedelta(seconds=2)).isoformat()
        edit_local_watch(runtime, actor, pr_todo_id, "--next-due-at", outage_due)
        receiver = {key: actor[key] for key in ("goal_id", "agent_id")}
        source = "synthetic-recovery-control"
        request_id = _hash([source, receiver])
        entry = plan_request_publication({
            "schema_version": ENTRY_SCHEMA, "request_id": request_id, "source_id": source,
            **receiver, "message": "Inspect the independent inbox signal.",
        }, entry=None)["entry"]
        _write(_root(root) / "entries" / _hash(receiver) / f"{request_id}.json", entry)

    calls = {"count": 0}

    def flaky_repo_observe(repo_slug, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"status": "unavailable", "repository": repo_slug}
        return {"status": "ok", "repository": repo_slug, "items": []}

    monkeypatch.setattr(
        "loopx.capabilities.native_chat.steward_monitor._observe_github_repository_prs",
        flaky_repo_observe,
    )
    before = local_watch_row(runtime, actor, pr_todo_id)
    quota_before = spent_slots(monitor, actor)
    runtime._steward_monitor_next_check.clear()
    initial = pump_steward_monitors(runtime)
    assert len(initial) == 1 and initial[0]["loopx_request"]["todo_id"] == pr_todo_id
    first = finish(runtime, initial[0])
    assert calls["count"] == 1
    if scope != "repository-only":
        assert first["status"] == "completed"
        native = first["response"]["native_loopx"]
        assert not native["model_invoked"] and not native["quota_spent"]
        observed = native["monitor"]
        assert observed["observation_incomplete"] is False
        assert observed["repository_observation_incomplete"] is True
        assert observed["material_change"] is True and observed["result_hash"] != before["result_hash"]
        snapshot = json.loads(snapshot_path.read_bytes())
        assert snapshot["repository_snapshot"] == trusted_repository
        assert snapshot["repository_observation_incomplete"] is True
        assert snapshot["inbox_items"] == [{"request_id": request_id, "source_id": source}]
        assert snapshot["result_hash"] == observed["result_hash"]
        assert len(poll_events(first)) == 1
        after = local_watch_row(runtime, actor, pr_todo_id)
        assert after["result_hash"] == observed["result_hash"] and after["next_due_at"] != before["next_due_at"]
        runtime._steward_monitor_next_check.clear()
        next_due = pump_steward_monitors(runtime)
        assert len(next_due) == 1 and next_due[0]["loopx_request"]["todo_id"] == secondary_todo_id
        assert "monitor_restart_recovery" not in next_due[0]["loopx_request"]
        assert finish(runtime, next_due[0])["status"] == "completed"
        assert spent_slots(monitor, actor) == quota_before
        return

    failed = first
    assert failed["status"] == "failed"
    assert failed["error_code"] == "runtime_error"
    assert failed["error"] == "steward monitor external signal source temporarily unavailable"
    assert not poll_events(failed) and not snapshot_path.exists()
    poll_fields = ("result_hash", "last_checked_at", "next_due_at", "material_change_generation")
    after_first_failure = local_watch_row(runtime, actor, pr_todo_id)
    for field in poll_fields:
        assert after_first_failure.get(field) == before.get(field)

    # 2. If due[0] stays unavailable on recovery, it executes its ONE bounded retry and stops failing over itself.
    # Replace flaky mock to fail a second time so we prove 1-retry cap AND non-starvation of due[1]!
    monkeypatch.setattr(
        "loopx.capabilities.native_chat.steward_monitor._observe_github_repository_prs",
        lambda repo_slug, **_kw: {"status": "unavailable", "repository": repo_slug},
    )
    runtime._steward_monitor_next_check.clear()
    recovery = pump_steward_monitors(runtime)
    assert len(recovery) == 1
    assert recovery[0]["loopx_request"]["todo_id"] == pr_todo_id
    assert recovery[0]["loopx_request"]["monitor_restart_recovery"]["original_turn_id"] == failed["turn_id"]
    failed_again = finish(runtime, recovery[0])
    assert failed_again["status"] == "failed"
    assert not poll_events(failed_again) and not snapshot_path.exists()
    after_second_failure = local_watch_row(runtime, actor, pr_todo_id)
    for field in poll_fields:
        assert after_second_failure.get(field) == before.get(field)

    # 3. Next pump skips the unrecoverable due[0] and dispatches due[1] instead of starving it!
    runtime._steward_monitor_next_check.clear()
    next_due = pump_steward_monitors(runtime)
    assert len(next_due) == 1 and next_due[0]["loopx_request"]["todo_id"] == secondary_todo_id
    done_secondary = finish(runtime, next_due[0])
    assert done_secondary["status"] == "completed", done_secondary
    assert local_watch_row(runtime, actor, secondary_todo_id)["next_due_at"] != second_due

    # 4. Also verify recovery succeeds when GitHub recovers after the first failure.
    third_due = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat()
    edit_local_watch(runtime, actor, pr_todo_id, "--next-due-at", third_due)
    monkeypatch.setattr(
        "loopx.capabilities.native_chat.steward_monitor._observe_github_repository_prs",
        flaky_repo_observe,
    )
    calls["count"] = 0
    runtime._steward_monitor_next_check.clear()
    first_try = pump_steward_monitors(runtime)
    assert len(first_try) == 1 and finish(runtime, first_try[0])["status"] == "failed"
    runtime._steward_monitor_next_check.clear()
    recovered_try = pump_steward_monitors(runtime)
    assert len(recovered_try) == 1
    recovered_done = finish(runtime, recovered_try[0])
    assert recovered_done["status"] == "completed", recovered_done
    assert len(poll_events(recovered_done)) == 1
    recovered_watch = local_watch_row(runtime, actor, pr_todo_id)
    assert recovered_watch["next_due_at"] != third_due
    assert recovered_watch["result_hash"] == json.loads(snapshot_path.read_bytes())["result_hash"]
    assert spent_slots(monitor, actor) == quota_before


@pytest.mark.parametrize("revision", [
    "changed-during-read", "equivalent-rewrite", "writer-during-commit",
    "repository-change", "repository-change-lost-reply",
])
def test_signal_monitor_commits_only_under_its_observed_scope(steward, tmp_path, monkeypatch, revision):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from test_steward_generic_monitor_todo import _set_signal_source_policy
    from loopx.capabilities.native_chat.steward_commands import native_command
    from loopx.capabilities.native_chat.steward_monitor_turns import (
        read_steward_monitor_scope,
        steward_monitor_scope_path,
        steward_monitor_snapshot_path,
        write_steward_monitor_scope,
    )
    from loopx.file_lock import LockAcquireTimeoutError, exclusive_file_lock

    store, runtime, _, transport, binding, _, _ = steward
    sid, actor, monitor = prepared(steward, tmp_path, monkeypatch, "sqlite")
    _set_signal_source_policy(runtime, store.load_session(sid), binding,
                              goal_id=actor["goal_id"], agent_id=actor["agent_id"])
    edit_local_watch(
        runtime, actor, monitor.current(actor)["todo_id"], "--next-due-at",
        (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )
    target = "steward-filter-ordering-prs"
    first_due = datetime.now(timezone.utc) - timedelta(seconds=10)
    added = native_command(runtime, [
        "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
        "--text", "Monitor scoped pull requests", "--claimed-by", actor["agent_id"],
        "--task-class", "continuous_monitor", "--action-kind", "local_runtime_observe",
        "--continuation-policy", "same_agent_non_delivery", "--required-capability", "filesystem",
        "--target-key", target, "--task-repository", "git:github.com/org/repo", "--watch-only",
        "--cadence", "30m", "--next-due-at", first_due.isoformat(), "--execute",
    ])
    todo_id = added["todo_id"]
    root, goal_id = runtime.coordination_runtime_root, actor["goal_id"]
    original_scope = {
        "schema_version": "steward_signal_monitor_scope_v0", "include_portfolio": False, "include_inbox": False,
        "pr_filter": {"exclude_drafts": False, "author_mode": "exclude", "authors": ["maintainer"]},
    }
    normalized = write_steward_monitor_scope(root, goal_id, target, original_scope)
    changed_scope = {**original_scope, "pr_filter": {**original_scope["pr_filter"], "exclude_drafts": True}}
    repository_changed = revision in {"repository-change", "repository-change-lost-reply"}
    transport.reconcile()

    def events(turn):
        return [event for event in store.events_after(turn["session_id"], turn["turn_id"], None)
                if event["kind"] == "native_loopx.monitor_poll"]

    def observe(items):
        return {"status": "ok", "repository": "org/repo", "items": items}

    observer = "loopx.capabilities.native_chat.steward_monitor._observe_github_repository_prs"
    committed_pr = {"number": 42, "isDraft": False, "author": {"login": "contributor"}}
    monkeypatch.setattr(observer, lambda *_args, **_kwargs: observe([committed_pr]))
    runtime._steward_monitor_next_check.clear()
    seeded = finish(runtime, pump_steward_monitors(runtime)[0])
    assert seeded["status"] == "completed" and len(events(seeded)) == 1
    snapshot_path = steward_monitor_snapshot_path(root, goal_id, todo_id)
    prior_snapshot = snapshot_path.read_bytes()
    edit_local_watch(runtime, actor, todo_id, "--next-due-at", (first_due - timedelta(seconds=1)).isoformat())
    before = local_watch_row(runtime, actor, todo_id)
    quota_before = spent_slots(monitor, actor)
    draft = {"number": 43, "isDraft": True, "author": {"login": "contributor"}}
    calls = 0
    edited_todo = None

    def read_repository(repository, **_kwargs):
        nonlocal calls, edited_todo
        calls += 1
        if calls == 1 and revision == "changed-during-read":
            write_steward_monitor_scope(root, goal_id, target, changed_scope)
        if calls == 1 and revision == "equivalent-rewrite":
            equivalent = {**original_scope, "pr_filter": {**original_scope["pr_filter"], "authors": ["MAINTAINER", "maintainer"]}}
            assert write_steward_monitor_scope(root, goal_id, target, equivalent) == normalized
        if calls == 1 and repository_changed:
            assert repository == "org/repo"
            actions = transport.core.actions
            proposal = actions.preview({
                "action_kind": "monitor.update", "summary": "Change the repository for this watch",
                "normalized_parameters": {"goal_id": goal_id, "todo_id": todo_id, "agent_id": actor["agent_id"],
                                          "operation": "edit", "task_repository": "git:github.com/org/replacement"},
                "context": {}, "idempotency_key": "change-watch-repository",
            })
            assert proposal["status"] == "preview_ready", proposal
            applied = actions.apply(proposal["proposal_id"])["proposal"]
            assert applied["status"] == "applied", applied
            edited_todo = local_watch_row(runtime, actor, todo_id)
            assert edited_todo["task_repository"] == "git:github.com/org/replacement"
        if repository_changed and calls > 1:
            assert repository == "org/replacement"
            return {"status": "ok", "repository": repository, "items": [{**committed_pr, "number": 44}]}
        return observe([draft])

    monkeypatch.setattr(observer, read_repository)
    runtime._steward_monitor_next_check.clear()
    with ThreadPoolExecutor(max_workers=1) as writers:
        if revision == "writer-during-commit":
            contended = Event()
            attempts = []
            native_poll = StewardMonitor._native_poll

            def edit_scope():
                path = steward_monitor_scope_path(root, goal_id, target)
                with pytest.raises(LockAcquireTimeoutError), exclusive_file_lock(path, timeout_seconds=0):
                    pass
                contended.set()
                return write_steward_monitor_scope(root, goal_id, target, changed_scope)

            def poll_with_concurrent_editor(owner, argv, **kwargs):
                attempts.append(writers.submit(edit_scope))
                assert contended.wait(5), "scope writer did not observe the commit lock"
                assert not attempts[0].done()
                return native_poll(owner, argv, **kwargs)

            monkeypatch.setattr(StewardMonitor, "_native_poll", poll_with_concurrent_editor)
        elif revision == "repository-change-lost-reply":
            from loopx.capabilities.native_chat.steward_commands import NativeStewardCommandError
            from loopx.capabilities.native_chat.steward_turn_common import _WORKER_EXITED_ERROR

            native_poll = StewardMonitor._native_poll

            def poll_with_lost_abort_reply(owner, argv, **kwargs):
                try:
                    return native_poll(owner, argv, **kwargs)
                except NativeStewardCommandError as error:
                    assert error.payload["provider_aborted_no_effect"] is True
                    raise RuntimeError(_WORKER_EXITED_ERROR) from None

            monkeypatch.setattr(StewardMonitor, "_native_poll", poll_with_lost_abort_reply)
        turns = pump_steward_monitors(runtime)
        assert len(turns) == 1 and turns[0]["loopx_request"]["todo_id"] == todo_id
        first = finish(runtime, turns[0])
        if revision == "writer-during-commit":
            assert attempts[0].result(timeout=5)["pr_filter"]["exclude_drafts"] is True
    assert calls == 1

    if revision == "changed-during-read" or repository_changed:
        changed = "repository" if repository_changed else "signal scope"
        lost_reply = revision == "repository-change-lost-reply"
        assert first["status"] == "failed"
        assert first["error"] == (_WORKER_EXITED_ERROR if lost_reply else f"the monitor {changed} changed after observation")
        assert not events(first)
        assert snapshot_path.read_bytes() == prior_snapshot
        assert snapshot_path.with_suffix(".pending.json").exists() is lost_reply
        after_failure = local_watch_row(runtime, actor, todo_id)
        previous_poll = edited_todo if edited_todo is not None else before
        for field in ("result_hash", "monitor_effect_id", "last_checked_at", "next_due_at", "material_change_generation"):
            assert after_failure.get(field) == previous_poll.get(field)
        runtime._steward_monitor_next_check.clear()
        recovery = pump_steward_monitors(runtime)
        assert len(recovery) == 1
        assert recovery[0]["loopx_request"]["monitor_restart_recovery"]["original_turn_id"] == first["turn_id"]
        accepted = finish(runtime, recovery[0])
        assert calls == 2 and accepted["status"] == "completed" and len(events(accepted)) == 1
        snapshot = json.loads(snapshot_path.read_bytes())
        assert not snapshot_path.with_suffix(".pending.json").exists()
        if repository_changed:
            assert snapshot["task_repository"] == "git:github.com/org/replacement"
            assert snapshot["repository_snapshot"]["items"] == [{**committed_pr, "number": 44}]
        else:
            assert snapshot["repository_snapshot"]["items"] == []
        assert snapshot["signal_scope"] == read_steward_monitor_scope(root, goal_id, target)
    else:
        assert first["status"] == "completed" and len(events(first)) == 1
        snapshot = json.loads(snapshot_path.read_bytes())
        assert snapshot["repository_snapshot"]["items"] == [draft]
        assert snapshot["signal_scope"] == normalized
        if revision == "writer-during-commit":
            assert read_steward_monitor_scope(root, goal_id, target)["pr_filter"]["exclude_drafts"] is True

    after = local_watch_row(runtime, actor, todo_id)
    assert after["next_due_at"] != before["next_due_at"] and after["result_hash"] == snapshot["result_hash"]
    assert spent_slots(monitor, actor) == quota_before


@pytest.mark.parametrize(
    ("error_code", "error_message", "fail_before_context"),
    [
        (
            "runtime_error",
            "The LoopX Chat execution worker exited before this turn finished. Your conversation is saved; execution may have occurred. This turn was not retried automatically.",
            False,
        ),
        ("runtime_error", "disk I/O error", False),
        ("runtime_error", "the portfolio scope changed after observation", False),
        (
            "runtime_unavailable",
            "The Agent runtime could not be prepared. Check the runtime installation and configuration, then retry.",
            True,
        ),
        ("lock_acquire_timeout", "file lock acquisition timed out (recorded holder pid 4189)", False),
        ("runtime_error", "file lock acquisition timed out (recorded holder pid 4189)", False),
        ("runtime_error", "file lock acquisition timed out (lock busy, current holder unknown)", False),
    ],
)
def test_transient_zero_effect_monitor_failures_recover_once(
    steward, tmp_path, monkeypatch, error_code, error_message, fail_before_context,
):
    from loopx.capabilities.native_chat.steward_turn import StewardTurnHost

    store, runtime, *_ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, "sqlite")
    default_watch = monitor.current(actor)
    todo_id = default_watch["todo_id"]
    due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    edit_local_watch(runtime, actor, todo_id, "--next-due-at", due)
    with monkeypatch.context() as paused:
        paused.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: True)
        runtime._steward_monitor_next_check.clear()
        first = pump_steward_monitors(runtime)
        assert len(first) == 1
        failed_turn = first[0]
    sid = failed_turn["session_id"]
    turn_id = failed_turn["turn_id"]
    if fail_before_context:
        runtime._fail_turn(
            sid, turn_id, error_code, error_message,
            status="failed", expected_statuses={"queued", "starting", "running"},
        )
    store.append_event(
        sid, turn_id, kind="agent.phase",
        payload={"phase": "manager_context", "label": "Reading authorized Goal state"},
    )
    store.append_event(
        sid, turn_id, kind="manager.context",
        payload={"schema_version": "manager_turn_context_v1", "goals": []},
    )
    if not fail_before_context:
        runtime._fail_turn(
            sid, turn_id, error_code, error_message,
            status="failed", expected_statuses={"queued", "starting", "running"},
        )
    request = StewardTurnHost.monitor_restart_request(store.load_turn(sid, turn_id))
    assert request is not None and request["monitor_restart_recovery"]["original_turn_id"] == turn_id

    runtime._steward_monitor_next_check.clear()
    recovered = pump_steward_monitors(runtime)
    assert len(recovered) == 1
    assert recovered[0]["loopx_request"]["monitor_restart_recovery"]["original_turn_id"] == turn_id
    done = finish(runtime, recovered[0])
    assert done["status"] == "completed", done
    assert local_watch_row(runtime, actor, todo_id)["next_due_at"] != due

    # Even if a restart carrier itself fails without polling, it must not spawn a second retry.
    store.update_turn(sid, done["turn_id"], status="failed", error_code=error_code, error=error_message)
    carrier = store.load_turn(sid, done["turn_id"])
    assert carrier is not None and carrier["client_turn_id"].endswith(".restart-recovery")
    assert StewardTurnHost.monitor_restart_request(carrier) is None


def test_chained_transient_monitor_recovery_waits_for_cooldown_then_recovers(
    steward, tmp_path, monkeypatch,
):
    from loopx.capabilities.native_chat.steward_turn import MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS, StewardTurnHost

    store, runtime, *_ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, "sqlite")
    default_watch = monitor.current(actor)
    todo_id = default_watch["todo_id"]
    due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    edit_local_watch(runtime, actor, todo_id, "--next-due-at", due)

    with monkeypatch.context() as paused:
        paused.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: True)
        runtime._steward_monitor_next_check.clear()
        initial = pump_steward_monitors(runtime)[0]
    sid = initial["session_id"]
    store.append_event(sid, initial["turn_id"], kind="agent.phase", payload={"phase": "manager_context"})
    store.append_event(sid, initial["turn_id"], kind="manager.context",
        payload={"schema_version": "manager_turn_context_v1", "goals": []})
    runtime._fail_turn(sid, initial["turn_id"], "runtime_error", "disk I/O error",
        status="failed", expected_statuses={"queued", "starting", "running"})

    with monkeypatch.context() as paused:
        paused.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: True)
        runtime._steward_monitor_next_check.clear()
        carrier = pump_steward_monitors(runtime)[0]
    store.append_event(sid, carrier["turn_id"], kind="agent.phase", payload={"phase": "manager_context"})
    store.append_event(sid, carrier["turn_id"], kind="manager.context",
        payload={"schema_version": "manager_turn_context_v1", "goals": []})
    runtime._fail_turn(sid, carrier["turn_id"], "runtime_unavailable",
        "The Agent runtime could not be prepared.",
        status="failed", expected_statuses={"queued", "starting", "running"})

    # Immediately after carrier failure (< cooldown), pumping stays idle instead of spinning.
    runtime._steward_monitor_next_check.clear()
    assert pump_steward_monitors(runtime) == []

    # After cooldown elapses, a bounded .restart-recovery.<carrier_turn_id> carrier recovers and polls.
    aged_completed = (
        datetime.now(timezone.utc) - timedelta(seconds=MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS + 5)
    ).isoformat().replace("+00:00", "Z")
    store.update_turn(sid, carrier["turn_id"], completed_at=aged_completed)
    req = StewardTurnHost.monitor_restart_request(store.load_turn(sid, carrier["turn_id"]))
    assert req is not None and req["monitor_restart_recovery"]["original_turn_id"] == carrier["turn_id"]

    runtime._steward_monitor_next_check.clear()
    chained = pump_steward_monitors(runtime)
    assert len(chained) == 1
    assert chained[0]["client_turn_id"].endswith(".restart-recovery." + carrier["turn_id"])
    assert chained[0]["loopx_request"]["monitor_restart_recovery"]["original_turn_id"] == carrier["turn_id"]
    done = finish(runtime, chained[0])
    assert done["status"] == "completed", done
    assert local_watch_row(runtime, actor, todo_id)["next_due_at"] != due


def test_session_queue_drain_preserves_active_and_queued_observation_only_monitors(
    steward, tmp_path, monkeypatch,
):
    from loopx.chat_agent import CodexChatAgentError
    import json
    import threading

    store, runtime, *_ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, "sqlite")
    default_watch = monitor.current(actor)
    todo_id = default_watch["todo_id"]
    due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    edit_local_watch(runtime, actor, todo_id, "--next-due-at", due)

    # Create a crash-stranded queued observation-only monitor turn (no _acceptance, status=queued).
    with monkeypatch.context() as paused:
        paused.setattr(runtime, "_start_accepted_turn_worker", lambda **_kw: True)
        runtime._steward_monitor_next_check.clear()
        queued_watch = pump_steward_monitors(runtime)[0]
    sid = queued_watch["session_id"]
    turn_id = queued_watch["turn_id"]
    store.update_session(sid, active_turn_id=None)
    queued_doc = store.load_turn(sid, turn_id)
    assert queued_doc is not None
    queued_doc.pop("_acceptance", None)
    queued_doc["status"] = "queued"
    queued_doc["started_at"] = None
    store._turn_path(sid, turn_id).write_text(json.dumps(queued_doc))

    adapter_calls = 0
    fail_calls = 0

    def fail_ensure_adapter(*_args, **_kwargs):
        nonlocal adapter_calls
        adapter_calls += 1
        raise CodexChatAgentError("Native account API transport is unavailable.")

    orig_fail = runtime._fail_queue_preparation

    def spy_fail(*args, **kwargs):
        nonlocal fail_calls
        fail_calls += 1
        return orig_fail(*args, **kwargs)

    # First verify draining while monitor is already running does not invoke _ensure_adapter or fail it,
    # and second verify draining a crash-stranded queued monitor uses StewardObservationAdapter cleanly.
    store.update_session(sid, active_turn_id=turn_id)
    store.update_turn(sid, turn_id, status="running", started_at=queued_doc["created_at"])
    monkeypatch.setattr(runtime, "_ensure_adapter", fail_ensure_adapter)
    monkeypatch.setattr(runtime, "_fail_queue_preparation", spy_fail)

    def complete_soon():
        time.sleep(0.12)
        store.update_turn(sid, turn_id, status="completed", response={"message": "ok", "proposals": []})

    t = threading.Thread(target=complete_soon, daemon=True)
    t.start()
    runtime._drain_session_queue(session_id=sid, work_dir=tmp_path, objective="Monitor")
    t.join(timeout=2)
    assert adapter_calls == 0
    assert fail_calls == 0
    assert store.load_turn(sid, turn_id)["status"] == "completed"

    # Now verify crash-stranded queued monitor branch invokes _start_accepted_turn_worker with StewardObservationAdapter.
    dispatched: list[object] = []
    store.update_session(sid, active_turn_id=None)
    queued_doc["status"] = "queued"
    queued_doc["started_at"] = None
    store._turn_path(sid, turn_id).write_text(json.dumps(queued_doc))

    def fake_run_turn(*, adapter, turn_id: str, **_kw):
        dispatched.append(adapter)
        store.update_turn(sid, turn_id, status="completed", response={"message": "ok", "proposals": []})
        store.update_session(sid, active_turn_id=None)

    monkeypatch.setattr(runtime, "_run_turn", fake_run_turn)
    runtime._drain_session_queue(session_id=sid, work_dir=tmp_path, objective="Monitor")
    assert adapter_calls == 0 and fail_calls == 0
    assert len(dispatched) == 1 and getattr(dispatched[0], "observation_only", False) is True


def test_lock_acquire_timeout_preserves_error_code_and_cooldown_bounds(
    steward, tmp_path, monkeypatch,
):
    from loopx.capabilities.native_chat.steward_turn import MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS, StewardTurnHost
    from loopx.file_lock import LockAcquireTimeoutError

    store, runtime, *_ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, "sqlite")
    todo_id = monitor.current(actor)["todo_id"]
    due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    edit_local_watch(runtime, actor, todo_id, "--next-due-at", due)

    orig_observe = StewardMonitor.observe
    remaining_timeouts = {"count": 2}

    def contended_observe(self, *args, **kwargs):
        if remaining_timeouts["count"] > 0:
            remaining_timeouts["count"] -= 1
            raise LockAcquireTimeoutError(
                incident={"holder": {"pid": 4189}, "operator_action": "retry"},
                incident_recorded=False,
                incident_channel="test",
            )
        return orig_observe(self, *args, **kwargs)

    monkeypatch.setattr(StewardMonitor, "observe", contended_observe)

    # First monitor attempt hits LockAcquireTimeoutError after manager.context; _run_turn must preserve error_code.
    runtime._steward_monitor_next_check.clear()
    initial = pump_steward_monitors(runtime)[0]
    failed = finish(runtime, initial)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "lock_acquire_timeout"
    assert failed["error"] == "file lock acquisition timed out (recorded holder pid 4189)"

    # Malformed lock strings under legacy runtime_error stay rejected.
    bad_turn = {
        **failed,
        "error_code": "runtime_error",
        "error": "file lock acquisition timed out (recorded holder pid 0)",
        "loopx_request": {**failed["loopx_request"], "monitor_restart_recovery": {"original_turn_id": "0" * 32}},
    }
    assert StewardTurnHost.monitor_restart_request(bad_turn) is None

    # Immediate bounded retry hits LockAcquireTimeoutError a second time.
    runtime._steward_monitor_next_check.clear()
    carrier = pump_steward_monitors(runtime)[0]
    assert carrier["loopx_request"]["monitor_restart_recovery"]["original_turn_id"] == failed["turn_id"]
    carrier_failed = finish(runtime, carrier)
    assert carrier_failed["error_code"] == "lock_acquire_timeout"

    # Chained retry respects the 300s cooldown before recovering once contention clears.
    runtime._steward_monitor_next_check.clear()
    assert pump_steward_monitors(runtime) == []
    aged = (datetime.now(timezone.utc) - timedelta(seconds=MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS + 5)).isoformat().replace("+00:00", "Z")
    store.update_turn(carrier_failed["session_id"], carrier_failed["turn_id"], completed_at=aged)
    runtime._steward_monitor_next_check.clear()
    recovered = pump_steward_monitors(runtime)
    assert len(recovered) == 1
    assert recovered[0]["loopx_request"]["monitor_restart_recovery"]["original_turn_id"] == carrier_failed["turn_id"]
    done = finish(runtime, recovered[0])
    assert done["status"] == "completed", done
    assert local_watch_row(runtime, actor, todo_id)["next_due_at"] != due


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_configure_rejects_malformed_plan_todo_and_preserves_due_open_watch(
    steward, tmp_path, monkeypatch, provider,
):
    from loopx.capabilities.native_chat import steward_monitor as monitor_mod

    _, runtime, _, _, binding, _, _ = steward
    _, actor, monitor = prepared(steward, tmp_path, monkeypatch, provider)
    todo_id = monitor.current(actor)["todo_id"]
    due = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    edit_local_watch(runtime, actor, todo_id, "--next-due-at", due)
    assert local_watch_row(runtime, actor, todo_id)["next_due_at"] == due
    assert [row["todo_id"] for row in monitor.due_monitors(actor)] == [todo_id]

    bindings = runtime.project_contexts.conversation_bindings
    orig_core = bindings._core
    orig_native = monitor_mod.native_command
    commands = []

    def tracking_native(ctrl, args):
        commands.append(list(args))
        return orig_native(ctrl, args)

    monkeypatch.setattr(monitor_mod, "native_command", tracking_native)
    injected = {"value": None}

    def wrapped_core(command, payload):
        res = orig_core(command, payload)
        if command == "collaboration.steward.monitor.plan" and injected["value"] is not _UNSET:
            return {**res, "todo": injected["value"]}
        return res

    _UNSET = object()
    monkeypatch.setattr(bindings, "_core", wrapped_core)

    for bad_todo in ("bad-non-mapping", "", ["bad"], 123, False):
        injected["value"] = bad_todo
        for op in ("pause", "enable"):
            preview = monitor.configure(binding["binding_id"], operation=op)["plan"]
            with pytest.raises(TypeError, match="mapping or None"):
                monitor.configure(
                    binding["binding_id"],
                    operation=op,
                    execute=True,
                    expected_plan_id=preview["plan_id"],
                )
        assert commands == []
        persisted = local_watch_row(runtime, actor, todo_id)
        assert persisted["status"] == "open"
        assert persisted["next_due_at"] == due
        fresh_monitor = StewardMonitor(runtime)
        assert fresh_monitor.current(actor)["status"] == "open"
        assert [row["todo_id"] for row in fresh_monitor.due_monitors(actor)] == [todo_id]

    injected["value"] = _UNSET
    valid_pause = StewardMonitor(runtime).configure(binding["binding_id"], operation="pause")["plan"]
    paused = StewardMonitor(runtime).configure(
        binding["binding_id"],
        operation="pause",
        execute=True,
        expected_plan_id=valid_pause["plan_id"],
    )
    assert (
        paused["ok"] is True
        and paused["applied"] is True
        and isinstance(paused.get("receipt"), dict)
        and paused["receipt"].get("ok") is True
        and len(commands) == 1
        and "--status" in commands[0]
        and "blocked" in commands[0]
    )
    assert local_watch_row(runtime, actor, todo_id)["status"] == "blocked"
    assert StewardMonitor(runtime).current(actor)["status"] == "blocked"
    assert StewardMonitor(runtime).due_monitors(actor) == []
