from __future__ import annotations

from typing import Any

from ...agents.agent_scope import (
    agent_scope_blocking_handoff_gates,
    agent_scope_count_advancement_items,
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
    normalize_todo_replan_obligation_id,
)
from ...todos.todo_semantics import (
    todo_advancement_frontier_counts,
    todo_item_is_watch_only_monitor,
)
from ...todos.succession_warning import (
    TODO_SUCCESSION_WARNING_REASON_CODE,
    todo_succession_gap_items,
)
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
from ...work_items.progress_observation import build_replan_context
from ..goal_vision_policy import (
    completed_todo_replan_threshold,
    goal_vision_repeats_advancement_until_closed,
)
from ..goal_vision_state import (
    goal_vision_state_is_closed,
)
from ..goal_vision_read_model import (
    VISION_ACCEPTANCE_GAP_TRIGGER as VISION_ACCEPTANCE_GAP_TRIGGER,
    VISION_SUCCESSOR_GAP_TRIGGER as VISION_SUCCESSOR_GAP_TRIGGER,
    _compact_projection_text,
    acceptance_gaps_from_agent_vision as acceptance_gaps_from_agent_vision,
    parse_vision_todo_delta_entries as parse_vision_todo_delta_entries,
)
from ..goal_vision_wait import build_goal_vision_wait_state
from . import outcome_continuity
from .ack_policy import (
    autonomous_replan_ack_satisfies_obligation,
    replan_successor_transition_ack,
)
from .fallback_disposition import (
    VISION_FRONTIER_TODO_DELTA_ACTIONS,  # noqa: F401
    FallbackDeclaration,  # noqa: F401
    agent_scoped_selectable_advancement_todo_ids,  # noqa: F401
    declared_fallback_gap_from_agent_vision,
    parse_fallback_declarations,  # noqa: F401
)
from .long_todo_chain import (
    LONG_TODO_CHAIN_TRIGGER,
    evaluate_long_todo_chain,
)
from .replan_rules import (
    GoalFrontierReplanFacts,
    GoalFrontierReplanRule,
    select_goal_frontier_replan_rule,
)
from .semantic_history import (
    latest_agent_vision_from_runs as latest_agent_vision_from_runs,
)
from .semantic_history import (
    latest_agent_vision_from_status_payload,
    latest_autonomous_replan_ack_from_status_payload,
    latest_missing_vision_checkpoint_from_status_payload,
    latest_replan_ack_feedback_from_status_payload,
    latest_runs_for_goal,
)
from .terminal import (
    GOAL_TERMINAL_SOURCE_COMPLETENESS_SCHEMA_VERSION,  # noqa: F401
    GOAL_TERMINAL_STATE_SCHEMA_VERSION,  # noqa: F401
    VISION_CHECKPOINT_NO_FOLLOWUP_RESOLUTION,  # noqa: F401
    _terminal_no_followup_resolves_vision_checkpoint,
    derive_goal_terminal_state,
    goal_frontier_is_terminal_no_followup,  # noqa: F401
)

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


def safe_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def select_autonomous_replan_obligation(
    item: dict[str, Any],
    project_asset: dict[str, Any] | None = None,
    *,
    agent_id: str | None = None,
) -> dict[str, Any] | None:
    project_asset = project_asset if isinstance(project_asset, dict) else {}
    safe_agent_id = str(agent_id or "").strip()
    if safe_agent_id:
        for source in (item, project_asset):
            obligations = source.get("autonomous_replan_obligations_by_agent")
            if not isinstance(obligations, dict):
                continue
            value = obligations.get(safe_agent_id)
            if isinstance(value, dict):
                return value
    value = item.get("autonomous_replan_obligation")
    if isinstance(value, dict):
        return value
    value = project_asset.get("autonomous_replan_obligation")
    if isinstance(value, dict):
        return value
    return None


def autonomous_replan_is_required(replan_obligation: dict[str, Any] | None) -> bool:
    return bool(replan_obligation and replan_obligation.get("required"))


def align_autonomous_replan_guidance_with_acceptance_policy(
    replan_obligation: dict[str, Any] | None,
    *,
    acceptance_gaps: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Remove wait-only guidance when the active vision requires advancement."""

    if not isinstance(replan_obligation, dict):
        return replan_obligation
    trigger_kinds = {
        str(trigger.get("kind") or "").strip()
        for trigger in (replan_obligation.get("triggers") or [])
        if isinstance(trigger, dict)
    }
    if "blocked_successor_no_progress_repeat" not in trigger_kinds or not any(
        goal_vision_repeats_advancement_until_closed(gap.get("advancement_policy"))
        for gap in (acceptance_gaps or [])
        if isinstance(gap, dict)
    ):
        return replan_obligation

    aligned = dict(replan_obligation)
    aligned["guidance_actions"] = [
        "discover_safe_successor",
        "create_runnable_todo",
        "successor_or_supersede",
    ]
    aligned["todo_actions"] = [
        {
            **action,
            "text": (
                "discover and promote one safe in-scope evidence-backed runnable "
                "todo, or replace the blocked successor through an explicit "
                "successor/supersede transition"
            ),
        }
        if isinstance(action, dict)
        and action.get("action") == "add"
        and action.get("role") == "agent"
        else action
        for action in (replan_obligation.get("todo_actions") or [])
    ]
    aligned["recommended_action"] = with_replan_novelty_guidance(
        "run a bounded autonomous replan for the exact blocked successor: "
        "create or claim one safe in-scope runnable advancement todo, or record "
        "an explicit successor/supersede transition; a maintenance-only "
        "continuation does not satisfy a repeat-until-closed vision"
    )
    aligned["satisfying_semantic_outcomes"] = [
        "fresh_vision_path_outcome",
        "new_runnable_successor",
        "new_concrete_blocker",
        "coverage_backed_exploration_exhausted",
        "coverage_backed_no_followup",
    ]
    return aligned


def autonomous_replan_decision_allowed(
    *,
    replan_obligation: dict[str, Any] | None,
    plan_ok: bool,
    workspace_blocked: bool,
    automation_prompt_upgrade_required: bool,
    agent_id: str | None = None,
    registered_agent_ids: list[str] | None = None,
) -> bool:
    return bool(
        autonomous_replan_is_required(replan_obligation)
        and autonomous_replan_scope_decision(
            replan_obligation,
            agent_id=agent_id,
            registered_agent_ids=registered_agent_ids,
        ).get("applies")
        and plan_ok
        and not workspace_blocked
        and not automation_prompt_upgrade_required
    )


def _normalize_replan_agent_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _autonomous_replan_owner_agent_ids(
    replan_obligation: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(replan_obligation, dict):
        return []
    owner_keys = (
        "agent_id",
        "claimed_by",
        "bound_agent",
        "owner_agent",
        "target_agent",
        "blocks_agent",
    )
    owners: list[str] = []

    def append_owner(value: Any) -> None:
        owner = _normalize_replan_agent_id(value)
        if owner and owner not in owners:
            owners.append(owner)

    for key in owner_keys:
        append_owner(replan_obligation.get(key))
    triggers = (
        replan_obligation.get("triggers")
        if isinstance(replan_obligation.get("triggers"), list)
        else []
    )
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        for key in owner_keys:
            append_owner(trigger.get(key))
    return owners


def autonomous_replan_scope_decision(
    replan_obligation: dict[str, Any] | None,
    *,
    agent_id: str | None,
    registered_agent_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return whether a replan obligation belongs to this agent lane.

    Explicit agent-owned replans are consumed only by that agent. Unscoped
    goal-level replans are deterministically assigned to one registered peer.
    """

    normalized_agent_id = _normalize_replan_agent_id(agent_id)
    owners = _autonomous_replan_owner_agent_ids(replan_obligation)
    required = autonomous_replan_is_required(replan_obligation)
    selected_peer_agent = None
    if not required:
        applies = False
        scope = "not_required"
    elif not normalized_agent_id:
        applies = True
        scope = "unscoped_quota_call"
    elif owners:
        applies = normalized_agent_id in owners
        scope = "explicit_agent_owner"
    else:
        selected_peer_agent = select_peer_for_work(
            registered_agent_ids or [],
            work_key=peer_work_key(
                replan_obligation,
                fallback="autonomous_replan",
            ),
        )
        applies = bool(selected_peer_agent and normalized_agent_id == selected_peer_agent)
        scope = "deterministic_peer_assignment"
    payload = {
        "schema_version": AUTONOMOUS_REPLAN_SCOPE_SCHEMA_VERSION,
        "required": required,
        "applies": applies,
        "scope": scope,
        "agent_id": normalized_agent_id,
        "agent_model": "peer_v1",
        "owner_agent_ids": owners,
        "selected_peer_agent": selected_peer_agent,
    }
    return payload


def projected_autonomous_replan_ack_for_agent(
    item: dict[str, Any],
    project_asset: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Return the current projected replan ACK when it belongs to this agent."""

    project_asset = project_asset if isinstance(project_asset, dict) else {}
    for candidate in (
        item.get("autonomous_replan_ack"),
        project_asset.get("autonomous_replan_ack"),
    ):
        normalized = normalize_projected_autonomous_replan_ack(candidate)
        if autonomous_replan_ack_matches_agent(normalized, agent_id=agent_id):
            return normalized
    return None


def acceptance_gaps_from_agent_profile_requirement(
    agent_profile: dict[str, Any] | None,
    *,
    agent_id: str | None,
    agent_vision: dict[str, Any] | None,
    missing_checkpoint: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Require a vision baseline for registered long-lived peer lanes."""

    if (
        not agent_id
        or not agent_profile_requires_vision(agent_profile)
        or isinstance(agent_vision, dict)
        or isinstance(missing_checkpoint, dict)
    ):
        return []
    return [
        {
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
        }
    ]


def build_vision_continuation_audit(
    *,
    goal_id: str | None = None,
    agent_id: str | None,
    acceptance_gaps: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Return the closeout audit contract for an open per-agent vision gap.

    This is a read-path contract: quota/status can tell an agent that the
    selected todo is only a step toward the active vision. Writeback still goes
    through normal todo, evidence, and refresh-state commands.
    """

    compact_acceptance_gaps = [
        gap for gap in (acceptance_gaps or []) if isinstance(gap, dict)
    ]
    if not compact_acceptance_gaps:
        return None
    vision_gap_judge = build_vision_gap_judge(
        goal_id=goal_id,
        agent_id=agent_id,
        acceptance_gaps=compact_acceptance_gaps,
    )
    acceptance_requirements = [
        text
        for text in (
            _compact_projection_text(gap.get("acceptance_summary"), limit=180)
            for gap in compact_acceptance_gaps
        )
        if text
    ]
    trigger_kinds = [
        kind
        for kind in (
            _compact_projection_text(gap.get("kind"), limit=80)
            for gap in compact_acceptance_gaps
        )
        if kind
    ]
    audit: dict[str, Any] = {
        "schema_version": VISION_CONTINUATION_AUDIT_SCHEMA_VERSION,
        "required": True,
        "agent_id": agent_id,
        "decision": "acceptance_gap_open",
        "selected_todo_is_goal_completion": False,
        "closeout_allowed_without_evidence": False,
        "trigger_count": len(compact_acceptance_gaps),
        "trigger_kinds": list(dict.fromkeys(trigger_kinds))[:5],
        "acceptance_gaps": compact_acceptance_gaps[:5],
        "vision_gap_judge": vision_gap_judge,
        "authoritative_evidence_kinds": [
            "changed_files",
            "public_safe_evidence_records",
            "public_web_research_findings",
            "evaluation_outputs",
            "successor_state",
            "blocker_state",
            "superseding_agent_vision",
        ],
        "not_satisfied_by": [
            "todo_completion_alone",
            "autonomous_replan_ack_alone",
            "vision_checkpoint_alone",
            "registry_registration_alone",
            "no_followup_without_acceptance_evidence",
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
        for gap in compact_acceptance_gaps
        if gap.get("reason_code") and gap.get("component_checks") and gap.get("resolution_hint")
    ]
    if diagnostics:
        audit["outcome_checkpoint_diagnostics"] = diagnostics[:5]
    return audit


def build_vision_gap_judge(
    *,
    goal_id: str | None = None,
    agent_id: str | None,
    acceptance_gaps: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Return the strict done/continue judge for active per-agent vision gaps.

    The read model keeps the prompt essence compact: unless current evidence
    satisfies, blocks, or supersedes the active vision, the agent should judge
    the vision as still CONTINUE.
    """

    compact_acceptance_gaps = [
        gap for gap in (acceptance_gaps or []) if isinstance(gap, dict)
    ]
    first_gap = compact_acceptance_gaps[0] if compact_acceptance_gaps else {}
    reason = _compact_projection_text(
        first_gap.get("replan_trigger_summary")
        or "active per-agent vision still has an open acceptance gap",
        limit=220,
    )
    evidence_read_instruction = (
        "Use the host-projected compact coverage ledger and uncovered frontier. "
        "The agent-scoped evidence log remains the durable source and an optional "
        "diagnostic readback, not a model-executed closure gate."
    )
    registry_read_instruction = (
        "Before external research, inspect the selected goal's registry-declared "
        "topic_authority and project_materials metadata, preferring any projected "
        "replan_context or agent_material_frontier. Use role, freshness, revision, "
        "boundary, gate_status, and conflict_rule to choose permitted references. "
        "Registration guides discovery; it neither grants access nor proves acceptance."
    )
    public_research_instruction = (
        "If projected evidence, the evidence log, and permitted registry-declared "
        "references remain missing, stale, or too weak and the acceptance question "
        "depends on public facts, run bounded public web research using primary or "
        "authoritative sources; record confirmed/refuted findings as public-safe "
        "evidence or a compact vision replan trigger before judging."
    )
    return {
        "schema_version": VISION_GAP_JUDGE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "done": False,
        "decision": "continue",
        "reason": (
            reason
            or "active per-agent vision still has an open acceptance gap"
        ),
        "agent_judge_instruction": (
            "Judge vision closure: compare active vision acceptance_summary "
            "with the host-projected agent-scoped coverage ledger. "
            "Inspect permitted registry-declared material references before bounded "
            "public web research. "
            "Mark done only when evidence proves completion, a blocker/user "
            "gate, or superseding/no-follow-up closure; otherwise continue."
        ),
        "evidence_read_instruction": evidence_read_instruction,
        "registry_read_instruction": registry_read_instruction,
        "external_research_instruction": public_research_instruction,
        "research_writeback_required_when_used": [
            "source_url_or_public_reference",
            "confirmed_or_refuted_finding",
            "supports_or_refutes_acceptance_gap",
            "successor_todo_or_vision_replan_trigger",
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


def _open_todo_count(summary: dict[str, Any] | None) -> int:
    if not isinstance(summary, dict):
        return 0
    return safe_non_negative_int(summary.get("open_count"))


def _blocking_user_open_count(summary: dict[str, Any] | None) -> int:
    if not isinstance(summary, dict):
        return 0
    gate_items = summary.get("gate_open_items")
    if isinstance(gate_items, list):
        return sum(
            1
            for item in gate_items
            if isinstance(item, dict) and _todo_item_is_actionable_open(item)
        )
    # Legacy or partial summaries without typed gate projection fail closed.
    return _open_todo_count(summary)


def _todo_item_is_actionable_open(item: dict[str, Any]) -> bool:
    if item.get("done") is True:
        return False
    status = str(item.get("status") or "open").strip().lower()
    return status in {"", "open", "todo", "active", "pending"}


def _todo_task_class(item: dict[str, Any]) -> str:
    return str(item.get("task_class") or "").strip()


def _count_advancement_items(items: Any, *, claimed_by: str | None = None) -> int:
    return agent_scope_count_advancement_items(items, claimed_by=claimed_by)


def _summary_task_counts(summary: dict[str, Any] | None) -> dict[str, int]:
    open_count = _open_todo_count(summary)
    if not isinstance(summary, dict):
        return {
            "open": open_count,
            "advancement": 0,
            "monitor": 0,
            "monitor_due": 0,
            "watch_only_monitor_due": 0,
        }
    executable = summary.get("executable_backlog_items")
    monitor_open = summary.get("monitor_open_items")
    if isinstance(summary.get("watch_only_monitor_count"), int):
        watch_only_count = safe_non_negative_int(
            summary.get("watch_only_monitor_count")
        )
    elif isinstance(monitor_open, list):
        watch_only_count = len(
            [
                item
                for item in monitor_open
                if isinstance(item, dict)
                and _todo_item_is_actionable_open(item)
                and todo_item_is_watch_only_monitor(item)
            ]
        )
    else:
        watch_only_count = 0
    if isinstance(summary.get("convergence_open_count"), int):
        open_count = safe_non_negative_int(summary.get("convergence_open_count"))
    else:
        # Compatibility-only fallback for summaries produced before the typed
        # TypeScript lane owner exposed convergence_open_count.
        open_count = max(0, open_count - watch_only_count)
    advancement_count = (
        _count_advancement_items(executable)
        if isinstance(executable, list)
        else safe_non_negative_int(summary.get("claimed_advancement_open_count"))
        + len(
            [
                item
                for item in (summary.get("unclaimed_priority_open_items") or [])
                if isinstance(item, dict)
                and _todo_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
            ]
        )
    )
    work_counts = summary.get("work_counts")
    if isinstance(work_counts, dict):
        monitor_count = max(
            0,
            safe_non_negative_int(work_counts.get("monitor"))
            - watch_only_count,
        )
    elif isinstance(monitor_open, list):
        monitor_count = len(
            [
                item
                for item in monitor_open
                if isinstance(item, dict)
                and _todo_item_is_actionable_open(item)
                and _todo_task_class(item) == TODO_TASK_CLASS_MONITOR
                and not todo_item_is_watch_only_monitor(item)
            ]
        )
    else:
        monitor_count = safe_non_negative_int(
            summary.get("claimed_monitor_open_count")
        )
    return {
        "open": open_count,
        "advancement": advancement_count,
        "monitor": monitor_count,
        "monitor_due": max(
            0,
            safe_non_negative_int(summary.get("monitor_due_count"))
            - safe_non_negative_int(summary.get("watch_only_monitor_due_count")),
        ),
        "watch_only_monitor_due": safe_non_negative_int(
            summary.get("watch_only_monitor_due_count")
        ),
    }


def _monitor_no_change_streak_trigger(
    agent_todo_summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> dict[str, Any] | None:
    if not isinstance(agent_todo_summary, dict):
        return None
    monitor_items = agent_todo_summary.get("monitor_open_items")
    if not isinstance(monitor_items, list):
        return None

    stalled: list[tuple[int, str, dict[str, Any]]] = []
    for item in monitor_items:
        if not isinstance(item, dict):
            continue
        if not _todo_item_is_actionable_open(item):
            continue
        if _todo_task_class(item) != TODO_TASK_CLASS_MONITOR:
            continue
        if todo_item_is_watch_only_monitor(item):
            continue
        claimed_by = agent_scope_item_claimed_by(item)
        if agent_id and claimed_by != agent_id:
            continue
        no_change_count = safe_non_negative_int(item.get("consecutive_no_change"))
        if no_change_count < MONITOR_NO_CHANGE_STREAK_THRESHOLD:
            continue
        target_key = str(
            item.get("target_key") or item.get("todo_id") or "monitor"
        ).strip()
        stalled.append((no_change_count, target_key, item))
    if not stalled:
        return None

    no_change_count, target_key, monitor = min(
        stalled,
        key=lambda entry: (-entry[0], entry[1]),
    )
    return {
        "kind": MONITOR_NO_CHANGE_STREAK_TRIGGER,
        "section": "agent_todo_summary.monitor_open_items",
        "text": (
            f"monitor {target_key} recorded {no_change_count} consecutive "
            "unchanged polls without selectable advancement"
        ),
        "todo_id": monitor.get("todo_id"),
        "monitor_target_id": target_key,
        "run_count": no_change_count,
        "threshold": MONITOR_NO_CHANGE_STREAK_THRESHOLD,
        "agent_id": agent_id,
    }


def _frontier_advancement_counts(
    *,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
) -> dict[str, int]:
    return todo_advancement_frontier_counts(
        agent_todo_summary,
        agent_id=agent_id,
    )


def _compact_todo_id(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    todo_id = str(item.get("todo_id") or "").strip()
    return todo_id or None


def _deferred_successors(
    summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {
            "ready_count": 0,
            "blocked_count": 0,
            "current_agent_ready_count": 0,
            "ready_todo_ids": [],
        }

    ready_items = [
        item
        for item in (summary.get("deferred_resume_candidates") or [])
        if isinstance(item, dict)
    ]
    deferred_items = [
        item for item in (summary.get("deferred_items") or []) if isinstance(item, dict)
    ]
    deferred_count = max(
        safe_non_negative_int(summary.get("deferred_count")),
        len(deferred_items),
        len(ready_items),
    )
    current_agent_ready_items = [
        item
        for item in ready_items
        if agent_id and agent_scope_item_claimed_by(item) == agent_id
    ]
    ready_todo_ids = [
        todo_id for todo_id in (_compact_todo_id(item) for item in ready_items[:5]) if todo_id
    ]
    projection = {
        "ready_count": len(ready_items),
        "blocked_count": max(0, deferred_count - len(ready_items)),
        "current_agent_ready_count": len(current_agent_ready_items),
        "ready_todo_ids": ready_todo_ids,
    }
    if ready_todo_ids:
        projection["top_ready_todo_id"] = ready_todo_ids[0]
    return projection


def _is_monitor_only_lane(
    work_lane_contract: dict[str, Any] | None,
) -> bool:
    return bool(
        _is_continuous_monitor_lane(work_lane_contract)
        and (
            work_lane_contract.get("must_attempt_work") is False
            or work_lane_contract.get("monitor_kind") == "todo_monitor_due"
        )
    )


def _is_continuous_monitor_lane(
    work_lane_contract: dict[str, Any] | None,
) -> bool:
    return bool(
        work_lane_contract
        and work_lane_contract.get("lane") == TODO_TASK_CLASS_MONITOR
    )


def _monitor_only_lane_has_future_schedule(
    agent_todo_summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(agent_todo_summary, dict):
        return False
    if "monitor_due_count" not in agent_todo_summary:
        return False
    if "monitor_schedule_gap_count" not in agent_todo_summary:
        return False
    if safe_non_negative_int(agent_todo_summary.get("monitor_due_count")) > 0:
        return False
    if safe_non_negative_int(agent_todo_summary.get("monitor_schedule_gap_count")) > 0:
        return False
    monitor_items = agent_todo_summary.get("monitor_open_items")
    return isinstance(monitor_items, list) and len(monitor_items) > 0


def _blocking_handoff_gate_count(
    agent_todo_summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> int:
    if not agent_id or not isinstance(agent_todo_summary, dict):
        return 0
    return len(agent_scope_blocking_handoff_gates(agent_todo_summary, agent_id=agent_id))


def _ready_deferred_successor_count(
    agent_todo_summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> int:
    if not isinstance(agent_todo_summary, dict):
        return 0
    current_count = safe_non_negative_int(
        agent_todo_summary.get("current_agent_deferred_resume_count")
    )
    unclaimed_count = safe_non_negative_int(
        agent_todo_summary.get("unclaimed_deferred_resume_count")
    )
    if current_count or unclaimed_count:
        return current_count + unclaimed_count
    candidates = (
        agent_todo_summary.get("deferred_resume_candidates")
        if isinstance(agent_todo_summary.get("deferred_resume_candidates"), list)
        else []
    )
    return len(
        [
            item
            for item in candidates
            if isinstance(item, dict)
            and agent_scope_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)
        ]
    )


def _replan_evidence_acknowledged(
    items: list[dict[str, Any]],
    latest_replan_ack: dict[str, Any] | None,
    *,
    time_key: str,
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
        (
            timestamp
            for item in items
            if isinstance(item, dict)
            if (timestamp := parse_timestamp(item.get(time_key))) is not None
        ),
        default=None,
    )
    if newest is None:
        return False
    return ack_time >= newest


def _succession_gap_acknowledged(
    gap_items: list[dict[str, Any]],
    latest_replan_ack: dict[str, Any] | None,
) -> bool:
    """Return true when a valid replan ack postdates the newest succession gap."""

    semantic_delta = (
        latest_replan_ack.get("semantic_delta")
        if isinstance(latest_replan_ack, dict)
        and isinstance(latest_replan_ack.get("semantic_delta"), dict)
        else {}
    )
    if "new_runnable_successor" not in set(semantic_delta.get("outcomes") or []):
        return False
    return _replan_evidence_acknowledged(
        gap_items,
        latest_replan_ack,
        time_key="completed_at",
    )


def _vision_gap_acknowledged(
    acceptance_gaps: list[dict[str, Any]],
    latest_replan_ack: dict[str, Any] | None,
) -> bool:
    """Return true when a valid replan ack covers the newest vision gap.

    A goal_vision_patch ack settles vision successor/checkpoint gaps even when
    the ack turn's own vision writebacks carry slightly newer timestamps;
    without this, an agent's acknowledgement turn regenerates the gap and quota
    stays in a permanent run-now loop (observed as alternating
    autonomous_replan_recorded / quota_slot_spent runs with no advancement).
    """

    if not acceptance_gaps or not isinstance(latest_replan_ack, dict):
        return False
    delta_contract = latest_replan_ack.get("delta_contract")
    delta_kinds = (
        delta_contract.get("delta_kinds")
        if isinstance(delta_contract, dict)
        else None
    )
    if isinstance(delta_kinds, list) and any(
        str(kind) in {"goal_vision_patch", "goal_vision_replan_trigger"}
        for kind in delta_kinds
    ):
        return True
    return _replan_evidence_acknowledged(
        acceptance_gaps,
        latest_replan_ack,
        time_key="generated_at",
    )


def _acknowledged_replan_obligation_id(
    latest_replan_ack: dict[str, Any] | None,
) -> str | None:
    """Return the exact obligation generation closed by the latest ACK."""

    semantic_delta = (
        latest_replan_ack.get("semantic_delta")
        if isinstance(latest_replan_ack, dict)
        and isinstance(latest_replan_ack.get("semantic_delta"), dict)
        else {}
    )
    return normalize_todo_replan_obligation_id(
        semantic_delta.get("obligation_id")
    )


def derive_goal_frontier_replan_obligation_from_summaries(
    *,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
    agent_id: str | None,
    existing_replan_obligation: dict[str, Any] | None,
    agent_todo_source_items: list[dict[str, Any]] | None = None,
    latest_replan_ack: dict[str, Any] | None = None,
    current_transition_replan_ack: dict[str, Any] | None = None,
    acceptance_gaps: list[dict[str, Any]] | None = None,
    monitor_lane_semantically_valid: bool = True,
) -> dict[str, Any] | None:
    """Return a compact replan obligation when the goal frontier has no advancement.

    This keeps the per-goal completion/replan rule in the goal-frontier policy
    seam. Quota should consume the resulting obligation instead of embedding
    monitor/vision semantics in its scheduler path.
    """

    agent_counts = _summary_task_counts(agent_todo_summary)
    frontier_counts = _frontier_advancement_counts(
        agent_todo_summary=agent_todo_summary,
        agent_id=agent_id,
    )
    total_frontier_advancement = sum(frontier_counts.values())
    selectable_frontier_advancement = (
        frontier_counts["current_agent_claimed_advancement_count"]
        + frontier_counts["unclaimed_advancement_count"]
    )
    monitor_no_change_trigger = (
        _monitor_no_change_streak_trigger(
            agent_todo_summary,
            agent_id=agent_id,
        )
        if selectable_frontier_advancement == 0
        else None
    )
    compact_acceptance_gaps = [
        item for item in (acceptance_gaps or []) if isinstance(item, dict)
    ]
    if any(gap.get("vision_todo_ids") for gap in compact_acceptance_gaps):
        # Diagnostic claim counts retain executor-excluded work. A causal
        # acceptance obligation needs an actually selectable Todo identity.
        selectable_frontier_advancement = len(
            agent_scoped_selectable_advancement_todo_ids(
                agent_todo_summary, agent_id=agent_id,
            )
        )
    successor_vision_required = any(
        item.get("kind")
        in {VISION_SUCCESSOR_GAP_TRIGGER, VISION_PROFILE_MISSING_TRIGGER}
        for item in compact_acceptance_gaps
    )
    outcome_checkpoint_replan_required = any(
        item.get("kind")
        == outcome_continuity.VISION_OUTCOME_CHECKPOINT_REQUIRED_TRIGGER
        for item in compact_acceptance_gaps
    )
    succession_gap_items = todo_succession_gap_items(
        agent_todo_summary,
        agent_id=agent_id,
    )
    long_chain_observation, long_chain_ack_decision = evaluate_long_todo_chain(
        agent_todo_summary=agent_todo_summary,
        agent_counts=agent_counts,
        frontier_counts=frontier_counts,
        agent_id=agent_id,
        agent_todo_source_items=agent_todo_source_items,
        latest_replan_ack=current_transition_replan_ack or latest_replan_ack,
    )
    replan_rule = select_goal_frontier_replan_rule(
        GoalFrontierReplanFacts(
            existing_replan_required=autonomous_replan_is_required(
                existing_replan_obligation
            ),
            blocking_handoff_gate_count=_blocking_handoff_gate_count(
                agent_todo_summary,
                agent_id=agent_id,
            ),
            ready_deferred_successor_count=_ready_deferred_successor_count(
                agent_todo_summary,
                agent_id=agent_id,
            ),
            successor_vision_required=successor_vision_required,
            blocking_user_open_count=_blocking_user_open_count(user_todo_summary),
            user_open_count=_open_todo_count(user_todo_summary),
            succession_gap_count=len(succession_gap_items),
            succession_gap_acknowledged=_succession_gap_acknowledged(
                succession_gap_items,
                latest_replan_ack,
            ),
            vision_gap_acknowledged=_vision_gap_acknowledged(
                compact_acceptance_gaps,
                latest_replan_ack,
            ),
            agent_advancement_count=agent_counts.get("advancement", 0),
            total_frontier_advancement=total_frontier_advancement,
            acceptance_gap_count=len(compact_acceptance_gaps),
            selectable_frontier_advancement=selectable_frontier_advancement,
            outcome_checkpoint_replan_required=(
                outcome_checkpoint_replan_required
            ),
            long_todo_chain_triggered=(
                long_chain_observation is not None
                and long_chain_ack_decision is not None
                and not long_chain_ack_decision.acknowledged
            ),
            current_agent_blocker_count=safe_non_negative_int(
                (agent_todo_summary or {}).get("current_agent_blocker_count")
            ),
            monitor_no_change_streak_triggered=(
                monitor_no_change_trigger is not None
            ),
            monitor_only_lane=_is_monitor_only_lane(work_lane_contract),
            monitor_count=agent_counts.get("monitor", 0),
            monitor_due_count=safe_non_negative_int(
                (agent_todo_summary or {}).get("monitor_due_count")
            ),
            monitor_schedule_gap_count=safe_non_negative_int(
                (agent_todo_summary or {}).get("monitor_schedule_gap_count")
            ),
            future_monitor_schedule_present=(
                _monitor_only_lane_has_future_schedule(agent_todo_summary)
            ),
            monitor_lane_semantically_valid=monitor_lane_semantically_valid,
        )
    )
    if not replan_rule.derives_obligation:
        return None
    if replan_rule.rule is GoalFrontierReplanRule.TODO_SUCCESSION_GAP:
        settlement_items = succession_gap_items[:3]
        settlement_todo_ids = [
            str(item.get("todo_id") or "").strip()
            for item in settlement_items
            if str(item.get("todo_id") or "").strip()
        ]
        settlement_summary = ", ".join(settlement_todo_ids)
        triggers = [
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
        ]
        return build_autonomous_replan_obligation_payload(
            schema_version=AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
            agent_id=agent_id,
            include_agent_id=True,
            stall_threshold=1,
            trigger_count=len(succession_gap_items),
            triggers=triggers,
            guidance_actions=[
                "record_no_followup",
                "link_successor",
                "create_successor",
            ],
            todo_actions=[
                {
                    "action": "settle",
                    "role": "agent",
                    "priority": "P0",
                    "todo_ids": settlement_todo_ids,
                    "text": (
                        "settle the exact completed Todos through no-follow-up or "
                        "link each to a real runnable successor"
                    ),
                },
                {
                    "action": "add",
                    "role": "agent",
                    "priority": "P0",
                    "text": (
                        "create a typed runnable successor only when concrete "
                        "technical or validation work remains"
                    ),
                },
            ],
            stop_condition=(
                "stop if the successor decision requires private material, "
                "credentials, destructive git, production actions, or owner-only decisions"
            ),
            recommended_action=(
                "settle the exact completed Todo(s) "
                f"{settlement_summary} through loopx todo complete --no-follow-up "
                "when no real work remains; otherwise link a real runnable successor. "
                "do not invent a user gate or add a lifecycle-only filler Todo"
            ),
            extra_fields={
                "resolution_mode": TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE,
                "satisfying_semantic_outcomes": ["new_runnable_successor"],
            },
        )
    if replan_rule.rule is GoalFrontierReplanRule.VISION_ACCEPTANCE_GAP:
        rearmed_after_obligation_id = _acknowledged_replan_obligation_id(
            latest_replan_ack
        )
        return build_autonomous_replan_obligation_payload(
            schema_version=AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
            agent_id=agent_id,
            include_agent_id=True,
            stall_threshold=1,
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
                    **{
                        key: gap.get(key)
                        for key in (
                            "generated_at",
                            "completed_todo_id",
                            "completed_todo_count",
                            "completed_todo_threshold",
                            "completed_todo_ids",
                            "reason_code",
                            "component_checks",
                            "resolution_hint",
                        )
                        if gap.get(key) is not None
                    },
                }
                for gap in compact_acceptance_gaps[:3]
            ],
            guidance_actions=[
                "create_successor",
                "update_agent_vision",
                "record_evidence_gap",
                "record_no_followup",
            ],
            todo_actions=[
                {
                    "action": "add",
                    "role": "agent",
                    "priority": "P0",
                    "text": (
                        "run a bounded vision-gap replan: create the next runnable "
                        "advancement todo or record an explicit no-follow-up rationale"
                    ),
                }
            ],
            stop_condition=(
                "stop if the gap requires private material, credentials, destructive git, "
                "production actions, or owner-only decisions"
            ),
            recommended_action=(
                " ".join(
                    str(gap["resolution_hint"])
                    for gap in compact_acceptance_gaps
                    if gap.get("resolution_hint")
                )
                if all(gap.get("resolution_hint") for gap in compact_acceptance_gaps)
                else
                "run a bounded vision-gap replan before another quiet poll: create "
                "successor work, update the agent vision, record evidence gap, or "
                "record no-follow-up"
            ),
            rearmed_after_obligation_id=rearmed_after_obligation_id,
        )
    if replan_rule.rule is GoalFrontierReplanRule.LONG_TODO_CHAIN:
        assert long_chain_observation is not None
        assert long_chain_ack_decision is not None
        long_chain_trigger = long_chain_observation.trigger
        return build_autonomous_replan_obligation_payload(
            schema_version=AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
            agent_id=agent_id,
            include_agent_id=True,
            stall_threshold=long_chain_trigger.get("threshold"),
            trigger_count=long_chain_trigger.get("trigger_count"),
            triggers=[
                {
                    "kind": LONG_TODO_CHAIN_TRIGGER,
                    "section": "agent_todo_summary",
                    "text": (
                        "current agent lane has a long selectable todo chain; "
                        "run a vision checkpoint/replan before continuing linearly"
                    ),
                    **long_chain_trigger,
                }
            ],
            guidance_actions=[
                "read_evidence_log",
                "run_bounded_public_research_if_local_evidence_is_missing",
                "group_or_prune_todo_chain",
                "update_agent_vision",
                "create_successor",
            ],
            todo_actions=[
                {
                    "action": "add",
                    "role": "agent",
                    "priority": "P1",
                    "text": (
                        "run a bounded long-chain vision replan: compare evidence "
                        "with the active vision, group or prune the todo chain, "
                        "and select the next high-value runnable slice"
                    ),
                }
            ],
            stop_condition=(
                "stop if pruning or external research requires private material, "
                "credentials, destructive git, production actions, or owner-only decisions"
            ),
            recommended_action=(
                "run a bounded long-chain vision replan before continuing a 15+ "
                "todo lane: read evidence, use public research if local evidence "
                "is weak, group/prune work, and write a concrete todo or vision delta"
            ),
            rearmed_after_obligation_id=(
                long_chain_ack_decision.rearmed_after_obligation_id
            ),
        )
    if replan_rule.rule is GoalFrontierReplanRule.MONITOR_NO_CHANGE_STREAK:
        assert monitor_no_change_trigger is not None
        return build_autonomous_replan_obligation_payload(
            schema_version=AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
            agent_id=agent_id,
            include_agent_id=True,
            stall_threshold=MONITOR_NO_CHANGE_STREAK_THRESHOLD,
            trigger_count=1,
            triggers=[monitor_no_change_trigger],
            guidance_actions=[
                "set_watch_expiry",
                "write_blocker",
                "supersede_monitor",
                "create_successor",
            ],
            todo_actions=[
                {
                    "action": "add",
                    "role": "agent",
                    "priority": "P1",
                    "text": (
                        "resolve the stalled monitor lane with an expiry, blocker, "
                        "supersede transition, or runnable successor"
                    ),
                }
            ],
            stop_condition=(
                "stop if the replan requires private material, credentials, "
                "destructive git, production actions, or owner-only decisions"
            ),
            recommended_action=(
                "resolve the current agent's stalled monitor lane before another "
                "quiet poll: set an expiry, write a blocker, supersede the monitor, "
                "or create runnable successor work"
            ),
        )
    assert replan_rule.rule is GoalFrontierReplanRule.MONITOR_FRONTIER_EXHAUSTED
    future_schedule_present = _monitor_only_lane_has_future_schedule(agent_todo_summary)

    return build_autonomous_replan_obligation_payload(
        schema_version=AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
        stall_threshold=1,
        trigger_count=1,
        triggers=[
            {
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
            }
        ],
        guidance_actions=[
            "create_successor",
            "supersede_monitor",
            "set_watch_expiry",
            "record_no_followup",
        ],
        todo_actions=[
            {
                "action": "add",
                "role": "agent",
                "priority": "P1",
                "text": (
                    "run a compact goal-frontier replan: create a successor runnable "
                    "todo, supersede stale monitor work, set watch-lane expiry, or "
                    "record an explicit no-follow-up rationale"
                ),
            }
        ],
        stop_condition=(
            "stop if the replan requires private material, credentials, destructive git, "
            "production actions, or owner-only decisions"
        ),
        recommended_action=(
            "run a bounded goal-frontier replan before another monitor-only quiet "
            "poll: create successor work, supersede the monitor lane, set an expiry, "
            "or record an evidence-backed no-follow-up outcome"
        ),
    )


def build_goal_frontier_projection_from_summaries(
    *,
    goal_id: str,
    agent_id: str | None,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
    replan_obligation: dict[str, Any] | None,
    acceptance_gaps: list[dict[str, Any]] | None = None,
    vision_wait_state: dict[str, Any] | None = None,
    fallback_gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    user_counts = _summary_task_counts(user_todo_summary)
    agent_counts = _summary_task_counts(agent_todo_summary)
    frontier_counts = _frontier_advancement_counts(
        agent_todo_summary=agent_todo_summary,
        agent_id=agent_id,
    )
    selectable_frontier_advancement = (
        frontier_counts["current_agent_claimed_advancement_count"]
        + frontier_counts["unclaimed_advancement_count"]
    )
    monitor_only_lane = bool(
        _is_monitor_only_lane(work_lane_contract)
        and agent_counts.get("monitor", 0) > 0
        and agent_counts.get("advancement", 0) == 0
        and (
            selectable_frontier_advancement == 0
            if agent_id
            else sum(frontier_counts.values()) == 0
        )
    )
    projection = build_goal_frontier_projection(
        goal_id=goal_id,
        agent_id=agent_id,
        user_counts=user_counts,
        agent_counts=agent_counts,
        current_agent_claimed_advancement_count=frontier_counts[
            "current_agent_claimed_advancement_count"
        ],
        unclaimed_advancement_count=frontier_counts["unclaimed_advancement_count"],
        other_agent_claimed_advancement_count=frontier_counts[
            "other_agent_claimed_advancement_count"
        ],
        monitor_only_lane=monitor_only_lane,
        replan_obligation=replan_obligation,
        acceptance_gaps=acceptance_gaps,
        vision_wait_state=vision_wait_state,
        fallback_gaps=fallback_gaps,
        deferred_successors=_deferred_successors(
            agent_todo_summary,
            agent_id=agent_id,
        ),
    )
    source_completeness, terminal_state = derive_goal_terminal_state(
        user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary,
        projection=projection,
    )
    if terminal_state:
        projection["source_completeness"] = source_completeness
        projection["terminal_state"] = terminal_state
    return projection


def build_goal_frontier_projection_context_from_status(
    *,
    goal_id: str,
    agent_id: str | None,
    status_payload: dict[str, Any],
    item: dict[str, Any],
    project_asset: dict[str, Any] | None,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
    neutral_replan_ack_classifications: set[str],
    agent_todo_source_items: list[dict[str, Any]] | None = None,
    registered_agent_ids: list[str] | None = None,
    goal_status: str | None = None,
    agent_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the quota-facing goal-frontier read model.

    Quota decides delivery permission, but this helper owns the goal-frontier
    state reduction: existing obligation scope, latest replan ACK, open
    per-agent vision gaps, derived replan obligation, and final projection.
    """

    replan_obligation = select_autonomous_replan_obligation(
        item,
        project_asset,
        agent_id=agent_id,
    )
    if replan_obligation:
        replan_obligation = ensure_replan_novelty_policy(replan_obligation)
    replan_scope = autonomous_replan_scope_decision(
        replan_obligation,
        agent_id=agent_id,
        registered_agent_ids=registered_agent_ids,
    )
    if replan_scope.get("required") and not replan_scope.get("applies"):
        replan_obligation = None

    latest_agent_replan_ack = latest_autonomous_replan_ack_from_status_payload(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
        neutral_classifications=neutral_replan_ack_classifications,
    )
    latest_replan_ack_feedback = latest_replan_ack_feedback_from_status_payload(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    ack_time = parse_timestamp((latest_agent_replan_ack or {}).get("generated_at"))
    feedback_time = parse_timestamp(
        (latest_replan_ack_feedback or {}).get("generated_at")
    )
    if ack_time is not None and feedback_time is not None and ack_time >= feedback_time:
        latest_replan_ack_feedback = None
    latest_agent_vision = latest_agent_vision_from_status_payload(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    latest_missing_vision_checkpoint = latest_missing_vision_checkpoint_from_status_payload(
        status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    latest_vision_checkpoint = (
        outcome_continuity.latest_outcome_vision_checkpoint_from_status_payload(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
        )
    )
    source_acceptance_gaps = (
        acceptance_gaps_from_agent_profile_requirement(
            agent_profile,
            agent_id=agent_id,
            agent_vision=latest_agent_vision,
            missing_checkpoint=latest_missing_vision_checkpoint,
        )
        + acceptance_gaps_from_agent_vision(
            latest_agent_vision,
            goal_status=goal_status,
        )
        + outcome_continuity.acceptance_gaps_from_vision_checkpoint(
            latest_missing_vision_checkpoint
        )
        + outcome_continuity.acceptance_gaps_from_outcome_checkpoint(
            latest_agent_vision,
            latest_vision_checkpoint,
        )
        + outcome_continuity.acceptance_gaps_from_todo_completion_checkpoint(
            latest_agent_vision,
            latest_vision_checkpoint,
            agent_todo_summary=agent_todo_summary,
            agent_id=agent_id,
            completed_todo_threshold=completed_todo_replan_threshold(
                (project_asset or {}).get("execution_profile")
            ),
        )
    )
    if _terminal_no_followup_resolves_vision_checkpoint(
        user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary,
        checkpoint=latest_missing_vision_checkpoint,
    ):
        source_acceptance_gaps = [
            gap
            for gap in source_acceptance_gaps
            if gap.get("kind")
            != outcome_continuity.VISION_CHECKPOINT_MISSING_TRIGGER
        ]
    todo_succession_gap_open = bool(
        todo_succession_gap_items(
            agent_todo_summary,
            agent_id=agent_id,
        )
    )
    replan_obligation = align_autonomous_replan_guidance_with_acceptance_policy(
        replan_obligation,
        acceptance_gaps=source_acceptance_gaps,
    )
    frontier_counts = _frontier_advancement_counts(
        agent_todo_summary=agent_todo_summary,
        agent_id=agent_id,
    )
    vision_wait_state = build_goal_vision_wait_state(
        agent_todo_summary=agent_todo_summary,
        source_items=agent_todo_source_items,
        agent_id=agent_id,
        acceptance_gaps=source_acceptance_gaps,
        selectable_advancement_count=(
            frontier_counts["current_agent_claimed_advancement_count"]
            + frontier_counts["unclaimed_advancement_count"]
        ),
    )
    acceptance_gaps = [] if vision_wait_state else source_acceptance_gaps
    declared_fallback_gaps = [
        gap
        for gap in (
            declared_fallback_gap_from_agent_vision(
                latest_agent_vision,
                agent_todo_summary=agent_todo_summary,
                agent_id=agent_id,
            ),
        )
        if isinstance(gap, dict)
    ]
    projected_replan_ack = projected_autonomous_replan_ack_for_agent(
        item,
        project_asset,
        agent_id=agent_id,
    )
    effective_replan_ack = latest_agent_replan_ack or projected_replan_ack
    replan_transition_ack = replan_successor_transition_ack(
        agent_todo_summary,
        agent_id=agent_id,
        replan_obligation=replan_obligation,
        agent_todo_items=agent_todo_source_items,
    )
    obligation_ack = replan_transition_ack or effective_replan_ack
    if (
        autonomous_replan_is_required(replan_obligation)
        and autonomous_replan_ack_satisfies_obligation(
            obligation_ack,
            replan_obligation=replan_obligation,
            acceptance_gaps=source_acceptance_gaps,
            todo_succession_gap_open=todo_succession_gap_open,
        )
        and autonomous_replan_ack_matches_frontier(
            obligation_ack,
            replan_obligation,
        )
    ):
        replan_obligation = None
        replan_scope = autonomous_replan_scope_decision(
            replan_obligation,
            agent_id=agent_id,
            registered_agent_ids=registered_agent_ids,
        )
    frontier_replan_obligation = derive_goal_frontier_replan_obligation_from_summaries(
        user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary,
        work_lane_contract=work_lane_contract,
        agent_id=agent_id,
        existing_replan_obligation=replan_obligation,
        agent_todo_source_items=agent_todo_source_items,
        latest_replan_ack=effective_replan_ack,
        current_transition_replan_ack=replan_transition_ack,
        acceptance_gaps=acceptance_gaps,
        monitor_lane_semantically_valid=not goal_vision_state_is_closed(
            (latest_agent_vision or {}).get("state")
        ),
    )
    frontier_transition_ack = replan_successor_transition_ack(
        agent_todo_summary,
        agent_id=agent_id,
        replan_obligation=frontier_replan_obligation,
        agent_todo_items=agent_todo_source_items,
    )
    frontier_obligation_ack = frontier_transition_ack or effective_replan_ack
    if (
        frontier_replan_obligation
        and autonomous_replan_ack_satisfies_obligation(
            frontier_obligation_ack,
            replan_obligation=frontier_replan_obligation,
            acceptance_gaps=source_acceptance_gaps,
            todo_succession_gap_open=todo_succession_gap_open,
        )
        and autonomous_replan_ack_matches_frontier(
            frontier_obligation_ack,
            frontier_replan_obligation,
        )
    ):
        frontier_replan_obligation = None
        replan_transition_ack = frontier_transition_ack
    if frontier_replan_obligation:
        replan_obligation = ensure_replan_novelty_policy(
            frontier_replan_obligation
        )
        replan_scope = autonomous_replan_scope_decision(
            replan_obligation,
            agent_id=agent_id,
            registered_agent_ids=registered_agent_ids,
        )

    if replan_obligation and latest_replan_ack_feedback:
        replan_obligation = dict(replan_obligation)
        replan_obligation["replan_ack_feedback"] = latest_replan_ack_feedback
        claims = latest_replan_ack_feedback.get("rejected_claims") or []
        first_claim = next(
            (item for item in claims if isinstance(item, dict)),
            None,
        )
        if first_claim:
            replan_obligation["recommended_action"] = (
                "previous replan ACK was rejected: "
                f"{first_claim.get('kind')}: {first_claim.get('reason')}; "
                + str(replan_obligation.get("recommended_action") or "replan again")
            )

    if replan_obligation:
        replan_obligation = dict(replan_obligation)
        replan_obligation["replan_context"] = build_replan_context(
            replan_obligation,
            goal_id=goal_id,
            agent_id=agent_id,
            newest_first_runs=latest_runs_for_goal(
                status_payload,
                goal_id=goal_id,
            ),
        )

    goal_frontier_projection = build_goal_frontier_projection_from_summaries(
        goal_id=goal_id,
        agent_id=agent_id,
        user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary,
        work_lane_contract=work_lane_contract,
        replan_obligation=replan_obligation,
        acceptance_gaps=acceptance_gaps,
        vision_wait_state=vision_wait_state,
        fallback_gaps=declared_fallback_gaps,
    )
    if latest_replan_ack_feedback:
        goal_frontier_projection["replan_ack_feedback"] = (
            latest_replan_ack_feedback
        )
    return {
        "schema_version": "goal_frontier_projection_context_v0",
        "replan_obligation": replan_obligation,
        "replan_scope": replan_scope,
        "goal_frontier_projection": goal_frontier_projection,
        "acceptance_gaps": acceptance_gaps,
        "vision_wait_state": vision_wait_state,
        "latest_replan_ack": latest_agent_replan_ack,
        "projected_replan_ack": projected_replan_ack,
        "replan_transition_ack": replan_transition_ack,
    }


def compact_replan_obligation(replan_obligation: dict[str, Any]) -> dict[str, Any]:
    compact = {
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
            "evidence_source": "agent_scoped_evidence_log",
            "delivery": "host_projected",
            "writeback": "typed_semantic_delta",
        }
    if isinstance(replan_obligation.get("replan_ack_feedback"), dict):
        compact["replan_ack_feedback"] = replan_obligation["replan_ack_feedback"]
        compact["recommended_action"] = replan_obligation.get("recommended_action")
    return compact


def build_autonomous_replan_recommendation(
    replan_obligation: dict[str, Any],
    *,
    reason: str | None = None,
) -> dict[str, Any]:
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


def build_autonomous_replan_decision(replan_obligation: dict[str, Any]) -> dict[str, Any]:
    triggers = (
        replan_obligation.get("triggers")
        if isinstance(replan_obligation.get("triggers"), list)
        else []
    )
    return {
        "schema_version": AUTONOMOUS_REPLAN_DECISION_SCHEMA_VERSION,
        "required": True,
        "decision": AUTONOMOUS_REPLAN_REQUIRED_MODE,
        "decision_plane": "goal_frontier_before_lane_quiet_or_agent_scope_wait",
        "not_disturbed_by": [
            "monitor_quiet_skip",
            "agent_scope_wait",
            "agent_scope_exhausted",
        ],
        "trigger_count": safe_non_negative_int(replan_obligation.get("trigger_count")),
        "triggers": [
            trigger.get("kind")
            for trigger in triggers
            if isinstance(trigger, dict) and trigger.get("kind")
        ],
    }


def build_goal_frontier_projection(
    *,
    goal_id: str,
    agent_id: str | None,
    user_counts: dict[str, int],
    agent_counts: dict[str, int],
    current_agent_claimed_advancement_count: int,
    unclaimed_advancement_count: int,
    other_agent_claimed_advancement_count: int,
    monitor_only_lane: bool,
    replan_obligation: dict[str, Any] | None,
    acceptance_gaps: list[dict[str, Any]] | None = None,
    deferred_successors: dict[str, Any] | None = None,
    vision_wait_state: dict[str, Any] | None = None,
    fallback_gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        if vision_wait_state.get("reason_code") == "current_agent_blocker":
            blockers.append("current_agent_blocker")
        else:
            blockers.append("vision_blocked_successor_wait")

    compact_acceptance_gaps = [
        item for item in (acceptance_gaps or []) if isinstance(item, dict)
    ]
    vision_continuation_audit = build_vision_continuation_audit(
        goal_id=goal_id,
        agent_id=agent_id,
        acceptance_gaps=compact_acceptance_gaps,
    )
    projection: dict[str, Any] = {
        "schema_version": GOAL_FRONTIER_PROJECTION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "source": "quota_should_run",
        "normalized_progress": {
            "user_open_count": user_counts.get("open", 0),
            "agent_open_count": agent_counts.get("open", 0),
            "agent_advancement_open_count": agent_counts.get("advancement", 0),
            "agent_monitor_open_count": agent_counts.get("monitor", 0),
            "agent_monitor_due_count": agent_counts.get("monitor_due", 0),
            "agent_watch_only_monitor_due_count": agent_counts.get(
                "watch_only_monitor_due", 0
            ),
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
        "deferred_successors": deferred_successors
        if isinstance(deferred_successors, dict)
        else {
            "ready_count": 0,
            "blocked_count": 0,
            "current_agent_ready_count": 0,
            "ready_todo_ids": [],
        },
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
        projection["autonomous_replan_decision"] = build_autonomous_replan_decision(
            replan_obligation
        )
    return projection
