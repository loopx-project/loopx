"""An ordinary request keeps its identity without inventing a canonical task."""
# ruff: noqa: F811 - import the existing parametrized real-backend fixture
import json
import sys
from pathlib import Path

import pytest

from test_local_delegation import HOST, brief

from test_local_delegation import service as delegation_service  # noqa: F401 - shared real File/SQLite fixture

ORIGINAL_MESSAGE = "求 17+25 的结果，保留原消息。\n" * 100 + "END 🧭"


def test_operator_binding_does_not_require_a_precreated_todo(delegation_service):
    _, runner = delegation_service
    config = json.loads(runner.config.read_text())
    config["bindings"][0].pop("todo_id")
    runner.config.write_text(json.dumps(config))

    binding = runner.binding("analysis")
    assert binding["agent_id"] == "analyst"
    assert binding.get("todo_id") is None


def _ordinary_binding(delegation_service):
    root, runner = delegation_service
    config = json.loads(runner.config.read_text())
    binding = config["bindings"][0]
    binding.pop("todo_id")
    binding["request_validation"] = {
        "validation_command": None,
        "validation_command_argv": [sys.executable, "-c",
            "import json; from pathlib import Path; assert json.loads(Path('output.json').read_text()) == {'answer': 42}"],
        "validation_label": "Independent arithmetic result",
        "validation_timeout_seconds": 10,
    }
    runner.config.write_text(json.dumps(config))
    source = HOST.replace("delegation = json.loads((workspace / 'DELEGATION.json').read_text())",
        "delegation = next(s['content'] for s in envelope['work_context']['sources'] if s.get('source') == 'delegation_work')")
    source = source.replace("print(json.dumps(build_result", """
(workspace / 'output.json').write_text(json.dumps({'answer': 17 + 25}))
(workspace / 'received-request.json').write_text(json.dumps(request))
print(json.dumps(build_result""")
    (root / "fixture-host.py").write_text(source)
    return root, runner, runner.binding("analysis")


def _exact_goal_service(delegation_service):
    from loopx.collaboration_mcp import Delegations
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    root, runner = delegation_service
    payload = json.loads(runner.registry.read_text())
    payload.update(profile_id="source_session_v1", session_bindings=[], session_receipts=[], lifetime_receipts=[])
    payload["goals"][0]["goal_instance_id"] = "ginst_" + "a" * 32
    # Replace only this disposable fixture's legacy document using the v2 writer.
    runner.registry.unlink()
    with source_session_registry_transaction(runner.registry, operation="exact-request-fixture",
                                            create=lambda: payload) as transaction:
        transaction.commit(transaction.payload_copy())
    return root, Delegations(runner.root, runner.registry, runner.goal_id, runner.agent_id, runner.config)


def test_original_request_executes_and_settles_without_changing_todos(delegation_service, monkeypatch):
    root, runner, binding = _ordinary_binding(delegation_service)
    before = runner._cli(binding, "todo", "list", "--goal-id", runner.goal_id)["todos"]
    monkeypatch.setattr(runner, "_spawn", lambda operation: None)
    initial = runner.start("analysis", "ordinary-request", brief())
    assert initial["todo_id"] is None
    runner.execute("ordinary-request")
    result = runner.read("ordinary-request")
    assert result["status"] == "accepted", json.dumps(result, indent=2)
    assert result["request_id"] == initial["request_id"]
    assert runner._cli(binding, "todo", "list", "--goal-id", runner.goal_id)["todos"] == before
    workspace = Path(binding["workspace"])
    assert (workspace / "host-invocations").read_text() == "1"
    received = json.loads((workspace / "received-request.json").read_text())
    identity = received["turn_envelope"]["writeback"]["settlement_plan"]["identity"]
    assert identity["binding_kind"] == "request"
    assert identity["request_subject"]["request_id"] == initial["request_id"]
    assert "todo_id" not in identity
    assert "replan_obligation_id" not in identity
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    def settlement():
        return read_heartbeat_settlement(runner.root, goal_id=runner.goal_id, agent_id=binding["agent_id"],
            todo_id=None, turn_instance_id=identity["turn_instance_id"], registry_path=runner.registry, goal_ref=runner._caller_goal_ref())
    original = settlement()
    assert original.identity.value.request_subject == identity["request_subject"]
    from loopx.control_plane.quota.heartbeat_receipt import heartbeat_receipt_view
    guard_view = heartbeat_receipt_view(original.heartbeat_receipt,
        turn_instance_id=identity["turn_instance_id"], status="settled")
    assert guard_view["settlement_identity"] == identity
    assert "settlement_binding_owed" not in guard_view
    assert original.settlement.failure is None
    assert original.writeback_run is not None and original.spend_run is not None
    runner.execute("ordinary-request")
    assert settlement().spend_run == original.spend_run
    assert (workspace / "host-invocations").read_text() == "1"
    assert runner._cli(binding, "todo", "list", "--goal-id", runner.goal_id)["todos"] == before


@pytest.mark.parametrize("with_todo", [False, True], ids=["original-request", "existing-todo"])
def test_exact_goal_request_preparation_replay_and_runtime_boundary(delegation_service, monkeypatch, with_todo):
    from loopx.control_plane.collaboration import delegation_results, delegation_validation
    from loopx.control_plane.collaboration.inbox import _read

    if with_todo:
        root, runner = delegation_service
        binding = runner.binding("analysis")
    else:
        root, runner, binding = _ordinary_binding(delegation_service)
    root, runner = _exact_goal_service((root, runner))
    monkeypatch.setattr(runner, "_spawn", lambda operation: None)
    initial = runner.start("analysis", "exact-request", brief())
    row = _read(runner.path("exact-request"))
    assert initial["status"] == "prepared" and initial["todo_id"] == binding["todo_id"]
    subject = delegation_validation.ordinary_request_subject(runner, row, binding)
    assert subject == (None if with_todo else initial["request_subject"])
    assert delegation_results.operation_brief(runner, row) == brief()
    assert runner.start("analysis", "exact-request", brief()) == initial
    # The exact profile currently admits collaboration/lifecycle only. Its
    # native runtime rejection must stay visible and must not launch a Host.
    runner.execute("exact-request")
    rejected = runner.read("exact-request")
    assert rejected["status"] == "rejected"
    assert "lifecycle-only profile source_session_v1" in rejected["error"]
    runner.execute("exact-request")
    assert runner.read("exact-request") == rejected
    assert not (Path(binding["workspace"]) / "host-invocations").exists()


@pytest.mark.parametrize("new_service", [False, True], ids=["captured-service", "restarted-service"])
def test_recreated_goal_cannot_execute_the_original_request(delegation_service, monkeypatch, new_service):
    from loopx.collaboration_mcp import Delegations
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    root, runner, binding = _ordinary_binding(delegation_service)
    root, runner = _exact_goal_service((root, runner))
    historical = runner
    monkeypatch.setattr(runner, "_spawn", lambda operation: None)
    initial = runner.start("analysis", "recreated-request", brief())
    with source_session_registry_transaction(runner.registry, operation="recreated-request-fixture") as transaction:
        payload = transaction.payload_copy()
        payload["goals"][0]["goal_instance_id"] = "ginst_" + "b" * 32
        transaction.commit(payload)
    if new_service:
        runner = Delegations(runner.root, runner.registry, runner.goal_id, runner.agent_id, runner.config)
    runner.execute("recreated-request")
    result = historical.read("recreated-request")
    assert result["status"] == "rejected"
    expected_error = "unavailable" if new_service else "historical_mutation_forbidden"
    assert expected_error in result["error"]
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert result["request_id"] == initial["request_id"]


@pytest.fixture
def ordinary_source(delegation_service, monkeypatch, request):
    from loopx.capabilities.manager_context import deliver, execution
    from test_manager_context_execution import source

    root, runner, binding = _ordinary_binding(delegation_service)
    if getattr(request, "param", None) == "codex":
        from test_loopx_turn_codex_cli import _fake_codex
        executable, log = _fake_codex(root)
        monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
        host_source = executable.read_text().replace('output_path = pathlib.Path', f'''
from loopx.control_plane.collaboration.peers import read_inbox, return_result
from loopx.control_plane.collaboration.inbox import acknowledge
from loopx.capabilities.manager_context.roundtrip import report
root = pathlib.Path({str(root)!r})
assert {json.dumps(ORIGINAL_MESSAGE, ensure_ascii=False)[1:-1]!r} in prompt
pathlib.Path('output.json').write_text(json.dumps({{'answer': 17 + 25}}))
for item in read_inbox(root / 'runtime', root / 'registry.json', {runner.goal_id!r}, 'analyst')['items']:
    acknowledge(root / 'runtime', {runner.goal_id!r}, 'analyst', item['request_id'], 'adopt', 'Read complete native request.')
    if item.get('source_kind') == 'peer':
        return_result(root / 'runtime', {runner.goal_id!r}, 'analyst', item['request_id'], '42')
    else:
        report(root / 'runtime', {runner.goal_id!r}, 'analyst', item['request_id'], 'conclusion', '42')
output_path = pathlib.Path''')
        executable.write_text(host_source)
        config_data = json.loads(runner.config.read_text())
        config_data["bindings"][0]["host_args"] = ["--host", "codex-cli", "--codex-bin", str(executable)]
        runner.config.write_text(json.dumps(config_data))
        binding = runner.binding("analysis")
    data = json.loads(runner.registry.read_text())
    config = Path(data["goals"][0]["repo"]) / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(runner.config.read_bytes())
    data["goals"][0]["spawn_policy"] = {"execution_config": ".loopx/config/delegations.json"}
    runner.registry.write_text(json.dumps(data))
    values = source(runner.root, runner.registry, goal_id=runner.goal_id,
        agent_id="analyst", requester="lead", binding="analysis",
        original_message=ORIGINAL_MESSAGE)
    store, session, turn, request, receipt, policy = values
    deliver(runner.root, runner.registry, session=session, turn=turn, request=request, source_store=store)
    host = root / "fixture-host.py"
    host.write_text(host.read_text().replace("print(json.dumps(build_result", """
from loopx.capabilities.manager_context.roundtrip import report
original = delegation['source_request']
acknowledge(root / 'runtime', envelope['goal_id'], actor, original['request_id'], 'adopt', 'Read the original requested arithmetic.')
report(root / 'runtime', envelope['goal_id'], actor, original['request_id'], 'conclusion', 'The independently checked result is 42.')
print(json.dumps(build_result"""))
    monkeypatch.setattr(execution.Delegations, "_spawn", lambda *_: None)
    submitted = execution.dispatch(runner.root, runner.registry, session=session, turn=turn,
        request=request, receipt=receipt, execution_allowed=lambda: True)
    assert submitted["submitted"] and submitted["status"] == "prepared", submitted
    worker, binding = execution._service(runner.root, runner.registry, {
        "goal_id": runner.goal_id, "agent_id": "analyst", "requester_agent_id": "lead", "binding_id": "analysis"})
    return root, worker, binding, values, submitted["operation_id"]


@pytest.mark.parametrize("ordinary_source", ["codex"], indirect=True)
def test_request_uses_the_actual_codex_launch_and_original_settlement(ordinary_source):
    root, worker, _, _, operation = ordinary_source
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted", worker.read(operation)
    worker.execute(operation)
    assert len((root / "codex-argv.jsonl").read_text().splitlines()) == 1


def test_original_chat_request_returns_without_creating_a_task(ordinary_source):
    from test_local_delegation import demo
    from loopx.capabilities.manager_context.roundtrip import drain

    root, worker, binding, (store, session, turn, request, receipt, _), operation = ordinary_source
    before = demo.canonical_tasks(root)
    worker.execute(operation)
    result = worker.read(operation)
    assert result["status"] == "accepted", result
    assert result["todo_id"] is None
    assert result["request_subject"]["request_id"] == receipt["request_id"]
    received = json.loads((Path(binding["workspace"]) / "received-request.json").read_text())
    content = next(s["content"] for s in received["turn_envelope"]["work_context"]["sources"]
        if s.get("source") == "delegation_work")
    assert content["source_request"]["message"] == ORIGINAL_MESSAGE
    assert content["source_request"]["brief"] == request["brief"]
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
        response={"context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    sends = []
    def transport(route, *args, **kwargs):
        sends.append(route["session_id"])
        return {"message_id": "original-request-return", "reply_verified": True}
    drain(worker.root, worker.registry, store, transport)
    drain(worker.root, worker.registry, store, transport)
    assert sends == [session["session_id"]]
    worker.execute(operation)
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    assert demo.canonical_tasks(root) == before


def test_original_request_runs_with_an_empty_canonical_task_population(ordinary_source):
    from canonical_authority_fixture import advance_canonical_fixture_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from test_local_delegation import demo
    root, worker, _, _, operation = ordinary_source
    # Only this disposable fixture is advanced, through the real provider CAS.
    state = root / "project" / "ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("- Validate synthetic evidence.", ""))
    advance_canonical_fixture_authority(worker.root, worker.goal_id,
        build_todo_runtime_shadow_projection(goal_id=worker.goal_id, handoff_mode="soft_claim", todos=[]),
        operation_id="empty-request-fixture")
    assert demo.canonical_tasks(root) == {}
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted", worker.read(operation)
    assert demo.canonical_tasks(root) == {}


def test_request_revocation_after_host_holds_settlement_and_recovers_original_turn(ordinary_source):
    from loopx.control_plane.collaboration.inbox import _read, _root, _write
    from test_local_delegation import demo

    root, worker, binding, (_, session, _, _, _, policy), operation = ordinary_source
    before = demo.canonical_tasks(root)
    original = json.loads(json.dumps(policy))
    policy["sources"][session["channel_id"]]["execution_bindings"] = []
    host = root / "fixture-host.py"
    host.write_text(host.read_text().replace("print(json.dumps(build_result",
        f"(root / 'runtime' / '.local' / 'manager-context' / 'policy.json').write_text({json.dumps(json.dumps(policy))})\nprint(json.dumps(build_result"))
    worker.execute(operation)
    held = _read(worker.path(operation))
    assert held["status"] == "turn_returned", held
    journals = list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
    assert len(journals) == 1
    journal = _read(journals[0])
    assert journal["completed_phases"] == ["host_execute", "typed_result", "validation"]
    assert journal["task_validation"]["ok"]
    assert demo.canonical_tasks(root) == before
    _write(_root(worker.root) / "policy.json", original)
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted"
    assert _read(journals[0])["turn_key"] == journal["turn_key"]
    assert _read(journals[0])["completed_phases"].count("quota_spend") == 1
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    assert demo.canonical_tasks(root) == before


def test_busy_request_retains_its_identity_and_runs_after_restart(ordinary_source):
    from loopx.control_plane.turn_driver.lane_fence import turn_lane_singleflight
    from loopx.collaboration_mcp import Delegations
    from test_local_delegation import demo

    root, worker, binding, _, operation = ordinary_source
    before = demo.canonical_tasks(root)
    identity = json.loads(worker.path(operation).read_text())["identity"]
    with turn_lane_singleflight(runtime_root=worker.root, goal_id=worker.goal_id,
            plan={"turn_envelope": {"agent_id": binding["agent_id"]}}):
        worker.execute(operation)
        row = json.loads(worker.path(operation).read_text())
        assert row["status"] == "prepared" and row["identity"] == identity
        assert not (Path(binding["workspace"]) / "host-invocations").exists()
    restarted = Delegations(worker.root, worker.registry, worker.goal_id, worker.agent_id, worker.config)
    restarted.execute(operation)
    restarted.execute(operation)
    assert restarted.read(operation)["status"] == "accepted"
    assert json.loads(worker.path(operation).read_text())["identity"] == identity
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    assert demo.canonical_tasks(root) == before


def test_lost_request_reply_recovers_without_repeating_host(ordinary_source, monkeypatch):
    import subprocess
    from test_local_delegation import demo

    root, worker, binding, _, operation = ordinary_source
    before = demo.canonical_tasks(root)
    record = worker._record_turn_result
    def lose_reply(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("committed request reply", 1)
    monkeypatch.setattr(worker, "_record_turn_result", lose_reply)
    worker.execute(operation)
    assert json.loads(worker.path(operation).read_text())["status"] == "running"
    monkeypatch.setattr(worker, "_record_turn_result", record)
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted"
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"
    assert demo.canonical_tasks(root) == before


def test_failed_request_rechecks_the_original_artifact_without_repeating_host(ordinary_source):
    root, worker, binding, _, operation = ordinary_source
    host = root / "fixture-host.py"
    host.write_text(host.read_text().replace("17 + 25", "17 + 24"))
    worker.execute(operation)
    rejected = worker.read(operation)
    assert rejected["status"] == "rejected" and rejected["task_failure"]["stage"] == "task_postcondition", rejected
    (Path(binding["workspace"]) / "output.json").write_text(json.dumps({"answer": 42}))
    worker.revalidate(operation)
    worker.execute(operation)
    assert worker.read(operation)["status"] == "accepted"
    assert (Path(binding["workspace"]) / "host-invocations").read_text() == "1"


def test_stop_before_request_host_keeps_all_tasks_and_effects_unchanged(ordinary_source):
    from test_local_delegation import demo
    root, worker, binding, _, operation = ordinary_source
    before = demo.canonical_tasks(root)
    worker.stop(operation, execute=True)
    worker.execute(operation)
    assert not (Path(binding["workspace"]) / "host-invocations").exists()
    assert demo.canonical_tasks(root) == before
    assert not list((worker.root / "goals" / worker.goal_id / "turns").glob("*.json"))
