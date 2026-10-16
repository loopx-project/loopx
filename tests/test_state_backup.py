from __future__ import annotations

import errno
import json
import hashlib
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import loopx.state_backup as state_backup
from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan
from loopx.control_plane.state_recovery_io import verify_state_backup
from tests.control_plane.canonical_authority_fixture import promoted_create_fixture


pytestmark = pytest.mark.usefixtures("isolated_effect_runtime")


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
    if sys.platform != "win32":
        # Windows stat reports 0o666 for every writable file and has no owner-only mode.
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
    archive = Path(plan["archive_path"]).with_name("previous.tar.gz")
    manifest = Path(plan["manifest_path"]).with_name("previous.manifest.json")
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


def test_failed_republication_preserves_the_original_verified_pair(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = runtime / "progress.txt"
    source.write_text("original accepted progress")
    plan = backup_plan(tmp_path)
    execute_state_backup_plan(plan)
    archive, manifest = Path(plan["archive_path"]), Path(plan["manifest_path"])
    original = (archive.read_bytes(), manifest.read_bytes())
    source.write_text("later unfinished work")
    replace = state_backup.os.replace

    def fail_manifest(src, dst):
        if Path(dst) == manifest:
            raise PermissionError("injected manifest publication failure")
        return replace(src, dst)

    monkeypatch.setattr(state_backup.os, "replace", fail_manifest)
    with pytest.raises(OSError):
        execute_state_backup_plan(plan)
    assert (archive.read_bytes(), manifest.read_bytes()) == original
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == json.loads(
        manifest.read_text())["execution"]["archive_sha256"]


def _backup_cli(root: Path) -> list[str]:
    return [sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
            "--runtime-root", str(root / "runtime"), "backup-state",
            "--project", str(root / "project"), "--output-dir", str(root / "backups"),
            "--backup-id", "exclusive", "--no-automations", "--no-skills",
            "--current-project-only", "--execute"]


def _strict_registry(payload: dict, writer: str = "goal_instance_v1") -> list:
    canonical = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                           sort_keys=True, separators=(",", ":")).encode()
    return [{"schema_version": "loopx_project_registry_envelope_v1",
             "minimum_writer_protocol": writer,
             "payload_sha256": "sha256:" + hashlib.sha256(canonical).hexdigest()}, payload]


def test_global_discovery_deduplicates_shared_canonical_runtimes(tmp_path):
    runtime, source_runtime = tmp_path / "runtime", tmp_path / "canonical-runtime"
    runtime.mkdir()
    source_runtime.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    state = project / "current.md"
    state.write_text("Original unfinished commitments")
    goals = [{"id": goal_id, "repo": str(project), "state_file": "current.md"}
             for goal_id in ("goal-a", "goal-b")]
    source = tmp_path / "canonical.json"
    source.write_text(json.dumps({"common_runtime_root": str(source_runtime), "goals": goals}))
    (runtime / "registry.global.json").write_text(json.dumps({"registry_role": "global-local",
        "common_runtime_root": str(runtime), "goals": [{**goal, "source_registry": "../canonical.json"} for goal in goals]}))
    plan = build_state_backup_plan(project=project, runtime_root=runtime, output_dir=tmp_path / "backups",
        include_automations=False, include_skills=False)
    roots = plan["registry_discovery"]["goal_runtime_roots"]
    assert roots["goal-a"] == roots["goal-b"] != "runtime-root"
    assert sum(row["source_path"] == str(source_runtime) for row in plan["included"]) == 1
    assert sum(row["source_path"] == str(source) for row in plan["included"]) == 1


@pytest.mark.parametrize("source_state", ["missing", "undeclared-runtime", "missing-repository"])
def test_global_discovery_records_unavailable_sources_without_copying_implicit_host_state(tmp_path, monkeypatch, source_state):
    from loopx import paths

    runtime, implicit = tmp_path / "runtime", tmp_path / "implicit-host-runtime"
    runtime.mkdir()
    implicit.mkdir()
    (implicit / "unselected.txt").write_text("Outside the explicitly selected backup roots")
    monkeypatch.setattr(paths, "select_default_runtime_root", lambda: implicit)
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source.json"
    goal = {"id": "goal-a", "repo": str(project), "state_file": "current.md"}
    if source_state != "missing":
        source.write_text(json.dumps({"goals": [goal if source_state != "missing-repository" else {"id": "goal-a"}]}))
    (runtime / "registry.global.json").write_text(json.dumps({"registry_role": "global-local",
        "common_runtime_root": str(runtime), "goals": [{**goal, "source_registry": "../source.json"}]}))
    plan = build_state_backup_plan(project=project, runtime_root=runtime, output_dir=tmp_path / "backups",
        include_automations=False, include_skills=False)
    assert plan["registry_discovery"]["goal_runtime_roots"] == {"goal-a": None}
    assert any(row["key"] == "registry_runtime:goal-a" for row in plan["missing"])
    assert not any(row["source_path"] == str(implicit) for row in plan["included"])
    if source.exists():
        assert any(row["source_path"] == str(source) for row in plan["included"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("writer", [None, "goal_instance_v1", "future_writer_v99"],
                         ids=["legacy", "strict-v1", "readable-future-writer"])
def test_native_backup_retains_external_routes_for_readable_project_registries(tmp_path, provider, writer):
    registry, runtime, old_state = promoted_create_fixture(tmp_path, provider=provider)
    added = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
        "--registry", str(registry), "--runtime-root", str(runtime), "todo", "add",
        "--goal-id", "goal-a", "--role", "agent", "--claimed-by", "agent-a",
        "--operation-id", "retained-work", "--text", "Keep this unfinished commitment"],
        capture_output=True, text=True, check=True, timeout=90)
    assert json.loads(added.stdout)["ok"] is True
    external = tmp_path / "external"
    external.mkdir()
    state = external / "full-goal.md"
    state.write_text(old_state.read_text() + "\n## Original commitments\nCurrent full Goal beyond the stale conventional copy.\n")
    source = external / "source-registry.json"
    source.write_bytes(registry.read_bytes())
    payload = json.loads(registry.read_text())
    payload["goals"][0].update(state_file="../external/full-goal.md", source_registry=str(source))
    project_registry = tmp_path / "project/.loopx/registry.json"
    project_registry.parent.mkdir()
    project_registry.write_text(json.dumps(_strict_registry(payload, writer) if writer else payload))
    expected = {"registry_active_state:goal-a": state.read_bytes(),
                "registry_source_registry:goal-a": source.read_bytes(),
                "configuration_source_registry": project_registry.read_bytes()}
    completed = subprocess.run(_backup_cli(tmp_path), capture_output=True, text=True, check=True, timeout=90)
    backup = json.loads(completed.stdout)
    assert backup["ok"] is True
    captured = {row["key"]: row["archive_path"] for row in backup["included"]}
    assert expected.keys() <= captured.keys(), backup["warnings"]
    for name in ("project", "runtime", "external", "registry.json"):
        (tmp_path / name).rename(tmp_path / f"unavailable-{name}")
    destination = tmp_path / "inert"
    audit = verify_state_backup(archive=Path(backup["archive_path"]), manifest=Path(backup["manifest_path"]),
                                destination=destination, execute=True)
    assert audit["status"] == "held" and audit["activation_eligible"] is False
    assert audit["execution_authority_granted"] is False
    assert audit["workspace"] == {"isolation": "inert", "registered": False}
    [authority] = [row for row in audit["components"] if row["owner_kind"] == "coordination_authority"]
    assert authority["state"] == "readable", authority
    assert authority["facts"]["provider"] == provider
    assert authority["facts"]["todo_count"] == 1 and authority["facts"]["history_commits"] == 2
    for key, raw in expected.items():
        assert (destination / "data" / captured[key]).read_bytes() == raw
    assert not (destination / "registry.global.json").exists()


@pytest.mark.parametrize("refusal", ["digest", "schema", "lifecycle", "global-envelope"])
def test_native_backup_keeps_registry_compatibility_refusals_and_previous_pair(tmp_path, refusal):
    project_registry = tmp_path / "project/.loopx/registry.json"
    project_registry.parent.mkdir(parents=True)
    project_registry.write_text('{"goals": []}')
    previous = execute_state_backup_plan(backup_plan(tmp_path))
    paths = [Path(previous[key]) for key in ("archive_path", "manifest_path")]
    original = [path.read_bytes() for path in paths]
    payload = {"goals": []}
    if refusal == "lifecycle":
        payload["profile_id"] = "source_session_v1"
    envelope = _strict_registry(payload)
    expected_error = {"digest": "payload digest", "schema": "schema_version",
                      "lifecycle": "lifecycle-only", "global-envelope": "JSON object"}[refusal]
    if refusal == "digest":
        envelope[0]["payload_sha256"] = "sha256:" + "0" * 64
    elif refusal == "schema":
        envelope[0]["schema_version"] = "unsupported_registry_v99"
    elif refusal == "lifecycle":
        envelope[0].update(schema_version="loopx_project_registry_envelope_v2", minimum_writer_protocol="goal_instance_v2")
    command = _backup_cli(tmp_path)
    if refusal == "global-envelope":
        project_registry = tmp_path / "runtime/registry.global.json"
        project_registry.parent.mkdir(exist_ok=True)
        command.remove("--current-project-only")
    project_registry.write_text(json.dumps(envelope))
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert result.returncode == 1, result.stdout
    rejected = json.loads(result.stdout)
    assert rejected["ok"] is False and expected_error in rejected["error"], rejected
    assert [path.read_bytes() for path in paths] == original
    assert set(paths[0].parent.iterdir()) == set(paths)


def test_native_cli_refuses_a_reused_backup_id_and_keeps_original_data(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = runtime / "progress.txt"
    source.write_text("accepted original")
    first = subprocess.run(_backup_cli(tmp_path), cwd=tmp_path, capture_output=True, text=True, check=True)
    payload = json.loads(first.stdout)
    archive, manifest = Path(payload["archive_path"]), Path(payload["manifest_path"])
    original = (archive.read_bytes(), manifest.read_bytes())
    source.write_text("new work must use another backup identity")
    retry = subprocess.run(_backup_cli(tmp_path), cwd=tmp_path, capture_output=True, text=True)
    assert retry.returncode == 1, retry.stdout
    assert json.loads(retry.stdout)["ok"] is False
    assert "backup-id" in json.loads(retry.stdout)["error"]
    markdown_command = _backup_cli(tmp_path)
    markdown_command[markdown_command.index("json")] = "markdown"
    markdown = subprocess.run(markdown_command, cwd=tmp_path, capture_output=True, text=True)
    assert markdown.returncode == 1
    assert "backup output already exists" in markdown.stdout
    assert "--backup-id" in markdown.stdout and archive.name in markdown.stdout
    assert "None" not in markdown.stdout
    assert (archive.read_bytes(), manifest.read_bytes()) == original
    restore(payload, tmp_path / "readback")
    assert (tmp_path / "readback/runtime-root/progress.txt").read_text() == "accepted original"


def test_concurrent_native_backups_publish_one_matching_pair(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "progress.txt").write_text("one immutable recovery unit")
    workers = [subprocess.Popen(_backup_cli(tmp_path), cwd=tmp_path,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
               for _ in range(2)]
    results = []
    try:
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=60)
            results.append((worker.returncode, json.loads(stdout), stderr))
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate(timeout=10)
    assert sorted(code for code, _, _ in results) == [0, 1], results
    payload = next(result for code, result, _ in results if code == 0)
    archive, manifest = Path(payload["archive_path"]), Path(payload["manifest_path"])
    assert json.loads(manifest.read_text()) == payload
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == payload["execution"]["archive_sha256"]
    assert sorted(path.name for path in archive.parent.iterdir()) == sorted([archive.name, manifest.name])


@pytest.mark.parametrize("failed_member", ["archive", "manifest"])
def test_new_publication_failure_preserves_prior_backups_and_allows_a_fresh_id(
    tmp_path, monkeypatch, failed_member,
):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "progress.txt").write_text("retained progress")
    previous = execute_state_backup_plan(backup_plan(tmp_path, "previous"))
    old_paths = [Path(previous[key]) for key in ("archive_path", "manifest_path")]
    old_bytes = [path.read_bytes() for path in old_paths]
    plan = backup_plan(tmp_path)
    archive, manifest = Path(plan["archive_path"]), Path(plan["manifest_path"])
    target = archive if failed_member == "archive" else manifest
    link = state_backup.os.link

    def fail_publication(source, destination, **kwargs):
        if Path(destination) == target:
            raise PermissionError("injected exclusive publication failure")
        return link(source, destination, **kwargs)

    with monkeypatch.context() as failure:
        failure.setattr(state_backup.os, "link", fail_publication)
        with pytest.raises(PermissionError, match="publication failure"):
            execute_state_backup_plan(plan)
    assert [path.read_bytes() for path in old_paths] == old_bytes
    assert not archive.exists()
    assert manifest.exists() is (failed_member == "archive")
    if manifest.exists():
        partial = manifest.read_bytes()
        with pytest.raises(FileExistsError, match="backup-id"):
            execute_state_backup_plan(plan)
        assert manifest.read_bytes() == partial
    retried = execute_state_backup_plan(backup_plan(tmp_path, "fresh-retry"))
    restore(retried, tmp_path / "readback")
    assert (tmp_path / "readback/runtime-root/progress.txt").read_text() == "retained progress"


@pytest.mark.parametrize("last_published", ["manifest.json", "tar.gz"])
def test_process_exit_during_publication_preserves_prior_units(tmp_path, last_published):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "progress.txt").write_text("durable input")
    previous = execute_state_backup_plan(backup_plan(tmp_path, "previous"))
    old_paths = [Path(previous[key]) for key in ("archive_path", "manifest_path")]
    old_bytes = [path.read_bytes() for path in old_paths]
    interruption = """
import os, runpy, sys
suffix = sys.argv.pop(1)
link = os.link
def publish(source, destination, **kwargs):
    link(source, destination, **kwargs)
    if str(destination).endswith(suffix):
        os._exit(86)
os.link = publish
runpy.run_module('loopx.cli', run_name='__main__')
"""
    command = _backup_cli(tmp_path)
    interrupted = subprocess.run([sys.executable, "-I", "-c", interruption,
                                  last_published, *command[4:]], cwd=tmp_path,
                                 capture_output=True, text=True, timeout=60)
    assert interrupted.returncode == 86, interrupted.stdout + interrupted.stderr
    archive = tmp_path / "backups/loopx-state-exclusive.tar.gz"
    manifest = tmp_path / "backups/loopx-state-exclusive.manifest.json"
    assert manifest.is_file()
    assert archive.exists() is (last_published == "tar.gz")
    if archive.exists():
        payload = json.loads(manifest.read_text())
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == payload["execution"]["archive_sha256"]
        restore(payload, tmp_path / "readback")
        assert (tmp_path / "readback/runtime-root/progress.txt").read_text() == "durable input"
    retained_manifest = manifest.read_bytes()
    retry = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert retry.returncode == 1 and "backup-id" in json.loads(retry.stdout)["error"]
    assert manifest.read_bytes() == retained_manifest
    assert [path.read_bytes() for path in old_paths] == old_bytes


def test_linkless_filesystem_uses_exclusive_private_copy(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    original = b"\x00\r\n\xff" * 32768
    (runtime / "progress.data").write_bytes(original)

    def unsupported(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, "filesystem has no hard links")

    monkeypatch.setattr(state_backup.os, "link", unsupported)
    plan = backup_plan(tmp_path)
    payload = execute_state_backup_plan(plan)
    archive, manifest = Path(payload["archive_path"]), Path(payload["manifest_path"])
    saved = (archive.read_bytes(), manifest.read_bytes())
    assert hashlib.sha256(saved[0]).hexdigest() == json.loads(saved[1])["execution"]["archive_sha256"]
    restore(payload, tmp_path / "readback")
    assert (tmp_path / "readback/runtime-root/progress.data").read_bytes() == original
    if os.name == "posix":
        assert archive.stat().st_mode & 0o777 == manifest.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError, match="backup-id"):
        execute_state_backup_plan(plan)
    assert (archive.read_bytes(), manifest.read_bytes()) == saved


@pytest.mark.parametrize("occupied_member", ["archive_path", "manifest_path"])
def test_linkless_publication_refuses_names_occupied_after_preflight(tmp_path, monkeypatch, occupied_member):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "progress.txt").write_text("private staged source")
    plan = backup_plan(tmp_path)
    occupied = Path(plan[occupied_member])

    def occupied_before_copy(source, destination):
        if Path(destination) == occupied:
            occupied.write_bytes(b"other writer output")
        raise OSError(errno.EOPNOTSUPP, "filesystem has no hard links")

    monkeypatch.setattr(state_backup.os, "link", occupied_before_copy)
    with pytest.raises(FileExistsError, match="backup-id"):
        execute_state_backup_plan(plan)
    assert occupied.read_bytes() == b"other writer output"


def test_dangling_manifest_is_occupied_and_is_not_replaced(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "progress.txt").write_text("preserve existing output names")
    plan = backup_plan(tmp_path)
    manifest = Path(plan["manifest_path"])
    manifest.parent.mkdir()
    target = tmp_path / "absent-owner-file"
    manifest.symlink_to(target)
    with pytest.raises(FileExistsError, match="backup-id"):
        execute_state_backup_plan(plan)
    assert manifest.is_symlink() and manifest.readlink() == target
    assert not target.exists() and not Path(plan["archive_path"]).exists()
