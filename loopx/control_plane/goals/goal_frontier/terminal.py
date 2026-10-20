from __future__ import annotations

from typing import Any

from ..acceptance_readback import configured_checks

GOAL_TERMINAL_STATE_SCHEMA_VERSION = "goal_terminal_state_v0"
GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION = "goal_terminal_source_completeness_v0"
VISION_CHECKPOINT_NO_FOLLOWUP_RESOLUTION = "record_no_followup"

def _strict_zero(value: Any) -> bool:
    return type(value) is int and value == 0


def _terminal_todo_source_state(
    summary: dict[str, Any] | None,
    *,
    role: str,
) -> tuple[str, bool]:
    if not isinstance(summary, dict):
        return "missing", False
    completeness = summary.get("source_completeness")
    if not (
        isinstance(completeness, dict)
        and completeness.get("schema_version") == "todo_source_completeness_v0"
        and completeness.get("status") == "valid"
        and completeness.get("source") == "structured_todo_projection"
        and completeness.get("role") == role
        and completeness.get("terminal_closure") == "valid"
    ):
        return "invalid", False
    total = summary.get("total_count")
    done = summary.get("done_count")
    deferred = summary.get("deferred_count")
    watch_only = summary.get("watch_only_monitor_count", 0)
    if not (
        type(total) is int
        and total >= 0
        and type(done) is int
        and type(watch_only) is int
        and watch_only >= 0
        and done + watch_only == total
        and _strict_zero(summary.get("convergence_open_count", summary.get("open_count")))
        and _strict_zero(deferred)
        and isinstance(summary.get("monitor_open_items"), list)
        and len(summary.get("monitor_open_items")) == watch_only
    ):
        return "invalid", False
    intent = summary.get("closure_intent")
    has_no_followup_intent = bool(
        isinstance(intent, dict)
        and intent.get("schema_version") == "todo_closure_intent_v0"
        and intent.get("kind") == "no_followup"
        and intent.get("derived") is True
        and intent.get("source") == "todo_no_followup"
        and type(intent.get("count")) is int
        and 0 < intent.get("count") <= done
    )
    return "valid", has_no_followup_intent


def _projection_has_empty_terminal_frontier(projection: dict[str, Any] | None) -> bool:
    if not isinstance(projection, dict):
        return False

    normalized = projection.get("normalized_progress")
    frontier = projection.get("remaining_advancement_frontier")
    monitors = projection.get("monitor_only_lanes")
    successors = projection.get("deferred_successors")
    if not all(isinstance(value, dict) for value in (normalized, frontier, monitors, successors)):
        return False

    required_keys = (
        (normalized, {"user_open_count", "agent_open_count", "agent_advancement_open_count", "agent_monitor_open_count", "agent_monitor_due_count"}),
        (frontier, {"current_agent_claimed_advancement_count", "unclaimed_advancement_count", "other_agent_claimed_advancement_count"}),
        (monitors, {"present", "quiet_until_material_transition"}),
        (successors, {"ready_count", "blocked_count", "current_agent_ready_count"}),
    )
    if any(not keys.issubset(values) for values, keys in required_keys):
        return False

    projected_counts = (
        normalized.get("user_open_count"),
        normalized.get("agent_open_count"),
        normalized.get("agent_advancement_open_count"),
        normalized.get("agent_monitor_open_count"),
        normalized.get("agent_monitor_due_count"),
        frontier.get("current_agent_claimed_advancement_count"),
        frontier.get("unclaimed_advancement_count"),
        frontier.get("other_agent_claimed_advancement_count"),
        successors.get("ready_count"),
        successors.get("blocked_count"),
        successors.get("current_agent_ready_count"),
    )
    return (
        all(_strict_zero(value) for value in projected_counts)
        and monitors.get("present") is False
        and monitors.get("quiet_until_material_transition") is False
        and projection.get("acceptance_gaps") == []
        and projection.get("autonomy_blockers") == []
        and projection.get("replan_required") is False
    )


def _terminal_todo_source_contract(
    *,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    user_status, user_intent = _terminal_todo_source_state(user_todo_summary, role="user")
    agent_status, agent_intent = _terminal_todo_source_state(agent_todo_summary, role="agent")
    source_completeness = {
        "schema_version": GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION,
        "user_todos": user_status,
        "agent_todos": agent_status,
    }
    acceptance = (agent_todo_summary or {}).get("goal_acceptance_contract")
    acceptance_confirmed = True
    if isinstance(acceptance, dict) and configured_checks(acceptance) is not None:
        # Todo completion runs only that Todo's criteria. The typed acceptance
        # owner separately qualifies the current contract and complete work
        # basis; a local pass/no-follow-up cannot manufacture that receipt.
        status = acceptance.get("status")
        source_completeness["goal_acceptance"] = status
        acceptance_confirmed = status == "accepted"
    return (
        source_completeness,
        user_status == agent_status == "valid" and (user_intent or agent_intent)
        and acceptance_confirmed,
    )


def derive_goal_terminal_state(
    *,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    projection: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source_completeness, source_closure_confirmed = _terminal_todo_source_contract(
        user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary,
    )
    if not (
        source_closure_confirmed
        and _projection_has_empty_terminal_frontier(projection)
    ):
        return source_completeness, None
    return source_completeness, {
        "schema_version": GOAL_TERMINAL_STATE_SCHEMA_VERSION,
        "kind": "no_followup",
        "derived": True,
        "source": "validated_goal_closure",
    }


def _terminal_no_followup_resolves_vision_checkpoint(
    *,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    checkpoint: dict[str, Any] | None,
) -> bool:
    if not isinstance(checkpoint, dict):
        return False
    required_resolution = checkpoint.get("required_resolution")
    if not (
        isinstance(required_resolution, list)
        and VISION_CHECKPOINT_NO_FOLLOWUP_RESOLUTION in required_resolution
    ):
        return False
    _, source_closure_confirmed = _terminal_todo_source_contract(
        user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary,
    )
    return source_closure_confirmed


def goal_frontier_is_terminal_no_followup(*, projection: dict[str, Any] | None) -> bool:
    if not _projection_has_empty_terminal_frontier(projection):
        return False
    terminal_state = projection.get("terminal_state")
    completeness = projection.get("source_completeness")
    return bool(
        isinstance(terminal_state, dict)
        and terminal_state.get("schema_version") == GOAL_TERMINAL_STATE_SCHEMA_VERSION
        and terminal_state.get("kind") == "no_followup"
        and terminal_state.get("derived") is True
        and terminal_state.get("source") == "validated_goal_closure"
        and isinstance(completeness, dict)
        and completeness.get("schema_version") == GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION
        and completeness.get("user_todos") == "valid"
        and completeness.get("agent_todos") == "valid"
        and completeness.get("goal_acceptance", "accepted") == "accepted"
    )
