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
from pathlib import Path

from .scanner import compact_json, scan_paths


def _iter_text_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {
            ".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json",
            ".jsonl", ".sh", ".mjs", ".js", ".ts", ".rs", ".go", ".conf",
        }:
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
