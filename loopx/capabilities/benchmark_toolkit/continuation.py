"""Read-only continuation decisions for runner-observed public progress."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any
from ...control_plane.content_digest import BARE_SHA256_PATTERN

BENCHMARK_PUBLIC_PROGRESS_SCHEMA_VERSION = "benchmark_public_progress_v0"
BENCHMARK_CONTINUATION_DECISION_SCHEMA_VERSION = "benchmark_continuation_decision_v0"

class BenchmarkContinuationDecision(str, Enum):
    CONTINUE = "continue"
    STOP_COMPLETE = "stop_complete"
    STOP_PROGRESS_REGRESSION = "stop_progress_regression"
    STOP_PROMPT_MISMATCH = "stop_prompt_mismatch"
    STOP_ROUND_LIMIT = "stop_round_limit"
    STOP_TASK_SHAPE_MISMATCH = "stop_task_shape_mismatch"
    STOP_TIME_BUDGET = "stop_time_budget"


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return int(value)


def _non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return int(value)


def _sha256_digest(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not BARE_SHA256_PATTERN.fullmatch(text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


def normalize_benchmark_public_progress(
    value: Mapping[str, Any],
) -> dict[str, int | str]:
    """Validate one content-free progress observation from the benchmark runner."""

    if set(value) != {
        "schema_version",
        "total_unit_count",
        "completed_unit_count",
    }:
        raise ValueError("benchmark_public_progress_fields_invalid")
    if value.get("schema_version") != BENCHMARK_PUBLIC_PROGRESS_SCHEMA_VERSION:
        raise ValueError("benchmark_public_progress_schema_unsupported")
    total = _positive_int(value.get("total_unit_count"), field="total_unit_count")
    completed = _non_negative_int(
        value.get("completed_unit_count"), field="completed_unit_count"
    )
    if completed > total:
        raise ValueError("completed_unit_count exceeds total_unit_count")
    return {
        "schema_version": BENCHMARK_PUBLIC_PROGRESS_SCHEMA_VERSION,
        "total_unit_count": total,
        "completed_unit_count": completed,
    }


def build_benchmark_continuation_decision(
    progress: Mapping[str, Any],
    *,
    expected_first_prompt_sha256: str,
    observed_first_prompt_sha256: str,
    expected_total_unit_count: int,
    previous_completed_unit_count: int,
    completed_segment_count: int,
    max_agent_segments: int,
    elapsed_ms: int,
    total_budget_ms: int,
) -> dict[str, Any]:
    """Decide whether another solver segment fits the frozen run envelope."""

    normalized = normalize_benchmark_public_progress(progress)
    expected_prompt = _sha256_digest(
        expected_first_prompt_sha256, field="expected_first_prompt_sha256"
    )
    observed_prompt = _sha256_digest(
        observed_first_prompt_sha256, field="observed_first_prompt_sha256"
    )
    prompt_matches = expected_prompt == observed_prompt
    expected_total = _positive_int(
        expected_total_unit_count, field="expected_total_unit_count"
    )
    previous = _non_negative_int(
        previous_completed_unit_count, field="previous_completed_unit_count"
    )
    completed_segments = _positive_int(
        completed_segment_count, field="completed_segment_count"
    )
    max_segments = _positive_int(max_agent_segments, field="max_agent_segments")
    if completed_segments > max_segments:
        raise ValueError("completed_segment_count exceeds max_agent_segments")
    elapsed = _non_negative_int(elapsed_ms, field="elapsed_ms")
    budget = _positive_int(total_budget_ms, field="total_budget_ms")
    completed = int(normalized["completed_unit_count"])
    total = int(normalized["total_unit_count"])
    if previous > total:
        raise ValueError("previous_completed_unit_count exceeds total_unit_count")
    remaining_budget = max(0, budget - elapsed)
    remaining_segments = max_segments - completed_segments
    task_shape_matches = total == expected_total

    if not prompt_matches:
        decision = BenchmarkContinuationDecision.STOP_PROMPT_MISMATCH
        reason = "first_prompt_digest_mismatch"
    elif not task_shape_matches:
        decision = BenchmarkContinuationDecision.STOP_TASK_SHAPE_MISMATCH
        reason = "total_unit_count_mismatch"
    elif completed < previous:
        decision = BenchmarkContinuationDecision.STOP_PROGRESS_REGRESSION
        reason = "public_progress_regressed"
    elif completed == total:
        decision = BenchmarkContinuationDecision.STOP_COMPLETE
        reason = "all_units_complete"
    elif remaining_budget == 0:
        decision = BenchmarkContinuationDecision.STOP_TIME_BUDGET
        reason = "total_agent_budget_exhausted"
    elif remaining_segments == 0:
        decision = BenchmarkContinuationDecision.STOP_ROUND_LIMIT
        reason = "agent_segment_limit_reached"
    else:
        decision = BenchmarkContinuationDecision.CONTINUE
        reason = (
            "requirements_remain_after_progress"
            if completed > previous
            else "requirements_remain_without_progress"
        )

    next_timeout_ms = (
        max(1, remaining_budget // remaining_segments)
        if decision is BenchmarkContinuationDecision.CONTINUE
        else 0
    )
    return {
        "ok": True,
        "schema_version": BENCHMARK_CONTINUATION_DECISION_SCHEMA_VERSION,
        "decision": decision.value,
        "reason_code": reason,
        "continuation_allowed": decision is BenchmarkContinuationDecision.CONTINUE,
        "total_unit_count": total,
        "expected_total_unit_count": expected_total,
        "task_shape_matches": task_shape_matches,
        "completed_unit_count": completed,
        "progress_delta": completed - previous,
        "completed_segment_count": completed_segments,
        "max_agent_segments": max_segments,
        "remaining_budget_ms": remaining_budget,
        "next_segment_timeout_ms": next_timeout_ms,
        "first_prompt_matches": prompt_matches,
        "first_prompt_digest_recorded": False,
        "continuation_prompt_policy": "original_task_plus_public_progress",
        "public_progress_only": True,
        "raw_task_recorded": False,
        "unit_ids_recorded": False,
        "path_recorded": False,
        "read_only": True,
        "host_invoked": False,
        "state_written": False,
    }
