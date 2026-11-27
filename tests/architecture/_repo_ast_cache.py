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
from typing import Protocol

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAX_CACHE_ENTRIES = 4096
# Matches escapes that can construct arbitrary ASCII characters or join split literals.
_CHAR_ESCAPE_RE = re.compile(r"\\(?:[xXuUN\r\n]|[0-7]{1,3})")
# Matches Python string literals without backtracking, then detects adjacent literals
# (implicit concatenation across whitespace/comments) or `+` / f-string interpolation.
_PY_STRING_PATTERN = (
    r"[rRuUbBfF]{0,2}(?:"
    + r'"""[^"\\]*(?:(?:\\.|"(?!""))[^"\\]*)*"""'
    + r"|'''[^'\\]*(?:(?:\\.|'(?!''))[^'\\]*)*'''"
    + r'|"(?!"")[^"\\\r\n]*(?:\\.[^"\\\r\n]*)*"'
    + r"|'(?!'')[^'\\\r\n]*(?:\\.[^'\\\r\n]*)*'"
    + r")"
)
_ADJACENT_STRING_SCAN_RE = re.compile(
    rf"#[^\r\n]*|(?P<adj>{_PY_STRING_PATTERN}(?:[ \t\r\n\f]|#[^\r\n]*)*{_PY_STRING_PATTERN})|{_PY_STRING_PATTERN}"
)
_PLUS_OR_FSTRING_SCAN_RE = re.compile(
    rf"#[^\r\n]*|(?P<concat>{_PY_STRING_PATTERN}(?:[ \t\r\n\f]|#[^\r\n]*)*\+|(?:\+[ \t\r\n\f]*[rRuUbBfF]{{0,2}}['\"]|[fF][rRuUbB]?['\"]))|{_PY_STRING_PATTERN}"
)


class CacheStats(Protocol):
    """Typed view of ``functools.lru_cache.cache_info()`` tuples."""

    @property
    def hits(self) -> int: ...
    @property
    def misses(self) -> int: ...
    @property
    def maxsize(self) -> int | None: ...
    @property
    def currsize(self) -> int: ...


def clear_caches() -> None:
    """Clear all per-worker cached directory, path, text, syntax, and AST entries."""
    _canonical_path.cache_clear()
    _iter_repo_python_sources.cache_clear()
    _read_text_cached.cache_clear()
    _check_python_syntax_cached.cache_clear()
    _parse_python_cached.cache_clear()
    may_split_or_escape_literal.cache_clear()


def cache_info() -> dict[str, CacheStats]:
    """Return ``lru_cache`` statistics for tests and diagnostics."""
    return {
        "sources": _iter_repo_python_sources.cache_info(),
        "text": _read_text_cached.cache_info(),
        "syntax": _check_python_syntax_cached.cache_info(),
        "ast": _parse_python_cached.cache_info(),
        "split_literal": may_split_or_escape_literal.cache_info(),
    }


def has_char_escape(source: str) -> bool:
    """Return True if ``source`` uses hex, unicode, octal, or line escapes."""
    return _CHAR_ESCAPE_RE.search(source) is not None


@lru_cache(maxsize=_MAX_CACHE_ENTRIES)
def may_split_or_escape_literal(source: str, *, include_plus: bool = False) -> bool:
    """Return True when ``source`` can assemble a target literal without spelling it whole.

    Detects non-trivial escape sequences, line continuations, and implicit adjacent
    string literal concatenation (for example ``"a" "b"``, ``r"a" r"b"``, or multiline
    literals separated by whitespace/comments). When ``include_plus=True``, also flags
    ``+`` string concatenation and f-string literals for suites that fold ``BinOp``/``JoinedStr``.
    """
    if _CHAR_ESCAPE_RE.search(source) is not None:
        return True
    for match in _ADJACENT_STRING_SCAN_RE.finditer(source):
        if match.lastgroup == "adj":
            return True
    if include_plus:
        for match in _PLUS_OR_FSTRING_SCAN_RE.finditer(source):
            if match.lastgroup == "concat":
                return True
    return False


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
