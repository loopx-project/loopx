"""Autonomous replan status projections inside the `status` bounded context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..work_items.replan_history_codec import (
    REPLAN_HISTORY_NEUTRAL_CLASSIFICATIONS as AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS,
)

from ..runtime.public_safety import public_safe_compact_text
from ..work_items.autonomous_replan_ack import (
    AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW,
    autonomous_replan_ack_recorded,
    latest_autonomous_replan_ack_for_projection as _latest_autonomous_replan_ack_for_projection,
)
from ..work_items.autonomous_replan_obligation import (
    AUTONOMOUS_REPLAN_STALL_THRESHOLD,
    autonomous_replan_obligation_from_runs as _autonomous_replan_obligation_from_runs,
    autonomous_replan_periodic_review_from_runs as _autonomous_replan_periodic_review_from_runs,
    build_autonomous_replan_obligation as _build_autonomous_replan_obligation,
    run_history_monitor_wait_already_acknowledged as _run_history_monitor_wait_already_acknowledged,
)


DEAD_MONITOR_REPEAT_THRESHOLD = 6
DEAD_MONITOR_REPEAT_SCHEMA_VERSION = "dead_monitor_repeat_v0"
AUTONOMOUS_REPLAN_SCHEMA_VERSION = "autonomous_replan_obligation_v0"
AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD = AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW


def build_autonomous_replan_obligation(
    evidence: list[dict[str, Any]],
    *,
    agent_todos: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return _build_autonomous_replan_obligation(
        evidence,
        agent_todos=agent_todos,
        public_safe_compact_text=public_safe_compact_text,
        autonomous_replan_schema_version=AUTONOMOUS_REPLAN_SCHEMA_VERSION,
        autonomous_replan_stall_threshold=AUTONOMOUS_REPLAN_STALL_THRESHOLD,
        dead_monitor_repeat_threshold=DEAD_MONITOR_REPEAT_THRESHOLD,
        dead_monitor_repeat_schema_version=DEAD_MONITOR_REPEAT_SCHEMA_VERSION,
    )


def run_history_monitor_wait_already_acknowledged(
    latest_runs: list[dict[str, Any]] | None,
    *,
    signal_count: int,
) -> bool:
    return _run_history_monitor_wait_already_acknowledged(
        latest_runs,
        signal_count=signal_count,
        autonomous_replan_ack_recorded=autonomous_replan_ack_recorded,
        neutral_classifications=AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS,
    )


def latest_autonomous_replan_ack_for_projection(
    latest_runs: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    return _latest_autonomous_replan_ack_for_projection(
        latest_runs,
        neutral_classifications=AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS,
    )


def autonomous_replan_periodic_review_from_runs(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_todos: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return _autonomous_replan_periodic_review_from_runs(
        latest_runs,
        agent_todos=agent_todos,
        autonomous_replan_ack_recorded=autonomous_replan_ack_recorded,
        neutral_classifications=AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS,
        periodic_run_threshold=AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD,
        build_autonomous_replan_obligation=build_autonomous_replan_obligation,
    )


def autonomous_replan_obligation_from_runs(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_todos: dict[str, Any] | None,
    agent_id: str | None = None,
    external_progress_review: Mapping[str, Any] | None = None,
    effective_turn_cadence: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return _autonomous_replan_obligation_from_runs(
        latest_runs,
        agent_todos=agent_todos,
        agent_id=agent_id,
        external_progress_review=external_progress_review,
        effective_turn_cadence=effective_turn_cadence,
        autonomous_replan_ack_recorded=autonomous_replan_ack_recorded,
        neutral_classifications=AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS,
        build_autonomous_replan_obligation=build_autonomous_replan_obligation,
        autonomous_replan_stall_threshold=AUTONOMOUS_REPLAN_STALL_THRESHOLD,
        dead_monitor_repeat_threshold=DEAD_MONITOR_REPEAT_THRESHOLD,
        dead_monitor_repeat_schema_version=DEAD_MONITOR_REPEAT_SCHEMA_VERSION,
        periodic_run_threshold=AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD,
    )
