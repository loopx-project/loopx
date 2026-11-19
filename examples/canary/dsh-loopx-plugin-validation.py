#!/usr/bin/env python3
"""Run bounded validation phases for the co-located DSH LoopX plugin."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages" / "dsh-loopx-plugin"
PHASE_SCRIPTS = {
    "quality": ("typecheck", "test", "smoke:peer-range", "smoke:mcp-transport"),
    "package": ("build", "smoke:artifact", "smoke:profile"),
    "runtime": ("smoke:runtime",),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=tuple(PHASE_SCRIPTS), required=True)
    args = parser.parse_args()

    pnpm = shutil.which("pnpm")
    if not pnpm:
        print("pnpm is required for DSH plugin validation", file=sys.stderr)
        return 2

    for script in PHASE_SCRIPTS[args.phase]:
        print(f"dsh-loopx-plugin validation: pnpm run {script}", flush=True)
        completed = subprocess.run(
            [pnpm, "run", script],
            cwd=PACKAGE_ROOT,
            check=False,
        )
        if completed.returncode:
            if args.phase == "runtime":
                print(
                    "runtime validation requires local child-process and loopback "
                    "socket access; rerun this command outside a restricted sandbox",
                    file=sys.stderr,
                )
            return completed.returncode

    print(f"dsh-loopx-plugin {args.phase} validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
