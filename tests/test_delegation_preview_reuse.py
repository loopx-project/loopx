"""Real original CLI comparisons; reusable processes must not reuse decisions."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from threading import Timer
from urllib.parse import quote

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from loopx.collaboration_mcp import Delegations, _pinned_release_environment, _python_module_command
from test_local_delegation import demo, service as delegation_service

def reusable_service(runner):
    return Delegations(runner.root, runner.registry, runner.goal_id, runner.agent_id,
                       runner.config, reuse_preview=True)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX source symlink observation")
def test_loaded_source_snapshot_keeps_complete_ordered_metadata(tmp_path):
    from loopx.control_plane.collaboration.delegation_preview_transport import _source_snapshot

    package = tmp_path / "loopx"
    nested = package / "nested"
    nested.mkdir(parents=True)
    (package / "a.py").write_text("one")
    (package / "skip.lock").write_text("ignored")
    (nested / "b.json").write_text("{}")
    (nested / "c.ts").write_text("export {}")
    (package / "linked.py").symlink_to(nested / "c.ts")
    (package / "linked-dir").symlink_to(nested, target_is_directory=True)
    expected = ["loopx/a.py", "loopx/linked.py", "loopx/nested/b.json", "loopx/nested/c.ts"]
    snapshot = _source_snapshot(tmp_path)
    assert [row[0] for row in snapshot] == expected
    for name, modified, changed, size in snapshot:
        metadata = (tmp_path / name).stat()
        assert (modified, changed, size) == (metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size)
    (nested / "b.json").write_text('{"changed":true}')
    assert _source_snapshot(tmp_path) != snapshot


@pytest.fixture
def service(delegation_service):
    root, runner = delegation_service
    reused = reusable_service(runner)
    yield root, reused
    reused._preview_transport.close()


def test_mcp_registration_explicitly_selects_reusable_service(service, monkeypatch):
    from loopx import collaboration_mcp as collaboration

    root, runner = service
    captured = []
    register = collaboration.register_delegation_tools

    def capture(server, delegations):
        captured.append(delegations)
        register(server, delegations)

    monkeypatch.setattr(collaboration, "register_delegation_tools", capture)
    collaboration.create_server(runner.root, runner.registry, runner.goal_id,
                                runner.agent_id, root / "lead", runner.config)
    assert len(captured) == 1 and captured[0]._preview_transport is not None
    try:
        assert captured[0].inspect("analysis") == runner.inspect("analysis")
        assert captured[0]._preview_transport._process is not None
    finally:
        captured[0]._preview_transport.close()


def test_real_mcp_session_rereads_changed_validator_without_effects(service):
    root, runner = service
    before = runner.registry.read_bytes(), runner.config.read_bytes()
    canonical = demo.canonical_tasks(root)
    validator = Path(json.loads(before[0])["goals"][0]["repo"]) / "validation" / "acceptance.py"
    original = validator.read_bytes()

    async def inspect_session():
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "loopx.collaboration_mcp", "--registry", str(runner.registry),
            "--runtime-root", str(runner.root), "--goal-id", runner.goal_id,
            "--agent-id", runner.agent_id, "--workspace", str(root / "lead"),
            "--execution-config", str(runner.config),
        ])
        results = []
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                for drift in (False, True, False):
                    validator.write_bytes(original + (b"\n# updated current validator\n" if drift else b""))
                    inspected = await session.call_tool("inspect_execution_binding", {"binding_id": "analysis"})
                    assert not inspected.isError
                    result = json.loads(inspected.content[0].text)
                    assert result["state"] == ("acceptance_unavailable" if drift else "runtime_unverified")
                    assert not any(result["effects"].values())
                    results.append(result)
        assert results[0] == results[2]

    try:
        asyncio.run(inspect_session())
    finally:
        validator.write_bytes(original)
    assert (runner.registry.read_bytes(), runner.config.read_bytes()) == before
    assert demo.canonical_tasks(root) == canonical
    assert not (root / "host-started").exists()
    assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))


@pytest.mark.parametrize("arguments", [
    ("turn", "run-once", "--execute"),
    ("turn", "run-once", "--resume-turn-key", "existing"),
    ("task-lease", "acquire"),
])
def test_execution_resume_and_mutations_keep_original_cli_transport(service, monkeypatch, arguments):
    _, runner = service
    binding = runner.binding("analysis", require_active=True)
    calls = []

    def cli_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"original_transport":true}', stderr="")

    def wrong_transport(**kwargs):
        pytest.fail("mutating request entered the preview worker")

    monkeypatch.setattr("loopx.collaboration_mcp.subprocess.run", cli_run)
    monkeypatch.setattr(runner._preview_transport, "preview", wrong_transport)
    assert runner._cli(binding, *arguments) == {"original_transport": True}
    assert len(calls) == 1 and calls[0][0][-len(arguments):] == list(arguments)
    assert calls[0][1]["cwd"] == binding["workspace"]
    assert runner._preview_transport._process is None


def fresh_cli(runner, binding, *argv, timeout=60):
    completed = subprocess.run(
        [*_python_module_command("loopx.cli"), "--registry", str(runner.registry),
         "--runtime-root", str(runner.root), "--format", "json", *argv],
        cwd=binding["workspace"], env=_pinned_release_environment(), capture_output=True,
        text=True, encoding="utf-8", timeout=timeout,
    )
    assert completed.returncode in (0, 1)
    return json.loads(completed.stdout)


@pytest.mark.parametrize("arguments", [
    ("turn", "run-once"),
    ("turn", "run-once", "--execute"),
    ("turn", "run-once", "--resume-turn-key", "existing"),
])
def test_leased_commands_keep_host_supervision_not_preview(service, monkeypatch, arguments):
    _, runner = service
    binding = runner.binding("analysis", require_active=True)
    lease_context = {"lease": {"idempotency_key": "original-execution"}}
    calls = []

    def supervised(argv, **kwargs):
        calls.append((argv, kwargs))
        kwargs["on_stdout"]('{"original_leased_host":true}')
        return {"outcome": "exited", "output_complete": True, "returncode": 0}

    def wrong_transport(*args, **kwargs):
        pytest.fail("leased command lost its original Host supervision")

    monkeypatch.setattr("loopx.control_plane.turn_driver.host_process_transport.run_host_process", supervised)
    monkeypatch.setattr("loopx.collaboration_mcp.subprocess.run", wrong_transport)
    monkeypatch.setattr(runner._preview_transport, "preview", wrong_transport)
    assert runner._cli(binding, *arguments, delegated_lease=lease_context) == {"original_leased_host": True}
    assert len(calls) == 1 and calls[0][0][-len(arguments):] == list(arguments)
    assert "loopx.control_plane.turn_driver.delegated_cli" in calls[0][0]
    assert calls[0][1]["delegated_lease"] is lease_context
    assert calls[0][1]["project"] == Path(binding["workspace"])
    assert runner._preview_transport._process is None


def test_reused_preview_matches_fresh_cli_and_rereads_validator_and_goal(service, monkeypatch):
    root, runner = service
    reused = runner._cli
    source = runner.registry.read_bytes(), runner.config.read_bytes()
    validator = Path(json.loads(source[0])["goals"][0]["repo"]) / "validation" / "acceptance.py"
    original = validator.read_bytes()
    canonical = demo.canonical_tasks(root)
    pids = set()
    try:
        for drift in (False, True, False):
            validator.write_bytes(original + (b"\n# current drift\n" if drift else b""))
            with monkeypatch.context() as patch:
                patch.setattr(runner, "_cli", lambda *args, **kwargs: fresh_cli(runner, *args, **kwargs))
                expected = runner.inspect("analysis")
            observed = runner.inspect("analysis")
            assert observed == expected
            assert observed["state"] == ("acceptance_unavailable" if drift else "runtime_unverified")
            pids.add(runner._preview_transport._process.pid)
        assert len(pids) == 1, "unchanged code and workspace should reuse the supervisor"
        workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
        unsafe = workspace / "unsafe-fixture.py"
        unsafe.write_text("fixture = " + repr("tok" + "en=" + "abcdefghijklmnop1234"))
        with monkeypatch.context() as patch:
            patch.setattr(runner, "_cli", lambda *args, **kwargs: fresh_cli(runner, *args, **kwargs))
            expected = runner.inspect("analysis")
        assert runner.inspect("analysis") == expected
        assert expected["state"] == "turn_blocked"
        unsafe.unlink()
        assert runner.inspect("analysis")["state"] == "runtime_unverified"
        assert demo.canonical_tasks(root) == canonical
        assert (runner.registry.read_bytes(), runner.config.read_bytes()) == source
        assert not (root / "host-started").exists()
        assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))
        assert not list(runner.path("inventory").parent.glob("*.json"))
    finally:
        runner._preview_transport.close()
    assert runner._cli == reused


def test_code_invalidation_retires_worker_without_reusing_authority(service, monkeypatch):
    from loopx.control_plane.collaboration import delegation_preview_transport as transport

    _, runner = service
    original = transport._source_snapshot
    generation = 0
    monkeypatch.setattr(transport, "_source_snapshot", lambda release: (*original(release), generation))
    try:
        first = runner.inspect("analysis")
        process = runner._preview_transport._process
        generation += 1
        second = runner.inspect("analysis")
        assert first == second
        assert process.poll() == 0
        assert runner._preview_transport._process.pid != process.pid
    finally:
        runner._preview_transport.close()


def test_real_source_edit_reloads_python_module_and_environment(tmp_path):
    from loopx.control_plane.collaboration.delegation_preview_transport import DelegationPreviewTransport

    package = tmp_path / "loopx"
    package.mkdir()
    module = package / "owned_fixture.py"
    module.write_text("VALUE = 'old'\n")
    worker = tmp_path / "fixture_worker.py"
    worker.write_text(
        "import sys,json,os\n"
        f"sys.path.insert(0,{str(package)!r})\n"
        "import owned_fixture\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " print(json.dumps({'kind':'preview','id':r['id'],'returncode':0,"
        "'value':{'module':owned_fixture.VALUE,'env':os.environ['LOOPX_PREVIEW_FIXTURE']}}),flush=True)\n"
    )
    transport = DelegationPreviewTransport()
    environment = {**_pinned_release_environment(), "LOOPX_PREVIEW_FIXTURE": "before"}

    def read():
        return transport.preview(command=[sys.executable, "-P", str(worker)], workspace=tmp_path,
            release=tmp_path, environment=environment, registry=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime", goal_id="goal", agent_id="agent", todo_id="todo",
            argv=("fixture",), timeout=5)

    try:
        assert read() == {"module": "old", "env": "before"}
        first = transport._process
        module.write_text("VALUE = 'updated'\n")
        assert read() == {"module": "updated", "env": "before"}
        assert first.poll() == 0
        second = transport._process
        environment["LOOPX_PREVIEW_FIXTURE"] = "after"
        assert read() == {"module": "updated", "env": "after"}
        assert second.poll() == 0
    finally:
        transport.close()


def test_concurrent_services_preserve_registry_runtime_and_workspace_partition(service, tmp_path, request, monkeypatch):
    root, first = service
    second_root, second = delegation_service.__wrapped__(
        tmp_path / "second", SimpleNamespace(param=request.node.callspec.params["delegation_service"]), monkeypatch
    )
    second = reusable_service(second)
    expected = [runner.inspect("analysis") for runner in (first, second)]
    before = [demo.canonical_tasks(candidate) for candidate in (root, second_root)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda runner: runner.inspect("analysis"), (first, second)))
        assert results == expected
        assert first._preview_transport._process.pid != second._preview_transport._process.pid
        assert [demo.canonical_tasks(candidate) for candidate in (root, second_root)] == before
    finally:
        first._preview_transport.close()
        second._preview_transport.close()


def test_source_change_during_preview_rejects_result_and_releases_worker(service, monkeypatch):
    from loopx.control_plane.collaboration import delegation_preview_transport as transport

    _, runner = service
    original = transport._source_snapshot
    calls = 0

    def changed(release):
        nonlocal calls
        calls += 1
        return (*original(release), calls)

    monkeypatch.setattr(transport, "_source_snapshot", changed)
    with pytest.raises(ValueError, match="source changed"):
        runner.inspect("analysis")
    assert runner._preview_transport._process is None


def test_preview_worker_rejects_execute_abbreviation_and_retargeting(service):
    _, runner = service
    binding = runner.binding("analysis", require_active=True)
    arguments = ("turn", "run-once", "--goal-id", runner.goal_id, "--agent-id", binding["agent_id"],
                 "--todo-id", binding["todo_id"], "--turn-instance-id", "readonly-worker",
                 *runner._execution_arguments(binding, "readonly-worker"))
    for extra in (("--exec",), ("--todo-id", "other"), ("--resume-turn-key", "other")):
        with pytest.raises(ValueError):
            # Explicit resume retains the old CLI. Test the narrow worker itself
            # to prove it cannot mutate even if an IO caller misroutes a frame.
            runner._preview_transport.preview(
                command=_python_module_command("loopx.control_plane.collaboration.delegation_preview_worker"),
                workspace=Path(binding["workspace"]), release=Path(__file__).resolve().parents[1],
                environment=_pinned_release_environment(), registry=runner.registry,
                runtime_root=runner.root, goal_id=runner.goal_id, agent_id=binding["agent_id"],
                todo_id=binding["todo_id"], argv=(*arguments, *extra), timeout=10,
            )
        assert runner._preview_transport._process is None
    assert not any(runner.inspect("analysis")["effects"].values())
    runner._preview_transport.close()


def test_preview_timeout_releases_transport_and_allows_fresh_inspection(service):
    _, runner = service
    binding = runner.binding("analysis", require_active=True)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        runner._cli(binding, "turn", "run-once", "--goal-id", runner.goal_id,
                    "--agent-id", binding["agent_id"], "--todo-id", binding["todo_id"],
                    "--turn-instance-id", "deadline", *runner._execution_arguments(binding, "deadline"), timeout=0.001)
    assert time.monotonic() - started < 5
    assert runner._preview_transport._process is None
    assert runner.inspect("analysis")["state"] == "runtime_unverified"
    runner._preview_transport.close()


def test_partial_supervisor_frame_obeys_parent_deadline_and_eof_cleanup():
    from loopx.control_plane.collaboration.delegation_preview_transport import DelegationPreviewTransport

    transport = DelegationPreviewTransport()
    # Isolate the pipe boundary: the supervisor emits an incomplete handshake
    # and waits for EOF. Reading a partial line must still honor the deadline.
    process = subprocess.Popen([sys.executable, "-c",
        "import sys;sys.stdout.write('{');sys.stdout.flush();sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    transport._process = process
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            transport._read(started + 0.1, 0.1)
    finally:
        transport.close()
    assert time.monotonic() - started < 2
    assert process.poll() == 0


@pytest.mark.skipif(sys.platform == "win32", reason="SIGSTOP fault injection requires POSIX")
def test_backpressured_supervisor_input_uses_original_parent_deadline(tmp_path, monkeypatch):
    from loopx.control_plane.collaboration.delegation_preview_transport import DelegationPreviewTransport

    transport = DelegationPreviewTransport()
    worker = (
        "import json,sys\nfor line in sys.stdin:\n"
        " r=json.loads(line);print(json.dumps({'kind':'preview','id':r['id'],"
        "'returncode':0,'value':{'read_only':True}}),flush=True)"
    )
    options = dict(command=[sys.executable, "-c", worker], workspace=tmp_path,
                   release=tmp_path, environment=_pinned_release_environment(),
                   registry=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
                   goal_id="fixture-goal", agent_id="fixture-agent", todo_id="todo_fixture")
    original_send = transport._send
    send_durations = []

    def measured_send(*args, **kwargs):
        started = time.monotonic()
        try:
            return original_send(*args, **kwargs)
        finally:
            send_durations.append(time.monotonic() - started)

    monkeypatch.setattr(transport, "_send", measured_send)
    timer = None
    process = None
    try:
        assert transport.preview(**options, argv=("warm",), timeout=5) == {"read_only": True}
        process = transport._process
        # Stop only our disposable supervisor. A valid sub-limit frame fills
        # its pipe; the parent must stop waiting for input before the watchdog
        # resumes cleanup, not merely time out the subsequent output read.
        os.kill(process.pid, signal.SIGSTOP)
        timer = Timer(1, lambda: os.kill(process.pid, signal.SIGCONT))
        timer.start()
        with pytest.raises(subprocess.TimeoutExpired):
            transport.preview(**options, argv=("x" * 65536,), timeout=0.1)
        assert send_durations[-1] < 0.4, send_durations
        assert transport._process is None
        assert process.poll() == 0
    finally:
        if timer:
            timer.cancel()
        if process and process.poll() is None:
            os.kill(process.pid, signal.SIGCONT)
        transport.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX retirement cleanup fence")
@pytest.mark.parametrize("retirement", ["idle", "lifetime", "broken_pipe"])
@pytest.mark.parametrize("startup_delay", [0, 0.35], ids=["ready", "slow-worker"])
def test_unaccepted_request_recovers_only_after_owned_retirement(
    tmp_path, monkeypatch, retirement, startup_delay,
):
    from loopx.control_plane.collaboration.delegation_preview_transport import DelegationPreviewTransport

    marker = tmp_path / "retiring"
    worker = (
        "import json,sys,signal\nfrom pathlib import Path\n"
        f"signal.signal(signal.SIGTERM,lambda *_:Path({str(marker)!r}).touch())\n"
        f"import time;time.sleep({startup_delay!r})\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line);print(json.dumps({'kind':'preview','id':r['id'],"
        "'returncode':0,'value':{'read_only':True}}),flush=True)"
    )
    # Advance only the chosen production retirement clock after the first
    # result. A 200ms lifetime from spawn can retire a slow worker before that
    # result and tests a different path. Keep the real Host's 300ms cleanup
    # grace, framed IO, request deadline and process group unchanged.
    timer = 300000 if retirement == "lifetime" else 30000
    preload = (
        "const schedule=globalThis.setTimeout,cancel=globalThis.clearTimeout;"
        "const clocks=new Map();"
        "globalThis.setTimeout=(f,ms,...a)=>{const h=schedule(f,ms,...a);"
        f"if(ms==={timer})clocks.set(h,()=>f(...a));return h;}};"
        "globalThis.clearTimeout=h=>{clocks.delete(h);return cancel(h);};"
        "process.once('SIGUSR2',()=>{const due=[...clocks];clocks.clear();"
        "for(const [h,fire] of due){cancel(h);fire();}});"
    )
    environment = {**_pinned_release_environment(), "NODE_OPTIONS": "--import=data:text/javascript," + quote(preload, safe="")}
    transport = DelegationPreviewTransport()
    options = dict(command=[sys.executable, "-c", worker], workspace=tmp_path,
                   release=tmp_path, environment=environment,
                   registry=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
                   goal_id="fixture", agent_id="lead", todo_id="todo_fixture",
                   argv=("inspect",), timeout=5)
    try:
        assert transport.preview(**options) == {"read_only": True}
        original = transport._process
        os.kill(original.pid, signal.SIGUSR2)
        until = time.monotonic() + 5
        while not marker.exists():
            assert time.monotonic() < until, "retirement did not start"
            time.sleep(0.005)
        assert original.poll() is None, "probe must enter the cleanup window"
        if retirement == "broken_pipe":
            send = transport._send

            def send_after_exit(value, *args):
                if value.get("kind") == "request" and value["id"] == 2:
                    original.wait(timeout=5)
                return send(value, *args)

            monkeypatch.setattr(transport, "_send", send_after_exit)
        assert transport.preview(**options) == {"read_only": True}
        assert original.poll() == 0
        assert transport._process.pid != original.pid
        assert transport._sequence == 1
    finally:
        transport.close()


@pytest.mark.parametrize("frame", [
    {"kind": "retired", "last_id": 1},
    {"kind": "retired", "last_id": 0, "extra": True},
    {"kind": "retired", "last_id": False},
])
def test_invalid_retirement_fence_cannot_replay_a_request(tmp_path, monkeypatch, frame):
    from loopx.control_plane.collaboration.delegation_preview_transport import DelegationPreviewTransport

    worker = "import sys,json\nfor line in sys.stdin:\n r=json.loads(line);print(json.dumps({'kind':'preview','id':r['id'],'returncode':0,'value':{}}),flush=True)"
    transport = DelegationPreviewTransport()
    read = transport._read
    calls = []

    def read_invalid_fence(*args):
        response = read(*args)
        calls.append(response)
        return frame if response.get("kind") == "preview" else response

    monkeypatch.setattr(transport, "_read", read_invalid_fence)
    with pytest.raises(ValueError, match="no structured result"):
        transport.preview(command=[sys.executable, "-c", worker], workspace=tmp_path,
                          release=tmp_path, environment=_pinned_release_environment(),
                          registry=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
                          goal_id="fixture", agent_id="lead", todo_id="todo_fixture",
                          argv=("inspect",), timeout=5)
    assert len(calls) == 2, "invalid fence must not start a second worker"
    assert transport._process is None
