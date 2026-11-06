"""Real Core File/SQLite continuation through the SForge caller and native IO."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture(params=["file", "sqlite"])
def continuing_trial(tmp_path, monkeypatch, request):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime import sforge
    from benchmark.edgebench.execution_commitment import NativeExecutionEvidence

    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "tests/control_plane"))
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "off")
    fixture = runpy.run_path(
        str(root / "tests/control_plane/test_goal_acceptance_cli.py")
    )["acceptance_goal"].__wrapped__
    project, _, cli, run = fixture(tmp_path, monkeypatch, request)
    trial = tmp_path / "native"
    trial.mkdir()
    record = trial / "solver_execution.json"
    now = time.time()
    record.write_text(
        json.dumps(
            dict(
                schema_version=1,
                status="normal_exit",
                segments=[dict(started_at=now, finished_at=now, status="normal_exit")],
            )
        )
    )
    child = [None]
    evidence = NativeExecutionEvidence(
        run_id="run",
        task_id="task",
        trial=trial,
        target_seconds=1,
        runner_active=lambda: child[0] is not None and child[0].poll() is None,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/v1/feedback/scoped/execution-commitment"
            packet = dict(
                schema_version="sforge_execution_commitment_observation_v0",
                task_id="task",
                observed_at_epoch=time.time(),
                fulfilled=evidence.fulfilled(),
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(packet).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/api/v1/feedback/scoped"
    monkeypatch.setenv("SFORGE_FEEDBACK_URL", endpoint)
    control = tmp_path / "control"
    control.mkdir()
    monkeypatch.setattr(sforge, "_PYTHON", str(Path(sys.executable).parent.parent))
    monkeypatch.setattr(sforge, "_CONTROL", str(control))
    monkeypatch.setattr(sforge, "_SRC", str(root))

    class Environment:
        async def upload_file(self, source, target):
            shutil.copyfile(source, target)

        async def exec(self, command):
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            return SimpleNamespace(stdout=result.stdout, return_code=result.returncode)

    class Runtime:
        goal_id = "goal-acceptance"
        agent_id = "agent-a"

        async def _loopx(self, environment, arguments, **kwargs):
            code, payload = run(*arguments)
            assert code == 0, payload
            return payload

    worker = sforge.SForgeWorker.__new__(sforge.SForgeWorker)
    worker.runtime, worker.environment = Runtime(), Environment()
    worker.cwd, worker.warm_start, worker.commitment_task_id = (
        str(project),
        None,
        "task",
    )
    try:
        yield worker, cli, run, record, child, control
    finally:
        if child[0] is not None:
            child[0].terminate()
            child[0].wait(5)
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_local_delivery_replans_until_native_commitment_is_fulfilled(
    continuing_trial, monkeypatch
):
    worker, cli, run, record, child, control = continuing_trial
    asyncio.run(worker._configure_execution_commitment())
    contract = cli("inspect")[1]["goal_acceptance_contract"]
    assert contract["scope"] == {"kind": "goal"} and contract["tasks"] == []
    code, failed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 1 and failed["checks_passed"] is False
    code, completed = run(
        "todo",
        "complete",
        "--goal-id",
        "goal-acceptance",
        "--todo-id",
        "todo_export",
        "--agent-id",
        "agent-a",
    )
    assert code == 0, completed
    code, quota = run(
        "quota",
        "should-run",
        "--goal-id",
        "goal-acceptance",
        "--agent-id",
        "agent-a",
        "--context-delivery",
        "off",
        "--delta-delivery",
        "off",
    )
    assert code == 0 and quota["decision"] == "autonomous_replan_required", quota
    assert quota["goal_frontier_projection"].get("terminal_state") is None

    # A warm read preserves the old configuration file and authority revision.
    before = cli("inspect")[1]
    original = (control / "execution-commitment-configure.json").read_bytes()
    worker.warm_start = object()
    asyncio.run(worker._configure_execution_commitment())
    assert cli("inspect")[1] == before
    assert (control / "execution-commitment-configure.json").read_bytes() == original
    assert not list(control.glob("*compare*"))

    child[0] = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    record.write_text(
        json.dumps(
            dict(
                schema_version=1,
                status="pending",
                segments=[dict(started_at=time.time(), status="pending")],
            )
        )
    )
    time.sleep(1.1)
    # An already running effect service can have an earlier environment. Only
    # the host-pinned relay supplies this check; a shell override cannot replace it.
    monkeypatch.setenv(
        "SFORGE_FEEDBACK_URL", "http://127.0.0.1:1/api/v1/feedback/stale"
    )
    code, passed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 0 and passed["checks_passed"] is True, passed
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "accepted"


def test_legacy_warm_goal_is_not_rewritten(continuing_trial):
    worker, cli, _, _, _, control = continuing_trial
    before = cli("inspect")[1]
    worker.warm_start = object()
    with pytest.raises(ValueError, match="does not retain"):
        asyncio.run(worker._configure_execution_commitment())
    assert cli("inspect")[1] == before
    assert not list(control.iterdir())
