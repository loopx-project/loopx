from __future__ import annotations

import hashlib
import gzip
import io
import http.client
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tarfile
import threading

import pytest

from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan
from loopx.control_plane.state_recovery_io import StateRecoveryError, read_state_recovery_audit, render_state_recovery_markdown, verify_state_backup
from tests.control_plane.canonical_authority_fixture import promoted_create_fixture


pytestmark = pytest.mark.usefixtures("isolated_effect_runtime")


def _cli(*args: object) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *map(str, args)],
        capture_output=True, text=True, env={**os.environ, "LOOPX_USAGE_PING": "0"}, timeout=90)
    return result.returncode, json.loads(result.stdout)


def _backup(root: Path, provider: str | None = None) -> tuple[Path, Path]:
    if provider:
        registry, runtime, state = promoted_create_fixture(root, provider=provider)
        project = state.parents[3]
        code, result = _cli("--registry", registry, "--runtime-root", runtime, "todo", "add",
            "--goal-id", "goal-a", "--role", "agent", "--claimed-by", "agent-a",
            "--operation-id", "retained-work", "--text", "Keep this unfinished commitment")
        assert code == 0, result
        state.write_text(state.read_text() + "\n## Original commitments\nPreserve the full Goal, including unfinished acceptance.\n")
    else:
        project, runtime = root / "project", root / "runtime"
        project.mkdir()
        runtime.mkdir()
        registry = root / "registry.json"
        registry.write_text(json.dumps({"goals": []}))
        (runtime / "retained.txt").write_text("saved data")
    result = execute_state_backup_plan(build_state_backup_plan(project=project, runtime_root=runtime,
        registry_path=registry, output_dir=root / "backups", backup_id="source", include_registry_projects=False,
        include_automations=False, include_skills=False))
    return Path(result["archive_path"]), Path(result["manifest_path"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("restore_source", [False, True], ids=["persistent-change", "aba"])
def test_backup_refuses_configuration_from_different_copied_registry_bytes(
    tmp_path, monkeypatch, provider, restore_source,
):
    from loopx.capabilities import configuration_backup

    original_add = tarfile.TarFile.add
    original_capture = configuration_backup.capture_configuration_backup
    copied = []

    def after_copy(tar, source, *args, **kwargs):
        result = original_add(tar, source, *args, **kwargs)
        if kwargs.get("arcname") == "configuration/registry.source.json":
            registry = tmp_path / "registry.json"
            copied.append(registry.read_bytes())
            value = json.loads(copied[0])
            value["goals"][0]["description"] = "Different source revision"
            registry.write_text(json.dumps(value))
        return result

    def after_capture(**kwargs):
        checkpoint = original_capture(**kwargs)
        if restore_source:
            # Restore A only after the real source owner captured B. Comparing
            # the live path after capture would miss this A -> B -> A change.
            (tmp_path / "registry.json").write_bytes(copied[0])
        return checkpoint

    monkeypatch.setattr(tarfile.TarFile, "add", after_copy)
    monkeypatch.setattr(configuration_backup, "capture_configuration_backup", after_capture)
    with pytest.raises(ValueError, match="configuration source.*copied bytes"):
        _backup(tmp_path, provider)
    assert len(copied) == 1
    assert not list((tmp_path / "backups").iterdir()), "a mixed backup pair must not be published"


def test_backup_refuses_a_required_configuration_source_without_copied_bytes(tmp_path):
    project, runtime, registry = tmp_path / "project", tmp_path / "runtime", tmp_path / "registry.json"
    project.mkdir()
    runtime.mkdir()
    registry.write_text(json.dumps({"goals": []}))
    plan = build_state_backup_plan(project=project, runtime_root=runtime, registry_path=registry,
        output_dir=tmp_path / "backups", include_registry_projects=False,
        include_automations=False, include_skills=False)
    plan["included"] = [row for row in plan["included"] if row["key"] != "configuration_source_registry"]
    with pytest.raises(ValueError, match="configuration source has no copied bytes witness"):
        execute_state_backup_plan(plan)
    assert not list((tmp_path / "backups").iterdir())


def _rewrite(archive: Path, manifest: Path, extra: tarfile.TarInfo | None = None,
             body: bytes = b"", replacements: dict[str, bytes] | None = None) -> None:
    with tarfile.open(archive, "r:gz") as source:
        records = [(row, source.extractfile(row).read() if row.isfile() else None) for row in source]
    with tarfile.open(archive, "w:gz") as target:
        for row, data in records:
            if replacements and row.name in replacements:
                data = replacements[row.name]
                row.size = len(data)
            target.addfile(row, io.BytesIO(data) if data is not None else None)
        if extra is not None:
            target.addfile(extra, io.BytesIO(body) if extra.isfile() else None)
    data = json.loads(manifest.read_text())
    data["execution"]["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    data["execution"]["archive_size_bytes"] = archive.stat().st_size
    manifest.write_text(json.dumps(data))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("owner", ["registry", "machine"])
def test_legacy_mixed_revision_archive_remains_readable_and_inert(tmp_path, provider, owner):
    from loopx.capabilities.configuration_backup import capture_configuration_backup, verify_configuration_backup

    archive, manifest = _backup(tmp_path, provider)
    registry = tmp_path / "registry.json"
    if owner == "registry":
        value = json.loads(registry.read_text())
        value["goals"][0]["description"] = "Later configuration in a historical archive"
        registry.write_text(json.dumps(value))
    else:
        machine = tmp_path / "runtime/machine/configuration.json"
        machine.parent.mkdir(exist_ok=True)
        machine.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v0", "new_goal_provider": "sqlite"}}}))
    later = capture_configuration_backup(registry_path=registry, runtime_root=tmp_path / "runtime")
    content = json.dumps(later, ensure_ascii=False, indent=2).encode()
    # Construct the supported historical v0 case deliberately. The current
    # producer refuses it; its new provenance claims must not enter this fixture.
    legacy = json.loads(manifest.read_text())
    legacy["registry_discovery"].pop("goal_runtime_roots")
    execution = legacy["execution"]
    execution.pop("configuration_capture")
    execution["configuration_backup"] = verify_configuration_backup(later)
    execution["archive_sha256"] = execution["archive_size_bytes"] = None
    execution.pop("archive_to_logical_ratio", None)
    for row in execution["file_members"]:
        if row["archive_path"] == "configuration-backup.json":
            row.update(size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    manifest.write_text(json.dumps(legacy))
    _rewrite(archive, manifest, replacements={"configuration-backup.json": content,
        "manifest.json": json.dumps(legacy).encode()})
    with tarfile.open(archive) as saved:
        earlier = json.load(saved.extractfile("configuration/registry.source.json"))
    assert "description" not in earlier["goals"][0]
    if owner == "machine":
        assert later["data"]["machine_configuration"] is not None
        with tarfile.open(archive) as saved:
            assert "runtime-root/machine/configuration.json" not in saved.getnames()
    for name in ("project", "runtime", "registry.json"):
        (tmp_path / name).rename(tmp_path / f"unavailable-{name}")
    audit = verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert", execute=True)
    [work] = [row for row in audit["components"] if row["owner_kind"] == "coordination_authority"]
    assert work["state"] == "readable" and work["facts"]["todo_count"] == 1
    assert work["facts"]["history_commits"] == 2
    assert audit["manifest_verified"] is True and audit["file_witnesses_verified"] is True
    assert audit["status"] == "held" and audit["capture_profile"] == "legacy_best_effort"
    assert any(row["reason_code"] == "legacy_manifest_incomplete" for row in audit["findings"])
    assert audit["activation_eligible"] is False and audit["execution_authority_granted"] is False


def _global_source_backup(root: Path, provider: str) -> tuple[dict, dict[str, bytes]]:
    registry, source_runtime, state = promoted_create_fixture(root / "host/canonical", provider=provider)
    _, shared_runtime, _ = promoted_create_fixture(root / "host", provider="sqlite" if provider == "file" else "file")
    for source, runtime, count in ((registry, source_runtime, 1), (root / "host/registry.json", shared_runtime, 3)):
        for index in range(count):
            code, result = _cli("--registry", source, "--runtime-root", runtime, "todo", "add",
                "--goal-id", "goal-a", "--role", "agent", "--claimed-by", "agent-a",
                "--operation-id", f"retained-{index}", "--text", f"Unfinished commitment {index}")
            assert code == 0, result
    current = registry.parent / "current-goal.md"
    current.write_text(state.read_text() + "\n## Original commitments\nThe source-owned full Goal.\n")
    payload = json.loads(registry.read_text())
    payload["goals"][0]["state_file"] = "../current-goal.md"
    registry.write_text(json.dumps(payload))
    cached = {**payload["goals"][0], "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
              "source_registry": "../canonical/registry.json"}
    (shared_runtime / "registry.global.json").write_text(json.dumps({"registry_role": "global-local",
        "common_runtime_root": str(shared_runtime), "goals": [cached]}))
    wrong = registry.parent / "canonical/registry.json"
    wrong.parent.mkdir()
    wrong.write_bytes(b'{"wrong_repository_relative_copy": true}')
    code, backup = _cli("--runtime-root", shared_runtime, "backup-state", "--project", state.parents[3],
        "--output-dir", root / "backups", "--backup-id", "source", "--no-automations", "--no-skills", "--execute")
    assert code == 0 and backup["ok"], backup
    expected = {"registry": registry.read_bytes(), "goal": current.read_bytes(), "wrong": wrong.read_bytes()}
    (root / "host").rename(root / "unavailable-host")
    return backup, expected


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_global_backup_inspects_the_canonical_source_not_a_same_alias_shared_store(tmp_path, provider):
    backup, expected = _global_source_backup(tmp_path, provider)
    destination = tmp_path / "inert"
    code, audit = _cli("backup-state", "verify", "--archive", backup["archive_path"],
        "--manifest", backup["manifest_path"], "--destination", destination, "--execute")
    assert code == 0, audit
    [authority] = [row for row in audit["components"] if row["owner_kind"] == "coordination_authority"]
    assert authority["state"] == "readable", authority
    assert authority["facts"]["provider"] == provider
    assert authority["facts"]["todo_count"] == 1 and authority["facts"]["history_commits"] == 2
    assert audit["status"] == "held" and audit["activation_eligible"] is False
    assert audit["execution_authority_granted"] is False
    assert audit["workspace"] == {"isolation": "inert", "registered": False}
    assert str(tmp_path) not in json.dumps(audit)
    with tarfile.open(backup["archive_path"]) as archive:
        files = [archive.extractfile(row).read() for row in archive if row.isfile()]
    assert expected["goal"] in files and expected["registry"] in files
    assert expected["wrong"] not in files
    roots = backup["registry_discovery"]["goal_runtime_roots"]
    assert roots["goal-a"] != "runtime-root"
    assert (destination / "data" / roots["goal-a"]).is_dir()
    assert not (destination / "registry.global.json").exists()


def test_explicit_missing_source_bindings_never_fall_back_to_the_shared_store(tmp_path):
    from loopx.control_plane.state_recovery_io import _audit_owners

    backup, _ = _global_source_backup(tmp_path, "file")
    destination = tmp_path / "inert"
    audit = verify_state_backup(archive=Path(backup["archive_path"]), manifest=Path(backup["manifest_path"]),
                                destination=destination, execute=True)
    data = destination / "data"
    sqlite_members = [path.relative_to(data).as_posix() for path in data.rglob("*.sqlite")]
    request = {"data_root": str(data), "measurements": audit["measurements"],
        "verified": {key: audit[key] for key in ("ok", "schema_version", "recovery_id", "backup", "file_count",
            "file_witnesses_verified", "manifest_verified", "capture_profile", "execution_authority_granted")},
        "sqlite_members": sqlite_members, "standalone_sqlite_members": sqlite_members,
        "included": backup["included"]}
    for roots in ({}, {"goal-a": None}):
        result = _audit_owners({**request, "registry_discovery": {"goal_runtime_roots": roots}})
        [authority] = [row for row in result["components"] if row["owner_kind"] == "coordination_authority"]
        assert authority["state"] == "unknown", authority
        assert result["execution_authority_granted"] is False
    root = data / backup["registry_discovery"]["goal_runtime_roots"]["goal-a"]
    root.rename(tmp_path / "unavailable-captured-source")
    result = _audit_owners({**request, "registry_discovery": backup["registry_discovery"]})
    [authority] = [row for row in result["components"] if row["owner_kind"] == "coordination_authority"]
    assert authority["state"] == "unknown", authority
    assert not root.exists()
    root.symlink_to(data / "runtime-root", target_is_directory=True)
    result = _audit_owners({**request, "registry_discovery": backup["registry_discovery"]})
    [authority] = [row for row in result["components"] if row["owner_kind"] == "coordination_authority"]
    assert authority["state"] == "unknown", authority
    assert result["execution_authority_granted"] is False


def test_unsafe_source_binding_is_refused_before_extracting_any_files(tmp_path):
    archive, manifest = _backup(tmp_path)
    external = json.loads(manifest.read_text())
    with tarfile.open(archive) as source:
        embedded = json.load(source.extractfile("manifest.json"))
    for payload in (external, embedded):
        payload["registry_discovery"]["goal_runtime_roots"] = {"goal-a": "../outside"}
        payload["included"].append({"archive_path": "../outside"})
    manifest.write_text(json.dumps(external))
    _rewrite(archive, manifest, replacements={"manifest.json": json.dumps(embedded).encode()})
    destination = tmp_path / "inert"
    with pytest.raises(StateRecoveryError, match="backup_manifest_invalid"):
        verify_state_backup(archive=archive, manifest=manifest, destination=destination, execute=True)
    assert not destination.exists() and not (tmp_path / "outside").exists()


def test_legacy_archive_without_source_bindings_keeps_original_runtime_readback(tmp_path):
    archive, manifest = _backup(tmp_path, "file")
    external = json.loads(manifest.read_text())
    with tarfile.open(archive) as source:
        embedded = json.load(source.extractfile("manifest.json"))
    for payload in (external, embedded):
        payload["registry_discovery"].pop("goal_runtime_roots", None)
    manifest.write_text(json.dumps(external))
    _rewrite(archive, manifest, replacements={"manifest.json": json.dumps(embedded).encode()})
    audit = verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert", execute=True)
    [authority] = [row for row in audit["components"] if row["owner_kind"] == "coordination_authority"]
    assert authority["state"] == "readable" and authority["facts"]["todo_count"] == 1
    assert audit["status"] == "held" and audit["execution_authority_granted"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_backup_recovers_readable_authority_after_original_sources_disappear(tmp_path, provider):
    archive, manifest = _backup(tmp_path, provider)
    (tmp_path / "project").rename(tmp_path / "unavailable-project")
    (tmp_path / "runtime").rename(tmp_path / "unavailable-runtime")
    (tmp_path / "registry.json").rename(tmp_path / "unavailable-registry.json")
    original = archive.read_bytes(), manifest.read_bytes()
    destination = tmp_path / "inert"
    preview = verify_state_backup(archive=archive, manifest=manifest, destination=destination)
    assert preview["status"] == "planned" and preview["written"] is False
    preview_text = render_state_recovery_markdown(preview)
    assert "owner records have not been inspected" in preview_text and "Readable: 0" not in preview_text
    assert not destination.exists()
    code, audit = _cli("backup-state", "verify", "--archive", archive, "--manifest", manifest,
                       "--destination", destination, "--execute")
    assert code == 0, audit
    assert audit["execution_authority_granted"] is False and audit["activation_eligible"] is False
    assert audit["workspace"] == {"isolation": "inert", "registered": False}
    assert audit["status"] == "held"
    assert audit["measurements"]["archive_bytes"] == archive.stat().st_size
    assert audit["measurements"]["regular_file_bytes"] == preview["logical_bytes"]
    assert all(type(value) is int and value >= 0 for value in audit["measurements"].values())
    assert read_state_recovery_audit(destination)["measurements"] == audit["measurements"]
    assert "archive checks/copy:" in render_state_recovery_markdown(audit)
    [goal] = [row for row in audit["components"] if row["owner_kind"] == "coordination_authority"]
    assert goal["state"] == "readable", goal
    assert goal["facts"]["todo_count"] == 1
    assert goal["facts"]["history_commits"] == 2
    assert goal["facts"]["provider"] == provider
    captures = json.loads(manifest.read_text())["included"]
    state_path = next(row["archive_path"] for row in captures if row["key"] == "registry_active_state:goal-a")
    assert "Preserve the full Goal, including unfinished acceptance." in (destination / "data" / state_path).read_text()
    assert (archive.read_bytes(), manifest.read_bytes()) == original
    assert json.loads((destination / "recovery-audit.json").read_text()) == audit
    assert str(tmp_path) not in json.dumps(audit)
    assert not (destination / "registry.global.json").exists()
    for path in [destination, destination / "data", destination / "recovery-audit.json"]:
        assert path.stat().st_mode & 0o077 == 0
    before = (destination / "recovery-audit.json").read_bytes()
    with pytest.raises(StateRecoveryError, match="recovery_destination_exists"):
        verify_state_backup(archive=archive, manifest=manifest, destination=destination, execute=True)
    assert (destination / "recovery-audit.json").read_bytes() == before


def test_valid_internal_symlink_stays_inert_and_chained_escape_is_refused(tmp_path):
    archive, manifest = _backup(tmp_path)
    link = tarfile.TarInfo("readable-link")
    link.type, link.linkname = tarfile.SYMTYPE, "runtime-root/retained.txt"
    _rewrite(archive, manifest, link)
    result = verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "valid", execute=True)
    assert result["execution_authority_granted"] is False
    assert (tmp_path / "valid/data/readable-link").is_symlink()
    alias = tarfile.TarInfo("alias")
    alias.type, alias.linkname = tarfile.SYMTYPE, "."
    _rewrite(archive, manifest, alias)
    escape = tarfile.TarInfo("escape-link")
    escape.type, escape.linkname = tarfile.SYMTYPE, "alias/../outside"
    _rewrite(archive, manifest, escape)
    with pytest.raises(StateRecoveryError, match="archive_link_escapes"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "invalid", execute=True)
    assert not (tmp_path / "invalid").exists()


def test_capacity_and_private_input_fail_before_publication(tmp_path, monkeypatch):
    import loopx.control_plane.state_recovery_io as recovery

    archive, manifest = _backup(tmp_path)
    original = archive.stat().st_mode
    archive.chmod(0o644)
    with pytest.raises(StateRecoveryError, match="input_not_private"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "invalid", execute=True)
    archive.chmod(original)
    monkeypatch.setattr(recovery, "MAX_MEMBERS", 1)
    with pytest.raises(StateRecoveryError, match="capacity_exceeded"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "invalid", execute=True)
    assert not (tmp_path / "invalid").exists()


def test_large_manifest_uses_existing_private_snapshot_transport(tmp_path):
    archive, manifest = _backup(tmp_path)
    external = json.loads(manifest.read_text())
    with tarfile.open(archive) as source:
        embedded = json.load(source.extractfile("manifest.json"))
    # Complete metadata exceeds the ordinary 2 MiB RPC; no wire ceiling is raised.
    external["warnings"] = embedded["warnings"] = ["synthetic warning " * 150000]
    manifest.write_text(json.dumps(external))
    _rewrite(archive, manifest, replacements={"manifest.json": json.dumps(embedded).encode()})
    result = verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert")
    assert result["manifest_verified"] is True and not result["written"]


def test_changed_audit_and_true_execution_flag_are_not_trusted_on_readback(tmp_path):
    archive, manifest = _backup(tmp_path)
    destination = tmp_path / "inert"
    result = verify_state_backup(archive=archive, manifest=manifest, destination=destination, execute=True)
    path = destination / "recovery-audit.json"
    altered_measurement = {**result, "measurements": {**result["measurements"],
        "owner_read_ms": result["measurements"]["owner_read_ms"] + 1}}
    path.write_text(json.dumps(altered_measurement))
    with pytest.raises(StateRecoveryError, match="audit_digest_mismatch"):
        read_state_recovery_audit(destination)
    changed = {**result, "observed_at": "2020-01-01T00:00:00.000Z"}
    path.write_text(json.dumps(changed))
    with pytest.raises(StateRecoveryError, match="audit_digest_mismatch"):
        read_state_recovery_audit(destination)
    changed["execution_authority_granted"] = True
    path.write_text(json.dumps(changed))
    with pytest.raises(StateRecoveryError, match="audit_invalid"):
        read_state_recovery_audit(destination)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_missing_selected_storage_is_unknown_without_creation_or_fallback(tmp_path, provider):
    archive, manifest = _backup(tmp_path, provider)
    destination = tmp_path / "inert"
    original = verify_state_backup(archive=archive, manifest=manifest, destination=destination, execute=True)
    from loopx.control_plane.state_recovery_io import _audit_owners

    selected = destination / "data/runtime-root/authority" / f"{provider}-v0"
    sqlite_files = [path.relative_to(destination / "data").as_posix() for path in selected.glob("*.sqlite")]
    selected.rename(tmp_path / "unavailable-store")
    observed = _audit_owners({"data_root": str(destination / "data"),
        "measurements": original["measurements"],
        "verified": {key: original[key] for key in ("ok", "schema_version", "recovery_id", "backup", "file_count",
            "file_witnesses_verified", "manifest_verified", "capture_profile", "execution_authority_granted")},
        "sqlite_members": sqlite_files, "standalone_sqlite_members": sqlite_files})
    [goal] = [row for row in observed["components"] if row["owner_kind"] == "coordination_authority"]
    assert goal["state"] == "unknown"
    assert observed["execution_authority_granted"] is False
    assert not selected.exists()
    if provider == "sqlite":
        assert not (destination / "data/runtime-root/authority/file-v0").exists()


def test_restored_code_remains_unclassified_data_and_is_never_loaded(tmp_path):
    archive, manifest = _backup(tmp_path)
    # Re-capture through the real producer so the hostile file has valid witnesses.
    marker = tmp_path / "executed"
    (tmp_path / "runtime/untrusted_plugin.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    result = execute_state_backup_plan(build_state_backup_plan(project=tmp_path / "project", runtime_root=tmp_path / "runtime",
        registry_path=tmp_path / "registry.json", output_dir=tmp_path / "with-code", backup_id="code",
        include_registry_projects=False, include_automations=False, include_skills=False))
    audit = verify_state_backup(archive=Path(result["archive_path"]), manifest=Path(result["manifest_path"]),
        destination=tmp_path / "inert-code", execute=True)
    assert not marker.exists()
    [remaining] = [row for row in audit["components"] if row["component_id"] == "remaining_state"]
    assert remaining["state"] == "unknown" and audit["execution_authority_granted"] is False


def test_sqlite_views_are_never_evaluated_as_authority_tables(tmp_path):
    archive, manifest = _backup(tmp_path, "sqlite")
    with tarfile.open(archive) as source:
        database_member = next(row.name for row in source if row.name.endswith(".sqlite"))
        database_bytes = source.extractfile(database_member).read()
        embedded = json.load(source.extractfile("manifest.json"))
    database_path = tmp_path / "view.sqlite"
    database_path.write_bytes(database_bytes)
    with sqlite3.connect(database_path) as database:
        database.executescript("ALTER TABLE metadata RENAME TO saved_metadata; "
            "CREATE VIEW metadata AS WITH RECURSIVE spin(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM spin) "
            "SELECT *, (SELECT count(*) FROM spin) AS expensive FROM saved_metadata;")
    changed = database_path.read_bytes()
    external = json.loads(manifest.read_text())
    for envelope in (external, embedded):
        witness = next(row for row in envelope["execution"]["file_members"] if row["archive_path"] == database_member)
        witness.update(sha256=hashlib.sha256(changed).hexdigest(), size_bytes=len(changed))
    manifest.write_text(json.dumps(external))
    _rewrite(archive, manifest, replacements={database_member: changed, "manifest.json": json.dumps(embedded).encode()})
    result = verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "view-inert", execute=True)
    [goal] = [row for row in result["components"] if row["owner_kind"] == "coordination_authority"]
    assert goal["state"] == "unknown"
    assert goal["reason_code"] == "sqlite_requires_owner_recovery"


def test_offline_worker_timeout_does_not_leave_a_copy_or_stop_shared_runtime(tmp_path, monkeypatch):
    import loopx.control_plane.state_recovery_io as recovery
    from loopx.control_plane.effect_runtime import effect_runtime_result

    archive, manifest = _backup(tmp_path)
    monkeypatch.setattr(recovery, "_OWNER_TIMEOUT_SECONDS", 0.001)
    with pytest.raises(StateRecoveryError, match="recovery_owner_timeout"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "timed-out", execute=True)
    assert not (tmp_path / "timed-out").exists()
    assert not list(tmp_path.glob(".loopx-recovery-*"))
    # This is still the real shared helper, not a fabricated availability result.
    normal = effect_runtime_result("configuration.backup", {"action": "capture", "data": {"machine_configuration": None, "goals": []}})
    assert normal["activation_performed"] is False


def test_real_sqlite_history_limit_is_complete_and_overflow_refuses_publication(tmp_path):
    registry, runtime, state = promoted_create_fixture(tmp_path, provider="sqlite")
    project = state.parents[3]
    owner = Path(__file__).parents[1] / "loopx/control_plane/coordination"
    # Seed the read-capacity fixture in one transaction, retaining owner codecs
    # and a complete real history audit instead of timing 20,000 FULL fsyncs.
    script = (
        f"import {{openLocalAuthorityStore}} from {json.dumps((owner / 'local_authority_provider.ts').as_uri())};"
        f"import {{SqliteAuthorityStore,commitDigest}} from {json.dumps((owner / 'sqlite_authority_store.ts').as_uri())};"
        f"import {{authorityStateDelta,authorityStateDigest,isAuthorityStateCheckpoint}} from {json.dumps((owner / 'authority_state_log.ts').as_uri())};"
        "import {DatabaseSync} from 'node:sqlite';"
    ) + """
let input='';for await(const chunk of process.stdin)input+=chunk;const r=JSON.parse(input);
const store=await openLocalAuthorityStore(r.root,'goal-a');const head=await store.loadAuthority();
if(!(store instanceof SqliteAuthorityStore)||head.status!=='loaded'||Number(head.cursor)!==r.first-1)
 throw new Error('Unexpected isolated fixture head');
const identity=await store.storeIdentity();if(identity.status!=='available')throw new Error('Missing fixture identity');
const projection=JSON.stringify(head.head),digest=authorityStateDigest(head.head);
const delta=JSON.stringify(authorityStateDelta(head.head,head.head));
const db=new DatabaseSync(store.path);db.exec('PRAGMA foreign_keys=ON; BEGIN IMMEDIATE');
try{
 const commit=db.prepare('INSERT INTO commits VALUES (?, ?, ?, ?, ?, ?, ?, ?)');
 const checkpoint=db.prepare('INSERT INTO checkpoints VALUES (?, ?, ?)');
 for(let i=r.first;i<=r.last;i++){
  const cursor=BigInt(i),operation=`history-capacity-${i}`;
  commit.run(i,operation,commitDigest(identity.store_identity,cursor,operation,head.head,[],[]),digest,digest,delta,'[]','[]');
  if(isAuthorityStateCheckpoint(cursor))checkpoint.run(i,projection,digest);
 }
 db.prepare('UPDATE head SET cursor=? WHERE singleton=1').run(r.last);
 db.exec('COMMIT');
}finally{db.close();}
const audit=await store.verifyAuthorityHistory();
if(audit.status!=='verified'||audit.commits!==r.last)throw new Error(JSON.stringify(audit));
const readback=await store.loadAuthority();
if(readback.status!=='loaded'||Number(readback.cursor)!==r.last)throw new Error('Fixture readback mismatch');
process.stdout.write(JSON.stringify(audit));
"""
    for first, last, expected in [(2, 20000, "readable"), (20001, 20001, "refused")]:
        seeded = subprocess.run(["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
            "--input-type=module", "-e", script], input=json.dumps({"root": str(runtime), "first": first, "last": last}),
            text=True, capture_output=True, check=True, timeout=120)
        assert json.loads(seeded.stdout)["commits"] == last
        backup = execute_state_backup_plan(build_state_backup_plan(project=project, runtime_root=runtime,
            registry_path=registry, output_dir=tmp_path / "backups", backup_id=f"history-{last}",
            include_registry_projects=False, include_automations=False, include_skills=False))
        destination = tmp_path / f"inert-{last}"
        code, result = _cli("backup-state", "verify", "--archive", backup["archive_path"],
            "--manifest", backup["manifest_path"], "--destination", destination, "--execute")
        if expected == "readable":
            assert code == 0, result
            [goal] = [row for row in result["components"] if row["owner_kind"] == "coordination_authority"]
            assert goal["state"] == "readable" and goal["facts"]["history_commits"] == 20000
        else:
            assert code == 1 and result["error_code"] == "authority_history_capacity", result
            assert not destination.exists()
            assert not list(tmp_path.glob(".loopx-recovery-*"))


def test_digest_refusal_precedes_tar_decoding_and_writes_nothing(tmp_path):
    archive, manifest = _backup(tmp_path)
    archive.write_bytes(b"not a tar archive")
    with pytest.raises(StateRecoveryError, match="archive_digest_mismatch"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert", execute=True)
    assert not (tmp_path / "inert").exists()
    assert not list(tmp_path.glob(".loopx-recovery-*"))


def test_external_manifest_cannot_relabel_the_embedded_capture(tmp_path):
    archive, manifest = _backup(tmp_path)
    data = json.loads(manifest.read_text())
    data["backup_id"] = "different-backup"
    manifest.write_text(json.dumps(data))
    with pytest.raises(StateRecoveryError, match="manifest_disagreement"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert", execute=True)
    assert not (tmp_path / "inert").exists()


def test_recomputed_archive_digest_does_not_hide_member_corruption(tmp_path):
    archive, manifest = _backup(tmp_path)
    _rewrite(archive, manifest, replacements={"runtime-root/retained.txt": b"altered data"})
    with pytest.raises(StateRecoveryError, match="member_witness_mismatch"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert", execute=True)


@pytest.mark.parametrize("name,kind,target,reason", [
    ("../escape", tarfile.REGTYPE, "", "archive_path_invalid"),
    ("/absolute", tarfile.REGTYPE, "", "archive_path_invalid"),
    ("runtime-root/RETAINED.txt", tarfile.REGTYPE, "", "archive_name_collision"),
    ("runtime-root/retained.txt", tarfile.REGTYPE, "", "archive_name_collision"),
    ("fifo", tarfile.FIFOTYPE, "", "archive_member_type_unsupported"),
    ("hardlink", tarfile.LNKTYPE, "runtime-root/retained.txt", "archive_member_type_unsupported"),
    ("outside", tarfile.SYMTYPE, "../escape", "archive_link_escapes"),
    ("runtime-root", tarfile.SYMTYPE, "project", "archive_name_collision"),
])
def test_archive_paths_and_member_types_fail_before_destination_creation(tmp_path, name, kind, target, reason):
    archive, manifest = _backup(tmp_path)
    member = tarfile.TarInfo(name)
    member.type, member.linkname = kind, target
    _rewrite(archive, manifest, member)
    with pytest.raises(StateRecoveryError, match=reason):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert", execute=True)
    assert not (tmp_path / "inert").exists()
    assert not (tmp_path.parent / "escape").exists()


def test_large_pax_metadata_is_refused_before_tarfile_decodes_its_body(tmp_path):
    archive, manifest = _backup(tmp_path)
    member = tarfile.TarInfo("extended-metadata")
    member.pax_headers = {"comment": "x" * (128 * 1024 + 1)}
    _rewrite(archive, manifest, member)
    with pytest.raises(StateRecoveryError, match="archive_metadata_too_large"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "invalid", execute=True)
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("kind,headers,decoder", [
    (tarfile.GNUTYPE_SPARSE, {}, "_proc_sparse"),
    (tarfile.REGTYPE, {"GNU.sparse.size": "invalid"}, "_proc_gnusparse_00"),
    (tarfile.REGTYPE, {"GNU.sparse.map": "invalid"}, "_proc_gnusparse_01"),
    (tarfile.REGTYPE, {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"}, "_proc_gnusparse_10"),
])
def test_sparse_records_are_refused_before_stdlib_extent_decoding(tmp_path, monkeypatch, kind, headers, decoder):
    archive, manifest = _backup(tmp_path)
    member = tarfile.TarInfo("unsupported-sparse")
    member.type, member.pax_headers = kind, headers
    _rewrite(archive, manifest, member)

    # Malformed sparse metadata must produce the same bounded refusal as a
    # valid unsupported record, rather than leaking a decoder exception.
    with pytest.raises(StateRecoveryError, match="^archive_sparse_unsupported$"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert", execute=True)

    def unexpected_decode(*_args):
        pytest.fail("unsupported sparse extents reached the stdlib decoder")

    monkeypatch.setattr(tarfile.TarInfo, decoder, unexpected_decode)
    with pytest.raises(StateRecoveryError, match="^archive_sparse_unsupported$"):
        verify_state_backup(archive=archive, manifest=manifest, destination=tmp_path / "inert", execute=True)
    assert not (tmp_path / "inert").exists()
    assert not list(tmp_path.glob(".loopx-recovery-*"))


@pytest.mark.parametrize("kind", [tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE])
def test_oversized_pax_variants_are_refused_without_reading_the_body(kind):
    from loopx.control_plane.state_recovery_io import _scan

    member = tarfile.TarInfo("oversized-pax")
    member.type, member.size = kind, 128 * 1024 + 1
    compressed = gzip.compress(member.tobuf())
    with pytest.raises(StateRecoveryError, match="^archive_metadata_too_large$"):
        _scan(io.BytesIO(compressed), len(compressed))


def test_malformed_pax_record_length_is_a_bounded_archive_error():
    from loopx.control_plane.state_recovery_io import _scan

    payload = b"9" * 5000 + b" comment=invalid\n"
    member = tarfile.TarInfo("malformed-pax")
    member.type, member.size = tarfile.XHDTYPE, len(payload)
    raw = member.tobuf() + payload + b"\0" * (-len(payload) % 512 + 1024)
    compressed = gzip.compress(raw)
    with pytest.raises(StateRecoveryError, match="^archive_unreadable$"):
        _scan(io.BytesIO(compressed), len(compressed))


def test_failed_publication_cleans_staging_even_when_destination_becomes_occupied(tmp_path, monkeypatch):
    import loopx.control_plane.state_recovery_io as recovery

    archive, manifest = _backup(tmp_path)
    destination = tmp_path / "inert"

    def failed_rename(source, target):
        assert target == destination and (source / "recovery-audit.json").is_file()
        (destination / "foreign-file").write_text("preserve this")
        raise OSError("publication interrupted")

    monkeypatch.setattr(recovery.os, "rename", failed_rename)
    with pytest.raises(OSError):
        verify_state_backup(archive=archive, manifest=manifest, destination=destination, execute=True)
    assert (destination / "foreign-file").read_text() == "preserve this"
    assert not (destination / "recovery-audit.json").exists()
    assert not list(tmp_path.glob(".loopx-recovery-*"))


def test_symlink_parent_and_occupied_directory_are_not_recovery_targets(tmp_path):
    archive, manifest = _backup(tmp_path)
    link = tmp_path / "alias"
    link.symlink_to(tmp_path / "project", target_is_directory=True)
    with pytest.raises(StateRecoveryError, match="parent_not_physical"):
        verify_state_backup(archive=archive, manifest=manifest, destination=link / "new", execute=True)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep").write_text("unchanged")
    with pytest.raises(StateRecoveryError, match="destination_exists"):
        verify_state_backup(archive=archive, manifest=manifest, destination=occupied, execute=True)
    assert (occupied / "keep").read_text() == "unchanged"


def test_real_http_upload_verify_reload_and_corrupt_input_keep_live_state_unchanged(tmp_path):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    archive, manifest = _backup(tmp_path)
    runtime = tmp_path / "replacement-runtime"
    runtime.mkdir()
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_root, server.registry_path, server.verbose = runtime, tmp_path / "absent-registry.json", False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(path, body=None, *, binary=False):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=90)
        encoded = body if binary else json.dumps(body).encode() if body is not None else None
        connection.request("POST" if body is not None else "GET", "/api/chat/state-recovery" + path,
            body=encoded, headers={"Content-Type": "application/octet-stream" if binary else "application/json"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        status = response.status
        connection.close()
        return status, payload

    try:
        assert request("")[1]["items"] == []
        assert not (runtime / "backups").exists()
        _, first = request("/archive", archive.read_bytes(), binary=True)
        _, second = request("/manifest", manifest.read_bytes(), binary=True)
        inputs = {"archive_ref": first["input_ref"], "manifest_ref": second["input_ref"], "execute": True}
        status, result = request("/verify", inputs)
        assert status == 200, result
        assert result["execution_authority_granted"] is False
        assert not server.registry_path.exists()
        status, rows = request("")
        assert status == 200 and rows["items"][0]["checkpoint_ref"] == result["checkpoint_ref"]
        read = request("/read", {"checkpoint_ref": result["checkpoint_ref"]})[1]
        assert read["historical_readback"] is True and read["audit_sha256"] == result["audit_sha256"]
        duplicate_status, duplicate = request("/verify", inputs)
        assert duplicate_status == 400 and duplicate["error_code"] == "recovery_destination_exists"
        checkpoint = runtime / "backups/state-recovery/checkpoints" / result["checkpoint_ref"]
        before = (checkpoint / "recovery-audit.json").read_bytes()
        _, damaged = request("/archive", b"damaged archive", binary=True)
        status, rejected = request("/verify", {**inputs, "archive_ref": damaged["input_ref"]})
        assert status == 400 and rejected["error_code"] == "archive_digest_mismatch"
        assert (checkpoint / "recovery-audit.json").read_bytes() == before
        assert len(list((runtime / "backups/state-recovery/checkpoints").iterdir())) == 1
        assert not server.registry_path.exists()
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
