"""A saved handoff can recover pre-launch failure through its original owner.

Real File/SQLite authority and governed CLI execution use the existing fixture
model Host. The return transport is synthetic; this does not qualify live App
or model behavior, or ordinary delegation without an explicit task binding.
"""

import json
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from loopx.capabilities.manager_context import _root, _write, execution
from loopx.control_plane.collaboration.inbox import _read
from loopx.file_lock import exclusive_file_lock
from test_independent_delegation_validation import independent_binding
from test_local_delegation import service as delegation_service, wait  # noqa: F401
from test_manager_context_execution import source


@pytest.fixture
def prepared_handoff(delegation_service):  # noqa: F811
    root, service = delegation_service
    independent_binding(delegation_service)
    data = json.loads(service.registry.read_text())
    config = Path(data["goals"][0]["repo"]) / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(service.config.read_bytes())
    data["goals"][0]["spawn_policy"] = {"execution_config": ".loopx/config/delegations.json"}
    service.registry.write_text(json.dumps(data))
    values = source(service.root, service.registry, goal_id=service.goal_id,
                    agent_id="analyst", requester="lead", binding="analysis")
    _, session, turn, request, receipt, _ = values
    # Kill the requesting process at the actual durable-write/pre-spawn seam.
    # No prepared row or execution result is fabricated by this test.
    child = subprocess.run([sys.executable, "-c", """
import json, os, sys
from pathlib import Path
from loopx.capabilities.manager_context import execution
data = json.load(sys.stdin)
execution.Delegations._spawn = lambda *_: os._exit(73)
execution.dispatch(Path(data.pop('root')), Path(data.pop('registry')),
                   **data, execution_allowed=lambda: True)
"""], input=json.dumps({"root": str(service.root), "registry": str(service.registry),
        "session": session, "turn": turn, "request": request, "receipt": receipt}),
        capture_output=True, text=True, timeout=60)
    assert child.returncode == 73, child.stderr
    worker, _ = execution._service(service.root, service.registry, {
        "goal_id": service.goal_id, "agent_id": "analyst", "requester_agent_id": "lead", "binding_id": "analysis"})
    operation = "context-" + receipt["request_id"]
    row = _read(worker.path(operation))
    assert row["status"] == "prepared" and "worker" not in row
    assert row["identity"]["source_request_id"] == receipt["request_id"]
    assert row["conversation"] == {"session_id": session["session_id"], "turn_id": turn["turn_id"]}
    assert not (Path(worker.binding("analysis")["workspace"]) / "host-invocations").exists()
    return root, worker, values, operation


def replay(prepared, **kwargs):
    _, worker, (_, session, turn, request, receipt, _), _ = prepared
    return execution.dispatch(worker.root, worker.registry, session=session, turn=turn,
        request=request, receipt=receipt, **({"execution_allowed": lambda: True} | kwargs))


def test_requester_crash_recovers_original_host_and_return_once(prepared_handoff, monkeypatch):
    root, worker, (store, session, turn, _, receipt, _), operation = prepared_handoff
    host = root / "fixture-host.py"
    host.write_text(host.read_text().replace("print(json.dumps(build_result", """
from loopx.capabilities.manager_context.roundtrip import report
from loopx.control_plane.collaboration.peers import read_inbox
source_id = delegation['source_request_id']
items = read_inbox(root / 'runtime', root / 'registry.json', envelope['goal_id'], actor)['items']
assert any(item['request_id'] == source_id for item in items)
acknowledge(root / 'runtime', envelope['goal_id'], actor, source_id, 'adopt', 'Read the original request.')
report(root / 'runtime', envelope['goal_id'], actor, source_id, 'conclusion', 'Original request independently handled.')
print(json.dumps(build_result"""))
    (root / "hold").touch()
    spawned = []
    spawn = execution.Delegations._spawn

    def observe_spawn(service, selected, **kwargs):
        spawned.append(selected)
        spawn(service, selected, **kwargs)

    monkeypatch.setattr(execution.Delegations, "_spawn", observe_spawn)
    recovered = replay(prepared_handoff)
    assert recovered["submitted"] and recovered["replayed"], recovered
    assert spawned == [operation], "A saved pre-launch handoff must rejoin its original execution owner"
    try:
        deadline = time.monotonic() + 45
        while not (root / "host-started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (root / "host-started").exists(), worker.read(operation)
        with ThreadPoolExecutor(max_workers=4) as callers:
            results = list(callers.map(lambda _: replay(prepared_handoff), range(4)))
        assert all(row["operation_id"] == operation and row["replayed"] for row in results)
        assert spawned == [operation]
    finally:
        (root / "release").touch()
    assert wait(worker, operation)["status"] == "accepted"
    assert (Path(worker.binding("analysis")["workspace"]) / "host-invocations").read_text() == "1"
    original = _read(worker.path(operation))
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
                      response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    from loopx.capabilities.manager_context.roundtrip import drain

    sends = []

    def transport(route, *_args, **_kwargs):
        sends.append(route["session_id"])
        return {"message_id": "original-result", "reply_verified": True}

    drain(worker.root, worker.registry, store, transport)
    drain(worker.root, worker.registry, store, transport)
    assert sends == [session["session_id"]]
    assert replay(prepared_handoff)["status"] == "accepted"
    assert _read(worker.path(operation))["identity"] == original["identity"]
    assert _read(worker.path(operation))["conversation"] == original["conversation"]
    assert spawned == [operation]


@pytest.mark.parametrize("gate", ["source_cancelled", "late_source_cancelled", "grant_revoked",
                                  "late_grant_revoked", "operation_stopped", "binding_changed"])
def test_prepared_recovery_rechecks_current_authority(prepared_handoff, monkeypatch, gate):
    _, worker, (_, _, _, _, _, policy), operation = prepared_handoff
    observations = iter([True, False] if gate == "late_source_cancelled"
                        else [gate != "source_cancelled"] * 2)

    def allowed():
        return next(observations)

    if gate == "grant_revoked":
        policy["sources"]["manager.external.test"]["execution_bindings"] = []
        _write(_root(worker.root) / "policy.json", policy)
    elif gate == "late_grant_revoked":
        read = execution.Delegations.read

        def revoke_after_read(service, selected):
            result = read(service, selected)
            policy["sources"]["manager.external.test"]["execution_bindings"] = []
            _write(_root(worker.root) / "policy.json", policy)
            return result

        monkeypatch.setattr(execution.Delegations, "read", revoke_after_read)
    elif gate == "operation_stopped":
        worker.stop(operation, execute=True)
    elif gate == "binding_changed":
        config = json.loads(worker.config.read_text())
        config["bindings"][0]["timeout_seconds"] += 1
        worker.config.write_text(json.dumps(config))
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda *_, **__: pytest.fail("refused work must not launch"))
    refused = replay(prepared_handoff, execution_allowed=allowed)
    if gate == "operation_stopped":
        assert refused["status"] == "stopped" and refused["replayed"], refused
    else:
        assert not refused["submitted"], refused


def test_prepared_busy_worker_is_only_observed(prepared_handoff, monkeypatch):
    _, worker, _, operation = prepared_handoff
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda *_, **__: pytest.fail("busy worker must not launch twice"))
    with exclusive_file_lock(worker.path(operation)):
        observed = replay(prepared_handoff)
    assert observed["submitted"] and observed["replayed"] and observed["status"] == "prepared"


def test_prepared_recovery_rejects_another_original_request(prepared_handoff, monkeypatch):
    _, worker, _, operation = prepared_handoff
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda *_, **__: pytest.fail("source mismatch must not launch"))
    for action in (worker.resume, worker.execute):
        with pytest.raises(ValueError, match="original source request changed"):
            action(operation, prepared_source_request_id="another-original-request")
    assert worker.read(operation)["status"] == "prepared"


def test_delayed_prepared_worker_cannot_resume_an_uncertain_host(prepared_handoff, monkeypatch):
    root, worker, (_, _, _, _, receipt, _), operation = prepared_handoff
    # A different original worker advances after pre-launch recovery is queued.
    # Lose its actual CLI result after Host execution, leaving uncertain running
    # work for explicit reconciliation rather than automatic recovery.
    def lose_reply(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("original Turn result", 1)

    monkeypatch.setattr(worker, "_record_turn_result", lose_reply)
    worker.execute(operation)
    assert worker.read(operation)["status"] == "running"
    counter = Path(worker.binding("analysis")["workspace"]) / "host-invocations"
    assert counter.read_text() == "1"
    before = worker.path(operation).read_bytes()
    journals = worker.root / "goals" / worker.goal_id / "turns"
    journal_bytes = {path.name: path.read_bytes() for path in journals.glob("*.json")}
    assert len(journal_bytes) == 1
    # Both the dispatch-side CAS and the real delayed child retain the fence.
    observed = worker.resume(operation, prepared_source_request_id=receipt["request_id"])
    assert observed["status"] == "running"
    completed = subprocess.run([sys.executable, "-m", "loopx.collaboration_mcp",
        "--delegation-action", "worker", "--runtime-root", str(worker.root),
        "--registry", str(worker.registry), "--goal-id", worker.goal_id,
        "--agent-id", worker.agent_id, "--execution-config", str(worker.config),
        "--operation-id", operation, "--prepared-source-request-id", receipt["request_id"]],
        capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert worker.path(operation).read_bytes() == before
    assert {path.name: path.read_bytes() for path in journals.glob("*.json")} == journal_bytes
    assert counter.read_text() == "1"
