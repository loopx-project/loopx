"""Run lifecycle projections inside the `status` bounded context."""

from __future__ import annotations

from typing import Any

from ...history import STATUS_NEUTRAL_CLASSIFICATIONS
from ...state_projection import actions_are_projection_aligned
from ..agents.agent_lane_recommendation import (
    compact_agent_lane_recommendation as _compact_agent_lane_recommendation,
    is_status_neutral_run as _is_status_neutral_run,
    latest_agent_lane_run as _latest_agent_lane_run,
    latest_run_recommended_action_for_projection as _latest_run_recommended_action_for_projection,
)
from ..progress_scope import AGENT_LANE_PROGRESS_SCOPE
from ..runtime.public_safety import public_safe_compact_text
from ..runtime.run_history import latest_run as _latest_run
from ..runtime.time import parse_timestamp


def is_status_neutral_run(run: dict[str, Any]) -> bool:
    return _is_status_neutral_run(
        run,
        status_neutral_classifications=STATUS_NEUTRAL_CLASSIFICATIONS,
        agent_lane_progress_scope=AGENT_LANE_PROGRESS_SCOPE,
    )


def latest_agent_lane_run(goal: dict[str, Any]) -> dict[str, Any] | None:
    return _latest_agent_lane_run(
        goal,
        agent_lane_progress_scope=AGENT_LANE_PROGRESS_SCOPE,
    )


def compact_agent_lane_recommendation(run: dict[str, Any] | None) -> dict[str, Any] | None:
    return _compact_agent_lane_recommendation(
        run,
        agent_lane_progress_scope=AGENT_LANE_PROGRESS_SCOPE,
        public_safe_compact_text=public_safe_compact_text,
    )


def latest_run_recommended_action_for_projection(
    *,
    current_status_run: dict[str, Any] | None,
    agent_lane_recommendation: dict[str, Any] | None,
    active_state_next_action: Any = None,
    preferred_agent_id: str | None = None,
    limit: int = 320,
) -> tuple[str | None, str | None]:
    return _latest_run_recommended_action_for_projection(
        current_status_run=current_status_run,
        agent_lane_recommendation=agent_lane_recommendation,
        active_state_next_action=active_state_next_action,
        preferred_agent_id=preferred_agent_id,
        limit=limit,
        public_safe_compact_text=public_safe_compact_text,
        actions_are_projection_aligned=actions_are_projection_aligned,
        parse_timestamp=parse_timestamp,
    )


def latest_run(goal: dict[str, Any]) -> dict[str, Any] | None:
    return _latest_run(
        goal,
        is_status_neutral_run=is_status_neutral_run,
    )
