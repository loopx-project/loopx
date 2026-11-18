"""Real Core CLI/server + packaged Workbench + a replaceable model backend."""
import hashlib
import importlib
import json
from pathlib import Path
import select
import socket
import subprocess
import sys
import time

from loopx.control_plane.effect_runtime import restart_effect_runtime

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "tests/control_plane"))
isolate_sqlite_runtime = importlib.import_module("canonical_authority_fixture").isolate_sqlite_runtime


def test_packaged_goal_creation_interruption_and_readback(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LOOPX_HOME", str(home / ".loopx"))
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    configuration = runtime / "machine/configuration.json"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(json.dumps({
        "schema_version": "loopx_machine_configuration_v0", "namespaces": {"goal_storage": {
            "schema_version": "loopx_goal_storage_defaults_v1", "new_goal_provider": "sqlite",
            "canonical_creation": True, "new_goal_handoff_mode": "hard_lease",
        }},
    }))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": []}))
    with socket.socket() as listener:
        listener.bind(("localhost", 0))
        port = listener.getsockname()[1]
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--runtime-root", str(runtime)]
    # Only Workbench's prebuilt UI is visited; Core serves its real API from the CLI.
    core_log = tmp_path / "core.log"
    with core_log.open("w+") as log:
        core = subprocess.Popen(command + [
            "chat", "--host", "localhost", "--port", str(port), "--no-open",
            "--scan-path", str(project), "--scan-root", str(project),
            "--project-workspace-grant", "workspace_read",
            "--assets-dir", str(ROOT / "apps/presentation/dashboard"),
        ], cwd=ROOT, stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                assert core.poll() is None, core_log.read_text()
                ready, _, _ = select.select([core.stdout], [], [], max(0, deadline - time.monotonic()))
                assert ready, core_log.read_text()
                line = core.stdout.readline()
                log.write(line)
                log.flush()
                if "Serving LoopX Chat at" in line:
                    break
            else:
                raise AssertionError("Core did not announce readiness within the harness startup budget")
            result = subprocess.run(
                ["node", str(Path(__file__).with_suffix(".mjs"))], cwd=ROOT / "apps/workbench",
                input=json.dumps({"root": str(tmp_path), "project": str(project), "home": str(home),
                                  "core": f"http://localhost:{port}", "registry": str(registry),
                                  "python": sys.executable, "repository": str(ROOT)}),
                capture_output=True, text=True, timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr + "\n" + core_log.read_text()
            report = json.loads(result.stdout)
            marker = runtime / "authority" / f"provider-{hashlib.sha256(report['goalId'].encode()).hexdigest()}.json"
            assert json.loads(marker.read_text())["provider"] == "sqlite"
            assert report["completedResults"] == 2
            assert report["interruptedTurns"] == 1
            print(json.dumps(report))
        finally:
            core.terminate()
            try:
                core.wait(timeout=5)
            except subprocess.TimeoutExpired:
                core.kill()
                core.wait(timeout=5)
            restart_effect_runtime(wait_for_exit=True)
