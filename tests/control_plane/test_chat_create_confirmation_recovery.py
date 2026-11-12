"""Recover the original Chat confirmation against the real Todo providers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Barrier, Event

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx import chat, chat_actions
from loopx.chat_action_store import ChatActionStore
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

        def save(proposal_id, *, current_state_fingerprint, receipt):
            if receipt.get("outcome") == "monitor_created" and not lost:
                lost.append(receipt)
                raise OSError("interrupted after provider commit")
            return apply(proposal_id, current_state_fingerprint=current_state_fingerprint, receipt=receipt)

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


def test_late_preparation_resumes_a_concurrent_commit(confirmation, monkeypatch):
    service, _, runtime, _ = confirmation
    proposal_id = _preview(service, "todo.create", "concurrent")
    entered, resume = Event(), Event()
    original_plan = chat_actions.plan_reviewed_todo_create

    def plan(**arguments):
        if not entered.is_set():
            entered.set()
            assert resume.wait(15), "the second confirmer did not finish"
        return original_plan(**arguments)

    monkeypatch.setattr(chat_actions, "plan_reviewed_todo_create", plan)
    lost = _interrupt_after_create(service, monkeypatch, "todo.create")
    with ThreadPoolExecutor(max_workers=1) as pool:
        late = pool.submit(service.apply, proposal_id)
        try:
            assert entered.wait(10)
            with pytest.raises(OSError, match="after provider commit"):
                service.apply(proposal_id)
        finally:
            resume.set()
        recovered = late.result(timeout=30)["proposal"]
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["canonical_receipt"] == lost[0]
    assert len(_snapshot(runtime)["todos"]) == 1


def test_saved_legacy_create_can_finish_assignment_once(confirmation, monkeypatch):
    service, registry, runtime, _ = confirmation
    proposal_id = _preview(service, "todo.create", "old-confirmation", agent="agent-a")
    proposal = service.store.start_apply(proposal_id)
    created = chat.apply_todo_review_preview(
        registry_path=registry, goal_id="goal-a", text="Review service availability",
        priority="P2", preview_id=proposal["expected_state_fingerprint"],
    )["receipt"]
    service.store.save_checkpoint(proposal_id, step="todo_created", receipt={
        "todo_id": created["todo_id"], "canonical_receipt": created,
    })
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
    assert recovered["receipt"]["canonical_receipt"] == created
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
    now = datetime.now(timezone.utc)
    clock = iter([now, now + timedelta(minutes=5)])
    monkeypatch.setattr("loopx.chat_actions.now_utc", lambda: next(clock))
    prepare = service.store.save_checkpoint
    barrier = Barrier(2)

    def save(proposal_id, *, step, receipt, **kwargs):
        if step == "todo_create_prepared":
            barrier.wait(timeout=15)
        return prepare(proposal_id, step=step, receipt=receipt, **kwargs)

    monkeypatch.setattr(service.store, "save_checkpoint", save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.apply, (proposal_id, proposal_id)))
    assert results[0]["proposal"]["receipt"] == results[1]["proposal"]["receipt"]
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


@pytest.mark.parametrize("kind, assign", [
    ("todo.create", False), ("todo.create", True), ("monitor.create", False),
])
def test_pending_create_display_recovers_original_confirmation(confirmation, monkeypatch, kind, assign):
    from loopx.chat_action_store import ActionConflictError
    from loopx.control_plane.todos import provider_projection

    service, registry, runtime, _ = confirmation
    record = json.loads(registry.read_text())["goals"][0]
    display = registry.parent / "project" / record["state_file"]
    proposal_id = _preview(service, kind, "display-pending", agent="agent-a" if assign else None)
    original_write = provider_projection.atomic_write_state_text
    writes = 0

    def write(path, text, **kwargs):
        nonlocal writes
        writes += 1
        if assign and writes == 1:
            return original_write(path, text, **kwargs)
        raise PermissionError("synthetic display failure")

    with monkeypatch.context() as faults:
        faults.setattr(provider_projection, "atomic_write_state_text", write)
        pending = service.apply(proposal_id)["proposal"]
        assert pending["status"] == "failed" and pending.get("receipt") is None
        failure = pending["failure"]
        assert failure["error_code"] == "canonical_create_projection_pending"
        details = failure["details"]
        assert details["canonical_committed"] is True and details["projection_delivery"] == "pending"
        todo_id = details["todo_id"]
        assert details["operation_id"] == f"{'chat-assign' if assign else 'chat-create'}:{proposal_id}"
        committed = _snapshot(runtime)
        assert [row["todo_id"] for row in committed["todos"]] == [todo_id]
        if assign:
            assert committed["todos"][0]["claimed_by"] == "agent-a"
            assert "claimed_by=agent-a" not in display.read_text()
        else:
            assert todo_id not in display.read_text()
        with pytest.raises(ActionConflictError, match="original task confirmation"):
            service.regenerate(proposal_id)
        retry = service.apply(proposal_id)["proposal"]
        assert retry["failure"]["details"] == details
        assert _snapshot(runtime) == committed

    reopened = ChatActionService(store=ChatActionStore(runtime / "chat" / "actions"),
                                 registry_path=registry)
    recovered = reopened.apply(proposal_id)["proposal"]
    assert recovered["status"] == "applied", recovered
    assert recovered["receipt"]["projection_verified"] is True
    assert recovered["receipt"]["projection_delivery"] in {"current", "delivered"}
    assert recovered["receipt"]["resource_ids"]["todo_id"] == todo_id
    assert todo_id in display.read_text()
    if assign:
        assert "claimed_by=agent-a" in display.read_text()
    assert reopened.apply(proposal_id)["proposal"]["receipt"] == recovered["receipt"]
    assert _snapshot(runtime) == committed


def test_pending_monitor_confirmation_preserves_the_registered_source_fence(confirmation, monkeypatch, tmp_path):
    from loopx import paths

    service, registry, runtime, _ = confirmation
    defaults = (tmp_path / "current", tmp_path / "legacy")
    for root in defaults:
        root.mkdir()
        (root / "machine.json").write_text("{}")
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", defaults[0])
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", defaults[1])

    original_registry = registry.read_text()
    unbound = json.loads(original_registry)
    del unbound["common_runtime_root"]
    registry.write_text(json.dumps(unbound))
    service.runtime_root_override = str(runtime)
    display = tmp_path / "project" / unbound["goals"][0]["state_file"]
    original_display = display.read_bytes()
    proposal_id = _preview(service, "monitor.create", "unbound-display")

    pending = service.apply(proposal_id)["proposal"]
    todo_id = pending["failure"]["details"]["todo_id"]
    committed = _snapshot(runtime)
    assert [row["todo_id"] for row in committed["todos"]] == [todo_id]
    assert service.apply(proposal_id)["proposal"]["status"] == "failed"
    assert display.read_bytes() == original_display and _snapshot(runtime) == committed

    registry.write_text(original_registry)
    applied = service.apply(proposal_id)["proposal"]
    assert applied["status"] == "applied", applied
    assert applied["receipt"]["resource_ids"]["todo_id"] == todo_id
    assert todo_id in display.read_text() and _snapshot(runtime) == committed
    for root in defaults:
        assert list(root.iterdir()) == [root / "machine.json"]
        assert (root / "machine.json").read_text() == "{}"
