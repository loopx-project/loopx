"""Read actual backup archive members for the typed cold-import owner.

Python owns tar/Host IO only. Membership and source coverage decisions remain
in TypeScript. No extraction, restore, writer stop or execution grant occurs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import tarfile
from typing import Any, BinaryIO


def _sha(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def read_cold_source_backup(manifest_path: Path) -> dict[str, Any]:
    """Witness the existing backup format, including pre-member-list archives.

    A checksum of a manifest alone is insufficient. Read the same open archive,
    compare its embedded source map and hash the bytes actually stored in each
    regular member (including tar hardlinks). Symlinks grant no byte coverage.
    """
    manifest_path = manifest_path.expanduser().absolute()
    if not stat.S_ISREG(manifest_path.lstat().st_mode):
        raise ValueError("cold_import_backup_manifest_unsafe")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != "loopx_state_backup_v0"
        or manifest.get("dry_run") is not False or manifest.get("execute_requested") is not True):
        raise ValueError("cold_import_backup_not_executed")
    execution = manifest.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("cold_import_backup_not_executed")
    archive_path = Path(execution["archive_path"]).expanduser().absolute()
    if not stat.S_ISREG(archive_path.lstat().st_mode):
        raise ValueError("cold_import_backup_archive_unsafe")
    with archive_path.open("rb") as archive:
        original_sha = _sha(archive)
        if original_sha != execution.get("archive_sha256"):
            raise ValueError("cold_import_backup_archive_changed")
        archive.seek(0)
        members: list[dict[str, Any]] = []
        seen: set[str] = set()
        with tarfile.open(fileobj=archive, mode="r:gz") as tar:
            embedded: dict[str, Any] | None = None
            for member in tar:
                name = member.name
                if (name in seen or name.startswith("/") or "\\" in name
                    or ".." in PurePosixPath(name).parts):
                    raise ValueError("cold_import_backup_member_unsafe")
                seen.add(name)
                if name == "manifest.json":
                    if not member.isfile():
                        raise ValueError("cold_import_backup_manifest_unsafe")
                    stream = tar.extractfile(member)
                    if stream is None:
                        raise ValueError("cold_import_backup_manifest_missing")
                    with stream:
                        embedded = json.load(stream)
                    continue
                if member.isfile() or member.islnk():
                    stream = tar.extractfile(member)
                    if stream is None:
                        raise ValueError("cold_import_backup_member_unreadable")
                    with stream:
                        digest = _sha(stream)
                    members.append({"archive_path": name, "sha256": digest})
            if not isinstance(embedded, dict) or any(embedded.get(key) != manifest.get(key)
                for key in ("schema_version", "runtime_root", "project", "included", "configuration_source_registry")):
                raise ValueError("cold_import_backup_source_map_changed")
        archive.seek(0)
        if _sha(archive) != original_sha or manifest_path.read_bytes() != manifest_bytes:
            raise ValueError("cold_import_backup_changed_retry")
    return {"schema_version": "loopx_state_backup_source_witness_v0",
        "archive_path": str(archive_path), "archive_sha256": original_sha,
        "manifest_path": str(manifest_path), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "included": [{"source_path": item["source_path"], "archive_path": item["archive_path"]}
            for item in manifest["included"]], "members": members}
