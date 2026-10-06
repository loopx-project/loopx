"""Production delegation/Turn/TS completion with an explicit fixture model host."""
import json
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from threading import Event, get_ident

import pytest
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "managed-research-team"))
import research_team as demo  # noqa: E402
from test_managed_research_scenario import fixture  # noqa: E402
from loopx.control_plane.collaboration import delegation_stop_lease as stop_lease  # noqa: E402
from loopx.collaboration_mcp import Delegations  # noqa: E402
from loopx.control_plane.collaboration.delegation_stop_signal import (  # noqa: E402
    DelegationFenced, DelegationStopRequested,
)
from loopx.control_plane.collaboration.peers import returns  # noqa: E402
from loopx.control_plane.collaboration.inbox import _read  # noqa: E402
from loopx.control_plane.turn_driver.lane_fence import turn_lane_liveness, turn_lane_singleflight  # noqa: E402
from loopx.file_lock import exclusive_file_lock, try_exclusive_file_lock  # noqa: E402
from loopx.file_lock import lock_holder_host_label  # noqa: E402


HOST = '''import json, os, sys, time
from pathlib import Path
from loopx.control_plane.turn_driver.host_candidate import build_result
from loopx.control_plane.collaboration.inbox import acknowledge
from loopx.control_plane.collaboration.peers import return_result
request = json.load(sys.stdin)
workspace = Path.cwd()
root = Path(sys.argv[1])
envelope = request['turn_envelope']
actor = envelope['agent_id']
counter = workspace / 'host-invocations'
counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
if (root / 'ignore-term').exists():
    import signal, subprocess
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)'])
    (root / 'host-child-pid').write_text(str(child.pid))
if (root / 'hold').exists():
    (root / 'host-pid').write_text(str(os.getpid()))
    (root / 'host-started').touch()
    while not (root / 'release').exists(): time.sleep(0.1)
delegation = json.loads((workspace / 'DELEGATION.json').read_text())
if not (root / 'skip-adoption').exists():
    acknowledge(root / 'runtime', envelope['goal_id'], actor, delegation['request_id'], 'adopt', 'Independently checked the requested scope.')
    return_result(root / 'runtime', envelope['goal_id'], actor, delegation['request_id'], 'Independent member conclusion; host acceptance is separate.')
print(json.dumps(build_result(request, {'result_kind':'validated_progress', 'classification':'artifact_written', 'summary':'Fixture host supplied output for independent verification.', 'next_action':'Return verified evidence.'}, host_name='Fixture')))
'''


@pytest.fixture(params=["file", "sqlite"])
def service(tmp_path, request, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    # Each case owns a private server. Do not accumulate five-minute idle
    # runtimes across the delegation suite, including failed setup/test cases.
    runtime_env = os.environ.copy()
    run_cleanup = subprocess.run  # Tests may replace the shared module before teardown.
    def retire_runtime():
        run_cleanup([
            sys.executable, "-c",
            "from pathlib import Path; import sys; "
            "from loopx.control_plane.effect_runtime import _runtime_dir, restart_effect_runtime; "
            "assert _runtime_dir().parent == Path(sys.argv[1]); "
            "result = restart_effect_runtime(); "
            "assert result['status'] in {'stopped', 'not_running'}, result",
            str(tmp_path),
        ], cwd=Path(__file__).resolve().parents[1], env=runtime_env,
            capture_output=True, text=True, timeout=30, check=True)
    request.addfinalizer(retire_runtime)
    root = tmp_path / "team"
    demo.prepare(root, provider=request.param)
    fixture(root)
    host = root / "fixture-host.py"
    host.write_text(HOST)
    config = root / "delegations.json"
    config.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": [{
        "id": "analysis", "agent_id": "analyst", "todo_id": "todo_analyst-initial", "requesters": ["lead"],
        "workspace": str(root / "analyst" / "initial"), "timeout_seconds": 60, "output_refs": ["output.json"],
        "host_args": ["--host", "generic-cli", "--iteration-context", "fresh", "--host-command-json",
                      json.dumps([sys.executable, str(host), str(root)])],
    }]}))
    return root, Delegations(root / "runtime", root / "registry.json", demo.GOAL, "lead", config)


def brief():
    return {"schema_version": "collaboration_brief_v0", "purpose": "Review synthetic cash flow",
            "context": "Use the initial filing and preserve the period distinction.",
            "constraints": ["No external actions"], "inputs": [], "acceptance": ["Pinned task validation"],
            "return_requirement": "Return the independently checked artifact"}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_delegation_fixture_isolates_cached_and_child_runtime_routes(tmp_path, monkeypatch, provider):
    """A warmed parent must use the same private Effect server as its CLI."""
    from types import SimpleNamespace

    from loopx.control_plane.effect_runtime import (
        _runtime_dir, _serving_runtime_identity, restart_effect_runtime, effect_runtime_result,
    )

    cached, isolated = tmp_path / "cached", tmp_path / "isolated"
    cached.mkdir()
    isolated.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(cached))
    finalizers = []
    service.__wrapped__(isolated, SimpleNamespace(param=provider, addfinalizer=finalizers.append), monkeypatch)

    assert _runtime_dir().parent == isolated
    child = subprocess.check_output([
        sys.executable, "-c",
        "from loopx.control_plane.effect_runtime import _runtime_dir; print(_runtime_dir())",
    ], text=True).strip()
    assert Path(child) == _runtime_dir()
    effect_runtime_result("runtime.ping", {})
    assert _serving_runtime_identity() is not None
    try:
        # Teardown targets the captured route, even if another test changed
        # the parent cache/environment. A neighboring runtime must survive.
        with monkeypatch.context() as neighbor:
            isolate_sqlite_runtime(cached, neighbor)
            neighbor_pid = effect_runtime_result("runtime.ping", {})["pid"]
            try:
                with monkeypatch.context() as mocked_calls:
                    mocked_calls.setattr(subprocess, "run", lambda *args, **kwargs:
                                         pytest.fail("teardown must retain its original runner"))
                    for finalize in reversed(finalizers):
                        finalize()
                assert effect_runtime_result("runtime.ping", {})["pid"] == neighbor_pid
            finally:
                restart_effect_runtime()
        assert _serving_runtime_identity() is None, "fixture must retire its private runtime before the next case"
    finally:
        restart_effect_runtime()


def test_delegation_fixture_retires_runtime_after_setup_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from loopx.control_plane.effect_runtime import (
        _serving_runtime_identity, restart_effect_runtime, effect_runtime_result,
    )

    finalizers = []
    def fail_after_runtime_start(*args, **kwargs):
        effect_runtime_result("runtime.ping", {})
        raise RuntimeError("fixture setup interrupted")

    monkeypatch.setattr(demo, "prepare", fail_after_runtime_start)
    try:
        with pytest.raises(RuntimeError, match="fixture setup interrupted"):
            service.__wrapped__(tmp_path, SimpleNamespace(param="file", addfinalizer=finalizers.append), monkeypatch)
        assert _serving_runtime_identity() is not None
        assert len(finalizers) == 1
        for finalize in reversed(finalizers):
            finalize()
        assert _serving_runtime_identity() is None
    finally:
        restart_effect_runtime()


@pytest.mark.parametrize("operation", ["--help", "x y", "x\ny", "x;echo", "x/../y"])
def test_worker_rejects_unbounded_operation_arguments(tmp_path, monkeypatch, operation):
    from loopx import collaboration_mcp as delegation

    runner = Delegations(tmp_path, tmp_path / "registry.json", "goal", "lead", tmp_path / "config.json")
    calls = []
    monkeypatch.setattr(delegation.subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="stable peer operation id"):
        runner._spawn(operation)
    assert calls == []


def test_delegation_captures_goal_ref_after_registry_becomes_available(tmp_path, monkeypatch):
    from loopx import collaboration_mcp as delegation

    runner = Delegations(
        tmp_path,
        tmp_path / "registry.json",
        "goal",
        "lead",
        tmp_path / "config.json",
    )
    captured = {"goal_id": "goal", "goal_instance_id": "instance-a"}
    calls = []
    monkeypatch.setattr(
        delegation,
        "capture_collaboration_goal_ref",
        lambda *args, **kwargs: calls.append((args, kwargs)) or captured,
    )

    assert runner._caller_goal_ref() == captured
    assert runner._caller_goal_ref() == captured
    assert len(calls) == 1


def test_worker_waits_for_a_transient_status_probe(service, monkeypatch):
    """A reader temporarily holding the lock must not discard admitted work."""
    from loopx import collaboration_mcp as delegation

    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-1", brief())
    path = runner.path("analysis-1")
    attempted = Event()
    main_thread = get_ident()
    executed = []

    @contextmanager
    def observed_lock(target, **kwargs):
        if target == path and get_ident() != main_thread:
            attempted.set()
        with exclusive_file_lock(target, **kwargs) as held:
            yield held

    monkeypatch.setattr(delegation, "exclusive_file_lock", observed_lock)
    monkeypatch.setattr(runner, "_execute", lambda *args: executed.append("ran"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with exclusive_file_lock(path):
            future = pool.submit(runner.execute, "analysis-1")
            assert attempted.wait(5)
            with pytest.raises(FutureTimeout):
                future.result(timeout=0.2)
        future.result(timeout=10)
    assert executed == ["ran"]


def wait(service, operation="analysis-1"):
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        result = service.read(operation)
        if result["status"] in {"accepted", "rejected"}:
            return result
        if result.get("error"):
            pytest.fail(str(result))
        time.sleep(0.25)
    pytest.fail(str(service.read(operation)))


@pytest.mark.parametrize("mcp_profile", ["delegation-default", "explicit-null", "unmatched-null"])
def test_confirmed_wake_reaches_native_acceptance_through_real_delegation_and_turn(service, mcp_profile):
    """Real detached worker/CLI/Turn; synthetic native transport, no model/effect."""
    from examples import operation_action_fixtures as fixtures
    from loopx.chat_action_store import ChatActionStore
    from loopx.cli import build_parser
    from loopx.control_plane.collaboration.operation_wake import dispatch_confirmed_operation_wake
    from loopx.control_plane.turn_driver.codex_cli import load_codex_cli_session
    from loopx.control_plane.turn_driver.codex_operation_host import run_codex_operation_host, operation_tool_handler
    from test_codex_operation_host import FAKE_SERVER
    from test_loopx_turn_codex_cli import _request

    root, runner = service
    executable = root / "fixture-codex"
    native_starts = root / "native-starts"
    startup_probe = (
        "from pathlib import Path\n"
        f"counter = Path({str(native_starts)!r})\n"
        "counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))\n"
    )
    executable.write_text(FAKE_SERVER.replace('thread = "owned-app-server-thread"',
        startup_probe + 'thread = "owned-app-server-thread"'))
    executable.chmod(0o700)
    config = json.loads(runner.config.read_text())
    binding = config["bindings"][0]
    binding["host_args"] = ["--host", "codex-cli", "--codex-bin", str(executable),
        "--codex-operation-tools", "--codex-model", "test-model", "--codex-reasoning-effort", "xhigh"]
    if mcp_profile == "explicit-null":
        # Existing operator option, not a new profile or a worker-selected grant.
        binding["host_args"] += ["--codex-mcp-server-json", "null"]
    runner.config.write_text(json.dumps(config))
    argv = runner._execution_arguments(binding, "preparation")
    parsed = build_parser().parse_args(["turn", "run-once", "--goal-id", runner.goal_id,
        "--agent-id", binding["agent_id"], "--todo-id", binding["todo_id"], *argv])
    if mcp_profile == "explicit-null":
        assert argv.count("--codex-mcp-server-json") == 2
        assert parsed.codex_mcp_server_json is None
    else:
        assert parsed.codex_mcp_server_json["name"] == "loopx_delegation"
    # Reproduce an original standalone operation Session: do NOT prepare it
    # with the injected delegation MCP just to make the later resume match.
    mcp_server = parsed.codex_mcp_server_json if mcp_profile == "delegation-default" else None
    lineage = {"goal_id": runner.goal_id, "agent_id": binding["agent_id"], "todo_id": binding["todo_id"]}
    request = _request()
    request["turn_envelope"].update(goal_id=runner.goal_id, agent_id=binding["agent_id"])
    request["turn_envelope"]["action"]["selected_todo"]["todo_id"] = binding["todo_id"]
    run_codex_operation_host(request, runtime_root=runner.root, registry_path=runner.registry,
        project=Path(binding["workspace"]), codex_bin=str(executable), model="test-model",
        reasoning_effort="xhigh", mcp_server=mcp_server, timeout_seconds=5)
    assert native_starts.read_text() == "1"
    session = load_codex_cli_session(runner.root, lineage=lineage)
    handler = operation_tool_handler(runtime_root=runner.root, registry_path=runner.registry,
        lineage=lineage, session_id=session["session_id"], profile_digest=session["operation_profile_digest"],
        model="test-model", reasoning_effort="xhigh")
    intent = fixtures.request(goal_id=runner.goal_id,
        payload={"schema_version": "qualification_v0", "marker": "synthetic"})
    terms = intent["normalized_parameters"]
    terms.pop("executor")
    terms.update(agent_id=binding["agent_id"], domain="qualification",
        operation_kind="qualification.observe", operation_schema="qualification_v0")
    terms["projection"].update(simulated=False, title="Synthetic native wake qualification")
    prepared = handler("loopx_operation", {"action": "prepare", "request": intent},
        {"thread_id": session["session_id"], "host_turn_id": "preparing-turn"})
    assert prepared["ok"], prepared
    store = ChatActionStore(runner.root / "chat" / "actions")
    proposal = prepared["proposal"]
    delivered = store.record_operation_delivery(proposal["proposal_id"], delivery=fixtures.delivery(proposal))
    confirmed = store.decide_operation(proposal["proposal_id"], decision="confirm",
                                     confirmation=fixtures.confirmation(delivered))
    wake_config = {"registry_path": str(runner.registry), "goal_id": runner.goal_id,
        "requester_agent_id": runner.agent_id, "project": str(root),
        "execution_config": "delegations.json", "binding_id": binding["id"]}
    receipt = dispatch_confirmed_operation_wake(confirmed, runtime_root=runner.root, configuration=wake_config)
    assert receipt["state"] == "delegation_requested", receipt
    result = wait(runner, receipt["operation_id"])
    stored = store.load(proposal["proposal_id"])
    resumed = load_codex_cli_session(runner.root, lineage=lineage)
    assert resumed["session_id"] == session["session_id"]
    assert resumed["operation_profile_digest"] == session["operation_profile_digest"]
    assert stored["operation"].get("agent_handoff") is None
    assert stored["operation"]["outcome"] is None
    # Omitting the original null override must still fail closed, rather than
    # changing/replacing the original Session to bypass the profile fence.
    if mcp_profile == "unmatched-null":
        assert result["status"] == "rejected", result
        assert stored["operation"].get("host_start") is None, result
        assert native_starts.read_text() == "1"  # refused before native process start
        assert stored["status"] == confirmed["status"]
        assert stored["operation"]["confirmation"] == confirmed["operation"]["confirmation"]
        return
    assert native_starts.read_text() == "2"
    assert stored["operation"]["host_start"] is not None, result
    assert stored["operation"]["host_start"]["route"]["thread_id"] == session["session_id"]
    # Fixture deliberately waits: startup is not validated domain completion.
    assert result["status"] == "rejected"
    journal = json.loads(runner.path(receipt["operation_id"]).read_text())
    assert stored["operation"]["host_start"]["turn_key"] == journal["turn_key"]
    assert dispatch_confirmed_operation_wake(stored, runtime_root=runner.root,
        configuration=wake_config)["state"] == "existing_delegation"
    assert native_starts.read_text() == "2"


def test_detached_result_reconnects_without_duplicate_execution(service):
    root, original = service
    (root / "hold").touch()
    async def disconnect_requester():
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "loopx.collaboration_mcp", "--registry", str(original.registry),
            "--runtime-root", str(original.root), "--goal-id", original.goal_id,
            "--agent-id", original.agent_id, "--workspace", str(root / "lead"),
            "--execution-config", str(original.config)])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                inspection = await session.call_tool("inspect_execution_binding", {"binding_id": "analysis"})
                assert not inspection.isError
                preflight = json.loads(inspection.content[0].text)
                assert preflight["state"] == "runtime_unverified"
                assert not any(preflight["effects"].values())
                assert not (root / "host-started").exists()
                inventory = await session.call_tool("list_delegations", {})
                assert not inventory.isError and json.loads(inventory.content[0].text)["items"] == []
                assert "stop_delegation" in {tool.name for tool in (await session.list_tools()).tools}
                result = await session.call_tool("start_delegation", {
                    "binding_id": "analysis", "operation_id": "analysis-1", "brief": brief()})
                assert not result.isError
                inventory = await session.call_tool("list_delegations", {})
                assert not inventory.isError
                assert json.loads(inventory.content[0].text)["items"][0]["operation_id"] == "analysis-1"
                return json.loads(result.content[0].text)
        # Exiting the real stdio session closes the requesting MCP process.
    first = asyncio.run(disconnect_requester())
    deadline = time.monotonic() + 45
    while not (root / "host-started").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert (root / "host-started").exists(), _read(original.path("analysis-1"))
    # Replace the requesting context. The original worker remains independent;
    # retry/resume cannot start another model call while its lock is held.
    reconnected = Delegations(original.root, original.registry, original.goal_id, original.agent_id, original.config)
    assert reconnected.start("analysis", "analysis-1", brief())["request_id"] == first["request_id"]
    reconnected.resume("analysis-1")
    (root / "release").touch()
    result = wait(reconnected)
    assert result["status"] == "accepted", result
    validation = result["validation"]
    assert validation["source"] == "goal_acceptance"
    # The fixture rule pins the validator, oracle module and source material.
    assert validation["check_count"] == 1 and validation["pinned_file_count"] == 3
    assert len(validation["basis_sha256"]) == 64
    assert set(validation) == {"source", "check_count", "pinned_file_count", "basis_sha256", "checked_at", "output_versions"}
    assert validation["checked_at"].endswith("Z")
    assert validation["output_versions"] == [{"ref": row["ref"], "sha256": row["sha256"]} for row in result["artifacts"]]
    fresh_validation = reconnected.read("analysis-1")["validation"]
    assert fresh_validation["checked_at"] > validation["checked_at"]
    assert {key: value for key, value in fresh_validation.items() if key != "checked_at"} == {
        key: value for key, value in validation.items() if key != "checked_at"}
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    assert not (root / "analyst" / "initial" / "DELEGATION.json").exists()
    assert demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    returned = returns(original.root, original.goal_id, "lead")["items"]
    assert len(returned) == 1
    assert returned[0]["decision"] == "adopt"
    assert wait(reconnected)["artifacts"] == result["artifacts"]
    # Accepted work cannot be stopped: nothing is written and the receipt repeats exactly.
    noop = reconnected.stop("analysis-1", execute=True)
    assert noop["phase"] == "noop" and noop["status"] == "accepted" and noop["stop"] is None
    assert reconnected.stop("analysis-1", execute=True) == noop
    assert not reconnected._stop_path(reconnected.path("analysis-1")).exists()
    assert wait(reconnected)["artifacts"] == result["artifacts"]
    assert "stop" not in reconnected.read("analysis-1")
    changed_brief = {**brief(), "purpose": "Changed instruction"}
    with pytest.raises(ValueError, match="identity conflict"):
        reconnected.start("analysis", "analysis-1", changed_brief)
    ungranted = Delegations(original.root, original.registry, original.goal_id, "reviewer", original.config)
    original_brief = brief()
    with pytest.raises(Exception, match="no delegation grant"):
        ungranted.start("analysis", "other", original_brief)
    registry = json.loads(original.registry.read_text())
    registry["goals"][0]["status"] = "stopped"
    original.registry.write_text(json.dumps(registry))
    assert reconnected.read("analysis-1")["status"] == "accepted"
    with pytest.raises(ValueError, match="stopped"):
        reconnected.start("analysis", "new-operation", original_brief)
    registry["goals"][0]["status"] = "active"
    original.registry.write_text(json.dumps(registry))
    output = root / "analyst" / "initial" / "output.json"
    output.write_text("{}")
    with pytest.raises(ValueError, match="acceptance rejected"):
        reconnected.read("analysis-1")


def test_accepted_output_cannot_change_between_check_and_return(service, monkeypatch):
    """Real owner checks; inject a writer at the subsequent artifact-read boundary."""
    root, runner = service
    runner.start("analysis", "analysis-1", brief())
    assert wait(runner)["status"] == "accepted"
    binding = runner._bound(_read(runner.path("analysis-1")))
    output = root / "analyst" / "initial" / "output.json"
    original = output.read_bytes()
    validate = runner._validate

    def change_after_checks(value):
        checked = validate(value)  # Execute the real configured oracle first.
        output.write_text("{}")
        return checked

    monkeypatch.setattr(runner, "_validate", change_after_checks)
    try:
        # _accepted also feeds the first journaled return, before a saved hash exists.
        with pytest.raises(Exception, match="output changed during validation"):
            runner._accepted(binding)
    finally:
        output.write_bytes(original)
        monkeypatch.setattr(runner, "_validate", validate)
    assert runner.read("analysis-1")["artifacts"][0]["text"] == original.decode()
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"


def test_first_return_withholds_changed_output_and_recovers_same_operation(service, monkeypatch):
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _operation_id: None)
    output = root / "analyst" / "initial" / "output.json"
    original = output.read_bytes()
    validate = runner._validate

    def change_before_first_return(value):
        checked = validate(value)
        if checked["plan"]["canonical_done"]:
            output.write_text("{}")
        return checked

    monkeypatch.setattr(runner, "_validate", change_before_first_return)
    runner.start("analysis", "analysis-1", brief())
    try:
        runner.execute("analysis-1")
        journal = _read(runner.path("analysis-1"))
        assert journal["status"] != "accepted"
        assert not journal.get("artifacts")
        assert "output changed during validation" in journal["error"]
    finally:
        output.write_bytes(original)
        monkeypatch.setattr(runner, "_validate", validate)
    runner.resume("analysis-1")
    runner.execute("analysis-1")
    result = runner.read("analysis-1")
    assert result["status"] == "accepted", result
    assert result["artifacts"][0]["text"] == original.decode()
    assert result["validation"]["output_versions"][0]["sha256"] == result["artifacts"][0]["sha256"]
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"


def test_host_timeout_removes_private_delegation_bootstrap(service, monkeypatch):
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _operation_id: None)
    runner.start("analysis", "analysis-timeout", brief())

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("loopx turn", 1)

    monkeypatch.setattr(runner, "_cli", timeout)
    runner.execute("analysis-timeout")

    workspace = root / "analyst" / "initial"
    assert not (workspace / "DELEGATION.json").exists()
    assert runner.read("analysis-timeout")["error"] == "TimeoutExpired"


def test_the_starting_conversation_is_pinned_beside_the_operation(service, monkeypatch):
    """The wake can only return to the conversation whose Turn started the work.

    A second start under the same operation id (another conversation, or a
    requester recovering its context) replays the original request; it never
    rebinds the pin, which is part of the operation's own identity.
    """
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _operation_id: None)
    start = {"session_id": "chat-session-1", "turn_id": "turn-1"}
    runner.start("analysis", "analysis-1", brief(), conversation=start)
    assert _read(runner.path("analysis-1"))["conversation"] == start

    elsewhere = {"session_id": "chat-session-2", "turn_id": "turn-9"}
    runner.start("analysis", "analysis-1", brief(), conversation=elsewhere)
    assert _read(runner.path("analysis-1"))["conversation"] == start
    # Starting without a conversation neither adds nor clears one.
    runner.start("analysis", "analysis-1", brief())
    assert _read(runner.path("analysis-1"))["conversation"] == start
    # An operation started outside a conversation carries no wake target.
    monkeypatch.setattr(runner, "_spawn", lambda _operation_id: None)
    runner.start("analysis", "analysis-2", brief())
    assert "conversation" not in _read(runner.path("analysis-2"))


def test_model_success_without_receiver_adoption_cannot_complete(service):
    root, runner = service
    (root / "skip-adoption").touch()
    runner.start("analysis", "analysis-1", brief())
    result = wait(runner)
    assert result["status"] == "rejected"
    assert "did not adopt" in result["error"]
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]


def test_rejected_operation_publishes_reason_with_terminal_state(service, monkeypatch):
    """A reader may stop polling as soon as it sees a terminal observation."""
    root, runner = service
    (root / "skip-adoption").touch()
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-1", brief())
    observe = runner._observe
    terminal_reads = []

    def read_on_publish(path, row, status, **facts):
        observe(path, row, status, **facts)
        if status == "rejected":
            result = runner.read("analysis-1")
            terminal_reads.append(result)
            assert "did not adopt" in result.get("error", "")

    monkeypatch.setattr(runner, "_observe", read_on_publish)
    runner.execute("analysis-1")
    assert len(terminal_reads) == 1
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert returns(runner.root, runner.goal_id, "lead")["items"] == []


def until(predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def process_gone(pid):
    """Platform-valid: a zombie has exited; a process ``ps`` cannot see is gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                           capture_output=True, text=True).stdout.strip()
    return not state or state.startswith("Z")


def start_held_worker(service, operation="analysis-stop"):
    root, runner = service
    (root / "hold").touch()
    runner.start("analysis", operation, brief())
    assert until(lambda: (root / "host-started").exists()), _read(runner.path(operation))
    return int((root / "host-pid").read_text())


def test_stop_while_executing_is_acknowledged_by_the_worker_and_settles(service, monkeypatch):
    """The detached worker acknowledges SIGTERM under its own lock; time proves nothing."""
    root, runner = service
    host_pid = start_held_worker(service)
    path = runner.path("analysis-stop")
    before = _read(path)
    assert before["status"] == "running" and before["worker"]["pid"] == before["worker"]["pgid"]
    # The real run-once child holds the member's lane from inside the worker's group,
    # which is what lets settlement attribute the lane without ever taking it.
    binding = runner.binding("analysis")
    lane = turn_lane_liveness(runner._lane_target(binding))
    assert lane["state"] == "live" and lane["holder"]["pid"] != before["worker"]["pid"]
    assert os.getpgid(lane["holder"]["pid"]) == before["worker"]["pgid"]
    receipt = runner.stop("analysis-stop", execute=True)
    host_gone = process_gone(host_pid)  # the instant settled returns, not after a wait
    assert receipt["phase"] == "settled" and receipt["status"] == "stopped", receipt
    assert host_gone
    stop = receipt["stop"]
    assert stop["requested_by"] == "lead" and stop["requested_status"] == "running"
    assert stop["worker"]["pid"] == before["worker"]["pid"]
    assert stop["ack"]["pid"] == before["worker"]["pid"] and stop["ack"]["source"] == "SIGTERM"
    assert stop["ack"]["observed_status"] == "running" and stop["ack"]["turn_key"]
    assert stop["settled"]["operation_lock_free"] and stop["settled"]["worker_lane_released"]
    assert stop["settled"]["lane_state"] in {"dead", "released"}
    assert stop["settled"]["host_process"] == "drained"
    assert stop["settled"]["turn_journal_status"] == "in_progress"
    # Canonical authority, not the record's annotation, proved no lease was owed.
    assert stop["lease"]["state"] == stop["settled"]["lease"] == "not_owed"
    # The acknowledged record is final: nobody writes it again, the Todo stays open,
    # the worker exits and the member's Turn lane can be taken.
    frozen = path.read_bytes()
    assert until(lambda: process_gone(before["worker"]["pid"]), timeout=20)
    with try_exclusive_file_lock(runner._lane_target(binding)) as held:
        assert held is not None
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert not (root / "analyst" / "initial" / "DELEGATION.json").exists()
    assert path.read_bytes() == frozen
    assert runner.stop("analysis-stop", execute=True) == receipt
    observed = runner.read("analysis-stop")
    assert observed["status"] == "stopped" and not observed["recovery_required"]
    assert observed["stop"] == {"stop_id": stop["stop_id"], "phase": "settled"}
    assert runner.wait("analysis-stop")["status"] == "stopped"
    monkeypatch.setattr(runner, "_spawn", lambda _: pytest.fail("stopped work must not respawn"))
    with pytest.raises(ValueError, match="start a new operation id"):
        runner.resume("analysis-stop")
    assert path.read_bytes() == frozen
    page = runner.operations()
    assert page["page_readback_complete"] and page["items"][0]["status"] == "stopped"
    assert returns(runner.root, runner.goal_id, "lead")["items"] == []


def test_stop_without_a_holder_is_acknowledged_by_the_requester(service, monkeypatch):
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-idle", brief())
    with monkeypatch.context() as platform:
        platform.delattr(os, "killpg", raising=False)
        receipt = runner.stop("analysis-idle", execute=True)
    assert receipt["phase"] == "settled" and receipt["status"] == "stopped"
    assert receipt["stop"]["worker"] is None and receipt["stop"]["requested_status"] == "prepared"
    assert receipt["stop"]["ack"]["pid"] == os.getpid() and receipt["stop"]["ack"]["source"] == "requester"
    assert receipt["stop"]["settled"]["turn_journal_status"] is None
    assert receipt["stop"]["settled"]["lane_state"] in {"absent", "released"}
    assert receipt["stop"]["settled"]["host_process"] == "not_launched"
    frozen = runner.path("analysis-idle").read_bytes()
    with pytest.raises(ValueError, match="start a new operation id"):
        runner.resume("analysis-idle")
    runner.execute("analysis-idle")  # a late worker finds terminal work and launches nothing
    assert runner.path("analysis-idle").read_bytes() == frozen
    assert not (root / "host-started").exists()
    assert runner.stop("analysis-idle", execute=True) == receipt
    with pytest.raises(ValueError, match="requires execute"):
        runner.stop("analysis-idle", execute=False)
    with pytest.raises(ValueError, match="unknown delegation operation"):
        runner.stop("never-started", execute=True)


def test_worker_killed_before_acknowledging_is_unknown_not_settled(service, monkeypatch):
    """A vanished named holder never becomes a settlement; the stop still fences resume."""
    import signal

    root, runner = service
    host_pid = start_held_worker(service)
    path = runner.path("analysis-stop")
    worker = _read(path)["worker"]
    killed = []

    def kill_without_grace(target, stop):
        if killed:
            return  # later calls find nothing left to signal
        killed.append(stop["worker"])
        assert stop["worker"] == worker and worker["pgid"] != os.getpgid(0)
        os.killpg(worker["pgid"], signal.SIGKILL)
        assert until(lambda: runner._operation_lock_free(target), timeout=20)

    monkeypatch.setattr(runner, "_signal_worker", kill_without_grace)
    first = runner.stop("analysis-stop", execute=True)
    assert first["stop"]["ack"] is None and first["phase"] in {"requested", "unknown"}, first
    # The operation lock is free now, yet the requester never acknowledges for a
    # named worker: the outcome converges on unknown once its Turn child is reaped.
    assert until(lambda: runner.stop("analysis-stop", execute=True)["phase"] == "unknown", timeout=20)
    receipt = runner.stop("analysis-stop", execute=True)
    assert receipt["status"] == "running" and receipt["stop"]["ack"] is None, receipt
    assert receipt["stop"]["settled"]["operation_lock_free"] and receipt["stop"]["settled"]["worker_lane_released"]
    assert receipt["stop"]["settled"]["turn_journal_status"] == "in_progress"
    # The execution is proven gone, so its lease is resolved before the terminal
    # receipt; canonical authority proves this operation held none.
    assert receipt["stop"]["lease"]["state"] == receipt["stop"]["settled"]["lease"] == "not_owed"
    assert len(killed) == 1
    assert until(lambda: process_gone(host_pid), timeout=20)
    assert runner.stop("analysis-stop", execute=True) == receipt
    with pytest.raises(ValueError, match="start a new operation id"):
        runner.resume("analysis-stop")
    observed = runner.read("analysis-stop")
    assert observed["stop"]["phase"] == "unknown" and not observed["recovery_required"]
    assert runner.wait("analysis-stop")["stop"]["phase"] == "unknown"
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]


def test_sigterm_without_a_stop_keeps_the_operation_recoverable(service, monkeypatch):
    """A shutdown signal is not a stop: the worker dies as before and resume stays available."""
    import signal

    root, runner = service
    start_held_worker(service, "analysis-term")
    path = runner.path("analysis-term")
    worker = _read(path)["worker"]
    target = runner._lane_target(runner.binding("analysis"))
    turn_child = turn_lane_liveness(target)["holder"]["pid"]
    try:
        os.kill(worker["pid"], signal.SIGTERM)
        assert until(lambda: runner._operation_lock_free(path), timeout=20)
        # Default termination, exactly as before: only the worker died, and its
        # orphaned Turn child still holds the member's lane.
        lane = turn_lane_liveness(target)
        assert lane["state"] == "live" and lane["holder"]["pid"] == turn_child
        assert not runner._stop_path(path).exists()
        assert _read(path)["status"] == "running" and "stop" not in runner.read("analysis-term")
        spawned = []
        monkeypatch.setattr(runner, "_spawn", spawned.append)
        runner.resume("analysis-term")
        assert spawned == ["analysis-term"]
    finally:
        try:
            os.killpg(worker["pgid"], signal.SIGKILL)  # the orphaned Turn child
        except ProcessLookupError:
            pass


def test_stop_settlement_never_refuses_a_concurrent_turn_on_the_member_lane(service, monkeypatch):
    """Settling reads the lane holder record; a real Turn racing it is always admitted."""
    import threading
    from loopx import file_lock

    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-race", brief())
    path = runner.path("analysis-race")
    binding = runner.binding("analysis")
    target = runner._lane_target(binding)
    lane_lock = target.with_name(target.name + ".lock")
    opened = []
    real_open = file_lock._open_lock_descriptor

    def recording_open(lock_path, **kwargs):
        opened.append((threading.get_ident(), Path(lock_path)))
        return real_open(lock_path, **kwargs)

    monkeypatch.setattr(file_lock, "_open_lock_descriptor", recording_open)
    lane = {"runtime_root": runner.root, "goal_id": runner.goal_id,
            "plan": {"turn_envelope": {"agent_id": binding["agent_id"]}}}
    admitted, refused, phases, settlers = [], [], [], []
    done = Event()

    def settle_continuously():
        settlers.append(threading.get_ident())
        while not done.is_set():
            phases.append(runner.stop("analysis-race", execute=True)["phase"])

    # An unnamed holder keeps the operation open, so every stop call settles again
    # and reads the member's lane while other Turns of that member take it.
    with exclusive_file_lock(path), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(settle_continuously)
        try:
            assert until(lambda: len(phases) >= 2, timeout=30)
            for _ in range(200):
                with turn_lane_singleflight(**lane) as held:
                    (admitted if held is not None else refused).append(held)
            assert until(lambda: len(phases) >= 4, timeout=30)
        finally:
            done.set()
        future.result(timeout=30)
    assert refused == [] and len(admitted) == 200
    assert set(phases) == {"requested"}
    assert [lock for ident, lock in opened if ident in settlers and lock == lane_lock] == []
    # Once the holder is gone the requester acknowledges; settling still never takes the lane.
    opened.clear()
    receipt = runner.stop("analysis-race", execute=True)
    assert receipt["phase"] == "settled" and receipt["stop"]["settled"]["lane_state"] == "released"
    assert lane_lock not in {lock for _, lock in opened}


def test_fenced_write_after_another_process_stop_writes_nothing(service, monkeypatch):
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-fenced", brief())
    path = runner.path("analysis-fenced")
    row = _read(path)
    foreign = runner._new_stop_record(row, requested_by="other-host-lead", worker=None)
    foreign.update(phase="acknowledged", ack={"pid": 1, "host": "elsewhere", "at": 0.0,
                                              "source": "requester", "observed_status": "running",
                                              "turn_key": None})
    from loopx.control_plane.collaboration.inbox import _write

    _write(runner._stop_path(path), foreign)
    frozen = path.read_bytes()
    with pytest.raises(DelegationFenced):
        runner._fenced_write(path, {**row, "status": "running"})
    with pytest.raises(DelegationFenced):
        runner._observe(path, dict(row), "running")
    with pytest.raises(DelegationFenced):
        runner._record_turn_result(path, {**row, "status": "running"},
                                   {"status": "committed", "result_kind": "validated_progress"})
    runner.execute("analysis-fenced")  # the foreign acknowledgement stands; nothing is rewritten
    assert path.read_bytes() == frozen
    assert _read(runner._stop_path(path)) == foreign
    assert not (root / "host-started").exists()
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    # A stop this process may acknowledge is taken from under the lock at entry.
    runner.start("analysis", "analysis-entry", brief())
    entry = runner.path("analysis-entry")
    _write(runner._stop_path(entry), runner._new_stop_record(_read(entry), requested_by="lead", worker=None))
    runner.execute("analysis-entry")
    assert _read(entry)["status"] == "stopped"
    acknowledged = _read(runner._stop_path(entry))
    assert acknowledged["phase"] == "acknowledged" and acknowledged["ack"]["source"] == "worker_entry"
    assert runner.stop("analysis-entry", execute=True)["phase"] == "settled"


def test_stop_settles_only_after_the_owned_host_and_its_descendants_exit(service):
    """A host and its same-group child that ignore SIGTERM keep the stop open until they exit.

    The postcondition is checked at the instant ``settled`` returns, not after a wait.
    """
    root, runner = service
    (root / "ignore-term").touch()
    host_pid = start_held_worker(service)
    assert until(lambda: (root / "host-child-pid").exists())
    child_pid = int((root / "host-child-pid").read_text())
    assert not process_gone(host_pid) and not process_gone(child_pid)
    receipt = runner.stop("analysis-stop", execute=True)
    host_gone, child_gone = process_gone(host_pid), process_gone(child_pid)
    assert receipt["phase"] == "settled", receipt
    assert host_gone and child_gone, (receipt, host_gone, child_gone)
    settled = receipt["stop"]["settled"]
    assert settled["host_process"] == "drained", settled
    # Rereading a settled stop neither reopens it nor admits or completes anything.
    frozen = runner.path("analysis-stop").read_bytes()
    assert runner.stop("analysis-stop", execute=True) == receipt
    assert runner.read("analysis-stop")["stop"]["phase"] == "settled"
    assert runner.path("analysis-stop").read_bytes() == frozen
    assert int((root / "analyst" / "initial" / "host-invocations").read_text()) == 1
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert returns(runner.root, runner.goal_id, "lead")["items"] == []


def test_interrupted_host_cleanup_keeps_the_stop_open_until_a_reread_sees_it_drained(service, monkeypatch):
    """A Host supervisor that never finishes cleaning up leaves no settlement to claim.

    Rereads with the same identity stay acknowledged without admitting or completing
    anything, and settle once the Host's group is observed gone.
    """
    import signal
    from loopx import collaboration_mcp

    root, runner = service
    monkeypatch.setattr(collaboration_mcp, "DELEGATION_STOP_GRACE_SECONDS", 2.0)
    host_pid = start_held_worker(service)
    path = runner.path("analysis-stop")
    record = json.loads(runner._host_process_record(path).read_text())
    assert record["phase"] == "spawned" and record["host_pid"] == host_pid == record["process_group"]
    bridge = record["bridge_pid"]
    os.kill(bridge, signal.SIGSTOP)  # the supervisor cannot run its cleanup
    try:
        first = runner.stop("analysis-stop", execute=True)
        assert first["phase"] == "acknowledged" and first["status"] == "stopped", first
        assert first["reason"] == "host_process_still_running" and not process_gone(host_pid)
        os.kill(bridge, signal.SIGKILL)  # and now never will: the Host is orphaned
        assert until(lambda: process_gone(bridge), timeout=20)
        frozen = path.read_bytes()
        for _ in range(3):
            again = runner.stop("analysis-stop", execute=True)
            assert again["phase"] == "acknowledged" and again["reason"] == "host_process_still_running", again
            assert again["stop"]["stop_id"] == first["stop"]["stop_id"]
            assert again["stop"]["ack"] == first["stop"]["ack"] and again["stop"]["settled"] is None
        assert runner.read("analysis-stop")["stop"]["phase"] == "acknowledged"
        assert not process_gone(host_pid) and path.read_bytes() == frozen
        with pytest.raises(ValueError, match="start a new operation id"):
            runner.resume("analysis-stop")
    finally:
        for target, sig in ((bridge, signal.SIGCONT), (host_pid, signal.SIGKILL)):
            try:
                os.killpg(target, sig) if target == host_pid else os.kill(target, sig)
            except ProcessLookupError:
                pass
    assert until(lambda: process_gone(host_pid), timeout=20)
    receipt = runner.stop("analysis-stop", execute=True)
    assert receipt["phase"] == "settled" and receipt["stop"]["settled"]["host_process"] == "drained", receipt
    assert receipt["stop"]["stop_id"] == first["stop"]["stop_id"]
    assert runner.stop("analysis-stop", execute=True) == receipt
    assert path.read_bytes() == frozen
    assert int((root / "analyst" / "initial" / "host-invocations").read_text()) == 1
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert returns(runner.root, runner.goal_id, "lead")["items"] == []


def test_a_stop_written_before_the_effects_linearizes_against_them(service, monkeypatch):
    """Todo completion and reply publication commit under the stop's own lock.

    The pre-check alone was not a boundary: the worker could pass it, a stop
    could be persisted, and both external effects still committed before the
    fenced record write, leaving `settled`/`stopped` for a member whose work had
    landed. Holding the dispatch lock across both effects makes the two sides
    linearize in either order.
    """
    from loopx.control_plane.collaboration.inbox import _write as write_inbox

    root, runner = service
    runner.start("analysis", "analysis-race", brief())
    path = runner.path("analysis-race")
    row = _read(path)
    # Reproduce the interleaving: the stop exists before the worker reaches its
    # effects. A same-process request is what the worker then acknowledges.
    write_inbox(runner._stop_path(path), runner._new_stop_record(
        row, requested_by=runner.agent_id, worker=None))

    # The checkpoint after the model returns refuses to run the effects.
    with pytest.raises(DelegationStopRequested):
        with exclusive_file_lock(runner._dispatch_lock(path)):
            runner._raise_if_stop_requested(path)

    runner.execute("analysis-race")
    assert _read(path)["status"] == "stopped"
    # Neither effect committed for a stop that was written first.
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert not (root / "runtime" / "replies" / "analysis-race" / "conclusion.json").exists()
    assert not (root / "host-started").exists()
    receipt = runner.stop("analysis-race", execute=True)
    assert receipt["phase"] == "settled" and receipt["status"] == "stopped"


def held_lease(monkeypatch, row, key):
    """The annotation `_acquire_delegation_lease` writes, and a canonical lease that still holds it."""
    row["turn_instance_id"] = key
    lease = {"owner": "analyst", "idempotency_key": key, "status": "active", "version": 1, "lease_epoch": 1}
    monkeypatch.setattr(stop_lease, "inspect_task_lease",
                        lambda **kwargs: {"ok": True, "action": "inspect", "active": True, "lease": lease})
    return {"required": True, "handoff_mode": "hard_lease", "lease": dict(lease)}


def test_a_failed_required_lease_release_keeps_the_stop_open_and_retries(service, monkeypatch):
    """A required lease the stop could not release is not a settlement.

    The member's Todo can stay blocked by that lease until its TTL, so reporting
    `settled` would be a terminal claim the owner cannot act on. The release is
    retried under the stop's own lock on the next read instead of being attempted
    once and forgotten.
    """
    from loopx.control_plane.collaboration.inbox import _write as write_inbox

    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-lease", brief())
    path = runner.path("analysis-lease")
    row = _read(path)
    # A bounded member task holds a required lease; make the record say so.
    row["task_lease"] = held_lease(monkeypatch, row, "lease-1")
    row["status"] = "stopped"
    runner._fenced_write(path, row)
    write_inbox(runner._stop_path(path), {
        **runner._new_stop_record(row, requested_by=runner.agent_id, worker=None),
        "phase": "acknowledged",
        "ack": {"pid": os.getpid(), "host": lock_holder_host_label(), "at": time.time(),
                "source": "requester", "observed_status": "stopped", "turn_key": None},
        # An earlier settlement could not release it, which is what the receipt records.
        "lease": {"state": "release_unproven", "error": "authority unavailable"},
    })

    attempts = []
    outcomes = [RuntimeError("authority temporarily unavailable"), {"released": True}]

    def flaky_release(**kwargs):
        attempts.append(kwargs)
        outcome = outcomes[min(len(attempts) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(stop_lease, "release_task_lease", flaky_release)

    # The first settle read tries the release, fails, and must not settle.
    receipt = runner.stop("analysis-lease", execute=True)
    assert attempts, "the stop never attempted the required release"
    assert receipt["phase"] == "acknowledged", receipt
    assert receipt["stop"]["reason"] == "required_lease_release_unproven"
    assert receipt["stop"]["lease"]["state"] == "release_unproven"

    # The next read retries the release and only then settles.
    settled = runner.stop("analysis-lease", execute=True)
    assert len(attempts) >= 2, "the failed release was never retried"
    assert settled["phase"] == "settled", settled
    assert settled["stop"]["lease"]["state"] == settled["stop"]["settled"]["lease"] == "released"

    # Once settled the receipt is stable, and a released lease is not re-attempted.
    before = len(attempts)
    assert runner.stop("analysis-lease", execute=True) == settled
    assert len(attempts) == before


def test_a_launched_host_on_a_platform_without_process_groups_fails_fast(service, monkeypatch):
    """A stop that cannot prove its Host drained says so instead of never settling.

    Windows cleanup is process-tree best effort, so no fact proves the Host's
    descendants exited. An acknowledged receipt the caller can never settle is
    worse than an actionable refusal that names the platform boundary.
    """
    from loopx.control_plane.turn_driver import host_process_transport

    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-platform", brief())
    path = runner.path("analysis-platform")
    record = runner._host_process_record(path)
    record.parent.mkdir(parents=True, exist_ok=True)
    # The caller may itself lead a live group; use a genuinely exited Host.
    with subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True) as finished_host:
        finished_host.wait(timeout=10)
    record.write_text(json.dumps({
        "schema_version": host_process_transport.HOST_PROCESS_RECORD_SCHEMA_VERSION,
        "host": lock_holder_host_label(), "supervises": "host", "supervision": "direct", "phase": "finished",
        "bridge_pid": finished_host.pid, "process_group": finished_host.pid,
    }))

    # A persisted finished Host record still needs platform drain capability.
    assert host_process_transport.execution_host_drain(record) == host_process_transport.HOST_PROCESS_DRAINED
    monkeypatch.delattr(host_process_transport.os, "killpg", raising=False)
    assert host_process_transport.execution_host_drain(record) == host_process_transport.HOST_PROCESS_UNSUPPORTED_PLATFORM

    operation_before = path.read_bytes()
    stop_path = runner._stop_path(path)
    assert not stop_path.exists()

    def unexpected_effect(*args, **kwargs):
        pytest.fail("unsupported stop must not acknowledge, signal, or release its lease")

    monkeypatch.setattr(runner, "_acknowledge_stop", unexpected_effect)
    monkeypatch.setattr(runner, "_signal_worker", unexpected_effect)
    from loopx.control_plane.collaboration import delegation_stop_lease
    monkeypatch.setattr(delegation_stop_lease, "settle", unexpected_effect)
    for _ in range(2):
        with pytest.raises(ValueError, match="cannot prove the launched Host drained"):
            runner.stop("analysis-platform", execute=True)
        assert path.read_bytes() == operation_before
        assert not stop_path.exists()

    # An existing request remains recoverable evidence, never a fabricated ACK.
    stop = runner._new_stop_record(
        json.loads(operation_before), requested_by=runner.agent_id, worker=None,
    )
    stop_path.write_text(json.dumps(stop))
    stop_before = stop_path.read_bytes()
    for _ in range(2):
        with pytest.raises(ValueError, match="cannot prove the launched Host drained"):
            runner.stop("analysis-platform", execute=True)
        assert path.read_bytes() == operation_before
        assert stop_path.read_bytes() == stop_before



def test_unsupported_stop_during_host_launch_preserves_continuation(service, monkeypatch):
    """No record yet does not prove an active worker cannot launch a Host."""
    from loopx.control_plane.turn_driver import host_process_transport

    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "platform-launch", brief())
    path = runner.path("platform-launch")
    entering, continue_launch = Event(), Event()
    cli = runner._cli

    def pause_launch(binding, *args, **kwargs):
        if args[:2] == ("turn", "run-once") and kwargs.get("host_record"):
            entering.set()
            assert continue_launch.wait(20)
        return cli(binding, *args, **kwargs)

    monkeypatch.setattr(runner, "_cli", pause_launch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(runner.execute, "platform-launch")
        try:
            assert entering.wait(20)
            assert json.loads(runner._host_process_record(path).read_text())["phase"] == "not_launched"
            before = path.read_bytes()
            with monkeypatch.context() as platform:
                platform.delattr(host_process_transport.os, "killpg", raising=False)
                with pytest.raises(ValueError, match="cannot prove the launched Host drained"):
                    runner.stop("platform-launch", execute=True)
            assert path.read_bytes() == before
            assert not runner._stop_path(path).exists()
        finally:
            continue_launch.set()
            worker.result(timeout=60)
    assert runner.read("platform-launch")["status"] == "accepted"
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"

def test_a_crash_between_the_ack_and_the_lease_result_keeps_the_stop_open(service, monkeypatch):
    """The lease obligation survives process loss after the acknowledgement.

    The ACK is written long before the lease is resolved, so a crash in between
    leaves the sidecar with no `lease` field. Reading that as "nothing was owed"
    settles a stop whose member still holds an active hard lease, with resume
    already refused and the Todo blocked until the TTL. The obligation comes
    from canonical authority for the operation's own identity, which the crash
    cannot lose.
    """
    from loopx.control_plane.collaboration.inbox import _write as write_inbox

    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-crash", brief())
    path = runner.path("analysis-crash")
    row = _read(path)
    # The member holds a real required lease, as a bounded task does.
    row["task_lease"] = held_lease(monkeypatch, row, "lease-crash")
    right_after_ack = {**row, "status": "stopped"}
    runner._fenced_write(path, right_after_ack)

    # The crash window: ACK persisted, no lease result written yet.
    write_inbox(runner._stop_path(path), {
        **runner._new_stop_record(right_after_ack, requested_by=runner.agent_id, worker=None),
        "phase": "acknowledged",
        "ack": {"pid": os.getpid(), "host": lock_holder_host_label(), "at": time.time(),
                "source": "requester", "observed_status": "stopped", "turn_key": None},
    })
    sidecar = _read(runner._stop_path(path))
    assert "lease" not in sidecar or sidecar.get("lease") is None
    assert _read(path)["task_lease"]["required"] is True

    # A release that succeeds on this read lets the stop settle, and the receipt
    # says the lease really is gone.
    monkeypatch.setattr(stop_lease, "release_task_lease",
                        lambda **kw: {"released": True})
    settled = runner.stop("analysis-crash", execute=True)
    assert settled["phase"] == "settled", settled
    assert settled["stop"]["lease"]["state"] == settled["stop"]["settled"]["lease"] == "released"


def test_a_crash_between_the_ack_and_the_lease_result_never_settles_unreleased(service, monkeypatch):
    """The same window, with the release still failing, must not report `settled`.

    `settled` tells the owner the member is safely stopped. Claiming it while a
    required lease is provably still active is exactly the terminal distortion
    the crash window used to produce.
    """
    from loopx.control_plane.collaboration.inbox import _write as write_inbox

    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-crash-open", brief())
    path = runner.path("analysis-crash-open")
    row = _read(path)
    row["task_lease"] = held_lease(monkeypatch, row, "lease-open")
    right_after_ack = {**row, "status": "stopped"}
    runner._fenced_write(path, right_after_ack)
    write_inbox(runner._stop_path(path), {
        **runner._new_stop_record(right_after_ack, requested_by=runner.agent_id, worker=None),
        "phase": "acknowledged",
        "ack": {"pid": os.getpid(), "host": lock_holder_host_label(), "at": time.time(),
                "source": "requester", "observed_status": "stopped", "turn_key": None},
    })

    def still_held(**kwargs):
        raise RuntimeError("authority unavailable")

    monkeypatch.setattr(stop_lease, "release_task_lease", still_held)
    receipt = runner.stop("analysis-crash-open", execute=True)
    assert receipt["phase"] == "acknowledged", receipt
    assert receipt["stop"]["reason"] == "required_lease_release_unproven"


def test_an_ordinary_delegation_gains_no_wake_state(service):
    """A delegation started outside a conversation is never a wake candidate.

    The wake capability is opt-in and belongs to a Chat conversation. An
    ordinary CLI/MCP delegation must keep the acceptance shape it always had: no
    intent, no persisted wake, and no change to what a plain read returns.
    """
    root, runner = service
    runner.start("analysis", "analysis-1", brief())
    acceptance = wait(runner)
    assert acceptance["status"] == "accepted"
    recorded = _read(runner.path("analysis-1"))
    assert "wake" not in recorded, "an ordinary delegation must not carry wake state"
    # Nor does it gain a wake target it could be routed to later.
    assert "conversation" not in recorded


@pytest.mark.parametrize("damage", ["unreadable", "owner"])
def test_lost_turn_reply_with_damaged_history_never_restarts_host(service, monkeypatch, damage):
    """Real Turn commit, lost reply, damaged readback, repair, same-operation return."""
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _operation_id: None)
    runner.start("analysis", "analysis-recovery", brief())
    record = runner._record_turn_result

    def lose_reply(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("turn reply after commit", 1)

    monkeypatch.setattr(runner, "_record_turn_result", lose_reply)
    runner.execute("analysis-recovery")
    count = root / "analyst" / "initial" / "host-invocations"
    assert count.read_text() == "1"
    assert _read(runner.path("analysis-recovery"))["status"] == "running"
    journals = list((runner.root / "goals" / runner.goal_id / "turns").glob("*.json"))
    assert len(journals) == 1
    path = journals[0]
    original = path.read_bytes()
    if damage == "unreadable":
        path.write_text("{damaged", encoding="utf-8")
    else:
        damaged = json.loads(original)
        damaged["plan"]["turn_envelope"]["agent_id"] = "foreign-agent"
        path.write_text(json.dumps(damaged), encoding="utf-8")
    monkeypatch.setattr(runner, "_record_turn_result", record)
    runner.execute("analysis-recovery")
    held = _read(runner.path("analysis-recovery"))
    assert held["status"] == "running"
    assert "journal" in held["error"].lower()
    # Recovery visibility retains the existing 15-second startup grace.
    with monkeypatch.context() as observation:
        observation.setattr("loopx.collaboration_mcp.time.time", lambda: held["created_at"] + 16)
        visible = runner.read("analysis-recovery")
    assert visible["recovery_required"] is True
    assert visible["error"] == held["error"]
    assert count.read_text() == "1"
    # Restore only this disposable fixture's bytes, simulating verified repair.
    path.write_bytes(original)
    runner.execute("analysis-recovery")
    assert runner.read("analysis-recovery")["status"] == "accepted"
    assert count.read_text() == "1"
