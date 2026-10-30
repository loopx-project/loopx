from __future__ import annotations

import ast
import codecs
import hashlib
import json
import os
import re
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contract import (
    AntiEntropyFinding,
    AntiEntropyReceipt,
    RetirementBasis,
)

_PY_DEF_OR_CLASS_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")

_TS_DEF_PATTERNS = [
    # function / async function / export function
    re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    # class / abstract class / export class
    re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    # interface
    re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    # type alias: type Foo = ... or type Foo<T> = ...
    re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:<[^>]*>)?\s*="),
    # enum
    re.compile(r"^\s*(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    # const / let / var Foo = (...) => or = async (...) => (with optional generics)
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:<[^>]*>)?\s*(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>"
    ),
    # const / let / var Foo = function
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?function"
    ),
    # const / let / var Foo = async (
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?=\s*async\s*(?:<[^>]*>)?\s*\("
    ),
    # class methods / interface methods: e.g. public async oldMethod(param: string): void {
    re.compile(
        r"^\s*(?:(?:public|private|protected|readonly|override)\s+)*(?:static\s+)?(?:async\s+)?\*?\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::\s*[^{;]+)?\s*\{?"
    ),
]

_TS_RESERVED_NAMES = {
    "if", "for", "switch", "while", "catch", "constructor", "get", "set",
    "function", "class", "export", "import", "return", "throw", "default",
    "new", "typeof", "instanceof", "void", "delete", "yield", "await",
}

_COMMENT_LINE_RE = re.compile(r"^\s*(?:#|//)\s*(.+)$")

_DOC_EXTENSIONS = {".md", ".rst", ".txt", ".json", ".yaml", ".yml", ".toml"}
_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_PY_EXTENSIONS = {".py", ".pyi"}

_RULE_AE001 = "AE001-DELETE_FIRST_VIOLATION"
_RULE_AE002 = "AE002-ZOMBIE_SYMBOL_REINTRODUCED"
_RULE_AE003 = "AE003-COMMENTED_CODE_ADVISORY"
_RULE_AE004 = "AE004-INSUFFICIENT_SOURCE_EVIDENCE"

_WORKSPACE_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

_MAX_SOURCE_FILE_BYTES = 2_000_000

_MAX_WORKSPACE_FILES = 20000


def _hash_diff(diff_text: str) -> str:
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()


def hash_retirement_basis(basis: RetirementBasis) -> str:
    """Stable hash binding the receipt to the exact declared retirement intent."""
    payload = json.dumps(basis.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def find_repo_root(start_path: Path | None = None) -> Path:
    """Resolve repository root by looking for .git folder up the tree.

    Kept for callers that locate a repository; verification itself requires an
    explicitly bound workspace and no longer infers one from the CWD.
    """
    cur = (start_path or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / ".git").exists():
            return parent
    return cur


def decode_git_diff_path(path_str: str, is_source: bool = True) -> str:
    """Decode a Git diff path with optional C-style quotes, stripping Git a/ or b/ prefix."""
    path_str = path_str.strip()
    if path_str.startswith('"') and path_str.endswith('"') and len(path_str) >= 2:
        inner = path_str[1:-1]
        try:
            raw_bytes = codecs.escape_decode(inner.encode("latin1"))[0]
            path_str = raw_bytes.decode("utf-8")
        except Exception:
            path_str = inner
    path_str = path_str.replace("\\", "/").strip()
    if path_str == "/dev/null":
        return "/dev/null"
    if is_source and path_str.startswith("a/"):
        return path_str[2:]
    elif not is_source and path_str.startswith("b/"):
        return path_str[2:]
    elif path_str.startswith("a/") or path_str.startswith("b/"):
        return path_str[2:]
    return path_str


def normalize_repo_path(path_str: str) -> str:
    """Normalize a repository-relative target path from RetirementBasis.

    Does not strip real directory segments (e.g. "a/") or leading hidden dots (e.g. ".api.py").
    """
    path_str = path_str.strip()
    if path_str.startswith('"') and path_str.endswith('"') and len(path_str) >= 2:
        inner = path_str[1:-1]
        try:
            raw_bytes = codecs.escape_decode(inner.encode("latin1"))[0]
            path_str = raw_bytes.decode("utf-8")
        except Exception:
            path_str = inner
    path_str = path_str.replace("\\", "/").strip()
    while path_str.startswith("./"):
        path_str = path_str[2:]
    while path_str.startswith("/"):
        path_str = path_str[1:]
    return path_str


def unquote_git_path(path_str: str) -> str:
    """Backward-compatible helper for unquoting git diff paths."""
    return decode_git_diff_path(path_str)


def parse_diff_git_header(line: str) -> tuple[str, str] | None:
    """Parse diff --git <src> <dst> line correctly, handling paths with spaces and quotes."""
    line = line.strip()
    if not line.startswith("diff --git "):
        return None
    rest = line[len("diff --git "):].strip()

    # Case 1: Both quoted, e.g. "a/foo bar" "b/foo bar"
    m_quoted = re.match(r'^("(?:[^"\\]|\\.)+")\s+("(?:[^"\\]|\\.)+")$', rest)
    if m_quoted:
        return m_quoted.group(1), m_quoted.group(2)

    # Case 2: Identical relative paths across a/ and b/ (deletion / modification)
    m_same = re.match(r"^a/(.+)\s+b/\1$", rest)
    if m_same:
        return f"a/{m_same.group(1)}", f"b/{m_same.group(1)}"

    # Case 3: Search for separator " b/" from the right
    if rest.startswith("a/"):
        idx = rest.rfind(" b/")
        if idx != -1:
            return rest[:idx], rest[idx + 1:]

    parts = rest.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


def _is_doc_file(file_path: str) -> bool:
    ext = Path(file_path).suffix.lower() if file_path else ""
    return ext in _DOC_EXTENSIONS


def _extract_deleted_paths(diff_text: str) -> set[str]:
    deleted_paths: set[str] = set()
    diff_text = textwrap.dedent(diff_text)

    # 1. Parse sections starting with diff --git
    diff_sections = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)
    for sec in diff_sections:
        if not sec.startswith("diff --git "):
            continue
        first_line = sec.splitlines()[0]
        is_deleted = bool(
            re.search(r"^deleted file mode \d+", sec, re.MULTILINE)
            or re.search(r"^\+\+\+\s+/dev/null", sec, re.MULTILINE)
        )
        if is_deleted:
            # Check for standard patch header: --- a/<path>\n+++ /dev/null
            m_patch = re.search(r"^---\s+(.+?)\n\+\+\+\s+/dev/null", sec, re.MULTILINE)
            if m_patch:
                clean_path = decode_git_diff_path(m_patch.group(1).strip(), is_source=True)
                if clean_path:
                    deleted_paths.add(clean_path)
                    continue

            # Empty file deletion with no content hunks (deleted file mode 100644 only)
            header_paths = parse_diff_git_header(first_line)
            if header_paths:
                clean_path = decode_git_diff_path(header_paths[0], is_source=True)
                if clean_path:
                    deleted_paths.add(clean_path)

    # 2. Also match standard --- <src>\n+++ /dev/null for diffs without diff --git
    for match in re.finditer(r"^---\s+(.+?)\n\+\+\+\s+/dev/null", diff_text, re.MULTILINE):
        raw_path = match.group(1).strip()
        clean_path = decode_git_diff_path(raw_path, is_source=True)
        if clean_path:
            deleted_paths.add(clean_path)

    return deleted_paths


def _extract_python_defs_from_code(code: str) -> set[str] | None:
    """Extract Python definitions from a complete-parseable code block.

    Returns None when the text cannot be parsed as Python at all (so callers can
    treat it as unparseable rather than as "defines nothing").
    """
    if not code.strip():
        return set()
    dedented = textwrap.dedent(code)
    try:
        tree = ast.parse(dedented)
        return _python_def_names(tree)
    except SyntaxError:
        try:
            wrapped = "def _wrapper_():\n" + textwrap.indent(dedented, "    ")
            tree = ast.parse(wrapped)
            return {
                name
                for name in _python_def_names(tree)
                if name != "_wrapper_"
            }
        except SyntaxError:
            return None


def _python_def_names(tree: ast.AST) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _extract_python_signature_defs(lines: list[str]) -> set[str]:
    """Line-level fallback for code fragments that cannot be parsed as Python.

    Only meaningful for in-diff code fragments; complete files must use the AST
    result instead so that prose/docstring examples are never counted as defs.
    """
    defs: set[str] = set()
    for line in lines:
        m = _PY_DEF_OR_CLASS_RE.match(line)
        if m:
            name = m.group(1)
            try:
                tree = ast.parse(f"{line.strip()}\n    pass")
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if node.name == name:
                            defs.add(name)
            except SyntaxError:
                defs.add(name)
    return defs


def _extract_ts_defs(lines: list[str]) -> set[str]:
    defs: set[str] = set()
    for line in lines:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("//") or line_clean.startswith("/*") or line_clean.startswith("*"):
            continue
        for pattern in _TS_DEF_PATTERNS:
            m = pattern.match(line)
            if m:
                candidate = m.group(1)
                if candidate not in _TS_RESERVED_NAMES:
                    defs.add(candidate)
                break
    return defs


_TS_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\Z")
_TS_QUOTE_CHARS = ("\"", "'", chr(96))


def _static_computed_member(
    text: str, start: int, out: list[str]
) -> tuple[str, int] | None:
    """Preserve a statically named computed member access (api[name]).

    Only a member name in a value position is preserved. Array literals,
    declarations, and dynamic expressions stay string-literal text and never
    become executable references; unparseable forms keep the normal string
    handling so they cannot be certified as absent evidence.
    """
    index = start + 1
    length = len(text)
    while index < length and text[index] in " \t":
        index += 1
    if index >= length or text[index] not in _TS_QUOTE_CHARS:
        return None
    quote = text[index]
    cursor = index + 1
    content_start = cursor
    while cursor < length:
        char = text[cursor]
        if char == "\\" or char == "\n":
            return None
        if char == quote:
            break
        cursor += 1
    if cursor >= length:
        return None
    name = text[content_start:cursor]
    if not _TS_IDENTIFIER_RE.match(name):
        return None
    after = cursor + 1
    while after < length and text[after] in " \t":
        after += 1
    if after >= length or text[after] != "]":
        return None
    previous = ""
    for position in range(len(out) - 1, -1, -1):
        token = out[position]
        stripped = token.rstrip()
        if stripped:
            previous = stripped[-1]
            break
    if not (previous.isalnum() or previous in "_$)]."):
        return None
    return name, after + 1


def _tokenize_ts_code(text: str) -> tuple[str, bool]:
    """Tokenize and strip comments and string-literal spans from TS/JS code.

    Preserves code expressions inside template string interpolations (`${` ... `}`),
    ensuring that active references to target symbols are preserved while plain
    template text and string literals are replaced with spaces.
    Handles nested template literals, brace nesting inside expressions, and
    escaped characters.
    Returns (stripped_code, is_clean) where is_clean is False if syntax has
    unclosed template literals, block comments, or unclosed strings.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    stack: list[Any] = []  # items: 'template' or {'type': 'expr', 'brace_depth': int}

    while i < n:
        if text[i] == "[" and (not stack or stack[-1] != "template"):
            computed = _static_computed_member(text, i, out)
            if computed is not None:
                name, next_index = computed
                out.append(" ")
                out.append(name)
                i = next_index
                continue
        if not stack:
            # Top-level code
            if text[i : i + 2] == "//":
                while i < n and text[i] != "\n":
                    out.append(" ")
                    i += 1
                if i < n and text[i] == "\n":
                    out.append("\n")
                    i += 1
            elif text[i : i + 2] == "/*":
                out.append(" ")
                out.append(" ")
                i += 2
                closed = False
                while i < n:
                    if text[i : i + 2] == "*/":
                        out.append(" ")
                        out.append(" ")
                        i += 2
                        closed = True
                        break
                    out.append("\n" if text[i] == "\n" else " ")
                    i += 1
                if not closed:
                    return "".join(out), False
            elif text[i] == '"':
                out.append(" ")
                i += 1
                closed = False
                while i < n:
                    if text[i] == "\\":
                        out.append(" ")
                        out.append(" ")
                        i += 2 if i + 1 < n else 1
                        continue
                    if text[i] == '"':
                        out.append(" ")
                        i += 1
                        closed = True
                        break
                    if text[i] == "\n":
                        break
                    out.append(" ")
                    i += 1
                if not closed:
                    return "".join(out), False
            elif text[i] == "'":
                out.append(" ")
                i += 1
                closed = False
                while i < n:
                    if text[i] == "\\":
                        out.append(" ")
                        out.append(" ")
                        i += 2 if i + 1 < n else 1
                        continue
                    if text[i] == "'":
                        out.append(" ")
                        i += 1
                        closed = True
                        break
                    if text[i] == "\n":
                        break
                    out.append(" ")
                    i += 1
                if not closed:
                    return "".join(out), False
            elif text[i] == "`":
                out.append(" ")
                i += 1
                stack.append("template")
            else:
                out.append(text[i])
                i += 1
        elif stack[-1] == "template":
            # Inside template literal
            if text[i : i + 2] == "${":
                out.append(" ")
                out.append(" ")
                i += 2
                stack.append({"type": "expr", "brace_depth": 1})
            elif text[i] == "`":
                out.append(" ")
                i += 1
                stack.pop()
            elif text[i] == "\\":
                out.append(" ")
                out.append(" ")
                i += 2 if i + 1 < n else 1
            else:
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
        else:
            # Inside template interpolation expression
            top = stack[-1]
            if text[i : i + 2] == "//":
                while i < n and text[i] != "\n":
                    out.append(" ")
                    i += 1
                if i < n and text[i] == "\n":
                    out.append("\n")
                    i += 1
            elif text[i : i + 2] == "/*":
                out.append(" ")
                out.append(" ")
                i += 2
                closed = False
                while i < n:
                    if text[i : i + 2] == "*/":
                        out.append(" ")
                        out.append(" ")
                        i += 2
                        closed = True
                        break
                    out.append("\n" if text[i] == "\n" else " ")
                    i += 1
                if not closed:
                    return "".join(out), False
            elif text[i] == '"':
                out.append(" ")
                i += 1
                closed = False
                while i < n:
                    if text[i] == "\\":
                        out.append(" ")
                        out.append(" ")
                        i += 2 if i + 1 < n else 1
                        continue
                    if text[i] == '"':
                        out.append(" ")
                        i += 1
                        closed = True
                        break
                    if text[i] == "\n":
                        break
                    out.append(" ")
                    i += 1
                if not closed:
                    return "".join(out), False
            elif text[i] == "'":
                out.append(" ")
                i += 1
                closed = False
                while i < n:
                    if text[i] == "\\":
                        out.append(" ")
                        out.append(" ")
                        i += 2 if i + 1 < n else 1
                        continue
                    if text[i] == "'":
                        out.append(" ")
                        i += 1
                        closed = True
                        break
                    if text[i] == "\n":
                        break
                    out.append(" ")
                    i += 1
                if not closed:
                    return "".join(out), False
            elif text[i] == "`":
                out.append(" ")
                i += 1
                stack.append("template")
            elif text[i] == "{":
                top["brace_depth"] += 1
                out.append("{")
                i += 1
            elif text[i] == "}":
                top["brace_depth"] -= 1
                if top["brace_depth"] == 0:
                    stack.pop()
                    out.append(" ")
                    i += 1
                else:
                    out.append("}")
                    i += 1
            else:
                out.append(text[i])
                i += 1

    return "".join(out), len(stack) == 0


def _strip_ts_line(line: str) -> str:
    """Strip // comments and string-literal spans from a TS/JS-like line, preserving template interpolations."""
    stripped, _ = _tokenize_ts_code(line)
    return stripped


def _extract_ts_refs(
    lines: list[str],
    target_symbols: set[str],
) -> set[str]:
    refs, _ = _extract_ts_refs_with_syntax_check(lines, target_symbols)
    return refs


def _extract_ts_refs_with_syntax_check(
    lines: list[str],
    target_symbols: set[str],
) -> tuple[set[str], bool]:
    """Extract real references to target symbols in TS/JS-like lines.

    Preserves lexical boundaries: template string text, string literals, and
    comments are stripped, while executable code inside template interpolations
    is preserved.
    """
    refs: set[str] = set()
    if not target_symbols:
        return refs, True

    full_text = "\n".join(lines)
    stripped_text, is_clean = _tokenize_ts_code(full_text)
    stripped_lines = stripped_text.splitlines()

    for i, raw_line in enumerate(lines):
        stripped = stripped_lines[i] if i < len(stripped_lines) else ""
        if not stripped.strip():
            continue

        defined_on_line: str | None = None
        for pattern in _TS_DEF_PATTERNS:
            m = pattern.match(raw_line.strip())
            if m:
                candidate = m.group(1)
                if candidate in target_symbols and candidate not in _TS_RESERVED_NAMES:
                    defined_on_line = candidate
                break

        line_for_refs = stripped
        if defined_on_line:
            line_for_refs = re.sub(r"\b" + re.escape(defined_on_line) + r"\b", "", line_for_refs, count=1)

        words = set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", line_for_refs))
        for s in target_symbols:
            if s in words:
                refs.add(s)

    return refs, is_clean


def _extract_defs_for_file(lines: list[str], file_path: str = "", is_full_file: bool = False) -> set[str]:
    """Extract definitions for in-diff fragments of a file.

    Documentation files never contribute symbol definitions: fenced examples in
    markdown are prose, not code. For Python fragments, the AST result wins when
    the fragment parses; the line-signature fallback only covers unparseable
    fragments of code files.
    """
    if _is_doc_file(file_path):
        return set()

    code_text = "\n".join(lines)
    ext = Path(file_path).suffix.lower() if file_path else ""

    if ext in _TS_EXTENSIONS:
        return _extract_ts_defs(lines)
    elif ext in _PY_EXTENSIONS:
        py_ast_defs = _extract_python_defs_from_code(code_text)
        if py_ast_defs is not None:
            return py_ast_defs
        if is_full_file:
            return set()
        return _extract_python_signature_defs(lines)
    else:
        py_ast_defs = _extract_python_defs_from_code(code_text)
        if py_ast_defs:
            return py_ast_defs
        if is_full_file:
            return _extract_ts_defs(lines)
        return _extract_python_signature_defs(lines) | _extract_ts_defs(lines)


@dataclass(frozen=True, slots=True)
class _DiffSymbolAnalysis:
    deleted_symbols: set[str] = field(default_factory=set)
    reintroduced_symbols: set[str] = field(default_factory=set)
    surviving_context_defs: set[str] = field(default_factory=set)
    still_defined_on_disk: set[str] = field(default_factory=set)
    all_added_lines: list[str] = field(default_factory=list)
    unverified_source_files: tuple[str, ...] = ()
    file_to_deleted_symbols: dict[str, set[str]] = field(default_factory=dict)
    deleted_paths: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _WorkspaceEvidence:
    """Complete post-change source evidence bound to an explicitly given workspace."""

    root: Path
    defined_symbols: dict[str, set[str]] = field(default_factory=dict)
    referenced_symbols: dict[str, set[str]] = field(default_factory=dict)
    existing_paths: set[str] = field(default_factory=set)
    scan_incomplete: bool = False
    incomplete_reason: str = ""
    skipped_files: tuple[str, ...] = ()


def _extract_python_refs_from_code(code: str) -> set[str] | None:
    """Extract real Python identifier references via AST (strings/docstrings excluded)."""
    try:
        tree = ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return None
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                refs.add(node.module.split(".")[0])
            for alias in node.names:
                refs.add(alias.name.split(".")[0])
                if alias.asname:
                    refs.add(alias.asname)
    return refs


def _scan_workspace(workspace: Path, symbols: tuple[str, ...]) -> _WorkspaceEvidence:
    """Scan an explicitly bound workspace for definitions and real references of target symbols.

    Prefilters by substring so most files never reach the parser. Documentation
    files are skipped: they are not executable evidence.
    """
    evidence = _WorkspaceEvidence(root=workspace)
    if not symbols:
        return evidence

    symbol_set = set(symbols)
    source_exts = _PY_EXTENSIONS | _TS_EXTENSIONS
    visited = 0
    skipped: list[str] = []

    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _WORKSPACE_SKIP_DIRS]
        for filename in filenames:
            file_path = Path(dirpath) / filename
            if file_path.suffix.lower() not in source_exts:
                continue
            rel = file_path.relative_to(workspace).as_posix()
            if visited >= _MAX_WORKSPACE_FILES:
                evidence.scan_incomplete = True
                evidence.incomplete_reason = f"Workspace source file count exceeded limit ({_MAX_WORKSPACE_FILES} files)"
                skipped.append(rel)
                evidence.skipped_files = tuple(skipped)
                return evidence
            try:
                size = file_path.stat().st_size
                if size > _MAX_SOURCE_FILE_BYTES:
                    evidence.scan_incomplete = True
                    evidence.incomplete_reason = f"Source file '{rel}' exceeds size limit ({_MAX_SOURCE_FILE_BYTES} bytes)"
                    skipped.append(rel)
                    continue
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                evidence.scan_incomplete = True
                evidence.incomplete_reason = f"Failed to read source file '{rel}': {exc}"
                skipped.append(rel)
                continue

            visited += 1
            if not any(s in content for s in symbol_set):
                continue

            ext = file_path.suffix.lower()
            if ext in _PY_EXTENSIONS:
                defs = _extract_python_defs_from_code(content)
                refs = _extract_python_refs_from_code(content)
                if defs is None or refs is None:
                    # Unparseable source cannot prove absence; treat the symbol
                    # as still defined to avoid false certification.
                    for s in symbol_set:
                        if s in content:
                            evidence.defined_symbols.setdefault(s, set()).add(rel)
                    continue
            else:
                content_lines = content.splitlines()
                defs = _extract_ts_defs(content_lines)
                refs, syntax_ok = _extract_ts_refs_with_syntax_check(content_lines, symbol_set)
                if not syntax_ok:
                    evidence.scan_incomplete = True
                    evidence.incomplete_reason = f"Source file '{rel}' has unclosed or unparseable JS/TS syntax"
                    skipped.append(rel)
                    continue

            for s in symbol_set:
                if s in defs:
                    evidence.defined_symbols.setdefault(s, set()).add(rel)
                if s in refs:
                    evidence.referenced_symbols.setdefault(s, set()).add(rel)

    evidence.skipped_files = tuple(skipped)
    return evidence


def _analyze_diff_symbols(diff_text: str, workspace: Path | None = None) -> _DiffSymbolAnalysis:
    """Analyze diff to extract deleted, reintroduced, surviving context, and disk-defined symbols.

    Source binding: when ``workspace`` is provided, complete post-change sources
    are read from that explicit root only — never from the caller's CWD. Files
    that cannot be read from the bound workspace are reported as unverified.
    """
    deleted_symbols: set[str] = set()
    reintroduced_symbols: set[str] = set()
    surviving_context_defs: set[str] = set()
    still_defined_on_disk: set[str] = set()
    all_added_lines: list[str] = []
    unverified_source_files: list[str] = []
    file_to_deleted_symbols: dict[str, set[str]] = {}

    diff_text = textwrap.dedent(diff_text)
    file_diffs = re.split(r"(?=^diff --git |^--- )", diff_text, flags=re.MULTILINE)
    for f_diff in file_diffs:
        if not f_diff.strip():
            continue

        file_path = ""
        first_line = f_diff.splitlines()[0] if f_diff.startswith("diff --git ") else ""
        header_paths = parse_diff_git_header(first_line) if first_line else None
        if header_paths:
            file_path = decode_git_diff_path(header_paths[1], is_source=False)
        else:
            m_patch = re.search(r"^\+\+\+\s+(.+?)(?:\n|$)", f_diff, re.MULTILINE)
            if m_patch:
                file_path = decode_git_diff_path(m_patch.group(1).strip(), is_source=False)

        hunk_texts = re.split(r"(?=^@@\s+-\d+.*?\s+\+\d+.*?\s+@@)", f_diff, flags=re.MULTILINE)
        file_d_syms: set[str] = set()

        for h_text in hunk_texts:
            if not h_text.startswith("@@"):
                continue

            lines = h_text.splitlines()[1:]
            pre_lines: list[str] = []
            post_lines: list[str] = []
            deleted_lines: list[str] = []
            added_lines: list[str] = []
            context_lines: list[str] = []

            for line in lines:
                if line.startswith(" "):
                    clean_line = line[1:]
                    pre_lines.append(clean_line)
                    post_lines.append(clean_line)
                    context_lines.append(clean_line)
                elif line.startswith("-") and not line.startswith("---"):
                    clean_line = line[1:]
                    pre_lines.append(clean_line)
                    deleted_lines.append(clean_line)
                elif line.startswith("+") and not line.startswith("+++"):
                    clean_line = line[1:]
                    post_lines.append(clean_line)
                    added_lines.append(clean_line)
                    all_added_lines.append(clean_line)

            pre_defs = _extract_defs_for_file(pre_lines, file_path)
            post_defs = _extract_defs_for_file(post_lines, file_path)
            deleted_defs = _extract_defs_for_file(deleted_lines, file_path)
            added_defs = _extract_defs_for_file(added_lines, file_path)
            ctx_defs = _extract_defs_for_file(context_lines, file_path)

            for s in ctx_defs:
                surviving_context_defs.add(s)

            for s in pre_defs:
                if s not in post_defs:
                    deleted_symbols.add(s)
                    file_d_syms.add(s)

            for s in deleted_defs:
                if s not in post_defs and s not in added_defs:
                    deleted_symbols.add(s)
                    file_d_syms.add(s)

            for s in added_defs:
                reintroduced_symbols.add(s)

        if file_path:
            file_to_deleted_symbols[file_path] = file_d_syms

        is_file_deleted = bool(
            re.search(r"^deleted file mode \d+", f_diff, re.MULTILINE)
            or re.search(r"^\+\+\+\s+/dev/null", f_diff, re.MULTILINE)
        )
        if file_path and file_path != "/dev/null" and not is_file_deleted and not _is_doc_file(file_path):
            if workspace is None:
                unverified_source_files.append(file_path)
            else:
                source_file = (workspace / file_path).resolve()
                if not source_file.is_file():
                    unverified_source_files.append(file_path)
                else:
                    try:
                        content = source_file.read_text(encoding="utf-8")
                        disk_defs = _extract_defs_for_file(
                            content.splitlines(), file_path, is_full_file=True
                        )
                        if (
                            Path(file_path).suffix.lower() in _PY_EXTENSIONS
                            and _extract_python_defs_from_code(content) is None
                        ):
                            unverified_source_files.append(file_path)
                            continue
                        for s in disk_defs:
                            still_defined_on_disk.add(s)
                            if s in deleted_symbols:
                                deleted_symbols.remove(s)
                    except (OSError, UnicodeDecodeError):
                        unverified_source_files.append(file_path)

    return _DiffSymbolAnalysis(
        deleted_symbols=deleted_symbols,
        reintroduced_symbols=reintroduced_symbols,
        surviving_context_defs=surviving_context_defs,
        still_defined_on_disk=still_defined_on_disk,
        all_added_lines=all_added_lines,
        unverified_source_files=tuple(unverified_source_files),
        file_to_deleted_symbols=file_to_deleted_symbols,
        deleted_paths=_extract_deleted_paths(diff_text),
    )


def _symbol_hunk_reintroduced(symbol: str, analysis: _DiffSymbolAnalysis) -> bool:
    """True when the diff itself visibly re-adds a definition of the symbol."""
    return symbol in analysis.reintroduced_symbols


def _verify_symbol_targets(
    basis: RetirementBasis,
    analysis: _DiffSymbolAnalysis,
    workspace_evidence: _WorkspaceEvidence | None,
) -> tuple[list[AntiEntropyFinding], set[str]]:
    """Evaluate each target symbol; returns (findings, certified_retired_symbols)."""
    findings: list[AntiEntropyFinding] = []
    certified: set[str] = set()

    for symbol in basis.target_symbols:
        # 1. Diff-visible zombie: definition re-added inside the diff itself.
        if _symbol_hunk_reintroduced(symbol, analysis):
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE002,
                    severity="blocking",
                    message=(
                        f"Target symbol '{symbol}' was slated for retirement but reintroduced "
                        "as an active definition in the diff."
                    ),
                    location=symbol,
                )
            )
            continue

        # 2. Symbol retirement is a derived fact about complete post-change
        # sources. Without an explicitly bound workspace it cannot be certified.
        if workspace_evidence is None:
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE004,
                    severity="blocking",
                    message=(
                        f"Target symbol '{symbol}' cannot be certified as retired: no source "
                        "workspace was bound. Pass an explicit workspace containing the complete "
                        "post-change sources; a diff alone does not prove definition removal."
                    ),
                    location=symbol,
                )
            )
            continue

        if workspace_evidence.scan_incomplete:
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE004,
                    severity="blocking",
                    message=(
                        f"Target symbol '{symbol}' cannot be certified as retired: workspace scan is "
                        f"incomplete ({workspace_evidence.incomplete_reason}). Complete post-change "
                        "source evidence is required."
                    ),
                    location=symbol,
                )
            )
            continue

        unverified_files = [
            f
            for f in analysis.unverified_source_files
            if symbol in analysis.file_to_deleted_symbols.get(f, set())
        ]
        if unverified_files:
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE004,
                    severity="blocking",
                    message=(
                        f"Target symbol '{symbol}' cannot be certified as retired: complete "
                        f"post-change source is unavailable in the bound workspace for {', '.join(unverified_files)}."
                    ),
                    location=symbol,
                )
            )
            continue

        # 3. Real references in post-change sources (AST identifiers exclude
        # string literals, comments and docstring examples).
        ref_files = sorted(workspace_evidence.referenced_symbols.get(symbol, set()))
        if ref_files:
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE002,
                    severity="blocking",
                    message=(
                        f"Target symbol '{symbol}' was slated for retirement but is still "
                        f"referenced by post-change sources: {', '.join(ref_files)}."
                    ),
                    location=symbol,
                )
            )
            continue

        # 4. Definition removal: complete workspace scan is authoritative, so a
        # retained definition is caught even when the diff only edited prose.
        def_files = sorted(workspace_evidence.defined_symbols.get(symbol, set()))
        if def_files:
            severity = "advisory" if basis.allow_compat_seam else "blocking"
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE001,
                    severity=severity,
                    message=(
                        f"Target symbol '{symbol}' was declared for retirement but its definition "
                        f"is still present in post-change sources: {', '.join(def_files)}. "
                        "Delete-first requires removing superseded definitions rather than "
                        "merely removing calls, altering docs, or adding wrappers."
                    ),
                    location=symbol,
                )
            )
            continue

        # 5. Unchanged context lines are diff-visible facts: a definition kept as
        # hunk context was never removed by this diff.
        if symbol in analysis.surviving_context_defs:
            if workspace_evidence is None:
                findings.append(
                    AntiEntropyFinding(
                        rule_id=_RULE_AE001,
                        severity="blocking" if not basis.allow_compat_seam else "advisory",
                        message=(
                            f"Target symbol '{symbol}' was declared for retirement but its "
                            "definition is still present in unchanged diff context lines. "
                            "Delete-first requires removing superseded definitions."
                        ),
                        location=symbol,
                    )
                )
            else:
                findings.append(
                    AntiEntropyFinding(
                        rule_id=_RULE_AE004,
                        severity="blocking",
                        message=(
                            f"Target symbol '{symbol}' appears as a retained definition in the diff "
                            "context but was not found in the bound workspace sources; the patch "
                            "does not match the bound post-change sources."
                        ),
                        location=symbol,
                    )
                )
            continue

        # 6. No definition remains anywhere in the bound workspace. Positive
        # deletion evidence in the diff attributes the retirement to this change.
        if symbol in analysis.deleted_symbols or symbol in analysis.deleted_paths:
            certified.add(symbol)

    return findings, certified


def _verify_path_targets(
    basis: RetirementBasis,
    analysis: _DiffSymbolAnalysis,
    workspace_evidence: _WorkspaceEvidence | None,
) -> list[AntiEntropyFinding]:
    findings: list[AntiEntropyFinding] = []
    for path in basis.target_paths:
        norm_path = normalize_repo_path(path)
        if norm_path not in analysis.deleted_paths:
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE001,
                    severity="blocking" if not basis.allow_compat_seam else "advisory",
                    message=(
                        f"Target path '{path}' was declared for retirement in RetirementBasis "
                        "but was not removed in the diff."
                    ),
                    location=path,
                )
            )
            continue
        if workspace_evidence is not None and norm_path != "/dev/null":
            disk_path = (workspace_evidence.root / norm_path).resolve()
            if disk_path.exists():
                findings.append(
                    AntiEntropyFinding(
                        rule_id=_RULE_AE004,
                        severity="blocking",
                        message=(
                            f"Target path '{path}' is deleted in the diff but still exists in the "
                            "bound workspace; the patch does not match the bound post-change sources."
                        ),
                        location=path,
                    )
                )
    return findings


def verify_delete_first(
    diff_text: str,
    retirement_basis: RetirementBasis | Mapping[str, Any] | None = None,
    workspace: str | Path | None = None,
    _analysis: _DiffSymbolAnalysis | None = None,
) -> list[AntiEntropyFinding]:
    findings: list[AntiEntropyFinding] = []

    if retirement_basis is None:
        return findings

    basis = (
        retirement_basis
        if isinstance(retirement_basis, RetirementBasis)
        else RetirementBasis.from_dict(retirement_basis)
    )

    if not basis.target_symbols and not basis.target_paths:
        return findings

    diff_text = textwrap.dedent(diff_text)
    analysis = _analysis or _analyze_diff_symbols(diff_text, Path(workspace) if workspace else None)

    if not diff_text.strip():
        for symbol in basis.target_symbols:
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE001,
                    severity="blocking" if not basis.allow_compat_seam else "advisory",
                    message=(
                        f"Target symbol '{symbol}' was declared for retirement but diff is empty. "
                        "Delete-first requires explicit removal of superseded logic."
                    ),
                    location=symbol,
                )
            )
        for path in basis.target_paths:
            findings.append(
                AntiEntropyFinding(
                    rule_id=_RULE_AE001,
                    severity="blocking" if not basis.allow_compat_seam else "advisory",
                    message=(
                        f"Target path '{path}' was declared for retirement but diff is empty."
                    ),
                    location=path,
                )
            )
        return findings

    workspace_evidence: _WorkspaceEvidence | None = None
    if workspace is not None and (basis.target_symbols or basis.target_paths):
        workspace_evidence = _scan_workspace(Path(workspace), basis.target_symbols)

    symbol_findings, _certified = _verify_symbol_targets(basis, analysis, workspace_evidence)
    findings.extend(symbol_findings)
    findings.extend(_verify_path_targets(basis, analysis, workspace_evidence))
    return findings


def audit_ghost_code(diff_text: str) -> list[AntiEntropyFinding]:
    findings: list[AntiEntropyFinding] = []
    consecutive_commented_code = 0
    start_line = ""

    diff_text = textwrap.dedent(diff_text)
    file_diffs = re.split(r"(?=^diff --git |^--- )", diff_text, flags=re.MULTILINE)

    for f_diff in file_diffs:
        if not f_diff.strip():
            continue

        file_path = ""
        m_git = re.search(r"^diff --git\s+(.+?)\s+(.+?)(?:\n|$)", f_diff, re.MULTILINE)
        if m_git:
            file_path = unquote_git_path(m_git.group(2).strip())
        else:
            m_patch = re.search(r"^\+\+\+\s+(.+?)(?:\n|$)", f_diff, re.MULTILINE)
            if m_patch:
                file_path = unquote_git_path(m_patch.group(1).strip())

        ext = Path(file_path).suffix.lower() if file_path else ""
        if ext in _DOC_EXTENSIONS:
            continue

        consecutive_commented_code = 0
        start_line = ""

        for line in f_diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                consecutive_commented_code = 0
                continue

            raw = line[1:].strip()
            comment_match = _COMMENT_LINE_RE.match(raw)
            if comment_match:
                comment_content = comment_match.group(1).strip()
                is_code_stmt = False

                if _PY_DEF_OR_CLASS_RE.match(comment_content):
                    is_code_stmt = True
                elif any(p.match(comment_content) for p in _TS_DEF_PATTERNS):
                    is_code_stmt = True
                elif comment_content.endswith(";") or comment_content.startswith(("import ", "from ", "export ", "return ", "if ", "for ")):
                    is_code_stmt = True
                else:
                    try:
                        parsed = ast.parse(comment_content)
                        if len(parsed.body) == 1 and isinstance(parsed.body[0], ast.Expr):
                            val = parsed.body[0].value
                            if isinstance(val, (ast.Name, ast.Constant)):
                                is_code_stmt = False
                            elif isinstance(val, ast.Tuple) and all(isinstance(elt, (ast.Name, ast.Constant)) for elt in val.elts):
                                is_code_stmt = False
                            else:
                                is_code_stmt = True
                        else:
                            is_code_stmt = True
                    except SyntaxError:
                        is_code_stmt = False

                if is_code_stmt:
                    consecutive_commented_code += 1
                    if consecutive_commented_code == 1:
                        start_line = raw
                else:
                    consecutive_commented_code = 0
            else:
                consecutive_commented_code = 0

            if consecutive_commented_code >= 3:
                findings.append(
                    AntiEntropyFinding(
                        rule_id=_RULE_AE003,
                        severity="advisory",
                        message=(
                            "Detected 3+ consecutive lines of commented-out code. "
                            "Consider complete removal under version control rather than ghost commenting."
                        ),
                        location=start_line[:80],
                    )
                )
                consecutive_commented_code = 0

    return findings


def evaluate_diff_hygiene(
    diff_text: str,
    retirement_basis: RetirementBasis | Mapping[str, Any] | None = None,
    workspace: str | Path | None = None,
) -> AntiEntropyReceipt:
    diff_text = textwrap.dedent(diff_text)
    diff_hash = _hash_diff(diff_text)
    all_findings: list[AntiEntropyFinding] = []

    basis: RetirementBasis | None = None
    if retirement_basis is not None:
        basis = (
            retirement_basis
            if isinstance(retirement_basis, RetirementBasis)
            else RetirementBasis.from_dict(retirement_basis)
        )

    workspace_path = Path(workspace) if workspace is not None else None
    source_evidence = "workspace_sources" if workspace_path is not None else "diff_only"

    analysis = _analyze_diff_symbols(diff_text, workspace_path)

    if basis is not None and (basis.target_symbols or basis.target_paths):
        workspace_evidence: _WorkspaceEvidence | None = None
        if workspace_path is not None and (basis.target_symbols or basis.target_paths):
            workspace_evidence = _scan_workspace(workspace_path, basis.target_symbols)
        symbol_findings, certified = _verify_symbol_targets(basis, analysis, workspace_evidence)
        all_findings.extend(symbol_findings)
        all_findings.extend(_verify_path_targets(basis, analysis, workspace_evidence))
    else:
        certified = set()

    all_findings.extend(audit_ghost_code(diff_text))

    has_blocking = any(f.severity == "blocking" for f in all_findings)
    verdict = "rejected" if has_blocking else "passed"

    retired: list[str] = sorted(certified)

    return AntiEntropyReceipt(
        diff_hash=diff_hash,
        verdict=verdict,
        findings=tuple(all_findings),
        retired_symbols=tuple(retired),
        basis_hash=hash_retirement_basis(basis) if basis is not None else "",
        source_root=str(workspace_path) if workspace_path is not None else None,
        source_evidence=source_evidence,
    )
