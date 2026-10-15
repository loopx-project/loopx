"""Standalone entrypoint for the public-safe-outbound scanner.

Usage:
  python -m loopx.capabilities.public_safe_outbound.scan_cli --scan-root <repo>
  git diff --cached | python -m loopx.capabilities.public_safe_outbound.scan_cli --diff

Exit 0 = clean (public-safe); exit 1 = any hit, or any input the scan could
not read (fail-closed). A configured PUBLIC_SAFE_CONFIG that cannot be read
also exits 1 rather than downgrading to the generic rules, and so does a scan
root that does not exist or a directory under it that cannot be listed.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from ...entrypoint import pin_utf8_stdio
from .scanner import compact_json, scan_paths

# Version-control metadata names. They are excluded wherever they appear below
# the scan root, as a directory or as a file: in a linked worktree `.git` is a
# *file* holding a `gitdir: /abs/path` pointer, which would otherwise be
# reported as a private absolute path on every scan of the checkout.
VCS_METADATA_NAMES = frozenset({".git", ".hg", ".svn"})

# Directories whose contents tools write rather than people author in the
# scanned tree: dependency installs, caches, build and coverage output, and
# virtualenvs. They hold no first-party text to review, and walking them spends
# the scan budget on vendored copies. Only a directory is matched; a file that
# happens to be named `build` is still scanned. This is this scan's own policy
# for walking a candidate tree on disk, not a shared source filter.
TOOL_MANAGED_DIRECTORY_NAMES = frozenset({
    "node_modules", "bower_components",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    ".cache", ".tox", ".nox", ".eggs",
    ".venv", "venv",
    "dist", "build", ".next", "coverage", "htmlcov",
})

# Suffixes whose contents are not reviewable text. Reading them as UTF-8 with
# `errors="replace"` would produce replacement-character noise that can match a
# rule by accident. SVG is XML and stays scanned; source maps are build output
# and do not.
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tif", ".tiff",
    ".mp3", ".wav", ".ogg", ".oga", ".flac", ".m4a", ".aac",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".tar", ".whl", ".jar", ".war", ".7z",
    ".pyc", ".pyo", ".class", ".o", ".a", ".so", ".dylib", ".dll", ".exe",
    ".bin", ".wasm", ".tsbuildinfo", ".map",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".sqlite", ".sqlite3", ".db", ".pdf", ".docx", ".xlsx", ".pptx",
})


# Content-free reasons for a candidate tree the walk could not fully see. Like
# the scanner's own unscanned reasons they carry no path: the receipt must not
# echo the absolute location it exists to keep out of public text.
SCAN_ROOT_MISSING = "scan_root_missing"
DIRECTORY_UNREADABLE = "directory_unreadable"


def _pruned_directory(name: str) -> bool:
    return name in VCS_METADATA_NAMES or name in TOOL_MANAGED_DIRECTORY_NAMES


def _is_candidate(relative_parts: tuple[str, ...]) -> bool:
    """Whether a file below the scan root belongs to the outbound candidate set."""
    *directories, name = relative_parts
    if name in VCS_METADATA_NAMES or any(_pruned_directory(part) for part in directories):
        return False
    return Path(name).suffix.lower() not in BINARY_SUFFIXES


def _iter_text_files(root: Path, *, unscanned: list[str] | None = None) -> Iterator[Path]:
    """Yield every regular file under ``root`` that could carry scannable text.

    Selection is a deny-list, not a suffix allow-list. An allow-list silently
    skipped ``.env``, ``Dockerfile``, ``Makefile``, ``id_rsa`` and every other
    extension-less or credential-named file -- the names that most often hold
    exactly what this scan exists to catch -- while ``node_modules`` and
    ``.venv`` were walked because ``.js`` and ``.py`` were allowed. What is
    left is read by ``scan_paths``, which bounds each file by ``limit_bytes``,
    so a stray binary cannot exhaust memory.

    A file given as the root is the caller's explicit choice and is scanned
    unless it is binary. Below a directory root, only the relative path counts:
    where the root itself lives never excludes what is under it. Pruned
    directories are never entered, so their contents cannot fail the walk.

    A root that does not exist, or a directory (the root included) that cannot
    be listed, names content the caller asked to check and the walk did not
    see. Each is appended to ``unscanned`` as a content-free reason; without a
    collector the listing error is raised instead of being skipped, because a
    silently shortened candidate set would read as a clean scan.
    """
    def unlisted(error: OSError) -> None:
        if unscanned is None:
            raise error
        unscanned.append(DIRECTORY_UNREADABLE)

    if not root.is_dir():
        if root.is_file():
            if root.suffix.lower() not in BINARY_SUFFIXES:
                yield root
        elif not root.exists():
            if unscanned is None:
                raise FileNotFoundError(SCAN_ROOT_MISSING)
            unscanned.append(SCAN_ROOT_MISSING)
        return
    # Like Path.rglob, os.walk does not follow symlinked directories by
    # default; unlike rglob, it reports a directory it cannot list.
    for directory, dirnames, filenames in os.walk(root, onerror=unlisted):
        dirnames[:] = sorted(name for name in dirnames if not _pruned_directory(name))
        base = Path(directory)
        for name in sorted(filenames):
            path = base / name
            if path.is_file() and _is_candidate(path.relative_to(root).parts):
                yield path


def main() -> int:
    pin_utf8_stdio()
    p = argparse.ArgumentParser()
    p.add_argument("--scan-root", type=Path, default=None)
    p.add_argument("--diff", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    args = p.parse_args()

    if args.scan_root:
        unseen: list[str] = []
        result = scan_paths(_iter_text_files(args.scan_root, unscanned=unseen), root=args.scan_root)
        if unseen:
            result.unscanned.extend(unseen)
            result.ok = False
    elif args.diff:
        diff = sys.stdin.read()
        from .scanner import scan_texts

        result = scan_texts({"<git-diff>": diff})
    else:
        p.error("specify --scan-root or --diff")

    if args.format == "json":
        print(compact_json(result))
    else:
        for hit in result.hits:
            line = f":{hit.line}" if hit.line is not None else ""
            print(f"[{hit.rule_id}] {hit.label} @ {hit.location}{line}")
        if result.unscanned:
            print(f"UNSCANNED: {sorted(set(result.unscanned))}")
        if result.ok:
            print("PASS: public-safe")
        elif result.hits:
            print("FAIL: hits found")
        else:
            print("FAIL: input could not be scanned")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
