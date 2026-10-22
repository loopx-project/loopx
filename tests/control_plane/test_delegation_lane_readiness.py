"""Delegation previews must not mistake an occupied native Turn lane for idle."""

import json
from contextlib import ExitStack
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.lane_fence import turn_lane_singleflight
from loopx.file_lock import lock_holder_path
from loopx.capabilities.manager_context import execution
from loopx.capabilities.manager_context.roundtrip import drain
from test_delegation_cli import cli
from test_independent_delegation_validation import independent_binding
from test_local_delegation import demo, service, wait  # noqa: F401
from test_manager_context_execution import source


def test_real_busy_lane_is_not_launchable_and_preview_keeps_its_holder(service):  # noqa: F811
    root, runner = service
    independent_binding(service)
    binding = runner.binding("analysis")
    before = demo.canonical_tasks(root)
    with turn_lane_singleflight(runtime_root=runner.root, goal_id=runner.goal_id,
            plan={"turn_envelope": {"agent_id": binding["agent_id"]}}) as held:
        assert held is not None
        holder = held.read_bytes()
        preview = runner.inspect("analysis")
        assert preview["state"] == "turn_blocked", json.dumps(preview)
        assert preview["turn_eligible"] is False
        assert preview["turn_lane"] == {"scope": "native_turn_lane", "state": "live"}
        status, native = cli(runner, "inspect", "--binding-id", "analysis")
        assert status == 0 and native["reason"] == "turn_lane_in_flight", native
        assert held.read_bytes() == holder
        assert not any(preview["effects"].values())
    assert demo.canonical_tasks(root) == before
    assert not (root / "analyst/initial/host-invocations").exists()
    assert not list((runner.root / "goals" / runner.goal_id / "turns").glob("*.json"))
    assert runner.inspect("analysis")["state"] == "runtime_unverified"


@pytest.mark.parametrize("damage", ["foreign_host", "unreadable"])
def test_unknown_lane_cannot_be_promoted_to_idle(service, damage):  # noqa: F811
    _, runner = service
    independent_binding(service)
    target = runner._lane_target(runner.binding("analysis"))
    with turn_lane_singleflight(runtime_root=runner.root, goal_id=runner.goal_id,
            plan={"turn_envelope": {"agent_id": "analyst"}}):
        pass
    path = lock_holder_path(target)
    released = path.read_bytes()
    if damage == "foreign_host":
        record = json.loads(released)
        record.pop("released_at", None)
        record["host"] = "another-fixture-machine"
        path.write_text(json.dumps(record))
    else:
        path.write_text("{unreadable")
    damaged = path.read_bytes()
    status, preview = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0 and preview["state"] == "turn_blocked", preview
    assert preview["reason"] == "turn_lane_state_unknown"
    assert preview["turn_lane"] == {"scope": "native_turn_lane", "state": damage}
    assert path.read_bytes() == damaged and not any(preview["effects"].values())
    path.write_bytes(released)
    assert runner.inspect("analysis")["state"] == "runtime_unverified"


def test_lane_becoming_busy_during_preview_is_observed_at_the_final_read(service, monkeypatch):  # noqa: F811
    _, runner = service
    independent_binding(service)
    native = runner._cli
    with ExitStack() as locks:
        def preview_then_occupy(*args, **kwargs):
            result = native(*args, **kwargs)
            assert locks.enter_context(turn_lane_singleflight(runtime_root=runner.root,
                goal_id=runner.goal_id, plan={"turn_envelope": {"agent_id": "analyst"}})) is not None
            return result
        monkeypatch.setattr(runner, "_cli", preview_then_occupy)
        preview = runner.inspect("analysis")
        assert preview["state"] == "turn_blocked" and not preview["turn_eligible"], preview
        assert preview["turn_lane"]["state"] == "live"


def test_another_members_lane_does_not_block_this_binding(service):  # noqa: F811
    _, runner = service
    independent_binding(service)
    with turn_lane_singleflight(runtime_root=runner.root, goal_id=runner.goal_id,
            plan={"turn_envelope": {"agent_id": "reviewer"}}):
        preview = runner.inspect("analysis")
        assert preview["state"] == "runtime_unverified", preview
        assert preview["turn_lane"] == {"scope": "native_turn_lane", "state": "absent"}


def test_busy_handoff_keeps_original_context_then_returns_once_after_release(service):  # noqa: F811
    root, runner = service
    independent_binding(service)
    data = json.loads(runner.registry.read_text())
    config = Path(data["goals"][0]["repo"]) / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(runner.config.read_bytes())
    data["goals"][0]["spawn_policy"] = {"execution_config": ".loopx/config/delegations.json"}
    runner.registry.write_text(json.dumps(data))
    store, session, turn, request, receipt, _ = source(runner.root, runner.registry,
        goal_id=runner.goal_id, agent_id="analyst", requester="lead", binding="analysis")
    # The existing fixture Host really reads and answers the original request;
    # the return transport below is synthetic, not an installed App/Lark claim.
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

    def dispatch():
        return execution.dispatch(runner.root, runner.registry, session=session, turn=turn,
            request=request, receipt=receipt, execution_allowed=lambda: True)

    operation = "context-" + receipt["request_id"]
    with turn_lane_singleflight(runtime_root=runner.root, goal_id=runner.goal_id,
            plan={"turn_envelope": {"agent_id": "analyst"}}):
        for _ in range(2):
            blocked = dispatch()
            assert not blocked["submitted"] and blocked["preflight"]["reason"] == "turn_lane_in_flight"
        assert not runner.path(operation).exists()
        assert not (root / "analyst/initial/host-invocations").exists()
    started = dispatch()
    worker = execution._service(runner.root, runner.registry, {"goal_id": runner.goal_id,
        "agent_id": "analyst", "requester_agent_id": "lead", "binding_id": "analysis"})[0]
    assert started["submitted"] and wait(worker, operation)["status"] == "accepted"
    assert dispatch()["operation_id"] == operation
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
        response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    sends = []

    def transport(route, *_args, **_kwargs):
        sends.append(route["session_id"])
        return {"message_id": "fixture-return", "reply_verified": True}

    drain(runner.root, runner.registry, store, transport)
    drain(runner.root, runner.registry, store, transport)
    assert sends == [session["session_id"]]
    assert (root / "analyst/initial/host-invocations").read_text() == "1"
