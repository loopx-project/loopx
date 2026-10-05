from __future__ import annotations

from typing import Any

from ...state_projection import next_action_resolution_trace
from ..agents.agent_scope_frontier import (
    agent_scope_frontier_action as _agent_scope_frontier_action,
)
from ..todos.contract import TODO_TASK_CLASS_ADVANCEMENT
from ..todos.todo_semantics import todo_item_is_actionable_open, todo_item_task_class
from .autonomous_replan_obligation import todo_lifecycle_settlement_obligation


def protocol_action_text(value: Any, *, limit: int = 220) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def protocol_action_label(value: Any, *, limit: int = 80) -> str | None:
    text = protocol_action_text(value)
    if not text:
        return None
    head, separator, _tail = text.partition(":")
    if separator and "[" in head and "]" in head and 8 <= len(head) <= limit:
        return head.strip()
    return protocol_action_text(text, limit=limit)


def protocol_first_candidate_action(payload: dict[str, Any]) -> str | None:
    goal_id = str(payload.get("goal_id") or "")
    agent_lane_next_action = (
        payload.get("agent_lane_next_action")
        if isinstance(payload.get("agent_lane_next_action"), dict)
        else {}
    )
    lane_text = protocol_action_label(agent_lane_next_action.get("next_step") or agent_lane_next_action.get("text"))
    if lane_text:
        todo_id = str(agent_lane_next_action.get("todo_id") or "").strip()
        return f"{todo_id}: {lane_text}" if todo_id else lane_text
    work_lane = (
        payload.get("work_lane_contract")
        if isinstance(payload.get("work_lane_contract"), dict)
        else {}
    )
    if work_lane.get("monitor_kind") == "todo_monitor_due":
        due_items = (
            work_lane.get("monitor_due_items")
            if isinstance(work_lane.get("monitor_due_items"), list)
            else []
        )
        for item in due_items:
            if not isinstance(item, dict):
                continue
            text = protocol_action_label(item.get("text"))
            if text:
                todo_id = str(item.get("todo_id") or "").strip()
                return f"{todo_id}: {text}" if todo_id else text
    if work_lane.get("obligation") == "repair_monitor_schedule_metadata":
        text = protocol_action_label(work_lane.get("action"), limit=160)
        if text:
            todo_id = str(work_lane.get("selected_todo_id") or "").strip()
            return f"{todo_id}: {text}" if todo_id else text
    capability_gate = (
        payload.get("capability_gate")
        if isinstance(payload.get("capability_gate"), dict)
        else {}
    )
    if capability_gate.get("action") == "run":
        runnable_candidates = (
            capability_gate.get("runnable_candidates")
            if isinstance(capability_gate.get("runnable_candidates"), list)
            else []
        )
        if runnable_candidates:
            return (
                f"choose one of {len(runnable_candidates)} "
                "capability-runnable todo(s) after steering audit"
            )
    scoped_fallback = (
        payload.get("scoped_user_gate_fallback")
        if isinstance(payload.get("scoped_user_gate_fallback"), dict)
        else {}
    )
    selected = (
        scoped_fallback.get("selected_executable")
        if isinstance(scoped_fallback.get("selected_executable"), dict)
        else {}
    )
    text = protocol_action_label(selected.get("text"))
    if text:
        if selected.get("fallback_kind") == "deferred_successor_replan":
            todo_id = str(selected.get("todo_id") or "").strip()
            action = f"replan ready deferred successor {text}"
            return f"{todo_id}: {action}" if todo_id else action
        return text

    agent_todos = (
        payload.get("agent_todo_summary")
        if isinstance(payload.get("agent_todo_summary"), dict)
        else {}
    )
    for key in ("first_executable_items", "first_open_items"):
        items = agent_todos.get(key) if isinstance(agent_todos.get(key), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if not todo_item_is_actionable_open(item):
                continue
            if todo_item_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT:
                continue
            text = protocol_action_label(item.get("text"))
            if text:
                return text

    backlog = (
        payload.get("autonomous_backlog_candidates")
        if isinstance(payload.get("autonomous_backlog_candidates"), dict)
        else {}
    )
    items = backlog.get("items") if isinstance(backlog.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if goal_id and str(item.get("goal_id") or "") != goal_id:
            continue
        text = protocol_action_label(item.get("text"))
        if text:
            return text

    reason_codes = (
        work_lane.get("reason_codes")
        if isinstance(work_lane.get("reason_codes"), list)
        else []
    )
    if "next_action_requires_advancement" in reason_codes:
        text = protocol_action_text(payload.get("recommended_action"))
        if text:
            return text
    for key in ("action", "obligation"):
        text = protocol_action_text(work_lane.get(key))
        if text:
            return text
    return protocol_action_text(payload.get("recommended_action"))


def protocol_replan_action_packet_instruction(payload: dict[str, Any]) -> str:
    packet = (
        payload.get("replan_action_packet")
        if isinstance(payload.get("replan_action_packet"), dict)
        else {}
    )
    uncovered = (
        packet.get("uncovered_frontier")
        if isinstance(packet.get("uncovered_frontier"), dict)
        else {}
    )
    writeback_contract = (
        packet.get("writeback_contract")
        if isinstance(packet.get("writeback_contract"), dict)
        else {}
    )
    if packet.get("settlement_only") is True:
        return protocol_action_text(writeback_contract.get("rule"))
    if uncovered.get("required_any_of") and writeback_contract.get(
        "successor_command"
    ):
        return (
            "select one bounded next slice; prefer "
            "replan_action_packet.writeback_contract.successor_command"
        )
    return "produce one typed outcome from the host-projected replan action packet"


def protocol_monitor_action(payload: dict[str, Any]) -> str | None:
    goal_id = str(payload.get("goal_id") or "")
    work_lane = (
        payload.get("work_lane_contract")
        if isinstance(payload.get("work_lane_contract"), dict)
        else {}
    )
    if work_lane.get("obligation") == "quiet_until_material_monitor_transition":
        return "quiet until a material monitor transition, regression, or concrete blocker appears"
    monitors = (
        payload.get("autonomous_monitor_candidates")
        if isinstance(payload.get("autonomous_monitor_candidates"), dict)
        else {}
    )
    items = monitors.get("items") if isinstance(monitors.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if goal_id and str(item.get("goal_id") or "") != goal_id:
            continue
        text = protocol_action_label(item.get("text"), limit=160)
        if text:
            return text
    return None


def resolve_canonical_primary_action(payload: dict[str, Any], *, mode: str) -> str:
    execution_obligation = (
        payload.get("execution_obligation")
        if isinstance(payload.get("execution_obligation"), dict)
        else {}
    )
    if mode == "governed_capability_intent":
        projection = (
            payload.get("pending_capability_intent")
            if isinstance(payload.get("pending_capability_intent"), dict)
            else {}
        )
        return protocol_action_text(
            projection.get("action_summary"), limit=320
        ) or "consume the validated pending capability intent"
    if mode == "external_evidence_observation":
        external_observation = (
            payload.get("external_evidence_observation")
            if isinstance(payload.get("external_evidence_observation"), dict)
            else {}
        )
        observation_target = protocol_action_text(
            external_observation.get("observation_target"),
            limit=220,
        )
        if observation_target:
            return observation_target
        return (
            "verify an observable external handle or compact writeback channel; "
            "write a compact blocker when it is absent"
        )
    if mode == "autonomous_replan":
        lifecycle_settlement = todo_lifecycle_settlement_obligation(payload)
        if lifecycle_settlement is not None:
            return protocol_action_text(
                lifecycle_settlement.get("recommended_action"),
                limit=360,
            ) or (
                "settle the exact completed Todo lifecycle or link a real runnable "
                "successor; do not create lifecycle-only filler"
            )
        return protocol_replan_action_packet_instruction(payload)
    if mode == "monitor_quiet_skip":
        return (
            "ensure this heartbeat's idempotent quota receipt is committed, then "
            "stay quiet if unchanged; the guard records the eligible no-progress "
            "observation and an earlier heartbeat receipt does not satisfy this one"
        )
    if mode == "agent_monitor_only":
        return "stay quiet until a monitor is due, a verified direct reply arrives, or the work mode changes"
    if mode == "monitor_due":
        work_lane = (
            payload.get("work_lane_contract")
            if isinstance(payload.get("work_lane_contract"), dict)
            else {}
        )
        return protocol_action_text(work_lane.get("action"), limit=260) or (
            "attempt the due monitor and write back only a material transition"
        )
    if _agent_scope_frontier_action(mode) is not None:
        agent_scope_frontier = (
            payload.get("agent_scope_frontier")
            if isinstance(payload.get("agent_scope_frontier"), dict)
            else {}
        )
        action = protocol_action_text(agent_scope_frontier.get("recommended_action"), limit=260)
        return action or "stay quiet until this agent has a concrete in-scope runnable todo"
    if mode == "scoped_user_gate_fallback":
        return protocol_first_candidate_action(payload) or (
            "surface the scoped user gate, then advance one non-gated fallback"
        )
    if mode == "bounded_delivery_with_user_notice":
        return protocol_first_candidate_action(payload) or "advance scope-bounded work with validation"
    if mode == "task_orchestration":
        contract = (
            payload.get("task_orchestration_contract")
            if isinstance(payload.get("task_orchestration_contract"), dict)
            else {}
        )
        lanes = contract.get("eligible_child_lanes")
        if not isinstance(lanes, list):
            lanes = (
                contract.get("eligible_peer_lanes")
                if isinstance(contract.get("eligible_peer_lanes"), list)
                else []
            )
        if contract.get("mode") == "adaptive":
            return (
                f"choose whether to spawn admitted child lanes ({len(lanes)} eligible); "
                "the task coordinator reviews evidence and writes back this bundle once"
            )
        return (
            f"activate/resume peer lanes ({len(lanes)} eligible); "
            "the task coordinator reviews evidence and writes back this bundle"
        )
    if mode in {"user_gate", "user_todo_blocker_push", "user_action_required"}:
        return "wait for user/owner action after surfacing the blocker or gate"
    if mode == "outcome_floor_recovery":
        return "produce the required outcome-floor evidence artifact or write the concrete blocker"
    if mode == "capability_bridge_repair":
        return (
            "execute interaction_contract.agent_channel.next_task_action.operation "
            "once with its real task-facing tool and exact target_ref when projected; "
            "preflight_allowed is false, and the instruction is context rather than "
            "CLI text; on success "
            "execute cli_channel."
            "next_cli_actions[0] in the same turn and continue only when quota allows"
        )
    if mode == "agent_workspace_repair":
        return "create or switch to an independent worktree/branch, then rerun quota guard before file edits"
    if mode == "automation_prompt_upgrade":
        return (
            "update the installed automation once using the stable migration id, "
            "run its completion ack, then rerun quota guard"
        )
    if mode == "control_plane_self_repair":
        return "repair the bounded control-plane/status projection fault exposed by quota"
    if mode == "boundary_projection_repair":
        return "repair goal_boundary.write_scope projection before attempting the selected write"
    if mode == "bounded_delivery":
        return protocol_first_candidate_action(payload) or "advance scope-bounded work with validation"
    if mode == "mapped_noop_if_unchanged":
        return "confirm no new instruction/evidence/todo/stale source/safe handoff, then quiet no-op"
    if execution_obligation.get("contract_obligation"):
        return str(execution_obligation.get("contract_obligation"))
    return "do not run delivery work for this goal in this turn"


def build_primary_action_projection(payload: dict[str, Any], *, mode: str) -> dict[str, Any]:
    primary_action = resolve_canonical_primary_action(payload, mode=mode)
    projection: dict[str, Any] = {"primary_action": primary_action}
    resolution_trace = next_action_resolution_trace(
        primary_action=primary_action,
        mode=mode,
        active_state_next_action=payload.get("active_state_next_action"),
        latest_run_recommended_action=payload.get("latest_run_recommended_action"),
        selected_recommended_action=payload.get("recommended_action"),
        agent_lane_next_action=payload.get("agent_lane_next_action"),
    )
    if resolution_trace:
        projection["resolution_trace"] = resolution_trace
    return projection


def _interaction_must_attempt(
    execution_obligation: dict[str, Any],
    *,
    mode: str,
    user_required: bool,
    scoped_user_gate_fallback: bool,
    bounded_delivery_with_user_notice: bool,
) -> bool:
    if mode == "governed_capability_intent":
        return bool(execution_obligation.get("must_attempt_work"))
    if user_required and not (
        scoped_user_gate_fallback or bounded_delivery_with_user_notice
    ):
        return False
    return bool(execution_obligation.get("must_attempt_work"))


def _interaction_delivery_allowed(
    payload: dict[str, Any],
    execution_obligation: dict[str, Any],
    *,
    mode: str,
    user_required: bool,
    scoped_user_gate_fallback: bool,
    bounded_delivery_with_user_notice: bool,
) -> bool:
    if mode == "governed_capability_intent":
        return bool(execution_obligation.get("must_attempt_work"))
    if mode == "mapped_noop_if_unchanged":
        return False
    if user_required and not (
        scoped_user_gate_fallback or bounded_delivery_with_user_notice
    ):
        return False
    return bool(
        execution_obligation.get(
            "delivery_allowed",
            payload.get("normal_delivery_allowed")
            or payload.get("recovery_delivery_allowed")
            or payload.get("self_repair_allowed")
            or payload.get("should_run"),
        )
    )


def interaction_quiet_noop_allowed(
    *,
    mode: str,
    user_required: bool,
    must_attempt: bool,
) -> bool:
    if user_required or must_attempt:
        return False
    return _agent_scope_frontier_action(mode) is not None or mode in {
        "monitor_quiet_skip",
        "mapped_noop_if_unchanged",
        "quota_throttled",
        "blocked_wait",
        "user_gate_cooldown_wait",
        "terminal_no_followup",
        "peer_coordination_blocked",
        "agent_monitor_only",
        "skip",
    }


def interaction_execution_flags(
    payload: dict[str, Any], *, mode: str, user_required: bool,
    blocked_successor_wait_observation: bool,
) -> tuple[bool, bool]:
    execution_obligation = payload.get("execution_obligation") if isinstance(payload.get("execution_obligation"), dict) else {}
    scoped_user_gate_fallback = mode == "scoped_user_gate_fallback"
    bounded_delivery_with_user_notice = mode == "bounded_delivery_with_user_notice"
    must_attempt = _interaction_must_attempt(
        execution_obligation,
        mode=mode,
        user_required=user_required,
        scoped_user_gate_fallback=scoped_user_gate_fallback,
        bounded_delivery_with_user_notice=bounded_delivery_with_user_notice,
    )
    if mode == "automation_prompt_upgrade":
        must_attempt = True
    if blocked_successor_wait_observation:
        must_attempt = True
    delivery_allowed = _interaction_delivery_allowed(
        payload,
        execution_obligation,
        mode=mode,
        user_required=user_required,
        scoped_user_gate_fallback=scoped_user_gate_fallback,
        bounded_delivery_with_user_notice=bounded_delivery_with_user_notice,
    )
    return must_attempt, delivery_allowed
