"""Bounded ordered file reads; classification stays with the caller.

This is a filesystem adapter, not an authorization or scan-result cache. The
caller must exclude private inputs before submitting them. Every admitted path
is reopened on every call; concurrent I/O cannot turn an earlier scan into proof.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path
from typing import TypeVar


@dataclass(frozen=True)
class Utf8FileRead:
    path: Path
    text: str | None
    error: OSError | UnicodeDecodeError | None


def _read_utf8(path: Path) -> Utf8FileRead:
    try:
        return Utf8FileRead(path, path.read_text(encoding="utf-8"), None)
    except (OSError, UnicodeDecodeError) as error:
        return Utf8FileRead(path, None, error)


@dataclass(frozen=True)
class BinaryFileRead:
    path: Path
    data: bytes | None
    error: OSError | None


def _read_bytes(path: Path) -> BinaryFileRead:
    try:
        return BinaryFileRead(path, path.read_bytes(), None)
    except OSError as error:
        return BinaryFileRead(path, None, error)


_Read = TypeVar("_Read")


def iter_binary_file_reads(
    paths: Iterable[Path], *, max_workers: int = 8
) -> Generator[BinaryFileRead, None, None]:
    """Read original bytes without decoding or newline normalization."""
    yield from _ordered_reads(paths, _read_bytes, max_workers)


def iter_utf8_file_reads(
    paths: Iterable[Path], *, max_workers: int = 8
) -> Generator[Utf8FileRead, None, None]:
    """Read UTF-8 text with the same ordered, bounded filesystem lifetime."""
    yield from _ordered_reads(paths, _read_utf8, max_workers)


def _ordered_reads(
    paths: Iterable[Path], read: Callable[[Path], _Read], max_workers: int
) -> Generator[_Read, None, None]:
    """Overlap disk waits with at most ``max_workers`` pending reads.

    Results retain input order, including failures. Unlike ``Executor.map`` on
    supported older Python runtimes, this does not eagerly submit the entire
    tree or retain all of its decoded content. No worker traverses directories,
    probes Git, classifies content, changes state, or retries a failed read.
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers must be a positive integer")
    iterator = iter(paths)
    first_paths = list(islice(iterator, max_workers))
    if not first_paths:
        return
    if max_workers == 1 or len(first_paths) == 1:
        for path in chain(first_paths, iterator):
            yield read(path)
        return
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="loopx-file-read") as pool:
        pending: deque[Future[_Read]] = deque(
            pool.submit(read, path) for path in first_paths
        )
        while pending:
            yield pending.popleft().result()
            next_path = next(iterator, None)
            if next_path is not None:
                pending.append(pool.submit(read, next_path))
