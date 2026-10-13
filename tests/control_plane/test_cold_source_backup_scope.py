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
            add(f"runtime/other/{index}/" + "segment/" * 512 + "file")
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
