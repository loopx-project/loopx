from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import cast

from ...agents.agent_scope import (
    agent_scope_blocking_handoff_gates,
    agent_scope_count_advancement_items as _count_advancement_items,
    agent_scope_item_claimed_by,
    agent_scope_item_claimed_by_agent_or_unclaimed,
)
from ...agents.runtime_model import peer_work_key, select_peer_for_work
from ...runtime.time import parse_timestamp
# Refs #4447: the todo contract owns this vocabulary; import it instead of
# restating the literal in every module that classifies a Todo.
from ...todos.contract import TODO_TASK_CLASS_ADVANCEMENT, TODO_TASK_CLASS_MONITOR
from ...todos.resume_planning import read_todo_deferred_work_counts
from ...todos.succession_warning import todo_succession_gap_items
from ...todos.todo_semantics import (
    todo_advancement_frontier_counts,
    todo_item_is_watch_only_monitor,
    todo_scoped_runnable_advancement_count,
    todo_summary_open_task_counts,
)
from ...todos.user_gate import open_todo_count as _open_todo_count
from ...work_items.autonomous_replan_ack import autonomous_replan_ack_matches_frontier
from ...work_items.autonomous_replan_obligation import (
    MONITOR_NO_CHANGE_STREAK_THRESHOLD,
    ensure_replan_novelty_policy,
    with_replan_novelty_guidance,
)
from ...work_items.lifecycle_recovery import attach_receipt_replan_context
from ...work_items.replan_history_codec import project_replan_history
from ..goal_vision_policy import (
    completed_todo_replan_threshold,
    goal_vision_repeats_advancement_until_closed,
)
from ..goal_vision_read_model import (
    VISION_ACCEPTANCE_GAP_TRIGGER as VISION_ACCEPTANCE_GAP_TRIGGER,
    VISION_SUCCESSOR_GAP_TRIGGER as VISION_SUCCESSOR_GAP_TRIGGER,
    acceptance_gaps_from_agent_vision as acceptance_gaps_from_agent_vision,
    parse_vision_todo_delta_entries as parse_vision_todo_delta_entries,
)
from ..goal_vision_state import goal_vision_state_is_closed
from ..goal_vision_wait import build_goal_vision_wait_state
from . import outcome_continuity as outcome_continuity
from .acceptance import (
    GOAL_ACCEPTANCE_HOLD_TRIGGERS as GOAL_ACCEPTANCE_HOLD_TRIGGERS,
    VISION_PROFILE_MISSING_TRIGGER as VISION_PROFILE_MISSING_TRIGGER,
    acceptance_gaps_from_agent_profile_requirement as acceptance_gaps_from_agent_profile_requirement,
    acceptance_gaps_from_held_goal_binding as acceptance_gaps_from_held_goal_binding,
    acceptance_gaps_from_outcomes as acceptance_gaps_from_outcomes,
    project_legacy_acceptance_hold_ack as project_legacy_acceptance_hold_ack,
)
from .ack_policy import (
    _acknowledged_replan_obligation_id as _acknowledged_replan_obligation_id,
    _replan_evidence_acknowledged as _replan_evidence_acknowledged,
    _succession_gap_acknowledged as _succession_gap_acknowledged,
    _vision_gap_acknowledged as _vision_gap_acknowledged,
    attach_rejected_replan_feedback as attach_rejected_replan_feedback,
    autonomous_replan_ack_satisfies_obligation as autonomous_replan_ack_satisfies_obligation,
    projected_autonomous_replan_ack_for_agent as projected_autonomous_replan_ack_for_agent,
    replan_successor_transition_ack as replan_successor_transition_ack,
)
from .fallback_source import (
    VISION_FRONTIER_TODO_DELTA_ACTIONS as VISION_FRONTIER_TODO_DELTA_ACTIONS,
    FallbackDeclaration as FallbackDeclaration,
    FallbackTodoSource as FallbackTodoSource,
    agent_scoped_selectable_advancement_todo_ids as agent_scoped_selectable_advancement_todo_ids,
    declared_fallback_gap_from_agent_vision as declared_fallback_gap_from_agent_vision,
    parse_fallback_declarations as parse_fallback_declarations,
)
from .long_todo_chain import (
    LONG_TODO_CHAIN_TRIGGER as LONG_TODO_CHAIN_TRIGGER,
    evaluate_long_todo_chain as evaluate_long_todo_chain,
)
from .projection import (
    AUTONOMOUS_REPLAN_DECISION_SCHEMA_VERSION as AUTONOMOUS_REPLAN_DECISION_SCHEMA_VERSION,
    AUTONOMOUS_REPLAN_REQUIRED_MODE as AUTONOMOUS_REPLAN_REQUIRED_MODE,
    GOAL_FRONTIER_PROJECTION_SCHEMA_VERSION as GOAL_FRONTIER_PROJECTION_SCHEMA_VERSION,
    VISION_CONTINUATION_AUDIT_SCHEMA_VERSION as VISION_CONTINUATION_AUDIT_SCHEMA_VERSION,
    VISION_GAP_JUDGE_SCHEMA_VERSION as VISION_GAP_JUDGE_SCHEMA_VERSION,
    autonomous_replan_is_required as autonomous_replan_is_required,
    build_autonomous_replan_decision as build_autonomous_replan_decision,
    build_autonomous_replan_recommendation as build_autonomous_replan_recommendation,
    build_goal_frontier_projection as build_goal_frontier_projection,
    build_vision_continuation_audit as build_vision_continuation_audit,
    build_vision_gap_judge as build_vision_gap_judge,
    compact_replan_obligation as compact_replan_obligation,
    safe_non_negative_int as safe_non_negative_int,
)
from .replan_rules import (
    AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION as AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
    FRONTIER_EXHAUSTED_MONITOR_TRIGGER as FRONTIER_EXHAUSTED_MONITOR_TRIGGER,
    TODO_SUCCESSION_GAP_TRIGGER as TODO_SUCCESSION_GAP_TRIGGER,
    GoalFrontierReplanFacts as GoalFrontierReplanFacts,
    GoalFrontierReplanRule as GoalFrontierReplanRule,
    build_rule_replan_obligation,
    select_goal_frontier_replan_rule as select_goal_frontier_replan_rule,
)
from .semantic_history import (
    _latest_runs_for_goal as _latest_runs_for_goal,
    latest_agent_vision_from_runs as latest_agent_vision_from_runs,
    latest_agent_vision_from_status_payload as latest_agent_vision_from_status_payload,
    latest_autonomous_replan_ack_from_status_payload as latest_autonomous_replan_ack_from_status_payload,
    latest_missing_vision_checkpoint_from_status_payload as latest_missing_vision_checkpoint_from_status_payload,
    latest_replan_ack_feedback_from_status_payload as latest_replan_ack_feedback_from_status_payload,
    receipt_bound_replan_ack_history as receipt_bound_replan_ack_history,
)
from .terminal import (
    GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION as GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION,
    GOAL_TERMINAL_STATE_SCHEMA_VERSION as GOAL_TERMINAL_STATE_SCHEMA_VERSION,
    VISION_CHECKPOINT_NO_FOLLOWUP_RESOLUTION as VISION_CHECKPOINT_NO_FOLLOWUP_RESOLUTION,
    _terminal_no_followup_resolves_vision_checkpoint,
    derive_goal_terminal_state as derive_goal_terminal_state,
    goal_frontier_is_terminal_no_followup as goal_frontier_is_terminal_no_followup,
)

Payload = dict[str, object]
PayloadView = Mapping[str, object]

AUTONOMOUS_REPLAN_SCOPE_SCHEMA_VERSION = "autonomous_replan_scope_v0"
MONITOR_NO_CHANGE_STREAK_TRIGGER = "monitor_no_change_streak"
_REPLAN_OWNER_KEYS = (
    "agent_id", "claimed_by", "bound_agent", "owner_agent", "target_agent", "blocks_agent",
)


def select_autonomous_replan_obligation(
    item: PayloadView,
    project_asset: PayloadView | None = None,
    *,
    agent_id: str | None = None,
) -> Payload | None:
    sources = (item, project_asset) if isinstance(project_asset, dict) else (item,)
    safe_agent_id = str(agent_id or "").strip()
    if safe_agent_id:
        for source in sources:
            obligations = source.get("autonomous_replan_obligations_by_agent")
            if isinstance(obligations, dict) and isinstance(
                value := obligations.get(safe_agent_id), dict
            ):
                return cast(Payload, value)
    for source in sources:
        if isinstance(value := source.get("autonomous_replan_obligation"), dict):
            return cast(Payload, value)
    return None


def align_autonomous_replan_guidance_with_acceptance_policy(
    replan_obligation: Payload | None,
    *,
    acceptance_gaps: Sequence[PayloadView] | None,
) -> Payload | None:
    """Remove wait-only guidance when the active vision requires advancement."""

    if not isinstance(replan_obligation, dict):
        return replan_obligation
    raw_triggers = replan_obligation.get("triggers")
    trigger_kinds = {
        str(trigger.get("kind") or "").strip()
        for trigger in (raw_triggers if isinstance(raw_triggers, list) else ())
        if isinstance(trigger, dict)
    }
    if "blocked_successor_no_progress_repeat" not in trigger_kinds or not any(
        goal_vision_repeats_advancement_until_closed(gap.get("advancement_policy"))
        for gap in (acceptance_gaps or ())
        if isinstance(gap, dict)
    ):
        return replan_obligation

    aligned = dict(replan_obligation)
    aligned["guidance_actions"] = [
        "discover_safe_successor", "create_runnable_todo", "successor_or_supersede",
    ]
    raw_todo_actions = replan_obligation.get("todo_actions")
    aligned["todo_actions"] = [
        {
            **action,
            "text": (
                "discover and promote one safe in-scope evidence-backed runnable "
                "todo, or replace the blocked successor through an explicit "
                "successor/supersede transition"
            ),
        }
        if isinstance(action, dict) and action.get("action") == "add" and action.get("role") == "agent"
        else action
        for action in (raw_todo_actions if isinstance(raw_todo_actions, list) else ())
    ]
    aligned["recommended_action"] = with_replan_novelty_guidance(
        "run a bounded autonomous replan for the exact blocked successor: "
        "create or claim one safe in-scope runnable advancement todo, or record "
        "an explicit successor/supersede transition; a maintenance-only "
        "continuation does not satisfy a repeat-until-closed vision"
    )
    aligned["satisfying_semantic_outcomes"] = [
        "fresh_vision_path_outcome", "new_runnable_successor", "new_concrete_blocker",
        "coverage_backed_exploration_exhausted", "coverage_backed_no_followup",
    ]
    return aligned


def _normalize_replan_agent_id(value: object) -> str | None:
    return str(value or "").strip() or None


def _autonomous_replan_owner_agent_ids(replan_obligation: PayloadView | None) -> list[str]:
    if not isinstance(replan_obligation, dict):
        return []
    triggers = replan_obligation.get("triggers")
    sources = [
        replan_obligation,
        *(t for t in (triggers if isinstance(triggers, list) else ()) if isinstance(t, dict)),
    ]
    return list(
        dict.fromkeys(
            owner
            for src in sources
            for key in _REPLAN_OWNER_KEYS
            if (owner := _normalize_replan_agent_id(src.get(key)))
        )
    )


def autonomous_replan_scope_decision(
    replan_obligation: PayloadView | None,
    *,
    agent_id: str | None,
    registered_agent_ids: list[str] | None = None,
) -> Payload:
    """Return whether a replan obligation belongs to this agent lane.

    Explicit agent-owned replans are consumed only by that agent. Unscoped
    goal-level replans are deterministically assigned to one registered peer.
    """

    normalized_agent_id = _normalize_replan_agent_id(agent_id)
    owners = _autonomous_replan_owner_agent_ids(replan_obligation)
    required = autonomous_replan_is_required(replan_obligation)
    selected_peer_agent = None
    if not required:
        applies, scope = False, "not_required"
    elif not normalized_agent_id:
        applies, scope = True, "unscoped_quota_call"
    elif owners:
        applies, scope = normalized_agent_id in owners, "explicit_agent_owner"
    else:
        obligation_obj = cast(Payload, replan_obligation)
        selected_peer_agent = select_peer_for_work(
            registered_agent_ids or [],
            # Display evidence and prose are mutable projections, not work
            # identity. Normalize before both selection and permission checks.
            work_key=peer_work_key(
                {
                    "obligation_id": obligation_obj.get("obligation_id")
                    or ensure_replan_novelty_policy(obligation_obj)["obligation_id"]
                },
                fallback="autonomous_replan",
            ),
        )
        applies = bool(selected_peer_agent and normalized_agent_id == selected_peer_agent)
        scope = "deterministic_peer_assignment"
    return {
        "schema_version": AUTONOMOUS_REPLAN_SCOPE_SCHEMA_VERSION,
        "required": required,
        "applies": applies,
        "scope": scope,
        "agent_id": normalized_agent_id,
        "agent_model": "peer_v1",
        "owner_agent_ids": owners,
        "selected_peer_agent": selected_peer_agent,
    }


def _todo_item_is_actionable_open(item: PayloadView) -> bool:
    return item.get("done") is not True and str(item.get("status") or "open").strip().lower() in {
        "", "open", "todo", "active", "pending",
    }


def _todo_task_class(item: PayloadView) -> str:
    return str(item.get("task_class") or "").strip()


def _is_actionable_monitor_item(item: object, *, watch_only: bool) -> bool:
    if not isinstance(item, dict) or not _todo_item_is_actionable_open(item):
        return False
    is_watch_only = todo_item_is_watch_only_monitor(item)
    return is_watch_only if watch_only else (
        _todo_task_class(item) == TODO_TASK_CLASS_MONITOR and not is_watch_only
    )


def _blocking_user_open_count(summary: Payload | None) -> int:
    if not isinstance(summary, dict):
        return 0
    gate_items = summary.get("gate_open_items")
    if isinstance(gate_items, list):
        return sum(1 for item in gate_items if isinstance(item, dict) and _todo_item_is_actionable_open(item))
    # Legacy or partial summaries without typed gate projection fail closed.
    return _open_todo_count(summary)


def _summary_task_counts(summary: Payload | None) -> dict[str, int]:
    open_count = _open_todo_count(summary)
    if not isinstance(summary, dict):
        return {"open": open_count, "advancement": 0, "monitor": 0, "monitor_due": 0, "watch_only_monitor_due": 0}
    executable = summary.get("executable_backlog_items")
    monitor_open = summary.get("monitor_open_items")
    watch_only_count = (
        safe_non_negative_int(summary.get("watch_only_monitor_count"))
        if isinstance(summary.get("watch_only_monitor_count"), int)
        else sum(1 for item in monitor_open if _is_actionable_monitor_item(item, watch_only=True))
        if isinstance(monitor_open, list)
        else 0
    )
    open_count = (
        safe_non_negative_int(summary.get("convergence_open_count"))
        if isinstance(summary.get("convergence_open_count"), int)
        # Compatibility-only fallback for summaries produced before the typed
        # TypeScript lane owner exposed convergence_open_count.
        else max(0, open_count - watch_only_count)
    )
    work_counts = todo_summary_open_task_counts(summary) if summary.get("work_counts") is not None else None
    if isinstance(work_counts, dict):
        advancement_count = safe_non_negative_int(work_counts.get("advancement"))
        monitor_count = max(0, safe_non_negative_int(work_counts.get("monitor")) - watch_only_count)
    else:
        unclaimed_items = summary.get("unclaimed_priority_open_items")
        advancement_count = (
            _count_advancement_items(executable)
            if isinstance(executable, list)
            else safe_non_negative_int(summary.get("claimed_advancement_open_count"))
            + sum(
                1 for item in (unclaimed_items if isinstance(unclaimed_items, list) else ())
                if isinstance(item, dict) and _todo_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
            )
        )
        monitor_count = (
            sum(1 for item in monitor_open if _is_actionable_monitor_item(item, watch_only=False))
            if isinstance(monitor_open, list)
            else safe_non_negative_int(summary.get("claimed_monitor_open_count"))
        )
    watch_only_due = safe_non_negative_int(summary.get("watch_only_monitor_due_count"))
    return {
        "open": open_count,
        "advancement": advancement_count,
        "monitor": monitor_count,
        "monitor_due": max(0, safe_non_negative_int(summary.get("monitor_due_count")) - watch_only_due),
        "watch_only_monitor_due": watch_only_due,
    }


def _monitor_no_change_streak_trigger(
    agent_todo_summary: PayloadView | None, *, agent_id: str | None,
) -> Payload | None:
    if not isinstance(agent_todo_summary, dict):
        return None
    monitor_items = agent_todo_summary.get("monitor_open_items")
    if not isinstance(monitor_items, list):
        return None
    eligible = [
        item for item in monitor_items
        if _is_actionable_monitor_item(item, watch_only=False)
        and (not agent_id or agent_scope_item_claimed_by(item) == agent_id)
    ]
    if not eligible:
        return None
    # The enclosing frontier already established that no advancement is
    # selectable. Keep its eligibility facts, but share count/expiry/winner
    # selection and the existing public trigger with the typed replan owner.
    return cast(
        Payload | None,
        project_replan_history(
            operation="frontier_monitor_streak",
            agent_id=agent_id,
            agent_todos={"monitor_open_items": eligible},
            streak_threshold=MONITOR_NO_CHANGE_STREAK_THRESHOLD,
        ),
    )


def _frontier_advancement_counts(
    *, agent_todo_summary: Payload | None, agent_id: str | None,
) -> dict[str, int]:
    return todo_advancement_frontier_counts(agent_todo_summary, agent_id=agent_id)


def _compact_todo_id(item: object) -> str | None:
    return str(item.get("todo_id") or "").strip() or None if isinstance(item, dict) else None


def _deferred_successors(summary: Payload | None, *, agent_id: str | None) -> Payload:
    summary_obj = summary if isinstance(summary, dict) else {}
    counts = read_todo_deferred_work_counts(summary_obj)
    raw_ready = summary_obj.get("deferred_resume_candidates")
    ready_items = [i for i in (raw_ready if isinstance(raw_ready, list) else ()) if isinstance(i, dict)]
    ready_todo_ids = [tid for i in ready_items[:5] if (tid := _compact_todo_id(i))]
    projection: Payload = {
        "ready_count": counts["ready"],
        "blocked_count": counts["blocked"],
        "unknown_count": counts["unknown"],
        "counts_complete": counts["complete"],
        "current_agent_ready_count": counts["ready_by_agent"].get(agent_id, 0),
        "ready_todo_ids": ready_todo_ids,
    }
    if ready_todo_ids:
        projection["top_ready_todo_id"] = ready_todo_ids[0]
    return projection


def _is_continuous_monitor_lane(work_lane_contract: PayloadView | None) -> bool:
    return bool(work_lane_contract and work_lane_contract.get("lane") == TODO_TASK_CLASS_MONITOR)


def _is_monitor_only_lane(work_lane_contract: PayloadView | None) -> bool:
    return bool(
        _is_continuous_monitor_lane(work_lane_contract)
        and work_lane_contract is not None
        and (
            work_lane_contract.get("must_attempt_work") is False
            or work_lane_contract.get("monitor_kind") == "todo_monitor_due"
        )
    )


def _monitor_only_lane_has_future_schedule(agent_todo_summary: PayloadView | None) -> bool:
    if not isinstance(agent_todo_summary, dict):
        return False
    if "monitor_due_count" not in agent_todo_summary or "monitor_schedule_gap_count" not in agent_todo_summary:
        return False
    if (
        safe_non_negative_int(agent_todo_summary.get("monitor_due_count")) > 0
        or safe_non_negative_int(agent_todo_summary.get("monitor_schedule_gap_count")) > 0
    ):
        return False
    monitor_items = agent_todo_summary.get("monitor_open_items")
    return isinstance(monitor_items, list) and len(monitor_items) > 0


def _blocking_handoff_gate_count(
    agent_todo_summary: Payload | None, *, agent_id: str | None,
) -> int:
    if not agent_id or not isinstance(agent_todo_summary, dict):
        return 0
    return len(agent_scope_blocking_handoff_gates(agent_todo_summary, agent_id=agent_id))


def _ready_deferred_successor_count(
    agent_todo_summary: PayloadView | None, *, agent_id: str | None,
) -> int:
    if not isinstance(agent_todo_summary, dict):
        return 0
    current_count = safe_non_negative_int(agent_todo_summary.get("current_agent_deferred_resume_count"))
    unclaimed_count = safe_non_negative_int(agent_todo_summary.get("unclaimed_deferred_resume_count"))
    if current_count or unclaimed_count:
        return current_count + unclaimed_count
    candidates = agent_todo_summary.get("deferred_resume_candidates")
    if not isinstance(candidates, list):
        return 0
    return sum(
        1 for item in candidates
        if isinstance(item, dict) and agent_scope_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)
    )


def derive_goal_frontier_replan_obligation_from_summaries(
    *,
    user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None,
    work_lane_contract: Payload | None,
    agent_id: str | None,
    existing_replan_obligation: Payload | None,
    agent_todo_source_items: list[Payload] | None = None,
    latest_replan_ack: Payload | None = None,
    current_transition_replan_ack: Payload | None = None,
    acceptance_gaps: list[Payload] | None = None,
    monitor_lane_semantically_valid: bool = True,
    capability_obligation: Payload | None = None,
    vision_settlement_query: bool = False,
    _frontier_counts: dict[str, int] | None = None,
) -> Payload | None:
    """Return a compact replan obligation when the goal frontier has no advancement."""

    agent_counts = _summary_task_counts(agent_todo_summary)
    # Only the enclosing context reduction supplies this fresh, same-agent
    # snapshot. Standalone callers still classify their current source.
    frontier_counts = (
        _frontier_counts if _frontier_counts is not None
        else _frontier_advancement_counts(agent_todo_summary=agent_todo_summary, agent_id=agent_id)
    )
    total_frontier_advancement = sum(frontier_counts.values())
    selectable_frontier_advancement = (
        frontier_counts["current_agent_claimed_advancement_count"]
        + frontier_counts["unclaimed_advancement_count"]
    )
    monitor_no_change_trigger = (
        _monitor_no_change_streak_trigger(agent_todo_summary, agent_id=agent_id)
        if selectable_frontier_advancement == 0
        else None
    )
    compact_acceptance_gaps = sorted(
        (item for item in (acceptance_gaps or ()) if isinstance(item, dict)),
        # An enforced hold must survive the bounded trigger projection. Its
        # exact checkpoint is required to settle; generic vision gaps follow.
        key=lambda gap: gap.get("kind") not in GOAL_ACCEPTANCE_HOLD_TRIGGERS,
    )
    if any(gap.get("vision_todo_ids") for gap in compact_acceptance_gaps):
        # Diagnostic claim counts retain executor-excluded work. A causal
        # acceptance obligation needs an actually selectable Todo identity.
        selectable_frontier_advancement = len(
            agent_scoped_selectable_advancement_todo_ids(agent_todo_summary, agent_id=agent_id)
        )
    successor_vision_required = any(
        item.get("kind") in {VISION_SUCCESSOR_GAP_TRIGGER, VISION_PROFILE_MISSING_TRIGGER}
        for item in compact_acceptance_gaps
    )
    outcome_checkpoint_replan_required = any(
        item.get("kind") == outcome_continuity.VISION_OUTCOME_CHECKPOINT_REQUIRED_TRIGGER
        for item in compact_acceptance_gaps
    )
    succession_gap_items = todo_succession_gap_items(agent_todo_summary, agent_id=agent_id)
    long_chain_observation, long_chain_ack_decision = evaluate_long_todo_chain(
        agent_todo_summary=agent_todo_summary,
        agent_counts=agent_counts,
        frontier_counts=frontier_counts,
        agent_id=agent_id,
        agent_todo_source_items=agent_todo_source_items,
        latest_replan_ack=current_transition_replan_ack or latest_replan_ack,
    )
    future_schedule_present = _monitor_only_lane_has_future_schedule(agent_todo_summary)
    summary_obj = agent_todo_summary or {}
    replan_rule = select_goal_frontier_replan_rule(
        GoalFrontierReplanFacts(
            existing_replan_required=autonomous_replan_is_required(existing_replan_obligation),
            blocking_handoff_gate_count=_blocking_handoff_gate_count(agent_todo_summary, agent_id=agent_id),
            ready_deferred_successor_count=_ready_deferred_successor_count(agent_todo_summary, agent_id=agent_id),
            successor_vision_required=successor_vision_required,
            blocking_user_open_count=_blocking_user_open_count(user_todo_summary),
            user_open_count=_open_todo_count(user_todo_summary),
            succession_gap_count=len(succession_gap_items),
            succession_gap_acknowledged=_succession_gap_acknowledged(succession_gap_items, latest_replan_ack),
            vision_gap_acknowledged=_vision_gap_acknowledged(compact_acceptance_gaps, latest_replan_ack),
            agent_advancement_count=agent_counts.get("advancement", 0),
            total_frontier_advancement=total_frontier_advancement,
            acceptance_gap_count=len(compact_acceptance_gaps),
            # An already-admitted vision review may create its successor before
            # writeback. Reconstruct its source only for exact receipt validation;
            # this query never replaces the ordinary frontier decision.
            selectable_frontier_advancement=(0 if vision_settlement_query else selectable_frontier_advancement),
            outcome_checkpoint_replan_required=outcome_checkpoint_replan_required,
            long_todo_chain_triggered=(
                long_chain_observation is not None
                and long_chain_ack_decision is not None
                and not long_chain_ack_decision.acknowledged
            ),
            current_agent_blocker_count=safe_non_negative_int(summary_obj.get("current_agent_blocker_count")),
            capability_gap_pending=bool(capability_obligation and capability_obligation.get("required") is True),
            monitor_no_change_streak_triggered=monitor_no_change_trigger is not None,
            monitor_only_lane=_is_monitor_only_lane(work_lane_contract),
            monitor_count=agent_counts.get("monitor", 0),
            monitor_due_count=safe_non_negative_int(summary_obj.get("monitor_due_count")),
            monitor_schedule_gap_count=safe_non_negative_int(summary_obj.get("monitor_schedule_gap_count")),
            future_monitor_schedule_present=future_schedule_present,
            monitor_lane_semantically_valid=monitor_lane_semantically_valid,
        )
    )
    if not replan_rule.derives_obligation:
        return None
    return build_rule_replan_obligation(
        replan_rule.rule,
        agent_id=agent_id,
        agent_counts=agent_counts,
        future_schedule_present=future_schedule_present,
        latest_replan_ack=latest_replan_ack,
        compact_acceptance_gaps=compact_acceptance_gaps,
        succession_gap_items=succession_gap_items,
        capability_obligation=capability_obligation,
        long_chain_observation=long_chain_observation,
        long_chain_ack_decision=long_chain_ack_decision,
        monitor_no_change_trigger=monitor_no_change_trigger,
    )


def build_goal_frontier_projection_from_summaries(
    *,
    goal_id: str,
    agent_id: str | None,
    user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None,
    work_lane_contract: Payload | None,
    replan_obligation: Payload | None,
    acceptance_gaps: list[Payload] | None = None,
    vision_wait_state: Payload | None = None,
    fallback_gaps: list[Payload] | None = None,
    _frontier_counts: dict[str, int] | None = None,
) -> Payload:
    user_counts = _summary_task_counts(user_todo_summary)
    agent_counts = _summary_task_counts(agent_todo_summary)
    # Only the enclosing context reduction supplies this fresh, same-agent
    # snapshot. Standalone callers still classify their current source.
    frontier_counts = (
        _frontier_counts if _frontier_counts is not None
        else _frontier_advancement_counts(agent_todo_summary=agent_todo_summary, agent_id=agent_id)
    )
    selectable_frontier_advancement = (
        frontier_counts["current_agent_claimed_advancement_count"]
        + frontier_counts["unclaimed_advancement_count"]
    )
    monitor_only_lane = bool(
        _is_monitor_only_lane(work_lane_contract)
        and agent_counts.get("monitor", 0) > 0
        and agent_counts.get("advancement", 0) == 0
        and (selectable_frontier_advancement == 0 if agent_id else sum(frontier_counts.values()) == 0)
    )
    projection = build_goal_frontier_projection(
        goal_id=goal_id,
        agent_id=agent_id,
        user_counts=user_counts,
        agent_counts=agent_counts,
        current_agent_claimed_advancement_count=frontier_counts["current_agent_claimed_advancement_count"],
        unclaimed_advancement_count=frontier_counts["unclaimed_advancement_count"],
        other_agent_claimed_advancement_count=frontier_counts["other_agent_claimed_advancement_count"],
        monitor_only_lane=monitor_only_lane,
        replan_obligation=replan_obligation,
        acceptance_gaps=acceptance_gaps,
        vision_wait_state=vision_wait_state,
        fallback_gaps=fallback_gaps,
        deferred_successors=_deferred_successors(agent_todo_summary, agent_id=agent_id),
    )
    source_completeness, terminal_state = derive_goal_terminal_state(
        user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary, projection=projection,
    )
    work_counts = (agent_todo_summary or {}).get("work_counts")
    normalized_progress = projection.get("normalized_progress")
    if isinstance(normalized_progress, dict):
        normalized_progress.update({
            "agent_counts_complete": isinstance(work_counts, dict) and work_counts.get("complete") is True,
            "agent_unclassified_open_count": work_counts.get("hidden") if isinstance(work_counts, dict) else None,
        })
    if terminal_state:
        projection["source_completeness"] = source_completeness
        projection["terminal_state"] = terminal_state
    return projection


def _obligation_satisfied_by_ack(
    obligation: Payload | None,
    ack: Payload | None,
    *,
    acceptance_gaps: list[Payload],
    todo_succession_gap_open: bool,
    stride_strategy: str,
) -> bool:
    return bool(
        autonomous_replan_is_required(obligation)
        and autonomous_replan_ack_satisfies_obligation(
            ack,
            replan_obligation=obligation,
            acceptance_gaps=acceptance_gaps,
            todo_succession_gap_open=todo_succession_gap_open,
            stride_strategy=stride_strategy,
        )
        and autonomous_replan_ack_matches_frontier(ack, obligation)
    )


def _collect_receipt_bound_candidates(
    *,
    scoped_ack_history: list[Payload] | None,
    latest_agent_replan_ack: Payload | None,
    frontier_replan_obligation: Payload | None,
    frontier_transition_candidate: Payload,
    receipt_bound_replan_obligation_id: str | None,
    receipt_bound_guard_recorded_at: str | None,
    user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None,
    work_lane_contract: Payload | None,
    agent_id: str | None,
    agent_todo_source_items: list[Payload] | None,
    effective_replan_ack: Payload | None,
    acceptance_gaps: list[Payload],
    frontier_counts: dict[str, int],
    stride_strategy: str,
) -> tuple[list[Payload], Payload, Payload | None]:
    # A later durable Turn can have already acknowledged the exact review that
    # an older receipt still owes. Keep that run-derived ACK distinct from a
    # current-frontier transition: the settlement owner validates its exact
    # Goal, Agent, obligation, chronology, and Turn before it can close the old
    # writeback. Projected asset ACKs never enter this candidate.
    historical_ack_candidates: list[Payload] = [
        {"historical_ack": dict(candidate)}
        for candidate in (scoped_ack_history if isinstance(scoped_ack_history, list) else ())[:8]
        if isinstance(candidate, dict)
    ]
    if latest_agent_replan_ack is not None and receipt_bound_replan_obligation_id:
        historical_ack_candidates.append({"historical_ack": latest_agent_replan_ack})
    if frontier_replan_obligation is None and receipt_bound_replan_obligation_id:
        source = derive_goal_frontier_replan_obligation_from_summaries(
            user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary,
            work_lane_contract=work_lane_contract, agent_id=agent_id,
            existing_replan_obligation=None, agent_todo_source_items=agent_todo_source_items,
            latest_replan_ack=effective_replan_ack, acceptance_gaps=acceptance_gaps,
            vision_settlement_query=True, _frontier_counts=frontier_counts,
        )
        if (source or {}).get("obligation_id") == receipt_bound_replan_obligation_id:
            source_ack = replan_successor_transition_ack(
                agent_todo_summary, agent_id=agent_id, replan_obligation=source,
                agent_todo_items=agent_todo_source_items, stride_strategy=stride_strategy,
            )
            frontier_transition_candidate = {
                "obligation": source, "ack": source_ack, "reconstructed_vision_review": True,
            }

    lifecycle_candidate = None
    if receipt_bound_replan_obligation_id and receipt_bound_guard_recorded_at:
        from ...work_items.lifecycle_recovery import receipt_bound_lifecycle_candidate

        lifecycle_candidate = receipt_bound_lifecycle_candidate(
            selected_obligation_id=receipt_bound_replan_obligation_id,
            guard_recorded_at=receipt_bound_guard_recorded_at,
            agent_id=agent_id,
            items=agent_todo_source_items or [],
            derive=lambda summary: derive_goal_frontier_replan_obligation_from_summaries(
                user_todo_summary=None, agent_todo_summary=summary,
                work_lane_contract=work_lane_contract, agent_id=agent_id,
                existing_replan_obligation=None, agent_todo_source_items=agent_todo_source_items,
                # Reconstruct the admitted closed-row source, not the current
                # frontier: independent agent work or user gates may have arrived
                # after admission. This candidate still requires the original
                # obligation digest; live gates retain authority over new work.
            ),
        )
    return historical_ack_candidates, frontier_transition_candidate, lifecycle_candidate


def build_goal_frontier_projection_context_from_status(
    *,
    goal_id: str,
    agent_id: str | None,
    status_payload: Payload,
    item: Payload,
    project_asset: Payload | None,
    user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None,
    work_lane_contract: Payload | None,
    neutral_replan_ack_classifications: set[str],
    agent_todo_source_items: list[Payload] | None = None,
    fallback_todo_source_items: FallbackTodoSource = None,
    available_capabilities: object = None,
    registered_agent_ids: list[str] | None = None,
    goal_status: str | None = None,
    agent_profile: Payload | None = None,
    receipt_bound_replan_obligation_id: str | None = None,
    receipt_bound_guard_recorded_at: str | None = None,
    replan_explore_reader: Callable[[str, str | None], PayloadView | None] | None = None,
) -> Payload:
    """Build the quota-facing goal-frontier read model."""

    def _scope(obligation: Payload | None) -> Payload:
        return autonomous_replan_scope_decision(
            obligation, agent_id=agent_id, registered_agent_ids=registered_agent_ids,
        )

    execution_profile_raw = (project_asset or {}).get("execution_profile")
    execution_profile = execution_profile_raw if isinstance(execution_profile_raw, dict) else {}
    stride_strategy = str(execution_profile.get("stride_strategy", "off"))

    def _transition_ack(obligation: Payload | None) -> Payload | None:
        return replan_successor_transition_ack(
            agent_todo_summary, agent_id=agent_id, replan_obligation=obligation,
            agent_todo_items=agent_todo_source_items, stride_strategy=stride_strategy,
        )

    replan_obligation = select_autonomous_replan_obligation(item, project_asset, agent_id=agent_id)
    if replan_obligation:
        replan_obligation = ensure_replan_novelty_policy(replan_obligation)
    replan_scope = _scope(replan_obligation)
    if replan_scope.get("required") and not replan_scope.get("applies"):
        replan_obligation = None

    latest_agent_replan_ack, scoped_ack_history = receipt_bound_replan_ack_history(
        status_payload, goal_id=goal_id, agent_id=agent_id,
        neutral_classifications=neutral_replan_ack_classifications,
        receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
    )
    latest_replan_ack_feedback = latest_replan_ack_feedback_from_status_payload(
        status_payload, goal_id=goal_id, agent_id=agent_id,
    )
    ack_time = parse_timestamp((latest_agent_replan_ack or {}).get("generated_at"))
    feedback_time = parse_timestamp((latest_replan_ack_feedback or {}).get("generated_at"))
    if ack_time is not None and feedback_time is not None and ack_time >= feedback_time:
        latest_replan_ack_feedback = None
    latest_agent_vision = latest_agent_vision_from_status_payload(status_payload, goal_id=goal_id, agent_id=agent_id)
    latest_missing_vision_checkpoint = latest_missing_vision_checkpoint_from_status_payload(
        status_payload, goal_id=goal_id, agent_id=agent_id,
    )
    latest_vision_checkpoint = outcome_continuity.latest_outcome_vision_checkpoint_from_status_payload(
        status_payload, goal_id=goal_id, agent_id=agent_id,
    )
    source_acceptance_gaps = (
        acceptance_gaps_from_agent_profile_requirement(
            agent_profile, agent_id=agent_id,
            agent_vision=latest_agent_vision, missing_checkpoint=latest_missing_vision_checkpoint,
        )
        + acceptance_gaps_from_agent_vision(latest_agent_vision, goal_status=goal_status)
        + outcome_continuity.acceptance_gaps_from_vision_checkpoint(latest_missing_vision_checkpoint)
        + outcome_continuity.acceptance_gaps_from_outcome_checkpoint(latest_agent_vision, latest_vision_checkpoint)
        + (
            []
            if execution_profile.get("replan_after_effective_turns") is not None
            else outcome_continuity.acceptance_gaps_from_todo_completion_checkpoint(
                latest_agent_vision, latest_vision_checkpoint,
                agent_todo_summary=agent_todo_summary, agent_id=agent_id,
                completed_todo_threshold=completed_todo_replan_threshold(execution_profile),
            )
        )
        + acceptance_gaps_from_outcomes(agent_todo_summary, agent_id=agent_id)
        + acceptance_gaps_from_held_goal_binding(agent_todo_summary, agent_todo_source_items, agent_id=agent_id)
    )
    if _terminal_no_followup_resolves_vision_checkpoint(
        user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary,
        checkpoint=latest_missing_vision_checkpoint,
    ):
        source_acceptance_gaps = [
            gap for gap in source_acceptance_gaps
            if gap.get("kind") != outcome_continuity.VISION_CHECKPOINT_MISSING_TRIGGER
        ]
    todo_succession_gap_open = bool(todo_succession_gap_items(agent_todo_summary, agent_id=agent_id))
    replan_obligation = align_autonomous_replan_guidance_with_acceptance_policy(
        replan_obligation, acceptance_gaps=source_acceptance_gaps,
    )
    frontier_counts = _frontier_advancement_counts(agent_todo_summary=agent_todo_summary, agent_id=agent_id)
    # Commitment floors include open work held by a receipt-owned retry. The
    # typed selection projection, when complete for this lane, owns how much
    # work is actually runnable; a commitment is not an execution candidate.
    selectable_advancement_count = todo_scoped_runnable_advancement_count(agent_todo_summary, agent_id=agent_id)
    if selectable_advancement_count is None:
        selectable_advancement_count = (
            frontier_counts["current_agent_claimed_advancement_count"]
            + frontier_counts["unclaimed_advancement_count"]
        )
    vision_wait_state = build_goal_vision_wait_state(
        agent_todo_summary=agent_todo_summary, source_items=agent_todo_source_items, agent_id=agent_id,
        acceptance_gaps=source_acceptance_gaps, selectable_advancement_count=selectable_advancement_count,
    )
    acceptance_gaps = (
        [gap for gap in source_acceptance_gaps if gap.get("kind") in GOAL_ACCEPTANCE_HOLD_TRIGGERS]
        if vision_wait_state
        else source_acceptance_gaps
    )
    declared_fallback_gap = declared_fallback_gap_from_agent_vision(
        latest_agent_vision, agent_todo_summary=agent_todo_summary, agent_id=agent_id,
        agent_todo_source_items=fallback_todo_source_items,
        rollout_events=_latest_runs_for_goal(status_payload, goal_id=goal_id),
        available_capabilities=available_capabilities,
    )
    declared_fallback_gaps = [declared_fallback_gap] if isinstance(declared_fallback_gap, dict) else []
    projected_replan_ack = projected_autonomous_replan_ack_for_agent(item, project_asset, agent_id=agent_id)
    raw_runtime_root = status_payload.get("runtime_root")
    effective_replan_ack = project_legacy_acceptance_hold_ack(
        latest_agent_replan_ack or projected_replan_ack,
        acceptance_gaps=source_acceptance_gaps, goal_id=goal_id, agent_id=agent_id,
        runtime_root=raw_runtime_root if isinstance(raw_runtime_root, str) else None,
    )
    replan_transition_ack = _transition_ack(replan_obligation)
    # Keep the validated history-obligation transition distinct from any
    # successor frontier obligation derived below. Exact Turn settlement may
    # still owe this receipt even when the next decision has another duty.
    run_replan_transition_ack = replan_transition_ack
    run_transition_candidate = {"obligation": replan_obligation, "ack": replan_transition_ack}
    if _obligation_satisfied_by_ack(
        replan_obligation, replan_transition_ack or effective_replan_ack,
        acceptance_gaps=source_acceptance_gaps, todo_succession_gap_open=todo_succession_gap_open,
        stride_strategy=stride_strategy,
    ):
        replan_obligation = None
        replan_scope = _scope(None)

    raw_frontier = status_payload.get("bounded_research_frontier")
    bounded_research_frontier = raw_frontier if isinstance(raw_frontier, dict) else {}
    raw_capability_obligation = bounded_research_frontier.get("obligation")
    frontier_replan_obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary,
        work_lane_contract=work_lane_contract, agent_id=agent_id,
        existing_replan_obligation=replan_obligation, agent_todo_source_items=agent_todo_source_items,
        latest_replan_ack=effective_replan_ack, current_transition_replan_ack=replan_transition_ack,
        _frontier_counts=frontier_counts, acceptance_gaps=acceptance_gaps,
        monitor_lane_semantically_valid=not goal_vision_state_is_closed((latest_agent_vision or {}).get("state")),
        capability_obligation=cast(Payload, raw_capability_obligation) if isinstance(raw_capability_obligation, dict) else None,
    )
    raw_transitions = bounded_research_frontier.get("settlement_transitions")
    capability_transitions = [c for c in raw_transitions if isinstance(c, dict)] if isinstance(raw_transitions, list) else []
    frontier_transition_ack = (
        next(
            (
                cast(Payload | None, c.get("ack")) for c in capability_transitions
                if isinstance(c.get("obligation"), dict)
                and c["obligation"].get("obligation_id") == (frontier_replan_obligation or {}).get("obligation_id")
            ),
            None,
        )
        if (frontier_replan_obligation or {}).get("capability_guard")
        else _transition_ack(frontier_replan_obligation)
    )
    frontier_transition_candidate: Payload = {
        "obligation": frontier_replan_obligation, "ack": frontier_transition_ack,
    }
    historical_ack_candidates, frontier_transition_candidate, lifecycle_candidate = _collect_receipt_bound_candidates(
        scoped_ack_history=scoped_ack_history, latest_agent_replan_ack=latest_agent_replan_ack,
        frontier_replan_obligation=frontier_replan_obligation,
        frontier_transition_candidate=frontier_transition_candidate,
        receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
        receipt_bound_guard_recorded_at=receipt_bound_guard_recorded_at,
        user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary,
        work_lane_contract=work_lane_contract, agent_id=agent_id,
        agent_todo_source_items=agent_todo_source_items, effective_replan_ack=effective_replan_ack,
        acceptance_gaps=acceptance_gaps, frontier_counts=frontier_counts, stride_strategy=stride_strategy,
    )
    if _obligation_satisfied_by_ack(
        frontier_replan_obligation, frontier_transition_ack or effective_replan_ack,
        acceptance_gaps=source_acceptance_gaps, todo_succession_gap_open=todo_succession_gap_open,
        stride_strategy=stride_strategy,
    ):
        frontier_replan_obligation = None
        replan_transition_ack = frontier_transition_ack
    if frontier_replan_obligation:
        replan_obligation = ensure_replan_novelty_policy(frontier_replan_obligation)
        replan_scope = _scope(replan_obligation)

    replan_obligation = attach_rejected_replan_feedback(replan_obligation, latest_replan_ack_feedback)
    raw_goal_acceptance = (agent_todo_summary or {}).get("goal_acceptance_contract")
    replan_obligation = attach_receipt_replan_context(
        replan_obligation, lifecycle_candidate, goal_id=goal_id, agent_id=agent_id,
        source_status=status_payload, stride_strategy=stride_strategy,
        explore_reader=replan_explore_reader, todo_items=agent_todo_source_items,
        goal_acceptance_contract=raw_goal_acceptance if isinstance(raw_goal_acceptance, dict) else None,
        receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
    )
    goal_frontier_projection = build_goal_frontier_projection_from_summaries(
        goal_id=goal_id, agent_id=agent_id, user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary, work_lane_contract=work_lane_contract,
        replan_obligation=replan_obligation, acceptance_gaps=acceptance_gaps,
        vision_wait_state=vision_wait_state, fallback_gaps=declared_fallback_gaps,
        _frontier_counts=frontier_counts,
    )
    if latest_replan_ack_feedback:
        goal_frontier_projection["replan_ack_feedback"] = latest_replan_ack_feedback
    return {
        "schema_version": "goal_frontier_projection_context_v0",
        "replan_obligation": replan_obligation,
        "stride_strategy": stride_strategy,
        "replan_scope": replan_scope,
        "goal_frontier_projection": goal_frontier_projection,
        "acceptance_gaps": acceptance_gaps,
        "vision_wait_state": vision_wait_state,
        "latest_replan_ack": latest_agent_replan_ack,
        "projected_replan_ack": projected_replan_ack,
        "replan_transition_ack": replan_transition_ack,
        "run_replan_transition_ack": run_replan_transition_ack,
        "replan_transition_candidates": [
            run_transition_candidate,
            frontier_transition_candidate,
            *historical_ack_candidates,
            *capability_transitions,
            *([lifecycle_candidate] if lifecycle_candidate else []),
        ],
    }
