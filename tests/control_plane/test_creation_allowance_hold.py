"""Saved creation allowance uses real HTTP, quota and canonical File/SQLite.

The native transport is scripted; the existing six-check oracle is unchanged.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import threading

import pytest

from test_creation_task_queue import (
    create_requested_task, restarted_host, task_row, wait_for_creation_turn,
)
from test_original_task_entry import cart_host as cart_host
from test_new_goal_storage_defaults import environment as environment
from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
from loopx.configure_goal import configure_goal


@pytest.fixture
def consumption(monkeypatch):
    condition, calls = threading.Condition(), []
    consume = ManagedSessionTurn.consume_creation_request

    def observe(self, creation):
        try:
            return consume(self, creation)
        finally:
            with condition:
                calls.append(deepcopy(creation))
                condition.notify_all()

    monkeypatch.setattr(ManagedSessionTurn, "consume_creation_request", observe)

    def after(previous):
        with condition:
            assert condition.wait_for(lambda: len(calls) > previous, timeout=15)

    return calls, after


def quota(controller, value):
    result = configure_goal(registry_path=controller.registry_path,
        goal_id="cart-repair", quota_compute=value, execute=True)
    assert result["written"] is True, result


def paused_request(host, consumption):
    _, request, controller, capture, project, _ = host
    creation, ids, check = create_requested_task(host)
    saved = creation["receipt"]["original_task_request"]
    quota(controller, 0)
    (project / "execute-native").write_text("valid")
    before = capture.read_bytes()
    count = len(consumption[0])
    status, result = request(f"/api/actions/{check['proposal_id']}/apply", {})
    assert status == 200 and result["turn"] is None, result
    consumption[1](count)
    original = controller.store.turn_for_client(ids["session_id"], "managed." + saved["operation_id"])
    if original is not None:
        original = controller.wait_for_turn(session_id=ids["session_id"], turn_id=original["turn_id"], timeout_sec=20)
    assert original is None, original
    assert capture.read_bytes() == before
    return creation, ids, saved, before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("resume", ["signal", "restart"])
def test_saved_creation_waits_for_actual_quota_then_runs_same_operation_once(
    environment, cart_host, consumption, provider, resume
):
    environment[0](provider)
    actions, request, controller, capture, project, runtime = cart_host
    creation, ids, saved, before = paused_request(cart_host, consumption)
    sid, todo = ids["session_id"], saved["todo_id"]
    original_session = controller.store.load_session(sid)
    checker_hash = hashlib.sha256((project / "checks.py").read_bytes()).hexdigest()
    assert task_row(runtime, todo)["status"] == "open"
    for value in (0, 1):
        if value:
            quota(controller, value)
        status, view = request(f"/api/chat/sessions/{sid}/native-turn?original_task=true&agent_id=codex")
        assert status == 200 and view["task"]["creation_request"] == saved, view
        assert view["task"]["creation_turn"] is None
        assert controller.store.turn_for_client(sid, "managed." + saved["operation_id"]) is None
        assert capture.read_bytes() == before
    if resume == "signal":
        controller.turn_release_wakeup.notify()
        turn = wait_for_creation_turn(controller, sid, saved["operation_id"])
        finished = controller.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=20)
    else:
        with restarted_host(cart_host) as (restored, _):
            turn = wait_for_creation_turn(restored, sid, saved["operation_id"])
            finished = restored.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=30)
            assert restored.store.load_session(sid)["upstream_thread_id"] == original_session["upstream_thread_id"]
            restored.turn_release_wakeup.notify()
            restored.resume_creation_requests()
            assert restored.store.turn_for_client(sid, "managed." + saved["operation_id"])["turn_id"] == turn["turn_id"]
    assert finished["response"]["native_loopx"]["status"] == "committed", finished
    assert finished["loopx_request"]["creation_request"] == saved
    assert finished["response"]["native_loopx"]["effects"]["quota_spent"] is True
    assert task_row(runtime, todo)["status"] == "done"
    assert hashlib.sha256((project / "checks.py").read_bytes()).hexdigest() == checker_hash
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    assert sum(row.get("method") == "turn/start" for row in calls) == 1
    assert not any(row.get("method") == "thread/start" for row in calls)
    assert actions.load(creation["proposal_id"])["receipt"]["original_task_request"] == saved


def assert_pending(host, saved, before):
    _, _, controller, capture, _, runtime = host
    assert controller.store.turn_for_client(saved["session_id"], "managed." + saved["operation_id"]) is None
    assert capture.read_bytes() == before
    assert task_row(runtime, saved["todo_id"])["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("source", ["missing", "changed"])
def test_actual_validation_source_refusal_retains_unstarted_creation(
    environment, cart_host, consumption, provider, source
):
    environment[0](provider)
    _, _, controller, _, project, _ = cart_host
    _, _, saved, before = paused_request(cart_host, consumption)
    checker = project / "checks.py"
    if source == "missing":
        checker.unlink()
    else:
        checker.write_text(checker.read_text() + "\n# A changed verifier requires new owner approval.\n")
    quota(controller, 1)
    count = len(consumption[0])
    controller.turn_release_wakeup.notify()
    consumption[1](count)
    assert_pending(cart_host, saved, before)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_goal_commit_guard_refusal_leaves_saved_creation_pending(
    environment, cart_host, consumption, monkeypatch, provider
):
    from loopx import attached_session
    environment[0](provider)
    _, _, controller, _, _, _ = cart_host
    _, _, saved, before = paused_request(cart_host, consumption)

    @contextmanager
    def refuse():
        raise ValueError("the original Goal commit guard refused publication")
        yield

    monkeypatch.setattr(attached_session, "managed_session_goal_commit", lambda **_: refuse)
    quota(controller, 1)
    count = len(consumption[0])
    controller.turn_release_wakeup.notify()
    consumption[1](count)
    assert_pending(cart_host, saved, before)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quota_drop_after_acceptance_keeps_same_turn_queued_until_fresh_admission(
    environment, cart_host, consumption, monkeypatch, provider
):
    environment[0](provider)
    _, _, controller, capture, _, runtime = cart_host
    _, _, saved, before = paused_request(cart_host, consumption)
    accept = controller.store.accept_managed_turn
    dropped = False

    def quota_drop(*args, **kwargs):
        nonlocal dropped
        accepted = accept(*args, **kwargs)
        if not dropped and kwargs.get("client_turn_id") == "managed." + saved["operation_id"]:
            dropped = True
            quota(controller, 0)
        return accepted

    monkeypatch.setattr(controller.store, "accept_managed_turn", quota_drop)
    quota(controller, 1)
    count = len(consumption[0])
    controller.turn_release_wakeup.notify()
    consumption[1](count)
    queued = controller.store.turn_for_client(saved["session_id"], "managed." + saved["operation_id"])
    assert queued is not None and queued["status"] == "queued", queued
    assert queued["started_at"] is None and queued["upstream_turn_id"] is None
    assert queued["delta_count"] == 0 and "_acceptance" not in queued
    assert capture.read_bytes() == before
    assert task_row(runtime, saved["todo_id"])["status"] == "open"
    count = len(consumption[0])
    controller.turn_release_wakeup.notify()
    consumption[1](count)
    held = controller.store.turn_for_client(saved["session_id"], "managed." + saved["operation_id"])
    assert held["turn_id"] == queued["turn_id"] and held["status"] == "queued", held
    assert held["started_at"] is None and held["upstream_turn_id"] is None
    assert capture.read_bytes() == before
    quota(controller, 1)
    controller.turn_release_wakeup.notify()
    finished = controller.wait_for_turn(session_id=saved["session_id"], turn_id=queued["turn_id"], timeout_sec=20)
    assert finished["response"]["native_loopx"]["status"] == "committed", finished
    assert finished["loopx_request"]["creation_request"] == saved
    assert controller.store.turn_for_client(saved["session_id"], "managed." + saved["operation_id"])["turn_id"] == queued["turn_id"]
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    assert sum(row.get("method") == "turn/start" for row in calls) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_goal_wide_run_cannot_admit_another_todo_for_saved_creation(
    environment, cart_host, consumption, monkeypatch, provider
):
    from loopx.control_plane.quota import live_decision
    environment[0](provider)
    _, _, controller, _, _, _ = cart_host
    _, _, saved, before = paused_request(cart_host, consumption)
    build = live_decision.build_live_quota_should_run_decision

    def different_selected_todo(*args, **kwargs):
        decision = build(*args, **kwargs)
        assert decision["ok"] is True and decision["should_run"] is True
        decision["selected_todo"]["todo_id"] = "todo_independent_work"
        return decision

    monkeypatch.setattr(live_decision, "build_live_quota_should_run_decision", different_selected_todo)
    quota(controller, 1)
    count = len(consumption[0])
    controller.turn_release_wakeup.notify()
    consumption[1](count)
    assert_pending(cart_host, saved, before)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_actual_dependency_wait_cannot_borrow_another_runnable_todo(
    environment, cart_host, consumption, provider
):
    from loopx.capabilities.native_chat.steward_commands import native_command
    environment[0](provider)
    _, _, controller, capture, _, runtime = cart_host
    _, _, saved, before = paused_request(cart_host, consumption)
    known = {row["todo_id"] for row in native_command(controller,
        ["todo", "list", "--goal-id", "cart-repair"])["todos"]}
    native_command(controller, ["todo", "add", "--goal-id", "cart-repair", "--role", "agent",
        "--text", "Complete the independent prerequisite", "--claimed-by", "codex"])
    current = native_command(controller, ["todo", "list", "--goal-id", "cart-repair"])["todos"]
    prerequisite, = {row["todo_id"] for row in current} - known
    native_command(controller, ["todo", "update", "--goal-id", "cart-repair", "--todo-id", saved["todo_id"],
        "--agent-id", "codex", "--status", "deferred", "--reason", "Wait for the actual prerequisite",
        "--resume-when", "todo_done:" + prerequisite])
    quota(controller, 1)
    owner = ManagedSessionTurn(controller)
    request = owner._request(saved["session_id"], saved["agent_id"], saved["operation_id"], saved["todo_id"])
    assert owner._creation_allowance(request) is False
    count = len(consumption[0])
    controller.turn_release_wakeup.notify()
    consumption[1](count)
    assert controller.store.turn_for_client(saved["session_id"], "managed." + saved["operation_id"]) is None
    assert capture.read_bytes() == before
    assert task_row(runtime, saved["todo_id"])["status"] == "deferred"
    assert task_row(runtime, prerequisite)["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_source_exception_after_acceptance_preserves_original_unstarted_turn(
    environment, cart_host, consumption, monkeypatch, provider
):
    environment[0](provider)
    _, _, controller, capture, project, _ = cart_host
    _, _, saved, before = paused_request(cart_host, consumption)
    checker = project / "checks.py"
    checker_bytes = checker.read_bytes()
    accept = controller.store.accept_managed_turn

    def missing_after_accept(*args, **kwargs):
        accepted = accept(*args, **kwargs)
        if kwargs.get("client_turn_id") == "managed." + saved["operation_id"]:
            checker.unlink()
        return accepted

    monkeypatch.setattr(controller.store, "accept_managed_turn", missing_after_accept)
    quota(controller, 1)
    count = len(consumption[0])
    controller.turn_release_wakeup.notify()
    consumption[1](count)
    queued = controller.store.turn_for_client(saved["session_id"], "managed." + saved["operation_id"])
    assert queued is not None and queued["status"] == "queued", queued
    assert queued["started_at"] is None and queued["upstream_turn_id"] is None
    assert queued["delta_count"] == 0 and "_acceptance" not in queued
    assert capture.read_bytes() == before
    count = len(consumption[0])
    controller.turn_release_wakeup.notify()
    consumption[1](count)
    held = controller.store.turn_for_client(saved["session_id"], "managed." + saved["operation_id"])
    assert held["turn_id"] == queued["turn_id"] and held["status"] == "queued", held
    assert held["started_at"] is None and held["upstream_turn_id"] is None
    assert capture.read_bytes() == before
    checker.write_bytes(checker_bytes)
    controller.turn_release_wakeup.notify()
    finished = controller.wait_for_turn(session_id=saved["session_id"], turn_id=queued["turn_id"], timeout_sec=20)
    assert finished["response"]["native_loopx"]["status"] == "committed", finished
    assert finished["loopx_request"]["creation_request"] == saved


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_final_native_quota_refusal_cannot_be_replaced_by_preparation_read(
    environment, cart_host, consumption, monkeypatch, provider
):
    environment[0](provider)
    _, _, controller, capture, _, runtime = cart_host
    _, _, saved, before = paused_request(cart_host, consumption)
    native = controller.adapters[saved["session_id"]].session
    write = native._write
    guard_boundary_reached = False

    def pause_before_native_guard(payload, **kwargs):
        nonlocal guard_boundary_reached
        if payload.get("method") == "turn/start":
            guard_boundary_reached = True
            quota(controller, 0)
        return write(payload, **kwargs)

    monkeypatch.setattr(native, "_write", pause_before_native_guard)
    quota(controller, 1)
    controller.turn_release_wakeup.notify()
    turn = wait_for_creation_turn(controller, saved["session_id"], saved["operation_id"])
    finished = controller.wait_for_turn(session_id=saved["session_id"], turn_id=turn["turn_id"], timeout_sec=20)
    result = finished["response"]["native_loopx"]
    assert guard_boundary_reached is True
    assert result["ok"] is False and result["error_code"] == "stale_goal_authority", result
    assert all(value is None for value in result["effects"].values()), result
    recovery = result["journal_observation"]["recovery_decision"]
    assert recovery["action"] == "blocked" and recovery["can_continue"] is False
    assert recovery["reinvoke_host"] is False and recovery["reason"] == "host_outcome_unknown"
    assert task_row(runtime, saved["todo_id"])["status"] == "open"
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    assert not any(row.get("method") == "turn/start" for row in calls)
    after = capture.read_bytes()
    quota(controller, 1)
    controller.turn_release_wakeup.notify()
    controller.resume_creation_requests()
    assert controller.store.turn_for_client(saved["session_id"], "managed." + saved["operation_id"])["turn_id"] == turn["turn_id"]
    assert capture.read_bytes() == after
