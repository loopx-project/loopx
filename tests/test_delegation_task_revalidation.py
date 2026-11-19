"""Version-bound requester decisions through real Turn acceptance and durable IO."""
import asyncio
import http.client
import json
import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread, get_ident

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from loopx.collaboration_mcp import Delegations
from test_local_delegation import brief, service as delegation_service, wait

service = delegation_service


def test_active_rejected_worker_defers_revalidation_until_release(service, monkeypatch):
    """Terminal publication is observable before the original worker releases ownership."""
    from loopx.control_plane.turn_driver.journal_store import turn_journal_path

    root, runner = service
    output = root / "analyst/initial/output.json"
    saved = output.read_bytes()
    output.write_text("{}")
    published, release = Event(), Event()
    observe = runner._observe

    def pause_after_rejection(path, row, status, **facts):
        observe(path, row, status, **facts)
        if status == "rejected":
            published.set()
            assert release.wait(120), "test did not release the terminal worker"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with monkeypatch.context() as worker_patch:
            worker_patch.setattr(runner, "_spawn", lambda _: None)
            worker_patch.setattr(runner, "_observe", pause_after_rejection)
            runner.start("analysis", "analysis-1", brief())
            worker = pool.submit(runner.execute, "analysis-1")
            try:
                assert published.wait(120), "worker did not publish the task failure"
                failed = runner.read("analysis-1")
                assert failed["status"] == "rejected" and failed["worker_active"]
                turn_key = failed["task_failure"]["turn_key"]
                path = runner.path("analysis-1")
                journal = turn_journal_path(runner.root, goal_id=runner.goal_id, turn_key=turn_key)
                original_record, original_journal = path.read_bytes(), journal.read_bytes()
                with monkeypatch.context() as duplicate_patch:
                    def forbidden_effect(*args, **kwargs):
                        pytest.fail("an active operation must not retry validation or write execution state")
                    duplicate_patch.setattr(runner, "_cli", forbidden_effect)
                    duplicate_patch.setattr(runner, "_spawn", forbidden_effect)
                    duplicate_patch.setattr(runner, "_fenced_write", forbidden_effect)
                    duplicate = runner.revalidate("analysis-1")
                assert duplicate["operation_id"] == "analysis-1"
                assert duplicate["task_failure"] == failed["task_failure"]
                assert duplicate["worker_active"] is True
                assert path.read_bytes() == original_record
                assert journal.read_bytes() == original_journal
                assert (root / "analyst/initial/host-invocations").read_text().strip() == "1"

                read = runner.read
                def release_after_active_read(operation):
                    result = read(operation)
                    if result["status"] == "rejected" and result["worker_active"]:
                        release.set()
                    return result
                with monkeypatch.context() as readiness_patch:
                    readiness_patch.setattr(runner, "read", release_after_active_read)
                    ready = wait(runner)
                assert ready["worker_active"] is False
            finally:
                release.set()
                worker.result(timeout=30)

    output.write_bytes(saved)
    runner.revalidate("analysis-1")
    accepted = wait(runner)
    assert accepted["status"] == "accepted", accepted
    assert json.loads(runner.path("analysis-1").read_text())["turn_key"] == turn_key
    assert (root / "analyst/initial/host-invocations").read_text().strip() == "1"


def test_revalidation_preserves_an_internal_lock_timeout(service, monkeypatch):
    """Only operation-lock contention is a no-op; retry execution failures stay visible."""
    from loopx.file_lock import LockAcquireTimeoutError

    root, runner = service
    output = root / "analyst/initial/output.json"
    saved = output.read_bytes()
    output.write_text("{}")
    runner.start("analysis", "analysis-1", brief())
    assert wait(runner)["status"] == "rejected"
    output.write_bytes(saved)
    failure = LockAcquireTimeoutError(incident={}, incident_recorded=False, incident_channel="fixture")
    def fail_inside_retry(*args, **kwargs):
        raise failure
    monkeypatch.setattr(runner, "_cli", fail_inside_retry)
    with pytest.raises(LockAcquireTimeoutError) as raised:
        runner.revalidate("analysis-1")
    assert raised.value is failure


@pytest.fixture
def revalidation_http(service):
    from loopx.agent_registry import load_goal_from_registry
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    root, runner = service
    workspace = Path(load_goal_from_registry(runner.registry, runner.goal_id)["repo"])
    config = workspace / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(runner.config.read_bytes())
    runner.config = config
    store = ChatSessionStore(runner.root)
    controller = ChatRuntimeController(store=store, codex_bin="fixture", registry_path=runner.registry)
    store.create_session(goal_id=runner.goal_id, agent_id="codex", channel_id=f"goal.{runner.goal_id}",
                         upstream_thread_id="fixture", session_id="recovery",
                         adapter_kind="codex_app_server", upstream_mode="chat")
    store.update_session("recovery", native_goal={"status": "active"}, loopx_mode={"settings": {
        "agent_id": runner.agent_id, "execution_config": ".loopx/config/delegations.json"}})

    with ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler) as server:
        server.daemon_threads = False
        server.verbose = False
        server.registry_path, server.runtime_root = runner.registry, runner.root
        server.chat_store, server.runtime_controller = store, controller
        serving = Thread(target=server.serve_forever, daemon=True)
        serving.start()

        def post(operation, expected=200):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=60)
            try:
                conn.request("POST", "/api/chat/sessions/recovery/loopx",
                             json.dumps({"operation": operation, "operation_id": "analysis-1"}),
                             {"Content-Type": "application/json",
                              "Origin": f"http://127.0.0.1:{server.server_port}"})
                response = conn.getresponse()
                payload = json.loads(response.read())
                assert response.status == expected, payload
                return payload
            finally:
                conn.close()

        try:
            yield root, runner, post
        finally:
            server.shutdown()
            serving.join(timeout=10)
            controller.close()


@pytest.mark.parametrize("reader_state", ["registered", "registering", "held", "revoked"])
def test_revalidation_survives_a_concurrent_status_read(revalidation_http, monkeypatch, reader_state):
    """Exercise the kernel-acquired, owner-record-not-yet-published interval over HTTP."""
    from loopx import file_lock
    from loopx.control_plane.turn_driver.journal_store import turn_journal_path

    root, runner, post = revalidation_http
    output = root / "analyst/initial/output.json"
    saved = output.read_bytes()
    output.write_text("{}")
    runner.start("analysis", "analysis-1", brief())
    failed = wait(runner)
    turn_key = failed["task_failure"]["turn_key"]
    output.write_bytes(saved)
    path = runner.path("analysis-1")
    journal = turn_journal_path(runner.root, goal_id=runner.goal_id, turn_key=turn_key)
    original_record, original_journal = path.read_bytes(), journal.read_bytes()
    # The last invocation registered as single flight and already released.
    # Until the new reader publishes, this metadata says nothing about it.
    with file_lock.try_exclusive_file_lock(path) as lock:
        assert lock is not None
        lock_stat = lock.stat()
    assert file_lock.lock_holder_liveness(path)[0] == file_lock.LOCK_HOLDER_RELEASED

    read_locked, release_read = Event(), Event()
    persist = file_lock._persist_holder_record
    try_acquire = file_lock._try_acquire_kernel_lock
    reader_thread = None
    collided = False

    def same_lock(stream):
        return os.path.samestat(os.fstat(stream.fileno()), lock_stat)

    def hold_read_record(stream, **kwargs):
        nonlocal reader_thread
        if same_lock(stream) and not read_locked.is_set():
            reader_thread = get_ident()
            if reader_state == "registered":
                persist(stream, **kwargs)
            read_locked.set()
            assert release_read.wait(20), "status read did not reach the revalidation request"
            if reader_state == "registered":
                return
        persist(stream, **kwargs)

    def release_on_second_attempt(stream):
        nonlocal collided
        if not same_lock(stream) or get_ident() == reader_thread:
            return try_acquire(stream)
        if collided and reader_state != "held":
            release_read.set()
            reading.result(timeout=20)
            if reader_state == "revoked":
                config = json.loads(runner.config.read_text())
                config["bindings"][0]["requesters"] = []
                runner.config.write_text(json.dumps(config))
        acquired = try_acquire(stream)
        collided |= not acquired
        return acquired

    with ThreadPoolExecutor(max_workers=1) as pool, monkeypatch.context() as patch:
        patch.setattr(file_lock, "_persist_holder_record", hold_read_record)
        patch.setattr(file_lock, "_try_acquire_kernel_lock", release_on_second_attempt)
        reading = pool.submit(post, "read")
        try:
            assert read_locked.wait(20)
            resumed = post("revalidate", expected=409 if reader_state == "revoked" else 200)
            assert collided, "requests did not contend on the same kernel lock"
            if reader_state == "held":
                assert resumed["status"] == "rejected", resumed
                assert resumed["worker_active"] is True
                assert resumed["task_failure"]["turn_key"] == turn_key
            if reader_state in {"held", "revoked"}:
                assert path.read_bytes() == original_record
                assert journal.read_bytes() == original_journal
        finally:
            release_read.set()
            reading.result(timeout=30)

    if reader_state in {"registered", "registering"}:
        assert resumed["status"] in {"turn_returned", "accepted"}, resumed
        accepted = wait(runner)
        assert accepted["status"] == "accepted", accepted
        assert any(artifact["ref"] == "output.json" for artifact in accepted["artifacts"])
        assert json.loads(path.read_bytes())["turn_key"] == turn_key
    assert (root / "analyst/initial/host-invocations").read_text() == "1"


def test_original_task_revalidation_repairs_output_without_repeating_host(service):
    root, runner = service
    output = root / "analyst/initial/output.json"
    saved = output.read_bytes()
    output.write_text("{}")
    runner.start("analysis", "analysis-1", brief())
    rejected = wait(runner)
    assert rejected["status"] == "rejected", rejected
    assert rejected["task_failure"]["resume_mode"] == "revalidate_cached_result"
    key = rejected["task_failure"]["turn_key"]
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(runner.registry),
        "--runtime-root", str(runner.root), "delegation", "revalidate", "--goal-id", runner.goal_id,
        "--agent-id", runner.agent_id, "--execution-config", str(runner.config),
        "--operation-id", "analysis-1", "--execute"]
    still_failed = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    assert still_failed["status"] == "rejected", still_failed
    assert still_failed["task_failure"]["turn_key"] == key
    output.write_bytes(saved)  # Actual task repair, not a replacement validator.
    async def recheck_mcp():
        params = StdioServerParameters(command=sys.executable, env={
            key: os.environ[key] for key in ("NODE_OPTIONS", "TMPDIR", "TEMP", "TMP")
            if key in os.environ}, args=["-m", "loopx.collaboration_mcp", "--registry", str(runner.registry),
            "--runtime-root", str(runner.root), "--goal-id", runner.goal_id, "--agent-id", runner.agent_id,
            "--workspace", str(root / "lead"), "--execution-config", str(runner.config)])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool("revalidate_delegation", {"operation_id": "analysis-1"})
                assert not result.isError, result
    asyncio.run(recheck_mcp())
    # A Windows MCP host may close its entire Job on disconnect. Reconcile
    # the original committed Turn through the existing independent caller.
    runner.resume("analysis-1")
    accepted = wait(runner)
    assert accepted["status"] == "accepted", json.dumps({"read": accepted, "record": json.loads(runner.path("analysis-1").read_text())})
    assert "task_failure" not in accepted
    assert (root / "analyst/initial/host-invocations").read_text() == "1"
    assert json.loads(runner.path("analysis-1").read_text())["turn_key"] == key
    with pytest.raises(ValueError, match="original independent task failure"):
        runner.revalidate("analysis-1")


@pytest.mark.parametrize("entrypoint", ["resume", "revalidate"])
def test_committed_revalidation_response_loss_recovers_same_turn(service, monkeypatch, entrypoint):
    root, runner = service
    output = root / "analyst/initial/output.json"
    saved = output.read_bytes()
    output.write_text("{}")
    runner.start("analysis", "analysis-1", brief())
    failed = wait(runner)
    turn_key = failed["task_failure"]["turn_key"]
    output.write_bytes(saved)
    cli = runner._cli
    committed = []
    def lose_response(binding, *args, **kwargs):
        result = cli(binding, *args, **kwargs)
        if "--retry-failed-turn" in args:
            assert result["status"] == "committed"
            committed.append(result["receipt"]["settlement_effect_id"])
            raise subprocess.TimeoutExpired("lost committed response", 1)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(runner, "_cli", lose_response)
        with pytest.raises(subprocess.TimeoutExpired):
            runner.revalidate("analysis-1")
    restarted = Delegations(runner.root, runner.registry, runner.goal_id, runner.agent_id, runner.config)
    assert restarted.read("analysis-1")["recovery_required"] is True
    # Revocation and a stop remain stronger than committed-result recovery.
    configured = restarted.config.read_bytes()
    revoked = json.loads(configured)
    revoked["bindings"][0]["requesters"] = []
    restarted.config.write_text(json.dumps(revoked))
    frozen = restarted.path("analysis-1").read_bytes()
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected
    with pytest.raises(EffectRuntimeRejected, match="no delegation grant"):
        getattr(restarted, entrypoint)("analysis-1")
    assert restarted.path("analysis-1").read_bytes() == frozen
    restarted.config.write_bytes(configured)
    stop_path = restarted._stop_path(restarted.path("analysis-1"))
    stop = restarted._new_stop_record(json.loads(frozen), requested_by="lead", worker=None)
    stop_path.write_text(json.dumps(stop))
    with pytest.raises(ValueError):
        getattr(restarted, entrypoint)("analysis-1")
    assert restarted.path("analysis-1").read_bytes() == frozen
    stop_path.unlink()  # Remove only this synthetic injected fence.
    getattr(restarted, entrypoint)("analysis-1")
    accepted = wait(restarted)
    assert accepted["status"] == "accepted", accepted
    assert json.loads(restarted.path("analysis-1").read_text())["turn_key"] == turn_key
    from loopx.control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path
    journal = load_turn_journal(turn_journal_path(runner.root, goal_id=runner.goal_id, turn_key=turn_key))
    assert journal["receipt"]["settlement_effect_id"] == committed[0]
    assert (root / "analyst/initial/host-invocations").read_text().strip() == "1"
    restarted.resume("analysis-1")
    assert restarted.read("analysis-1")["status"] == "accepted"


def test_revalidation_and_committed_recovery_require_current_ancestry(service, monkeypatch):
    from test_delegation_result_use import accepted_chain

    root, runner = service
    request = accepted_chain(root, runner)
    # Use an unfinished Todo; the accepted chain's synthesis already completed
    # its Todo and must not be dispatched again as a new failed task.
    workspace = root / "reviewer/initial"
    (workspace / "declared-input.json").write_bytes(
        (root / "reviewer/corrected/declared-input.json").read_bytes())
    config = json.loads(runner.config.read_text())
    config["bindings"].append({**config["bindings"][0], "id": "failure",
        "agent_id": "reviewer", "todo_id": "todo_reviewer-initial", "workspace": str(workspace)})
    runner.config.write_text(json.dumps(config))
    output = workspace / "output.json"
    saved_output = output.read_bytes()
    output.write_text("{}")
    runner.start("failure", "failed-synthesis", request)
    failed = wait(runner, "failed-synthesis")
    assert failed["status"] == "rejected", failed
    turn_key = failed["task_failure"]["turn_key"]
    output.write_bytes(saved_output)
    ancestor_input = root / "analyst/corrected/declared-input.json"
    saved_input = ancestor_input.read_bytes()
    ancestor_input.write_text("{}")
    frozen = runner.path("failed-synthesis").read_bytes()
    assert runner.read("failed-synthesis")["current_use"]["state"] == "unavailable"
    with pytest.raises(ValueError, match="input version unavailable"):
        runner.revalidate("failed-synthesis")
    assert runner.path("failed-synthesis").read_bytes() == frozen
    ancestor_input.write_bytes(saved_input)

    cli = runner._cli
    committed = []

    def lose_response(binding, *args, **kwargs):
        result = cli(binding, *args, **kwargs)
        if "--retry-failed-turn" in args:
            assert result["status"] == "committed"
            committed.append(result["receipt"]["settlement_effect_id"])
            raise subprocess.TimeoutExpired("lost committed response", 1)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_cli", lose_response)
        with pytest.raises(subprocess.TimeoutExpired):
            runner.revalidate("failed-synthesis")
    restarted = Delegations(runner.root, runner.registry, runner.goal_id, runner.agent_id, runner.config)
    assert restarted.read("failed-synthesis")["recovery_required"] is True
    ancestor_input.write_text("{}")
    frozen = restarted.path("failed-synthesis").read_bytes()
    for entrypoint in ("resume", "revalidate"):
        with pytest.raises(ValueError, match="input version unavailable"):
            getattr(restarted, entrypoint)("failed-synthesis")
        assert restarted.path("failed-synthesis").read_bytes() == frozen
    ancestor_input.write_bytes(saved_input)
    restarted.revalidate("failed-synthesis")
    accepted = wait(restarted, "failed-synthesis")
    assert accepted["status"] == "accepted"
    assert accepted["current_use"]["state"] == "current"
    assert json.loads(restarted.path("failed-synthesis").read_text())["turn_key"] == turn_key
    from loopx.control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path
    journal = load_turn_journal(turn_journal_path(runner.root, goal_id=runner.goal_id, turn_key=turn_key))
    assert journal["receipt"]["settlement_effect_id"] == committed[0]
    assert (workspace / "host-invocations").read_text() == "1"
