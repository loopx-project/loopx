"""Exercise installer recovery against an isolated KunlunCode command fixture."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import venv
from pathlib import Path

import pytest

from loopx.kunluncode_goal_mode import cli


FAKE_KUNLUN = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
path = Path(os.environ["KUNLUN_FIXTURE_STATE"])
state = json.loads(path.read_text())
mode = os.environ["KUNLUN_FIXTURE_MODE"]
args = sys.argv[1:]
def save():
    path.write_text(json.dumps(state))
if args[:2] == ["config", "get"]:
    if mode == "change_before_remove":
        counter = path.with_name("config-reads")
        reads = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(reads))
        if reads == 2:
            state["entries"] = [entry for entry in state["entries"] if entry.get("name") != "loopx-kunluncode"]
            state["entries"].append({"name": "loopx-kunluncode", "command": "/fixture/concurrent", "args": []})
            save()
    if mode == "race_before_restore" and "add_new" in state["events"] and not path.with_name("race-injected").exists():
        snapshot = list(state["entries"])
        state["entries"].append({"name": "loopx-kunluncode", "command": "/fixture/concurrent", "args": []})
        path.with_name("race-injected").touch()
        save()
        print(json.dumps(snapshot))
    elif mode == "invalid_config":
        print("{}")
    elif mode == "status_unavailable" and "add_new" in state["events"]:
        sys.exit(7)
    else:
        print(json.dumps(state["entries"]))
    sys.exit(0)
if args[:2] == ["mcp", "remove"]:
    state["events"].append("remove")
    if mode != "remove_intact_timeout":
        state["entries"] = [entry for entry in state["entries"] if entry.get("name") != args[2]]
    save()
    if mode in ("remove_intact_timeout", "remove_absent_timeout"):
        time.sleep(10)
    sys.exit(0)
if args[:2] == ["mcp", "add"]:
    command = args[args.index("--command") + 1]
    arguments = args[args.index("--args") + 1:] if "--args" in args else []
    is_new = command == os.environ["KUNLUN_FIXTURE_NEW"]
    state["events"].append("add_new" if is_new else "add_old")
    others = [entry for entry in state["entries"] if entry.get("name") != args[2]]
    if is_new and mode == "add_foreign_timeout":
        state["entries"] = others + [{"name": args[2], "command": "/fixture/other", "args": []}]
    elif (not is_new and mode != "failure_restore_no_write") or (is_new and mode in ("normal", "add_written_timeout", "add_written_failure")):
        state["entries"] = others + [{"name": args[2], "command": command, "args": arguments}]
    save()
    if (is_new and mode in ("add_written_timeout", "add_absent_timeout", "add_foreign_timeout", "status_unavailable", "race_before_restore")) or (not is_new and mode == "failure_restore_written_timeout"):
        time.sleep(10)
    if is_new and mode in ("add_written_failure", "add_absent_failure", "failure_restore_no_write", "failure_restore_written_timeout"):
        sys.exit(7)
    sys.exit(0)
raise SystemExit("unexpected fixture command")
'''


@pytest.fixture
def kunlun_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if os.name == "nt":
        pytest.skip("the isolated executable fixture requires POSIX scripts")
    tool = tmp_path / "kunluncode"
    tool.write_text(FAKE_KUNLUN, encoding="utf-8")
    tool.chmod(0o755)
    interpreter = tmp_path / "adapter-python"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    state = tmp_path / "state.json"
    monkeypatch.setattr(cli, "DEFAULT_MCP_VENV", tmp_path / "managed" / ".venv")
    previous = {"name": cli.MCP_SERVER_NAME, "command": "/fixture/old", "args": ["--serve"]}
    unrelated = {"name": "unrelated-service", "command": "/fixture/unrelated", "args": ["--keep"]}
    state.write_text(json.dumps({"entries": [previous, unrelated], "events": []}), encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("KUNLUN_FIXTURE_STATE", str(state))
    monkeypatch.setenv("KUNLUN_FIXTURE_NEW", str(interpreter))
    real_run = cli._run

    def bounded_run(command, **kwargs):
        if command[1:3] in (["mcp", "add"], ["mcp", "remove"]):
            kwargs["timeout"] = 0.8
        return real_run(command, **kwargs)

    monkeypatch.setattr(cli, "_run", bounded_run)
    return interpreter, state, previous, unrelated


@pytest.mark.parametrize(
    ("mode", "expected", "backup_retained"),
    [
        ("add_written_timeout", "new", True),
        ("add_absent_timeout", "missing", True),
        ("add_foreign_timeout", "other", True),
        ("status_unavailable", "missing", True),
        ("remove_intact_timeout", "old", True),
        ("remove_absent_timeout", "missing", True),
    ],
)
def test_timeout_checks_state_before_recovery(
    mode, expected, backup_retained, kunlun_fixture, monkeypatch: pytest.MonkeyPatch
):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", mode)
    with pytest.raises(RuntimeError, match="timed out") as error:
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [item for item in state["entries"] if item.get("name") == unrelated["name"]] == [unrelated]
    entries = [item for item in state["entries"] if item.get("name") == cli.MCP_SERVER_NAME]
    if expected == "old":
        assert entries == [previous]
    elif expected == "new":
        assert entries == [{"name": cli.MCP_SERVER_NAME, "command": str(interpreter), "args": [str(cli.MCP_SCRIPT)]}]
        assert "requested entry is present" in str(error.value)
    elif expected == "other":
        assert entries == [{"name": cli.MCP_SERVER_NAME, "command": "/fixture/other", "args": []}]
        assert "another entry is present" in str(error.value)
    else:
        assert entries == []
        assert ("could not be verified" if mode == "status_unavailable" else "no entry is present") in str(error.value)
    assert "add_old" not in state["events"]
    backups = list((cli.DEFAULT_MCP_VENV.parent / "recovery").glob("mcp-entry-*.json"))
    assert bool(backups) is backup_retained
    if backup_retained:
        assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == previous
        assert str(backups[0]) in str(error.value)


@pytest.mark.parametrize("mode", ["add_written_timeout", "add_absent_timeout"])
def test_timeout_without_previous_entry_does_not_blindly_retry(mode, kunlun_fixture, monkeypatch):
    interpreter, state_path, _, unrelated = kunlun_fixture
    state_path.write_text(json.dumps({"entries": [unrelated], "events": []}), encoding="utf-8")
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", mode)
    with pytest.raises(RuntimeError, match="timed out"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=False)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert unrelated in state["entries"]
    assert state["events"] == ["add_new"]
    targets = [item for item in state["entries"] if item.get("name") == cli.MCP_SERVER_NAME]
    assert len(targets) == (1 if mode == "add_written_timeout" else 0)


@pytest.mark.parametrize("invalid", ["extra_metadata", "duplicate", "invalid_config"])
def test_unrestorable_or_ambiguous_registration_is_never_removed(
    invalid, kunlun_fixture, monkeypatch: pytest.MonkeyPatch
):
    interpreter, state_path, previous, _ = kunlun_fixture
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if invalid == "extra_metadata":
        state["entries"][0]["env"] = {"TOKEN": "fixture"}
    elif invalid == "duplicate":
        state["entries"].append(dict(previous))
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", invalid)
    with pytest.raises(RuntimeError):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after == state


@pytest.mark.parametrize(("mode", "expected"), [("add_written_failure", "new"), ("add_absent_failure", "old")])
def test_failed_add_rechecks_state_before_restoring(mode, expected, kunlun_fixture, monkeypatch):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", mode)
    with pytest.raises(RuntimeError, match="registration failed"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert unrelated in state["entries"]
    entries = [item for item in state["entries"] if item.get("name") == cli.MCP_SERVER_NAME]
    if expected == "old":
        assert entries == [previous]
        assert "add_old" in state["events"]
    else:
        assert entries == [{"name": cli.MCP_SERVER_NAME, "command": str(interpreter), "args": [str(cli.MCP_SCRIPT)]}]
        assert "add_old" not in state["events"]


def test_restore_success_without_readback_does_not_claim_recovery(kunlun_fixture, monkeypatch):
    interpreter, state_path, _, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "failure_restore_no_write")
    with pytest.raises(RuntimeError, match="not verified"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    assert json.loads(state_path.read_text(encoding="utf-8"))["entries"] == [unrelated]


def test_normal_failure_restoration_timeout_is_checked_before_reporting(kunlun_fixture, monkeypatch):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "failure_restore_written_timeout")
    with pytest.raises(RuntimeError, match="previous entry is present, but restoration timed out"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert previous in state["entries"] and unrelated in state["entries"]
    backup, = (cli.DEFAULT_MCP_VENV.parent / "recovery").glob("mcp-entry-*.json")
    assert json.loads(backup.read_text(encoding="utf-8")) == previous


def test_timeout_backup_is_private_and_does_not_leak_arguments(kunlun_fixture, monkeypatch):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    previous["args"] = ["--token", "fixture-secret"]
    state_path.write_text(json.dumps({"entries": [previous, unrelated], "events": []}), encoding="utf-8")
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "add_absent_timeout")
    with pytest.raises(RuntimeError, match="previous entry saved at") as error:
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    backup, = (cli.DEFAULT_MCP_VENV.parent / "recovery").glob("mcp-entry-*.json")
    assert json.loads(backup.read_text(encoding="utf-8")) == previous
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert "fixture-secret" not in str(error.value)


def test_successful_replacement_removes_backup_only_after_readback(kunlun_fixture, monkeypatch):
    interpreter, state_path, _, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "normal")
    assert cli.install_mcp(python=str(interpreter), dry_run=False, replace=True) == str(interpreter)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert unrelated in state["entries"]
    assert {"name": cli.MCP_SERVER_NAME, "command": str(interpreter), "args": [str(cli.MCP_SCRIPT)]} in state["entries"]
    assert list((cli.DEFAULT_MCP_VENV.parent / "recovery").glob("mcp-entry-*.json")) == []


def test_success_without_matching_readback_preserves_backup(kunlun_fixture, monkeypatch):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "add_success_no_write")
    with pytest.raises(RuntimeError, match="did not match readback"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    assert json.loads(state_path.read_text(encoding="utf-8"))["entries"] == [unrelated]
    backup, = (cli.DEFAULT_MCP_VENV.parent / "recovery").glob("mcp-entry-*.json")
    assert json.loads(backup.read_text(encoding="utf-8")) == previous


def test_insecure_backup_directory_prevents_removal(kunlun_fixture, monkeypatch):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    directory = cli.DEFAULT_MCP_VENV.parent / "recovery"
    directory.mkdir(parents=True, mode=0o755)
    directory.chmod(0o755)
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "normal")
    with pytest.raises(RuntimeError, match="could not be backed up"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"entries": [previous, unrelated], "events": []}


def test_symlinked_backup_directory_prevents_removal(kunlun_fixture, monkeypatch, tmp_path):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    parent = cli.DEFAULT_MCP_VENV.parent
    parent.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (parent / "recovery").symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "normal")
    with pytest.raises(RuntimeError, match="could not be backed up"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"entries": [previous, unrelated], "events": []}
    assert list(target.iterdir()) == []


def test_incomplete_backup_is_removed_and_registration_is_preserved(kunlun_fixture, monkeypatch):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "normal")

    def fail_fsync(_descriptor):
        raise OSError("synthetic sync failure")

    monkeypatch.setattr(cli.os, "fsync", fail_fsync)
    with pytest.raises(RuntimeError, match="could not be backed up"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"entries": [previous, unrelated], "events": []}
    assert list((cli.DEFAULT_MCP_VENV.parent / "recovery").iterdir()) == []


def test_duplicate_registration_is_not_removed_on_uninstall(kunlun_fixture, monkeypatch):
    interpreter, state_path, _, _ = kunlun_fixture
    entry = {"name": cli.MCP_SERVER_NAME, "command": str(interpreter), "args": [str(cli.MCP_SCRIPT)]}
    state_path.write_text(json.dumps({"entries": [entry, entry], "events": []}), encoding="utf-8")
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "duplicate")
    with pytest.raises(RuntimeError, match="multiple"):
        cli.uninstall_mcp(dry_run=False)
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"entries": [entry, entry], "events": []}


def test_changed_registration_is_not_removed(kunlun_fixture, monkeypatch):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "change_before_remove")
    with pytest.raises(RuntimeError, match="changed before removal"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert unrelated in state["entries"]
    assert {entry["command"] for entry in state["entries"]} == {"/fixture/unrelated", "/fixture/concurrent"}
    assert state["events"] == []
    backup, = (cli.DEFAULT_MCP_VENV.parent / "recovery").glob("mcp-entry-*.json")
    assert json.loads(backup.read_text(encoding="utf-8")) == previous


def test_timeout_does_not_overwrite_entry_added_after_readback(kunlun_fixture, monkeypatch):
    interpreter, state_path, _, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "race_before_restore")
    with pytest.raises(RuntimeError, match="timed out"):
        cli.install_mcp(python=str(interpreter), dry_run=False, replace=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert unrelated in state["entries"]
    assert any(entry.get("command") == "/fixture/concurrent" for entry in state["entries"])
    assert "add_old" not in state["events"]


def test_install_cli_reports_timeout_and_saved_backup_as_json(kunlun_fixture, monkeypatch, capsys):
    interpreter, state_path, previous, unrelated = kunlun_fixture
    monkeypatch.setenv("KUNLUN_FIXTURE_MODE", "add_absent_timeout")
    assert cli.main(["install", "--python", str(interpreter), "--replace"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["ok"] is False
    assert "timed out" in error["error"] and "previous entry saved at" in error["error"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert previous not in state["entries"] and unrelated in state["entries"]
    backups = list((cli.DEFAULT_MCP_VENV.parent / "recovery").glob("mcp-entry-*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == previous


@pytest.mark.parametrize("probe_state", ["match", "mismatch", "timeout"])
def test_timed_out_uv_install_verifies_the_environment_before_reporting(
    probe_state, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    venv = tmp_path / ".venv"
    python = cli._venv_python(venv)
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(cli, "DEFAULT_MCP_VENV", venv)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/fixture/uv")

    def timeout(command, **_kwargs):
        if command[0] == "/fixture/uv":
            raise subprocess.TimeoutExpired(command, 300)
        if command[0] == str(python) and probe_state == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        code = 0 if command[0] == str(python) and probe_state == "match" else 1
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(cli, "_run", timeout)
    if probe_state == "match":
        assert cli.provision_mcp_python() == str(python)
    else:
        with pytest.raises(RuntimeError, match="timed out"):
            cli.provision_mcp_python()


@pytest.mark.parametrize("interpreter_exists", [True, False])
def test_timed_out_venv_creation_reports_state_without_starting_install(
    interpreter_exists, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    venv_path = tmp_path / ".venv"
    python = cli._venv_python(venv_path)
    monkeypatch.setattr(cli, "DEFAULT_MCP_VENV", venv_path)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/fixture/uv")
    calls = []

    def timeout(command, **_kwargs):
        if command[0] != "/fixture/uv":
            return subprocess.CompletedProcess(command, 1, "", "")
        calls.append(command)
        if interpreter_exists:
            python.parent.mkdir(parents=True)
            python.touch()
        raise subprocess.TimeoutExpired(command, 120)

    monkeypatch.setattr(cli, "_run", timeout)
    with pytest.raises(RuntimeError, match="interpreter exists" if interpreter_exists else "no interpreter found"):
        cli.provision_mcp_python()
    assert len(calls) == 1 and calls[0][1] == "venv"


def test_provisioned_python_probe_verifies_versions_and_checkout(tmp_path: Path, monkeypatch):
    env_dir = tmp_path / "adapter"
    venv.create(env_dir, with_pip=False)
    python = cli._venv_python(env_dir)
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"], text=True,
    ).strip())
    loopx_package = site / "loopx"
    (loopx_package / "kunluncode_goal_mode").mkdir(parents=True)
    (loopx_package / "__init__.py").write_text(f"__version__ = {cli.LOOPX_VERSION!r}\n")
    (loopx_package / "kunluncode_goal_mode/__init__.py").write_text("")
    (loopx_package / "kunluncode_goal_mode/server.py").write_text("")
    mcp_package = site / "mcp/server"
    mcp_package.mkdir(parents=True)
    (mcp_package.parent / "__init__.py").write_text("")
    (mcp_package / "__init__.py").write_text("")
    (mcp_package / "fastmcp.py").write_text("class FastMCP: pass\n")
    loopx_meta = site / f"loopx-{cli.LOOPX_VERSION}.dist-info"
    mcp_meta = site / f"mcp-{cli.MCP_SDK_VERSION}.dist-info"
    loopx_meta.mkdir()
    mcp_meta.mkdir()
    (loopx_meta / "METADATA").write_text(f"Metadata-Version: 2.1\nName: loopx\nVersion: {cli.LOOPX_VERSION}\n")
    metadata = mcp_meta / "METADATA"
    metadata.write_text(f"Metadata-Version: 2.1\nName: mcp\nVersion: {cli.MCP_SDK_VERSION}\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "untrusted"))
    assert cli._provisioned_python_matches(python, None)
    assert cli._provisioned_python_matches(python, site)
    assert not cli._provisioned_python_matches(python, tmp_path / "different-checkout")
    checkout = tmp_path / "editable-checkout"
    checkout.mkdir()
    loopx_package.rename(checkout / "loopx")
    (site / "loopx-editable.pth").write_text(str(checkout) + "\n")
    assert cli._provisioned_python_matches(python, checkout)
    assert not cli._provisioned_python_matches(python, site)
    (loopx_meta / "METADATA").write_text("Metadata-Version: 2.1\nName: loopx\nVersion: 0.0.1\n")
    assert not cli._provisioned_python_matches(python, checkout)
    (loopx_meta / "METADATA").write_text(f"Metadata-Version: 2.1\nName: loopx\nVersion: {cli.LOOPX_VERSION}\n")
    metadata.write_text("Metadata-Version: 2.1\nName: mcp\nVersion: 0.0.1\n")
    assert not cli._provisioned_python_matches(python, None)
