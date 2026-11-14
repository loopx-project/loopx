"""Recover the original Chat confirmation against the real Todo providers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Event

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.chat import todo_creation
from loopx.chat_action_store import ActionConflictError, ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.mutation_api import add_goal_todo


@pytest.fixture(params=["file", "sqlite"])
def confirmation(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(
        tmp_path, provider=request.param, registered_agents=["agent-a", "agent-b"],
    )
    service = ChatActionService(store=ChatActionStore(runtime / "chat" / "actions"),
                                registry_path=registry)
    return service, registry, runtime, request.param


def _preview(service, kind, key, *, agent=None):
    parameters = {"goal_id": "goal-a"}
    if kind == "monitor.create":
        parameters.update(agent_id="agent-a", target="Review service availability",
                          target_key="service-health", cadence="2h", timezone="UTC",
                          stop_condition="watch_only")
    else:
        parameters.update(text="Review service availability", priority="P2")
        if agent:
            parameters["agent_id"] = agent
    return service.preview({
        "action_kind": kind, "summary": "Review service availability",
        "normalized_parameters": parameters, "context": {"kind": "goal", "goal_id": "goal-a"},
        "idempotency_key": key,
    })["proposal_id"]


def _snapshot(runtime):
    return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")


def _interrupt_after_create(service, monkeypatch, kind):
    lost = []
    if kind == "todo.create":
        checkpoint = service.store.save_checkpoint

        def save(proposal_id, *, step, receipt, **kwargs):
            if step == "todo_created" and not lost:
                lost.append(receipt["canonical_receipt"])
                raise OSError("interrupted after provider commit")
            return checkpoint(proposal_id, step=step, receipt=receipt, **kwargs)

        monkeypatch.setattr(service.store, "save_checkpoint", save)
    else:
        apply = service.store.apply

        def save(proposal_id, *, current_state_fingerprint, receipt, **kwargs):
            if receipt.get("outcome") == "monitor_created" and not lost:
                lost.append(receipt)
                raise OSError("interrupted after provider commit")
            return apply(proposal_id, current_state_fingerprint=current_state_fingerprint, receipt=receipt, **kwargs)

        monkeypatch.setattr(service.store, "apply", save)
    return lost


@pytest.mark.parametrize("kind", ["todo.create", "monitor.create"])
def test_lost_chat_confirmation_recovers_original_create(confirmation, monkeypatch, kind):
    service, registry, runtime, _ = confirmation
    proposal_id = _preview(service, kind, "original-confirmation")
    lost = _interrupt_after_create(service, monkeypatch, kind)
    confirmed_at = datetime.now(timezone.utc)
    monkeypatch.setattr("loopx.chat_actions.now_utc", lambda: confirmed_at)
    with pytest.raises(OSError, match="after provider commit"):
        service.apply(proposal_id)
    after_commit = _snapshot(runtime)
    assert len(after_commit["todos"]) == 1
    todo = after_commit["todos"][0]
    if kind == "monitor.create":
        assert todo["next_due_at"]

    # Advance the time and registration revision without changing the source.
    registry.write_text(registry.read_text() + "\n")
    monkeypatch.setattr("loopx.chat_actions.now_utc", lambda: confirmed_at + timedelta(minutes=5))
    reopened = ChatActionService(store=ChatActionStore(runtime / "chat" / "actions"),
                                 registry_path=registry)
    recovered = reopened.apply(proposal_id)["proposal"]
    assert recovered["status"] == "applied", recovered
    assert recovered["receipt"]["receipt_id"] == lost[0]["receipt_id"]
    assert recovered["receipt"]["outcome"] == lost[0]["outcome"]
    assert recovered["receipt"]["resource_ids"]["todo_id"] == todo["todo_id"]
    assert _snapshot(runtime) == after_commit
    assert reopened.apply(proposal_id)["proposal"]["receipt"] == recovered["receipt"]


def test_separate_confirmation_cannot_claim_the_original_create(confirmation, monkeypatch):
    service, _, runtime, _ = confirmation
    first = _preview(service, "todo.create", "first", agent="agent-a")
    competing = _preview(service, "todo.create", "competing", agent="agent-b")
    _interrupt_after_create(service, monkeypatch, "todo.create")
    with pytest.raises(OSError, match="after provider commit"):
        service.apply(first)
    saved = _snapshot(runtime)
    assert service.apply(competing)["proposal"]["status"] == "stale"
    assert _snapshot(runtime) == saved
    assert service.apply(first)["proposal"]["status"] == "applied"
    after_assignment = _snapshot(runtime)
    assert len(after_assignment["todos"]) == 1
    assert after_assignment["todos"][0]["claimed_by"] == "agent-a"
    assert service.apply(first)["proposal"]["status"] == "applied"
    assert _snapshot(runtime) == after_assignment


def test_duplicate_confirmation_keeps_the_running_owner(confirmation, monkeypatch):
    service, _, runtime, _ = confirmation
    proposal_id = _preview(service, "todo.create", "concurrent")
    entered, resume = Event(), Event()
    original_plan = todo_creation.plan_reviewed_todo_create

    def plan(**arguments):
        entered.set()
        assert resume.wait(15), "the duplicate confirmer did not return"
        return original_plan(**arguments)

    monkeypatch.setattr(todo_creation, "plan_reviewed_todo_create", plan)
    lost = _interrupt_after_create(service, monkeypatch, "todo.create")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.apply, proposal_id)
        try:
            assert entered.wait(10)
            with pytest.raises(ActionConflictError, match="original confirmation is in progress"):
                service.apply(proposal_id)
            with pytest.raises(ActionConflictError, match="original confirmation is in progress"):
                service.regenerate(proposal_id)
        finally:
            resume.set()
        with pytest.raises(OSError, match="after provider commit"):
            pending.result(timeout=30)
    committed = _snapshot(runtime)
    recovered = service.apply(proposal_id)["proposal"]
    assert recovered["receipt"]["canonical_receipt"] == lost[0]
    assert len(committed["todos"]) == 1
    assert _snapshot(runtime) == committed


def test_interrupted_assignment_recovers_at_original_revision(confirmation, monkeypatch):
    service, _, runtime, _ = confirmation
    proposal_id = _preview(service, "todo.create", "assignment", agent="agent-a")
    original_save = service.store.save_checkpoint

    def save(proposal_id, *, step, receipt, **kwargs):
        if step == "todo_assigned":
            raise OSError("interrupted after assignment")
        return original_save(proposal_id, step=step, receipt=receipt, **kwargs)

    with monkeypatch.context() as faults:
        faults.setattr(service.store, "save_checkpoint", save)
        with pytest.raises(OSError, match="after assignment"):
            service.apply(proposal_id)
    assigned = _snapshot(runtime)
    assert assigned["todos"][0]["claimed_by"] == "agent-a"
    recovered = service.apply(proposal_id)["proposal"]
    assert recovered["status"] == "applied"
    saved = service.load(proposal_id)["checkpoint"]["steps"]["todo_created"]
    assert recovered["receipt"]["canonical_receipt"] == saved["canonical_receipt"]
    assert _snapshot(runtime) == assigned


@pytest.mark.parametrize("kind", ["todo.create", "monitor.create"])
def test_uncommitted_confirmation_rejects_changed_basis(confirmation, monkeypatch, kind):
    service, registry, runtime, _ = confirmation
    proposal_id = _preview(service, kind, "unfinished")
    original_save = service.store.save_checkpoint

    def save(proposal_id, *, step, receipt, **kwargs):
        saved = original_save(proposal_id, step=step, receipt=receipt, **kwargs)
        if step == "todo_create_prepared":
            raise OSError("interrupted before provider commit")
        return saved

    with monkeypatch.context() as faults:
        faults.setattr(service.store, "save_checkpoint", save)
        with pytest.raises(OSError, match="before provider commit"):
            service.apply(proposal_id)
    assert not _snapshot(runtime)["todos"]
    if kind == "todo.create":
        add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent", text="Independent Todo")
    else:
        registry.write_text(registry.read_text() + "\n")
    before_retry = _snapshot(runtime)
    assert service.apply(proposal_id)["proposal"]["status"] == "stale"
    assert _snapshot(runtime) == before_retry


def test_concurrent_monitor_confirmation_uses_one_schedule(confirmation, monkeypatch):
    service, _, runtime, _ = confirmation
    proposal_id = _preview(service, "monitor.create", "concurrent")
    entered, resume = Event(), Event()
    prepare = service.store.save_checkpoint

    def save(proposal_id, *, step, receipt, **kwargs):
        if step == "todo_create_prepared":
            entered.set()
            assert resume.wait(15), "the duplicate confirmer did not return"
        return prepare(proposal_id, step=step, receipt=receipt, **kwargs)

    monkeypatch.setattr(service.store, "save_checkpoint", save)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.apply, proposal_id)
        try:
            assert entered.wait(10)
            with pytest.raises(ActionConflictError, match="in progress"):
                service.apply(proposal_id)
        finally:
            resume.set()
        initial = pending.result(timeout=30)["proposal"]
    assert initial["receipt"] == service.apply(proposal_id)["proposal"]["receipt"]
    todos = _snapshot(runtime)["todos"]
    assert len(todos) == 1 and todos[0]["next_due_at"]


def test_retry_never_writes_a_replacement_runtime(confirmation, monkeypatch, tmp_path):
    service, registry, runtime, provider = confirmation
    proposal_id = _preview(service, "todo.create", "source-bound")
    _interrupt_after_create(service, monkeypatch, "todo.create")
    with pytest.raises(OSError, match="after provider commit"):
        service.apply(proposal_id)
    after_commit = _snapshot(runtime)
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    _, alternate_runtime, _ = promoted_create_fixture(alternate, provider=provider)
    alternate_before = _snapshot(alternate_runtime)
    source = registry.read_text()
    registry.write_text(json.dumps({**json.loads(source), "common_runtime_root": str(alternate_runtime)}))
    with pytest.raises(ValueError, match="source changed"):
        service.apply(proposal_id)
    assert _snapshot(alternate_runtime) == alternate_before
    registry.write_text(source)
    assert service.apply(proposal_id)["proposal"]["status"] == "applied"
    assert _snapshot(runtime) == after_commit
