"""Production delegation/Turn/TS completion with an explicit fixture model host."""
import json
import asyncio
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from threading import Event, get_ident

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "managed-research-team"))
import research_team as demo  # noqa: E402
from test_managed_research_scenario import fixture  # noqa: E402
from loopx.collaboration_mcp import Delegations  # noqa: E402
from loopx.control_plane.collaboration.peers import returns  # noqa: E402
from loopx.control_plane.collaboration.inbox import _read  # noqa: E402
from loopx.file_lock import exclusive_file_lock  # noqa: E402


HOST = '''import json, sys, time
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
if (root / 'hold').exists():
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
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
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


@pytest.mark.parametrize("operation", ["--help", "x y", "x\ny", "x;echo", "x/../y"])
def test_worker_rejects_unbounded_operation_arguments(tmp_path, monkeypatch, operation):
    from loopx import collaboration_mcp as delegation

    runner = Delegations(tmp_path, tmp_path / "registry.json", "goal", "lead", tmp_path / "config.json")
    calls = []
    monkeypatch.setattr(delegation.subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="stable peer operation id"):
        runner._spawn(operation)
    assert calls == []


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
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    assert demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    returned = returns(original.root, original.goal_id, "lead")["items"]
    assert len(returned) == 1
    assert returned[0]["decision"] == "adopt"
    assert wait(reconnected)["artifacts"] == result["artifacts"]
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
