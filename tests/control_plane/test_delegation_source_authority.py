"""A persisted Chat handoff cannot outlive its original execution grant.

Real File/SQLite authority and a real worker use the existing fixture Host.
This checks source revocation, not live App or provider qualification.
"""

import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from loopx.capabilities.manager_context import (
    _root,
    _write,
    deliver,
    execution,
)
from loopx.control_plane.collaboration import delegation_results, delegation_validation
from loopx.control_plane.collaboration.inbox import _read
from loopx.control_plane.collaboration.source_policy_fence import source_policy_fence
from loopx.control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
from loopx.control_plane.collaboration.peers import _operation_address
from test_independent_delegation_validation import independent_binding
from test_local_delegation import demo, service as delegation_service  # noqa: F401
from test_manager_context_execution import source


@pytest.fixture
def saved_handoff(delegation_service, monkeypatch, request):  # noqa: F811
    root, service = delegation_service
    independent_binding(delegation_service)
    data = json.loads(service.registry.read_text())
    config = Path(data["goals"][0]["repo"]) / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(service.config.read_bytes())
    data["goals"][0]["spawn_policy"] = {"execution_config": ".loopx/config/delegations.json"}
    service.registry.write_text(json.dumps(data))
    values = source(service.root, service.registry, goal_id=service.goal_id,
                    agent_id="analyst", requester="lead", binding="analysis",
                    original_message=request.param if isinstance(getattr(request, "param", None), str) else None,
                    chat_root=root / "source-chat" if getattr(request, "param", False) else None)
    store, session, turn, request, receipt, _ = values
    deliver(service.root, service.registry, session=session, turn=turn,
            request=request, source_store=store)
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda *_: None)
    submitted = execution.dispatch(service.root, service.registry, session=session, turn=turn,
        request=request, receipt=receipt, execution_allowed=lambda: True)
    assert submitted["submitted"] and submitted["status"] == "prepared", submitted
    worker, binding = execution._service(service.root, service.registry, {
        "goal_id": service.goal_id, "agent_id": "analyst",
        "requester_agent_id": "lead", "binding_id": "analysis"})
    return root, worker, binding, values, submitted["operation_id"]


def run_worker(worker, operation):
    completed = subprocess.run([sys.executable, "-m", "loopx.collaboration_mcp",
        "--delegation-action", "worker", "--runtime-root", str(worker.root),
        "--registry", str(worker.registry), "--goal-id", worker.goal_id,
        "--agent-id", worker.agent_id, "--execution-config", str(worker.config),
        "--operation-id", operation], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr


def changed_source_work(worker, binding, operation, source_id, change):
    """Corrupt only disposable provenance; the real worker must not adopt it."""
    row = _read(worker.path(operation))
    if change == "conversation":
        row["conversation"] = {"session_id": "another-session", "turn_id": "another-turn"}
        _write(worker.path(operation), row)
        return
    if change in {"peer_operation", "operation_missing"}:
        with collaboration_goal_scope(worker.registry, goal_id=worker.goal_id, agents=(),
                caller_goal_ref=worker._caller_goal_ref()) as scope:
            _, path = _operation_address(worker.root, scope, worker.agent_id, operation)
    else:
        request_id = source_id if change == "source_brief" else row["identity"]["request_id"]
        path = next((_root(worker.root) / "entries").glob("*/" + request_id + ".json"))
    original = path.read_bytes()
    if change == "operation_missing":
        path.unlink()
        return path, original
    entry = _read(path)
    entry["brief"]["constraints"].append("Changed after the original dispatch")
    _write(path, entry)
    return path, original


@pytest.mark.parametrize("change", ["source_brief", "peer_brief", "peer_operation", "operation_missing", "conversation"])
def test_worker_checks_original_work_without_a_chat_replay(saved_handoff, change):
    root, worker, binding, (_, _, _, _, receipt, _), operation = saved_handoff
    tasks_before = demo.canonical_tasks(root)
    changed_source_work(worker, binding, operation, receipt["request_id"], change)
    run_worker(worker, operation)
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    assert demo.canonical_tasks(root) == tasks_before
    row = _read(worker.path(operation))
    assert row["status"] == "rejected"
    assert "source execution" in row["error"]
    assert worker.read(operation)["operation_id"] == operation


@pytest.mark.parametrize("change", ["source_brief", "peer_brief", "peer_operation"])
def test_worker_rechecks_original_work_after_host_before_acceptance(saved_handoff, monkeypatch, change):
    root, worker, binding, (_, _, _, _, receipt, _), operation = saved_handoff
    tasks_before = demo.canonical_tasks(root)
    cli = worker._cli
    changed = False
    original_sources = []

    def change_after_host(current_binding, *args, **kwargs):
        nonlocal changed
        result = cli(current_binding, *args, **kwargs)
        if not changed and args[:2] == ("turn", "run-once") and "--execute" in args:
            changed = True
            original_sources.append(changed_source_work(worker, binding, operation, receipt["request_id"], change))
        return result

    monkeypatch.setattr(worker, "_cli", change_after_host)
    worker.execute(operation)
    assert changed
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    assert demo.canonical_tasks(root) == tasks_before
    journals = list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    assert len(journals) == 1
    journal = _read(journals[0])
    assert "quota_spend" not in journal["completed_phases"]
    assert journal["task_validation"]["ok"] is True
    assert _read(worker.path(operation))["status"] != "accepted"
    # Restore only the original disposable source. The saved validated Turn,
    # not a second Host, must finish its original completion and settlement.
    for path, original in original_sources:
        path.write_bytes(original)
    worker.execute(operation)
    settled = _read(worker.path(operation))
    assert settled["status"] == "accepted", settled
    assert settled["turn_key"] == journal["turn_key"]
    assert demo.canonical_tasks(root)[binding["todo_id"]]["done"]
    assert _read(journals[0])["completed_phases"].count("quota_spend") == 1
    worker.execute(operation)
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"


@pytest.mark.parametrize("change", ["revoke", "sender", "blocked", "requester", "binding",
                                  "policy_missing", "route_missing", "source_missing", "body", "closed"])
def test_changed_queued_source_cannot_reach_the_real_worker_host(saved_handoff, change):
    root, worker, binding, (store, session, turn, _, receipt, policy), operation = saved_handoff
    before = demo.canonical_tasks(root)
    grant = policy["sources"][session["channel_id"]]
    if change == "revoke":
        grant["execution_bindings"] = []
    elif change == "sender":
        grant["sender_ids"] = ["another-person"]
    elif change == "blocked":
        grant["blocked_targets"] = [{"goal_id": worker.goal_id, "agent_id": binding["agent_id"]}]
    elif change in {"requester", "binding"}:
        grant["execution_bindings"][0]["requester_agent_id" if change == "requester" else "binding_id"] = "reviewer"
    elif change == "route_missing":
        (_root(worker.root) / "roundtrips" / (receipt["request_id"] + ".json")).unlink()
    elif change == "source_missing":
        store._turn_path(session["session_id"], turn["turn_id"]).unlink()
    elif change == "body":
        # Simulate corrupted immutable provenance in this disposable Chat store.
        _write(store._turn_path(session["session_id"], turn["turn_id"]),
               {**turn, "message": "Different source request"})
    elif change == "closed":
        store.update_session(session["session_id"], status="closed")
    _write(_root(worker.root) / "policy.json", policy)
    if change == "policy_missing":
        (_root(worker.root) / "policy.json").unlink()
    chat_before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    run_worker(worker, operation)
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == chat_before
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    assert demo.canonical_tasks(root) == before
    row = _read(worker.path(operation))
    assert row["status"] == "rejected"
    assert "source execution" in row["error"]
    # Revocation blocks effects, not inspection of the historical operation.
    assert worker.read(operation)["operation_id"] == operation


@pytest.mark.parametrize("saved_handoff", [False, True], indirect=True)
def test_completed_source_and_separate_chat_store_keep_authorized_execution(saved_handoff):
    root, worker, binding, (store, session, turn, _, receipt, _), operation = saved_handoff
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
                      response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    assert store.load_turn(session["session_id"], turn["turn_id"])["status"] == "completed"
    run_worker(worker, operation)
    row = worker.read(operation)
    assert row["status"] == "accepted", row
    assert demo.canonical_tasks(root)[binding["todo_id"]]["done"]
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    run_worker(worker, operation)
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"


def revoke(worker, session, policy):
    policy["sources"][session["channel_id"]]["execution_bindings"] = []
    _write(_root(worker.root) / "policy.json", policy)


def test_revocation_during_real_acceptance_capture_is_rechecked_before_launch(saved_handoff, monkeypatch):
    _, worker, binding, (_, session, _, _, _, policy), operation = saved_handoff
    capture = delegation_validation.capture
    def changed(*args):
        observed = capture(*args)
        revoke(worker, session, policy)
        return observed
    monkeypatch.setattr(delegation_validation, "capture", changed)
    worker.execute(operation)
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert _read(worker.path(operation))["status"] == "rejected"


@pytest.mark.parametrize("saved_handoff", [True], indirect=True)
def test_final_source_revocation_cannot_split_completion_and_settlement(
    saved_handoff, monkeypatch,
):
    root, worker, binding, (_, session, _, _, _, _), operation = saved_handoff
    complete = worker._complete_delegated_todo
    revocation_started = threading.Event()
    revocation_finished = threading.Event()
    revocation_errors = []
    revocation_threads = []
    completed_before_revocation = []

    def revoke_from_owner():
        revocation_started.set()
        try:
            policy_path = _root(worker.root) / "policy.json"
            with source_policy_fence(policy_path):
                current = _read(policy_path)
                current["sources"][session["channel_id"]]["execution_bindings"] = []
                _write(policy_path, current)
        except Exception as exc:  # pragma: no cover - asserted below
            revocation_errors.append(exc)
        finally:
            revocation_finished.set()

    def complete_with_concurrent_revocation(row, current_binding):
        thread = threading.Thread(target=revoke_from_owner, daemon=True)
        thread.start()
        assert revocation_started.wait(5)
        complete(row, current_binding)
        completed_before_revocation.append(not revocation_finished.is_set())
        revocation_threads.append(thread)

    monkeypatch.setattr(worker, "_complete_delegated_todo", complete_with_concurrent_revocation)
    worker.execute(operation)
    [thread] = revocation_threads
    thread.join(10)
    assert not revocation_errors
    assert completed_before_revocation == [True]
    assert revocation_finished.is_set()
    row = worker.read(operation)
    assert row["status"] == "accepted", row
    assert demo.canonical_tasks(root)[binding["todo_id"]]["done"]
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    worker.execute(operation)
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"


@pytest.mark.parametrize("saved_handoff", [True], indirect=True)
def test_prelaunch_refusal_recreates_original_bootstrap_on_authorized_resume(
    saved_handoff, monkeypatch,
):
    root, worker, binding, (_, session, _, _, _, policy), operation = saved_handoff
    original_policy = json.loads(json.dumps(policy))
    acquire = worker._acquire_delegation_lease
    revoked = False

    def revoke_after_original_lease(path, row, current_binding):
        nonlocal revoked
        result = acquire(path, row, current_binding)
        if not revoked:
            revoked = True
            policy["sources"][session["channel_id"]]["execution_bindings"] = []
            _write(_root(worker.root) / "policy.json", policy)
        return result

    monkeypatch.setattr(worker, "_acquire_delegation_lease", revoke_after_original_lease)
    worker.execute(operation)
    refused = _read(worker.path(operation))
    assert refused["status"] == "running", refused
    assert "source execution" in refused["error"]
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert not demo.canonical_tasks(root)[binding["todo_id"]]["done"]
    assert not (Path(binding["workspace"]) / "DELEGATION.json").exists()

    _write(_root(worker.root) / "policy.json", original_policy)
    worker.execute(operation)
    resumed = worker.read(operation)
    assert resumed["status"] == "accepted", resumed
    assert demo.canonical_tasks(root)[binding["todo_id"]]["done"]
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    worker.execute(operation)
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"


@pytest.mark.parametrize("checkpoint", ["host_return", "completion_dependencies"])
def test_revocation_after_host_preserves_original_turn_for_authorized_settlement(saved_handoff, monkeypatch, checkpoint):
    root, worker, binding, (_, session, _, _, _, policy), operation = saved_handoff
    original_policy = json.loads(json.dumps(policy))
    cli, dependencies = worker._cli, delegation_results.require_dependencies
    def returned(*args, **kwargs):
        result = cli(*args, **kwargs)
        if "run-once" in args:
            revoke(worker, session, policy)
        return result
    def checked(*args):
        dependencies(*args)
        revoke(worker, session, policy)
    with monkeypatch.context() as boundary:
        if checkpoint == "host_return":
            boundary.setattr(worker, "_cli", returned)
        else:
            boundary.setattr(delegation_results, "require_dependencies", checked)
        worker.execute(operation)
    held = _read(worker.path(operation))
    assert held["status"] == "turn_returned", held
    assert "source execution" in held["error"]
    assert not demo.canonical_tasks(root)[binding["todo_id"]]["done"]
    assert delegation_validation.validated_turn_journal(worker, held, binding) is not None
    counter = Path(binding["workspace"]) / "host-invocations"
    assert counter.read_text() == "1"
    with pytest.raises(ValueError, match="source execution"):
        worker.resume(operation)
    _write(_root(worker.root) / "policy.json", original_policy)
    worker.execute(operation)
    settled = worker.read(operation)
    assert settled["status"] == "accepted", settled
    assert _read(worker.path(operation))["turn_key"] == held["turn_key"]
    assert counter.read_text() == "1"
    assert demo.canonical_tasks(root)[binding["todo_id"]]["done"]
