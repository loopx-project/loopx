"""Premerge check for both existing RFC language mirrors in the same PR diff."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

RFC_ROOT = "docs/architecture/rfcs/"


def git_paths(*args: str) -> set[str]:
    raw = subprocess.check_output(["git", *args, "-z", "--", RFC_ROOT])
    return {os.fsdecode(path) for path in raw.split(b"\0") if path}


def revision(ref: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        text=True,
    ).strip()


def missing_mirrors(changed: set[str], existing: set[str]) -> list[tuple[str, str]]:
    missing = []
    for path in sorted(changed):
        if not path.startswith(RFC_ROOT) or not path.endswith(".md"):
            continue
        mirror = (
            path.removesuffix(".zh-CN.md") + ".md"
            if path.endswith(".zh-CN.md") else path.removesuffix(".md") + ".zh-CN.md"
        )
        if mirror in existing and mirror not in changed:
            missing.append((path, mirror))
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="check the index; --base includes earlier branch commits")
    parser.add_argument("--base", help="target-branch commit; compare from the merge base")
    parser.add_argument("--head", help="exact proposed commit, required with --base")
    args = parser.parse_args()
    if args.staged and args.head:
        parser.error("--head cannot be combined with --staged")
    if not args.staged and not (args.base and args.head):
        parser.error("supply --staged or both --base and --head")
    try:
        if args.staged:
            base = revision("HEAD")
            if args.base:
                base = subprocess.check_output(
                    ["git", "merge-base", revision(args.base), base], text=True,
                ).strip()
            changed = git_paths("diff", "--cached", "--name-only", "--no-renames", base)
            after = git_paths("ls-files", "--cached")
        else:
            head = revision(args.head)
            base = subprocess.check_output(
                ["git", "merge-base", revision(args.base), head], text=True,
            ).strip()
            changed = git_paths("diff", "--name-only", "--no-renames", base, head)
            after = git_paths("ls-tree", "-r", "--name-only", head)
        before = git_paths("ls-tree", "-r", "--name-only", base)
    except subprocess.CalledProcessError:
        print("Cannot read the RFC diff; fetch the exact base/head and retry.", file=sys.stderr)
        return 1
    missing = missing_mirrors(changed, before | after)
    if missing:
        print("RFC bilingual diff check failed:", file=sys.stderr)
        for path, mirror in missing:
            print(f"  {path}: include a matching change to {mirror}", file=sys.stderr)
        return 1
    print("RFC bilingual diff check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
