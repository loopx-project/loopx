"""Private snapshot leaves must be exclusive regular files.

A hard link is still a regular file, so rejecting a symlink does not stop a
second name outside the private directory from sharing the inode.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.runtime.private_permissions import (
    protect_private_path,
    require_private_path,
)

_HARDLINK_UNSUPPORTED = {
    errno.EPERM,
    errno.EOPNOTSUPP,
    errno.ENOTSUP,
    errno.EXDEV,
    errno.ENOSYS,
}


def _private_directory(tmp_path: Path) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="loopx-effect-", dir=tmp_path))
    directory.chmod(0o700)
    protect_private_path(directory, directory=True)
    return directory


def _exclusive_leaf(tmp_path: Path, name: str = "response.json") -> tuple[Path, Path, bytes]:
    directory = _private_directory(tmp_path)
    leaf = directory / name
    payload = b'{"sentinel":"keep"}'
    leaf.write_bytes(payload)
    protect_private_path(leaf)
    return directory, leaf, payload


def _hard_linked_leaf(tmp_path: Path) -> tuple[Path, Path, bytes]:
    sentinel = tmp_path / "sentinel.json"
    original = b'{"sentinel":"keep"}'
    sentinel.write_bytes(original)
    directory = _private_directory(tmp_path)
    link = directory / "response.json"
    try:
        os.link(sentinel, link)
    except OSError as exc:
        if exc.errno in _HARDLINK_UNSUPPORTED:
            pytest.skip("this host cannot create a hard link")
        raise
    protect_private_path(link)
    info = link.lstat()
    external = sentinel.lstat()
    assert not stat.S_ISLNK(info.st_mode)
    assert stat.S_ISREG(info.st_mode)
    assert info.st_ino == external.st_ino and info.st_dev == external.st_dev
    assert info.st_nlink == 2
    assert sentinel.read_bytes() == original
    return sentinel, link, original


def test_exclusive_private_leaf_is_admitted(tmp_path: Path) -> None:
    _directory, leaf, payload = _exclusive_leaf(tmp_path)
    require_private_path(leaf)
    descriptor = os.open(leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        require_private_path(leaf, descriptor=descriptor)
    finally:
        os.close(descriptor)
    assert leaf.read_bytes() == payload
    assert leaf.stat().st_nlink == 1


def test_private_directory_keeps_its_ordinary_link_count(tmp_path: Path) -> None:
    directory = _private_directory(tmp_path)
    (directory / "nested").mkdir()
    info = directory.lstat()
    assert stat.S_ISDIR(info.st_mode)
    if os.name == "posix":
        assert info.st_nlink > 1
    require_private_path(directory, directory=True)


def test_require_private_path_rejects_a_hard_linked_leaf(tmp_path: Path) -> None:
    sentinel, link, original = _hard_linked_leaf(tmp_path)
    with pytest.raises(OSError, match="private IO path is not a regular owned leaf"):
        require_private_path(link)
    descriptor = os.open(link, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with pytest.raises(OSError, match="private IO path is not a regular owned leaf"):
            require_private_path(link, descriptor=descriptor)
    finally:
        os.close(descriptor)
    assert sentinel.read_bytes() == original


def test_snapshot_read_rejects_a_hard_linked_leaf_without_changing_it(tmp_path: Path) -> None:
    sentinel, link, original = _hard_linked_leaf(tmp_path)
    envelope = {
        "schema_version": effect_runtime.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION,
        "request_id": "owned-request",
        "ok": True,
        "result_ref": {
            "byte_count": len(original),
            "sha256": hashlib.sha256(original).hexdigest(),
        },
    }
    with pytest.raises(effect_runtime.EffectRuntimeResponseAmbiguous):
        effect_runtime._read_local_snapshot_response(
            envelope,
            link,
            method="goal.checkpoint_read_context.commit",
            request_id="owned-request",
            timeout=10,
        )
    assert sentinel.read_bytes() == original
    assert link.stat().st_nlink == 2
