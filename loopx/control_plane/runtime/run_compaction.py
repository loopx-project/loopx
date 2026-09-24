from __future__ import annotations

from typing import Any, Callable, Optional


HUMAN_REWARD_COMPACT_FIELDS = (
    "recorded_at",
    "actor_kind",
    "decision",
    "reward",
    "reason_summary",
    "follow_up",
    "lesson",
)
OPERATOR_GATE_COMPACT_FIELDS = (
    "recorded_at",
    "gate",
    "decision",
    "operator_question",
    "reason_summary",
    "follow_up",
    "agent_command",
)
OPERATOR_GATE_RESUME_CONTRACT_COMPACT_FIELDS = (
    "version",
    "goal_id",
    "run_id",
    "gate_id",
    "created_state_ref",
    "created_policy_version",
    "allowed_decisions",
    "operator_decision",
    "latest_state_ref",
    "freshness_check",
    "precondition_check",
    "migration_or_rebase_result",
    "resulting_action",
    "validation_after_resume",
)
CONTROLLER_READINESS_COMPACT_FIELDS = (
    "classification",
    "read_only_observer_ready",
    "decision_advisor_ready",
    "write_controller_ready",
    "missing_gates",
    "review_judgment",
    "next_handoff_condition",
)
CONTROLLER_READINESS_GATE_FIELDS = (
    "id",
    "ok",
    "review",
)
VISION_CHECKPOINT_COMPACT_FIELDS = (
    "schema_version",
    "agent_id",
    "required",
    "satisfied",
    "decision",
    "delivery_boundary",
    "agent_vision_state",
    "unchanged_reason",
    "missing_baseline",
)
VISION_CHECKPOINT_TRIGGER_FIELDS = (
    "kind",
    "delivery_outcome",
    "todo_id",
)
VISION_CHECKPOINT_CONTINUITY_BASIS_FIELDS = (
    "kind",
    "vision_generated_at",
)
QUOTA_MONITOR_TARGET_COMPACT_FIELDS = (
    "schema_version",
    "target_id",
    "monitor_mode",
    "effective_action",
    "agent_id",
    "frontier_identity",
)
RUN_BASE_COMPACT_FIELDS = (
    "generated_at",
    "run_id",
    "turn_instance_id",
    "replan_obligation_id",
    "goal_id",
    "parent_run_id",
    "spawned_by_goal_id",
    "agent_role",
    "classification",
    "agent_id",
    "agent_lane",
    "progress_scope",
    "todo_id",
    "progress_observation",
    "blocked_retry",
    "delivery_batch_scale",
    "delivery_outcome",
    "lifecycle_phase",
    "lifecycle_flags",
    "recommended_action",
    "health_check",
    "result_status",
    "approval_state",
    "active_task_count",
    "active_priorities",
    "cache_check",
    "project_map",
    "json_exists",
    "markdown_exists",
    "usage",
)

CompactProjection = Callable[[Any], Optional[dict[str, Any]]]
LifecycleFlags = Callable[[Optional[dict[str, Any]]], list[str]]
PrimaryLifecyclePhase = Callable[..., str]
TextCompactor = Callable[..., Any]
RunProjection = Callable[[dict[str, Any]], Optional[dict[str, Any]]]
ValueProjection = Callable[[Any], Optional[dict[str, Any]]]
SubagentRunCompactor = Callable[..., Optional[dict[str, Any]]]


def compact_human_reward(reward: Any) -> dict[str, Any] | None:
    if not isinstance(reward, dict):
        return None
    compact = {field: reward[field] for field in HUMAN_REWARD_COMPACT_FIELDS if field in reward}
    lesson = compact.get("lesson")
    if isinstance(lesson, dict):
        compact["lesson"] = {
            field: lesson[field]
            for field in ("schema_version", "kind", "summary", "avoid", "prefer")
            if field in lesson
        }
    return compact or None


def compact_operator_gate(operator_gate: Any) -> dict[str, Any] | None:
    if not isinstance(operator_gate, dict):
        return None
    compact = {field: operator_gate[field] for field in OPERATOR_GATE_COMPACT_FIELDS if field in operator_gate}
    return compact or None


def compact_operator_gate_resume_contract(contract: Any) -> dict[str, Any] | None:
    if not isinstance(contract, dict):
        return None
    compact = {
        field: contract[field]
        for field in OPERATOR_GATE_RESUME_CONTRACT_COMPACT_FIELDS
        if field in contract
    }
    interrupt = contract.get("interrupt_payload") if isinstance(contract.get("interrupt_payload"), dict) else {}
    if interrupt:
        compact["interrupt_payload"] = {
            field: interrupt[field]
            for field in ("question", "choices")
            if field in interrupt
        }
    return compact or None


def compact_controller_readiness(readiness: Any) -> dict[str, Any] | None:
    if not isinstance(readiness, dict):
        return None
    compact = {
        field: readiness[field]
        for field in CONTROLLER_READINESS_COMPACT_FIELDS
        if field in readiness
    }
    gates = []
    for gate in readiness.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        gates.append({field: gate[field] for field in CONTROLLER_READINESS_GATE_FIELDS if field in gate})
    if gates:
        compact["gates"] = gates
    return compact or None


def compact_vision_checkpoint(checkpoint: Any) -> dict[str, Any] | None:
    if not isinstance(checkpoint, dict):
        return None
    compact = {
        field: checkpoint[field]
        for field in VISION_CHECKPOINT_COMPACT_FIELDS
        if field in checkpoint
    }
    # semantic_closeout is the long-standing default. Preserve the explicit
    # field only for the non-default in-flight boundary that callers must carry
    # across heartbeat settlements.
    if compact.get("delivery_boundary") == "semantic_closeout":
        compact.pop("delivery_boundary")
    triggers: list[dict[str, Any]] = []
    raw_triggers = checkpoint.get("triggers")
    if isinstance(raw_triggers, list):
        for trigger in raw_triggers[:5]:
            if not isinstance(trigger, dict):
                continue
            compact_trigger = {
                field: trigger[field]
                for field in VISION_CHECKPOINT_TRIGGER_FIELDS
                if field in trigger
            }
            if compact_trigger:
                triggers.append(compact_trigger)
    if triggers:
        compact["triggers"] = triggers
    continuity_basis = checkpoint.get("continuity_basis")
    if isinstance(continuity_basis, dict):
        compact_basis = {
            field: continuity_basis[field]
            for field in VISION_CHECKPOINT_CONTINUITY_BASIS_FIELDS
            if field in continuity_basis
        }
        if compact_basis:
            compact["continuity_basis"] = compact_basis
    required_resolution = checkpoint.get("required_resolution")
    if isinstance(required_resolution, list):
        compact["required_resolution"] = [
            str(item)
            for item in required_resolution[:5]
            if str(item or "").strip()
        ]
    repair_delta_kinds = checkpoint.get("repair_delta_kinds")
    if isinstance(repair_delta_kinds, list):
        compact["repair_delta_kinds"] = [
            str(item)
            for item in repair_delta_kinds[:5]
            if str(item or "").strip()
        ]
    return compact or None


def compact_quota_monitor_target(run: dict[str, Any]) -> dict[str, Any] | None:
    if str(run.get("classification") or "") != "quota_monitor_poll":
        return None
    target = run.get("monitor_target")
    if not isinstance(target, dict):
        event = run.get("monitor_event")
        target = event.get("monitor_target") if isinstance(event, dict) else None
    if not isinstance(target, dict):
        return None
    if not str(target.get("target_id") or "").strip():
        return None
    compact = {
        field: target[field]
        for field in QUOTA_MONITOR_TARGET_COMPACT_FIELDS
        if field in target
    }
    return compact or None


def compact_replan_ack_feedback(run: dict[str, Any]) -> dict[str, Any] | None:
    noop = run.get("autonomous_replan_noop")
    ack = run.get("autonomous_replan_ack")
    delta = ack.get("delta_contract") if isinstance(ack, dict) else None
    rejected = delta.get("rejected_claims") if isinstance(delta, dict) else None
    if not isinstance(noop, dict) or not isinstance(rejected, list) or not rejected:
        return None
    claims = [
        {
            "kind": str(item.get("kind") or "").strip(),
            "reason": str(item.get("reason") or "").strip(),
        }
        for item in rejected[:5]
        if isinstance(item, dict)
        and str(item.get("kind") or "").strip()
        and str(item.get("reason") or "").strip()
    ]
    if not claims:
        return None
    return {
        "schema_version": "replan_ack_feedback_v0",
        "generated_at": run.get("generated_at"),
        "agent_id": run.get("agent_id"),
        "classification": noop.get("classification"),
        "rejected_claims": claims,
    }


def compact_run_base(
    run: dict[str, Any],
    *,
    run_compact_fields: tuple[str, ...],
    run_lifecycle_flags: LifecycleFlags,
    primary_lifecycle_phase: PrimaryLifecyclePhase,
    compact_human_reward: CompactProjection,
    compact_operator_gate: CompactProjection,
    compact_autonomous_replan_ack: RunProjection,
    compact_operator_gate_resume_contract: CompactProjection,
    compact_controller_readiness: CompactProjection,
    public_safe_compact_text: TextCompactor,
    compact_subagent_run: SubagentRunCompactor,
    max_subagent_activity_items: int,
    compact_agent_vision: ValueProjection | None = None,
) -> dict[str, Any]:
    compact = {field: run[field] for field in run_compact_fields if field in run}
    flags = run_lifecycle_flags(run)
    compact.setdefault("lifecycle_phase", primary_lifecycle_phase(flags, fallback="run_recorded"))
    compact.setdefault("lifecycle_flags", flags)

    agent_vision = compact_agent_vision(run.get("agent_vision")) if compact_agent_vision else None
    if agent_vision:
        compact["agent_vision"] = agent_vision

    vision_checkpoint = compact_vision_checkpoint(run.get("vision_checkpoint"))
    if vision_checkpoint:
        compact["vision_checkpoint"] = vision_checkpoint

    monitor_target = compact_quota_monitor_target(run)
    if monitor_target:
        compact["monitor_target"] = monitor_target

    reward = compact_human_reward(run.get("human_reward"))
    if reward:
        compact["human_reward"] = reward

    operator_gate = compact_operator_gate(run.get("operator_gate"))
    if operator_gate:
        compact["operator_gate"] = operator_gate

    replan_ack = compact_autonomous_replan_ack(run)
    if replan_ack:
        compact["autonomous_replan_ack"] = replan_ack

    replan_feedback = compact_replan_ack_feedback(run)
    if replan_feedback:
        compact["replan_ack_feedback"] = replan_feedback

    resume_contract = compact_operator_gate_resume_contract(run.get("operator_gate_resume_contract"))
    if resume_contract:
        compact["operator_gate_resume_contract"] = resume_contract

    readiness = compact_controller_readiness(run.get("controller_readiness"))
    if readiness:
        compact["controller_readiness"] = readiness

    merge_decision = public_safe_compact_text(run.get("merge_decision"), limit=240)
    if merge_decision:
        compact["merge_decision"] = merge_decision

    subagents = [
        child
        for child in (
            compact_subagent_run(
                child_run,
                parent_goal_id=str(run.get("goal_id") or ""),
                parent_run_id=str(run.get("run_id") or run.get("generated_at") or ""),
            )
            for child_run in (run.get("subagents") or [])
            if isinstance(child_run, dict)
        )
        if child
    ]
    if subagents:
        compact["subagents"] = subagents[:max_subagent_activity_items]
        compact["subagent_count"] = len(subagents)

    return compact


def attach_run_summary_projections(
    compact: dict[str, Any],
    run: dict[str, Any],
    *,
    compact_session_runtime_projection_from_run: RunProjection,
) -> dict[str, Any]:
    """Attach optional read-path summaries to a compact run payload."""
    result = dict(compact)

    session_projection = compact_session_runtime_projection_from_run(run)
    if session_projection:
        result["session_runtime_projection"] = session_projection

    return result
