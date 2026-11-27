"""Run the DeepSeek Harness host adapter with ``python -m loopx.hosts.dsh``."""

from __future__ import annotations

from .turn_host_adapter import main as main

if __name__ == "__main__":
    raise SystemExit(main())
