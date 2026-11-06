"""Small, invalidatable File Chat journal observations, never execution state."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Any


class FileChatDiscovery:
    """Keep only caller-selected metadata while the complete file is unchanged.

    Native Chat uses this for terminal-Turn discovery and source-control fields.
    Selected Turns/requests and every admission still use their original fresh
    readers. No body, grant, queue decision or provider effect is retained here.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._directories: dict[Path, dict[Path, tuple[tuple[int, ...], dict[str, Any]]]] = {}

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, ...]:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size

    def rows(self, directory: Path, paths: Iterable[Path], *,
             read: Callable[[Path], dict[str, Any]],
             project: Callable[[Path, dict[str, Any]], dict[str, Any] | None],
             ) -> Iterator[tuple[Path, dict[str, Any]]]:
        seen = set()
        with self._lock:
            entries = self._directories.setdefault(directory, {})
        for path in paths:
            seen.add(path)
            try:
                before = self._fingerprint(path)
            except OSError:
                before = None
            with self._lock:
                saved = entries.get(path)
                if before is None or saved is None or saved[0] != before:
                    entries.pop(path, None)
                    saved = None
            if saved is not None:
                yield path, deepcopy(saved[1])
                continue
            # Preserve the owning reader's missing/unreadable/strict behavior.
            value = read(path)
            view = project(path, value)
            if before is not None and view is not None:
                try:
                    unchanged = self._fingerprint(path) == before
                except OSError:
                    unchanged = False
                if unchanged:
                    with self._lock:
                        entries[path] = before, deepcopy(view)
            yield path, value if view is None else view
        with self._lock:
            for path in entries.keys() - seen:
                del entries[path]
