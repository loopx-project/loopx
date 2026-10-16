import json
import os
import subprocess
import sys
import tarfile
import threading
import http.client
import hashlib
from datetime import datetime
from pathlib import Path
import venv

import pytest

from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import read_machine_configuration
from loopx.capabilities.configuration_backup import (
    capture_configuration_backup,
    restore_configuration_backup,
)
from loopx.control_plane.effect_runtime import (
    EffectRuntimeConflict,
    EffectRuntimeRejected,
    restart_effect_runtime,
)
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
    environment = {**os.environ, "LOOPX_USAGE_PING": "0"}
    if os.name == "nt":
        # Exercise native Windows locale decoding even if pytest uses -X utf8.
        environment["PYTHONUTF8"] = "0"
        # Keep captured CLI JSON decodable when the parent pytest uses UTF-8.
        environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *map(str, args)],
        env=environment, capture_output=True, text=True,
        encoding="utf-8" if os.name == "nt" else None, timeout=60)
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
    assert "完整".encode("utf-8") in original
    assert cli(*arguments, "export", "--output", output, "--execute")[0] == 1
    assert output.read_bytes() == original
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    assert cli(*arguments, "verify", "--input", output)[0] == 0
    restore = (*arguments, "restore", "--input", output, "--expected-sha256", exported["sha256"], "--destination", tmp_path / "restored")
    assert cli(*restore)[1]["written"] is False
    assert cli(*restore, "--execute")[1]["readback_verified"] is True
    code, occupied = cli(*restore, "--execute")
    assert code == 1 and occupied["activation_performed"] is False
    assert occupied["error"] == "configuration restore destination already exists"
    saved = (tmp_path / "restored/configuration-backup.json").read_bytes()
    assert json.loads(saved) == json.loads(original)
    changed = (*arguments, "restore", "--input", output,
        "--expected-sha256", "changed", "--destination", tmp_path / "changed")
    code, mismatch = cli(*changed, "--execute")
    assert code == 1 and mismatch["error"] == "reviewed configuration backup digest changed"
    assert mismatch["activation_performed"] is False and not (tmp_path / "changed").exists()
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps({**json.loads(original), "sha256": "changed"}))
    code, rejected = cli(*arguments, "verify", "--input", tampered)
    assert code == 1 and rejected["error"] == "configuration backup digest mismatch"
    assert rejected["activation_performed"] is False
    assert (tmp_path / "restored/configuration-backup.json").read_bytes() == saved


@pytest.mark.parametrize("refusal", ["occupied", "tampered", "reviewed_digest"])
def test_configuration_refusals_keep_typed_errors_across_real_transport(environment, tmp_path, refusal):
    source, runtime, registry, _ = environment
    backup = capture_configuration_backup(registry_path=registry, runtime_root=runtime)
    destination = tmp_path / "checkpoint"
    expected_sha256 = backup["sha256"]
    exception, kind, code = EffectRuntimeRejected, "request_rejected", "invalid_request"
    if refusal == "occupied":
        restore_configuration_backup(backup, destination=destination, expected_sha256=expected_sha256, execute=True)
        saved = (destination / "configuration-backup.json").read_bytes()
        exception, kind, code = EffectRuntimeConflict, "conflict", "state_conflict"
    elif refusal == "tampered":
        backup = {**backup, "sha256": "changed"}
    else:
        expected_sha256 = "changed"
    original = source.read_bytes(), (runtime / "machine/configuration.json").read_bytes()
    with pytest.raises(exception) as failure:
        restore_configuration_backup(backup, destination=destination, expected_sha256=expected_sha256, execute=True)
    assert failure.value.error_kind == kind and failure.value.diagnostic_code == code
    if refusal == "occupied":
        assert (destination / "configuration-backup.json").read_bytes() == saved
    else:
        assert not destination.exists()
    assert original == (source.read_bytes(), (runtime / "machine/configuration.json").read_bytes())


def test_full_state_backup_has_first_class_configuration_component(environment, tmp_path):
    source, runtime, registry, goal = environment
    payload = execute_state_backup_plan(build_state_backup_plan(project=source.parent.parent,
        runtime_root=runtime, output_dir=tmp_path / "backups", include_skills=False, include_automations=False))
    with tarfile.open(payload["archive_path"]) as archive:
        backup = json.load(archive.extractfile("configuration-backup.json"))
    assert backup["data"]["goals"][0]["goal_configuration"] == goal
    assert payload["execution"]["configuration_backup"]["readback_verified"] is True
    capture = payload["execution"]["configuration_capture"]
    assert datetime.fromisoformat(capture["started_at"]) <= datetime.fromisoformat(capture["completed_at"])
    with tarfile.open(payload["archive_path"]) as archive:
        # The shared projection and the routed source each retain their actual
        # copied bytes. Aliases can be links to the first regular member.
        witnesses = capture["source_revisions"]
        assert len(witnesses) >= 2
        assert {row["sha256"] for row in witnesses} == {
            hashlib.sha256(source.read_bytes()).hexdigest(), hashlib.sha256(registry.read_bytes()).hexdigest(),
            hashlib.sha256((runtime / "machine/configuration.json").read_bytes()).hexdigest(),
        }
        for row in witnesses:
            raw = archive.extractfile(row["archive_path"]).read()
            assert row["sha256"] == hashlib.sha256(raw).hexdigest()
            assert row["size_bytes"] == len(raw)


@pytest.mark.parametrize("change", ["changed", "removed", "created", "aba"])
def test_full_backup_refuses_machine_configuration_from_another_capture(
    environment, tmp_path, monkeypatch, change,
):
    from loopx.capabilities import configuration_backup

    source, runtime, _, _ = environment
    path = runtime / "machine/configuration.json"
    original = path.read_bytes()
    if change == "created":
        path.unlink()
    original_capture = configuration_backup.capture_configuration_backup
    captured = []

    def between_owner_captures(**kwargs):
        # All tar members are already copied. Exercise the real configuration
        # owner with a later complete envelope (or its confirmed absence).
        if change == "removed":
            path.unlink()
        else:
            replacement = json.loads(original)
            replacement["namespaces"]["goal_storage"]["new_goal_provider"] = "file"
            path.write_text(json.dumps(replacement))
        checkpoint = original_capture(**kwargs)
        captured.append(checkpoint["data"]["machine_configuration"])
        if change == "aba":
            path.write_bytes(original)
        return checkpoint

    monkeypatch.setattr(configuration_backup, "capture_configuration_backup", between_owner_captures)
    plan = build_state_backup_plan(project=source.parent.parent, runtime_root=runtime,
        output_dir=tmp_path / "backups", include_skills=False, include_automations=False)
    with pytest.raises(ValueError, match="configuration .*copied bytes"):
        execute_state_backup_plan(plan)
    assert len(captured) == 1
    assert not list((tmp_path / "backups").iterdir()), "contradictory configuration must not publish"


@pytest.mark.parametrize("removed", [False, True], ids=["retained", "removed-after-copy"])
@pytest.mark.parametrize("link_kind", ["hardlink", "internal-symlink"])
def test_machine_configuration_alias_uses_copied_target_witness(
    environment, tmp_path, monkeypatch, removed, link_kind,
):
    from loopx.capabilities import configuration_backup

    source, runtime, _, _ = environment
    machine = runtime / "machine/configuration.json"
    # A regular copy can witness a supported local alias. Confirmed absence
    # still cannot contradict either retained kind of link.
    target = runtime / "earlier-machine.json"
    if link_kind == "hardlink":
        os.link(machine, target)
    else:
        if os.name == "nt":
            pytest.skip("native Windows symlink privilege is not qualified")
        machine.rename(target)
        machine.symlink_to("../earlier-machine.json")
    original_capture = configuration_backup.capture_configuration_backup

    def after_copy(**kwargs):
        if removed:
            machine.unlink()
        return original_capture(**kwargs)

    monkeypatch.setattr(configuration_backup, "capture_configuration_backup", after_copy)
    plan = build_state_backup_plan(project=source.parent.parent, runtime_root=runtime,
        output_dir=tmp_path / "backups", include_skills=False, include_automations=False)
    if removed:
        with pytest.raises(ValueError, match="configuration source absence differs from copied bytes"):
            execute_state_backup_plan(plan)
        assert not list((tmp_path / "backups").iterdir())
    else:
        payload = execute_state_backup_plan(plan)
        with tarfile.open(payload["archive_path"]) as archive:
            member = archive.getmember("runtime-root/machine/configuration.json")
            assert member.islnk() if link_kind == "hardlink" else member.issym()
            checkpoint = json.load(archive.extractfile("configuration-backup.json"))
            assert json.load(archive.extractfile(member)) == checkpoint["data"]["machine_configuration"]
        assert any(row["archive_path"] == "runtime-root/earlier-machine.json"
            for row in payload["execution"]["configuration_capture"]["source_revisions"])


@pytest.mark.skipif(os.name == "nt", reason="native Windows symlink privilege is not qualified")
@pytest.mark.parametrize("restore_alias", [False, True], ids=["repointed", "aba"])
def test_machine_configuration_alias_route_must_match_saved_target(
    environment, tmp_path, monkeypatch, restore_alias,
):
    from loopx.capabilities import configuration_backup

    source, runtime, _, _ = environment
    machine = runtime / "machine/configuration.json"
    first, second = runtime / "first-machine.json", runtime / "second-machine.json"
    machine.rename(first)
    changed = json.loads(first.read_text())
    changed["namespaces"]["goal_storage"]["new_goal_provider"] = "file"
    second.write_text(json.dumps(changed))
    machine.symlink_to("../first-machine.json")
    original_capture = configuration_backup.capture_configuration_backup

    def after_copy(**kwargs):
        machine.unlink()
        machine.symlink_to("../second-machine.json")
        checkpoint = original_capture(**kwargs)
        if restore_alias:
            machine.unlink()
            machine.symlink_to("../first-machine.json")
        return checkpoint

    monkeypatch.setattr(configuration_backup, "capture_configuration_backup", after_copy)
    plan = build_state_backup_plan(project=source.parent.parent, runtime_root=runtime,
        output_dir=tmp_path / "backups", include_skills=False, include_automations=False)
    with pytest.raises(ValueError, match="configuration source differs from copied bytes"):
        execute_state_backup_plan(plan)
    assert not list((tmp_path / "backups").iterdir())


@pytest.mark.parametrize("invalid", ["directory", "malformed", "broken-link"])
def test_full_backup_does_not_treat_unreadable_machine_source_as_absent(
    environment, tmp_path, invalid,
):
    source, runtime, _, _ = environment
    path = runtime / "machine/configuration.json"
    path.unlink()
    if invalid == "directory":
        path.mkdir()
    elif invalid == "malformed":
        path.write_bytes(b"not a configuration envelope")
    else:
        if os.name == "nt":
            pytest.skip("native Windows symlink privilege is not qualified")
        path.symlink_to("unavailable-configuration.json")
    plan = build_state_backup_plan(project=source.parent.parent, runtime_root=runtime,
        output_dir=tmp_path / "backups", include_skills=False, include_automations=False)
    with pytest.raises((ValueError, OSError)):
        execute_state_backup_plan(plan)
    assert not list((tmp_path / "backups").iterdir())


def test_full_backup_refuses_registry_absence_after_its_bytes_were_copied(
    environment, tmp_path, monkeypatch,
):
    from loopx.capabilities import configuration_backup

    source, runtime, registry, _ = environment
    original_capture = configuration_backup.capture_configuration_backup

    def after_copy(**kwargs):
        registry.unlink()
        return original_capture(**kwargs)

    monkeypatch.setattr(configuration_backup, "capture_configuration_backup", after_copy)
    plan = build_state_backup_plan(project=source.parent.parent, runtime_root=runtime,
        output_dir=tmp_path / "backups", include_skills=False, include_automations=False)
    with pytest.raises(ValueError, match="configuration source absence differs from copied bytes"):
        execute_state_backup_plan(plan)
    assert not list((tmp_path / "backups").iterdir())


def test_full_backup_refuses_a_changed_routed_registry_source(environment, tmp_path, monkeypatch):
    source, runtime, _, _ = environment
    original_add = tarfile.TarFile.add
    changed = []

    def after_copy(tar, path, *args, **kwargs):
        result = original_add(tar, path, *args, **kwargs)
        if str(kwargs.get("arcname", "")).endswith("/source-registry/registry.json"):
            captured = json.loads(source.read_text())
            captured["goals"][0]["description"] = "Changed canonical source"
            source.write_text(json.dumps(captured))
            changed.append(True)
        return result

    monkeypatch.setattr(tarfile.TarFile, "add", after_copy)
    plan = build_state_backup_plan(project=source.parent.parent, runtime_root=runtime,
        output_dir=tmp_path / "backups", include_skills=False, include_automations=False)
    with pytest.raises(ValueError, match="configuration source differs from copied bytes"):
        execute_state_backup_plan(plan)
    assert changed == [True]
    assert not list((tmp_path / "backups").iterdir())


def test_full_archive_explicit_configuration_cohort_retains_missing_source_inventory(environment, tmp_path):
    source, runtime, registry, goal = environment
    original = json.loads(registry.read_text())
    original["goals"].append({"id": "historical", "repo": str(tmp_path / "removed-project"),
        "state_file": "original.md", "source_registry": str(tmp_path / "removed-registry.json"),
        "control_plane": {"stale_projection": True}})
    registry.write_text(json.dumps(original))
    original_bytes = registry.read_bytes()
    common = ("--runtime-root", runtime, "--registry", registry, "backup-state", "--project",
        source.parent.parent, "--no-skills", "--no-automations", "--execute")
    # Default capture cannot invent source-owned configuration from a projection.
    code, failed = cli(*common, "--output-dir", tmp_path / "strict")
    assert code == 1 and "source_registry is missing" in failed["error"]
    assert not list((tmp_path / "strict").glob("*.tar.gz"))
    code, saved = cli(*common, "--output-dir", tmp_path / "selected",
        "--configuration-goal-id", "fixture")
    assert code == 0, saved
    assert saved["configuration_goal_ids"] == ["fixture"]
    assert any(row["key"] == "registry_source_registry:historical" for row in saved["missing"])
    with tarfile.open(saved["archive_path"]) as archive:
        checkpoint = json.load(archive.extractfile("configuration-backup.json"))
        assert checkpoint["data"]["goals"] == [{"goal_id": "fixture", "goal_configuration": goal}]
        assert archive.extractfile("runtime-root/registry.global.json").read() == original_bytes
    assert saved["execution"]["configuration_backup"]["readback_verified"] is True
    assert registry.read_bytes() == original_bytes
    code, rejected = cli(*common, "--output-dir", tmp_path / "still-strict",
        "--configuration-goal-id", "historical")
    assert code == 1 and "source_registry is missing" in rejected["error"]


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
    archive = Path(plan["archive_path"]).with_name("previous.tar.gz")
    manifest = Path(plan["manifest_path"]).with_name("previous.manifest.json")
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
        checkpoint = runtime / "backups/configuration" / exported["sha256"]
        assert not checkpoint.exists()
        assert post("restore", {**body, "expected_sha256": "changed", "execute": True})[0] == 400
        assert not checkpoint.exists()
        status, restored = post("restore", {**body, "execute": True})
        assert status == 200 and restored["status"] == "restored"
        assert restored["checkpoint_ref"] == f"backups/configuration/{exported['sha256']}"
        saved = (checkpoint / "configuration-backup.json").read_bytes()
        assert json.loads(saved.decode("utf-8")) == exported["backup"]
        assert "完整".encode("utf-8") in saved
        assert post("restore", {**body, "execute": True})[0] == 400
        assert (checkpoint / "configuration-backup.json").read_bytes() == saved
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_browser_backup_rejects_selected_environment_without_loopx(tmp_path):
    selected = tmp_path / "empty-environment"
    # uv's macOS Python resolves libpython relative to its real executable.
    # A copied binary is not a runnable empty environment on that host.
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(selected)
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
