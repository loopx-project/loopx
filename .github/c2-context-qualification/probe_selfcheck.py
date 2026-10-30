#!/usr/bin/env python3
"""Run the single bounded system-boundary self-check for the RPC probes."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    for name in ("rpc_probe.py", "auxiliary_probe.py"):
        subprocess.run([sys.executable, str(HERE / name), "--self-check"], check=True)
    print("PROBE_SELFCHECK complete; no LoopX CLI, model, or SDK calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
