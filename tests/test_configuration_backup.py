import json
import os
import subprocess
import sys
import tarfile
import threading
import http.client
from pathlib import Path
import venv

import pytest

from loopx.configuration_backup import capture_configuration_backup, restore_configuration_backup
from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import read_machine_configuration
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime


@pytest.fixture
def environment(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    source = tmp_path / "project/.loopx/registry.json"
    source.parent.mkdir(parents=True)
    goal = {"id": "fixture", "repo": str(source.parent.parent), "coordination": {
        "storage_target": {"schema_version": "loopx_new_goal_storage_target_v0", "provider": "sqlite"}},
        "control_plane": {"optional_provider": {"nullable": None, "text": "完整" * 20000}}}
    source.write_text(json.dumps({"goals": [goal]}))
    runtime = tmp_path / "runtime"
    machine = runtime / "machine/configuration.json"
    machine.parent.mkdir(parents=True)
    machine.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v0", "new_goal_provider": "sqlite"}}}))
    global_registry = runtime / "registry.global.json"
    global_registry.write_text(json.dumps({"registry_role": "global-local", "goals": [{
        "id": "fixture", "source_registry": str(source), "repo": goal["repo"], "control_plane": {"stale": True}}]}))
    yield source, runtime, global_registry, goal
    restart_effect_runtime()


def cli(*args):
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *map(str, args)],
        env={**os.environ, "LOOPX_USAGE_PING": "0"}, capture_output=True, text=True, timeout=60)
    return result.returncode, json.loads(result.stdout)


def test_source_owner_capture_and_real_machine_owner_readback(environment, tmp_path):
    source, runtime, registry, goal = environment
    before = source.read_bytes(), (runtime / "machine/configuration.json").read_bytes()
    backup = capture_configuration_backup(registry_path=registry, runtime_root=runtime)
    assert backup["data"]["goals"] == [{"goal_id": "fixture", "goal_configuration": goal}]
    destination = tmp_path / "checkpoint"
    receipt = restore_configuration_backup(backup, destination=destination, expected_sha256=backup["sha256"], execute=True)
    assert receipt["readback_verified"] is True and receipt["activation_performed"] is False
    assert read_machine_configuration(destination, registry=build_builtin_machine_configuration_registry()) == backup["data"]["machine_configuration"]
    assert not (destination / "registry.global.json").exists()
    assert before == (source.read_bytes(), (runtime / "machine/configuration.json").read_bytes())


def test_cli_export_verify_restore_and_occupied_target(environment, tmp_path):
    _, runtime, registry, _ = environment
    output = tmp_path / "backup.json"
    arguments = ("--runtime-root", runtime, "--registry", registry, "configuration-backup")
    assert cli(*arguments, "export", "--output", output)[1]["written"] is False
    assert not output.exists()
    code, exported = cli(*arguments, "export", "--output", output, "--execute")
    assert code == 0 and exported["goal_count"] == 1
    original = output.read_bytes()
    assert cli(*arguments, "export", "--output", output, "--execute")[0] == 1
    assert output.read_bytes() == original and output.stat().st_mode & 0o777 == 0o600
    assert cli(*arguments, "verify", "--input", output)[0] == 0
    restore = (*arguments, "restore", "--input", output, "--expected-sha256", exported["sha256"], "--destination", tmp_path / "restored")
    assert cli(*restore)[1]["written"] is False
    assert cli(*restore, "--execute")[1]["readback_verified"] is True
    assert cli(*restore, "--execute")[0] == 1


def test_full_state_backup_has_first_class_configuration_component(environment, tmp_path):
    source, runtime, registry, goal = environment
    payload = execute_state_backup_plan(build_state_backup_plan(project=source.parent.parent,
        runtime_root=runtime, output_dir=tmp_path / "backups", include_skills=False, include_automations=False))
    with tarfile.open(payload["archive_path"]) as archive:
        backup = json.load(archive.extractfile("configuration-backup.json"))
    assert backup["data"]["goals"][0]["goal_configuration"] == goal
    assert payload["execution"]["configuration_backup"]["readback_verified"] is True


def test_current_project_cli_uses_selected_project_not_invoked_registry(environment, tmp_path):
    source, runtime, registry, goal = environment
    other = tmp_path / "other-project/.loopx/registry.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"goals": [{"id": "other", "repo": str(other.parent.parent), "extension": {"disabled": False}}]}))
    code, payload = cli("--runtime-root", runtime, "--registry", registry, "backup-state",
        "--project", other.parent.parent, "--output-dir", tmp_path / "project-backup",
        "--current-project-only", "--no-skills", "--no-automations", "--execute")
    assert code == 0, payload
    with tarfile.open(payload["archive_path"]) as archive:
        backup = json.load(archive.extractfile("configuration-backup.json"))
    assert [entry["goal_id"] for entry in backup["data"]["goals"]] == ["other"]
    assert backup["data"]["goals"][0]["goal_configuration"]["extension"] == {"disabled": False}


def test_lossy_source_values_abort_without_replacing_previous_backup(environment, tmp_path):
    source, runtime, registry, goal = environment
    goal["extension"] = {"large_integer": 9007199254740993}
    source.write_text(json.dumps({"goals": [goal]}))
    plan = build_state_backup_plan(project=source.parent.parent, runtime_root=runtime,
        output_dir=tmp_path / "previous-backup", include_skills=False, include_automations=False)
    from pathlib import Path
    archive, manifest = Path(plan["archive_path"]), Path(plan["manifest_path"])
    archive.parent.mkdir()
    archive.write_bytes(b"previous verified archive")
    manifest.write_bytes(b"previous verified manifest")
    with pytest.raises(ValueError, match="complete source values"):
        execute_state_backup_plan(plan)
    assert archive.read_bytes() == b"previous verified archive"
    assert manifest.read_bytes() == b"previous verified manifest"
    assert len(list(archive.parent.iterdir())) == 2


def test_live_http_download_recovery_and_negative_digest(environment):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    _, runtime, registry, _ = environment
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.verbose = registry, runtime, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(operation, body):
        connection = http.client.HTTPConnection(*server.server_address, timeout=30)
        try:
            connection.request("POST", f"/api/chat/configuration-backup/{operation}", json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        status, exported = post("export", {})
        assert status == 200 and exported["goal_count"] == 1
        body = {"backup": exported["backup"], "expected_sha256": exported["sha256"], "execute": False}
        status, response = post("restore", body)
        assert status == 200, response
        assert response["status"] == "preview"
        assert not (runtime / "backups/configuration").exists()
        assert post("restore", {**body, "execute": True})[1]["status"] == "restored"
        assert post("restore", {**body, "execute": True})[0] == 400
        assert post("restore", {**body, "expected_sha256": "changed"})[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_browser_backup_rejects_selected_environment_without_loopx(tmp_path):
    selected = tmp_path / "empty-environment"
    venv.EnvBuilder(with_pip=False).create(selected)
    python = selected / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    temporary = tmp_path / "servers"
    temporary.mkdir()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", """
import assert from 'node:assert/strict';
import {configurationBackupScenario} from './examples/personal-workspace-browser/configuration-backup.mjs';
await assert.rejects(configurationBackupScenario.run({
  browser: {newPage() {throw new Error('unexpected UI: wrong interpreter started');}}, url: ''
}), /No module named ['"]loopx['"]/);
"""],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "LOOPX_TEST_PYTHON": str(python), "LOOPX_PYTHON_BIN": str(python),
             "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary)},
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(temporary.glob("loopx-configuration-browser-*"))


def test_browser_backup_reuses_selected_python_without_checkout_shadow(tmp_path):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "loopx.py").write_text("raise RuntimeError('unexpected PYTHONPATH shadow')\n")
    result = subprocess.run(
        ["node", "--input-type=module", "-e", """
import assert from 'node:assert/strict';
import {configurationBackupScenario} from './examples/personal-workspace-browser/configuration-backup.mjs';
await assert.rejects(configurationBackupScenario.run({
  browser: {newPage() {throw new Error('selected backend reached UI');}}, url: ''
}), /selected backend reached UI/);
"""],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "LOOPX_TEST_PYTHON": sys.executable, "LOOPX_PYTHON_BIN": sys.executable,
             "PYTHONPATH": str(shadow)}, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
