"""Standalone entrypoint for the public-safe-outbound scanner.

Usage:
  python -m loopx.capabilities.public_safe_outbound.scan_cli --scan-root <repo>
  git diff --cached | python -m loopx.capabilities.public_safe_outbound.scan_cli --diff

Exit 0 = clean (public-safe); exit 1 = any hit, or any input the scan could
not read (fail-closed). A configured PUBLIC_SAFE_CONFIG that cannot be read
also exits 1 rather than downgrading to the generic rules.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

from .scanner import compact_json, scan_paths

# Version control, dependency, cache, build and virtualenv directories hold no
# first-party text to review, and walking them spends the scan budget on
# vendored copies. `.git`, `.hg` and `.svn` are also matched against a path's
# own final name: in a linked worktree `.git` is a *file* holding a
# `gitdir: /abs/path` pointer, which would otherwise be reported as a private
# absolute path on every scan of the checkout.
SKIP_PARTS = frozenset({
    ".git", ".hg", ".svn",
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


# VCS pointer names that must be skipped even when they name a file.
VCS_POINTER_NAMES = frozenset({".git", ".hg", ".svn"})


def _iter_text_files(root: Path) -> Iterator[Path]:
    """Yield every regular file under ``root`` that could carry scannable text.

    Selection is a deny-list, not a suffix allow-list. An allow-list silently
    skipped ``.env``, ``Dockerfile``, ``Makefile``, ``id_rsa`` and every other
    extension-less or credential-named file -- the names that most often hold
    exactly what this scan exists to catch -- while ``node_modules`` and
    ``.venv`` were walked because ``.js`` and ``.py`` were allowed. What is
    left is read by ``scan_paths``, which bounds each file by ``limit_bytes``,
    so a stray binary cannot exhaust memory.
    """
    for path in root.iterdir() if root.is_dir() else [root]:
        if path.is_dir():
            if VCS_POINTER_NAMES.intersection(path.parts) or SKIP_PARTS.intersection(
                path.relative_to(root).parts
            ):
                continue
            for nested in path.rglob("*"):
                if not nested.is_file():
                    continue
                parts = nested.relative_to(root).parts
                if SKIP_PARTS.intersection(parts[:-1]) or VCS_POINTER_NAMES.intersection(parts):
                    continue
                if nested.suffix.lower() in BINARY_SUFFIXES:
                    continue
                yield nested
            continue
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if SKIP_PARTS.intersection(parts[:-1]) or VCS_POINTER_NAMES.intersection(parts):
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        yield path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan-root", type=Path, default=None)
    p.add_argument("--diff", action="store_true")
    p.add_argument("--format", choices=["json", "text"], default="json")
    args = p.parse_args()

    if args.scan_root:
        result = scan_paths(_iter_text_files(args.scan_root), root=args.scan_root)
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
            print(f"[{hit.rule_id}] {hit.label} @ {hit.location}")
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
