"""Real native arbitration for tests of the unchanged scheduler projections."""
from collections.abc import Collection
from typing import Any

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.scheduler.arbitration import (
    SchedulerArbitration,
    _scheduler_arbitration_facts,
)
from loopx.control_plane.scheduler.scheduler_hint import build_scheduler_hint


def project_native_arbitration(
    payload: dict[str, Any], *, agent_scope_frontier_actions: Collection[str] = (),
) -> SchedulerArbitration:
    result = effect_runtime_result("work_item.interaction_reads.project", {
        "required_reads": [], "goal_id": None, "goal_state_file": None,
        "goal_acceptance_enabled": False, "should_run": False,
        "delivery_allowed": False, "selection_required": False,
        "has_replan": False, "settlement_only": False,
        "arbitration_facts": _scheduler_arbitration_facts(
            payload.get("interaction_contract"), agent_scope_frontier_actions,
        ),
    })
    return SchedulerArbitration.from_projection(result["arbitration"])


def build_native_scheduler_hint(
    payload: dict[str, Any], *, agent_scope_frontier_actions: Collection[str] = (),
    **kwargs: Any,
) -> dict[str, Any]:
    return build_scheduler_hint(payload, arbitration=project_native_arbitration(
        payload, agent_scope_frontier_actions=agent_scope_frontier_actions,
    ), **kwargs)
