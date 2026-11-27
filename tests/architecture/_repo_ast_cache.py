"""Per-worker read and read-only AST cache for architecture census tests.

Architecture tests across ``tests/architecture/`` repeatedly scan the same
tracked ``loopx/`` Python modules within a single pytest / xdist worker.
Caching file reads and read-only AST parses by ``(resolved_path, st_mtime_ns,
st_size)`` eliminates redundant disk reads and ``ast.parse`` calls while
automatically invalidating whenever a file changes on disk.

IMPORTANT: ``parse_python_cached`` returns shared, read-only ``ast.Module``
instances. Callers MUST NOT mutate returned AST nodes (no ``setattr``,
``delattr``, child rewriting, or list mutation on ``body``); use
``read_text_cached(path)`` + ``ast.parse(...)`` when mutation is required.
"""

from __future__ import annotations

import ast
from functools import lru_cache
import os
from pathlib import Path
import re

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAX_CACHE_ENTRIES = 4096
# Matches escapes that can construct arbitrary ASCII characters or join split literals.
_CHAR_ESCAPE_RE = re.compile(r"\\(?:[xXuUN\r\n]|[0-7]{2,3})")


def clear_caches() -> None:
    """Clear all per-worker cached directory, path, text, syntax, and AST entries."""
    _canonical_path.cache_clear()
    _iter_repo_python_sources.cache_clear()
    _read_text_cached.cache_clear()
    _check_python_syntax_cached.cache_clear()
    _parse_python_cached.cache_clear()


def cache_info() -> dict[str, object]:
    """Return ``lru_cache`` statistics for tests and diagnostics."""
    return {
        "sources": _iter_repo_python_sources.cache_info(),
        "text": _read_text_cached.cache_info(),
        "syntax": _check_python_syntax_cached.cache_info(),
        "ast": _parse_python_cached.cache_info(),
    }


def has_char_escape(source: str) -> bool:
    """Return True if ``source`` uses hex, unicode, multi-digit octal, or line escapes."""
    return _CHAR_ESCAPE_RE.search(source) is not None


@lru_cache(maxsize=_MAX_CACHE_ENTRIES)
def _canonical_path(raw_path: str) -> str:
    return str(Path(raw_path).resolve())


def _stat_target(path: Path) -> tuple[str, os.stat_result]:
    resolved = _canonical_path(str(path))
    return resolved, os.stat(resolved)


def _scan_python_sources(root: Path, exclude_parts: frozenset[str]) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in exclude_parts]
        base = Path(dirpath)
        for filename in filenames:
            if filename.endswith(".py"):
                found.append(base / filename)
    found.sort()
    return tuple(found)


@lru_cache(maxsize=64)
def _iter_repo_python_sources(
    root: Path,
    exclude_parts: frozenset[str],
) -> tuple[Path, ...]:
    return _scan_python_sources(root, exclude_parts)


def iter_python_sources(
    root: Path,
    *,
    exclude_parts: tuple[str, ...] | frozenset[str] = ("__pycache__",),
) -> tuple[Path, ...]:
    """Return sorted ``*.py`` paths under ``root``, skipping ``exclude_parts``.

    Repository directories under ``_REPO_ROOT`` are cached per worker; arbitrary
    external/``tmp_path`` directories are scanned fresh so tests mutating temp
    trees never observe stale directory listings.
    """
    excluded = frozenset(exclude_parts)
    try:
        resolved = root.resolve()
        in_repo = resolved.is_relative_to(_REPO_ROOT)
    except OSError:
        in_repo = False
    if in_repo:
        return _iter_repo_python_sources(root, excluded)
    return _scan_python_sources(root, excluded)


@lru_cache(maxsize=_MAX_CACHE_ENTRIES)
def _read_text_cached(resolved_path: str, mtime_ns: int, size: int) -> str:
    del mtime_ns, size
    return Path(resolved_path).read_text(encoding="utf-8")


def read_text_cached(path: Path) -> str:
    """Read UTF-8 text from ``path``, cached by ``(resolved_path, mtime_ns, size)``."""
    resolved, st = _stat_target(path)
    return _read_text_cached(resolved, st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=_MAX_CACHE_ENTRIES)
def _check_python_syntax_cached(resolved_path: str, mtime_ns: int, size: int) -> None:
    source = _read_text_cached(resolved_path, mtime_ns, size)
    ast.parse(source, filename=resolved_path)


def check_python_syntax_cached(path: Path) -> None:
    """Validate Python syntax without retaining an AST in the shared cache."""
    resolved, st = _stat_target(path)
    _check_python_syntax_cached(resolved, st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=_MAX_CACHE_ENTRIES)
def _parse_python_cached(resolved_path: str, mtime_ns: int, size: int) -> ast.Module:
    source = _read_text_cached(resolved_path, mtime_ns, size)
    return ast.parse(source, filename=resolved_path)


def parse_python_cached(path: Path) -> ast.Module:
    """Parse ``path`` into a shared immutable ``ast.Module`` cached by stat key."""
    resolved, st = _stat_target(path)
    return _parse_python_cached(resolved, st.st_mtime_ns, st.st_size)
