from __future__ import annotations
from ..quota.blocked_transition_notice import blocked_priority_fallback_owner_reason
from ..quota.effective_action import EffectiveAction
import shlex
import typing
from collections.abc import Mapping
from typing import Any

from ..agents.agent_scope_frontier import AgentScopeFrontierAction, agent_scope_frontier_action as _agent_scope_frontier_action
from ..agents.capability_gate import runtime_capabilities_for_cli_projection
from ..goals.goal_frontier import AUTONOMOUS_REPLAN_REQUIRED_MODE
from ..goals.goal_vision_wait import exact_blocked_successor_wait_state
from ..quota.settlement import (
    SettlementStepKind,
    settlement_binding_args,
    settlement_step_command,
)
from ..quota.spend_sources import (
    build_quota_spend_action,
    host_goal_turn_reentry_action,
)
from ..scheduler.execution_context import (
    APP_HEARTBEAT_SETTLEMENT_RUNTIME_PROFILES,
    SchedulerExecutionContextResolution,
    render_scheduler_execution_args,
    scheduler_runtime_profile_for_execution_context,
)
from ..todos.contract import (
    TODO_TASK_CLASS_MONITOR,
    TODO_TASK_CLASS_USER_ACTION,
    normalize_todo_id,
    normalize_todo_replan_obligation_id,
)
from ..todos.todo_semantics import todo_item_task_class
from ..todos.user_gate import open_todo_count
from ..todos.write_hint import build_capability_resolution_writeback_actions
from .autonomous_replan_obligation import (
    build_autonomous_replan_cli_actions,
    replan_obligation_id_from_packet,
    todo_lifecycle_settlement_obligation,
)
from .accountable_settlement import build_accountable_work_item_settlement_plan
from . import action_selection_contract as selection
from . import runtime_capability_reentry as capability_reentry_adapter
from .primary_action import (
    build_primary_action_projection,
    protocol_action_label as _protocol_action_label,
    protocol_action_text,
    protocol_first_candidate_action as _protocol_first_candidate_action,
    protocol_monitor_action as _protocol_monitor_action,
)
from .replan_settlement import project_replan_settlement_contract
from .unsettled_host_turn_contract import recovery_cli_actions
from .user_action_frontier import user_action_owns_empty_agent_lane

INTERACTION_CONTRACT_SCHEMA_VERSION = "loopx_interaction_contract_v0"
INTERACTION_RESPONSE_PLAN_SCHEMA_VERSION = "interaction_response_plan_v0"
PROTOCOL_ACTION_PACKET_SCHEMA_VERSION = "protocol_action_packet_v0"
PROTOCOL_ACTION_PACKET_LLM_POLICY = "no_api"
AUXILIARY_MONITOR_POLL_CLI_SCHEMA_VERSION = "auxiliary_monitor_poll_cli_v0"
AUXILIARY_MONITOR_OBSERVATION_INPUT_SCHEMA_VERSION = (
    "auxiliary_monitor_observation_input_v0"
)
AUXILIARY_MONITOR_RESULT_HASH_ENV = "LOOPX_MONITOR_RESULT_HASH"


class _InteractionContractRequired(typing.TypedDict):
    schema_version: str
    mode: str
    user_channel: dict[str, Any]
    agent_channel: dict[str, Any]
    cli_channel: dict[str, Any]


class InteractionContractPacket(_InteractionContractRequired, total=False):
    response_plan: dict[str, Any]
    fallback_policy: dict[str, Any]


def _blocked_successor_wait_observation_required(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("effective_action")
        == AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value
        and exact_blocked_successor_wait_state(payload)
    )


def _user_todo_item_is_explicitly_non_gating(item: dict[str, Any]) -> bool:
    if item.get("gating") is False or item.get("non_gating") is True:
        return True
    if todo_item_task_class(item) in {
        TODO_TASK_CLASS_MONITOR,
        TODO_TASK_CLASS_USER_ACTION,
    }:
        return True
    action_kind = str(item.get("action_kind") or "").strip().lower()
    return action_kind in {
        "monitor",
        "observe",
        "watch",
        "fyi",
        "informational",
        "non_gating",
    }


def user_channel_action_todo_actions(summary: Any, *, limit: int = 3) -> list[str]:
    if not isinstance(summary, dict):
        return []
    first_open_items = summary.get("first_open_items")
    if not isinstance(first_open_items, list):
        return []
    actions: list[str] = []
    for item in first_open_items:
        if not isinstance(item, dict):
            continue
        if _user_todo_item_is_explicitly_non_gating(item):
            continue
        text = _protocol_action_label(item.get("text"))
        if not text:
            continue
        actions.append(text)
        if len(actions) >= limit:
            break
    return actions


def user_channel_notice_todo_actions(summary: Any, *, limit: int = 3) -> list[str]:
    if not isinstance(summary, dict):
        return []
    source_items = summary.get("user_action_items")
    if not isinstance(source_items, list):
        source_items = summary.get("first_open_items")
    if not isinstance(source_items, list):
        return []
    actions: list[str] = []
    for item in source_items:
        if not isinstance(item, dict):
            continue
        if todo_item_task_class(item) != TODO_TASK_CLASS_USER_ACTION:
            continue
        text = _protocol_action_label(item.get("text"))
        if not text:
            continue
        actions.append(text)
        if len(actions) >= limit:
            break
    return actions


def user_channel_action_required(payload: dict[str, Any]) -> bool:
    if payload.get("agent_work_mode") == "monitor_only":
        return False
    if _user_gate_scope_projection_repair_active(payload):
        return False
    if _user_gate_notification_suppressed(payload):
        return False
    obligation = payload.get("autonomous_replan_obligation")
    if isinstance(obligation, dict) and obligation.get("required") is True:
        # An active replan obligation owns the frontier; a non-blocking user
        # action must not mask the agent-side work it obligates.
        return False
    return bool(payload.get("requires_user_action")) or bool(
        user_channel_action_todo_actions(payload.get("user_todo_summary"))
    ) or user_action_owns_empty_agent_lane(payload)


def _user_gate_notification_suppressed(payload: dict[str, Any]) -> bool:
    cooldown = payload.get("user_gate_notification_cooldown")
    return isinstance(cooldown, dict) and cooldown.get("notification_suppressed") is True


def _user_gate_scope_projection_repair_active(payload: dict[str, Any]) -> bool:
    repair = payload.get("stall_self_repair")
    return bool(
        isinstance(repair, dict)
        and repair.get("trigger")
        in {
            "user_gate_scope_projection_drift",
            "runtime_capability_user_gate_overreach",
        }
    )


def _capability_resolution_user_actions(payload: dict[str, Any]) -> list[str]:
    capability_gate = (
        payload.get("capability_gate")
        if isinstance(payload.get("capability_gate"), dict)
        else {}
    )
    if not capability_gate.get("owner_missing"):
        return []
    owner_action = protocol_action_text(capability_gate.get("owner_action"))
    return [owner_action] if owner_action else []


def finalize_user_gate_notification_cooldown(
    payload: dict[str, Any],
    *,
    available_capabilities: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    turn_instance_id: str | None = None,
    runtime_root: str | None = None,
) -> None:
    scheduler_hint = payload.get("scheduler_hint")
    cooldown = (
        scheduler_hint.get("user_gate_notification_cooldown")
        if isinstance(scheduler_hint, dict)
        else None
    )
    if isinstance(cooldown, dict):
        payload["user_gate_notification_cooldown"] = dict(cooldown)
    if _user_gate_notification_suppressed(payload):
        payload["pending_user_action"] = bool(
            payload.get("requires_user_action")
            or user_channel_action_todo_actions(payload.get("user_todo_summary"))
        )
        payload["requires_user_action"] = False
    payload["interaction_contract"] = build_interaction_contract(
        payload,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
        runtime_root=runtime_root,
    )
    attach_user_action_compat_fields(payload)


def projected_user_channel_actions(
    payload: dict[str, Any],
    *,
    limit: int = 3,
) -> list[str]:
    if payload.get("agent_work_mode") == "monitor_only":
        return []
    if _user_gate_scope_projection_repair_active(payload):
        return []
    if _user_gate_notification_suppressed(payload):
        return []
    actions = user_channel_action_todo_actions(
        payload.get("user_todo_summary"),
        limit=limit,
    )
    if actions:
        return actions
    notices = user_channel_notice_todo_actions(
        payload.get("user_todo_summary"),
        limit=limit,
    )
    if notices:
        return notices
    capability_actions = _capability_resolution_user_actions(payload)
    if capability_actions:
        return capability_actions[:limit]
    if not user_channel_action_required(payload):
        return []
    capability_gate = (
        payload.get("capability_gate")
        if isinstance(payload.get("capability_gate"), dict)
        else {}
    )
    if capability_gate.get("owner_missing"):
        owner_action = protocol_action_text(capability_gate.get("owner_action"))
        if owner_action:
            return [owner_action]
    for key in ("gate_prompt", "operator_question", "open_todo_notify_reason"):
        text = protocol_action_text(payload.get(key))
        if text:
            return [text]
    return []


def attach_user_action_compat_fields(payload: dict[str, Any]) -> None:
    action_required = user_channel_action_required(payload)
    payload["requires_user_action"] = action_required
    payload["action_required"] = action_required
    if _user_gate_scope_projection_repair_active(payload):
        for key in ("notify_user_on_gate", "gate_prompt", "open_todo_notify_reason"):
            payload.pop(key, None)
    payload["open_count"] = open_todo_count(payload.get("user_todo_summary"))


def protocol_action_packet_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the ordered semantic fields rendered by protocol_action_packet_v0."""

    execution_obligation = (
        payload.get("execution_obligation")
        if isinstance(payload.get("execution_obligation"), dict)
        else {}
    )
    work_lane = (
        payload.get("work_lane_contract")
        if isinstance(payload.get("work_lane_contract"), dict)
        else {}
    )
    automation_liveness = (
        payload.get("automation_liveness")
        if isinstance(payload.get("automation_liveness"), dict)
        else {}
    )
    scheduler_hint = (
        payload.get("scheduler_hint")
        if isinstance(payload.get("scheduler_hint"), dict)
        else {}
    )
    requires_user_action = user_channel_action_required(payload)
    blocked_successor_wait_observation = (
        _blocked_successor_wait_observation_required(payload)
    )
    must_attempt_work = bool(execution_obligation.get("must_attempt_work")) or (
        blocked_successor_wait_observation
    )
    scoped_user_gate_fallback = isinstance(payload.get("scoped_user_gate_fallback"), dict)
    bounded_delivery_with_user_notice = (
        requires_user_action
        and not scoped_user_gate_fallback
        and must_attempt_work
        and bool(
            execution_obligation.get(
                "delivery_allowed",
                payload.get("normal_delivery_allowed")
                or payload.get("recovery_delivery_allowed")
                or payload.get("self_repair_allowed")
                or payload.get("should_run"),
            )
        )
    )
    quiet_noop_allowed = (
        not requires_user_action
        and not must_attempt_work
        and not scoped_user_gate_fallback
    )

    user_actions = projected_user_channel_actions(payload)

    if requires_user_action and scoped_user_gate_fallback:
        primary_actor = "agent_with_user_gate"
        agent_action_required = True
        agent_action = _protocol_first_candidate_action(payload) or (
            "surface the scoped user gate, then advance one non-gated fallback"
        )
    elif bounded_delivery_with_user_notice:
        primary_actor = "agent_with_user_gate"
        agent_action_required = True
        agent_action = _protocol_first_candidate_action(payload) or "advance scope-bounded work with validation"
    elif requires_user_action:
        primary_actor = "user"
        agent_action_required = False
        capability_gate = (
            payload.get("capability_gate")
            if isinstance(payload.get("capability_gate"), dict)
            else {}
        )
        agent_action = (
            capability_gate.get("owner_action")
            if capability_gate.get("action") == "ask_owner"
            and capability_gate.get("owner_action")
            else "wait for user/owner action after surfacing the blocker or gate"
        )
    elif blocked_successor_wait_observation:
        primary_actor = "agent"
        agent_action_required = True
        agent_action = (
            "record one no-spend blocked-successor wait observation, then rerun quota"
        )
    elif must_attempt_work and payload.get("agent_work_mode") == "monitor_only":
        primary_actor = "agent"
        agent_action_required = True
        agent_action = (
            protocol_action_text(work_lane.get("action"), limit=220)
            or "attempt the due monitor and write back only a material transition"
        )
    elif must_attempt_work:
        primary_actor = "agent"
        agent_action_required = True
        if str(execution_obligation.get("kind") or "") == "outcome_floor_recovery":
            agent_action = (
                "produce the required outcome-floor evidence artifact or write "
                "the concrete blocker"
            )
        else:
            agent_action = _protocol_first_candidate_action(payload) or "advance scope-bounded work with validation"
    else:
        primary_actor = "agent"
        agent_action_required = False
        agent_action = _protocol_monitor_action(payload) or "quiet no-op; no material transition"

    action_key = (
        "agent_action"
        if agent_action_required
        else "user_action"
        if requires_user_action
        else "agent_action"
    )
    action_value = (
        agent_action
        if agent_action_required
        else user_actions[0]
        if requires_user_action and user_actions
        else agent_action
    )
    fields: dict[str, Any] = {
        "actor": primary_actor,
        "user_action_required": requires_user_action,
        "agent_action_required": agent_action_required,
        "quiet_noop_allowed": quiet_noop_allowed,
    }
    if work_lane.get("lane"):
        fields["lane"] = work_lane.get("lane")
    if automation_liveness.get("automation_action"):
        fields["automation"] = automation_liveness.get("automation_action")
    if scheduler_hint.get("action"):
        fields["scheduler"] = scheduler_hint.get("action")
    if automation_liveness.get("pause_allowed") is False:
        fields["pause_allowed"] = False
    fields["llm"] = PROTOCOL_ACTION_PACKET_LLM_POLICY
    if user_actions and (not requires_user_action or action_key != "user_action"):
        fields["user_action_pending"] = True
        text = protocol_action_text(user_actions[0], limit=80)
        if text:
            fields["user_action"] = text
    text = protocol_action_text(action_value, limit=80)
    if text:
        fields[action_key] = text
    return fields


def render_protocol_action_packet_summary(fields: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if isinstance(value, bool):
            rendered = str(value).lower()
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return " ".join(parts)


def build_protocol_action_packet(payload: dict[str, Any]) -> dict[str, Any]:
    fields = protocol_action_packet_fields(payload)
    return {
        "schema_version": PROTOCOL_ACTION_PACKET_SCHEMA_VERSION,
        "summary": render_protocol_action_packet_summary(fields),
    }


def _interaction_mode(payload: dict[str, Any]) -> str:
    execution_obligation = (
        payload.get("execution_obligation")
        if isinstance(payload.get("execution_obligation"), dict)
        else {}
    )
    heartbeat_recommendation = (
        payload.get("heartbeat_recommendation")
        if isinstance(payload.get("heartbeat_recommendation"), dict)
        else {}
    )
    kind = str(execution_obligation.get("kind") or "")
    effective_action = str(payload.get("effective_action") or "")
    state = str(payload.get("state") or "")
    if effective_action == EffectiveAction.GOVERNED_CAPABILITY_INTENT.value:
        return effective_action
    if effective_action == EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value:
        return effective_action
    if effective_action == EffectiveAction.AGENT_MONITOR_ONLY.value:
        return "agent_monitor_only"
    if effective_action == EffectiveAction.MONITOR_DUE.value:
        return "monitor_due"
    if effective_action == EffectiveAction.TERMINAL_NO_FOLLOWUP.value or state == "terminal_no_followup":
        return "terminal_no_followup"
    if effective_action == EffectiveAction.PEER_COORDINATION_BLOCKED.value:
        return effective_action
    if payload.get("scoped_user_gate_fallback"):
        return "scoped_user_gate_fallback"
    if _user_gate_notification_suppressed(payload):
        return "user_gate_cooldown_wait"
    if effective_action == EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED.value:
        return "automation_prompt_upgrade"
    if user_channel_action_required(payload):
        if (
            bool(execution_obligation.get("must_attempt_work"))
            and bool(
                execution_obligation.get(
                    "delivery_allowed",
                    payload.get("normal_delivery_allowed")
                    or payload.get("recovery_delivery_allowed")
                    or payload.get("self_repair_allowed")
                    or payload.get("should_run"),
                )
            )
        ):
            return "bounded_delivery_with_user_notice"
        if payload.get("notify_user_on_gate") or state == "operator_gate":
            return "user_gate"
        if payload.get("notify_user_on_open_todo"):
            return "user_todo_blocker_push"
        return "user_action_required"
    if kind == "external_evidence_observation_required":
        return "external_evidence_observation"
    if kind == AUTONOMOUS_REPLAN_REQUIRED_MODE:
        return "autonomous_replan"
    if effective_action == EffectiveAction.COORDINATE_TASK_BUNDLE.value:
        return "task_orchestration"
    agent_scope_action = _agent_scope_frontier_action(effective_action)
    if agent_scope_action is not None:
        return agent_scope_action.value
    if effective_action == EffectiveAction.MONITOR_QUIET_SKIP.value:
        return "monitor_quiet_skip"
    if effective_action == EffectiveAction.HEARTBEAT_SETTLED_SKIP.value:
        return "heartbeat_settled_skip"
    if payload.get("recovery_delivery_allowed") or effective_action == EffectiveAction.OUTCOME_FLOOR_RECOVERY.value:
        return "outcome_floor_recovery"
    if effective_action == EffectiveAction.CAPABILITY_BRIDGE_REPAIR.value:
        return "capability_bridge_repair"
    if effective_action == EffectiveAction.AGENT_WORKSPACE_REPAIR.value:
        return effective_action
    if effective_action == EffectiveAction.BOUNDARY_PROJECTION_REPAIR.value:
        return "boundary_projection_repair"
    if payload.get("self_repair_allowed"):
        return "control_plane_self_repair"
    if heartbeat_recommendation.get("stop_if_unchanged"):
        return "mapped_noop_if_unchanged"
    if payload.get("normal_delivery_allowed") or payload.get("should_run"):
        return "bounded_delivery"
    if state == "blocked_health":
        return "health_blocked"
    if state == "throttled":
        return "quota_throttled"
    if state in {"waiting", "focus_wait"}:
        return "blocked_wait"
    return "skip"


def _scoped_cli_args(
    agent_identity: dict[str, Any],
    *,
    available_capabilities: Any,
) -> str:
    agent_id = str(agent_identity.get("agent_id") or "").strip()
    if not agent_id:
        return ""
    capability_args = "".join(
        f" --available-capability {shlex.quote(capability)}"
        for capability in runtime_capabilities_for_cli_projection(
            available_capabilities
        )
    )
    return f" --agent-id {agent_id}{capability_args}"


def _turn_scoped_cli_settlement_context(
    payload: dict[str, Any],
    *,
    available_capabilities: Any,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ),
    turn_instance_id: str | None = None,
    runtime_root: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if selection.action_portfolio_requires_explicit_selection(payload):
        return None, None
    agent_identity = payload.get("agent_identity") if isinstance(payload.get("agent_identity"), dict) else {}
    selected_todo = (
        payload.get("selected_todo")
        if isinstance(payload.get("selected_todo"), dict)
        else {}
    )
    goal_id = str(payload.get("goal_id") or "").strip()
    agent_id = str(agent_identity.get("agent_id") or "").strip()
    selected_todo_id = normalize_todo_id(selected_todo.get("todo_id"))
    semantic_replan_obligation_id = replan_obligation_id_from_packet(
        payload.get("replan_action_packet")
    )
    replan_settlement_contract = None
    todo_id = selected_todo_id
    replan_obligation_id = None
    if semantic_replan_obligation_id is not None:
        replan_settlement_contract = project_replan_settlement_contract(
            selected_todo_id=selected_todo_id,
            semantic_replan_obligation_id=semantic_replan_obligation_id,
        )
        binding = replan_settlement_contract["settlement_binding"]
        if binding["kind"] == "todo":
            todo_id = normalize_todo_id(binding["id"])
        else:
            todo_id = None
            replan_obligation_id = normalize_todo_replan_obligation_id(
                binding["id"]
            )
    if not goal_id or not agent_id or bool(todo_id) == bool(replan_obligation_id):
        return None, replan_settlement_contract
    scoped_cli_args = _scoped_cli_args(
        agent_identity,
        available_capabilities=available_capabilities,
    )
    plan = build_accountable_work_item_settlement_plan(
        runtime_profile=scheduler_runtime_profile_for_execution_context(
            scheduler_execution_context
        ),
        goal_id=goal_id,
        agent_id=agent_id,
        command_prefix=selection.render_cli_command_prefix(runtime_root=runtime_root),
        todo_id=todo_id,
        replan_obligation_id=replan_obligation_id,
        scoped_cli_args=scoped_cli_args,
        lifecycle_actor_args=f" --agent-id {shlex.quote(agent_id)}",
        turn_instance_id=turn_instance_id,
        delivery_boundary=(
            str(selected_todo.get("delivery_boundary"))
            if selected_todo.get("delivery_boundary")
            == "in_flight_continuation"
            else None
        ),
    )
    return (
        plan.as_dict() if plan is not None else None,
        replan_settlement_contract,
    )


def _terminal_cli_actions(
    *,
    mode: str,
    goal_id: str,
    scoped_cli_args: str,
    settlement_plan: Mapping[str, Any] | None,
    quota_spend_action: str,
    capability_resolution_actions: list[str],
    capability_reentry_actions: list[str],
    command_prefix: str,
) -> list[str]:
    if mode == "capability_bridge_repair":
        return capability_reentry_actions or [
            "perform the projected real task-facing capability check, then rerun "
            "quota in this same turn"
        ]
    resolution_actions = [
        *capability_resolution_actions,
        *capability_reentry_actions,
    ]
    if mode in {
        "bounded_delivery",
        "outcome_floor_recovery",
        "control_plane_self_repair",
        "boundary_projection_repair",
        "scoped_user_gate_fallback",
        "bounded_delivery_with_user_notice",
    }:
        typed_writeback = settlement_step_command(
            settlement_plan,
            SettlementStepKind.DURABLE_WRITEBACK,
        )
        return [
            *resolution_actions,
            typed_writeback
            or f"{command_prefix} refresh-state --goal-id {goal_id} --classification <validated_progress>{scoped_cli_args}",
            quota_spend_action,
        ]
    if mode in {"user_gate", "user_todo_blocker_push", "user_action_required"}:
        return [
            *resolution_actions,
            "no quota spend for blocker-push/gate-notification",
        ]
    return ["no quota spend without validated transition/blocker writeback"]


def interaction_next_cli_actions(
    payload: dict[str, Any],
    *,
    mode: str,
    available_capabilities: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    capability_reentry: dict[str, Any] | None = None,
    capability_reentry_resolved: bool = False,
    settlement_plan: Mapping[str, Any] | None = None,
    turn_instance_id: str | None = None,
    runtime_root: str | None = None,
) -> list[str]:
    goal_id = str(payload.get("goal_id") or "<GOAL_ID>")
    command_prefix = selection.render_cli_command_prefix(runtime_root=runtime_root)
    agent_identity = payload.get("agent_identity") if isinstance(payload.get("agent_identity"), dict) else {}
    scoped_cli_args = _scoped_cli_args(
        agent_identity,
        available_capabilities=available_capabilities,
    )
    lifecycle_actor_args = (
        f" --agent-id {shlex.quote(str(agent_identity.get('agent_id')).strip())}"
        if agent_identity.get("agent_id")
        else ""
    )
    if settlement_plan is None:
        settlement_plan, _replan_settlement_contract = _turn_scoped_cli_settlement_context(
            payload,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_execution_context,
            turn_instance_id=turn_instance_id,
            runtime_root=runtime_root,
        )
    settlement_args = settlement_binding_args(settlement_plan)
    try:
        scheduler_args = render_scheduler_execution_args(
            scheduler_execution_context=scheduler_execution_context,
        )
    except ValueError:
        scheduler_args = ""
    selection_command_template = selection.action_portfolio_selection_command_template(
        payload,
        scoped_cli_args=scoped_cli_args,
        scheduler_args=scheduler_args,
        turn_instance_id=turn_instance_id,
        runtime_root=runtime_root,
    )
    if selection_command_template:
        return [selection_command_template]
    typed_quota_guard = (
        f"{command_prefix} --format json quota should-run --goal-id {goal_id}"
        f"{scoped_cli_args}{scheduler_args}"
        if scheduler_args
        else "rerun the typed quota_guard from the current host packet"
    )
    if turn_reentry_action := host_goal_turn_reentry_action(
        payload, settlement_plan, scheduler_execution_context, turn_instance_id, typed_quota_guard
    ):
        return [turn_reentry_action]
    typed_monitor_poll = (
        f"{command_prefix} quota monitor-poll --goal-id {goal_id}{scoped_cli_args}"
        f"{scheduler_args} --execute"
        if scheduler_args
        else "use the current host packet's typed monitor command"
    )
    typed_heartbeat_receipt_retry = (
        f"on missing/write_failed heartbeat_receipt only: {typed_quota_guard} "
        '--turn-instance-id "${LOOPX_TURN:?}"'
        if scheduler_args
        else (
            "on missing/write_failed heartbeat_receipt only: retry the current "
            "host packet's typed quota guard with the same heartbeat turn id"
        )
    )
    heartbeat_turn_receipt_enabled = (
        scheduler_runtime_profile_for_execution_context(
            scheduler_execution_context
        )
        in APP_HEARTBEAT_SETTLEMENT_RUNTIME_PROFILES
    )
    if mode == "governed_capability_intent":
        projection = (
            payload.get("pending_capability_intent")
            if isinstance(payload.get("pending_capability_intent"), Mapping)
            else {}
        )
        command = protocol_action_text(projection.get("command"), limit=1200)
        return [command] if command else []
    quota_spend_action = build_quota_spend_action(
        goal_id,
        scoped_cli_args=scoped_cli_args,
        payload=payload,
        settlement_plan=settlement_plan,
        scheduler_execution_context=scheduler_execution_context,
        command_prefix=command_prefix,
    )
    capability_resolution_actions = build_capability_resolution_writeback_actions(
        payload.get("capability_gate"),
        goal_id=goal_id,
        agent_id=(
            str(agent_identity.get("agent_id"))
            if agent_identity.get("agent_id")
            else None
        ),
    )
    if capability_reentry is None and not capability_reentry_resolved:
        capability_reentry = capability_reentry_adapter.build_runtime_capability_reentry_packet(
            payload,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_execution_context,
            turn_instance_id=turn_instance_id,
            runtime_root=runtime_root,
        )
    capability_reentry_actions = (
        [str(candidate["command"]) for candidate in capability_reentry["candidates"]]
        if capability_reentry is not None
        else []
    )
    if mode == "terminal_no_followup":
        return ["no quota spend until explicit goal resume or newly projected work"]
    if mode == "agent_monitor_only":
        return [
            "no quota spend until a due monitor, verified direct reply, or explicit work-mode change"
        ]
    if mode == "automation_prompt_upgrade":
        automation_prompt_upgrade = (
            payload.get("automation_prompt_upgrade")
            if isinstance(payload.get("automation_prompt_upgrade"), dict)
            else {}
        )
        actions = [
            str(item.get("command") or "").strip()
            for item in automation_prompt_upgrade.get("agent_example_commands") or []
            if isinstance(item, dict)
        ]
        completion_command = str(
            automation_prompt_upgrade.get("completion_command") or ""
        ).strip()
        if completion_command:
            actions.append(completion_command)
        return [action for action in actions if action] or [
            f"{command_prefix} heartbeat-prompt --thin --goal-id {goal_id} --agent-id <registered-agent> --agent-scope '<scope>'",
        ]
    if mode == "monitor_quiet_skip":
        if heartbeat_turn_receipt_enabled:
            return [typed_heartbeat_receipt_retry]
        return [
            typed_monitor_poll,
            typed_quota_guard,
        ]
    if mode == "heartbeat_settled_skip":
        return [
            (
                "finish this heartbeat without another poll, writeback, or quota spend; "
                "the next scheduler trigger must use a fresh turn identity"
            )
        ]
    if mode == "monitor_due":
        return [
            typed_monitor_poll,
            typed_quota_guard,
        ]
    task_orchestration_contract = payload.get("task_orchestration_contract")
    if (
        mode == "task_orchestration"
        and isinstance(task_orchestration_contract, Mapping)
        and task_orchestration_contract.get("mode") == "adaptive"
    ):
        return [typed_quota_guard]
    if mode == AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED.value:
        agent_scope_frontier = (
            payload.get("agent_scope_frontier")
            if isinstance(payload.get("agent_scope_frontier"), dict)
            else {}
        )
        monitor_candidates = (
            agent_scope_frontier.get("monitor_blocked_resume_candidates")
            if isinstance(agent_scope_frontier.get("monitor_blocked_resume_candidates"), list)
            else []
        )
        if monitor_candidates:
            first_candidate = (
                monitor_candidates[0]
                if isinstance(monitor_candidates[0], dict)
                else {}
            )
            monitor_todo_id = str(
                first_candidate.get("blocking_monitor_todo_id") or "<monitor_todo_id>"
            )
            gated_todo_id = str(first_candidate.get("todo_id") or "<gated_todo_id>")
            return [
                f"{command_prefix} todo complete --goal-id {goal_id} --todo-id {monitor_todo_id}{lifecycle_actor_args} --evidence '<validated gate evidence>'",
                f"{command_prefix} todo update --goal-id {goal_id} --todo-id {gated_todo_id}{lifecycle_actor_args} --note '<public-safe gate repair reason>'",
                f"{command_prefix} refresh-state --goal-id {goal_id} --classification standing_monitor_gate_repair_recorded --delivery-batch-scale single_surface --delivery-outcome outcome_progress{settlement_args}{scoped_cli_args}",
                quota_spend_action,
            ]
        route_candidates = (
            agent_scope_frontier.get("route_continuation_replan_candidates")
            if isinstance(agent_scope_frontier.get("route_continuation_replan_candidates"), list)
            else []
        )
        if route_candidates:
            return [
                f"{command_prefix} todo add --goal-id {goal_id} --role agent --text '<public-safe route continuation advancement todo>'",
                f"{command_prefix} refresh-state --goal-id {goal_id} --classification route_continuation_replan_recorded --delivery-batch-scale single_surface --delivery-outcome outcome_progress{settlement_args}{scoped_cli_args}",
                quota_spend_action,
            ]
        candidates = (
            agent_scope_frontier.get("deferred_resume_candidates")
            if isinstance(agent_scope_frontier.get("deferred_resume_candidates"), list)
            else []
        )
        first_candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        todo_id = str(first_candidate.get("todo_id") or "<todo_id>")
        return [
            (
            f"{command_prefix} todo update --goal-id {goal_id} --todo-id {todo_id}"
            f"{lifecycle_actor_args} --status open --clear-resume-when "
            "--note '<public-safe successor replan reason>'"
            ),
            f"{command_prefix} refresh-state --goal-id {goal_id} --classification successor_replan_recorded --delivery-batch-scale single_surface --delivery-outcome outcome_progress{settlement_args}{scoped_cli_args}",
            quota_spend_action,
        ]
    if (
        mode == AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value
        and _blocked_successor_wait_observation_required(payload)
    ):
        return [
            typed_monitor_poll,
            typed_quota_guard,
        ]
    if _agent_scope_frontier_action(mode) is not None:
        return [
            "no quota spend while this agent has no in-scope runnable candidate",
            typed_quota_guard,
        ]
    if mode == "external_evidence_observation":
        return [
            "read approved controller/job/marker/writeback surfaces only",
            (
                "on a substantive transition or blocker only: "
                f"{command_prefix} refresh-state --goal-id {goal_id} "
                "--classification <compact_blocker_or_transition> "
                "--delivery-batch-scale <scale> --delivery-outcome <outcome>"
                f"{settlement_args}{scoped_cli_args}"
            ),
            (
                "after that accountable writeback receipt only: "
                f"{quota_spend_action}; otherwise do not spend for unchanged observation"
            ),
        ]
    if mode == "agent_workspace_repair":
        return [
            "create or switch to an independent git worktree/branch",
            typed_quota_guard,
        ]
    if mode == "unsettled_host_turn_recovery":
        return recovery_cli_actions(
            payload,
            command_prefix=command_prefix,
            goal_id=goal_id,
            lifecycle_actor_args=lifecycle_actor_args,
            typed_quota_guard=typed_quota_guard,
            turn_instance_id=turn_instance_id,
        )
    if mode == "autonomous_replan":
        return build_autonomous_replan_cli_actions(
            payload,
            goal_id=goal_id,
            settlement_args=settlement_args,
            scoped_cli_args=scoped_cli_args,
            quota_spend_action=quota_spend_action,
            settlement_chain_ready=settlement_plan is not None,
            command_prefix=command_prefix,
            lifecycle_actor_args=lifecycle_actor_args,
            runtime_root=runtime_root,
        )
    return _terminal_cli_actions(
        mode=mode,
        goal_id=goal_id,
        scoped_cli_args=scoped_cli_args,
        settlement_plan=settlement_plan,
        quota_spend_action=quota_spend_action,
        capability_resolution_actions=capability_resolution_actions,
        capability_reentry_actions=capability_reentry_actions,
        command_prefix=command_prefix,
    )


def _interaction_required_reads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    reads = payload.get("required_reads")
    if not isinstance(reads, list):
        return []
    result: list[dict[str, Any]] = []
    for item in reads:
        if not isinstance(item, dict):
            continue
        command = protocol_action_text(item.get("command"), limit=(
            item.get("prompt_budget_bytes", 360) if item.get("source") == "turn_start_capability_hook" else 360))
        if not command:
            continue
        result.append({**item, "command": command})
    return result


def _interaction_spend_policy(
    execution_obligation: dict[str, Any],
    heartbeat_recommendation: dict[str, Any],
    *,
    mode: str,
    spend_after_validation: bool,
) -> str | None:
    if mode == "governed_capability_intent":
        return "the capability consumer persists its own idempotent receipt"
    if mode == "terminal_no_followup":
        return "no spend for terminal automation shutdown"
    if mode == "agent_monitor_only":
        return "no spend while advancement is paused and no monitor is due"
    if mode in {"user_gate", "user_todo_blocker_push", "user_action_required"}:
        return "no spend for gate or blocker push"
    if mode == "monitor_quiet_skip":
        return "no spend for unchanged heartbeat stall receipt"
    if mode == "heartbeat_settled_skip":
        return "no spend for an already-settled heartbeat turn"
    if mode == "monitor_due":
        return (
            "spend once only after a validated material monitor transition; "
            "unchanged monitor polls are no-spend"
        )
    if mode == AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED.value:
        return "spend once after validated successor replan/todo writeback"
    if _agent_scope_frontier_action(mode) is not None:
        return "no spend while the current agent has no in-scope runnable candidate"
    if mode == "agent_workspace_repair":
        return "no spend for moving agent work into an independent worktree"
    if mode == "unsettled_host_turn_recovery":
        return "no spend for repairing the prior Turn closeout"
    if mode == "automation_prompt_upgrade":
        return "no spend until the host update is acknowledged and quota reruns"
    if mode == "capability_bridge_repair":
        return (
            "no spend or advancement checkpoint for capability verification; rerun "
            "quota in the same turn"
        )
    if mode == "autonomous_replan" and not spend_after_validation:
        return "no spend for Todo lifecycle settlement; rerun quota after the transition"
    if mode == "autonomous_replan":
        return (
            "spend only after accountable replan delta; no spend for "
            "surface_only watch-lane continuation"
        )
    if spend_after_validation:
        return "spend once after validated writeback"
    raw_policy = execution_obligation.get("spend_policy") or heartbeat_recommendation.get(
        "spend_policy"
    )
    if isinstance(raw_policy, str) and len(raw_policy) <= 80:
        return raw_policy
    return "no spend without validated transition"


def _blocked_priority_fallback_user_reason(payload: dict[str, Any]) -> str | None:
    fallback = (
        payload.get("blocked_priority_fallback")
        if isinstance(payload.get("blocked_priority_fallback"), dict)
        else {}
    )
    if not fallback:
        return None
    if (
        fallback.get("requires_user_action") is not True
        and fallback.get("notify_user") is not True
    ):
        return None
    # The typed notice carries what #4381 asks the owner to be told — the task,
    # the concrete cause, the impact, who can resolve it, the recovery
    # condition and the next action — and it takes precedence over the generic
    # fallback prose. The rendering lives with the notice contract.
    return blocked_priority_fallback_owner_reason(fallback)


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


def _interaction_quiet_noop_allowed(
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


def _interaction_spend_after_validation(mode: str) -> bool:
    return mode in {
        "bounded_delivery",
        "outcome_floor_recovery",
        "autonomous_replan",
        "control_plane_self_repair",
        "boundary_projection_repair",
        "external_evidence_observation",
        "monitor_due",
        "task_orchestration",
        AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED.value,
        "scoped_user_gate_fallback",
        "bounded_delivery_with_user_notice",
    }


def _interaction_user_reason(payload: dict[str, Any]) -> Any:
    return (
        (
            payload.get("stall_self_repair", {}).get("reason")
            if _user_gate_scope_projection_repair_active(payload)
            and isinstance(payload.get("stall_self_repair"), dict)
            else None
        )
        or (
            payload.get("user_gate_notification_cooldown", {}).get("reason")
            if _user_gate_notification_suppressed(payload)
            else None
        )
        or payload.get("open_todo_notify_reason")
        or payload.get("gate_prompt")
        or payload.get("operator_question")
        or _blocked_priority_fallback_user_reason(payload)
        or (
            payload.get("scoped_user_gate_fallback", {}).get("reason")
            if isinstance(payload.get("scoped_user_gate_fallback"), dict)
            else None
        )
        or (
            "open user todo requires user-visible follow-up while independent "
            "agent work may continue"
            if user_channel_action_todo_actions(payload.get("user_todo_summary"))
            else None
        )
        or (
            "open non-blocking user action should be surfaced while independent "
            "agent work continues"
            if user_channel_notice_todo_actions(payload.get("user_todo_summary"))
            else None
        )
        or (
            payload.get("agent_scope_frontier", {}).get("reason")
            if isinstance(payload.get("agent_scope_frontier"), dict)
            else None
        )
        or (
            payload.get("capability_gate", {}).get("reason")
            if isinstance(payload.get("capability_gate"), dict)
            else None
        )
    )


def _build_interaction_agent_channel(
    payload: dict[str, Any],
    *,
    mode: str,
    must_attempt: bool,
    delivery_allowed: bool,
    quiet_noop_allowed: bool,
    capability_reentry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    channel: dict[str, Any] = {
        "must_attempt": must_attempt,
        "delivery_allowed": delivery_allowed,
        "quiet_noop_allowed": quiet_noop_allowed,
    }
    channel.update(build_primary_action_projection(payload, mode=mode))
    work_lane = (
        payload.get("work_lane_contract")
        if isinstance(payload.get("work_lane_contract"), Mapping)
        else {}
    )
    if isinstance(work_lane.get("auxiliary_monitor_poll"), Mapping):
        channel["auxiliary_monitor_poll"] = dict(
            work_lane["auxiliary_monitor_poll"]
        )
    if isinstance(payload.get("action_portfolio"), dict):
        channel["action_portfolio_ref"] = "$.action_portfolio"
    selection.apply_action_selection_agent_gate(channel, payload)
    if capability_reentry is not None:
        capability_reentry_adapter.apply_agent_channel_projection(
            channel,
            capability_reentry,
            selection_required=selection.action_portfolio_requires_explicit_selection(payload),
        )
    if _blocked_successor_wait_observation_required(payload):
        channel["primary_action"] = (
            "record one no-spend blocked-successor wait observation, rerun quota, "
            "then replan after the second unchanged frontier"
        )
    return channel


def _build_interaction_user_channel(
    payload: dict[str, Any],
    heartbeat_recommendation: dict[str, Any],
    *,
    user_required: bool,
) -> dict[str, Any]:
    actions = projected_user_channel_actions(payload, limit=3)
    notification_suppressed = _user_gate_notification_suppressed(payload)
    non_blocking_notice = bool(
        not user_required
        and not notification_suppressed
        and user_channel_notice_todo_actions(payload.get("user_todo_summary"), limit=3)
    )
    notify = heartbeat_recommendation.get("notify", "DONT_NOTIFY")
    if user_required or non_blocking_notice:
        notify = "NOTIFY"
    if notification_suppressed or _user_gate_scope_projection_repair_active(payload):
        notify = "DONT_NOTIFY"
    channel: dict[str, Any] = {
        "action_required": user_required,
        "notify": notify,
    }
    if actions:
        channel["max_items"] = 3
        channel["actions"] = actions
    if non_blocking_notice:
        channel["non_blocking"] = True
    reason = _interaction_user_reason(payload)
    if reason:
        channel["reason"] = reason
    return channel


def _build_interaction_response_plan(
    *,
    user_channel: dict[str, Any],
    agent_channel: dict[str, Any],
) -> dict[str, Any] | None:
    """Project the exact host-visible response for a blocking user gate."""

    if not (
        user_channel.get("action_required") is True
        and user_channel.get("notify") == "NOTIFY"
        and agent_channel.get("must_attempt") is False
        and agent_channel.get("delivery_allowed") is False
        and agent_channel.get("quiet_noop_allowed") is False
    ):
        return None
    return {
        "schema_version": INTERACTION_RESPONSE_PLAN_SCHEMA_VERSION,
        "kind": "surface_user_gate",
        "decision": "ask_user",
        "action_sequence": ["notify", "wait"],
        "silent_wait_allowed": False,
    }


def _build_interaction_cli_channel(
    payload: dict[str, Any],
    execution_obligation: dict[str, Any],
    heartbeat_recommendation: dict[str, Any],
    *,
    mode: str,
    spend_after_validation: bool,
    available_capabilities: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    capability_reentry: dict[str, Any] | None = None,
    turn_instance_id: str | None = None,
    runtime_root: str | None = None,
) -> dict[str, Any]:
    spend_after_selection = selection.delivery_spend_allowed(payload, spend_after_validation)
    settlement_plan, replan_settlement_contract = (
        _turn_scoped_cli_settlement_context(
            payload,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_execution_context,
            turn_instance_id=turn_instance_id,
            runtime_root=runtime_root,
        )
    )
    channel = {
        "next_cli_actions": interaction_next_cli_actions(
            payload,
            mode=mode,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_execution_context,
            capability_reentry=capability_reentry,
            capability_reentry_resolved=True,
            settlement_plan=settlement_plan,
            turn_instance_id=turn_instance_id,
            runtime_root=runtime_root,
        ),
        "spend_allowed_now": False,
        "spend_after_validation": spend_after_selection,
        "spend_policy": _interaction_spend_policy(
            execution_obligation,
            heartbeat_recommendation,
            mode=mode,
            spend_after_validation=spend_after_selection,
        ),
    }
    work_lane = (
        payload.get("work_lane_contract")
        if isinstance(payload.get("work_lane_contract"), Mapping)
        else {}
    )
    auxiliary_monitor = (
        work_lane.get("auxiliary_monitor_poll")
        if isinstance(work_lane.get("auxiliary_monitor_poll"), Mapping)
        else None
    )
    if auxiliary_monitor is not None:
        selected_monitor_id = normalize_todo_id(
            auxiliary_monitor.get("selected_todo_id")
        )
        try:
            auxiliary_scheduler_args = render_scheduler_execution_args(
                scheduler_execution_context=scheduler_execution_context,
            )
        except ValueError:
            auxiliary_scheduler_args = ""
        if selected_monitor_id:
            command_prefix = selection.render_cli_command_prefix(
                runtime_root=runtime_root
            )
            agent_identity = (
                payload.get("agent_identity")
                if isinstance(payload.get("agent_identity"), dict)
                else {}
            )
            safe_turn_instance_id = str(turn_instance_id or "").strip()
            auxiliary_projection: dict[str, Any] = {
                **dict(auxiliary_monitor),
                "schema_version": AUXILIARY_MONITOR_POLL_CLI_SCHEMA_VERSION,
                "input_contract": {
                    "schema_version": (
                        AUXILIARY_MONITOR_OBSERVATION_INPUT_SCHEMA_VERSION
                    ),
                    "result_hash": {
                        "required": True,
                        "environment_variable": AUXILIARY_MONITOR_RESULT_HASH_ENV,
                        "source": "fresh_external_observation_digest",
                    },
                    "material_change": {
                        "required": True,
                        "unchanged_command_key": "command",
                        "changed_command_key": "material_change_command",
                    },
                },
            }
            if not safe_turn_instance_id:
                auxiliary_projection.update(
                    {
                        "availability": "turn_binding_required",
                        "reason_code": "auxiliary_monitor_turn_instance_id_missing",
                    }
                )
            else:
                command = (
                    f"{command_prefix} quota monitor-poll --goal-id "
                    f"{shlex.quote(str(payload.get('goal_id') or '<GOAL_ID>'))}"
                    f"{_scoped_cli_args(agent_identity, available_capabilities=available_capabilities)}"
                    f"{auxiliary_scheduler_args} --turn-instance-id "
                    f"{shlex.quote(safe_turn_instance_id)} --todo-id "
                    f"{shlex.quote(selected_monitor_id)} --result-hash "
                    f'"${{{AUXILIARY_MONITOR_RESULT_HASH_ENV}:?}}"'
                )
                auxiliary_projection.update(
                    {
                        "availability": "ready",
                        "turn_instance_id": safe_turn_instance_id,
                        "command": f"{command} --execute",
                        "material_change_command": (
                            f"{command} --material-change --execute"
                        ),
                    }
                )
            channel["auxiliary_monitor_poll"] = auxiliary_projection
    selection.apply_action_selection_cli_gate(channel, payload)
    if settlement_plan is not None and spend_after_selection:
        channel["settlement_plan"] = settlement_plan
    if settlement_plan is not None and replan_settlement_contract is not None:
        channel["replan_settlement_contract"] = replan_settlement_contract
    if capability_reentry is not None:
        capability_reentry_adapter.apply_cli_channel_projection(
            channel,
            capability_reentry,
            selection_required=selection.action_portfolio_requires_explicit_selection(payload),
        )
    selected_todo = (
        payload.get("selected_todo")
        if isinstance(payload.get("selected_todo"), Mapping)
        else {}
    )
    if spend_after_selection and selected_todo.get("task_repository"):
        channel["delivery_workspace_causality"] = {
            "schema_version": "delivery_workspace_causality_v0",
            "refresh": "delivery_workspace; otherwise --delivery-workspace-path",
            "spend": "recorded_delivery_workspace",
            "mismatch": "fail_closed",
        }
    return channel


def _attach_interaction_required_reads(
    contract: dict[str, Any],
    required_reads: list[dict[str, Any]],
) -> None:
    if not required_reads:
        return
    contract["agent_channel"]["required_reads"] = required_reads
    contract["cli_channel"]["required_reads"] = required_reads


def _attach_interaction_post_writeback_actions(
    contract: dict[str, Any], payload: dict[str, Any]
) -> None:
    goal_boundary = (
        payload.get("goal_boundary")
        if isinstance(payload.get("goal_boundary"), Mapping)
        else {}
    )
    actions = goal_boundary.get("post_writeback_actions")
    if isinstance(actions, list) and actions:
        contract["cli_channel"]["post_writeback_actions"] = actions


def _attach_interaction_vision_continuation_audit(
    contract: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    vision_continuation_audit = (
        payload.get("vision_continuation_audit")
        if isinstance(payload.get("vision_continuation_audit"), dict)
        else {}
    )
    if not vision_continuation_audit.get("required"):
        return
    contract["agent_channel"]["vision_continuation_audit"] = vision_continuation_audit
    vision_gap_judge = (
        vision_continuation_audit.get("vision_gap_judge")
        if isinstance(vision_continuation_audit.get("vision_gap_judge"), dict)
        else {}
    )
    contract["cli_channel"]["vision_continuation_audit"] = {
        "required": True,
        "required_before_closeout": vision_continuation_audit.get(
            "required_before_closeout"
        )
        or [],
        "recommended_action": vision_continuation_audit.get("recommended_action"),
    }
    if vision_gap_judge:
        contract["cli_channel"]["vision_continuation_audit"]["vision_gap_judge"] = {
            "done": vision_gap_judge.get("done"),
            "decision": vision_gap_judge.get("decision"),
            "reason": vision_gap_judge.get("reason"),
            "agent_judge_instruction": vision_gap_judge.get("agent_judge_instruction"),
            "evidence_read_instruction": vision_gap_judge.get(
                "evidence_read_instruction"
            ),
            "registry_read_instruction": vision_gap_judge.get(
                "registry_read_instruction"
            ),
            "done_only_when": vision_gap_judge.get("done_only_when") or [],
            "continue_when": vision_gap_judge.get("continue_when") or [],
            "otherwise": vision_gap_judge.get("otherwise"),
        }


def _attach_interaction_vision_wait_state(
    contract: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    vision_wait_state = (
        payload.get("vision_wait_state")
        if isinstance(payload.get("vision_wait_state"), dict)
        else {}
    )
    if vision_wait_state.get("state") != "waiting":
        return
    contract["agent_channel"]["vision_wait_state"] = vision_wait_state
    contract["cli_channel"]["vision_wait_state"] = {
        "state": "waiting",
        "reason_code": vision_wait_state.get("reason_code"),
        "selected_todo_id": vision_wait_state.get("selected_todo_id"),
        "resume_when": vision_wait_state.get("resume_when"),
        "automatic_resume": vision_wait_state.get("automatic_resume") is True,
        "spend_policy": "no spend while the exact resume condition is pending",
    }


def _interaction_fallback_policy_required(payload: dict[str, Any], *, mode: str) -> bool:
    return mode in {
        "user_gate",
        "user_todo_blocker_push",
        "user_action_required",
        "outcome_floor_recovery",
        "external_evidence_observation",
        "scoped_user_gate_fallback",
        "unsettled_host_turn_recovery",
    } or bool(payload.get("blocked_priority_fallback"))


def build_interaction_contract(
    payload: dict[str, Any],
    *,
    available_capabilities: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    turn_instance_id: str | None = None,
    runtime_root: str | None = None,
) -> InteractionContractPacket:
    execution_obligation = (
        payload.get("execution_obligation")
        if isinstance(payload.get("execution_obligation"), dict)
        else {}
    )
    heartbeat_recommendation = (
        payload.get("heartbeat_recommendation")
        if isinstance(payload.get("heartbeat_recommendation"), dict)
        else {}
    )
    mode = _interaction_mode(payload)
    monitor_only = payload.get("agent_work_mode") == "monitor_only"
    user_required = False if monitor_only else user_channel_action_required(payload)
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
    if _blocked_successor_wait_observation_required(payload):
        must_attempt = True
    delivery_allowed = _interaction_delivery_allowed(
        payload,
        execution_obligation,
        mode=mode,
        user_required=user_required,
        scoped_user_gate_fallback=scoped_user_gate_fallback,
        bounded_delivery_with_user_notice=bounded_delivery_with_user_notice,
    )
    quiet_noop_allowed = _interaction_quiet_noop_allowed(
        mode=mode,
        user_required=user_required,
        must_attempt=must_attempt,
    )
    spend_after_validation = (
        _interaction_spend_after_validation(mode)
        and todo_lifecycle_settlement_obligation(payload) is None
    )
    required_reads = _interaction_required_reads(payload)
    capability_reentry = capability_reentry_adapter.build_runtime_capability_reentry_packet(
        payload,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
        runtime_root=runtime_root,
    )

    user_channel = _build_interaction_user_channel(
        payload,
        heartbeat_recommendation,
        user_required=user_required,
    )
    if monitor_only:
        user_channel = {
            "action_required": False,
            "notify": "DONT_NOTIFY",
            "reason": payload.get("reason"),
        }
    agent_channel = _build_interaction_agent_channel(
        payload,
        mode=mode,
        must_attempt=must_attempt,
        delivery_allowed=delivery_allowed,
        quiet_noop_allowed=quiet_noop_allowed,
        capability_reentry=capability_reentry,
    )
    contract: dict[str, Any] = {
        "schema_version": INTERACTION_CONTRACT_SCHEMA_VERSION,
        "mode": mode,
        "user_channel": user_channel,
        "agent_channel": agent_channel,
        "cli_channel": _build_interaction_cli_channel(
            payload,
            execution_obligation,
            heartbeat_recommendation,
            mode=mode,
            spend_after_validation=spend_after_validation,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_execution_context,
            capability_reentry=capability_reentry,
            turn_instance_id=turn_instance_id,
            runtime_root=runtime_root,
        ),
    }
    response_plan = _build_interaction_response_plan(
        user_channel=user_channel,
        agent_channel=agent_channel,
    )
    if response_plan is not None:
        contract["response_plan"] = response_plan
    _attach_interaction_required_reads(contract, required_reads)
    _attach_interaction_post_writeback_actions(contract, payload)
    _attach_interaction_vision_continuation_audit(contract, payload)
    _attach_interaction_vision_wait_state(contract, payload)
    if _interaction_fallback_policy_required(payload, mode=mode):
        contract["fallback_policy"] = {"do_not_cancel_on_block": True}
    return typing.cast(InteractionContractPacket, contract)
