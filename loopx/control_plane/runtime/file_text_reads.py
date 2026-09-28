"""Bounded ordered UTF-8 reads; classification stays with the caller.

This is a filesystem adapter, not an authorization or scan-result cache. The
caller must exclude private inputs before submitting them. Every admitted path
is reopened on every call; concurrent I/O cannot turn an earlier scan into proof.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path


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


def iter_utf8_file_reads(
    paths: Iterable[Path], *, max_workers: int = 8
) -> Iterator[Utf8FileRead]:
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
            yield _read_utf8(path)
        return
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="loopx-file-read") as pool:
        pending: deque[Future[Utf8FileRead]] = deque(
            pool.submit(_read_utf8, path) for path in first_paths
        )
        while pending:
            yield pending.popleft().result()
            path = next(iterator, None)
            if path is not None:
                pending.append(pool.submit(_read_utf8, path))
