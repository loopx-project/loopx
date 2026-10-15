"""Bounded tar/filesystem adapter for the typed, inert recovery audit owner.

No restored Python, plugin, shell, credential or live registry is loaded.
Semantic manifest agreement and owner readback remain in TypeScript.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import unicodedata
import zlib
from typing import Any, BinaryIO, Iterator, NoReturn

from .control_plane.effect_runtime import _node_executable, effect_runtime_result, effect_runtime_request_scope

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_EXPANSION_RATIO = 2000
_CHUNK = 256 * 1024
_OWNER_TIMEOUT_SECONDS = 90


class StateRecoveryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise StateRecoveryError(code)


def _json(data: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, "manifest_duplicate_key")
            result[key] = value
        return result

    def constant(_value: str) -> None:
        raise StateRecoveryError("manifest_invalid_json")

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise StateRecoveryError("manifest_invalid_json") from exc
    if not isinstance(value, dict):
        raise StateRecoveryError("manifest_invalid_json")
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        _require(depth <= 64, "recovery_capacity_exceeded")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def _runtime(method: str, request: dict[str, Any]) -> dict[str, Any]:
    value = effect_runtime_result(method, request, timeout=90,
        large_local_snapshot=method in {"state.recovery.archive.verify", "state.recovery.manifest.verify"})
    if not isinstance(value, dict):
        raise StateRecoveryError("recovery_owner_result_invalid")
    if value.get("ok") is not True:
        code = value.get("error_code")
        raise StateRecoveryError(code if isinstance(code, str) else "recovery_owner_result_invalid")
    return value


def _audit_owners(request: dict[str, Any]) -> dict[str, Any]:
    command = [_node_executable(), "--no-warnings", "--max-old-space-size=512",
               "--experimental-sqlite", "--experimental-strip-types",
               str(Path(__file__).parent / "control_plane/state_recovery_worker.ts")]
    try:
        completed = subprocess.run(command, input=json.dumps(request), text=True, encoding="utf-8",
                                   capture_output=True, timeout=_OWNER_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills and waits for this child only, never the daemon.
        raise StateRecoveryError("recovery_owner_timeout") from exc
    _require(bool(completed.stdout.strip()), "recovery_owner_unavailable")
    _require(len(completed.stdout) <= 2 * 1024 * 1024, "recovery_owner_result_invalid")
    result = _json(completed.stdout.encode())
    if result.get("ok") is not True:
        raise StateRecoveryError(str(result.get("error_code") or "recovery_owner_unavailable"))
    _require(completed.returncode == 0, "recovery_owner_unavailable")
    return result


@contextmanager
def _regular_input(path: Path, limit: int) -> Iterator[BinaryIO]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise StateRecoveryError("recovery_input_unavailable") from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        _require(stat.S_ISREG(info.st_mode), "recovery_input_not_regular")
        _require(info.st_uid == os.getuid() and not info.st_mode & 0o077, "recovery_input_not_private")
        _require(0 < info.st_size <= limit, "recovery_capacity_exceeded")
        yield handle


def _sha(handle: BinaryIO) -> tuple[str, int]:
    handle.seek(0)
    digest, count = hashlib.sha256(), 0
    while chunk := handle.read(_CHUNK):
        count += len(chunk)
        _require(count <= MAX_ARCHIVE_BYTES, "recovery_capacity_exceeded")
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest(), count


class _BoundedGzip(io.RawIOBase):
    def __init__(self, handle: BinaryIO, archive_size: int):
        super().__init__()
        self.source = gzip.GzipFile(fileobj=handle, mode="rb")
        self.limit = min(MAX_EXPANDED_BYTES, archive_size * MAX_EXPANSION_RATIO)
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        _require(size >= 0, "archive_unbounded_read")
        data = self.source.read(min(size, self.limit - self.total + 1))
        self.total += len(data)
        _require(self.total <= self.limit, "recovery_capacity_exceeded")
        return data


class _RecoveryTarInfo(tarfile.TarInfo):
    def _reject_sparse(self, *_args: object) -> NoReturn:
        raise StateRecoveryError("archive_sparse_unsupported")

    # The stdlib invokes these hooks before yielding the final member. Refuse
    # unsupported extents before their metadata/body is decoded or expanded.
    _proc_gnusparse_00 = _reject_sparse
    _proc_gnusparse_01 = _reject_sparse
    _proc_gnusparse_10 = _reject_sparse

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo | None:
        _require(self.type != tarfile.GNUTYPE_SPARSE, "archive_sparse_unsupported")
        # Bound extended metadata before tarfile allocates/decodes its body.
        if self.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                         tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            _require(self.size <= 128 * 1024, "archive_metadata_too_large")
            if isinstance(archive, _RecoveryTarFile):
                archive.recovery_metadata_bytes += self.size
                _require(archive.recovery_metadata_bytes <= MAX_MANIFEST_BYTES, "archive_metadata_too_large")
        # The stdlib hook is absent from typeshed; keep its real bound signature
        # here rather than weakening the public stream/return types.
        process: Callable[[tarfile.TarFile], tarfile.TarInfo | None] = getattr(super(), "_proc_member")
        return process(archive)


class _RecoveryTarFile(tarfile.TarFile):
    recovery_metadata_bytes = 0


def _member_name(raw: str) -> str:
    _require(bool(raw) and len(raw) <= 1024 and "\\" not in raw and "\x00" not in raw,
             "archive_path_invalid")
    path = PurePosixPath(raw)
    _require(bool(path.parts) and len(path.parts) <= 64 and not path.is_absolute()
             and ".." not in path.parts and ":" not in path.parts[0]
             and all(len(part.encode("utf-8")) <= 255 for part in path.parts),
             "archive_path_invalid")
    normalized = str(path)
    _require(normalized not in ("", "."), "archive_path_invalid")
    return normalized


def _physical_ancestors(destination: Path) -> None:
    _require(destination.is_absolute() and str(destination) == os.path.normpath(destination),
             "recovery_destination_invalid")
    for parent in destination.parents:
        try:
            info = parent.lstat()
        except OSError as exc:
            raise StateRecoveryError("recovery_destination_parent_unavailable") from exc
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),
                 "recovery_destination_parent_not_physical")


def _physical_parent(destination: Path) -> None:
    _physical_ancestors(destination)
    _require(not destination.exists() and not destination.is_symlink(), "recovery_destination_exists")


def _directories(root: Path, parts: tuple[str, ...]) -> Path:
    current = root
    for part in parts:
        current /= part
        current.mkdir(mode=0o700, exist_ok=True)
        _require(current.is_dir() and not current.is_symlink(), "archive_parent_not_directory")
    return current


def _check_links(links: list[tuple[str, str]]) -> None:
    by_name = {unicodedata.normalize("NFC", name).casefold(): target for name, target in links}
    for name, target in links:
        pending = list(PurePosixPath(name).parent.parts) + list(PurePosixPath(target).parts)
        resolved: list[str] = []
        hops = 0
        while pending:
            part = pending.pop(0)
            if part == "..":
                _require(bool(resolved), "archive_link_escapes")
                resolved.pop()
            elif part != ".":
                resolved.append(part)
                key = unicodedata.normalize("NFC", "/".join(resolved)).casefold()
                if key in by_name:
                    hops += 1
                    _require(hops <= 40, "archive_link_cycle")
                    resolved.pop()
                    pending = list(PurePosixPath(by_name[key]).parts) + pending


def _scan(handle: BinaryIO, archive_size: int, output: Path | None = None) -> dict[str, Any]:
    handle.seek(0)
    stream = _BoundedGzip(handle, archive_size)
    names: dict[str, str] = {}
    folded: dict[str, str] = {}
    ancestors: set[str] = set()
    links: list[tuple[str, str]] = []
    files, sqlite_members, standalone = [], [], []
    embedded: dict[str, Any] | None = None
    logical = 0
    try:
        with _RecoveryTarFile.open(fileobj=stream, mode="r|", tarinfo=_RecoveryTarInfo) as archive:
            for member in archive:
                name = _member_name(member.name)
                key = unicodedata.normalize("NFC", name).casefold()
                _require(name not in names and key not in folded, "archive_name_collision")
                _require(len(names) < MAX_MEMBERS, "recovery_capacity_exceeded")
                _require(member.sparse is None, "archive_sparse_unsupported")
                kind = "file" if member.isfile() else "directory" if member.isdir() else "symlink" if member.issym() else None
                _require(kind is not None, "archive_member_type_unsupported")
                names[name], folded[key] = str(kind), name
                for parent in PurePosixPath(name).parents:
                    parent_key = unicodedata.normalize("NFC", str(parent)).casefold()
                    ancestors.add(parent_key)
                    _require(names.get(folded.get(parent_key, ""), "directory") == "directory",
                             "archive_parent_not_directory")
                if kind != "directory":
                    _require(key not in ancestors, "archive_parent_not_directory")
                if kind == "symlink":
                    target = member.linkname
                    _require(bool(target) and len(target) <= 1024 and "\x00" not in target
                             and not PurePosixPath(target).is_absolute() and "\\" not in target,
                             "archive_link_escapes")
                    resolved = os.path.normpath(str(PurePosixPath(name).parent / target))
                    _require(resolved != ".." and not resolved.startswith("../"), "archive_link_escapes")
                    links.append((name, target))
                    continue
                if output is not None:
                    _directories(output, PurePosixPath(name).parts if kind == "directory" else PurePosixPath(name).parts[:-1])
                if kind == "directory":
                    continue
                logical += member.size
                _require(0 <= member.size <= MAX_EXPANDED_BYTES and logical <= MAX_EXPANDED_BYTES,
                         "recovery_capacity_exceeded")
                if name == "manifest.json":
                    _require(member.size <= MAX_MANIFEST_BYTES, "archive_metadata_too_large")
                source = archive.extractfile(member)
                if source is None:
                    raise StateRecoveryError("archive_member_unreadable")
                data, prefix = bytearray(), bytearray()
                digest, size = hashlib.sha256(), 0
                destination = None
                try:
                    if output is not None:
                        destination = open(output / name, "xb")
                        os.chmod(output / name, 0o600)
                    while chunk := source.read(_CHUNK):
                        size += len(chunk)
                        _require(size <= member.size, "archive_member_size_mismatch")
                        digest.update(chunk)
                        prefix.extend(chunk[:max(0, 100 - len(prefix))])
                        if name == "manifest.json":
                            data.extend(chunk)
                        if destination is not None:
                            destination.write(chunk)
                    _require(size == member.size, "archive_member_size_mismatch")
                    if destination is not None:
                        destination.flush()
                        os.fsync(destination.fileno())
                finally:
                    source.close()
                    if destination is not None:
                        destination.close()
                if name == "manifest.json":
                    embedded = _json(bytes(data))
                else:
                    files.append({"archive_path": name, "sha256": digest.hexdigest(), "size_bytes": size})
                if prefix[:16] == b"SQLite format 3\0":
                    sqlite_members.append(name)
                    if prefix[18:20] == b"\x01\x01":
                        standalone.append(name)
            # tar EOF must not conceal an appended archive or unparsed payload.
            while trailing := archive.fileobj.read(_CHUNK):
                _require(not trailing.strip(b"\0"), "archive_trailing_data")
        # Consume gzip EOF too: truncated CRC/trailing expansion is not a valid tar.
        while stream.read(_CHUNK):
            pass
    except StateRecoveryError:
        raise
    except (tarfile.TarError, EOFError, gzip.BadGzipFile, OSError, zlib.error,
            ValueError, OverflowError, KeyError, RecursionError) as exc:
        raise StateRecoveryError("archive_unreadable") from exc
    finally:
        stream.source.close()
    _require(embedded is not None, "embedded_manifest_missing")
    _check_links(links)
    # Links are materialized last and no member may live beneath a link.
    if output is not None:
        for name, target in links:
            _directories(output, PurePosixPath(name).parts[:-1])
            os.symlink(target, output / name)
    return {"embedded_manifest": embedded, "files": files, "logical_bytes": logical,
            "sqlite_members": sqlite_members, "standalone_sqlite_members": standalone,
            "entry_count": len(names)}


@effect_runtime_request_scope()
def verify_state_backup(
    *, archive: Path, manifest: Path, destination: Path, execute: bool = False,
) -> dict[str, Any]:
    """Preview or publish one inert audit. This API has no live-adoption operation."""
    started = time.monotonic_ns()
    _require(os.name == "posix", "recovery_platform_unqualified")
    _physical_parent(destination)
    with _regular_input(manifest, MAX_MANIFEST_BYTES) as manifest_handle:
        manifest_bytes = manifest_handle.read(MAX_MANIFEST_BYTES + 1)
    _require(len(manifest_bytes) <= MAX_MANIFEST_BYTES, "recovery_capacity_exceeded")
    external = _json(manifest_bytes)
    with _regular_input(archive, MAX_ARCHIVE_BYTES) as archive_handle:
        archive_sha, archive_size = _sha(archive_handle)
        _runtime("state.recovery.archive.verify", {"external_manifest": external,
            "archive_sha256": archive_sha, "archive_size_bytes": archive_size})
        observation = _scan(archive_handle, archive_size)
        verified = _runtime("state.recovery.manifest.verify", {
            "external_manifest": external, "embedded_manifest": observation["embedded_manifest"],
            "archive_sha256": archive_sha, "archive_size_bytes": archive_size,
            "external_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "files": observation["files"],
            "sqlite_member_count": len(observation["sqlite_members"]),
        })
        _require(shutil.disk_usage(destination.parent).free >= observation["logical_bytes"] + MAX_MANIFEST_BYTES,
                 "recovery_disk_capacity")
        if not execute:
            return {**verified, "status": "planned", "written": False,
                    "logical_bytes": observation["logical_bytes"], "entry_count": observation["entry_count"],
                    "archive_processing_ms": (time.monotonic_ns() - started) // 1_000_000,
                    "activation_eligible": False}
        staging = Path(tempfile.mkdtemp(prefix=".loopx-recovery-", dir=destination.parent))
        reserved = False
        try:
            data = staging / "data"
            data.mkdir(mode=0o700)
            copied = _scan(archive_handle, archive_size, data)
            _require(copied == observation and _sha(archive_handle) == (archive_sha, archive_size),
                     "recovery_input_changed")
            audit = _audit_owners({"data_root": str(data), "verified": verified,
                "measurements": {"archive_bytes": archive_size,
                    "regular_file_bytes": copied["logical_bytes"],
                    "archive_processing_ms": (time.monotonic_ns() - started) // 1_000_000},
                "sqlite_members": observation["sqlite_members"],
                "standalone_sqlite_members": observation["standalone_sqlite_members"]})
            audit = _runtime("state.recovery.audit.readback", audit)
            for member in observation["files"]:
                with (data / member["archive_path"]).open("rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                _require(digest == member["sha256"], "recovery_owner_mutated_source")
            audit_path = staging / "recovery-audit.json"
            with audit_path.open("x", encoding="utf-8") as handle:
                os.chmod(audit_path, 0o600)
                json.dump(audit, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            _require(_json(audit_path.read_bytes()) == audit, "recovery_audit_readback_mismatch")
            _physical_parent(destination)
            destination.mkdir(mode=0o700)
            reserved = True
            os.rename(staging, destination)
            reserved = False
            return read_state_recovery_audit(destination)
        finally:
            try:
                if reserved:
                    # Never recursively remove a destination occupied meanwhile.
                    destination.rmdir()
            finally:
                shutil.rmtree(staging, ignore_errors=True)


def read_state_recovery_audit(destination: Path) -> dict[str, Any]:
    path = destination / "recovery-audit.json"
    _physical_ancestors(path)
    with _regular_input(path, MAX_MANIFEST_BYTES) as handle:
        audit = _json(handle.read(MAX_MANIFEST_BYTES + 1))
    return _runtime("state.recovery.audit.readback", audit)


def render_state_recovery_markdown(payload: dict[str, Any]) -> str:
    if payload.get("ok") is not True:
        return f"Backup inspection failed: {payload.get('error_code', 'recovery_unavailable')}. No execution authority was granted."
    if payload.get("status") == "planned":
        return "\n".join([
            "Archive and manifest checks passed. No copy has been created and owner records have not been inspected.",
            f"Saved files: {payload['file_count']}; logical bytes: {payload['logical_bytes']}",
            "Repeat with --execute to create and inspect a new inert copy; this never activates a Goal.",
        ])
    summary = payload.get("summary", {})
    lines = [
        f"Backup inspection: {payload['status']}",
        f"Recovery: {payload['recovery_id']}",
        f"Readable: {summary.get('readable', 0)}; historical only: {summary.get('historical_only', 0)}; unknown: {summary.get('unknown', 0)}",
        "The copy is inert. Goal execution and live settings were not activated.",
    ]
    measurements = payload.get("measurements")
    if isinstance(measurements, dict):
        lines.append(f"Archive: {measurements['archive_bytes']} bytes; regular files: {measurements['regular_file_bytes']} bytes; "
                     f"archive checks/copy: {measurements['archive_processing_ms']} ms; owner reads: {measurements['owner_read_ms']} ms.")
    return "\n".join(lines)
