"""Dual-Model Behavior Constraint x Efficiency Microbenchmark Suite (M1/M2/M3).

Scope boundary: owns bounded Tier-0 (<15s deterministic differential) and
Tier-1 (1-3m Base vs Head dual-model scenarios: Codex GPT + DeepSeek V4.1
Flash ``deepseek-v4-1-flash-260910``) across M1 (#9498/#9504 envelope/recovery),
M2 (#9503 epoch prefetch / semantic-unit fold gate), and M3 (basic
claim/block/settle invariants). Long-horizon M4/M5 studies remain in
``benchmark/edgebench/``.
"""

from __future__ import annotations

from .metrics import (
    MICROBENCH_DIFF_SCHEMA_VERSION,
    MICROBENCH_RECEIPT_SCHEMA_VERSION,
    BehaviorConstraintResult,
    EfficiencyMetrics,
    assert_public_safe_payload,
    collect_codex_rollout_metrics,
    compare_base_vs_head_efficiency,
    measure_text_tokens,
)
from .models import (
    DEFAULT_ARK_FLASH_MODEL,
    DEFAULT_CODEX_MODEL,
)
from .scenarios import (
    MICROBENCH_SCENARIO_IDS,
    prepare_m1_fixture,
    prepare_m2_fixture,
    prepare_m3_fixture,
    run_m1_deterministic_arm,
    run_m2_deterministic_arm,
    run_m3_deterministic_arm,
)
from .tier0 import run_tier0_differential
from .tier1 import run_tier1_dual_model_suite

__all__ = [
    "BehaviorConstraintResult",
    "DEFAULT_ARK_FLASH_MODEL",
    "DEFAULT_CODEX_MODEL",
    "EfficiencyMetrics",
    "MICROBENCH_DIFF_SCHEMA_VERSION",
    "MICROBENCH_RECEIPT_SCHEMA_VERSION",
    "MICROBENCH_SCENARIO_IDS",
    "assert_public_safe_payload",
    "collect_codex_rollout_metrics",
    "compare_base_vs_head_efficiency",
    "measure_text_tokens",
    "prepare_m1_fixture",
    "prepare_m2_fixture",
    "prepare_m3_fixture",
    "run_m1_deterministic_arm",
    "run_m2_deterministic_arm",
    "run_m3_deterministic_arm",
    "run_tier0_differential",
    "run_tier1_dual_model_suite",
]
