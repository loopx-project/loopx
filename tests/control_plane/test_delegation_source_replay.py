"""Replaying a Chat handoff must recover that exact original operation.

Use real Inbox, Chat and File/SQLite owners. A saved operation id alone cannot
identify a newly selected binding, substitute a brief or redirect the return.
"""

import copy
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from loopx.capabilities.manager_context import _root, _write, execution
from loopx.control_plane.collaboration.inbox import _read
from loopx.control_plane.collaboration.peers import request as peer_request
from loopx.control_plane.collaboration.source_grant_observation import read_delegation_source_execution
from loopx.control_plane.turn_driver.lane_fence import turn_lane_singleflight
from test_independent_delegation_validation import independent_binding
from test_delegation_source_authority import saved_handoff  # noqa: F401
from test_local_delegation import brief, demo, service as delegation_service  # noqa: F401


def durable_bytes(root):
    # Scoped read locks update diagnostic holder timestamps, not business state.
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not p.name.endswith(".lock")}


@pytest.mark.parametrize("change", ["revoked", "closed", "other_host", "context_only", "advanced", "cancelled"])
def test_background_source_recovery_cannot_create_or_retarget_execution(saved_handoff, monkeypatch, change):  # noqa: F811
    root, worker, binding, (store, session, turn, _, receipt, policy), operation = saved_handoff
    if change == "revoked":
        policy["sources"][session["channel_id"]]["execution_bindings"] = []
        _write(_root(worker.root) / "policy.json", policy)
    elif change == "closed":
        store.update_session(session["session_id"], status="closed")
    elif change == "other_host":
        from loopx.chat_store import ChatSessionStore
        store = ChatSessionStore(root / "another-chat-host")
    elif change == "context_only":
        worker.path(operation).unlink()  # A saved Inbox alone is never launch intent.
    elif change == "advanced":
        row = _read(worker.path(operation))
        row["status"] = "running"
        _write(worker.path(operation), row)
    calls = []
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda *_args, **kwargs: calls.append(kwargs))
    before, tasks = durable_bytes(worker.root), demo.canonical_tasks(root)
    pump = execution.QueuedSourcePump(worker.root, worker.registry, store)
    pump.tick(cancelled=lambda: change == "cancelled")
    assert calls == []
    assert durable_bytes(worker.root) == before
    assert demo.canonical_tasks(root) == tasks
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))


@pytest.mark.parametrize("preview", ["refused", "error"])
def test_background_source_recovery_rechecks_current_preflight_and_its_bounded_retry(saved_handoff, monkeypatch, preview):  # noqa: F811
    root, worker, binding, (store, _, _, _, _, _), operation = saved_handoff
    calls = []
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda *_args, **kwargs: calls.append(kwargs))
    clock = [0.0]
    monkeypatch.setattr(execution, "monotonic", lambda: clock[0])
    inspect = execution.Delegations.inspect
    observations = []
    def blocked(self, selected):
        observations.append(selected)
        if preview == "error":
            raise RuntimeError("native preview unavailable")
        return {**inspect(self, selected), "dispatch_allowed": False}
    monkeypatch.setattr(execution.Delegations, "inspect", blocked)
    pump = execution.QueuedSourcePump(worker.root, worker.registry, store)
    # Multiple candidates cannot turn a failed full preview into repeated IO
    # in one probe. Use the real retained source at every candidate address.
    pump.paths = iter([worker.path(operation)] * 3)
    pump.tick()
    pump.tick()
    assert len(observations) == 1 and calls == []
    assert _read(worker.path(operation))["status"] == "prepared"
    monkeypatch.setattr(execution.Delegations, "inspect", inspect)
    # A new service after restart may inspect immediately, but recovers only
    # the original prepared operation and carries its worker-entry CAS.
    restarted = execution.QueuedSourcePump(worker.root, worker.registry, store)
    restarted.tick()
    assert calls == [{"prepared_source_request_id": _read(worker.path(operation))["identity"]["source_request_id"]}]
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert not demo.canonical_tasks(root)[binding["todo_id"]]["done"]


def test_slow_queue_preview_does_not_block_original_return_or_server_close(saved_handoff, monkeypatch):  # noqa: F811
    from loopx.capabilities.manager_context.roundtrip import ReturnService, report
    from loopx.capabilities.manager_context import acknowledge

    _, worker, _, (store, session, turn, _, receipt, _), operation = saved_handoff
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
        response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    entered, release, sent = threading.Event(), threading.Event(), threading.Event()
    launched = []
    def slow_preview(*_):
        entered.set()
        assert release.wait(15)
        return {"dispatch_allowed": True}
    monkeypatch.setattr(execution.Delegations, "inspect", slow_preview)
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda *args, **kwargs: launched.append(args))
    def transport(*_):
        sent.set()
        return {"reply_verified": True, "idempotency_key": "fixture-provider-return"}
    background = ReturnService(worker.root, worker.registry, store, transport)
    try:
        background.start()
        assert entered.wait(5)
        acknowledge(worker.root, worker.goal_id, "analyst", receipt["request_id"], "no_change", "Reviewed original")
        report(worker.root, worker.goal_id, "analyst", receipt["request_id"], "conclusion", "No execution needed")
        assert sent.wait(8), "An unrelated slow execution preview blocked the completed source return"
        started = time.monotonic()
        background.close()
        assert time.monotonic() - started < 4
    finally:
        background.close()
        release.set()
        background.execution_thread.join(timeout=5)
    assert not launched
    assert not background.thread.is_alive() and not background.execution_thread.is_alive()
    assert _read(worker.path(operation))["status"] == "prepared"


def test_unstarted_native_cadence_wait_retains_source_until_its_original_interval(saved_handoff):  # noqa: F811
    from loopx.control_plane.effect_runtime import effect_runtime_result

    root, worker, binding, (_, _, _, _, receipt, _), operation = saved_handoff
    scope = {"runtime_root": str(worker.root), "goal_id": worker.goal_id,
        "agent_id": binding["agent_id"], "automation_id": None}
    effect_runtime_result("quota.automation_cadence.manage", {**scope, "operation": "configure",
        "expected_revision": 0, "min_interval_minutes": 1,
        "owner_reference": "fixture-owner-cadence", "execute": True})
    # Seed a prior native reservation in this disposable runtime, retaining a
    # real interval with enough time for the first worker to observe its wait.
    anchor = time.time_ns() // 1_000_000 - 40_000
    previous = {**scope, "request_id": "fixture-preceding-turn", "now_ms": anchor, "trigger_at_ms": anchor}
    assert effect_runtime_result("quota.automation_cadence.admit", previous)["admitted"]
    effect_runtime_result("quota.automation_cadence.confirm_start", previous)
    original, tasks = _read(worker.path(operation)), demo.canonical_tasks(root)
    worker.execute(operation, prepared_source_request_id=receipt["request_id"])
    waiting = _read(worker.path(operation))
    assert waiting["turn_result"]["reason"] == "minimum_interval_wait", waiting
    assert waiting["status"] == "prepared", waiting
    assert "turn_key" not in waiting
    assert waiting["identity"] == original["identity"]
    assert demo.canonical_tasks(root) == tasks
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    # Wait for the real floor; do not rewrite a start receipt or bypass policy.
    assert not list((worker.root / "goals" / worker.goal_id / "turns" / "decisions").glob("*.json"))
    due = effect_runtime_result("quota.automation_cadence.manage", {**scope, "operation": "read"})["next_eligible_at_ms"]
    time.sleep(max(0, (due - time.time_ns() // 1_000_000) / 1000) + .02)
    worker.execute(operation, prepared_source_request_id=receipt["request_id"])
    accepted = _read(worker.path(operation))
    assert accepted["status"] == "accepted", accepted
    assert accepted["identity"] == original["identity"]
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    journals = list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text())["completed_phases"].count("quota_spend") == 1


@pytest.mark.parametrize("change", ["binding", "brief", "source_request", "peer_request", "conversation"])
def test_saved_handoff_replay_cannot_substitute_its_original_identity(saved_handoff, change):  # noqa: F811
    _, worker, binding, (_, session, turn, request, receipt, policy), operation = saved_handoff
    request = copy.deepcopy(request)
    if change == "binding":
        config = json.loads(worker.config.read_text())
        replacement = {**binding, "id": "other-review"}
        config["bindings"].append(replacement)
        worker.config.write_text(json.dumps(config))
        policy["sources"][session["channel_id"]]["execution_bindings"].append({
            "goal_id": worker.goal_id, "agent_id": binding["agent_id"],
            "requester_agent_id": worker.agent_id, "binding_id": replacement["id"],
        })
        _write(_root(worker.root) / "policy.json", policy)
        request["execution_binding_id"] = replacement["id"]
    elif change == "brief":
        request["brief"]["constraints"].append("A changed request is not the original work.")
    else:
        row = _read(worker.path(operation))
        if change == "source_request":
            row["identity"]["source_request_id"] = "f" * 64
        elif change == "peer_request":
            other = peer_request(worker.root, worker.registry, worker.goal_id, worker.agent_id,
                binding["agent_id"], "another-operation", request["brief"])
            row["identity"]["request_id"] = other["request_id"]
        else:
            row["conversation"] = {"session_id": "another-session", "turn_id": "another-turn"}
        _write(worker.path(operation), row)
    before = durable_bytes(worker.root)
    result = execution.dispatch(worker.root, worker.registry, session=session, turn=turn,
        request=request, receipt=receipt, execution_allowed=lambda: True)
    assert result["submitted"] is False, result
    assert "status" not in result and "replayed" not in result
    assert durable_bytes(worker.root) == before
    assert not (Path(binding["workspace"]) / "host-invocations").exists()


def test_exact_prepared_replay_rejoins_only_the_original_operation(saved_handoff, monkeypatch):  # noqa: F811
    _, worker, binding, (_, session, turn, request, receipt, _), operation = saved_handoff
    resumed = []
    monkeypatch.setattr(
        execution.Delegations,
        "_spawn",
        lambda _self, operation_id, **kwargs: resumed.append((operation_id, kwargs)),
    )
    before = durable_bytes(worker.root)
    assert read_delegation_source_execution(worker, binding["id"], operation,
        source_request_id=receipt["request_id"], brief=request["brief"],
        conversation={"session_id": session["session_id"], "turn_id": turn["turn_id"]})["status"] == "prepared"
    result = execution.dispatch(worker.root, worker.registry, session=session, turn=turn,
        request=request, receipt=receipt, execution_allowed=lambda: True)
    assert result["submitted"], result
    assert result["replayed"] and result["status"] == "prepared"
    assert result["operation_id"] == operation
    assert resumed == [(operation, {"prepared_source_request_id": receipt["request_id"]})]
    assert durable_bytes(worker.root) == before
    assert _read(worker.path(operation))["conversation"] == {
        "session_id": session["session_id"], "turn_id": turn["turn_id"],
    }
    assert not (Path(binding["workspace"]) / "host-invocations").exists()


def test_chat_handoff_replay_reports_an_original_return_mismatch(saved_handoff):  # noqa: F811
    _, worker, binding, (store, session, turn, request, _, _), operation = saved_handoff
    row = _read(worker.path(operation))
    row["conversation"] = {"session_id": "another-session", "turn_id": "another-turn"}
    _write(worker.path(operation), row)
    before = worker.path(operation).read_bytes()
    answer = "The original scope remains in effect."
    response = execution.handoff_response(worker.root, worker.registry,
        session=session, turn=turn, response={"context_handoff": request, "message": answer},
        source_authorized=lambda: True, execution_allowed=lambda: True, source_store=store)
    assert response["context_handoff_receipt"]["status"] == "delivered"
    assert response["context_execution"]["submitted"] is False
    assert "status" not in response["context_execution"]
    assert response["message"].startswith(answer + "\n\n")
    assert worker.path(operation).read_bytes() == before
    assert not (Path(binding["workspace"]) / "host-invocations").exists()


@pytest.mark.parametrize("hard_lease", [False, True])
def test_busy_lane_after_dispatch_keeps_the_original_operation_retryable(saved_handoff, monkeypatch, hard_lease):  # noqa: F811
    root, worker, binding, (store, session, turn, request, receipt, _), operation = saved_handoff
    if hard_lease:
        # Configure only this newly created disposable authority. The active
        # Goal's policy migration/fence and execution grants are never changed.
        program = '''
import {openLocalAuthorityStore} from "./loopx/control_plane/coordination/local_authority_provider.ts";
const [root,goal] = process.argv.slice(1);
const store = await openLocalAuthorityStore(root,goal);
const state = await store.loadAuthority();
const result = await store.commitAuthority({expected_provider_revision:state.provider_revision,
 operation_id:"fixture-lane-race",events:[],receipts:[],
 next_projection:{...state.head,handoff_mode:"hard_lease"}});
if(result.status!=="applied") throw new Error(JSON.stringify(result));
'''
        subprocess.run(["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
            "--input-type=module", "-e", program, str(worker.root), worker.goal_id],
            capture_output=True, text=True, timeout=30, check=True)
    original = _read(worker.path(operation))
    tasks = demo.canonical_tasks(root)
    # Dispatch already observed an idle lane and persisted the operation. The
    # actual native Turn now loses its single-flight race, before any Host work.
    with turn_lane_singleflight(runtime_root=worker.root, goal_id=worker.goal_id,
            plan={"turn_envelope": {"agent_id": binding["agent_id"]}}) as holder:
        assert holder is not None
        held = holder.read_bytes()
        queued_leases = []
        for _ in range(2):
            worker.execute(operation, prepared_source_request_id=receipt["request_id"])
            observed = _read(worker.path(operation))
            assert observed["status"] == "prepared"
            if hard_lease:
                queued_leases.append(observed["task_lease"]["lease"])
        queued = _read(worker.path(operation))
        assert queued["status"] == "prepared", queued
        assert queued["turn_result"]["reason"] == "turn_lane_in_flight"
        assert queued["identity"] == original["identity"]
        assert queued["conversation"] == original["conversation"]
        assert "turn_key" not in queued
        assert holder.read_bytes() == held
        if hard_lease:
            lease = queued["task_lease"]["lease"]
            assert queued_leases[0] == queued_leases[1]
            assert lease["status"] == "active"
            assert lease["owner"] == binding["agent_id"]
            assert lease["idempotency_key"] == worker._turn_instance_id(queued)
        else:
            assert demo.canonical_tasks(root) == tasks
        assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
        assert not (Path(binding["workspace"]) / "host-invocations").exists()
        assert not (Path(binding["workspace"]) / "DELEGATION.json").exists()
    submitted = []
    monkeypatch.setattr(execution.Delegations, "_spawn",
        lambda _self, operation_id, **kwargs: submitted.append((operation_id, kwargs)))
    resumed = execution.dispatch(worker.root, worker.registry, session=session, turn=turn,
        request=request, receipt=receipt, execution_allowed=lambda: True)
    assert resumed["submitted"] and resumed["operation_id"] == operation
    assert submitted == [(operation, {"prepared_source_request_id": receipt["request_id"]})]
    host = root / "fixture-host.py"
    host.write_text(host.read_text().replace("print(json.dumps(build_result", """
from loopx.capabilities.manager_context.roundtrip import report
source_id = delegation['source_request_id']
acknowledge(root / 'runtime', envelope['goal_id'], actor, source_id, 'adopt', 'Read original request')
report(root / 'runtime', envelope['goal_id'], actor, source_id, 'conclusion', 'Original request handled')
print(json.dumps(build_result"""))
    worker.execute(operation, prepared_source_request_id=receipt["request_id"])
    assert worker.read(operation)["status"] == "accepted"
    completed = _read(worker.path(operation))
    journals = list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text())
    assert journal["completed_phases"].count("quota_spend") == 1
    if hard_lease:
        proof = worker._cli(binding, "task-lease", "inspect", "--goal-id", worker.goal_id,
            "--todo-id", binding["todo_id"])["lease"]
        assert proof["status"] == "released"
        assert proof["idempotency_key"] == lease["idempotency_key"]
        assert proof["lease_epoch"] == lease["lease_epoch"]
    worker.execute(operation, prepared_source_request_id=receipt["request_id"])
    assert _read(worker.path(operation)) == completed
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
        response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    from loopx.capabilities.manager_context.roundtrip import drain
    sends = []

    def transport(route, *_args, **_kwargs):
        sends.append(route["session_id"])
        return {"message_id": "fixture-return", "reply_verified": True}

    drain(worker.root, worker.registry, store, transport)
    drain(worker.root, worker.registry, store, transport)
    assert sends == [session["session_id"]]


def test_native_cli_worker_keeps_a_late_busy_request_without_chat_source(delegation_service, monkeypatch):  # noqa: F811
    root, worker = delegation_service
    independent_binding(delegation_service)
    monkeypatch.setattr(worker, "_spawn", lambda _: None)
    operation = "cli-lane-race"
    worker.start("analysis", operation, brief())
    original = _read(worker.path(operation))
    binding = worker.binding("analysis")
    assert "source_request_id" not in original["identity"]
    with turn_lane_singleflight(runtime_root=worker.root, goal_id=worker.goal_id,
            plan={"turn_envelope": {"agent_id": binding["agent_id"]}}):
        worker.execute(operation)
        queued = worker.read(operation)
        assert queued["status"] == "prepared"
        assert queued["error"] == "turn_lane_in_flight"
        assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert worker.resume(operation)["operation_id"] == operation
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted"
    worker.execute(operation)
    assert _read(worker.path(operation))["identity"] == original["identity"]
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"


def test_unrelated_large_native_result_does_not_expand_the_observation(saved_handoff, monkeypatch):  # noqa: F811
    _, worker, binding, (_, _, _, _, receipt, _), operation = saved_handoff
    native = worker._cli

    def result_with_auxiliary_details(*args, **kwargs):
        result = native(*args, **kwargs)
        if args[1:3] == ("turn", "run-once"):
            # A later native result may carry large evidence/settlement detail;
            # it is not input to delegation's status rule or another RPC grant.
            result["auxiliary_detail"] = "x" * (3 * 1024 * 1024)
            result["host"]["auxiliary_detail"] = "y" * (3 * 1024 * 1024)
        return result

    monkeypatch.setattr(worker, "_cli", result_with_auxiliary_details)
    with turn_lane_singleflight(runtime_root=worker.root, goal_id=worker.goal_id,
            plan={"turn_envelope": {"agent_id": binding["agent_id"]}}):
        worker.execute(operation, prepared_source_request_id=receipt["request_id"])
    queued = worker.read(operation)
    assert queued["status"] == "prepared", queued
    assert queued["error"] == "turn_lane_in_flight"
    assert not (Path(binding["workspace"]) / "host-invocations").exists()


def test_prepared_replay_retains_original_work_while_native_lane_is_busy(saved_handoff, monkeypatch):  # noqa: F811
    root, worker, binding, (store, session, turn, request, receipt, _), operation = saved_handoff
    submitted = []
    inspected = []
    inspect = execution.Delegations.inspect

    def native_preflight(service, binding_id):
        inspected.append(binding_id)
        return inspect(service, binding_id)

    monkeypatch.setattr(execution.Delegations, "inspect", native_preflight)
    monkeypatch.setattr(execution.Delegations, "_spawn",
        lambda _self, operation_id, **kwargs: submitted.append((operation_id, kwargs)))
    original = worker.path(operation).read_bytes()
    tasks = demo.canonical_tasks(root)
    with turn_lane_singleflight(runtime_root=worker.root, goal_id=worker.goal_id,
            plan={"turn_envelope": {"agent_id": binding["agent_id"]}}) as holder:
        assert holder is not None
        held = holder.read_bytes()
        for _ in range(2):
            refused = execution.dispatch(worker.root, worker.registry, session=session, turn=turn,
                request=request, receipt=receipt, execution_allowed=lambda: True)
            assert refused["submitted"] is False, refused
            assert refused["preflight"]["reason"] == "turn_lane_in_flight"
            assert refused["operation_id"] == operation and refused["status"] == "prepared"
        assert not submitted
        assert holder.read_bytes() == held
        assert worker.path(operation).read_bytes() == original
        assert demo.canonical_tasks(root) == tasks
        assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    recovered = execution.dispatch(worker.root, worker.registry, session=session, turn=turn,
        request=request, receipt=receipt, execution_allowed=lambda: True)
    assert recovered["submitted"] and recovered["operation_id"] == operation
    assert inspected == [binding["id"]] * 3
    assert submitted == [(operation, {"prepared_source_request_id": receipt["request_id"]})]
    # The same real fixture Host now reads and answers the original source,
    # not merely the separate peer execution request.
    host = root / "fixture-host.py"
    host.write_text(host.read_text().replace("print(json.dumps(build_result", """
from loopx.control_plane.collaboration.peers import read_inbox
from loopx.capabilities.manager_context.roundtrip import report
source_id = delegation['source_request_id']
items = read_inbox(root / 'runtime', root / 'registry.json', envelope['goal_id'], actor)['items']
assert any(item['request_id'] == source_id for item in items)
acknowledge(root / 'runtime', envelope['goal_id'], actor, source_id, 'adopt', 'Read original request')
report(root / 'runtime', envelope['goal_id'], actor, source_id, 'conclusion', 'Original request handled')
print(json.dumps(build_result"""))
    worker.execute(operation, prepared_source_request_id=receipt["request_id"])
    assert worker.read(operation)["status"] == "accepted"
    terminal = worker.path(operation).read_bytes()
    # Terminal recovery observes the accepted original operation; a completed
    # task's new-admission refusal cannot hide it or cause another submission.
    replay = execution.dispatch(worker.root, worker.registry, session=session, turn=turn,
        request=request, receipt=receipt, execution_allowed=lambda: True)
    assert replay["submitted"] and replay["status"] == "accepted"
    assert worker.path(operation).read_bytes() == terminal
    assert len(submitted) == 1
    assert len(inspected) == 3
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
        response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    from loopx.capabilities.manager_context.roundtrip import drain
    sends = []

    def transport(route, *_args, **_kwargs):
        sends.append(route["session_id"])
        return {"message_id": "fixture-return", "reply_verified": True}

    drain(worker.root, worker.registry, store, transport)
    drain(worker.root, worker.registry, store, transport)
    assert sends == [session["session_id"]]
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
