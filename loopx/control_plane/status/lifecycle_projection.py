"""Goal lifecycle projections inside the `status` bounded context."""

from __future__ import annotations

from typing import Any

from ..runtime.run_compaction import (
    compact_controller_readiness,
    compact_human_reward,
    compact_operator_gate,
)
from ..work_items.attention_fields import (
    operator_gate_attention_fields as _operator_gate_attention_fields,
    readiness_attention_fields as _readiness_attention_fields,
)
from ..work_items.lifecycle import (
    goal_lifecycle_fields as _goal_lifecycle_fields,
    ordered_lifecycle_flags as _ordered_lifecycle_flags,
    primary_lifecycle_phase as _primary_lifecycle_phase,
    run_lifecycle_flags as _run_lifecycle_flags,
    run_lifecycle_phase as _run_lifecycle_phase,
)
from ...operator_gate import (
    DEFAULT_OPERATOR_GATE,
    normalize_operator_question,
)
from .adapter_status_vocabulary import CONNECTED_ADAPTER_STATUSES

LIFECYCLE_PRIORITY = (
    "controller_ready",
    "reward_judged",
    "operator_approved",
    "controller_gated",
    "operator_gated",
    "adapter_inspected",
    "mapped",
    "refreshed",
    "connected",
    "registered",
    "planned",
    "run_recorded",
)


def ordered_lifecycle_flags(flags: list[str]) -> list[str]:
    return _ordered_lifecycle_flags(
        flags,
        lifecycle_priority=LIFECYCLE_PRIORITY,
    )


def primary_lifecycle_phase(flags: list[str], fallback: str = "registered") -> str:
    return _primary_lifecycle_phase(
        flags,
        lifecycle_priority=LIFECYCLE_PRIORITY,
        fallback=fallback,
    )


def run_lifecycle_flags(run: dict[str, Any] | None) -> list[str]:
    return _run_lifecycle_flags(
        run,
        lifecycle_priority=LIFECYCLE_PRIORITY,
        compact_human_reward=compact_human_reward,
        compact_operator_gate=compact_operator_gate,
        compact_controller_readiness=compact_controller_readiness,
    )


def run_lifecycle_phase(run: dict[str, Any] | None) -> str:
    return _run_lifecycle_phase(
        run,
        lifecycle_priority=LIFECYCLE_PRIORITY,
        compact_human_reward=compact_human_reward,
        compact_operator_gate=compact_operator_gate,
        compact_controller_readiness=compact_controller_readiness,
    )


def goal_lifecycle_fields(goal: dict[str, Any], current_run: dict[str, Any] | None) -> dict[str, Any]:
    return _goal_lifecycle_fields(
        goal,
        current_run,
        lifecycle_priority=LIFECYCLE_PRIORITY,
        connected_adapter_statuses=CONNECTED_ADAPTER_STATUSES,
        compact_human_reward=compact_human_reward,
        compact_operator_gate=compact_operator_gate,
        compact_controller_readiness=compact_controller_readiness,
    )


def readiness_attention_fields(run: dict[str, Any] | None) -> dict[str, Any]:
    return _readiness_attention_fields(
        run,
        compact_controller_readiness=compact_controller_readiness,
    )


def operator_gate_attention_fields(run: dict[str, Any] | None) -> dict[str, Any]:
    return _operator_gate_attention_fields(
        run,
        compact_operator_gate=compact_operator_gate,
        normalize_operator_question=normalize_operator_question,
        default_operator_gate=DEFAULT_OPERATOR_GATE,
    )
