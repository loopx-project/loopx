"""Bounded source context for failed pytest reports; no runtime payload text."""
from __future__ import annotations

import ast
import builtins
import inspect
import re
from functools import lru_cache
from pathlib import Path

import pytest


PREFIX = "LOOPX_PYTEST_FAILURE "
_PATH = re.compile(r"(?:tests|loopx|scripts|examples|packages)/[A-Za-z0-9_./-]+\.py")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# Freeze identities and public names before tests can add/rename exceptions.
_KNOWN_EXCEPTIONS = tuple((value, value.__name__) for value in vars(builtins).values()
                          if isinstance(value, type) and issubclass(value, BaseException)) + (
    (pytest.fail.Exception, pytest.fail.Exception.__name__),
)


def _source_path(value, root):
    try:
        path = Path(value)
        relative = (path if path.is_absolute() else root / path).resolve().relative_to(root)
    except (TypeError, ValueError, OSError, RuntimeError):
        return None
    name = relative.as_posix()
    return name if _PATH.fullmatch(name) and len(name) <= 256 else None


class _HideLiterals(ast.NodeTransformer):
    def visit_Interpolation(self, node):
        # Python 3.14 keeps original interpolation text separately from value.
        self.generic_visit(node)
        node.str = None
        return node

    def visit_Constant(self, node):
        if node.value is None or isinstance(node.value, bool) or (
            isinstance(node.value, (int, float)) and node.value in (0, 1)
        ):
            return node
        return ast.copy_location(ast.Constant(value="<literal>"), node)


@lru_cache(maxsize=32)
def _source_context(root, path):
    try:
        with (root / path).open("rb") as stream:
            source = stream.read(1_000_001)
        if len(source) > 1_000_000:
            return {}, frozenset()
        tree = ast.parse(source.decode("utf-8"))
        classes = frozenset(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
        assertions = {
            node.lineno: ast.unparse(_HideLiterals().visit(node.test))[:256]
            for node in ast.walk(tree) if isinstance(node, ast.Assert)
        }
        return assertions, classes
    except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError):
        return {}, frozenset()


def exception_type_name(exception, root):
    for known, public_name in _KNOWN_EXCEPTIONS:
        if exception is known:
            return public_name
    name = exception.__name__
    if not _NAME.fullmatch(name):
        return None
    # A dynamically constructed exception name can itself contain a secret.
    # Only disclose non-builtin names declared in in-root Python source.
    path = _source_path(inspect.getsourcefile(exception), root)
    return name if path is not None and name in _source_context(root, path)[1] else None


def failure_context(report, root):
    frames = []
    traceback = getattr(report.longrepr, "reprtraceback", None)
    for entry in getattr(traceback, "reprentries", ()):
        location = getattr(entry, "reprfileloc", None)
        path = _source_path(getattr(location, "path", None), root)
        line = getattr(location, "lineno", None)
        if path is not None and type(line) is int and 0 < line <= 1_000_000_000:
            try:
                assertion = _source_context(root, path)[0].get(line)
            except Exception:
                assertion = None
            frames.append({"path": path, "line": line, "assertion": assertion})
    name = getattr(report, "loopx_failure_type", None)
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        name = None
    return {"exception_type": name,
            "frames": frames[-8:], "frames_truncated": len(frames) > 8}
