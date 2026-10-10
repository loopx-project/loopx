"""Backup witnesses describe archived bytes, never a later source reread."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tarfile

import pytest

from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan


def fixture(root: Path):
    project, runtime = root / "project", root / "runtime"
    state = project / ".codex/goals/example/ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_bytes(b"## Agent Todo\n- [ ] Current work\n## Agent Todo Archive\n"
                      b"- [x] Unreferenced history\n  <!-- future_metadata=null -->\n")
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "example", "repo": str(project), "state_file": str(state),
         "future_configuration": {"disabled": False, "unset": None}}]}))
    historical = runtime / "goals/example/task-leases/retained.json"
    historical.parent.mkdir(parents=True)
    historical.write_bytes(b'{"status":"released","future":{"null":null,"flag":false}}\n')
    receipt = runtime / "goals/example/receipts/original.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(b'{"operation_id":"original","unknown":[false,null]}\n')
    (runtime / "receipt-link").symlink_to("goals/example/receipts/original.json")
    os.link(receipt, runtime / "receipt-hardlink")
    return project, runtime, state, registry


def plan(root: Path):
    project, runtime, state, registry = fixture(root)
    return build_state_backup_plan(project=project, runtime_root=runtime,
        output_dir=root / "backups", backup_id="fixture", include_automations=False,
        include_skills=False, include_registry_projects=False, registry_path=registry), state


def assert_members(payload):
    members = payload["execution"]["file_members"]
    by_path = {row["archive_path"]: row for row in members}
    assert len(by_path) == len(members)
    with tarfile.open(payload["archive_path"]) as archive:
        expected = {row.name for row in archive if row.isfile() and row.name != "manifest.json"}
        assert set(by_path) == expected
        for path, row in by_path.items():
            data = archive.extractfile(path).read()
            assert row == {"archive_path": path, "size_bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}
        internal = json.load(archive.extractfile("manifest.json"))
        assert internal["execution"]["file_members"] == members
    external = json.loads(Path(payload["manifest_path"]).read_text())
    assert external["execution"]["file_members"] == members


@pytest.mark.parametrize("with_sqlite", [False, True])
def test_real_cli_inventory_binds_raw_history_and_generated_configuration(tmp_path, with_sqlite):
    project, runtime, state, registry = fixture(tmp_path)
    original = state.read_bytes()
    if with_sqlite:
        with sqlite3.connect(runtime / "retained.data") as database:
            database.execute("CREATE TABLE receipts(value TEXT)")
            database.execute("INSERT INTO receipts VALUES(?)", ('{"future":[false,null]}',))
    result = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "backup-state", "--project", str(project),
        "--output-dir", str(tmp_path / "backups"), "--current-project-only", "--no-automations",
        "--no-skills", "--execute"], env={**os.environ, "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert_members(payload)
    restored = tmp_path / "restored"
    with tarfile.open(payload["archive_path"]) as archive:
        archive.extractall(restored, filter="data")
    assert (restored / "project/.codex/goals/example/ACTIVE_GOAL_STATE.md").read_bytes() == original
    assert (restored / "runtime-root/receipt-link").is_symlink()
    assert (restored / "runtime-root/receipt-hardlink").read_bytes() == (
        runtime / "goals/example/receipts/original.json").read_bytes()
    if with_sqlite:
        with sqlite3.connect(restored / "runtime-root/retained.data") as database:
            assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert database.execute("SELECT value FROM receipts").fetchone()[0] == '{"future":[false,null]}'
    assert state.read_bytes() == original


def test_member_digest_uses_the_copied_stream_not_a_later_source_reread(tmp_path, monkeypatch):
    payload, state = plan(tmp_path)
    original = state.read_bytes()
    addfile = tarfile.TarFile.addfile

    def change_after_copy(archive, info, fileobj=None):
        result = addfile(archive, info, fileobj)
        if info.name == "project/.codex/goals/example/ACTIVE_GOAL_STATE.md":
            state.write_bytes(b"later source contents\n")
        return result

    monkeypatch.setattr(tarfile.TarFile, "addfile", change_after_copy)
    completed = execute_state_backup_plan(payload)
    assert_members(completed)
    row = next(row for row in completed["execution"]["file_members"]
               if row["archive_path"] == "project/.codex/goals/example/ACTIVE_GOAL_STATE.md")
    assert row["sha256"] == hashlib.sha256(original).hexdigest()
    assert row["sha256"] != hashlib.sha256(state.read_bytes()).hexdigest()


def test_short_source_read_cannot_publish_partial_inventory_or_replace_backup(tmp_path, monkeypatch):
    payload, state = plan(tmp_path)
    output = Path(payload["output_dir"])
    output.mkdir()
    archive, manifest = Path(payload["archive_path"]), Path(payload["manifest_path"])
    archive.write_bytes(b"previous archive")
    manifest.write_bytes(b"previous manifest")
    addfile = tarfile.TarFile.addfile

    def truncate_before_copy(archive, info, fileobj=None):
        if info.name == "project/.codex/goals/example/ACTIVE_GOAL_STATE.md":
            state.write_bytes(b"")
        return addfile(archive, info, fileobj)

    monkeypatch.setattr(tarfile.TarFile, "addfile", truncate_before_copy)
    with pytest.raises(OSError, match="unexpected end of data"):
        execute_state_backup_plan(payload)
    assert archive.read_bytes() == b"previous archive"
    assert manifest.read_bytes() == b"previous manifest"
    assert sorted(path.name for path in output.iterdir()) == sorted([archive.name, manifest.name])
