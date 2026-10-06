"""Durable local document effects, independent of Todo editing or authority.

Callers own locking, admission and recovery policy. These Python Host IO
primitives preserve UTF-8 bytes, permissions and file/directory durability;
they neither choose an authority provider nor authorize a mutation.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def atomic_write_state_text(path: Path, text: str, *, create_only: bool = False) -> None:
    """Persist complete UTF-8 state while the caller holds its sibling lock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if not create_only and path.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            os.chmod(temporary_path, mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if create_only:
            os.link(temporary_path, path)
        else:
            os.replace(temporary_path, path)
        fsync_state_directory(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def fsync_state_directory(path: Path) -> None:
    if os.name == "posix":
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)


def verify_state_text_durable(path: Path, text: str) -> None:
    """Re-establish durability after a previous publish may have failed at fsync.

    Equality of visible bytes alone does not prove the directory entry persisted.
    The caller holds the same document lock as the state writer.
    """
    with path.open("r+" if os.name == "nt" else "r", encoding="utf-8", newline="") as handle:
        if handle.read() != text:
            raise RuntimeError("Todo Markdown projection readback mismatch")
        os.fsync(handle.fileno())
    fsync_state_directory(path)


