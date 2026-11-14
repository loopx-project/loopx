"""The original Task confirmation survives interruptions across native writes."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import runpy
from threading import Event

import pytest

from host_process_fixture import write_python_host_fixture
from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from loopx.capabilities.native_chat.machine_defaults import CODEX_CHAT_DEFAULTS_SCHEMA_VERSION
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from test_goal_create_completion import preview_goal
from test_new_goal_storage_defaults import app, environment  # noqa: F401


@pytest.fixture(params=["file", "sqlite"])
def task_start(environment, app, request, monkeypatch):  # noqa: F811
    configure, _, _, project, runtime = environment
    actions, post, service = app
    provider, mode = request.param if isinstance(request.param, tuple) else (request.param, "hard_lease")
    configure(provider, mode=mode)
    goal = preview_goal(post)
    code, applied = post(f"/api/actions/{goal['proposal_id']}/apply", {})
    assert code == 200, applied

    capture = runtime / "task-native-requests.jsonl"
    source = runpy.run_path(str(Path(__file__).parents[2] / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
    fake = write_python_host_fixture(
        runtime / "task-codex",
        source.replace(
            '    method = request.get("method")',
            f'    with open({str(capture)!r}, "a") as stream:\n'
            '        stream.write(json.dumps(request) + "\\n")\n'
            '    method = request.get("method")',
        ),
    )
    codex_home = runtime / "fixture-codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("LOOPX_CHAT_CODEX_HOME", str(codex_home))

    def reopen():
        if service.runtime_controller is not None:
            service.runtime_controller.close()
        chat = ChatSessionStore(runtime)
        controller = ChatRuntimeController(
            store=chat, codex_bin=str(fake), registry_path=service.registry_path,
            project_contexts=ChatProjectContexts([project]),
        )
        service.chat_store, service.runtime_controller = chat, controller
        return chat, controller

    chat, controller = reopen()
    try:
        code, preview = post("/api/actions/preview", {
            "action_kind": "todo.create",
            "summary": "Verify the supporting appendix",
            "idempotency_key": "start-appendix",
            "context": {"kind": "goal", "goal_id": "report"},
            "normalized_parameters": {
                "goal_id": "report", "text": "Verify the supporting appendix",
                "agent_id": "codex", "start_execution": True,
            },
        })
        assert code == 201, preview
        yield actions, post, chat, controller, reopen, runtime, capture, preview["proposal"]["proposal_id"]
    finally:
        service.runtime_controller.close()


def _snapshot(runtime):
    return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="report", include_leases=True)


def _stop_goal(post):
    code, preview = post("/api/actions/preview", {
        "action_kind": "goal.lifecycle",
        "summary": "Stop the report Goal",
        "context": {"kind": "goal_directory"},
        "idempotency_key": "stop-report",
        "normalized_parameters": {"goal_id": "report", "operation": "stop", "reason": "Owner stopped the Goal"},
    })
    assert code == 201, preview
    code, stopped = post(f"/api/actions/{preview['proposal']['proposal_id']}/apply", {})
    assert code == 200 and stopped["proposal"]["status"] == "applied", stopped


@pytest.mark.parametrize("task_start", [(provider, mode) for provider in ("file", "sqlite")
    for mode in ("soft_claim", "hard_lease")], indirect=True)
@pytest.mark.parametrize("stop_first", [True, False], ids=["stop-first", "start-first"])
def test_stop_and_task_start_order(task_start, monkeypatch, stop_first):
    actions, post, chat, controller, _, _, capture, proposal_id = task_start
    path = f"/api/actions/{proposal_id}/apply"
    entered, proceed = Event(), Event()
    submit = controller.submit_turn

    def pause_before_admission(**kwargs):
        entered.set()
        assert proceed.wait(20), "the original confirmation did not resume"
        return submit(**kwargs)

    monkeypatch.setattr(controller, "submit_turn", pause_before_admission)
    with ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(post, path, {})
        try:
            assert entered.wait(10), "the original confirmation did not reach admission"
            if stop_first:
                _stop_goal(post)
        finally:
            proceed.set()
        code, result = original.result(timeout=25)

    steps = actions.load(proposal_id)["checkpoint"]["steps"]
    sid = steps["task_session_opened"]["session_id"]
    client_id = f"task-start-{proposal_id}"
    if stop_first:
        assert code == 400 and "Goal is no longer active" in result["error"], result
        assert chat.turn_for_client(sid, client_id) is None
        assert chat.messages(sid) == []
    else:
        assert code == 202, result
        tid = result["proposal"]["receipt"]["resource_ids"]["turn_id"]
        assert controller.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "completed"
        _stop_goal(post)
        code, replay = post(path, {})
        assert code == 202 and replay["proposal"]["receipt"] == result["proposal"]["receipt"], replay
        assert replay["turn"]["turn_id"] == tid and replay["turn"]["status"] == "completed"

    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(call["method"] == "turn/start" for call in calls) == (0 if stop_first else 1)


def test_concurrent_task_confirmation_preserves_the_inflight_owner(task_start, monkeypatch):
    actions, post, chat, controller, _, runtime, capture, proposal_id = task_start
    path = f"/api/actions/{proposal_id}/apply"
    entered, proceed = Event(), Event()
    save = actions.save_checkpoint

    def pause(proposal_id, *, step, receipt):
        if step == "task_session_opened" and not entered.is_set():
            entered.set()
            assert proceed.wait(15), "the first confirmation did not resume"
        return save(proposal_id, step=step, receipt=receipt)

    monkeypatch.setattr(actions, "save_checkpoint", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(post, path, {})
        try:
            assert entered.wait(10), "the first confirmation did not open its Session"
            code, concurrent = post(path, {})
            assert code == 409 and concurrent["error_code"] == "action_conflict", concurrent
        finally:
            proceed.set()
        code, applied = original.result(timeout=20)
    assert code == 202, applied
    ids = applied["proposal"]["receipt"]["resource_ids"]
    sid, tid = ids["session_id"], ids["turn_id"]
    assert controller.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "completed"
    before = _snapshot(runtime)
    code, replay = post(path, {})
    assert code == 202 and replay["proposal"]["receipt"] == applied["proposal"]["receipt"], replay
    assert _snapshot(runtime) == before
    assert [row["session_id"] for row in chat.session_candidates(
        goal_id="report", agent_id="codex", channel_id=f"task.{ids['todo_id']}",
    )] == [sid]
    calls = [json.loads(line)["method"] for line in capture.read_text().splitlines()]
    assert calls.count("thread/start") == calls.count("turn/start") == 1


@pytest.mark.parametrize("change", ["defaults", "close"])
def test_task_session_is_pinned_before_its_checkpoint(task_start, monkeypatch, change):
    actions, post, chat, controller, reopen, runtime, capture, proposal_id = task_start
    path = f"/api/actions/{proposal_id}/apply"
    save = actions.save_checkpoint

    def interrupt(proposal_id, *, step, receipt):
        if step == "task_session_opened":
            raise OSError("Session persisted before the confirmation checkpoint")
        return save(proposal_id, step=step, receipt=receipt)

    with monkeypatch.context() as faults:
        faults.setattr(actions, "save_checkpoint", interrupt)
        code, failed = post(path, {})
        assert code == 424, failed
    steps = actions.load(proposal_id)["checkpoint"]["steps"]
    todo_id = steps["todo_created"]["todo_id"]
    channel = f"task.{todo_id}"
    assert "task_session_opened" not in steps
    original, = chat.session_candidates(goal_id="report", agent_id="codex", channel_id=channel)
    sid = original["session_id"]
    assert not chat.messages(sid)
    before = _snapshot(runtime)

    if change == "defaults":
        config = {
            "runtime_root": runtime,
            "registry": build_builtin_machine_configuration_registry(),
            "configuration": {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
                "codex_chat": {"schema_version": CODEX_CHAT_DEFAULTS_SCHEMA_VERSION, "model": "fixture-next"},
            }},
        }
        plan = configure_machine_configuration(**config)
        assert configure_machine_configuration(
            **config, execute=True, expected_plan_revision=plan["plan_revision"],
        )["status"] == "applied"
    else:
        assert controller.close_session(sid)
    chat, controller = reopen()

    code, recovered = post(path, {})
    if change == "defaults":
        assert code == 202, recovered
        ids = recovered["proposal"]["receipt"]["resource_ids"]
        assert ids["session_id"] == sid and ids["todo_id"] == todo_id
        assert controller.wait_for_turn(session_id=sid, turn_id=ids["turn_id"], timeout_sec=10)["status"] == "completed"
    else:
        assert code == 400 and "original Task Session is closed" in recovered["error"], recovered
    assert [row["session_id"] for row in chat.session_candidates(
        goal_id="report", agent_id="codex", channel_id=channel,
    )] == [sid]
    calls = [json.loads(line)["method"] for line in capture.read_text().splitlines()]
    assert calls.count("thread/start") == 1
    assert calls.count("turn/start") == (1 if change == "defaults" else 0)
    assert _snapshot(runtime) == before


@pytest.mark.parametrize("interruption", [
    "accepted", "accepted-then-stopped", "finished-then-stopped", "legacy-finished-then-stopped",
])
def test_task_retry_respects_original_turn_and_current_authority(task_start, monkeypatch, interruption):
    actions, post, chat, controller, reopen, runtime, capture, proposal_id = task_start
    path = f"/api/actions/{proposal_id}/apply"
    lost = {}

    def before_dispatch(*, session_id, turn_id, **kwargs):
        lost.update(session_id=session_id, turn_id=turn_id)
        raise OSError("Turn accepted before dispatch")

    save = actions.save_checkpoint

    def after_completion(proposal_id, *, step, receipt):
        if step == "task_session_opened" and interruption.startswith("legacy"):
            # Old confirmations admitted a Turn without this checkpoint.
            return actions.load(proposal_id)
        if step == "execution_started":
            lost.update(receipt)
            assert controller.wait_for_turn(**receipt, timeout_sec=10)["status"] == "completed"
            raise OSError("Turn completed before the confirmation checkpoint")
        return save(proposal_id, step=step, receipt=receipt)

    with monkeypatch.context() as faults:
        if interruption.startswith("accepted"):
            faults.setattr(controller, "_start_accepted_turn_worker", before_dispatch)
        else:
            faults.setattr(actions, "save_checkpoint", after_completion)
        code, failed = post(path, {})
        assert code == 424 and lost, failed
    sid, tid = lost["session_id"], lost["turn_id"]
    observed = chat.turn_for_client(sid, f"task-start-{proposal_id}")
    assert observed["turn_id"] == tid
    assert observed["status"] == ("completed" if "finished" in interruption else "queued")
    if interruption.endswith("stopped"):
        _stop_goal(post)
    before = _snapshot(runtime)
    chat, controller = reopen()

    code, recovered = post(path, {})
    if interruption == "accepted-then-stopped":
        assert code == 400 and "Goal is no longer active" in recovered["error"], recovered
        assert chat.load_turn(sid, tid)["upstream_turn_id"] is None
    else:
        assert code == 202, recovered
        ids = recovered["proposal"]["receipt"]["resource_ids"]
        assert ids["session_id"] == sid and ids["turn_id"] == tid
        assert recovered["turn"]["created"] is False
        if "finished" in interruption:
            assert recovered["turn"]["status"] == "completed"
        assert controller.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "completed"
        code, replay = post(path, {})
        assert code == 202 and replay["proposal"]["receipt"] == recovered["proposal"]["receipt"], replay
        assert replay["turn"]["status"] == "completed" and replay["turn"]["created"] is False
    assert len(chat.session_candidates(goal_id="report", agent_id="codex",
        channel_id=chat.load_session(sid)["channel_id"])) == 1
    assert len([row for row in chat.messages(sid) if row.get("role") == "user"]) == 1
    calls = [json.loads(line)["method"] for line in capture.read_text().splitlines()]
    assert calls.count("thread/start") == 1
    assert calls.count("turn/start") == (0 if interruption == "accepted-then-stopped" else 1)
    assert _snapshot(runtime) == before
