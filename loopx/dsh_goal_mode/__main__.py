"""Compatibility entrypoint forwarding ``python -m loopx.dsh_goal_mode`` to ``loopx.hosts.dsh.__main__``."""

from __future__ import annotations

from ..hosts.dsh.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
