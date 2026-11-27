"""Regression tests for ``tests.architecture._repo_ast_cache``."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from tests.architecture._repo_ast_cache import (
    cache_info,
    check_python_syntax_cached,
    clear_caches,
    iter_python_sources,
    may_split_or_escape_literal,
    parse_python_cached,
    read_text_cached,
)


def test_cache_invalidates_when_file_stat_changes(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("VALUE = 'first'\n", encoding="utf-8")

    first_text = read_text_cached(target)
    first_tree = parse_python_cached(target)
    assert first_text == "VALUE = 'first'\n"
    assert parse_python_cached(target) is first_tree

    # Ensure both size and mtime_ns change deterministically.
    target.write_text("VALUE = 'second_longer'\n", encoding="utf-8")
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert read_text_cached(target) == "VALUE = 'second_longer'\n"
    second_tree = parse_python_cached(target)
    assert second_tree is not first_tree
    assert isinstance(second_tree.body[0], ast.Assign)
    assert isinstance(second_tree.body[0].value, ast.Constant)
    assert second_tree.body[0].value.value == "second_longer"


def test_parse_python_cached_does_not_suppress_or_cache_syntax_errors(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n", encoding="utf-8")

    with pytest.raises(SyntaxError):
        parse_python_cached(broken)

    broken.write_text("VALID = 1\n", encoding="utf-8")
    stat = broken.stat()
    os.utime(broken, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    tree = parse_python_cached(broken)
    assert len(tree.body) == 1
    check_python_syntax_cached(broken)
    before_hits = cache_info()["syntax"].hits
    check_python_syntax_cached(broken)
    assert cache_info()["syntax"].hits == before_hits + 1


def test_iter_python_sources_excludes_pycache_and_sees_new_tmp_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "ignored.py").write_text("X = 1\n", encoding="utf-8")
    first = tmp_path / "a.py"
    first.write_text("A = 1\n", encoding="utf-8")

    assert iter_python_sources(tmp_path) == (first,)

    second = tmp_path / "b.py"
    second.write_text("B = 2\n", encoding="utf-8")
    assert iter_python_sources(tmp_path) == (first, second)

    clear_caches()


@pytest.mark.parametrize(
    ("source", "include_plus", "expected"),
    [
        ('X = "plain"\n', False, False),
        ('X = ""\nY = """docstring\n"quoted"\n"""\n', False, False),
        ('# comment with "unmatched\nX = "ok"\n', False, False),
        ('X = "periodic_" "report_trigger_decision_v0"\n', False, True),
        ('X = r"^[0-9a-f]{6" r"4}$"\n', False, True),
        ('X = (\n    "sha"\n    # wrapped across lines\n    "256:"\n)\n', False, True),
        ('X = "a""b"\n', False, True),
        ('X = "a"\'b\'\n', False, True),
        ('X = "sha\\x3256:"\n', False, True),
        ('X = "a\\12b"\n', False, True),
        ('X = "a" \\\n    "b"\n', False, True),
        ('X = "a" + y\n', False, False),
        ('X = "a" + y\n', True, True),
        ('X = PREFIX + "256:"\n', True, True),
        ('X = f"sha{suffix}"\n', True, True),
        ('X = "a + b"\n', True, False),
    ],
)
def test_may_split_or_escape_literal_detects_concat_and_escapes(
    source: str, include_plus: bool, expected: bool
) -> None:
    assert may_split_or_escape_literal(source, include_plus=include_plus) is expected
    before_hits = cache_info()["split_literal"].hits
    assert may_split_or_escape_literal(source, include_plus=include_plus) is expected
    assert cache_info()["split_literal"].hits == before_hits + 1
