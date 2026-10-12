"""Host IO for explicit result files; result_publication.ts owns the contract."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile

from .inbox import _root
from ..content_digest import BARE_SHA256_PATTERN
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


MAX_FILE_BYTES = 30 * 1024 * 1024


def result_file_path(root, attachment):
    digest = attachment.get("sha256")
    if not isinstance(digest, str) or not BARE_SHA256_PATTERN.fullmatch(digest):
        raise ValueError("invalid result file digest")
    return _root(root) / "result-files" / digest


def read_result_file(root, attachment):
    """Check the immutable snapshot again before a provider consumes its bytes."""
    raw = _read_regular(result_file_path(root, attachment))
    if len(raw) != attachment["size"] or hashlib.sha256(raw).hexdigest() != attachment["sha256"]:
        raise ValueError("result file snapshot changed")
    return raw


def _read_regular(path):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
            raise ValueError("result file must be a bounded regular file")
        raw = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if not raw or len(raw) > MAX_FILE_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("result file is empty, oversized or changed during read")
    return raw


def _workspace_file(workspace, ref):
    """No symlink component may redirect a file outside the qualified workspace."""
    path = workspace / ref
    for parent in [path, *path.parents]:
        if parent == workspace:
            break
        if parent.is_symlink():
            raise ValueError("result file symlinks are not supported")
    resolved = path.resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError("result file is outside the workspace")
    # On POSIX, pin every parent directory before opening the leaf. Checking
    # resolve() alone leaves a parent-symlink replacement window.
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        directory = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = Path(ref).parts
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                    raise ValueError("result file must be a bounded regular file")
                raw = stream.read(MAX_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
            if not raw or len(raw) > MAX_FILE_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("result file changed during read or exceeds its bound")
            return raw
        finally:
            os.close(directory)
    raise ValueError("result file snapshots require no-follow directory opens on this host")


def snapshot_result_files(root, scope, refs, *, workspace=None):
    try:
        refs = effect_runtime_result("collaboration.result.attachment_refs", {
            "refs": refs,
            "workspace_current": not scope.exact or scope.caller_goal_ref == scope.current_goal_ref,
        })["refs"]
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    selected = Path(scope.goal["repo"]).resolve()
    if workspace is not None and Path(workspace).resolve() != selected:
        from ...project_alias import resolve_canonical_project_alias

        alias = resolve_canonical_project_alias(Path(workspace), goal_id=scope.goal_id, global_registry=scope.registry_path)
        if alias.get("applied") and Path(alias["canonical_project"]).resolve() == selected:
            selected = Path(workspace).resolve()
    result, total = [], 0
    for ref in refs:
        raw = _workspace_file(selected, ref)
        total += len(raw)
        if total > 60 * 1024 * 1024:
            raise ValueError("result attachments exceed the aggregate bound")
        attachment = {"ref": ref, "name": Path(ref).name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        path = result_file_path(root, attachment)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            read_result_file(root, attachment)
        else:
            fd, temporary = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        result.append(attachment)
    return result
