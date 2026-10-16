"""Trusted local backup witness for cold authority migration.

The migration path has no product-level archive or expanded-byte ceiling. It
streams archive bytes, keeps duplicate-name state on disk and returns only the
source-owner-selected member digests. Generic browser/inert recovery keeps its
independent bounded inspection profile in ``state_recovery_io``.
"""

from __future__ import annotations

from contextlib import closing
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tarfile
import tempfile
from typing import Any, BinaryIO

from ...state_backup import (
    LOCAL_AUTHORITY_MIGRATION_BACKUP_PROFILE,
    build_scoped_state_backup_plan,
    execute_state_backup_plan,
)


WITNESS_SCHEMA = "loopx_state_backup_source_witness_v1"
_CHUNK = 1024 * 1024
_MAX_MEMBER_NAME_BYTES = 16 * 1024


class ColdSourceBackupError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        required_bytes: int | None = None,
        available_bytes: int | None = None,
    ) -> None:
        self.code = code
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes
        super().__init__(code)


def _backup_segment(value: str, fallback: str) -> str:
    compact = "".join(character if character.isascii() and (
        character.isalnum() or character in "._-") else "-" for character in value)
    compact = compact.strip("-._") or fallback
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{compact[:48]}-{digest}"


def _sha(handle: BinaryIO) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    handle.seek(0)
    for block in iter(lambda: handle.read(_CHUNK), b""):
        size += len(block)
        digest.update(block)
    handle.seek(0)
    return digest.hexdigest(), size


def _open_regular(path: Path) -> tuple[BinaryIO, os.stat_result]:
    try:
        observed = path.lstat()
        if not stat.S_ISREG(observed.st_mode):
            raise ColdSourceBackupError("cold_import_backup_input_unsafe")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except ColdSourceBackupError:
        raise
    except OSError as exc:
        raise ColdSourceBackupError("cold_import_backup_input_unavailable") from exc
    handle = os.fdopen(fd, "rb")
    info = os.fstat(handle.fileno())
    if (not stat.S_ISREG(info.st_mode)
            or (observed.st_dev, observed.st_ino) != (info.st_dev, info.st_ino)):
        handle.close()
        raise ColdSourceBackupError("cold_import_backup_input_unsafe")
    return handle, info


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return all(
        getattr(before, field) == getattr(after, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def _member_name(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or ".." in path.parts
        or len(value.encode("utf-8")) > _MAX_MEMBER_NAME_BYTES
        or any(not part or len(part.encode("utf-8")) > 255 for part in path.parts)
    ):
        raise ColdSourceBackupError("cold_import_backup_member_unsafe")
    normalized = str(path)
    if normalized in ("", "."):
        raise ColdSourceBackupError("cold_import_backup_member_unsafe")
    return normalized


def _safe_link(name: str, target: str, *, hardlink: bool) -> str:
    if not target or "\\" in target or "\x00" in target or PurePosixPath(target).is_absolute():
        raise ColdSourceBackupError("cold_import_backup_link_unsafe")
    if hardlink:
        return _member_name(target)
    resolved = os.path.normpath(str(PurePosixPath(name).parent / target))
    if resolved == ".." or resolved.startswith("../"):
        raise ColdSourceBackupError("cold_import_backup_link_unsafe")
    return _member_name(resolved)


def _member_key(name: str) -> bytes:
    # This witness never extracts paths. Preserve distinctions that are legal
    # on the source filesystem; generic recovery owns portability policy.
    return name.encode("utf-8")


def _member_marker(database: sqlite3.Connection, key: bytes) -> bytes | None:
    row = database.execute("SELECT marker FROM members WHERE name = ?", (key,)).fetchone()
    return None if row is None else row[0]


def _record_member(database: sqlite3.Connection, key: bytes, marker: bytes) -> None:
    database.execute("INSERT INTO members(name, marker) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET marker = excluded.marker", (key, marker))


def _require_directory_ancestors(database: sqlite3.Connection, name: str) -> None:
    parts = PurePosixPath(name).parts
    prefix = parts[0]
    for part in parts[1:]:
        key = _member_key(prefix)
        marker = _member_marker(database, key)
        if marker is None:
            _record_member(database, key, b"I")  # Inferred from an earlier child.
        elif marker not in (b"D", b"I"):
            raise ColdSourceBackupError("cold_import_backup_member_unsafe")
        prefix += "/" + part


def _require_acyclic_symlink(database: sqlite3.Connection, name: str, target: str) -> None:
    current = _member_key(target)
    seen = {_member_key(name)}
    while True:
        if current in seen:
            raise ColdSourceBackupError("cold_import_backup_link_unsafe")
        seen.add(current)
        marker = _member_marker(database, current)
        if marker is None or marker == b"L:" or not marker.startswith(b"L:"):
            return
        current = marker[2:]


def _selected_prefixes(manifest: dict[str, Any], source_paths: list[Path] | None) -> list[str] | None:
    if source_paths is None:
        return None
    prefixes: list[str] = []
    included = manifest.get("included")
    if not isinstance(included, list):
        raise ColdSourceBackupError("cold_import_backup_manifest_invalid")
    for source in source_paths:
        source = source.expanduser().absolute()
        for item in included:
            if not isinstance(item, dict):
                continue
            try:
                suffix = source.relative_to(Path(str(item.get("source_path") or "")))
            except ValueError:
                continue
            prefix = PurePosixPath(str(item.get("archive_path") or ""))
            candidate = str(prefix / suffix.as_posix()) if suffix.parts else str(prefix)
            if candidate not in prefixes:
                prefixes.append(candidate)
    return prefixes


def _matches(name: str, prefixes: list[str] | None) -> bool:
    return prefixes is None or any(name == prefix or name.startswith(prefix + "/") for prefix in prefixes)


def _scan_archive(
    archive: BinaryIO, *, selected: list[str] | None, workspace: Path
) -> tuple[dict[str, Any], list[dict[str, Any]], int, int, int]:
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace_info = workspace.lstat()
    if not stat.S_ISDIR(workspace_info.st_mode):
        raise ColdSourceBackupError("cold_import_backup_workspace_unsafe")
    try:
        with tempfile.TemporaryDirectory(prefix=".cold-import-index-", dir=workspace) as temporary:
            database_path = Path(temporary) / "members.sqlite3"
            embedded: dict[str, Any] | None = None
            members: list[dict[str, Any]] = []
            member_count = logical_bytes = 0
            archive.seek(0)
            # One disposable transaction avoids per-prefix durable writes.
            # This scratch index is not a Goal authority or recovery store.
            with closing(sqlite3.connect(database_path)) as database:
                database.execute("CREATE TABLE members (name BLOB PRIMARY KEY, marker BLOB NOT NULL) WITHOUT ROWID")
                with tarfile.open(fileobj=archive, mode="r|gz") as tar:
                    # r|gz streams bytes but still caches every TarInfo on
                    # supported Python versions. No random member lookup is
                    # needed: hardlink coverage already lives in the disk DB.
                    for member in iter(tar.next, None):
                        tar.members.clear()
                        name = _member_name(member.name)
                        key_bytes = _member_key(name)
                        prior = _member_marker(database, key_bytes)
                        if prior is not None and not (member.isdir() and prior == b"I"):
                            raise ColdSourceBackupError("cold_import_backup_member_unsafe")
                        _require_directory_ancestors(database, name)
                        member_count += 1
                        if member.isdir():
                            _record_member(database, key_bytes, b"D")
                            continue
                        if member.issym():
                            if selected is not None and _matches(name, selected):
                                target = _safe_link(name, member.linkname, hardlink=False)
                                _require_acyclic_symlink(database, name, target)
                                _record_member(database, key_bytes, b"L:" + _member_key(target))
                            else:
                                # A complete disaster-recovery archive may retain
                                # unrelated absolute links. This witness never
                                # extracts or grants byte coverage from them.
                                _record_member(database, key_bytes, b"L:")
                            continue
                        if member.islnk():
                            target = _safe_link(name, member.linkname, hardlink=True)
                            marker = _member_marker(database, _member_key(target))
                            if marker is None or not marker.startswith(b"F:"):
                                raise ColdSourceBackupError("cold_import_backup_link_unsafe")
                            digest = marker[2:].decode("ascii")
                            _record_member(database, key_bytes, marker)
                            if _matches(name, selected):
                                members.append({"archive_path": name, "sha256": digest})
                            continue
                        if not member.isfile():
                            raise ColdSourceBackupError("cold_import_backup_member_unsafe")
                        source = tar.extractfile(member)
                        if source is None:
                            raise ColdSourceBackupError("cold_import_backup_member_unreadable")
                        digest, size = hashlib.sha256(), 0
                        data = bytearray() if name == "manifest.json" else None
                        with source:
                            for block in iter(lambda: source.read(_CHUNK), b""):
                                size += len(block)
                                digest.update(block)
                                if data is not None:
                                    data.extend(block)
                        if size != member.size:
                            raise ColdSourceBackupError("cold_import_backup_member_unreadable")
                        logical_bytes += size
                        hexdigest = digest.hexdigest()
                        _record_member(database, key_bytes, b"F:" + hexdigest.encode("ascii"))
                        if name == "manifest.json":
                            try:
                                parsed = json.loads(bytes(data or b""))
                            except (UnicodeError, json.JSONDecodeError) as exc:
                                raise ColdSourceBackupError("cold_import_backup_manifest_invalid") from exc
                            if not isinstance(parsed, dict):
                                raise ColdSourceBackupError("cold_import_backup_manifest_invalid")
                            embedded = parsed
                        elif _matches(name, selected):
                            members.append({"archive_path": name, "sha256": hexdigest})
                database.commit()
            workspace_bytes = sum(path.stat().st_size for path in Path(temporary).iterdir())
    except (OSError, sqlite3.Error) as exc:
        if (getattr(exc, "errno", None) == errno.ENOSPC
                or getattr(exc, "sqlite_errorcode", None) == getattr(sqlite3, "SQLITE_FULL", 13)
                # Python 3.10 does not expose sqlite_errorcode.
                or isinstance(exc, sqlite3.OperationalError) and str(exc) == "database or disk is full"):
            available = shutil.disk_usage(workspace).free
            raise ColdSourceBackupError("cold_import_backup_workspace_capacity", available_bytes=available) from exc
        raise
    if embedded is None:
        raise ColdSourceBackupError("cold_import_backup_manifest_missing")
    return embedded, members, member_count, logical_bytes, workspace_bytes


def read_cold_source_backup(
    manifest_path: Path,
    *,
    source_paths: list[Path] | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Stream and witness a trusted local migration backup.

    Total archive bytes, expanded bytes and member count are limited by the
    actual filesystem and process resources, not fixed product constants.
    Structural metadata, paths and links remain fail-closed.
    """
    manifest_path = manifest_path.expanduser().absolute()
    manifest_handle, _ = _open_regular(manifest_path)
    with manifest_handle:
        manifest_bytes = manifest_handle.read()
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ColdSourceBackupError("cold_import_backup_manifest_invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "loopx_state_backup_v0"
        or manifest.get("dry_run") is not False
        or manifest.get("execute_requested") is not True
    ):
        raise ColdSourceBackupError("cold_import_backup_not_executed")
    execution = manifest.get("execution")
    if not isinstance(execution, dict):
        raise ColdSourceBackupError("cold_import_backup_not_executed")
    selected = _selected_prefixes(manifest, source_paths)
    archive_path = Path(str(execution.get("archive_path") or "")).expanduser().absolute()
    archive, before = _open_regular(archive_path)
    with archive:
        original_sha, archive_size = _sha(archive)
        if original_sha != execution.get("archive_sha256"):
            raise ColdSourceBackupError("cold_import_backup_archive_changed")
        declared_size = execution.get("archive_size_bytes")
        if declared_size is not None and declared_size != archive_size:
            raise ColdSourceBackupError("cold_import_backup_archive_changed")
        scan_workspace = (workspace or manifest_path.parent / ".cold-import-work").expanduser().absolute()
        embedded, members, member_count, logical_bytes, workspace_bytes = _scan_archive(
            archive, selected=selected, workspace=scan_workspace
        )
        after = os.fstat(archive.fileno())
        if not _same_file(before, after):
            raise ColdSourceBackupError("cold_import_backup_changed_retry")
    compared = (
        "schema_version",
        "backup_profile",
        "runtime_root",
        "project",
        "included",
        "configuration_source_registry",
    )
    if any(embedded.get(key) != manifest.get(key) for key in compared):
        raise ColdSourceBackupError("cold_import_backup_source_map_changed")
    manifest_readback, _ = _open_regular(manifest_path)
    with manifest_readback:
        if manifest_readback.read() != manifest_bytes:
            raise ColdSourceBackupError("cold_import_backup_changed_retry")
    included = manifest.get("included")
    if not isinstance(included, list):
        raise ColdSourceBackupError("cold_import_backup_manifest_invalid")
    return {
        "schema_version": WITNESS_SCHEMA,
        "verification_profile": "trusted_local_authority_migration_v1",
        "capacity_policy": "resource_bounded_without_fixed_total_byte_ceiling",
        "backup_profile": manifest.get("backup_profile", "complete_state_v0"),
        "archive_path": str(archive_path),
        "archive_sha256": original_sha,
        "archive_size_bytes": archive_size,
        "logical_size_bytes": logical_bytes,
        "scanned_member_count": member_count,
        "workspace_peak_bytes": workspace_bytes,
        "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "included": [
            {
                "source_path": item.get("source_path"),
                "archive_path": item.get("archive_path"),
            }
            for item in included
            if isinstance(item, dict)
        ],
        "members": members,
    }


def create_cold_source_backup(
    *,
    project: Path,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    operation_id: str,
    source_paths: list[Path],
) -> dict[str, Any]:
    """Create or replay one scoped migration backup and return its witness."""
    output = runtime_root / "backups" / "cold-import"
    sources = [
        (f"migration_source:{index}", source, f"authority-source/{index:04d}")
        for index, source in enumerate(source_paths)
    ]
    backup_id = (f"authority-{_backup_segment(goal_id, 'goal')}-"
        f"{_backup_segment(operation_id, 'operation')}")
    plan = build_scoped_state_backup_plan(
        project=project,
        runtime_root=runtime_root,
        output_dir=output,
        backup_id=backup_id,
        registry_path=registry_path,
        configuration_goal_ids=[goal_id],
        sources=sources,
        backup_profile=LOCAL_AUTHORITY_MIGRATION_BACKUP_PROFILE,
    )
    archive = Path(plan["archive_path"])
    manifest = Path(plan["manifest_path"])
    if archive.exists() != manifest.exists():
        raise ColdSourceBackupError("cold_import_backup_incomplete")
    if not archive.exists():
        try:
            execute_state_backup_plan(plan)
        except FileExistsError:
            # A concurrent replay may have published the same immutable pair.
            if not (archive.exists() and manifest.exists()):
                raise
    workspace = runtime_root / "authority-transition" / "migration-work" / goal_id
    return read_cold_source_backup(manifest, source_paths=source_paths, workspace=workspace)
