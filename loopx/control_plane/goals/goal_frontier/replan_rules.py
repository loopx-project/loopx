"""Goal-frontier replan rule selection: the decision interpreter for the
`goal_frontier` bounded context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import cast

from ...todos.succession_warning import TODO_SUCCESSION_WARNING_REASON_CODE
from ...work_items.autonomous_replan_obligation import (
    MONITOR_NO_CHANGE_STREAK_THRESHOLD,
    TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE,
    build_autonomous_replan_obligation_payload,
)
from ..goal_vision_read_model import VISION_ACCEPTANCE_GAP_TRIGGER
from .acceptance import GOAL_ACCEPTANCE_HOLD_TRIGGERS
from .ack_policy import _acknowledged_replan_obligation_id
from .long_todo_chain import (
    LONG_TODO_CHAIN_TRIGGER,
    LongTodoChainAckDecision,
    LongTodoChainObservation,
)

Payload = dict[str, object]
PayloadView = Mapping[str, object]

GOAL_FRONTIER_REPLAN_RULE_DECISION_SCHEMA_VERSION = "goal_frontier_replan_rule_decision_v0"
AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION = "autonomous_replan_obligation_v0"
FRONTIER_EXHAUSTED_MONITOR_TRIGGER = "frontier_exhausted_monitor_lane"
TODO_SUCCESSION_GAP_TRIGGER = TODO_SUCCESSION_WARNING_REASON_CODE

_VISION_GAP_TRIGGER_KEYS = (
    "generated_at", "completed_todo_id", "completed_todo_count", "completed_todo_threshold",
    "completed_todo_ids", "vision_todo_ids", "frontier_revision", "outcome_id",
    "requirement_ids", "legacy_frontier_revision", "reason_code", "component_checks",
    "resolution_hint",
)


class GoalFrontierReplanRule(str, Enum):
    EXISTING_OBLIGATION = "existing_obligation"
    BLOCKING_HANDOFF_GATE = "blocking_handoff_gate"
    READY_DEFERRED_SUCCESSOR = "ready_deferred_successor"
    OPEN_USER_TODO = "open_user_todo"
    USER_ACTION_OWNS_EMPTY_FRONTIER = "user_action_owns_empty_frontier"
    TODO_SUCCESSION_GAP = "todo_succession_gap"
    VISION_ACCEPTANCE_GAP = "vision_acceptance_gap"
    LONG_TODO_CHAIN = "long_todo_chain"
    CURRENT_AGENT_BLOCKER = "current_agent_blocker"
    MONITOR_NO_CHANGE_STREAK = "monitor_no_change_streak"
    NOT_MONITOR_ONLY = "not_monitor_only"
    NO_OPEN_MONITOR = "no_open_monitor"
    ADVANCEMENT_REMAINS = "advancement_remains"
    DUE_MONITOR_EXECUTION = "due_monitor_execution"
    FUTURE_MONITOR_WAIT = "future_monitor_wait"
    MONITOR_FRONTIER_EXHAUSTED = "monitor_frontier_exhausted"
    CAPABILITY_EVIDENCE_GAP = "capability_evidence_gap"


# Stable presentation indices for the v0 wire contract. Optional rules append
# here; their actual precedence is the explicit table in the interpreter.
GOAL_FRONTIER_REPLAN_RULE_ORDER = tuple(GoalFrontierReplanRule)


@dataclass(frozen=True)
class GoalFrontierReplanFacts:
    existing_replan_required: bool = False
    blocking_handoff_gate_count: int = 0
    ready_deferred_successor_count: int = 0
    successor_vision_required: bool = False
    blocking_user_open_count: int = 0
    user_open_count: int = 0
    succession_gap_count: int = 0
    succession_gap_acknowledged: bool = False
    vision_gap_acknowledged: bool = False
    agent_advancement_count: int = 0
    total_frontier_advancement: int = 0
    acceptance_gap_count: int = 0
    selectable_frontier_advancement: int = 0
    outcome_checkpoint_replan_required: bool = False
    long_todo_chain_triggered: bool = False
    current_agent_blocker_count: int = 0
    capability_gap_pending: bool = False
    monitor_no_change_streak_triggered: bool = False
    monitor_only_lane: bool = False
    monitor_count: int = 0
    monitor_due_count: int = 0
    monitor_schedule_gap_count: int = 0
    future_monitor_schedule_present: bool = False
    monitor_lane_semantically_valid: bool = True


@dataclass(frozen=True)
class GoalFrontierReplanRuleDecision:
    rule: GoalFrontierReplanRule
    derives_obligation: bool
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": GOAL_FRONTIER_REPLAN_RULE_DECISION_SCHEMA_VERSION,
            "rule": self.rule.value,
            "rule_index": GOAL_FRONTIER_REPLAN_RULE_ORDER.index(self.rule),
            "derives_obligation": self.derives_obligation,
            "reason": self.reason,
        }


def select_goal_frontier_replan_rule(
    facts: GoalFrontierReplanFacts,
) -> GoalFrontierReplanRuleDecision:
    """Select the first matching goal-frontier rule in policy order."""

    ordered_rules = (
        (
            GoalFrontierReplanRule.EXISTING_OBLIGATION,
            facts.existing_replan_required,
            False,
            "an existing scoped obligation remains authoritative",
        ),
        (
            GoalFrontierReplanRule.BLOCKING_HANDOFF_GATE,
            facts.blocking_handoff_gate_count > 0,
            False,
            "a blocking handoff gate owns the next transition",
        ),
        (
            GoalFrontierReplanRule.READY_DEFERRED_SUCCESSOR,
            facts.ready_deferred_successor_count > 0
            and not facts.successor_vision_required,
            False,
            "a deferred successor is already runnable",
        ),
        (
            GoalFrontierReplanRule.OPEN_USER_TODO,
            facts.blocking_user_open_count > 0,
            False,
            "open blocking user work owns the frontier",
        ),
        (
            GoalFrontierReplanRule.TODO_SUCCESSION_GAP,
            facts.succession_gap_count > 0
            and facts.agent_advancement_count == 0
            and facts.total_frontier_advancement == 0
            and not facts.succession_gap_acknowledged,
            True,
            "completed advancement work lacks a successor or no-followup rationale",
        ),
        (
            GoalFrontierReplanRule.VISION_ACCEPTANCE_GAP,
            facts.acceptance_gap_count > 0
            and not facts.vision_gap_acknowledged
            and (
                facts.successor_vision_required
                or facts.outcome_checkpoint_replan_required
                or facts.selectable_frontier_advancement == 0
            ),
            True,
            (
                "the scoped vision gap lacks a fresh evidence-linked outcome "
                "checkpoint or satisfying runnable frontier"
            ),
        ),
        (
            GoalFrontierReplanRule.LONG_TODO_CHAIN,
            facts.long_todo_chain_triggered,
            True,
            "the selectable todo chain crossed the bounded replan threshold",
        ),
        (
            GoalFrontierReplanRule.CURRENT_AGENT_BLOCKER,
            facts.current_agent_blocker_count > 0,
            False,
            "an explicit current-agent blocker owns the empty frontier",
        ),
        (
            GoalFrontierReplanRule.CAPABILITY_EVIDENCE_GAP,
            facts.capability_gap_pending and facts.selectable_frontier_advancement == 0,
            True,
            "a caller-owned evidence gap remains without selectable advancement",
        ),
        (
            GoalFrontierReplanRule.MONITOR_NO_CHANGE_STREAK,
            facts.monitor_only_lane
            and facts.monitor_count > 0
            and facts.monitor_no_change_streak_triggered,
            True,
            "the current agent monitor crossed the no-change replan threshold",
        ),
        (
            GoalFrontierReplanRule.USER_ACTION_OWNS_EMPTY_FRONTIER,
            facts.user_open_count > 0 and facts.total_frontier_advancement == 0,
            False,
            "open user-owned work owns the empty frontier",
        ),
        (
            GoalFrontierReplanRule.NOT_MONITOR_ONLY,
            not facts.monitor_only_lane,
            False,
            "the selected lane is not monitor-only",
        ),
        (
            GoalFrontierReplanRule.NO_OPEN_MONITOR,
            facts.monitor_count <= 0,
            False,
            "no open monitor remains",
        ),
        (
            GoalFrontierReplanRule.ADVANCEMENT_REMAINS,
            facts.agent_advancement_count > 0
            or facts.total_frontier_advancement > 0,
            False,
            "advancement work remains on the frontier",
        ),
        (
            GoalFrontierReplanRule.DUE_MONITOR_EXECUTION,
            facts.monitor_due_count > 0
            and facts.monitor_schedule_gap_count == 0
            and facts.monitor_lane_semantically_valid,
            False,
            "a scheduled monitor is due and remains executable",
        ),
        (
            GoalFrontierReplanRule.FUTURE_MONITOR_WAIT,
            facts.future_monitor_schedule_present
            and facts.monitor_due_count == 0
            and facts.monitor_schedule_gap_count == 0
            and facts.monitor_lane_semantically_valid,
            False,
            "the monitor-only lane has a valid future schedule and should wait quietly",
        ),
        (
            GoalFrontierReplanRule.MONITOR_FRONTIER_EXHAUSTED,
            True,
            True,
            "only monitor work remains on an empty advancement frontier",
        ),
    )
    for rule, matches, derives_obligation, reason in ordered_rules:
        if matches:
            return GoalFrontierReplanRuleDecision(
                rule=rule,
                derives_obligation=derives_obligation,
                reason=reason,
            )
    raise AssertionError("goal-frontier replan rules must have a terminal rule")


def _agent_add_todo_action(text: str, *, priority: str = "P0") -> Payload:
    return {"action": "add", "role": "agent", "priority": priority, "text": text}


def _frontier_obligation_payload(
    *,
    triggers: list[Payload],
    guidance_actions: list[str],
    todo_actions: list[Payload],
    stop_subject: str,
    recommended_action: str,
    agent_id: str | None = None,
    include_agent_id: bool = True,
    stall_threshold: int = 1,
    trigger_count: int = 1,
    rearmed_after_obligation_id: str | None = None,
    extra_fields: Payload | None = None,
) -> Payload:
    return cast(
        Payload,
        build_autonomous_replan_obligation_payload(
            schema_version=AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
            agent_id=agent_id,
            include_agent_id=include_agent_id,
            stall_threshold=stall_threshold,
            trigger_count=trigger_count,
            triggers=triggers,
            guidance_actions=guidance_actions,
            todo_actions=todo_actions,
            stop_condition=(
                f"stop if {stop_subject} requires private material, credentials, "
                "destructive git, production actions, or owner-only decisions"
            ),
            recommended_action=recommended_action,
            rearmed_after_obligation_id=rearmed_after_obligation_id,
            extra_fields=extra_fields,
        ),
    )


def _build_todo_succession_obligation(
    succession_gap_items: Sequence[PayloadView],
    *,
    agent_id: str | None,
) -> Payload:
    settlement_items = list(succession_gap_items[:3])
    settlement_todo_ids = [
        tid for item in settlement_items if (tid := str(item.get("todo_id") or "").strip())
    ]
    return _frontier_obligation_payload(
        agent_id=agent_id,
        trigger_count=len(succession_gap_items),
        triggers=[
            {
                "kind": TODO_SUCCESSION_GAP_TRIGGER,
                "section": "agent_todo_summary.todo_succession_warning",
                "todo_id": item.get("todo_id"),
                "text": item.get("text")
                or item.get("title")
                or "completed advancement needs a successor or no-followup rationale",
                "agent_id": agent_id,
                "claimed_by": item.get("claimed_by"),
                "completion_turn_key": item.get("completion_turn_key"),
            }
            for item in settlement_items
        ],
        guidance_actions=["record_no_followup", "link_successor", "create_successor"],
        todo_actions=[
            {
                "action": "settle",
                "role": "agent",
                "priority": "P0",
                "todo_ids": settlement_todo_ids,
                "text": (
                    "settle the completed scope using its original completion identity, or "
                    "record a genuine dependency on the mutable successor; do not update terminal links"
                ),
            },
            _agent_add_todo_action(
                "create a typed runnable successor only when concrete "
                "technical or validation work remains"
            ),
        ],
        stop_subject="the successor decision",
        recommended_action=(
            f"settle the exact completed Todo(s) {', '.join(settlement_todo_ids)} "
            "through loopx todo complete --no-follow-up with the original completion "
            "identity when no follow-up remains in that scope. Independent open/deferred "
            "work remains active. For genuine dependent work, record its real readiness "
            "dependency on the mutable successor, preserving gates; never add links to a "
            "terminal Todo; do not invent a user gate or lifecycle-only filler Todo"
        ),
        extra_fields={
            "resolution_mode": TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE,
            "satisfying_semantic_outcomes": ["new_runnable_successor"],
        },
    )


def _build_vision_acceptance_obligation(
    compact_acceptance_gaps: Sequence[PayloadView],
    *,
    agent_id: str | None,
    latest_replan_ack: PayloadView | None,
) -> Payload:
    acceptance_held = any(
        gap.get("kind") in GOAL_ACCEPTANCE_HOLD_TRIGGERS for gap in compact_acceptance_gaps
    )
    trigger_gaps = list(compact_acceptance_gaps[:3])
    # Three independent holds may fill the detail slots. Preserve the canonical
    # outcome owner's complete agent-set checkpoint as one more bounded trigger,
    # so the same ACK can cover both sources without dropping a held Todo or
    # acknowledging an unseen outcome revision.
    outcome_gap = next(
        (
            gap
            for gap in compact_acceptance_gaps
            if gap.get("kind") == "goal_outcome_uncovered" and gap.get("agent_id") == agent_id
        ),
        None,
    )
    if outcome_gap is not None and outcome_gap not in trigger_gaps:
        trigger_gaps.append(outcome_gap)
    recommended = (
        " ".join(str(g["resolution_hint"]) for g in compact_acceptance_gaps if g.get("resolution_hint"))
        if all(g.get("resolution_hint") for g in compact_acceptance_gaps)
        else (
            "run a bounded vision-gap replan before another quiet poll: create "
            "successor work, update the agent vision, record evidence gap, or "
            "record no-follow-up"
        )
    )
    return _frontier_obligation_payload(
        agent_id=agent_id,
        trigger_count=len(compact_acceptance_gaps),
        triggers=[
            {
                "kind": gap.get("kind") or VISION_ACCEPTANCE_GAP_TRIGGER,
                "section": "goal_frontier_projection.acceptance_gaps",
                "text": gap.get("replan_trigger_summary")
                or "bounded agent vision reports an open acceptance gap",
                "agent_id": agent_id,
                "acceptance_summary": gap.get("acceptance_summary"),
                "advancement_policy": gap.get("advancement_policy"),
                **{k: gap.get(k) for k in _VISION_GAP_TRIGGER_KEYS if gap.get(k) is not None},
            }
            for gap in trigger_gaps
        ],
        guidance_actions=(
            ["inspect_acceptance_binding", "select_eligible_successor", "record_evidence_gap"]
            if acceptance_held
            else ["create_successor", "update_agent_vision", "record_evidence_gap", "record_no_followup"]
        ),
        # Another unbound advancement Todo cannot repair this admission hold.
        # Keep authoring suggestions aligned with the typed exits.
        todo_actions=(
            []
            if acceptance_held
            else [
                _agent_add_todo_action(
                    "run a bounded vision-gap replan: create the next runnable "
                    "advancement todo or record an explicit no-follow-up rationale"
                )
            ]
        ),
        stop_subject="the gap",
        recommended_action=recommended,
        rearmed_after_obligation_id=_acknowledged_replan_obligation_id(latest_replan_ack),
    )


def build_rule_replan_obligation(
    rule: GoalFrontierReplanRule,
    *,
    agent_id: str | None,
    agent_counts: Mapping[str, int],
    future_schedule_present: bool,
    latest_replan_ack: PayloadView | None,
    compact_acceptance_gaps: Sequence[PayloadView],
    succession_gap_items: Sequence[PayloadView],
    capability_obligation: Payload | None,
    long_chain_observation: LongTodoChainObservation | None,
    long_chain_ack_decision: LongTodoChainAckDecision | None,
    monitor_no_change_trigger: Payload | None,
) -> Payload | None:
    """Build the autonomous replan obligation payload for a deriving frontier rule."""

    if rule is GoalFrontierReplanRule.CAPABILITY_EVIDENCE_GAP:
        return capability_obligation
    if rule is GoalFrontierReplanRule.TODO_SUCCESSION_GAP:
        return _build_todo_succession_obligation(succession_gap_items, agent_id=agent_id)
    if rule is GoalFrontierReplanRule.VISION_ACCEPTANCE_GAP:
        return _build_vision_acceptance_obligation(
            compact_acceptance_gaps, agent_id=agent_id, latest_replan_ack=latest_replan_ack,
        )
    if rule is GoalFrontierReplanRule.LONG_TODO_CHAIN:
        assert long_chain_observation is not None and long_chain_ack_decision is not None
        trigger = long_chain_observation.trigger
        return _frontier_obligation_payload(
            agent_id=agent_id,
            stall_threshold=cast(int, trigger.get("threshold")),
            trigger_count=cast(int, trigger.get("trigger_count")),
            triggers=[{
                "kind": LONG_TODO_CHAIN_TRIGGER,
                "section": "agent_todo_summary",
                "text": (
                    "current agent lane has a long claimed todo chain; "
                    "run a vision checkpoint/replan before continuing linearly"
                ),
                **trigger,
            }],
            guidance_actions=[
                "use_replan_context", "run_bounded_public_research_if_local_evidence_is_missing",
                "group_or_prune_todo_chain", "update_agent_vision", "create_successor",
            ],
            todo_actions=[],
            stop_subject="pruning or external research",
            recommended_action=(
                "run a bounded long-chain vision replan before continuing a 15+ "
                "todo lane: read evidence, use public research if local evidence "
                "is weak, group/prune work, and record an evidence-linked vision "
                "path through replan_action_packet.writeback_contract using the "
                "current Turn settlement binding; retain existing runnable work "
                "when appropriate instead of adding a Todo whose only action is replan"
            ),
            rearmed_after_obligation_id=long_chain_ack_decision.rearmed_after_obligation_id,
        )
    if rule is GoalFrontierReplanRule.MONITOR_NO_CHANGE_STREAK:
        assert monitor_no_change_trigger is not None
        return _frontier_obligation_payload(
            agent_id=agent_id,
            stall_threshold=MONITOR_NO_CHANGE_STREAK_THRESHOLD,
            triggers=[monitor_no_change_trigger],
            guidance_actions=["set_watch_expiry", "write_blocker", "supersede_monitor", "create_successor"],
            todo_actions=[_agent_add_todo_action(
                "resolve the stalled monitor lane with an expiry, blocker, "
                "supersede transition, or runnable successor",
                priority="P1",
            )],
            stop_subject="the replan",
            recommended_action=(
                "resolve the current agent's stalled monitor lane before another "
                "quiet poll: set an expiry, write a blocker, supersede the monitor, "
                "or create runnable successor work"
            ),
        )
    assert rule is GoalFrontierReplanRule.MONITOR_FRONTIER_EXHAUSTED
    return _frontier_obligation_payload(
        include_agent_id=False,
        triggers=[{
            "kind": FRONTIER_EXHAUSTED_MONITOR_TRIGGER,
            "section": "goal_frontier_projection",
            "text": (
                "current goal frontier has no current, unclaimed, or other-agent "
                "advancement todo while only monitor work remains"
            ),
            "agent_id": agent_id,
            "agent_open_count": agent_counts.get("open", 0),
            "agent_monitor_open_count": agent_counts.get("monitor", 0),
            "future_monitor_schedule_present": future_schedule_present,
        }],
        guidance_actions=["create_successor", "supersede_monitor", "set_watch_expiry", "record_no_followup"],
        todo_actions=[_agent_add_todo_action(
            "run a compact goal-frontier replan: create a successor runnable "
            "todo, supersede stale monitor work, set watch-lane expiry, or "
            "record an explicit no-follow-up rationale",
            priority="P1",
        )],
        stop_subject="the replan",
        recommended_action=(
            "run a bounded goal-frontier replan before another monitor-only quiet "
            "poll: create successor work, supersede the monitor lane, set an expiry, "
            "or record an evidence-backed no-follow-up outcome"
        ),
    )
