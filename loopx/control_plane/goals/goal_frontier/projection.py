"""Read-model projection and vision-audit payload builders for goal_frontier."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..goal_vision_read_model import _compact_projection_text

Payload = dict[str, object]
PayloadView = Mapping[str, object]

GOAL_FRONTIER_PROJECTION_SCHEMA_VERSION = "goal_frontier_projection_v0"
VISION_CONTINUATION_AUDIT_SCHEMA_VERSION = "vision_continuation_audit_v0"
VISION_GAP_JUDGE_SCHEMA_VERSION = "vision_gap_judge_v0"
AUTONOMOUS_REPLAN_DECISION_SCHEMA_VERSION = "autonomous_replan_decision_v0"
AUTONOMOUS_REPLAN_REQUIRED_MODE = "autonomous_replan_required"


def safe_non_negative_int(value: object) -> int:
    if not value or not isinstance(value, (int, float, str, bytes, bytearray)):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def autonomous_replan_is_required(replan_obligation: PayloadView | None) -> bool:
    return bool(replan_obligation and replan_obligation.get("required"))


def build_vision_gap_judge(
    *,
    goal_id: str | None = None,
    agent_id: str | None,
    acceptance_gaps: Sequence[PayloadView] | None,
) -> Payload:
    """Return the strict done/continue judge for active per-agent vision gaps."""

    first_gap = next((g for g in (acceptance_gaps or ()) if isinstance(g, dict)), {})
    reason = _compact_projection_text(
        first_gap.get("replan_trigger_summary")
        or "active per-agent vision still has an open acceptance gap",
        limit=220,
    )
    return {
        "schema_version": VISION_GAP_JUDGE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "done": False,
        "decision": "continue",
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


def build_vision_continuation_audit(
    *,
    goal_id: str | None = None,
    agent_id: str | None,
    acceptance_gaps: Sequence[PayloadView] | None,
) -> Payload | None:
    """Return the closeout audit contract for an open per-agent vision gap."""

    compact_gaps = [gap for gap in (acceptance_gaps or ()) if isinstance(gap, dict)]
    if not compact_gaps:
        return None
    acceptance_requirements = [
        t for g in compact_gaps
        if (t := _compact_projection_text(g.get("acceptance_summary"), limit=180))
    ]
    trigger_kinds = [
        k for g in compact_gaps
        if (k := _compact_projection_text(g.get("kind"), limit=80))
    ]
    audit: Payload = {
        "schema_version": VISION_CONTINUATION_AUDIT_SCHEMA_VERSION,
        "required": True,
        "agent_id": agent_id,
        "decision": "acceptance_gap_open",
        "selected_todo_is_goal_completion": False,
        "closeout_allowed_without_evidence": False,
        "trigger_count": len(compact_gaps),
        "trigger_kinds": list(dict.fromkeys(trigger_kinds))[:5],
        "acceptance_gaps": compact_gaps[:5],
        "vision_gap_judge": build_vision_gap_judge(
            goal_id=goal_id, agent_id=agent_id, acceptance_gaps=compact_gaps,
        ),
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
    feedback = replan_obligation.get("replan_ack_feedback")
    if isinstance(feedback, dict):
        compact["replan_ack_feedback"] = feedback
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
    raw_triggers = replan_obligation.get("triggers")
    triggers = raw_triggers if isinstance(raw_triggers, list) else []
    return {
        "schema_version": AUTONOMOUS_REPLAN_DECISION_SCHEMA_VERSION,
        "required": True,
        "decision": AUTONOMOUS_REPLAN_REQUIRED_MODE,
        "decision_plane": "goal_frontier_before_lane_quiet_or_agent_scope_wait",
        "not_disturbed_by": ["monitor_quiet_skip", "agent_scope_wait", "agent_scope_exhausted"],
        "trigger_count": safe_non_negative_int(replan_obligation.get("trigger_count")),
        "triggers": [t.get("kind") for t in triggers if isinstance(t, dict) and t.get("kind")],
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
    fallback_gaps: Sequence[PayloadView] | None = None,
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

    compact_acceptance_gaps = [item for item in (acceptance_gaps or ()) if isinstance(item, dict)]
    vision_continuation_audit = build_vision_continuation_audit(
        goal_id=goal_id, agent_id=agent_id, acceptance_gaps=compact_acceptance_gaps,
    )
    projection: Payload = {
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
        "deferred_successors": deferred_successors
        if isinstance(deferred_successors, dict)
        else {"ready_count": 0, "blocked_count": 0, "current_agent_ready_count": 0, "ready_todo_ids": []},
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
        projection["fallback_gaps"] = list(fallback_gaps[:1])
    if isinstance(vision_wait_state, dict):
        projection["vision_wait_state"] = vision_wait_state
    if replan_required and isinstance(replan_obligation, dict):
        projection["autonomous_replan_decision"] = build_autonomous_replan_decision(replan_obligation)
    return projection
