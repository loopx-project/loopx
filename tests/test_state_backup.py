from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setenv("TMPDIR", str(temporary))
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    yield
    # Stop only the real runtime launched in this test's private directory.
    effect_runtime.restart_effect_runtime()


def backup_plan(root: Path, backup_id: str = "fixture"):
    return build_state_backup_plan(
        project=root / "project", runtime_root=root / "runtime",
        output_dir=root / "backups", backup_id=backup_id,
        include_automations=False, include_skills=False,
        include_registry_projects=False,
    )


def restore(payload, root: Path):
    with tarfile.open(payload["archive_path"]) as archive:
        archive.extractall(root, filter="data")
        return set(archive.getnames())


@pytest.mark.parametrize("name", ["authority.sqlite", "history.data"])
def test_sqlite_snapshot_survives_checkpoint_after_database_member(tmp_path, monkeypatch, name):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    database = runtime / name
    metadata = {"unknown": {"enabled": False, "nullable": None}, "evidence": ["样例" * 4096]}
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE commitments(id INTEGER PRIMARY KEY, metadata TEXT)")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO commitments VALUES(1, ?)", (json.dumps(metadata),))
        writer.commit()
        expected = writer.execute("SELECT * FROM commitments").fetchall()
        original = tarfile.TarFile.add
        checkpointed = []

        def checkpoint_after_member(archive, source, *args, **kwargs):
            result = original(archive, source, *args, **kwargs)
            if kwargs.get("arcname") == f"runtime-root/{name}":
                writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                checkpointed.append(True)
            return result

        monkeypatch.setattr(tarfile.TarFile, "add", checkpoint_after_member)
        payload = execute_state_backup_plan(backup_plan(tmp_path))
        restored = tmp_path / "restored"
        names = restore(payload, restored)
        with sqlite3.connect(restored / "runtime-root" / name) as copy:
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert copy.execute("SELECT * FROM commitments").fetchall() == expected
        assert checkpointed == [True]
        assert f"runtime-root/{name}-wal" not in names
        assert f"runtime-root/{name}-shm" not in names
        assert writer.execute("SELECT * FROM commitments").fetchall() == expected


def test_duplicate_targets_reuse_snapshot_and_preserve_ordinary_files(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    database = runtime / "history.data"
    (runtime / "notes-wal").write_text("ordinary file")
    (runtime / "not-a-database.sqlite").write_text("ordinary text")
    (runtime / "database-link").symlink_to("history.data")
    with sqlite3.connect(database) as writer:
        writer.execute("CREATE TABLE facts(value TEXT)")
        writer.execute("INSERT INTO facts VALUES('preserved')")
        writer.commit()
        plan = backup_plan(tmp_path)
        plan["included"].append({"source_path": str(database), "archive_path": "exact/database"})
        payload = execute_state_backup_plan(plan)
    restored = tmp_path / "restored"
    names = restore(payload, restored)
    records = payload["execution"]["sqlite_snapshots"]
    assert len(records) == 1
    assert records[0]["archive_paths"] == ["runtime-root/history.data", "exact/database"]
    for member in records[0]["archive_paths"]:
        data = (restored / member).read_bytes()
        assert hashlib.sha256(data).hexdigest() == records[0]["snapshot_sha256"]
        assert len(data) == records[0]["snapshot_size_bytes"]
    assert (restored / "runtime-root/database-link").is_symlink()
    assert "runtime-root/notes-wal" in names
    assert (restored / "runtime-root/not-a-database.sqlite").read_text() == "ordinary text"
    assert (Path(payload["archive_path"]).stat().st_mode & 0o777) == 0o600
    assert (Path(payload["manifest_path"]).stat().st_mode & 0o777) == 0o600


def test_backup_does_not_walk_or_archive_junction_targets(tmp_path, monkeypatch):
    project = tmp_path / "project"
    state = project / ".loopx"
    redirected = state / "redirect"
    redirected.mkdir(parents=True)
    outside_secret = redirected / "secret.txt"
    outside_secret.write_text("outside backup scope")

    def is_junction(path: Path) -> bool:
        return path == redirected

    monkeypatch.setattr(Path, "is_junction", is_junction, raising=False)

    plan = backup_plan(tmp_path)
    state_target = next(
        item for item in plan["included"] if item["archive_path"] == "project/.loopx"
    )
    assert state_target["stats"]["files"] == 0
    assert state_target["stats"]["symlinks"] == 1

    payload = execute_state_backup_plan(plan)
    with tarfile.open(payload["archive_path"]) as archive:
        assert "project/.loopx/redirect/secret.txt" not in archive.getnames()


def test_invalid_sqlite_fails_without_replacing_previous_backup(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "history.data").write_bytes(b"SQLite format 3\x00" + b"invalid" * 100)
    plan = backup_plan(tmp_path)
    archive, manifest = Path(plan["archive_path"]), Path(plan["manifest_path"])
    archive.parent.mkdir()
    archive.write_bytes(b"previous verified archive")
    manifest.write_bytes(b"previous manifest")
    with pytest.raises(RuntimeError):
        execute_state_backup_plan(plan)
    assert archive.read_bytes() == b"previous verified archive"
    assert manifest.read_bytes() == b"previous manifest"
    assert sorted(path.name for path in archive.parent.iterdir()) == sorted([archive.name, manifest.name])


def test_real_cli_backs_up_committed_wal_with_qualified_ts_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    database = runtime / "authority.sqlite"
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE receipts(operation TEXT PRIMARY KEY, result TEXT)")
        writer.execute("INSERT INTO receipts VALUES('original-operation', '{\"unknown\":null}')")
        writer.commit()
        expected = writer.execute("SELECT * FROM receipts").fetchall()
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json", "--runtime-root", str(runtime),
             "backup-state", "--project", str(tmp_path / "project"), "--output-dir", str(tmp_path / "backups"),
             "--no-automations", "--no-skills", "--current-project-only", "--execute"],
            env={**os.environ, "LOOPX_USAGE_PING": "0"}, capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        record = payload["execution"]["sqlite_snapshots"][0]
        assert record["runtime_identity"]["synchronous_statement_finalization"] is True
        assert record["runtime_identity"]["node_version"].startswith("v")
        restore(payload, tmp_path / "restored")
        with sqlite3.connect(tmp_path / "restored/runtime-root/authority.sqlite") as copy:
            assert copy.execute("SELECT * FROM receipts").fetchall() == expected
