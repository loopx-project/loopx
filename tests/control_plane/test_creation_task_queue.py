"""An explicit creation request reaches the original Task through its Session queue.

HTTP/Chat/Core and File/SQLite are real. The existing six-check oracle remains
independent; the native protocol and artifact editing are scripted transport.
"""
from contextlib import contextmanager
import hashlib
import http.client
import json
import subprocess
import sys
import threading
import time

import pytest

from test_original_task_entry import cart_host as cart_host
from test_new_goal_storage_defaults import environment as environment
from loopx.chat_actions import ChatActionService
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.chat_turn_acceptance import CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.capabilities.native_chat.project_context import ChatProjectContexts


def preview_requested_task(host, *, requested=True):
    request = host[1]
    parameters = {"goal_id": "cart-repair", "title": "Cart repair",
        "objective": "Repair cart.py according to README.md and unchanged checks.py.",
        "completion_criteria": "The six existing checks pass without changing checks.py.",
        "agent_id": "codex", "workspace_ref": "current", "heartbeat": {"enabled": False},
        "initial_todos": ["Repair cart.py to pass the six existing behavioral checks."]}
    if requested:
        parameters.update(start_execution=True, permission="workspace_write_on_confirmation")
    status, preview = request("/api/actions/preview", {"action_kind": "goal.create",
        "summary": "Repair the cart using its existing checks", "idempotency_key": "cart-queue-request",
        "context": {"kind": "goal_channel", "goal_id": "workspace"}, "normalized_parameters": parameters})
    assert status == 201, preview
    return preview["proposal"]


def create_requested_task(host, *, requested=True):
    actions, request, controller, _, _, _ = host
    proposal = preview_requested_task(host, requested=requested)
    status, applied = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 202, applied
    ids = applied["proposal"]["receipt"]["resource_ids"]
    first = controller.wait_for_turn(session_id=ids["session_id"], turn_id=ids["turn_id"], timeout_sec=20)
    assert first["status"] == "completed", first
    proposals = actions.list(goal_id="cart-repair", context_kind="goal")
    checks = [row for row in proposals if row["action_kind"] == "goal.acceptance.check"]
    assert len(checks) == 1, first
    return actions.load(proposal["proposal_id"]), ids, checks[0]


def wait_for_creation_turn(controller, session_id, operation_id, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        turn = controller.store.turn_for_client(session_id, "managed." + operation_id)
        if turn is not None:
            return turn
        time.sleep(0.02)
    raise AssertionError("the original Session queue did not consume the saved creation request")


def task_row(runtime, todo_id):
    authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="cart-repair", include_leases=True)
    return next(row for row in authority["todos"] if row["todo_id"] == todo_id)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["valid", "failed"])
def test_creation_queue_consumes_only_the_saved_request_and_returns_original_result(environment, cart_host, monkeypatch, provider, mode):
    environment[0](provider)
    actions, request, controller, capture, project, runtime = cart_host
    creation, ids, check = create_requested_task(cart_host)
    saved = creation["receipt"]["original_task_request"]
    sid, todo = ids["session_id"], ids["todo_ids"][0]
    assert saved["session_id"] == sid and saved["todo_id"] == todo
    assert saved["operation_id"] == "goal-create-" + creation["proposal_id"]
    original_session = controller.store.load_session(sid)
    history = controller.store.messages(sid)
    checker_hash = hashlib.sha256((project / "checks.py").read_bytes()).hexdigest()
    before = capture.read_bytes()
    for _ in range(2):
        status, observed = request(f"/api/chat/sessions/{sid}/native-turn?original_task=true&agent_id=codex")
        assert status == 200, observed
        assert observed["task"]["creation_request"] == saved
        assert observed["task"]["creation_turn"] is None
        assert observed["task"]["state"] == "unbound"
    assert capture.read_bytes() == before and task_row(runtime, todo)["status"] == "open"
    (project / "execute-native").write_text(mode)
    status, adopted = request(f"/api/actions/{check['proposal_id']}/apply", {})
    assert status == 200 and adopted["turn"] is None, adopted
    assert adopted["proposal"]["receipt"]["checks_passed"] is False
    turn = wait_for_creation_turn(controller, sid, saved["operation_id"])
    finished = controller.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=20)
    result = finished["response"]["native_loopx"]
    assert result["status"] == ("committed" if mode == "valid" else "failed"), result
    assert result["effects"]["quota_spent"] is (mode == "valid")
    assert task_row(runtime, todo)["status"] == ("done" if mode == "valid" else "open")
    assert finished["loopx_request"]["creation_request"] == saved
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    assert sum(row.get("method") == "turn/start" for row in calls) == 1
    assert not any(row.get("method") == "thread/start" for row in calls)
    assert controller.store.load_session(sid)["upstream_thread_id"] == original_session["upstream_thread_id"]
    assert controller.store.messages(sid)[:len(history)] == history
    assert hashlib.sha256((project / "checks.py").read_bytes()).hexdigest() == checker_hash
    after = capture.read_bytes()
    for thread in list(controller.session_queue_threads.values()):
        thread.join(timeout=2)
    with monkeypatch.context() as stopped:
        def unexpected_queue(**_kwargs):
            raise AssertionError("a terminal original operation must not wake another queue worker")
        stopped.setattr(controller, "resume_session_queue", unexpected_queue)
        controller.resume_creation_requests()
    assert capture.read_bytes() == after
    assert controller.store.turn_for_client(sid, "managed." + saved["operation_id"])["turn_id"] == turn["turn_id"]
    status, replay = request(f"/api/actions/{creation['proposal_id']}/apply", {})
    assert status in (200, 202) and replay["proposal"]["receipt"]["original_task_request"] == saved
    assert capture.read_bytes() == after


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_historical_creation_and_check_adoption_do_not_authorize_a_native_task(environment, cart_host, monkeypatch, provider):
    import builtins
    environment[0](provider)
    _, request, controller, capture, project, runtime = cart_host
    original_import = builtins.__import__
    def legacy_creation_import(name, *args, **kwargs):
        if name.endswith("chat.goal_creation_execution"):
            raise AssertionError("historical creation loaded the optional original-Task execution adapter")
        return original_import(name, *args, **kwargs)
    with monkeypatch.context() as legacy:
        legacy.setattr(builtins, "__import__", legacy_creation_import)
        creation, ids, check = create_requested_task(cart_host, requested=False)
    assert "original_task_request" not in creation["receipt"]
    sid = ids["session_id"]
    before = capture.read_bytes()
    (project / "execute-native").write_text("valid")
    status, adopted = request(f"/api/actions/{check['proposal_id']}/apply", {})
    assert status == 200 and adopted["turn"] is None, adopted
    controller.resume_creation_requests()
    status, observed = request(f"/api/chat/sessions/{sid}/native-turn?original_task=true&agent_id=codex")
    assert status == 200 and "creation_request" not in observed["task"], observed
    assert capture.read_bytes() == before
    assert task_row(runtime, ids["todo_ids"][0])["status"] == "open"


@contextmanager
def restarted_host(host):
    actions, _, prior, _, project, runtime = host
    prior.close()
    controller = ChatRuntimeController(store=prior.store, codex_bin=prior.codex_bin,
        registry_path=prior.registry_path, project_contexts=ChatProjectContexts([project]))
    service = ChatActionService(store=actions, registry_path=prior.registry_path,
        runtime_controller=controller, chat_store=prior.store)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.registry_path, server.runtime_root = prior.registry_path, runtime
    server.runtime_controller, server.action_store, server.action_service = controller, actions, service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller.resume_creation_requests()
    def request(path):
        connection = http.client.HTTPConnection(*server.server_address, timeout=30)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        yield controller, request
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        controller.close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_restart_resumes_the_same_accepted_unstarted_creation_turn(environment, cart_host, monkeypatch, provider):
    environment[0](provider)
    _, request, controller, capture, project, _ = cart_host
    creation, ids, check = create_requested_task(cart_host)
    saved = creation["receipt"]["original_task_request"]
    original_start = controller._start_accepted_turn_worker
    def lost_dispatch(**kwargs):
        if kwargs["loopx_execution"]:
            return False
        return original_start(**kwargs)
    monkeypatch.setattr(controller, "_start_accepted_turn_worker", lost_dispatch)
    (project / "execute-native").write_text("valid")
    before = capture.read_bytes()
    assert request(f"/api/actions/{check['proposal_id']}/apply", {})[0] == 200
    queued = wait_for_creation_turn(controller, ids["session_id"], saved["operation_id"])
    assert queued["status"] == "queued" and queued.get("started_at") is None
    assert capture.read_bytes() == before
    thread_id = controller.store.load_session(ids["session_id"])["upstream_thread_id"]
    with restarted_host(cart_host) as (restored, read):
        finished = restored.wait_for_turn(session_id=ids["session_id"], turn_id=queued["turn_id"], timeout_sec=30)
        assert finished["response"]["native_loopx"]["status"] == "committed", finished
        assert restored.store.load_session(ids["session_id"])["upstream_thread_id"] == thread_id
        status, observed = read(f"/api/chat/sessions/{ids['session_id']}/native-turn?original_task=true&agent_id=codex")
        assert status == 200 and observed["task"]["creation_turn"]["turn_id"] == queued["turn_id"], observed
        calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
        assert sum(row.get("method") == "turn/start" for row in calls) == 1
        assert not any(row.get("method") == "thread/start" for row in calls)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_failed_creation_dispatch_is_retained_without_another_model(environment, cart_host, monkeypatch, provider):
    environment[0](provider)
    _, request, controller, capture, project, runtime = cart_host
    creation, ids, check = create_requested_task(cart_host)
    saved = creation["receipt"]["original_task_request"]
    def fail_dispatch(**_kwargs):
        raise OSError("synthetic worker start failure")
    monkeypatch.setattr(controller, "_start_accepted_turn_worker", fail_dispatch)
    (project / "execute-native").write_text("valid")
    before = capture.read_bytes()
    assert request(f"/api/actions/{check['proposal_id']}/apply", {})[0] == 200
    turn = wait_for_creation_turn(controller, ids["session_id"], saved["operation_id"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and controller.store.load_turn(ids["session_id"], turn["turn_id"])["status"] != "failed":
        time.sleep(0.02)
    failed = controller.store.load_turn(ids["session_id"], turn["turn_id"])
    assert failed["status"] == "failed", failed
    controller.resume_creation_requests()
    for thread in list(controller.session_queue_threads.values()):
        thread.join(timeout=2)
    assert capture.read_bytes() == before
    assert controller.store.turn_for_client(ids["session_id"], "managed." + saved["operation_id"])["turn_id"] == turn["turn_id"]
    assert task_row(runtime, saved["todo_id"])["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["foreign-session", "fingerprint", "upstream-thread", "missing-thread-pin"])
def test_startup_rejects_a_changed_source_before_waking_any_session_queue(environment, cart_host, monkeypatch, provider, change):
    environment[0](provider)
    actions, request, controller, capture, project, _ = cart_host
    creation, ids, _ = create_requested_task(cart_host)
    before = capture.read_bytes()
    payload = actions._read()
    source = payload["proposals"][creation["proposal_id"]]
    if change == "foreign-session":
        foreign = controller.store.create_session(goal_id="cart-repair", managed_agent_id="codex",
            managed_workspace_path=str(project.resolve()), goal_instance_id=ids["goal_instance_id"],
            channel_id="agent.codex", agent_id="codex", executor_endpoint_id="codex",
            adapter_kind="codex_app_server", upstream_thread_id="foreign-thread", upstream_mode="chat")
        source["receipt"]["original_task_request"]["session_id"] = foreign["session_id"]
    elif change == "fingerprint":
        source["expected_state_fingerprint"] = "changed-confirmed-source"
    elif change == "upstream-thread":
        # Keep every public creation descriptor unchanged while replacing the
        # actual native history owner. A Session id alone cannot certify the
        # original conversation, even when Goal/Task/lifetime still match.
        path = controller.store._session_path(ids["session_id"])
        session = json.loads(path.read_text())
        session["upstream_thread_id"] = "replacement-native-thread"
        path.write_text(json.dumps(session))
        assert source["receipt"]["original_task_request"] == creation["receipt"]["original_task_request"]
    else:
        # Missing WIP provenance is unknown; restart cannot upgrade it from
        # whichever native binding currently occupies the original Session.
        source["checkpoint"]["steps"]["first_session_opened"].pop("upstream_thread_id")
    actions._write(payload)  # Only corrupt this disposable attack fixture.
    wakes = []
    monkeypatch.setattr(controller, "resume_session_queue", lambda **kwargs: wakes.append(kwargs))
    controller.resume_creation_requests()
    assert wakes == []
    status, observed = request(f"/api/chat/sessions/{ids['session_id']}/native-turn?original_task=true&agent_id=codex")
    assert status == 200 and observed["task"]["state"] == "unbound", observed
    assert observed["task"]["reason"] and "creation_request" not in observed["task"]
    assert capture.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_no_intent_empty_queue_does_not_read_canonical_creation_acceptance(environment, cart_host, monkeypatch, provider):
    environment[0](provider)
    _, _, controller, capture, project, _ = cart_host
    _, ids, _ = create_requested_task(cart_host, requested=False)
    before = capture.read_bytes()
    def unexpected_acceptance(**_kwargs):
        raise AssertionError("an empty ordinary queue must not discover canonical creation acceptance")
    monkeypatch.setattr("loopx.control_plane.goals.acceptance.inspect_goal_acceptance", unexpected_acceptance)
    controller.resume_session_queue(session_id=ids["session_id"], work_dir=project,
        objective="Continue the existing conversation.")
    for thread in list(controller.session_queue_threads.values()):
        thread.join(timeout=2)
    assert ids["session_id"] not in controller.session_queue_workers
    assert capture.read_bytes() == before
    cold_read = subprocess.run([sys.executable, "-c", '''
import builtins
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from loopx.chat_action_store import ChatActionStore
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.native_chat.creation_queue import consume_creation_request, resume_creation_requests
store = ChatSessionStore(Path(sys.argv[1]), startup_maintenance=False)
actions = ChatActionStore(Path(sys.argv[2]))
original_import = builtins.__import__
def feature_off_import(name, *args, **kwargs):
    if name.endswith(("steward_managed_mode", "control_plane.effect_runtime", "control_plane.goals.acceptance")):
        raise AssertionError("feature-off queue imported a new creation admission owner: " + name)
    return original_import(name, *args, **kwargs)
def unexpected_wake(**kwargs):
    raise AssertionError("a historical no-intent creation woke a native queue")
builtins.__import__ = feature_off_import
controller = SimpleNamespace(store=store, original_task_action_store=actions,
    closed=threading.Event(), resume_session_queue=unexpected_wake)
assert consume_creation_request(controller, sys.argv[3]) is False
resume_creation_requests(controller)
''', str(controller.store.root.parent), str(controller.original_task_action_store.root), ids["session_id"]],
        capture_output=True, text=True, timeout=15)
    assert cold_read.returncode == 0, cold_read.stdout + cold_read.stderr
    assert capture.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["missing-publication", "unfinished-acceptance", "already-started", "foreign-request"])
def test_startup_cannot_redispatch_an_unknown_or_foreign_queued_operation(environment, cart_host, monkeypatch, provider, change):
    environment[0](provider)
    _, request, controller, capture, project, _ = cart_host
    creation, ids, check = create_requested_task(cart_host)
    saved = creation["receipt"]["original_task_request"]
    original_start = controller._start_accepted_turn_worker
    dispatch_prepared = threading.Event()
    def held_dispatch(**kwargs):
        if kwargs["loopx_execution"]:
            dispatch_prepared.set()
            return False
        return original_start(**kwargs)
    monkeypatch.setattr(controller, "_start_accepted_turn_worker", held_dispatch)
    (project / "execute-native").write_text("valid")
    before = capture.read_bytes()
    assert request(f"/api/actions/{check['proposal_id']}/apply", {})[0] == 200
    queued = wait_for_creation_turn(controller, ids["session_id"], saved["operation_id"])
    assert dispatch_prepared.wait(timeout=10)
    # An accepted Turn can be discovered before acceptance settles. Corrupt
    # only the fully prepared crash input under its existing Session fence,
    # so a concurrent acceptance write cannot replace the attack fixture.
    with controller._session_adapter_lock(ids["session_id"]):
        if change == "already-started":
            controller.store.update_turn(ids["session_id"], queued["turn_id"],
                status="starting", started_at="2026-11-15T00:00:00Z")
        else:
            path = controller.store._turn_path(ids["session_id"], queued["turn_id"])
            raw = json.loads(path.read_text())
            assert "_acceptance" not in raw
            if change == "missing-publication":
                raw.pop("upstream_turn_id")
            elif change == "unfinished-acceptance":
                raw["_acceptance"] = {"schema_version": CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA, "phase": "prepared"}
            else:
                raw["loopx_request"]["creation_request"]["session_id"] = "foreign-session"
            path.write_text(json.dumps(raw))  # Only this disposable corruption input.
    wakes = []
    monkeypatch.setattr(controller, "resume_session_queue", lambda **kwargs: wakes.append(kwargs))
    controller.resume_creation_requests()
    assert wakes == []
    assert capture.read_bytes() == before
    assert controller.store.turn_for_client(ids["session_id"], "managed." + saved["operation_id"])["turn_id"] == queued["turn_id"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_pending_creation_rechecks_only_after_a_content_free_signal(environment, cart_host, monkeypatch, provider):
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    environment[0](provider)
    _, _, controller, _, _, _ = cart_host
    original = ManagedSessionTurn._validation_source
    attempted = threading.Event()
    count = []
    def read_current(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        finally:
            if "_drain_session_queue" in threading.current_thread().name:
                count.append(1)
                attempted.set()
    monkeypatch.setattr(ManagedSessionTurn, "_validation_source", read_current)
    creation, ids, _ = create_requested_task(cart_host)
    assert attempted.wait(timeout=10)
    time.sleep(0.1)  # Let the original Turn's terminal signal coalesce.
    before = len(count)
    time.sleep(2.2)  # An old two-second polling implementation would reread.
    assert len(count) == before
    controller.turn_release_wakeup.notify()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(count) == before:
        time.sleep(0.02)
    assert len(count) > before
    assert controller.store.turn_for_client(ids["session_id"],
        "managed." + creation["receipt"]["original_task_request"]["operation_id"]) is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_creation_terminal_during_queue_read_keeps_the_original_ordinary_followup(environment, cart_host, monkeypatch, provider):
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    environment[0](provider)
    _, request, controller, capture, project, runtime = cart_host
    # Retain real acceptance and FIFO state while controlling only dispatch.
    # The queue worker must observe the native completion at the second read,
    # after it has already cached the original queued operation.
    monkeypatch.setattr(controller, "resume_session_queue", lambda **_kwargs: None)
    start = controller._start_accepted_turn_worker
    monkeypatch.setattr(controller, "_start_accepted_turn_worker",
        lambda **kwargs: False if kwargs["loopx_execution"] else start(**kwargs))
    creation, ids, check = create_requested_task(cart_host)
    saved = creation["receipt"]["original_task_request"]
    sid = ids["session_id"]
    assert request(f"/api/actions/{check['proposal_id']}/apply", {})[0] == 200
    native, created = ManagedSessionTurn(controller).submit(sid,
        agent_id=saved["agent_id"], operation_id=saved["operation_id"],
        todo_id=saved["todo_id"], _creation_request=saved)
    assert created and native["status"] == "queued"
    followup, created = controller.enqueue_turn(session_id=sid,
        client_turn_id="ordinary-after-original", message="Read the original results.",
        work_dir=project, objective="Retain the original cart repair.", origin="web")
    assert created and followup["status"] == "queued"
    before = capture.read_bytes()
    original_read = controller.store.turn_for_client
    terminalized = []
    def finish_native_before_consumer_read(session_id, client_id):
        if not terminalized and session_id == sid and client_id == "managed." + saved["operation_id"]:
            terminalized.append(native["turn_id"])
            controller._fail_queue_preparation(sid, native["turn_id"],
                OSError("synthetic native worker exit before consumer read"))
        return original_read(session_id, client_id)
    monkeypatch.setattr(controller.store, "turn_for_client", finish_native_before_consumer_read)
    worker = threading.Thread(target=controller._drain_session_queue,
        kwargs={"session_id": sid, "work_dir": project, "objective": "Retain the original cart repair."})
    controller.session_queue_workers.add(sid)
    controller.session_queue_threads[sid] = worker
    worker.start()
    worker.join(timeout=20)
    assert not worker.is_alive(), "the original queue did not reconcile terminal work"
    assert terminalized == [native["turn_id"]]
    assert controller.store.load_turn(sid, native["turn_id"])["status"] == "failed"
    assert controller.store.load_turn(sid, followup["turn_id"])["status"] == "completed"
    assert controller.store.load_session(sid)["active_turn_id"] is None
    assert sid not in controller.session_queue_workers
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    starts = [row for row in calls if row.get("method") == "turn/start"]
    assert len(starts) == 1  # Only the already requested ordinary follow-up.
    assert starts[0]["params"]["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert "runtimeWorkspaceRoots" not in starts[0]["params"]
    assert not any(row.get("method") == "thread/start" for row in calls)
    assert task_row(runtime, saved["todo_id"])["status"] == "open"
