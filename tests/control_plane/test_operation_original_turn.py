"""Original-owner wake is a single canonical intent, never self-delegation."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from examples import operation_action_fixtures as fixtures
from loopx.chat_action_store import ActionConflictError
from loopx.control_plane.collaboration.operation_wake import dispatch_confirmed_operation_wake


def fixture(tmp_path):
    api, store = fixtures.service(tmp_path)
    handler = fixtures.managed_handler(api, store)
    request = fixtures.request()
    request["normalized_parameters"].pop("executor")
    request["normalized_parameters"]["projection"]["simulated"] = False
    prepared = handler("loopx_operation", {"action": "prepare", "request": request},
                       {"thread_id": "owned-managed-thread", "host_turn_id": "native-fixture"})
    assert prepared["ok"], prepared
    proposal = prepared["proposal"]
    delivered = store.record_operation_delivery(proposal["proposal_id"], delivery=fixtures.delivery(proposal))
    confirmed = store.decide_operation(proposal["proposal_id"], decision="confirm",
                                      confirmation=fixtures.confirmation(delivered))
    worker = tmp_path / "worker"
    worker.mkdir()
    binding = {"id": "original", "continuation_kind": "original_operation",
        "agent_id": "finance-fixture-agent", "todo_id": "todo-managed",
        "requesters": ["finance-fixture-agent"], "workspace": str(worker),
        "timeout_seconds": 30, "output_refs": [], "host_args": [
            "--host", "codex-cli", "--codex-operation-tools", "--codex-model", "test-model",
            "--codex-reasoning-effort", "xhigh"]}
    config = api.registry_path.parent / "delegations.json"
    config.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": [binding]}))
    wake = {"registry_path": str(api.registry_path), "goal_id": fixtures.GOAL_ID,
            "requester_agent_id": "finance-fixture-agent", "project": str(api.registry_path.parent.parent),
            "execution_config": ".loopx/delegations.json", "binding_id": "original"}
    return api, store, confirmed, config, binding, wake


@pytest.mark.parametrize("lost_spawn", [False, True])
def test_single_launch_and_lost_ack_never_relaunch_or_consume(tmp_path, monkeypatch, lost_spawn):
    api, store, proposal, config, binding, wake = fixture(tmp_path)
    launches = []

    def spawn(argv, **kwargs):
        launches.append((argv, kwargs))
        if lost_spawn:
            raise OSError("private spawn diagnostic must not escape")

    monkeypatch.setattr("loopx.control_plane.collaboration.operation_wake.subprocess.Popen", spawn)

    def dispatch():
        return dispatch_confirmed_operation_wake(proposal, runtime_root=store.root.parent.parent, configuration=wake)

    with ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(lambda _: dispatch(), range(3)))
    assert len(launches) == 1
    states = sorted(row["state"] for row in receipts)
    assert states == sorted(["existing_turn", "existing_turn", "launch_uncertain" if lost_spawn else "turn_requested"])
    stored = store.load(proposal["proposal_id"])
    assert stored["operation"]["lifecycle_state"] == "claimed"
    assert stored["operation"].get("host_start") is None
    assert stored["operation"].get("agent_handoff") is None
    assert stored["operation"]["outcome"] is None
    assert all(row["execution_allowed"] is False and row["native_start_verified"] is False for row in receipts)
    assert "private spawn" not in json.dumps(receipts)
    argv, kwargs = launches[0]
    assert argv[argv.index("--confirmed-operation-id") + 1] == proposal["proposal_id"]
    assert argv[argv.index("--todo-id") + 1] == "todo-managed"
    assert "--validation-command-json" not in argv
    assert "--manual-interval-bypass-reason" in argv
    assert kwargs["cwd"] == Path(binding["workspace"])
    # Removing this grant stops callbacks; no fallback delegation or new process.
    config.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": []}))
    assert dispatch()["state"] == "blocked"
    assert len(launches) == 1


@pytest.mark.parametrize("fault", ["grant", "owner", "todo", "fresh", "retarget", "profile", "scope", "stopped", "timeout", "runner"])
def test_invalid_original_route_is_refused_before_launch_intent(tmp_path, monkeypatch, fault):
    api, store, proposal, config, binding, wake = fixture(tmp_path)
    if fault == "grant":
        binding["requesters"] = []
    elif fault == "owner":
        binding["agent_id"] = "other"
    elif fault == "todo":
        binding["todo_id"] = "other"
    elif fault == "fresh":
        binding["host_args"] += ["--iteration-context", "fresh"]
    elif fault == "retarget":
        binding["host_args"] += ["--agent-id", "other"]
    elif fault == "timeout":
        binding["host_args"] += ["--timeout-seconds", "3600"]
    elif fault == "runner":
        binding["host_args"] += ["--dsh-runner", "unqualified.test.runner"]
    elif fault == "profile":
        binding["host_args"][binding["host_args"].index("--codex-model") + 1] = "different"
    elif fault == "scope":
        wake["goal_id"] = "different"
    elif fault == "stopped":
        data = json.loads(api.registry_path.read_text())
        data["goals"][0]["status"] = "stopped"
        api.registry_path.write_text(json.dumps(data))
    config.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": [binding]}))
    monkeypatch.setattr("loopx.control_plane.collaboration.operation_wake.subprocess.Popen",
                        lambda *a, **k: pytest.fail("invalid route launched"))
    result = dispatch_confirmed_operation_wake(proposal, runtime_root=store.root.parent.parent, configuration=wake)
    assert result["state"] == ("out_of_scope" if fault == "scope" else "blocked")
    assert store.load(proposal["proposal_id"])["operation"].get("turn_launch") is None


def test_reserved_intent_refuses_configuration_drift_and_unconfirmed_expired_input(tmp_path):
    _api, store, proposal, _config, binding, _wake = fixture(tmp_path)
    route = {"goal_id": fixtures.GOAL_ID, "agent_id": "finance-fixture-agent",
        "todo_id": "todo-managed", "host_surface": "loopx-managed-codex", "thread_id": "owned-managed-thread",
        "profile_digest": "c" * 64, "model": "test-model", "reasoning_effort": "xhigh"}
    options = {"binding_current": True, "requester_agent_id": binding["agent_id"],
        "operator_binding": binding, "turn_launch": {"binding_id": binding["id"],
            "turn_instance_id": "operation-wake-original", "configuration_digest": hashlib.sha256(b"operator config").hexdigest()},
        "launch_context": {"host": "codex-cli", "operation_tools": True, "iteration_context": "resume"},
        "executor_route": route}
    first = store.reserve_agent_operation_turn(proposal["proposal_id"], **options)
    assert first["launch_new_turn"] is True
    options["turn_launch"]["configuration_digest"] = "b" * 64
    with pytest.raises(ActionConflictError, match="launch changed"):
        store.reserve_agent_operation_turn(proposal["proposal_id"], **options)
    # The typed owner, not Python checks, owns both refusal invariants.
    for mutate in [lambda p: p["operation"].update(confirmation=None),
                   lambda p: p["operation"].pop("turn_launch")]:
        copied = json.loads(json.dumps(store.load(proposal["proposal_id"])))
        mutate(copied)
        if copied["operation"].get("turn_launch") is None:
            options["now"] = "2100-01-01T00:00:00Z"
        with pytest.raises(ActionConflictError):
            store._agent_operation_plan(copied, action="wake", **options)
