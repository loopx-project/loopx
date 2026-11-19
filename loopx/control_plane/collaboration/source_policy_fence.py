"""Serialize source-policy readers and writers across threads and processes."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Iterator

from ...file_lock import exclusive_file_lock


_LOCKS_GUARD = Lock()
_THREAD_LOCKS: dict[str, RLock] = {}


def _thread_lock(path: Path) -> RLock:
    key = str(path.expanduser().resolve())
    with _LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, RLock())


@contextmanager
def source_policy_fence(policy_path: Path) -> Iterator[None]:
    """Hold the in-process and cross-process fences used by source writers."""

    with _thread_lock(policy_path), exclusive_file_lock(policy_path.with_suffix(".lock")):
        yield
