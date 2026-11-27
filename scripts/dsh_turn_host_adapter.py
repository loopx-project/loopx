#!/usr/bin/env python3
"""Backward-compatible launcher for the LoopX DeepSeek Harness host adapter.

The adapter implementation moved to the host-adapter subpackage
``loopx/hosts/dsh``. This launcher keeps the historical path working:
existing commands, examples, and docs that call
``python3 scripts/dsh_turn_host_adapter.py`` or import
``dsh_turn_host_adapter`` resolve to the same implementation. Prefer
``python -m loopx.hosts.dsh`` in new code.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.hosts.dsh import (  # noqa: E402,F401
    ACCEPTED_RESULT_KINDS,
    COMPLETED_PHASES,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SESSION_ROOT_NAME,
    LOOPX_TURN_HOST_REQUEST_SCHEMA,
    LOOPX_TURN_RESULT_SCHEMA,
    MATERIAL_KINDS,
    TEXT_LIMITS,
    build_result,
    extract_action_text,
    extract_turn_authority,
    load_dsh_runner,
    main,
    parse_model_json,
    render_prompt,
    run_dsh_turn,
)

if __name__ == "__main__":
    raise SystemExit(main())
