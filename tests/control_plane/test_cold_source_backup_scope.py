"""Large unrelated backup inventories do not inflate cold-import transport."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from loopx.control_plane.coordination.cold_source_backup import read_cold_source_backup


def backup(tmp_path: Path, unrelated: int = 0, unsafe: bool = False) -> Path:
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
    manifest["execution"] = {
        "archive_path": str(archive),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_scoped_proof_preserves_complete_artifact_checksums_below_transport_limit(tmp_path):
    manifest = backup(tmp_path, unrelated=4500)
    before = manifest.read_bytes()
    full = read_cold_source_backup(manifest)
    assert len(json.dumps(full).encode()) > 16 * 1024 * 1024
    scoped = read_cold_source_backup(manifest, source_paths=[tmp_path / "runtime/goals/example"])
    assert len(json.dumps(scoped).encode()) < 4096
    assert scoped == {**full, "members": full["members"][:2]}
    assert manifest.read_bytes() == before
    exact = read_cold_source_backup(manifest, source_paths=[tmp_path / "runtime/goals/example/state.md"])
    assert exact == {**full, "members": full["members"][:1]}


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
