"""Exercise the split Workspace over work first accepted by an installed Core."""

from contextlib import contextmanager
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlencode
from zipfile import ZipFile

import pytest

from tests.test_core_distribution import AGENT, GOAL, _assert_committed, _environment, _run
from tests.test_core_distribution import task as task


@pytest.fixture
def wheels():
    core = os.environ.get("LOOPX_CORE_TEST_WHEEL")
    workspace = os.environ.get("LOOPX_WORKSPACE_TEST_WHEEL")
    if not core or not workspace:
        pytest.skip("set LOOPX_CORE_TEST_WHEEL and LOOPX_WORKSPACE_TEST_WHEEL to the built artifacts")
    return Path(core).resolve(strict=True), Path(workspace).resolve(strict=True)


@pytest.fixture
def core_python(tmp_path, wheels):
    uv = shutil.which("uv")
    assert uv, "uv is required to qualify a clean split installation"
    environment = tmp_path / "environment"
    subprocess.run([uv, "venv", "--python", sys.executable, str(environment)], check=True, timeout=45)
    python = str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    subprocess.run([uv, "pip", "install", "--offline", "--python", python, str(wheels[0])],
                   check=True, timeout=45)
    return python


def test_workspace_wheel_owns_the_complete_bundle_without_core_files(wheels):
    core, workspace = wheels
    with ZipFile(core) as core_zip, ZipFile(workspace) as workspace_zip:
        assert not (set(core_zip.namelist()) & set(workspace_zip.namelist()))
        prefix = "loopx/web/chat/"
        manifest = json.loads(workspace_zip.read(prefix + "bundle-manifest.json"))
        assets = {name.removeprefix(prefix) for name in workspace_zip.namelist() if name.startswith(prefix)}
        assert assets == manifest["files"].keys() | {"bundle-manifest.json"}
        assert all(sha256(workspace_zip.read(prefix + name)).hexdigest() == digest
                   for name, digest in manifest["files"].items())
        assert "loopx/chat_server.py" in workspace_zip.namelist()
        assert "loopx/dashboard_launcher.py" in workspace_zip.namelist()
        assert "loopx/cli.py" not in workspace_zip.namelist()
        assert "loopx/control_plane/turn_driver/executor.py" in core_zip.namelist()


def _python_json(python, cwd, source):
    result = subprocess.run([python, "-I", "-c", source], cwd=cwd, env=_environment(python),
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


_RUNTIME_IDENTITY = "from loopx.release_manifest import release_runtime_identity; print(json.dumps(release_runtime_identity()))"


def _read(port, path, *, status=200):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        connection.request("GET", path, headers={"Origin": f"http://127.0.0.1:{port}"})
        response = connection.getresponse()
        body = response.read()
        assert response.status == status, (path, response.status, body[:500])
        return body
    finally:
        connection.close()


@contextmanager
def _running_workspace(python, cwd, arguments, log_path, port):
    with log_path.open("w", encoding="utf-8") as log, subprocess.Popen(
        [python, "-I", "-X", "importtime", "-m", "loopx.entrypoint", *arguments],
        cwd=cwd, env=_environment(python), stdout=log, stderr=log,
    ) as process:
        try:
            deadline = time.monotonic() + 25
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    ready = json.loads(_read(port, "/api/chat/readiness"))
                except (ConnectionError, TimeoutError):
                    time.sleep(0.1)
                else:
                    assert ready["state"] == "ready"
                    yield ready["runtime_identity"]
                    return
            pytest.fail(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()


def test_workspace_reads_the_original_task_and_core_replays_after_removal(core_python, task, wheels, tmp_path):
    workspace, runtime, registry, arguments = task
    before = _python_json(core_python, workspace, "import json; " + _RUNTIME_IDENTITY)
    assert before["package_fingerprint"]
    absent = _run(core_python, ["dashboard"], workspace, expected=2)
    assert "install the matching loopx-workspace wheel" in absent.stderr
    completed = json.loads(_run(core_python, arguments, workspace).stdout)
    turn_key = _assert_committed(core_python, workspace, runtime, completed)
    journal = runtime / "goals" / GOAL / "turns" / f"{turn_key.removeprefix('sha256:')}.json"
    journal_bytes = journal.read_bytes()
    todo_id = json.loads(journal_bytes)["plan"]["work_contract"]["selected_todo"]["todo_id"]
    registry_bytes = registry.read_bytes()

    subprocess.run(["uv", "pip", "install", "--offline", "--python", core_python, str(wheels[1])],
                   check=True, timeout=45)
    installed = _python_json(core_python, workspace, """import contextlib, importlib.metadata as m, importlib.util as u
import io, json, sys
from pathlib import Path
from loopx.entrypoint import main
core = m.distribution("loopx-core")
owner = m.distribution("loopx-workspace")
assert owner.requires == [f"loopx-core=={core.version}"]
assert u.find_spec("loopx.cli") is None
with contextlib.redirect_stdout(io.StringIO()):
    assert main(["--help"]) == 0
    assert main(["commands", "--format", "json"]) == 0
assert "loopx.chat_server" not in sys.modules
assert "loopx.dashboard_launcher" not in sys.modules
analysis = "loopx/capabilities/benchmark_toolkit/trajectory_assessment_store.py"
print(json.dumps({
    "module": str(Path(owner.locate_file("loopx/dashboard_launcher.py"))),
    "has_analysis": any(p.as_posix() == analysis for p in core.files),
}))
""")
    combined = _python_json(core_python, workspace, "import json; " + _RUNTIME_IDENTITY)
    assert combined["package_fingerprint"] != before["package_fingerprint"]
    help_text = _run(core_python, ["--help"], workspace).stdout
    assert "loopx dashboard" in help_text
    endpoint_help = _run(core_python, ["chat-endpoint", "--help"], workspace).stdout
    assert "chat-endpoint" in endpoint_help

    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    endpoint_list = json.loads(_run(core_python, [*route, "chat-endpoint", "list", "--format", "json"], workspace).stdout)
    assert endpoint_list["ok"] and endpoint_list["endpoints"] == []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    options = ["--port", str(port), "--no-open", "--goal-id", GOAL, "--scan-path", str(workspace)]
    for option in ("--codex-bin", "--claude-bin", "--kiro-cli-bin", "--lark-cli-bin"):
        options.extend([option, str(tmp_path / "unconfigured-host")])
    history_query = urlencode({"goal_id": GOAL, "agent_id": AGENT})
    detail_query = urlencode({"goal_id": GOAL, "todo_id": todo_id})
    observed = []
    for command in ("dashboard", "chat"):
        log = tmp_path / f"{command}.log"
        argv = [*route, command, *options]
        if command == "chat":
            argv.append("--no-private-reactions")
        with _running_workspace(core_python, workspace, argv, log, port) as identity:
            assert identity == combined
            html = _read(port, "/chat/")
            script = re.search(rb'src="(/chat/assets/[^"]+\.js)"', html)
            assert script and _read(port, script[1].decode())
            task_view = json.loads(_read(port, f"/api/chat/todo/detail?{detail_query}"))
            assert task_view["todo_id"] == todo_id and task_view["status"] == "done"
            completed_view = json.loads(_read(port, f"/api/chat/completed-todos?{history_query}"))
            assert [(item["todo_id"], item["done"]) for item in completed_view["items"]] == [(todo_id, True)]
            assert completed_view["items"][0]["evidence"] == (
                "LoopX Turn validated completion: Produced squared integers zero through seven."
            )
            assert "benchmark_toolkit.trajectory_assessment_store" not in log.read_text(encoding="utf-8")
            if not installed["has_analysis"]:
                response = json.loads(_read(port, f"/api/chat/research-analysis?goal_id={GOAL}", status=503))
                assert response["error_code"] == "research_analysis_unavailable"
            observed.append((task_view, completed_view))
            if command == "dashboard":
                assert "Reusing existing LoopX Chat service" in _run(core_python, argv, workspace).stdout
                module = Path(installed["module"])
                original = module.read_bytes()
                try:
                    module.write_bytes(original + b"\n# Disposable service revision check.\n")
                    stale = _run(core_python, argv, workspace, expected=1)
                    assert "different installed runtime" in stale.stdout
                finally:
                    module.write_bytes(original)
    assert observed[0] == observed[1]

    subprocess.run(["uv", "pip", "uninstall", "--python", core_python, "loopx-workspace"],
                   check=True, timeout=45)
    assert _python_json(core_python, workspace, "import json; " + _RUNTIME_IDENTITY) == before
    assert "loopx dashboard" not in _run(core_python, ["--help"], workspace).stdout
    _run(core_python, ["dashboard"], workspace, expected=2)
    replay = json.loads(_run(core_python, [*arguments, "--resume-turn-key", turn_key], workspace).stdout)
    assert not any(replay["effects"][name] for name in ("host_invoked", "state_written", "quota_spent"))
    assert _assert_committed(core_python, workspace, runtime, replay) == turn_key
    assert registry.read_bytes() == registry_bytes and journal.read_bytes() == journal_bytes
