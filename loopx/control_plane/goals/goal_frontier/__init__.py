from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Literal, overload

from ...agents.agent_scope import (
    agent_scope_blocking_handoff_gates,
    agent_scope_count_advancement_items as _count_advancement_items,
    agent_scope_item_claimed_by,
    agent_scope_item_claimed_by_agent_or_unclaimed,
)
from ...agents.profile import agent_profile_requires_vision
from ...agents.runtime_model import peer_work_key, select_peer_for_work
from ...runtime.time import parse_timestamp
# Refs #4447: the todo contract owns this vocabulary; import it instead of
# restating the literal in every module that classifies a Todo.
from ...todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_replan_obligation_id as normalize_todo_replan_obligation_id,
)
from ...todos.resume_planning import read_todo_deferred_work_counts
from ...todos.succession_warning import TODO_SUCCESSION_WARNING_REASON_CODE, todo_succession_gap_items
from ...todos.todo_semantics import (
    todo_advancement_frontier_counts,
    todo_item_is_watch_only_monitor,
    todo_scoped_runnable_advancement_count,
    todo_summary_open_task_counts,
)
from ...todos.user_gate import open_todo_count as _open_todo_count
from ...work_items.autonomous_replan_ack import (
    autonomous_replan_ack_matches_agent,
    autonomous_replan_ack_matches_frontier,
    normalize_projected_autonomous_replan_ack,
)
from ...work_items.autonomous_replan_obligation import (
    MONITOR_NO_CHANGE_STREAK_THRESHOLD,
    TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE,
    build_autonomous_replan_obligation_payload,
    ensure_replan_novelty_policy,
    with_replan_novelty_guidance,
)
from ...work_items.lifecycle_recovery import attach_receipt_replan_context
from ...work_items.replan_history_codec import project_replan_history
from ...work_items.replan_semantics_codec import project_replan_semantics
from ..goal_vision_policy import completed_todo_replan_threshold, goal_vision_repeats_advancement_until_closed
from ..goal_vision_read_model import (
    VISION_ACCEPTANCE_GAP_TRIGGER as VISION_ACCEPTANCE_GAP_TRIGGER,
    VISION_SUCCESSOR_GAP_TRIGGER as VISION_SUCCESSOR_GAP_TRIGGER,
    _compact_projection_text,
    acceptance_gaps_from_agent_vision as acceptance_gaps_from_agent_vision,
    parse_vision_todo_delta_entries as parse_vision_todo_delta_entries,
)
from ..goal_vision_state import goal_vision_state_is_closed
from ..goal_vision_wait import build_goal_vision_wait_state
from . import outcome_continuity
from .acceptance import (
    GOAL_ACCEPTANCE_HOLD_TRIGGERS,
    acceptance_gaps_from_held_goal_binding,
    acceptance_gaps_from_outcomes,
    project_legacy_acceptance_hold_ack,
)
from .ack_policy import (
    attach_rejected_replan_feedback,
    autonomous_replan_ack_satisfies_obligation,
    replan_successor_transition_ack,
)
from .fallback_source import (
    VISION_FRONTIER_TODO_DELTA_ACTIONS,  # noqa: F401
    FallbackDeclaration,  # noqa: F401
    FallbackTodoSource,
    agent_scoped_selectable_advancement_todo_ids,  # noqa: F401
    declared_fallback_gap_from_agent_vision,
    parse_fallback_declarations,  # noqa: F401
)
from .long_todo_chain import (
    LONG_TODO_CHAIN_TRIGGER,
    LongTodoChainAckDecision,
    LongTodoChainObservation,
    evaluate_long_todo_chain,
)
from .replan_rules import (
    GoalFrontierReplanFacts,
    GoalFrontierReplanRule,
    GoalFrontierReplanRuleDecision,
    select_goal_frontier_replan_rule,
)
from .semantic_history import (
    _latest_runs_for_goal,
    acknowledged_replan_obligation_id as _acknowledged_replan_obligation_id,
    latest_agent_vision_from_runs as latest_agent_vision_from_runs,
    latest_agent_vision_from_status_payload,
    latest_autonomous_replan_ack_from_status_payload as latest_autonomous_replan_ack_from_status_payload,
    latest_missing_vision_checkpoint_from_status_payload,
    latest_replan_ack_feedback_from_status_payload as latest_replan_ack_feedback_from_status_payload,
    receipt_bound_replan_ack_history as receipt_bound_replan_ack_history,
    receipt_bound_replan_source_acks,
    resolve_replan_ack_history_state,
)
from .terminal import (
    GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION,  # noqa: F401
    GOAL_TERMINAL_STATE_SCHEMA_VERSION,  # noqa: F401
    VISION_CHECKPOINT_NO_FOLLOWUP_RESOLUTION,  # noqa: F401
    _terminal_no_followup_resolves_vision_checkpoint,
    derive_goal_terminal_state,
    goal_frontier_is_terminal_no_followup as goal_frontier_is_terminal_no_followup,  # noqa: F401
)

Payload = dict[str, object]
PayloadView = Mapping[str, object]


class GoalFrontierProjectionContext(dict[str, object]):
    """Typed goal-frontier context keeping ``dict`` compatibility for callers."""

    @overload
    def __getitem__(self, key: Literal["stride_strategy", "schema_version"]) -> str: ...
    @overload
    def __getitem__(self, key: str) -> object: ...
    def __getitem__(self, key: str) -> object:
        return super().__getitem__(key)

    @overload
    def get(
        self,
        key: Literal[
            "replan_obligation", "vision_wait_state", "latest_replan_ack",
            "projected_replan_ack", "replan_transition_ack", "run_replan_transition_ack",
        ],
        default: object = None,
        /,
    ) -> Payload | None: ...
    @overload
    def get(self, key: Literal["replan_scope", "goal_frontier_projection"], default: object = None, /) -> Payload: ...
    @overload
    def get(
        self, key: Literal["acceptance_gaps", "replan_transition_candidates"], default: object = None, /,
    ) -> list[Payload]: ...
    @overload
    def get(self, key: str, default: object = None, /) -> object: ...
    def get(self, key: str, default: object = None, /) -> object:
        return super().get(key, default)

GOAL_FRONTIER_PROJECTION_SCHEMA_VERSION = "goal_frontier_projection_v0"
VISION_CONTINUATION_AUDIT_SCHEMA_VERSION = "vision_continuation_audit_v0"
VISION_GAP_JUDGE_SCHEMA_VERSION = "vision_gap_judge_v0"
AUTONOMOUS_REPLAN_DECISION_SCHEMA_VERSION = "autonomous_replan_decision_v0"
AUTONOMOUS_REPLAN_SCOPE_SCHEMA_VERSION = "autonomous_replan_scope_v0"
AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION = "autonomous_replan_obligation_v0"
AUTONOMOUS_REPLAN_REQUIRED_MODE = "autonomous_replan_required"
FRONTIER_EXHAUSTED_MONITOR_TRIGGER = "frontier_exhausted_monitor_lane"
MONITOR_NO_CHANGE_STREAK_TRIGGER = "monitor_no_change_streak"
VISION_PROFILE_MISSING_TRIGGER = "required_agent_vision_missing"
TODO_SUCCESSION_GAP_TRIGGER = TODO_SUCCESSION_WARNING_REASON_CODE

_REPLAN_OWNER_KEYS = ("agent_id", "claimed_by", "bound_agent", "owner_agent", "target_agent", "blocks_agent")
_VISION_GAP_COPY_KEYS = (
    "generated_at", "completed_todo_id", "completed_todo_count", "completed_todo_threshold",
    "completed_todo_ids", "vision_todo_ids", "frontier_revision", "outcome_id",
    "requirement_ids", "legacy_frontier_revision", "reason_code", "component_checks", "resolution_hint",
)
_STOP_CONDITION_SUFFIX = (
    "requires private material, credentials, destructive git, production actions, or owner-only decisions"
)


def _as_dict(value: object) -> Payload:
    return value if isinstance(value, dict) else {}


def _opt_dict(value: object) -> Payload | None:
    return value if isinstance(value, dict) else None


def _dict_items(value: object) -> list[Payload]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, dict)]


def _opt_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _replan_stop_condition(subject: str) -> str:
    return f"stop if {subject} {_STOP_CONDITION_SUFFIX}"


def _add_todo_action(priority: str, text: str) -> Payload:
    return {"action": "add", "role": "agent", "priority": priority, "text": text}


def _build_frontier_obligation(
    *, stall_threshold: int, trigger_count: int, triggers: list[Payload],
    guidance_actions: list[str], todo_actions: list[Payload], stop_subject: str,
    recommended_action: str, agent_id: str | None = None, include_agent_id: bool = False,
    rearmed_after_obligation_id: str | None = None, extra_fields: Payload | None = None,
) -> Payload:
    """Assemble a schema-bound replan obligation payload with shared stop wording."""
    return build_autonomous_replan_obligation_payload(
        schema_version=AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
        agent_id=agent_id, include_agent_id=include_agent_id,
        stall_threshold=stall_threshold, trigger_count=trigger_count, triggers=triggers,
        guidance_actions=guidance_actions, todo_actions=todo_actions,
        stop_condition=_replan_stop_condition(stop_subject), recommended_action=recommended_action,
        rearmed_after_obligation_id=rearmed_after_obligation_id, extra_fields=extra_fields,
    )


def safe_non_negative_int(value: object) -> int:
    if not value or not isinstance(value, (int, float, str, bytes, bytearray)):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def select_autonomous_replan_obligation(
    item: PayloadView, project_asset: PayloadView | None = None, *, agent_id: str | None = None,
) -> Payload | None:
    sources: tuple[PayloadView, ...] = (item, project_asset if isinstance(project_asset, dict) else {})
    safe_agent_id = _opt_str(agent_id)
    if safe_agent_id:
        for source in sources:
            obligations = _opt_dict(source.get("autonomous_replan_obligations_by_agent"))
            if obligations is not None and (value := _opt_dict(obligations.get(safe_agent_id))) is not None:
                return value
    for source in sources:
        if (value := _opt_dict(source.get("autonomous_replan_obligation"))) is not None:
            return value
    return None


def autonomous_replan_is_required(replan_obligation: PayloadView | None) -> bool:
    return bool(replan_obligation and replan_obligation.get("required"))


def align_autonomous_replan_guidance_with_acceptance_policy(
    replan_obligation: Payload | None, *, acceptance_gaps: Sequence[PayloadView] | None,
) -> Payload | None:
    """Remove wait-only guidance when the active vision requires advancement."""
    if not isinstance(replan_obligation, dict):
        return replan_obligation
    trigger_kinds = {_opt_str(t.get("kind")) or "" for t in _dict_items(replan_obligation.get("triggers"))}
    if "blocked_successor_no_progress_repeat" not in trigger_kinds or not any(
        goal_vision_repeats_advancement_until_closed(gap.get("advancement_policy"))
        for gap in _dict_items(acceptance_gaps)
    ):
        return replan_obligation

    raw_actions = replan_obligation.get("todo_actions")
    todo_actions = raw_actions if isinstance(raw_actions, list) else []
    aligned = dict(replan_obligation)
    aligned["guidance_actions"] = ["discover_safe_successor", "create_runnable_todo", "successor_or_supersede"]
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
        for action in todo_actions
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
    return _opt_str(value)


def _autonomous_replan_owner_agent_ids(replan_obligation: PayloadView | None) -> list[str]:
    if not isinstance(replan_obligation, dict):
        return []
    sources: list[PayloadView] = [replan_obligation, *_dict_items(replan_obligation.get("triggers"))]
    return list(dict.fromkeys(
        owner
        for source in sources
        for key in _REPLAN_OWNER_KEYS
        if (owner := _normalize_replan_agent_id(source.get(key))) is not None
    ))


def autonomous_replan_scope_decision(
    replan_obligation: PayloadView | None,
    *,
    agent_id: str | None,
    registered_agent_ids: Sequence[str] | None = None,
) -> Payload:
    """Return whether a replan obligation belongs to this agent lane.

    Explicit agent-owned replans are consumed only by that agent. Unscoped
    goal-level replans are deterministically assigned to one registered peer.
    """
    normalized_agent_id = _normalize_replan_agent_id(agent_id)
    owners = _autonomous_replan_owner_agent_ids(replan_obligation)
    required = autonomous_replan_is_required(replan_obligation)
    selected_peer_agent: str | None = None
    if not required:
        applies, scope = False, "not_required"
    elif not normalized_agent_id:
        applies, scope = True, "unscoped_quota_call"
    elif owners:
        applies, scope = normalized_agent_id in owners, "explicit_agent_owner"
    else:
        obligation_dict = _as_dict(replan_obligation)
        selected_peer_agent = select_peer_for_work(
            registered_agent_ids or [],
            # Display evidence and prose are mutable projections, not work
            # identity. Normalize before both selection and permission checks.
            work_key=peer_work_key(
                {
                    "obligation_id": obligation_dict.get("obligation_id")
                    or ensure_replan_novelty_policy(obligation_dict)["obligation_id"],
                },
                fallback="autonomous_replan",
            ),
        )
        applies = bool(selected_peer_agent and normalized_agent_id == selected_peer_agent)
        scope = "deterministic_peer_assignment"
    return {
        "schema_version": AUTONOMOUS_REPLAN_SCOPE_SCHEMA_VERSION,
        "required": required, "applies": applies, "scope": scope,
        "agent_id": normalized_agent_id, "agent_model": "peer_v1",
        "owner_agent_ids": owners, "selected_peer_agent": selected_peer_agent,
    }


def projected_autonomous_replan_ack_for_agent(
    item: PayloadView, project_asset: PayloadView | None, *, agent_id: str | None,
) -> Payload | None:
    """Return the current projected replan ACK when it belongs to this agent."""
    asset = project_asset if isinstance(project_asset, dict) else {}
    for candidate in (item.get("autonomous_replan_ack"), asset.get("autonomous_replan_ack")):
        normalized = normalize_projected_autonomous_replan_ack(_opt_dict(candidate))
        if autonomous_replan_ack_matches_agent(normalized, agent_id=agent_id):
            return normalized
    return None


def acceptance_gaps_from_agent_profile_requirement(
    agent_profile: PayloadView | None,
    *,
    agent_id: str | None,
    agent_vision: PayloadView | None,
    missing_checkpoint: PayloadView | None,
) -> list[Payload]:
    """Require a vision baseline for registered long-lived peer lanes."""
    if (
        not agent_id
        or not agent_profile_requires_vision(agent_profile)
        or isinstance(agent_vision, dict)
        or isinstance(missing_checkpoint, dict)
    ):
        return []
    return [{
        "kind": VISION_PROFILE_MISSING_TRIGGER,
        "source": "agent_profile",
        "agent_id": agent_id,
        "replan_trigger_summary": (
            "the registered long-lived agent lane requires a persisted vision "
            "baseline before ordinary delivery or monitor-only quiet wait"
        ),
        "acceptance_summary": (
            "Write a bounded agent vision with objective, acceptance evidence, "
            "advancement policy, and replan trigger; or explicitly mark the "
            "profile vision requirement optional."
        ),
        "advancement_policy": "repeat_until_closed",
    }]


def build_vision_continuation_audit(
    *, goal_id: str | None = None, agent_id: str | None, acceptance_gaps: Sequence[PayloadView] | None,
) -> Payload | None:
    """Return the closeout audit contract for an open per-agent vision gap.

    This is a read-path contract: quota/status can tell an agent that the
    selected todo is only a step toward the active vision. Writeback still goes
    through normal todo, evidence, and refresh-state commands.
    """
    compact_gaps = _dict_items(acceptance_gaps)
    if not compact_gaps:
        return None
    vision_gap_judge = build_vision_gap_judge(goal_id=goal_id, agent_id=agent_id, acceptance_gaps=compact_gaps)
    acceptance_requirements = [
        t for g in compact_gaps if (t := _compact_projection_text(g.get("acceptance_summary"), limit=180))
    ]
    trigger_kinds = [k for g in compact_gaps if (k := _compact_projection_text(g.get("kind"), limit=80))]
    audit: Payload = {
        "schema_version": VISION_CONTINUATION_AUDIT_SCHEMA_VERSION,
        "required": True, "agent_id": agent_id, "decision": "acceptance_gap_open",
        "selected_todo_is_goal_completion": False, "closeout_allowed_without_evidence": False,
        "trigger_count": len(compact_gaps), "trigger_kinds": list(dict.fromkeys(trigger_kinds))[:5],
        "acceptance_gaps": compact_gaps[:5], "vision_gap_judge": vision_gap_judge,
        "authoritative_evidence_kinds": [
            "changed_files", "public_safe_evidence_records", "public_web_research_findings",
            "evaluation_outputs", "successor_state", "blocker_state", "superseding_agent_vision",
        ],
        "not_satisfied_by": [
            "todo_completion_alone", "autonomous_replan_ack_alone", "vision_checkpoint_alone",
            "registry_registration_alone", "no_followup_without_acceptance_evidence",
        ],
        "required_before_closeout": [
            "derive_requirements_from_active_vision_and_current_todo",
            "name_authoritative_evidence_for_each_requirement",
            "inspect_registry_declared_materials_before_external_research",
            "run_bounded_public_research_when_local_evidence_is_missing",
            "create_successor_or_write_vision_replan_trigger_when_unproven",
        ],
        "recommended_action": (
            "audit active per-agent vision acceptance before todo closeout; "
            "if evidence is weak or missing, inspect registry-declared material "
            "references before bounded public research, then keep the vision "
            "active with a successor todo or --vision-replan-trigger"
        ),
    }
    if acceptance_requirements:
        audit["acceptance_requirements"] = acceptance_requirements[:5]
    diagnostics = [
        {key: gap[key] for key in ("reason_code", "component_checks", "resolution_hint")}
        for gap in compact_gaps
        if gap.get("reason_code") and gap.get("component_checks") and gap.get("resolution_hint")
    ]
    if diagnostics:
        audit["outcome_checkpoint_diagnostics"] = diagnostics[:5]
    return audit


def build_vision_gap_judge(
    *, goal_id: str | None = None, agent_id: str | None, acceptance_gaps: Sequence[PayloadView] | None,
) -> Payload:
    """Return the strict done/continue judge for active per-agent vision gaps.

    The read model keeps the prompt essence compact: unless current evidence
    satisfies, blocks, or supersedes the active vision, the agent should judge
    the vision as still CONTINUE.
    """
    compact_gaps = _dict_items(acceptance_gaps)
    first_gap = compact_gaps[0] if compact_gaps else {}
    reason = _compact_projection_text(
        first_gap.get("replan_trigger_summary") or "active per-agent vision still has an open acceptance gap",
        limit=220,
    )
    return {
        "schema_version": VISION_GAP_JUDGE_SCHEMA_VERSION,
        "goal_id": goal_id, "agent_id": agent_id, "done": False, "decision": "continue",
        "reason": reason or "active per-agent vision still has an open acceptance gap",
        "agent_judge_instruction": (
            "Judge vision closure: compare active vision acceptance_summary "
            "with the host-projected agent-scoped coverage ledger. "
            "Inspect permitted registry-declared material references before bounded "
            "public web research. "
            "Mark done only when evidence proves completion, a blocker/user "
            "gate, or superseding/no-follow-up closure; otherwise continue."
        ),
        "evidence_read_instruction": (
            "Use the host-projected compact coverage ledger and uncovered frontier. "
            "The agent-scoped evidence log remains the durable source and an optional "
            "diagnostic readback, not a model-executed closure gate."
        ),
        "registry_read_instruction": (
            "Before external research, inspect the selected goal's registry-declared "
            "topic_authority and project_materials metadata, preferring any projected "
            "replan_context or agent_material_frontier. Use role, freshness, revision, "
            "boundary, gate_status, and conflict_rule to choose permitted references. "
            "Registration guides discovery; it neither grants access nor proves acceptance."
        ),
        "external_research_instruction": (
            "If projected evidence, the evidence log, and permitted registry-declared "
            "references remain missing, stale, or too weak and the acceptance question "
            "depends on public facts, run bounded public web research using primary or "
            "authoritative sources; record confirmed/refuted findings as public-safe "
            "evidence or a compact vision replan trigger before judging."
        ),
        "research_writeback_required_when_used": [
            "source_url_or_public_reference", "confirmed_or_refuted_finding",
            "supports_or_refutes_acceptance_gap", "successor_todo_or_vision_replan_trigger",
        ],
        "done_only_when": [
            "authoritative_evidence_satisfies_acceptance",
            "final_deliverable_or_eval_output_satisfies_acceptance",
            "blocker_or_user_gate_is_projected",
            "superseding_vision_or_no_followup_closes_the_frontier",
        ],
        "continue_when": [
            "evidence_is_missing_weak_or_stale",
            "todo_lifecycle_or_protocol_status_is_the_only_proof",
            "acceptance_gap_is_still_projected",
        ],
        "otherwise": "continue",
    }


def _blocking_user_open_count(summary: Payload | None) -> int:
    if not isinstance(summary, dict):
        return 0
    gate_items = summary.get("gate_open_items")
    if isinstance(gate_items, list):
        return sum(1 for item in gate_items if isinstance(item, dict) and _todo_item_is_actionable_open(item))
    # Legacy or partial summaries without typed gate projection fail closed.
    return _open_todo_count(summary)


def _todo_item_is_actionable_open(item: PayloadView) -> bool:
    if item.get("done") is True:
        return False
    return str(item.get("status") or "open").strip().lower() in {"", "open", "todo", "active", "pending"}


def _todo_task_class(item: PayloadView) -> str:
    return str(item.get("task_class") or "").strip()


def _summary_task_counts(summary: Payload | None) -> dict[str, int]:
    open_count = _open_todo_count(summary)
    if not isinstance(summary, dict):
        return {"open": open_count, "advancement": 0, "monitor": 0, "monitor_due": 0, "watch_only_monitor_due": 0}
    executable = summary.get("executable_backlog_items")
    monitor_open = summary.get("monitor_open_items")
    open_monitors = [i for i in _dict_items(monitor_open) if _todo_item_is_actionable_open(i)] if isinstance(monitor_open, list) else []
    if isinstance(summary.get("watch_only_monitor_count"), int):
        watch_only_count = safe_non_negative_int(summary.get("watch_only_monitor_count"))
    elif isinstance(monitor_open, list):
        watch_only_count = sum(1 for item in open_monitors if todo_item_is_watch_only_monitor(item))
    else:
        watch_only_count = 0
    if isinstance(summary.get("convergence_open_count"), int):
        open_count = safe_non_negative_int(summary.get("convergence_open_count"))
    else:
        # Compatibility-only fallback for summaries produced before the typed
        # TypeScript lane owner exposed convergence_open_count.
        open_count = max(0, open_count - watch_only_count)
    work_counts = todo_summary_open_task_counts(summary) if summary.get("work_counts") is not None else None
    if isinstance(work_counts, dict):
        advancement_count = safe_non_negative_int(work_counts.get("advancement"))
        monitor_count = max(0, safe_non_negative_int(work_counts.get("monitor")) - watch_only_count)
    else:
        advancement_count = (
            _count_advancement_items(executable)
            if isinstance(executable, list)
            else safe_non_negative_int(summary.get("claimed_advancement_open_count"))
            + sum(1 for i in _dict_items(summary.get("unclaimed_priority_open_items")) if _todo_task_class(i) == TODO_TASK_CLASS_ADVANCEMENT)
        )
        monitor_count = (
            sum(1 for i in open_monitors if _todo_task_class(i) == TODO_TASK_CLASS_MONITOR and not todo_item_is_watch_only_monitor(i))
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
    agent_todo_summary: Payload | None, *, agent_id: str | None,
) -> Payload | None:
    if not isinstance(agent_todo_summary, dict):
        return None
    monitor_items = agent_todo_summary.get("monitor_open_items")
    if not isinstance(monitor_items, list):
        return None
    eligible = [
        item for item in monitor_items
        if isinstance(item, dict)
        and _todo_item_is_actionable_open(item)
        and _todo_task_class(item) == TODO_TASK_CLASS_MONITOR
        and not todo_item_is_watch_only_monitor(item)
        and (not agent_id or agent_scope_item_claimed_by(item) == agent_id)
    ]
    if not eligible:
        return None
    # The enclosing frontier already established that no advancement is
    # selectable. Keep its eligibility facts, but share count/expiry/winner
    # selection and the existing public trigger with the typed replan owner.
    return project_replan_history(
        operation="frontier_monitor_streak",
        agent_id=agent_id,
        agent_todos={"monitor_open_items": eligible},
        streak_threshold=MONITOR_NO_CHANGE_STREAK_THRESHOLD,
    )


def _frontier_advancement_counts(
    *, agent_todo_summary: Payload | None, agent_id: str | None,
) -> dict[str, int]:
    return todo_advancement_frontier_counts(agent_todo_summary, agent_id=agent_id)


def _compact_todo_id(item: object) -> str | None:
    return _opt_str(item.get("todo_id")) if isinstance(item, dict) else None


def _deferred_successors(summary: Payload | None, *, agent_id: str | None) -> Payload:
    source = summary if isinstance(summary, dict) else {}
    counts = read_todo_deferred_work_counts(source)
    ready_todo_ids = [
        tid for item in _dict_items(source.get("deferred_resume_candidates"))[:5] if (tid := _compact_todo_id(item))
    ]
    ready_by_agent = _as_dict(counts.get("ready_by_agent"))
    projection: Payload = {
        "ready_count": counts["ready"], "blocked_count": counts["blocked"],
        "unknown_count": counts["unknown"], "counts_complete": counts["complete"],
        "current_agent_ready_count": ready_by_agent.get(agent_id, 0) if agent_id else 0,
        "ready_todo_ids": ready_todo_ids,
    }
    if ready_todo_ids:
        projection["top_ready_todo_id"] = ready_todo_ids[0]
    return projection


def _is_continuous_monitor_lane(work_lane_contract: PayloadView | None) -> bool:
    return bool(work_lane_contract and work_lane_contract.get("lane") == TODO_TASK_CLASS_MONITOR)


def _is_monitor_only_lane(work_lane_contract: PayloadView | None) -> bool:
    return bool(
        work_lane_contract is not None
        and _is_continuous_monitor_lane(work_lane_contract)
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
    if safe_non_negative_int(agent_todo_summary.get("monitor_due_count")) > 0:
        return False
    if safe_non_negative_int(agent_todo_summary.get("monitor_schedule_gap_count")) > 0:
        return False
    monitor_items = agent_todo_summary.get("monitor_open_items")
    return isinstance(monitor_items, list) and len(monitor_items) > 0


def _blocking_handoff_gate_count(agent_todo_summary: Payload | None, *, agent_id: str | None) -> int:
    if not agent_id or not isinstance(agent_todo_summary, dict):
        return 0
    return len(agent_scope_blocking_handoff_gates(agent_todo_summary, agent_id=agent_id))


def _ready_deferred_successor_count(agent_todo_summary: Payload | None, *, agent_id: str | None) -> int:
    if not isinstance(agent_todo_summary, dict):
        return 0
    current_count = safe_non_negative_int(agent_todo_summary.get("current_agent_deferred_resume_count"))
    unclaimed_count = safe_non_negative_int(agent_todo_summary.get("unclaimed_deferred_resume_count"))
    if current_count or unclaimed_count:
        return current_count + unclaimed_count
    return sum(
        1 for item in _dict_items(agent_todo_summary.get("deferred_resume_candidates"))
        if agent_scope_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)
    )


def _replan_evidence_acknowledged(
    items: list[Payload], latest_replan_ack: Payload | None, *, time_key: str,
) -> bool:
    """Return true when a valid replan ack postdates the newest evidence item.

    A gap that the agent already acknowledged with a repair delta must not
    re-derive an endless replan obligation: without this, any stale gap turns
    quota into a permanent run-now loop that no acknowledgement can settle.
    """
    if not items or not isinstance(latest_replan_ack, dict):
        return False
    ack_time = parse_timestamp(latest_replan_ack.get("generated_at"))
    if ack_time is None:
        return False
    newest = max(
        (ts for item in items if isinstance(item, dict) and (ts := parse_timestamp(item.get(time_key))) is not None),
        default=None,
    )
    return newest is not None and ack_time >= newest


def _succession_gap_acknowledged(gap_items: list[Payload], latest_replan_ack: Payload | None) -> bool:
    """Return true when a valid replan ack postdates the newest succession gap."""
    raw_outcomes = _as_dict(_as_dict(latest_replan_ack).get("semantic_delta")).get("outcomes")
    outcomes = set(raw_outcomes) if isinstance(raw_outcomes, (list, tuple, set)) else set()
    if "new_runnable_successor" not in outcomes:
        return False
    return _replan_evidence_acknowledged(gap_items, latest_replan_ack, time_key="completed_at")


def _vision_gap_acknowledged(acceptance_gaps: list[Payload], latest_replan_ack: Payload | None) -> bool:
    """Delegate exact acceptance holds and ordinary evidence freshness to TS."""
    if not acceptance_gaps or not isinstance(latest_replan_ack, dict):
        return False
    return project_replan_semantics({
        "operation": "vision_ack", "acceptance_gaps": acceptance_gaps, "ack": latest_replan_ack,
    })["acknowledged"] is True


def _evaluate_long_chain_with_rearm(
    *, agent_todo_summary: Payload | None, agent_counts: dict[str, int], frontier_counts: dict[str, int],
    agent_id: str | None, agent_todo_source_items: list[Payload] | None,
    latest_replan_ack: Payload | None, prior_rearm_ack: Payload | None,
    current_transition_replan_ack: Payload | None,
) -> tuple[LongTodoChainObservation | None, LongTodoChainAckDecision | None]:
    def evaluate(ack: Payload | None) -> tuple[LongTodoChainObservation | None, LongTodoChainAckDecision | None]:
        return evaluate_long_todo_chain(
            agent_todo_summary=agent_todo_summary, agent_counts=agent_counts,
            frontier_counts=frontier_counts, agent_id=agent_id,
            agent_todo_source_items=agent_todo_source_items, latest_replan_ack=ack,
        )

    observation, ack_decision = evaluate(current_transition_replan_ack or latest_replan_ack)
    if (
        ack_decision and not ack_decision.acknowledged
        and ack_decision.rearmed_after_obligation_id is None and prior_rearm_ack is not None
    ):
        _, prior_decision = evaluate(prior_rearm_ack)
        if prior_decision and not prior_decision.acknowledged and prior_decision.rearmed_after_obligation_id is not None:
            ack_decision = LongTodoChainAckDecision(
                acknowledged=False, rearmed_after_obligation_id=prior_decision.rearmed_after_obligation_id,
            )
    return observation, ack_decision


def _build_todo_succession_gap_obligation(*, agent_id: str | None, succession_gap_items: list[Payload]) -> Payload:
    settlement_items = succession_gap_items[:3]
    settlement_todo_ids = [tid for item in settlement_items if (tid := _opt_str(item.get("todo_id")))]
    triggers: list[Payload] = [
        {
            "kind": TODO_SUCCESSION_GAP_TRIGGER,
            "section": "agent_todo_summary.todo_succession_warning",
            "todo_id": item.get("todo_id"),
            "text": item.get("text") or item.get("title") or "completed advancement needs a successor or no-followup rationale",
            "agent_id": agent_id,
            "claimed_by": item.get("claimed_by"),
            "completion_turn_key": item.get("completion_turn_key"),
        }
        for item in settlement_items
    ]
    return _build_frontier_obligation(
        agent_id=agent_id, include_agent_id=True, stall_threshold=1, trigger_count=len(succession_gap_items),
        triggers=triggers, guidance_actions=["record_no_followup", "link_successor", "create_successor"],
        todo_actions=[
            {
                "action": "settle", "role": "agent", "priority": "P0", "todo_ids": settlement_todo_ids,
                "text": (
                    "settle the completed scope using its original completion identity, or "
                    "record a genuine dependency on the mutable successor; do not update terminal links"
                ),
            },
            _add_todo_action("P0", "create a typed runnable successor only when concrete technical or validation work remains"),
        ],
        stop_subject="the successor decision",
        recommended_action=(
            f"settle the exact completed Todo(s) {', '.join(settlement_todo_ids)} through "
            "loopx todo complete --no-follow-up with the original completion identity "
            "when no follow-up remains in that scope. Independent open/deferred work "
            "remains active. For genuine dependent work, record its real readiness "
            "dependency on the mutable successor, preserving gates; never add links to "
            "a terminal Todo; do not invent a user gate or lifecycle-only filler Todo"
        ),
        extra_fields={
            "resolution_mode": TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE,
            "satisfying_semantic_outcomes": ["new_runnable_successor"],
        },
    )


def _build_vision_acceptance_gap_obligation(
    *, agent_id: str | None, compact_acceptance_gaps: list[Payload],
    latest_replan_ack: Payload | None, prior_rearm_ack: Payload | None,
) -> Payload:
    acceptance_held = any(gap.get("kind") in GOAL_ACCEPTANCE_HOLD_TRIGGERS for gap in compact_acceptance_gaps)
    trigger_gaps = compact_acceptance_gaps[:3]
    # Three independent holds may fill the detail slots. Preserve the
    # canonical outcome owner's complete agent-set checkpoint as one more
    # bounded trigger, so the same ACK can cover both sources without
    # dropping a held Todo or acknowledging an unseen outcome revision.
    outcome_gap = next(
        (g for g in compact_acceptance_gaps if g.get("kind") == "goal_outcome_uncovered" and g.get("agent_id") == agent_id),
        None,
    )
    if outcome_gap is not None and outcome_gap not in trigger_gaps:
        trigger_gaps.append(outcome_gap)
    triggers: list[Payload] = [
        {
            "kind": gap.get("kind") or VISION_ACCEPTANCE_GAP_TRIGGER,
            "section": "goal_frontier_projection.acceptance_gaps",
            "text": gap.get("replan_trigger_summary") or "bounded agent vision reports an open acceptance gap",
            "agent_id": agent_id,
            "acceptance_summary": gap.get("acceptance_summary"),
            "advancement_policy": gap.get("advancement_policy"),
            **{key: gap.get(key) for key in _VISION_GAP_COPY_KEYS if gap.get(key) is not None},
        }
        for gap in trigger_gaps
    ]
    hints = [str(gap["resolution_hint"]) for gap in compact_acceptance_gaps if gap.get("resolution_hint")]
    recommended_action = (
        " ".join(hints)
        if len(hints) == len(compact_acceptance_gaps)
        else (
            "run a bounded vision-gap replan before another quiet poll: create "
            "successor work, update the agent vision, record evidence gap, or record no-follow-up"
        )
    )
    return _build_frontier_obligation(
        agent_id=agent_id, include_agent_id=True, stall_threshold=1, trigger_count=len(compact_acceptance_gaps),
        triggers=triggers,
        guidance_actions=(
            ["inspect_acceptance_binding", "select_eligible_successor", "record_evidence_gap"]
            if acceptance_held
            else ["create_successor", "update_agent_vision", "record_evidence_gap", "record_no_followup"]
        ),
        # Another unbound advancement Todo cannot repair this admission
        # hold. Keep authoring suggestions aligned with the typed exits.
        todo_actions=(
            []
            if acceptance_held
            else [_add_todo_action(
                "P0",
                "run a bounded vision-gap replan: create the next runnable "
                "advancement todo or record an explicit no-follow-up rationale",
            )]
        ),
        stop_subject="the gap", recommended_action=recommended_action,
        rearmed_after_obligation_id=_acknowledged_replan_obligation_id(latest_replan_ack or prior_rearm_ack),
    )


def _build_rule_obligation(
    *, decision: GoalFrontierReplanRuleDecision, agent_id: str | None, agent_counts: dict[str, int],
    agent_todo_summary: Payload | None, compact_acceptance_gaps: list[Payload],
    succession_gap_items: list[Payload], long_chain_observation: LongTodoChainObservation | None,
    long_chain_ack_decision: LongTodoChainAckDecision | None, monitor_no_change_trigger: Payload | None,
    capability_obligation: Payload | None, latest_replan_ack: Payload | None, prior_rearm_ack: Payload | None,
) -> Payload | None:
    rule = decision.rule
    if rule is GoalFrontierReplanRule.CAPABILITY_EVIDENCE_GAP:
        return capability_obligation
    if rule is GoalFrontierReplanRule.TODO_SUCCESSION_GAP:
        return _build_todo_succession_gap_obligation(agent_id=agent_id, succession_gap_items=succession_gap_items)
    if rule is GoalFrontierReplanRule.VISION_ACCEPTANCE_GAP:
        return _build_vision_acceptance_gap_obligation(
            agent_id=agent_id, compact_acceptance_gaps=compact_acceptance_gaps,
            latest_replan_ack=latest_replan_ack, prior_rearm_ack=prior_rearm_ack,
        )
    if rule is GoalFrontierReplanRule.LONG_TODO_CHAIN:
        assert long_chain_observation is not None and long_chain_ack_decision is not None
        trigger = long_chain_observation.trigger
        raw_threshold, raw_count = trigger.get("threshold"), trigger.get("trigger_count")
        return _build_frontier_obligation(
            agent_id=agent_id, include_agent_id=True,
            stall_threshold=raw_threshold if isinstance(raw_threshold, int) else long_chain_observation.threshold,
            trigger_count=raw_count if isinstance(raw_count, int) else long_chain_observation.trigger_count,
            triggers=[{
                "kind": LONG_TODO_CHAIN_TRIGGER, "section": "agent_todo_summary",
                "text": "current agent lane has a long claimed todo chain; run a vision checkpoint/replan before continuing linearly",
                **trigger,
            }],
            guidance_actions=[
                "use_replan_context", "run_bounded_public_research_if_local_evidence_is_missing",
                "group_or_prune_todo_chain", "update_agent_vision", "create_successor",
            ],
            todo_actions=[], stop_subject="pruning or external research",
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
        return _build_frontier_obligation(
            agent_id=agent_id, include_agent_id=True,
            stall_threshold=MONITOR_NO_CHANGE_STREAK_THRESHOLD, trigger_count=1,
            triggers=[monitor_no_change_trigger],
            guidance_actions=["set_watch_expiry", "write_blocker", "supersede_monitor", "create_successor"],
            todo_actions=[_add_todo_action(
                "P1",
                "resolve the stalled monitor lane with an expiry, blocker, supersede transition, or runnable successor",
            )],
            stop_subject="the replan",
            recommended_action=(
                "resolve the current agent's stalled monitor lane before another "
                "quiet poll: set an expiry, write a blocker, supersede the monitor, "
                "or create runnable successor work"
            ),
        )
    assert rule is GoalFrontierReplanRule.MONITOR_FRONTIER_EXHAUSTED
    return _build_frontier_obligation(
        stall_threshold=1, trigger_count=1,
        triggers=[{
            "kind": FRONTIER_EXHAUSTED_MONITOR_TRIGGER, "section": "goal_frontier_projection",
            "text": "current goal frontier has no current, unclaimed, or other-agent advancement todo while only monitor work remains",
            "agent_id": agent_id, "agent_open_count": agent_counts.get("open", 0),
            "agent_monitor_open_count": agent_counts.get("monitor", 0),
            "future_monitor_schedule_present": _monitor_only_lane_has_future_schedule(agent_todo_summary),
        }],
        guidance_actions=["create_successor", "supersede_monitor", "set_watch_expiry", "record_no_followup"],
        todo_actions=[_add_todo_action(
            "P1",
            "run a compact goal-frontier replan: create a successor runnable "
            "todo, supersede stale monitor work, set watch-lane expiry, or "
            "record an explicit no-follow-up rationale",
        )],
        stop_subject="the replan",
        recommended_action=(
            "run a bounded goal-frontier replan before another monitor-only quiet "
            "poll: create successor work, supersede the monitor lane, set an expiry, "
            "or record an evidence-backed no-follow-up outcome"
        ),
    )


def derive_goal_frontier_replan_obligation_from_summaries(
    *,
    user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None,
    work_lane_contract: PayloadView | None,
    agent_id: str | None,
    existing_replan_obligation: PayloadView | None,
    agent_todo_source_items: list[Payload] | None = None,
    latest_replan_ack: Payload | None = None,
    prior_rearm_ack: Payload | None = None,
    current_transition_replan_ack: Payload | None = None,
    acceptance_gaps: list[Payload] | None = None,
    monitor_lane_semantically_valid: bool = True,
    capability_obligation: Payload | None = None,
    vision_settlement_query: bool = False,
    _frontier_counts: dict[str, int] | None = None,
) -> Payload | None:
    """Return a compact replan obligation when the goal frontier has no advancement.

    This keeps the per-goal completion/replan rule in the goal-frontier policy
    seam. Quota should consume the resulting obligation instead of embedding
    monitor/vision semantics in its scheduler path.
    """
    agent_counts = _summary_task_counts(agent_todo_summary)
    # Only the enclosing context reduction supplies this fresh, same-agent
    # snapshot. Standalone callers still classify their current source.
    frontier_counts = (
        _frontier_counts if _frontier_counts is not None
        else _frontier_advancement_counts(agent_todo_summary=agent_todo_summary, agent_id=agent_id)
    )
    total_frontier_advancement = sum(frontier_counts.values())
    selectable_frontier_advancement = (
        frontier_counts["current_agent_claimed_advancement_count"] + frontier_counts["unclaimed_advancement_count"]
    )
    monitor_no_change_trigger = (
        _monitor_no_change_streak_trigger(agent_todo_summary, agent_id=agent_id)
        if selectable_frontier_advancement == 0 else None
    )
    compact_acceptance_gaps = sorted(
        _dict_items(acceptance_gaps),
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
    gap_kinds = {gap.get("kind") for gap in compact_acceptance_gaps}
    successor_vision_required = bool(gap_kinds & {VISION_SUCCESSOR_GAP_TRIGGER, VISION_PROFILE_MISSING_TRIGGER})
    succession_gap_items = todo_succession_gap_items(agent_todo_summary, agent_id=agent_id)
    long_chain_observation, long_chain_ack_decision = _evaluate_long_chain_with_rearm(
        agent_todo_summary=agent_todo_summary, agent_counts=agent_counts, frontier_counts=frontier_counts,
        agent_id=agent_id, agent_todo_source_items=agent_todo_source_items,
        latest_replan_ack=latest_replan_ack, prior_rearm_ack=prior_rearm_ack,
        current_transition_replan_ack=current_transition_replan_ack,
    )
    summary_map = _as_dict(agent_todo_summary)
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
            selectable_frontier_advancement=0 if vision_settlement_query else selectable_frontier_advancement,
            outcome_checkpoint_replan_required=outcome_continuity.VISION_OUTCOME_CHECKPOINT_REQUIRED_TRIGGER in gap_kinds,
            long_todo_chain_triggered=bool(
                long_chain_observation is not None and long_chain_ack_decision is not None
                and not long_chain_ack_decision.acknowledged
            ),
            current_agent_blocker_count=safe_non_negative_int(summary_map.get("current_agent_blocker_count")),
            capability_gap_pending=bool(capability_obligation and capability_obligation.get("required") is True),
            monitor_no_change_streak_triggered=monitor_no_change_trigger is not None,
            monitor_only_lane=_is_monitor_only_lane(work_lane_contract),
            monitor_count=agent_counts.get("monitor", 0),
            monitor_due_count=safe_non_negative_int(summary_map.get("monitor_due_count")),
            monitor_schedule_gap_count=safe_non_negative_int(summary_map.get("monitor_schedule_gap_count")),
            future_monitor_schedule_present=_monitor_only_lane_has_future_schedule(agent_todo_summary),
            monitor_lane_semantically_valid=monitor_lane_semantically_valid,
        )
    )
    if not replan_rule.derives_obligation:
        return None
    return _build_rule_obligation(
        decision=replan_rule, agent_id=agent_id, agent_counts=agent_counts,
        agent_todo_summary=agent_todo_summary, compact_acceptance_gaps=compact_acceptance_gaps,
        succession_gap_items=succession_gap_items, long_chain_observation=long_chain_observation,
        long_chain_ack_decision=long_chain_ack_decision, monitor_no_change_trigger=monitor_no_change_trigger,
        capability_obligation=capability_obligation, latest_replan_ack=latest_replan_ack, prior_rearm_ack=prior_rearm_ack,
    )


def build_goal_frontier_projection_from_summaries(
    *, goal_id: str, agent_id: str | None, user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None, work_lane_contract: PayloadView | None,
    replan_obligation: PayloadView | None, acceptance_gaps: Sequence[PayloadView] | None = None,
    vision_wait_state: PayloadView | None = None, fallback_gaps: list[Payload] | None = None,
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
    selectable = frontier_counts["current_agent_claimed_advancement_count"] + frontier_counts["unclaimed_advancement_count"]
    monitor_only_lane = bool(
        _is_monitor_only_lane(work_lane_contract)
        and agent_counts.get("monitor", 0) > 0 and agent_counts.get("advancement", 0) == 0
        and (selectable == 0 if agent_id else sum(frontier_counts.values()) == 0)
    )
    projection = build_goal_frontier_projection(
        goal_id=goal_id, agent_id=agent_id, user_counts=user_counts, agent_counts=agent_counts,
        current_agent_claimed_advancement_count=frontier_counts["current_agent_claimed_advancement_count"],
        unclaimed_advancement_count=frontier_counts["unclaimed_advancement_count"],
        other_agent_claimed_advancement_count=frontier_counts["other_agent_claimed_advancement_count"],
        monitor_only_lane=monitor_only_lane, replan_obligation=replan_obligation,
        acceptance_gaps=acceptance_gaps, vision_wait_state=vision_wait_state, fallback_gaps=fallback_gaps,
        deferred_successors=_deferred_successors(agent_todo_summary, agent_id=agent_id),
    )
    source_completeness, terminal_state = derive_goal_terminal_state(
        user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary, projection=projection,
    )
    work_counts = _opt_dict(_as_dict(agent_todo_summary).get("work_counts"))
    _as_dict(projection["normalized_progress"]).update({
        "agent_counts_complete": work_counts is not None and work_counts.get("complete") is True,
        "agent_unclassified_open_count": work_counts.get("hidden") if work_counts is not None else None,
    })
    if terminal_state:
        projection["source_completeness"] = source_completeness
        projection["terminal_state"] = terminal_state
    return projection


def _collect_source_acceptance_gaps(
    *, goal_id: str, agent_id: str | None, goal_status: str | None, status_payload: Payload,
    project_asset: PayloadView | None, user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None, agent_todo_source_items: list[Payload] | None,
    agent_profile: PayloadView | None, latest_agent_vision: Payload | None,
) -> list[Payload]:
    latest_missing_checkpoint = latest_missing_vision_checkpoint_from_status_payload(status_payload, goal_id=goal_id, agent_id=agent_id)
    latest_vision_checkpoint = outcome_continuity.latest_outcome_vision_checkpoint_from_status_payload(status_payload, goal_id=goal_id, agent_id=agent_id)
    execution_profile = _opt_dict(_as_dict(project_asset).get("execution_profile"))
    completion_gaps = (
        [] if _as_dict(execution_profile).get("replan_after_effective_turns") is not None
        else outcome_continuity.acceptance_gaps_from_todo_completion_checkpoint(
            latest_agent_vision, latest_vision_checkpoint,
            agent_todo_summary=agent_todo_summary, agent_id=agent_id,
            completed_todo_threshold=completed_todo_replan_threshold(execution_profile),
        )
    )
    gaps = (
        acceptance_gaps_from_agent_profile_requirement(
            agent_profile, agent_id=agent_id, agent_vision=latest_agent_vision, missing_checkpoint=latest_missing_checkpoint,
        )
        + acceptance_gaps_from_agent_vision(latest_agent_vision, goal_status=goal_status)
        + outcome_continuity.acceptance_gaps_from_vision_checkpoint(latest_missing_checkpoint)
        + outcome_continuity.acceptance_gaps_from_outcome_checkpoint(latest_agent_vision, latest_vision_checkpoint)
        + completion_gaps
        + acceptance_gaps_from_outcomes(agent_todo_summary, agent_id=agent_id)
        + acceptance_gaps_from_held_goal_binding(agent_todo_summary, agent_todo_source_items, agent_id=agent_id)
    )
    if _terminal_no_followup_resolves_vision_checkpoint(
        user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary, checkpoint=latest_missing_checkpoint,
    ):
        return [gap for gap in gaps if gap.get("kind") != outcome_continuity.VISION_CHECKPOINT_MISSING_TRIGGER]
    return gaps


def _obligation_satisfied_by_ack(
    obligation: Payload | None, ack: Payload | None, *,
    acceptance_gaps: list[Payload], todo_succession_gap_open: bool, stride_strategy: str,
) -> bool:
    return bool(
        obligation
        and autonomous_replan_ack_satisfies_obligation(
            ack, replan_obligation=obligation, acceptance_gaps=acceptance_gaps,
            todo_succession_gap_open=todo_succession_gap_open, stride_strategy=stride_strategy,
        )
        and autonomous_replan_ack_matches_frontier(ack, obligation)
    )


def _collect_historical_ack_candidates(
    scoped_ack_history: object, latest_agent_replan_ack: Payload | None, receipt_bound_replan_obligation_id: str | None,
) -> list[Payload]:
    candidates: list[Payload] = [{"historical_ack": dict(c)} for c in _dict_items(scoped_ack_history)[:8]]
    if latest_agent_replan_ack is not None and receipt_bound_replan_obligation_id:
        candidates.append({"historical_ack": latest_agent_replan_ack})
    return candidates


def _reconstruct_receipt_bound_candidates(
    *, goal_id: str, agent_id: str | None, status_payload: Payload, user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None, work_lane_contract: PayloadView | None,
    agent_todo_source_items: list[Payload] | None, acceptance_gaps: list[Payload],
    frontier_counts: dict[str, int], stride_strategy: str, frontier_replan_obligation: Payload | None,
    frontier_transition_candidate: Payload, receipt_bound_replan_obligation_id: str | None,
    receipt_bound_guard_recorded_at: str | None,
) -> tuple[Payload, Payload | None]:
    if frontier_replan_obligation is None and receipt_bound_replan_obligation_id:
        # Timestamp order cannot identify a predecessor within one writer second.
        # Reuse the original builder and exact receipt digest to choose a source;
        # the live successor and shared settlement qualifier still govern effects.
        for source_ack_candidate in receipt_bound_replan_source_acks(
            status_payload, goal_id=goal_id, agent_id=agent_id, recorded_before=receipt_bound_guard_recorded_at,
        ):
            source = derive_goal_frontier_replan_obligation_from_summaries(
                user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary,
                work_lane_contract=work_lane_contract, agent_id=agent_id,
                existing_replan_obligation=None, agent_todo_source_items=agent_todo_source_items,
                latest_replan_ack=source_ack_candidate, acceptance_gaps=acceptance_gaps,
                vision_settlement_query=True, _frontier_counts=frontier_counts,
            )
            if _as_dict(source).get("obligation_id") == receipt_bound_replan_obligation_id:
                source_ack = replan_successor_transition_ack(
                    agent_todo_summary, agent_id=agent_id, replan_obligation=source,
                    agent_todo_items=agent_todo_source_items, stride_strategy=stride_strategy,
                )
                frontier_transition_candidate = {
                    "obligation": source, "ack": source_ack, "reconstructed_vision_review": True,
                }
                break

    lifecycle_candidate: Payload | None = None
    if receipt_bound_replan_obligation_id and receipt_bound_guard_recorded_at:
        from ...work_items.lifecycle_recovery import receipt_bound_lifecycle_candidate

        lifecycle_candidate = receipt_bound_lifecycle_candidate(
            selected_obligation_id=receipt_bound_replan_obligation_id,
            guard_recorded_at=receipt_bound_guard_recorded_at, agent_id=agent_id,
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
    return frontier_transition_candidate, lifecycle_candidate


def _resolve_frontier_obligation_ack(
    *, bounded_research: PayloadView, frontier_replan_obligation: Payload | None,
    agent_todo_summary: Payload | None, agent_id: str | None,
    agent_todo_source_items: list[Payload] | None, stride_strategy: str,
) -> tuple[list[Payload], Payload | None]:
    capability_transitions = _dict_items(bounded_research.get("settlement_transitions"))
    frontier_dict = _as_dict(frontier_replan_obligation)
    if frontier_dict.get("capability_guard"):
        frontier_id = frontier_dict.get("obligation_id")
        ack = next(
            (
                _opt_dict(c.get("ack")) for c in capability_transitions
                if _as_dict(c.get("obligation")).get("obligation_id") == frontier_id
            ),
            None,
        )
    else:
        ack = replan_successor_transition_ack(
            agent_todo_summary, agent_id=agent_id, replan_obligation=frontier_replan_obligation,
            agent_todo_items=agent_todo_source_items, stride_strategy=stride_strategy,
        )
    return capability_transitions, ack


def build_goal_frontier_projection_context_from_status(
    *,
    goal_id: str,
    agent_id: str | None,
    status_payload: Payload,
    item: PayloadView,
    project_asset: PayloadView | None,
    user_todo_summary: Payload | None,
    agent_todo_summary: Payload | None,
    work_lane_contract: PayloadView | None,
    neutral_replan_ack_classifications: set[str],
    agent_todo_source_items: list[Payload] | None = None,
    fallback_todo_source_items: FallbackTodoSource = None,
    available_capabilities: object = None,
    registered_agent_ids: list[str] | None = None,
    goal_status: str | None = None,
    agent_profile: PayloadView | None = None,
    receipt_bound_replan_obligation_id: str | None = None,
    receipt_bound_guard_recorded_at: str | None = None,
    replan_explore_reader: Callable[[str, str | None], PayloadView | None] | None = None,
) -> GoalFrontierProjectionContext:
    """Build the quota-facing goal-frontier read model.

    Quota decides delivery permission, but this helper owns the goal-frontier
    state reduction: existing obligation scope, latest replan ACK, open
    per-agent vision gaps, derived replan obligation, and final projection.
    """
    replan_obligation = select_autonomous_replan_obligation(item, project_asset, agent_id=agent_id)
    if replan_obligation:
        replan_obligation = ensure_replan_novelty_policy(replan_obligation)
    replan_scope = autonomous_replan_scope_decision(
        replan_obligation, agent_id=agent_id, registered_agent_ids=registered_agent_ids,
    )
    if replan_scope.get("required") and not replan_scope.get("applies"):
        replan_obligation = None

    latest_agent_replan_ack, scoped_ack_history, latest_replan_ack_feedback, prior_rearm_ack = (
        resolve_replan_ack_history_state(
            status_payload, goal_id=goal_id, agent_id=agent_id,
            neutral_classifications=neutral_replan_ack_classifications,
            receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
        )
    )
    latest_agent_vision = latest_agent_vision_from_status_payload(status_payload, goal_id=goal_id, agent_id=agent_id)
    source_acceptance_gaps = _collect_source_acceptance_gaps(
        goal_id=goal_id, agent_id=agent_id, goal_status=goal_status, status_payload=status_payload,
        project_asset=project_asset, user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary, agent_todo_source_items=agent_todo_source_items,
        agent_profile=agent_profile, latest_agent_vision=latest_agent_vision,
    )
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
            frontier_counts["current_agent_claimed_advancement_count"] + frontier_counts["unclaimed_advancement_count"]
        )
    vision_wait_state = build_goal_vision_wait_state(
        agent_todo_summary=agent_todo_summary, source_items=agent_todo_source_items, agent_id=agent_id,
        acceptance_gaps=source_acceptance_gaps, selectable_advancement_count=selectable_advancement_count,
    )
    acceptance_gaps = (
        [gap for gap in source_acceptance_gaps if gap.get("kind") in GOAL_ACCEPTANCE_HOLD_TRIGGERS]
        if vision_wait_state else source_acceptance_gaps
    )
    fallback_gap = declared_fallback_gap_from_agent_vision(
        latest_agent_vision, agent_todo_summary=agent_todo_summary, agent_id=agent_id,
        agent_todo_source_items=fallback_todo_source_items,
        rollout_events=_latest_runs_for_goal(status_payload, goal_id=goal_id),
        available_capabilities=available_capabilities,
    )
    declared_fallback_gaps = [fallback_gap] if isinstance(fallback_gap, dict) else []
    projected_replan_ack = projected_autonomous_replan_ack_for_agent(item, project_asset, agent_id=agent_id)
    runtime_root_raw = status_payload.get("runtime_root")
    effective_replan_ack = project_legacy_acceptance_hold_ack(
        latest_agent_replan_ack or projected_replan_ack, acceptance_gaps=source_acceptance_gaps,
        goal_id=goal_id, agent_id=agent_id,
        runtime_root=runtime_root_raw if isinstance(runtime_root_raw, str) else None,
    )
    stride_strategy = str(_as_dict(_as_dict(project_asset).get("execution_profile")).get("stride_strategy", "off"))
    replan_transition_ack = replan_successor_transition_ack(
        agent_todo_summary, agent_id=agent_id, replan_obligation=replan_obligation,
        agent_todo_items=agent_todo_source_items, stride_strategy=stride_strategy,
    )
    # Keep the validated history-obligation transition distinct from any
    # successor frontier obligation derived below. Exact Turn settlement may
    # still owe this receipt even when the next decision has another duty.
    run_replan_transition_ack = replan_transition_ack
    run_transition_candidate: Payload = {"obligation": replan_obligation, "ack": replan_transition_ack}
    if autonomous_replan_is_required(replan_obligation) and _obligation_satisfied_by_ack(
        replan_obligation, replan_transition_ack or effective_replan_ack,
        acceptance_gaps=source_acceptance_gaps, todo_succession_gap_open=todo_succession_gap_open,
        stride_strategy=stride_strategy,
    ):
        replan_obligation = None
        replan_scope = autonomous_replan_scope_decision(
            replan_obligation, agent_id=agent_id, registered_agent_ids=registered_agent_ids,
        )
    bounded_research = _as_dict(status_payload.get("bounded_research_frontier"))
    frontier_replan_obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary,
        work_lane_contract=work_lane_contract, agent_id=agent_id,
        existing_replan_obligation=replan_obligation, agent_todo_source_items=agent_todo_source_items,
        latest_replan_ack=effective_replan_ack, prior_rearm_ack=prior_rearm_ack,
        current_transition_replan_ack=replan_transition_ack, _frontier_counts=frontier_counts,
        acceptance_gaps=acceptance_gaps,
        monitor_lane_semantically_valid=not goal_vision_state_is_closed(_as_dict(latest_agent_vision).get("state")),
        capability_obligation=_opt_dict(bounded_research.get("obligation")),
    )
    capability_transitions, frontier_transition_ack = _resolve_frontier_obligation_ack(
        bounded_research=bounded_research, frontier_replan_obligation=frontier_replan_obligation,
        agent_todo_summary=agent_todo_summary, agent_id=agent_id,
        agent_todo_source_items=agent_todo_source_items, stride_strategy=stride_strategy,
    )
    frontier_transition_candidate: Payload = {
        "obligation": frontier_replan_obligation, "ack": frontier_transition_ack,
    }
    # A later durable Turn can have already acknowledged the exact review that
    # an older receipt still owes. Keep that run-derived ACK distinct from a
    # current-frontier transition: the settlement owner validates its exact
    # Goal, Agent, obligation, chronology, and Turn before it can close the old
    # writeback. Projected asset ACKs never enter this candidate.
    historical_ack_candidates = _collect_historical_ack_candidates(
        scoped_ack_history, latest_agent_replan_ack, receipt_bound_replan_obligation_id,
    )
    frontier_transition_candidate, lifecycle_candidate = _reconstruct_receipt_bound_candidates(
        goal_id=goal_id, agent_id=agent_id, status_payload=status_payload,
        user_todo_summary=user_todo_summary, agent_todo_summary=agent_todo_summary,
        work_lane_contract=work_lane_contract, agent_todo_source_items=agent_todo_source_items,
        acceptance_gaps=acceptance_gaps, frontier_counts=frontier_counts,
        stride_strategy=stride_strategy, frontier_replan_obligation=frontier_replan_obligation,
        frontier_transition_candidate=frontier_transition_candidate,
        receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
        receipt_bound_guard_recorded_at=receipt_bound_guard_recorded_at,
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
        replan_scope = autonomous_replan_scope_decision(
            replan_obligation, agent_id=agent_id, registered_agent_ids=registered_agent_ids,
        )

    replan_obligation = attach_rejected_replan_feedback(replan_obligation, latest_replan_ack_feedback)
    replan_obligation = attach_receipt_replan_context(
        replan_obligation, lifecycle_candidate, goal_id=goal_id, agent_id=agent_id,
        source_status=status_payload, stride_strategy=stride_strategy, explore_reader=replan_explore_reader,
        todo_items=agent_todo_source_items,
        goal_acceptance_contract=_opt_dict(_as_dict(agent_todo_summary).get("goal_acceptance_contract")),
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
    return GoalFrontierProjectionContext({
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
    })


def compact_replan_obligation(replan_obligation: PayloadView) -> Payload:
    compact: Payload = {
        "schema_version": replan_obligation.get("schema_version"),
        "obligation_id": replan_obligation.get("obligation_id"),
        "stall_threshold": replan_obligation.get("stall_threshold"),
        "trigger_count": replan_obligation.get("trigger_count"),
        "triggers": replan_obligation.get("triggers") or [],
        "stop_condition": replan_obligation.get("stop_condition"),
    }
    if replan_obligation.get("frontier_identity"):
        compact["frontier_identity"] = replan_obligation.get("frontier_identity")
    if isinstance(replan_obligation.get("replan_novelty_policy"), dict):
        # Keep hot quota/status packets on the two authoritative seams. The
        # detailed selection hint remains available on the full obligation.
        compact["replan_novelty_policy"] = {
            "evidence_source": "compact_run_history",
            "delivery": "host_projected",
            "writeback": "typed_semantic_delta",
        }
    if isinstance(replan_obligation.get("replan_ack_feedback"), dict):
        compact["replan_ack_feedback"] = replan_obligation["replan_ack_feedback"]
        compact["recommended_action"] = replan_obligation.get("recommended_action")
    return compact


def build_autonomous_replan_recommendation(
    replan_obligation: PayloadView, *, reason: str | None = None,
) -> Payload:
    return {
        "recommended_mode": AUTONOMOUS_REPLAN_REQUIRED_MODE,
        "notify": "NOTIFY",
        "replan_obligation": compact_replan_obligation(replan_obligation),
        "spend_policy": (
            "append exactly one heartbeat spend only after executing the selected "
            "replan slice, validating it, and writing back todo split/add/retire state"
        ),
        "reason": reason
        or (
            "status exposes an autonomous replan obligation; advance the goal-level "
            "planning-trigger slice before monitor-only or agent-scope wait "
            "classification; replan turns always notify because DONT_NOTIFY would "
            "mislead agents into a quiet no-op"
        ),
    }


def build_autonomous_replan_decision(replan_obligation: PayloadView) -> Payload:
    return {
        "schema_version": AUTONOMOUS_REPLAN_DECISION_SCHEMA_VERSION,
        "required": True,
        "decision": AUTONOMOUS_REPLAN_REQUIRED_MODE,
        "decision_plane": "goal_frontier_before_lane_quiet_or_agent_scope_wait",
        "not_disturbed_by": ["monitor_quiet_skip", "agent_scope_wait", "agent_scope_exhausted"],
        "trigger_count": safe_non_negative_int(replan_obligation.get("trigger_count")),
        "triggers": [
            kind for trigger in _dict_items(replan_obligation.get("triggers")) if (kind := trigger.get("kind"))
        ],
    }


def build_goal_frontier_projection(
    *,
    goal_id: str,
    agent_id: str | None,
    user_counts: Mapping[str, int],
    agent_counts: Mapping[str, int],
    current_agent_claimed_advancement_count: int,
    unclaimed_advancement_count: int,
    other_agent_claimed_advancement_count: int,
    monitor_only_lane: bool,
    replan_obligation: PayloadView | None,
    acceptance_gaps: Sequence[PayloadView] | None = None,
    deferred_successors: PayloadView | None = None,
    vision_wait_state: PayloadView | None = None,
    fallback_gaps: list[Payload] | None = None,
) -> Payload:
    replan_required = autonomous_replan_is_required(replan_obligation)
    blockers: list[str] = []
    if monitor_only_lane:
        blockers.append("monitor_only_lane")
    if (
        current_agent_claimed_advancement_count == 0
        and unclaimed_advancement_count == 0
        and other_agent_claimed_advancement_count > 0
    ):
        blockers.append("other_agent_claimed_advancement")
    if replan_required:
        blockers.append("autonomous_replan_obligation")
    if isinstance(vision_wait_state, dict):
        blockers.append(
            "current_agent_blocker"
            if vision_wait_state.get("reason_code") == "current_agent_blocker"
            else "vision_blocked_successor_wait"
        )

    compact_acceptance_gaps = _dict_items(acceptance_gaps)
    vision_continuation_audit = build_vision_continuation_audit(
        goal_id=goal_id, agent_id=agent_id, acceptance_gaps=compact_acceptance_gaps,
    )
    projection: Payload = {
        "schema_version": GOAL_FRONTIER_PROJECTION_SCHEMA_VERSION,
        "goal_id": goal_id, "agent_id": agent_id, "source": "quota_should_run",
        "normalized_progress": {
            "user_open_count": user_counts.get("open", 0),
            "agent_open_count": agent_counts.get("open", 0),
            "agent_advancement_open_count": agent_counts.get("advancement", 0),
            "agent_monitor_open_count": agent_counts.get("monitor", 0),
            "agent_monitor_due_count": agent_counts.get("monitor_due", 0),
            "agent_watch_only_monitor_due_count": agent_counts.get("watch_only_monitor_due", 0),
        },
        "remaining_advancement_frontier": {
            "current_agent_claimed_advancement_count": current_agent_claimed_advancement_count,
            "unclaimed_advancement_count": unclaimed_advancement_count,
            "other_agent_claimed_advancement_count": other_agent_claimed_advancement_count,
        },
        "monitor_only_lanes": {
            "present": monitor_only_lane,
            "quiet_until_material_transition": monitor_only_lane,
        },
        "deferred_successors": (
            deferred_successors if isinstance(deferred_successors, dict)
            else {"ready_count": 0, "blocked_count": 0, "current_agent_ready_count": 0, "ready_todo_ids": []}
        ),
        "acceptance_gaps": compact_acceptance_gaps[:5],
        "autonomy_blockers": blockers,
        "replan_required": replan_required,
    }
    if vision_continuation_audit:
        projection["vision_continuation_audit"] = vision_continuation_audit
    # Advisory-only field: unlike acceptance_gaps it is never cleared by the
    # blocked-successor wait state, which is exactly when a declared fallback
    # would otherwise disappear silently.
    if fallback_gaps:
        projection["fallback_gaps"] = fallback_gaps[:1]
    if isinstance(vision_wait_state, dict):
        projection["vision_wait_state"] = vision_wait_state
    if replan_required and isinstance(replan_obligation, dict):
        projection["autonomous_replan_decision"] = build_autonomous_replan_decision(replan_obligation)
    return projection
