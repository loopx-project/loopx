"""Large unrelated backup inventories do not inflate cold-import transport."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

from loopx.control_plane.coordination.cold_source_backup import (
    ColdSourceBackupError, read_cold_source_backup,
)


def backup(tmp_path: Path, unrelated: int = 0, unsafe: bool = False,
    extra_members: tuple[str, ...] = ()) -> Path:
    archive = tmp_path / "state.tar.gz"
    manifest = {
        "schema_version": "loopx_state_backup_v0", "dry_run": False,
        "execute_requested": True, "runtime_root": str(tmp_path / "runtime"),
        "project": str(tmp_path / "project"), "configuration_source_registry": None,
        "included": [{"source_path": str(tmp_path / "runtime"), "archive_path": "runtime"}],
    }
    with tarfile.open(archive, "w:gz") as tar:
        def add(name: str, data: bytes = b"") -> None:
            member = tarfile.TarInfo(name)
            if name.endswith("/"):
                member.type = tarfile.DIRTYPE
                tar.addfile(member)
                return
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        add("manifest.json", json.dumps(manifest).encode())
        add("runtime/goals/example/state.md", b"retained source")
        add("runtime/goals/example/task-leases/settled.json", b"retained lease")
        for index in range(unrelated):
            # Tar names need no extraction. Each segment remains portable;
            # many unrelated deep paths reproduce an oversized member proof.
            add(f"runtime/other/{index}/" + (("s" * 250 + "/") * 16) + "file")
        if unsafe:
            add("runtime/other/../escape")
        for name in extra_members:
            add(name)
    manifest["execution"] = {
        "archive_path": str(archive),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_scoped_proof_preserves_complete_artifact_checksums_below_transport_limit(tmp_path, monkeypatch):
    manifest = backup(tmp_path, unrelated=4500)
    before = manifest.read_bytes()
    scans = []
    open_archive = tarfile.open

    def observe_scan(*args, **kwargs):
        archive = open_archive(*args, **kwargs)
        scans.append(archive)
        return archive

    monkeypatch.setattr(tarfile, "open", observe_scan)
    full = read_cold_source_backup(manifest)
    assert len(json.dumps(full).encode()) > 16 * 1024 * 1024
    scoped = read_cold_source_backup(manifest, source_paths=[tmp_path / "runtime/goals/example"])
    assert len(json.dumps(scoped).encode()) < 4096
    assert scoped == {**full, "members": full["members"][:2]}
    assert manifest.read_bytes() == before
    exact = read_cold_source_backup(manifest, source_paths=[tmp_path / "runtime/goals/example/state.md"])
    assert exact == {**full, "members": full["members"][:1]}
    # Streaming data is insufficient if tarfile still caches every header.
    # The selected output may grow; unrelated header retention must not.
    assert len(scans) == 3
    assert max(len(scan.members) for scan in scans) <= 1


@pytest.mark.parametrize("names", [("A.txt", "a.txt"), ("caf\u00e9.txt", "cafe\u0301.txt")])
def test_trusted_witness_preserves_distinct_source_names(tmp_path, names):
    paths = tuple(f"runtime/other/{name}" for name in names)
    manifest = backup(tmp_path, extra_members=paths)
    full = read_cold_source_backup(manifest)
    assert [member["archive_path"] for member in full["members"][-2:]] == list(paths)
    scoped = read_cold_source_backup(manifest,
        source_paths=[tmp_path / "runtime/goals/example/state.md"])
    assert scoped["members"] == full["members"][:1]


@pytest.mark.parametrize("paths", [
    ("runtime/other/file", "runtime/other/file"),
    ("runtime/other/./file", "runtime/other/file"),
    ("runtime/other", "runtime/other/file"),
    ("runtime/other/file", "runtime/other"),
])
def test_trusted_witness_rejects_real_path_conflicts_in_both_orders(tmp_path, paths):
    manifest = backup(tmp_path, extra_members=paths)
    with pytest.raises(ValueError, match="member_unsafe"):
        read_cold_source_backup(manifest, source_paths=[tmp_path / "runtime/goals/example"])


def test_trusted_witness_accepts_directory_header_after_its_children(tmp_path):
    manifest = backup(tmp_path,
        extra_members=("runtime/other/file", "runtime/other/"))
    witness = read_cold_source_backup(manifest)
    assert witness["members"][-1]["archive_path"] == "runtime/other/file"


def test_index_capacity_failure_retains_original_backup_and_cleans_workspace(tmp_path, monkeypatch):
    manifest = backup(tmp_path, unrelated=30)
    archive = Path(json.loads(manifest.read_text())["execution"]["archive_path"])
    original = manifest.read_bytes(), archive.read_bytes()
    connect = sqlite3.connect

    def limited_index(path):
        assert Path(path).parent.name.startswith(".cold-import-index-")
        database = connect(path)
        database.execute("PRAGMA max_page_count=2")
        return database

    monkeypatch.setattr(sqlite3, "connect", limited_index)
    with pytest.raises(ColdSourceBackupError, match="workspace_capacity") as failure:
        read_cold_source_backup(manifest)
    assert failure.value.available_bytes >= 0
    assert (manifest.read_bytes(), archive.read_bytes()) == original
    assert list((tmp_path / ".cold-import-work").iterdir()) == []


def test_scoping_does_not_hide_unsafe_unrelated_members_or_changed_artifact(tmp_path):
    manifest = backup(tmp_path, unsafe=True)
    with pytest.raises(ValueError, match="member_unsafe"):
        read_cold_source_backup(manifest, source_paths=[tmp_path / "runtime/goals/example"])
    manifest = backup(tmp_path)
    archive = Path(json.loads(manifest.read_text())["execution"]["archive_path"])
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="archive_changed"):
        read_cold_source_backup(manifest, source_paths=[tmp_path / "runtime/goals/example"])


def test_streaming_witness_has_no_old_expanded_byte_ceiling(tmp_path):
    archive = tmp_path / "large-expanded.tar.gz"
    source = tmp_path / "source.bin"
    source.touch()
    with source.open("r+b") as handle:
        handle.truncate(513 * 1024 * 1024)
    manifest = {
        "schema_version": "loopx_state_backup_v0", "dry_run": False,
        "execute_requested": True, "runtime_root": str(tmp_path / "runtime"),
        "project": str(tmp_path / "project"), "configuration_source_registry": None,
        "included": [{"source_path": str(source), "archive_path": "authority-source/0000"}],
    }
    with tarfile.open(archive, "w:gz") as tar:
        embedded = json.dumps(manifest).encode()
        member = tarfile.TarInfo("manifest.json")
        member.size = len(embedded)
        tar.addfile(member, io.BytesIO(embedded))
        tar.add(source, arcname="authority-source/0000")
    manifest["execution"] = {
        "archive_path": str(archive),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archive_size_bytes": archive.stat().st_size,
    }
    path = tmp_path / "large-expanded.manifest.json"
    path.write_text(json.dumps(manifest))

    witness = read_cold_source_backup(path, source_paths=[source])
    assert witness["logical_size_bytes"] > 512 * 1024 * 1024
    expected = hashlib.sha256()
    block = b"\0" * (1024 * 1024)
    for _ in range(513):
        expected.update(block)
    assert witness["members"] == [{
        "archive_path": "authority-source/0000", "sha256": expected.hexdigest()}]


def test_streaming_witness_rejects_symlink_ancestor_and_cycle(tmp_path):
    def unsafe_manifest(name: str, members: list[tarfile.TarInfo]) -> Path:
        archive = tmp_path / f"{name}.tar.gz"
        embedded = {
            "schema_version": "loopx_state_backup_v0", "dry_run": False,
            "execute_requested": True, "runtime_root": str(tmp_path / "runtime"),
            "project": str(tmp_path / "project"), "configuration_source_registry": None,
            "included": [{"source_path": str(tmp_path), "archive_path": "root"}],
        }
        with tarfile.open(archive, "w:gz") as tar:
            data = json.dumps(embedded).encode()
            manifest_member = tarfile.TarInfo("manifest.json")
            manifest_member.size = len(data)
            tar.addfile(manifest_member, io.BytesIO(data))
            for member in members:
                tar.addfile(member, io.BytesIO(b"x") if member.isfile() else None)
        embedded["execution"] = {"archive_path": str(archive),
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(embedded))
        return path

    link = tarfile.TarInfo("root/link")
    link.type, link.linkname = tarfile.SYMTYPE, "target"
    descendant = tarfile.TarInfo("root/link/file")
    descendant.size = 1
    with pytest.raises(ValueError, match="member_unsafe"):
        read_cold_source_backup(unsafe_manifest("ancestor", [link, descendant]),
            source_paths=[tmp_path])

    first = tarfile.TarInfo("root/a")
    first.type, first.linkname = tarfile.SYMTYPE, "b"
    second = tarfile.TarInfo("root/b")
    second.type, second.linkname = tarfile.SYMTYPE, "a"
    with pytest.raises(ValueError, match="link_unsafe"):
        read_cold_source_backup(unsafe_manifest("cycle", [first, second]),
            source_paths=[tmp_path])
