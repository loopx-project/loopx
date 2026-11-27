"""First-class DeepSeek Harness (dsh) host adapter for LoopX goal mode.

The adapter turns one governed LoopX Turn into one bounded DeepSeek Harness
work segment and returns a typed ``loopx_turn_result_v0`` object. Authority
extraction, prompt rendering, result shaping, and the SDK runner live in
:mod:`loopx.dsh_goal_mode.turn_host_adapter`; run the adapter directly with
``python -m loopx.dsh_goal_mode``.
"""

from __future__ import annotations

from .turn_host_adapter import (
    ACCEPTED_RESULT_KINDS,
    COMPLETED_PHASES,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SESSION_ROOT_NAME,
    DSH_HOST_CAPABILITIES as DSH_HOST_CAPABILITIES,
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

ADAPTER_MODULE = "loopx.dsh_goal_mode"
COMPAT_LAUNCHER = "scripts/dsh_turn_host_adapter.py"

__all__ = [
    "ACCEPTED_RESULT_KINDS",
    "ADAPTER_MODULE",
    "COMPAT_LAUNCHER",
    "COMPLETED_PHASES",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "DEFAULT_REASONING_EFFORT",
    "DEFAULT_SESSION_ROOT_NAME",
    "LOOPX_TURN_HOST_REQUEST_SCHEMA",
    "LOOPX_TURN_RESULT_SCHEMA",
    "MATERIAL_KINDS",
    "TEXT_LIMITS",
    "build_result",
    "extract_action_text",
    "extract_turn_authority",
    "load_dsh_runner",
    "main",
    "parse_model_json",
    "render_prompt",
    "run_dsh_turn",
]
