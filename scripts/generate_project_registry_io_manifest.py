#!/usr/bin/env python3
"""Compute the project-registry I/O census; optionally export a local report."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from loopx.semantics.project_registry_io import (  # noqa: E402
    build_project_registry_io_manifest,
    render_project_registry_io_manifest,
    validate_project_registry_io_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare an explicit --output report without writing")
    parser.add_argument("--output", type=Path, help="write an optional report here instead of JSON stdout")
    args = parser.parse_args()
    if args.check and args.output is None:
        parser.error("--check requires --output; the census is no longer committed. "
                     "Run examples/semantic-vocabulary-drift-smoke.py for semantic validation.")

    root = ROOT
    manifest = build_project_registry_io_manifest(root)
    content = render_project_registry_io_manifest(manifest)
    errors = validate_project_registry_io_manifest(root, manifest)
    for error in errors:
        print(error, file=sys.stderr)
    if args.output is None:
        print(content, end="")
        return 1 if errors else 0
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.is_file() else None
        if current != content:
            print(f"stale or missing project registry I/O report: {args.output}; "
                  "rerun with the same --output path without --check", file=sys.stderr)
            return 1
        print(f"project registry I/O report is current: {len(manifest['sites'])} sites")
        return 1 if errors else 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"wrote project registry I/O report: {args.output} ({len(manifest['sites'])} sites)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
