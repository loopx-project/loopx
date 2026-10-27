"""Replaying a Chat handoff must recover that exact original operation.

Use real Inbox, Chat and File/SQLite owners. A saved operation id alone cannot
identify a newly selected binding, substitute a brief or redirect the return.
"""

import copy
import json
from pathlib import Path

import pytest

from loopx.capabilities.manager_context import _root, _write, execution
from loopx.control_plane.collaboration.inbox import _read
from loopx.control_plane.collaboration.peers import request as peer_request
from loopx.control_plane.collaboration.source_grant_observation import read_delegation_source_execution
from loopx.control_plane.turn_driver.lane_fence import turn_lane_singleflight
from test_delegation_source_authority import saved_handoff  # noqa: F401
from test_local_delegation import demo, service as delegation_service  # noqa: F401


def durable_bytes(root):
    # Scoped read locks update diagnostic holder timestamps, not business state.
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not p.name.endswith(".lock")}


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
