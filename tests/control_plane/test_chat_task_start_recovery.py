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
from loopx.chat import apply_todo_review_preview, build_todo_review_preview
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.mutation_api import add_goal_todo, update_goal_todo
from tests.control_plane.canonical_authority_fixture import (
    change_unrelated_goal_title, ensure_unrelated_goal_in_registry, promoted_create_fixture,
)
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

    def pause(proposal_id, *, step, receipt, **kwargs):
        if step == "task_session_opened" and not entered.is_set():
            entered.set()
            assert proceed.wait(15), "the first confirmation did not resume"
        return save(proposal_id, step=step, receipt=receipt, **kwargs)

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


def test_task_start_recovers_after_unrelated_todo_write(task_start, monkeypatch):
    actions, post, _, controller, _, runtime, capture, proposal_id = task_start
    path = f"/api/actions/{proposal_id}/apply"
    save = actions.save_checkpoint

    def interrupt(proposal_id, *, step, receipt, **kwargs):
        stored = save(proposal_id, step=step, receipt=receipt, **kwargs)
        if step == "todo_created":
            raise OSError("Interrupted before the managed claim")
        return stored

    with monkeypatch.context() as fault:
        fault.setattr(actions, "save_checkpoint", interrupt)
        code, result = post(path, {})
    assert code == 424, result
    original = actions.load(proposal_id)["checkpoint"]["steps"]["todo_created"]
    other = add_goal_todo(registry_path=controller.registry_path, runtime_root_arg=str(runtime),
        goal_id="report", text="Follow up on the separate budget check", role="agent")
    before = {row["todo_id"]: row for row in _snapshot(runtime)["todos"]}
    assert before[other["todo_id"]].get("claimed_by") is None
    code, applied = post(path, {})
    assert code == 202, applied
    receipt = applied["proposal"]["receipt"]
    assert receipt["canonical_receipt"] == original["canonical_receipt"]
    ids = receipt["resource_ids"]
    assert ids["todo_id"] == original["todo_id"]
    assert controller.wait_for_turn(session_id=ids["session_id"], turn_id=ids["turn_id"], timeout_sec=10)["status"] == "completed"
    final = _snapshot(runtime)
    rows = {row["todo_id"]: row for row in final["todos"]}
    assert rows[original["todo_id"]].get("claimed_by") == "codex"
    assert {key: row for key, row in rows.items() if key != original["todo_id"]} == {
        key: row for key, row in before.items() if key != original["todo_id"]
    }
    assert post(path, {})[1]["proposal"]["receipt"] == receipt
    assert _snapshot(runtime) == final
    calls = [json.loads(line)["method"] for line in capture.read_text().splitlines()]
    assert calls.count("turn/start") == 1


@pytest.mark.parametrize("interruption", ["queued", "todo_created"])
def test_task_start_recovers_after_unrelated_goal_title_edit(task_start, monkeypatch, interruption):
    actions, post, chat, controller, reopen, runtime, capture, proposal_id = task_start
    unrelated_goal_id = ensure_unrelated_goal_in_registry(controller.registry_path, "report")
    path = f"/api/actions/{proposal_id}/apply"
    created = None
    original_receipt = None

    if interruption == "queued":
        monkeypatch.setattr(controller, "_start_accepted_turn_worker", lambda **kwargs: True)
        code, accepted = post(path, {})
        assert code == 202 and accepted["proposal"]["status"] == "applied", accepted
        original_receipt = accepted["proposal"]["receipt"]
        original_ids = original_receipt["resource_ids"]
        assert chat.load_turn(original_ids["session_id"], original_ids["turn_id"])["status"] == "queued"
    else:
        save = actions.save_checkpoint

        def interrupt(proposal_id, *, step, receipt, **kwargs):
            saved = save(proposal_id, step=step, receipt=receipt, **kwargs)
            if step == "todo_created":
                raise OSError("Interrupted before the managed claim")
            return saved

        with monkeypatch.context() as fault:
            fault.setattr(actions, "save_checkpoint", interrupt)
            code, failed = post(path, {})
        assert code == 424, failed
        created = actions.load(proposal_id)["checkpoint"]["steps"]["todo_created"]

    change_unrelated_goal_title(controller.registry_path, unrelated_goal_id)
    chat, controller = reopen()
    code, recovered = post(path, {})
    assert code == 202, recovered
    receipt = recovered["proposal"]["receipt"]
    ids = receipt["resource_ids"]
    if created is not None:
        todo_id = created["todo_id"]
        assert ids["todo_id"] == todo_id and receipt["canonical_receipt"] == created["canonical_receipt"]
    else:
        assert receipt == original_receipt
        assert ids["session_id"] == original_ids["session_id"]
        assert ids["turn_id"] == original_ids["turn_id"]
    assert controller.wait_for_turn(
        session_id=ids["session_id"], turn_id=ids["turn_id"], timeout_sec=10,
    )["status"] == "completed"
    assert post(path, {})[1]["proposal"]["receipt"] == receipt
    calls = [json.loads(line)["method"] for line in capture.read_text().splitlines()]
    assert calls.count("turn/start") == 1


@pytest.mark.parametrize("change", ["defaults", "close"])
def test_task_session_is_pinned_before_its_checkpoint(task_start, monkeypatch, change):
    actions, post, chat, controller, reopen, runtime, capture, proposal_id = task_start
    path = f"/api/actions/{proposal_id}/apply"
    save = actions.save_checkpoint

    def interrupt(proposal_id, *, step, receipt, **kwargs):
        if step == "task_session_opened":
            raise OSError("Session persisted before the confirmation checkpoint")
        return save(proposal_id, step=step, receipt=receipt, **kwargs)

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

    def after_completion(proposal_id, *, step, receipt, **kwargs):
        if step == "task_session_opened" and interruption.startswith("legacy"):
            # Old confirmations admitted a Turn without this checkpoint.
            return actions.load(proposal_id)
        if step == "execution_started":
            lost.update(receipt)
            assert controller.wait_for_turn(**receipt, timeout_sec=10)["status"] == "completed"
            raise OSError("Turn completed before the confirmation checkpoint")
        return save(proposal_id, step=step, receipt=receipt, **kwargs)

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


def _legacy_created_task(actions, registry_path, example_id):
    """Write the pre-preparation creation receipt with its original operation ID."""
    params = actions.load(example_id)["normalized_parameters"]
    intent = dict(registry_path=registry_path, goal_id=params["goal_id"], text=params["text"])
    preview = build_todo_review_preview(**intent)
    proposal = actions.create_preview(
        action_kind="todo.create", summary=params["text"], normalized_parameters=params,
        context={"kind": "goal", "goal_id": params["goal_id"]}, expected_state_fingerprint=preview["preview_id"],
        permission_classification="durable_write", validation_evidence=[], available_transitions=["apply", "cancel"],
        idempotency_key="legacy-start-appendix",
    )
    proposal_id = proposal["proposal_id"]
    actions.start_apply(proposal_id)
    receipt = apply_todo_review_preview(**intent, preview_id=preview["preview_id"])["receipt"]
    todo_id = receipt["todo_id"]
    actions.save_checkpoint(proposal_id, step="todo_created", receipt={"todo_id": todo_id, "canonical_receipt": receipt})
    return params, proposal_id, receipt


@pytest.mark.parametrize(("task_start", "interruption"), [
    pytest.param((provider, "soft_claim" if stage == "turn-completed-v23" else "hard_lease"), stage,
        id=f"{provider}-{stage}")
    for provider in ("file", "sqlite") for stage in (
        "claim-committed", "turn-completed", "turn-completed-v23", "claim-missing", "edited-before-claim",
    )
], indirect=["task_start"])
def test_legacy_task_confirmation_recovers_the_original_execution(task_start, environment, interruption):  # noqa: F811
    actions, post, _, controller, reopen, runtime, capture, example_id = task_start
    params, proposal_id, receipt = _legacy_created_task(actions, controller.registry_path, example_id)
    todo_id = receipt["todo_id"]
    owner = params["agent_id"]
    completed = interruption.startswith("turn-completed")
    if interruption == "turn-completed-v23":
        # 2.3.0 assigned the Todo without a managed claim before submitting its Turn.
        update_goal_todo(registry_path=controller.registry_path, goal_id=params["goal_id"], todo_id=todo_id,
            claimed_by=owner, agent_id=owner, authority_reason="owner-confirmed typed Chat action", dry_run=False)
    elif interruption in {"claim-committed", "turn-completed"}:
        # The old native claim request had the same operation ID without a reviewed revision.
        claim = update_goal_todo(registry_path=controller.registry_path, goal_id=params["goal_id"],
            todo_id=todo_id, agent_id=owner, claimed_by=owner, claim_only=True, manage_execution=True,
            claim_operation_id=f"chat-task-start:{proposal_id}", dry_run=False)
        assert claim["status"] == "applied"
    if completed or interruption == "claim-missing":
        actions.save_checkpoint(proposal_id, step="todo_assigned", receipt={"todo_id": todo_id, "agent_id": owner})
    if interruption == "edited-before-claim":
        update_goal_todo(registry_path=controller.registry_path, goal_id=params["goal_id"],
            todo_id=todo_id, text="Review a different appendix", agent_id=owner, dry_run=False)
    if completed:
        _, _, _, project, _ = environment
        session, _ = controller.open_session(goal_id=params["goal_id"], agent_id=owner, work_dir=project,
            objective=params["text"], mode="resume_latest", channel_id=f"task.{todo_id}", agent_goal_id=params["goal_id"])
        sid = session["session_id"]
        if interruption != "turn-completed-v23":
            actions.save_checkpoint(proposal_id, step="task_session_opened", receipt={"session_id": sid})
        turn, _ = controller.submit_turn(session_id=sid, client_turn_id=f"task-start-{proposal_id}",
            message=params["text"], work_dir=project, objective=params["text"])
        identity = {"session_id": sid, "turn_id": turn["turn_id"]}
        assert controller.wait_for_turn(**identity, timeout_sec=10)["status"] == "completed"
        actions.save_checkpoint(proposal_id, step="execution_started", receipt=identity)
        _stop_goal(post)
    actions.mark_failed(proposal_id, error_code="canonical_action_failed", message="Interrupted before confirmation")
    before = _snapshot(runtime)
    chat, controller = reopen()
    path = f"/api/actions/{proposal_id}/apply"

    code, result = post(path, {})
    if interruption in {"claim-missing", "edited-before-claim"}:
        assert code == 409, result
        assert not capture.exists()
    else:
        assert code == 202, result
        applied = result["proposal"]["receipt"]
        ids = applied["resource_ids"]
        assert applied["canonical_receipt"] == receipt and ids["todo_id"] == todo_id
        resumed = {"session_id": ids["session_id"], "turn_id": ids["turn_id"]}
        if completed:
            assert resumed == identity and result["turn"]["created"] is False
        assert controller.wait_for_turn(**resumed, timeout_sec=10)["status"] == "completed"
        assert post(path, {})[1]["proposal"]["receipt"] == applied
        assert [row["session_id"] for row in chat.session_candidates(
            goal_id=params["goal_id"], agent_id=owner, channel_id=f"task.{todo_id}",
        )] == [ids["session_id"]]
        methods = [json.loads(line)["method"] for line in capture.read_text().splitlines()]
        assert methods.count("thread/start") == methods.count("turn/start") == 1
    assert _snapshot(runtime) == before


@pytest.mark.parametrize("recovery", ["active", "stopped", "legacy", "retargeted"])
def test_applied_confirmation_retries_the_original_undispatched_turn(task_start, monkeypatch, tmp_path, recovery):
    actions, request, chat, controller, reopen, runtime, capture, proposal_id = task_start
    if recovery == "legacy":
        _, proposal_id, _ = _legacy_created_task(actions, controller.registry_path, proposal_id)
        actions.mark_failed(proposal_id, error_code="canonical_action_failed", message="Interrupted after creation")
    path = f"/api/actions/{proposal_id}"
    stopped = recovery == "stopped"
    save = actions.save_checkpoint

    def save_legacy_checkpoint(proposal_id, *, step, receipt):
        if step == "todo_created":
            receipt = {key: value for key, value in receipt.items() if key != "source"}
        return save(proposal_id, step=step, receipt=receipt)

    # The server persisted acceptance and returned it, but its scheduled worker
    # never wrote the first durable start fact before the process went away.
    with monkeypatch.context() as faults:
        faults.setattr(controller, "_start_accepted_turn_worker", lambda **kwargs: True)
        if recovery == "legacy":
            faults.setattr(actions, "save_checkpoint", save_legacy_checkpoint)
        code, original = request(f"{path}/apply", {})
    assert code == 202 and original["proposal"]["status"] == "applied", original
    receipt = original["proposal"]["receipt"]
    ids = receipt["resource_ids"]
    sid, tid = ids["session_id"], ids["turn_id"]
    queued = chat.load_turn(sid, tid)
    assert queued["status"] == "queued" and queued["started_at"] is None
    if stopped:
        _stop_goal(request)
    before = _snapshot(runtime)
    chat, controller = reopen()

    # Looking at the original confirmation or its goal's list must never start it.
    code, detail = request(path)
    assert code == 200 and detail["proposal"]["receipt"] == receipt
    code, history = request("/api/actions?goal_id=report")
    assert code == 200, history
    listed = next(row for row in history["proposals"] if row["proposal_id"] == proposal_id)
    assert chat.load_turn(sid, tid) == queued
    assert all(json.loads(line)["method"] != "turn/start" for line in capture.read_text().splitlines())
    if recovery == "retargeted":
        provider = before["source_authority"].removesuffix("_v0")
        _, other_runtime, _ = promoted_create_fixture(
            tmp_path / "other", provider=provider, goal_id="report",
            registered_agents=["codex"], handoff_mode="hard_lease",
        )
        other_before = _snapshot(other_runtime)
        registry = controller.registry_path
        original_registry = registry.read_bytes()
        registration = json.loads(original_registry)
        registration["common_runtime_root"] = str(other_runtime)
        registry.write_text(json.dumps(registration))
        try:
            code, blocked = request(f"{path}/apply", {})
            assert code == 400 and "Goal source changed" in blocked["error"], blocked
            assert chat.load_turn(sid, tid) == queued
            assert _snapshot(runtime) == before
            assert _snapshot(other_runtime) == other_before
        finally:
            registry.write_bytes(original_registry)

    if stopped:
        code, retried = request(f"{path}/apply", {})
        assert code == 400 and "Goal is no longer active" in retried["error"], retried
        assert chat.load_turn(sid, tid) == queued
    else:
        entered, proceed = Event(), Event()
        start = controller._start_accepted_turn_worker

        def pause_ready_dispatch(**kwargs):
            entered.set()
            assert proceed.wait(20), "the original retry did not resume"
            return start(**kwargs)

        monkeypatch.setattr(controller, "_start_accepted_turn_worker", pause_ready_dispatch)
        with ThreadPoolExecutor(max_workers=1) as pool:
            original_retry = pool.submit(request, f"{path}/apply", {})
            try:
                assert entered.wait(10), "the original retry did not reach dispatch"
                code, concurrent = request(f"{path}/apply", {})
                assert code == 409 and concurrent["error_code"] == "action_conflict", concurrent
            finally:
                proceed.set()
            code, retried = original_retry.result(timeout=25)
        assert code == 202 and retried["proposal"]["receipt"] == receipt, retried
        assert retried["turn"]["created"] is False
        assert retried["turn"]["turn_id"] == tid
        assert controller.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=3)["status"] == "completed"
        code, again = request(f"{path}/apply", {})
        assert code == 202 and again["proposal"]["receipt"] == receipt
        assert again["turn"]["status"] == "completed"
        code, complete = request(path)
        assert code == 200 and "task_execution_dispatch_pending" not in complete["proposal"]

    assert detail["proposal"]["task_execution_dispatch_pending"] is True
    assert listed["task_execution_dispatch_pending"] is True
    assert "task_execution_dispatch_pending" not in actions.load(proposal_id)
    assert actions.load(proposal_id)["receipt"] == receipt
    assert _snapshot(runtime) == before
    assert [row["session_id"] for row in chat.session_candidates(
        goal_id="report", agent_id="codex", channel_id=f"task.{ids['todo_id']}",
    )] == [sid]
    assert len([row for row in chat.messages(sid) if row.get("role") == "user"]) == 1
    calls = [json.loads(line)["method"] for line in capture.read_text().splitlines()]
    assert calls.count("thread/start") == 1
    assert calls.count("turn/start") == (0 if stopped else 1)
