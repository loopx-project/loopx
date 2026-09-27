"""Text-mode file reads and writes in the shipped runtime must pin UTF-8 explicitly.

`Path.read_text()` / `Path.write_text()` and text-mode `open()` without
`encoding=` decode and encode with `locale.getpreferredencoding(False)` - `cp936`
on a zh-CN Windows host. LoopX writes structured state (run index, backlog,
machine configuration, receipts) as UTF-8, so on such a host one non-ASCII
character either raises `UnicodeDecodeError` on read or writes bytes the next
UTF-8 reader cannot decode.

#4338, #4942 and #4997 fixed the reported sites. This guard keeps the class from
returning, next to `test_runtime_subprocess_utf8.py`, which does the same for
text-mode subprocess reads.
"""

from __future__ import annotations

import ast
import codecs
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# `utf-8-sig` is a UTF-8 codec that tolerates a leading BOM on read; it is not
# locale-dependent, so it cannot reopen the reported failure.
ALLOWED_ENCODINGS = frozenset({"utf-8", "utf-8-sig"})

# Positional index of the `encoding` parameter for the two `Path` helpers, whose
# signatures differ: `read_text(encoding, errors)` and `write_text(data, encoding,
# errors)`. A positional codec must be recognised, or the guard would report a
# call that does pin UTF-8.
_PATH_HELPER_ENCODING_INDEX = {"read_text": 0, "write_text": 1}


def _is_codec_name(value: str) -> bool:
    """Whether a string literal is a real codec name, as `pathlib` would accept it."""

    try:
        codecs.lookup(value)
    except LookupError:
        return False
    return True


def _is_path_helper(node: ast.Call, attribute: str) -> bool:
    """Whether the call's arguments fit the `pathlib` signature rather than a keyed lookup.

    `installed.read_text("INSTALLER")` shares the method name but takes a registry
    key, not a codec, and is not file I/O. A literal positional argument that is
    not a codec name can only be the other shape.
    """

    index = _PATH_HELPER_ENCODING_INDEX[attribute]
    if len(node.args) <= index:
        return True
    argument = node.args[index]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return _is_codec_name(argument.value)
    return True


def _pinned_encoding(node: ast.Call, attribute: str | None) -> str | None:
    """The literal codec this call pins, or None when it pins nothing provable."""

    for keyword in node.keywords:
        if keyword.arg != "encoding":
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
        return None
    if attribute is not None:
        index = _PATH_HELPER_ENCODING_INDEX.get(attribute)
        if index is not None and len(node.args) > index:
            argument = node.args[index]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                return argument.value
    return None


def _opens_in_text_mode(node: ast.Call) -> bool:
    """Whether a builtin `open()` call is not provably binary."""

    mode: str | None = None
    if len(node.args) >= 2:
        argument = node.args[1]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            mode = argument.value
        else:
            return True  # a dynamic mode is not proven binary
    for keyword in node.keywords:
        if keyword.arg != "mode":
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            mode = keyword.value.value
        else:
            return True
    return mode is None or "b" not in mode


def _text_io_calls_without_utf8_encoding(package_root: Path | None = None) -> list[str]:
    """Locate text-mode file reads and writes that do not pin UTF-8.

    Reporting only a missing `encoding` keyword is too weak: `encoding="latin-1"`
    reproduces the same locale bug with an explicit codec, so the pinned value is
    checked as well.
    """

    root = package_root or (REPO_ROOT / "loopx")
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attribute = node.func.attr if isinstance(node.func, ast.Attribute) else None
            is_path_helper = attribute in _PATH_HELPER_ENCODING_INDEX and _is_path_helper(node, attribute)
            is_builtin_open = isinstance(node.func, ast.Name) and node.func.id == "open"
            if not (is_path_helper or is_builtin_open):
                continue
            if is_builtin_open and not _opens_in_text_mode(node):
                continue
            if _pinned_encoding(node, attribute) in ALLOWED_ENCODINGS:
                continue
            offenders.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    return offenders


def test_shipped_runtime_pins_utf8_for_every_text_file_read_and_write() -> None:
    """A text read or write without an explicit codec reopens the #4941 failure."""

    assert _text_io_calls_without_utf8_encoding() == []


def test_guard_reports_missing_and_non_utf8_codecs(tmp_path: Path) -> None:
    """The guard covers a wrong explicit codec and builtin `open`, not only a missing keyword."""

    package = tmp_path / "loopx"
    package.mkdir()
    (package / "sample.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def reads_and_writes(path: Path) -> None:\n"
        "    path.read_text()\n"
        "    path.write_text('x')\n"
        "    path.read_text(encoding='latin-1')\n"
        "    path.read_text(encoding='utf-8')\n"
        "    path.write_text('x', 'utf-8')\n"
        "    path.read_text(encoding='utf-8-sig')\n"
        "    open(path, encoding='utf-8')\n"
        "    open(path, 'rb')\n"
        "    open(path)\n"
        "    installed.read_text('INSTALLER')\n",
        encoding="utf-8",
    )

    offenders = _text_io_calls_without_utf8_encoding(package)

    # Line 14 is a keyed lookup on a non-path object: same method name, not file I/O.
    assert [entry.rsplit(":", 1)[-1] for entry in offenders] == ["5", "6", "7", "13"]
