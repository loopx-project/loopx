"""Update intent, progress and detached observation without live user state."""

import argparse
import json
import os
import subprocess
import sys
import time
import zipfile
import zipimport

import pytest

from loopx.cli_commands.support_control_update_progress import UpdateProgress
from loopx.cli_commands import update_operation as operations
from loopx.self_update import build_update_plan, _source_config, _installer_env_for_source
from test_self_update_runtime_activation import doctor_payload


def test_installed_channel_and_pinned_ref_survive_both_update_entrypoints(monkeypatch):
    for name in ("LOOPX_REF", "LOOPX_REPO", "LOOPX_ARCHIVE_URL"):
        monkeypatch.delenv(name, raising=False)
    for ref in ("main", "stable", "f" * 40):
        plan = build_update_plan(doctor_payload=doctor_payload(source_ref=ref))
        assert plan["source"]["ref"] == ref
        assert plan["source"]["ref_source"] == "installed_manifest"
        assert plan["source"]["distribution"] == "runtime"
    assert _source_config(repo=None, ref=None, archive_url=None)["ref"] == "stable"


def test_runtime_check_uses_one_qualified_version_and_revision(monkeypatch):
    from loopx.cli_commands import update_source
    def no_branch_probe(*_args):
        raise AssertionError("The mutable branch version must not describe a qualified artifact")
    monkeypatch.setattr("loopx.self_update._source_version_check", no_branch_probe)
    monkeypatch.setattr(update_source, "resolve_artifact", lambda *_: {
        "source_revision": "2" * 40, "package_version": "1.2.3"})
    monkeypatch.setattr(update_source, "target_direction", lambda *_: "ahead")
    plan = build_update_plan(action="check", doctor_payload=doctor_payload(), resolve_remote=True)
    assert plan["source"]["resolved_revision"] == "2" * 40
    assert plan["source_version_check"]["version"] == "1.2.3"


@pytest.mark.parametrize("direction,accepted", [("ahead", True), ("identical", True), ("behind", False), ("diverged", False), ("unknown", False)])
def test_target_is_resolved_before_any_mutation_and_downgrade_needs_intent(monkeypatch, direction, accepted):
    from loopx.cli_commands import update_source
    artifact = {"source_revision": "2" * 40, "package_version": "1.0.0"}
    monkeypatch.setattr(update_source, "resolve_artifact", lambda *_: artifact)
    monkeypatch.setattr(update_source, "target_direction", lambda *_: direction)
    plan = build_update_plan(action="apply", doctor_payload=doctor_payload(), resolve_remote=True)
    assert plan["source"]["resolved_revision"] == artifact["source_revision"]
    assert plan["plan"]["apply_supported"] is accepted
    override = build_update_plan(action="apply", doctor_payload=doctor_payload(), resolve_remote=True, allow_downgrade=True)
    assert override["plan"]["apply_supported"] is True


def test_installer_uses_frozen_revision_but_persists_channel_and_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://operator-proxy.invalid:8080")
    source = _source_config(repo=None, ref="main", archive_url=None, build_from_source=True)
    source["resolved_revision"] = "b" * 40
    env = _installer_env_for_source(source)
    assert env["LOOPX_REF"] == "b" * 40
    assert env["LOOPX_UPDATE_CHANNEL"] == "main"
    assert env["HTTPS_PROXY"] == "http://operator-proxy.invalid:8080"
    assert env["LOOPX_BUILD_FROM_SOURCE"] == "1"


def test_json_stdout_is_final_only_and_heartbeat_does_not_invent_progress(tmp_path, capsys):
    progress = UpdateProgress(json_output=True, status_path=tmp_path / "status.json")
    try:
        progress.report({"stage": "download", "received": 1024, "total": None})
        progress.changed -= 11
        progress._write()
        captured = capsys.readouterr()
        assert captured.out == ""
        events = [json.loads(line) for line in captured.err.splitlines()]
        assert events[-1]["total"] is None and events[-1]["stalled"] is True
        assert events[0]["operation_id"] == events[-1]["operation_id"]
        assert events[-1]["last_progress_at"] < events[-1]["updated_at"] - 10
        assert json.loads((tmp_path / "status.json").read_text())["received"] == 1024
    finally:
        progress.stop()


def test_active_worker_is_reattached_and_status_never_starts_an_install(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPX_UPDATE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LOOPX_BIN_DIR", str(tmp_path / "bin"))
    root = operations.operation_root()
    operation_id = "c" * 32
    folder = root / operation_id
    folder.mkdir()
    operations.atomic_json(root / f"{operations.installation_key()}.active.json", {"operation_id": operation_id})
    operations.atomic_json(folder / "status.json", {"operation_id": operation_id,
        "status": "running", "pid": os.getpid(), "updated_at": time.time()})
    def no_spawn(*_args, **_kwargs):
        raise AssertionError("A second installation must not start")
    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    result = operations.start_or_observe(argparse.Namespace(), tmp_path / "registry.json")
    assert result["operation_id"] == operation_id and result["reattached"] is True
    assert operations.read_operation()["status"] == "running"
    operations.atomic_json(folder / "result.json", {"ok": False, "error": "activation failed"})
    assert operations.read_operation()["status"] == "failed"


def test_real_detached_worker_finishes_after_its_observer_exits(tmp_path):
    env = dict(os.environ, LOOPX_UPDATE_STATE_DIR=str(tmp_path / "state"),
        LOOPX_BIN_DIR=str(tmp_path / "bin"), LOOPX_USAGE_PING="0")
    # An unsupported source checkout is a real negative update journey: the
    # owning CLI resolves it and records failure, without running an installer.
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(tmp_path / "registry.json"),
        "--format", "json", "update", "apply", "--background", "--source", "--ref", "main"]
    started = subprocess.run(command, env=env, text=True, capture_output=True, timeout=30, check=True)
    operation_id = json.loads(started.stdout)["operation_id"]
    assert len(operation_id) == 32
    for _ in range(80):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
            "update", "status", "--operation-id", operation_id], env=env, text=True, capture_output=True, timeout=15)
        payload = json.loads(result.stdout)
        if payload["status"] not in {"starting", "running"}:
            break
        time.sleep(.1)
    assert payload["status"] == "failed", payload
    assert payload["result"]["changes_applied"] is False
    assert payload["result"]["plan"]["apply_supported"] is False


@pytest.mark.parametrize("switched", [True, False])
def test_material_failure_reports_an_observed_switch_but_never_invents_one(monkeypatch, switched):
    from loopx.self_update import execute_update_plan
    plan = build_update_plan(action="apply", doctor_payload=doctor_payload(), build_from_source=True)
    plan["source"]["resolved_revision"] = "1" * 40
    readback = {"install_freshness": {"manifest_source_git_commit": "1" * 40,
        "release_id": "new-release" if switched else "fixture"}}
    monkeypatch.setenv("LOOPX_PYTHON", sys.executable)
    monkeypatch.setattr("loopx.self_update.run_archive_installer", lambda *_a, **_kw: (
        subprocess.CompletedProcess([], 1, "", "host material failed"), {}))
    monkeypatch.setattr("loopx.self_update.subprocess.run", lambda *_a, **_kw:
        subprocess.CompletedProcess([], 0, json.dumps(readback), ""))
    monkeypatch.setattr("loopx.runtime_activation.restart_managed_loopx_services", lambda: [])
    result = execute_update_plan(plan)
    assert result["ok"] is False
    assert result["changes_applied"] is switched
    assert result["execution"]["activation_observed"] is switched


def test_windows_console_guard_runs_in_original_process_before_detaching(tmp_path, monkeypatch):
    from loopx.cli_commands import support_control_update as command
    archive = tmp_path / "loopx.exe"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("__main__.py", "")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys.modules["__main__"], "__loader__", zipimport.zipimporter(str(archive)))
    monkeypatch.delenv("LOOPX_UPDATE_WORKER", raising=False)
    monkeypatch.setattr(command, "build_update_plan", lambda **_kw: {
        "ok": False, "plan": {"apply_supported": False}, "error": "original_console_refusal"})
    def no_detach(*_args, **_kwargs):
        raise AssertionError("Detaching would hide the locked console from its existing guard")
    monkeypatch.setattr(operations, "start_or_observe", no_detach)
    parser = argparse.ArgumentParser()
    command.register_update_command(parser.add_subparsers(dest="command"), lambda _parser: None)
    args = parser.parse_args(["update", "apply", "--background"])
    args.runtime_root = None
    values = []
    code = command.handle_update_command(args, registry_path=tmp_path / "registry.json",
        registry_was_supplied=True, print_payload=lambda value, *_: values.append(value),
        output_format=lambda _: "json")
    assert code == 1
    assert values[0]["error"] == "original_console_refusal"
