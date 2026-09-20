from __future__ import annotations
from .turn_contract_generated import LoopXTurnRoute  # compatibility re-export
from ..quota.effective_action import EffectiveAction

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from ..agent_context import envelope_agent_context
from .subagent_host_adapter import (
    project_child_context_adapter,
    supported_child_context_modes,
)
from .subagent_execution_topology import (
    bind_child_operations_to_topology,
    build_subagent_execution_topology,
)
from ..quota.turn_envelope import (
    TURN_ENVELOPE_SCHEMA_VERSION,
    turn_envelope_action_signature_document,
)
from ..scheduler.execution_context import (
    scheduler_execution_context_for_turn,
)
from .transaction import build_loopx_turn_transaction_plan


LOOPX_TURN_PLAN_SCHEMA_VERSION = "loopx_turn_plan_v0"
LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION = "loopx_turn_session_binding_v0"
LOOPX_ITERATION_CONTEXT_POLICY_SCHEMA_VERSION = (
    "loopx_iteration_context_policy_v0"
)
LOOPX_CHILD_HOST_OPERATION_SCHEMA_VERSION = "loopx_child_host_operation_v0"
SUPPORTED_HOSTS = {"codex-cli", "claude-code", "dsh", "generic-cli"}
SUPPORTED_EXECUTION_MODES = {"interactive-visible", "isolated-headless"}
SUPPORTED_ITERATION_CONTEXT_POLICIES = {"fresh", "resume_if_available"}
REPLAN_ACTIONS = {
    "autonomous_replan",
    "autonomous_replan_required",
    "successor_replan_required",
}
REPAIR_ACTIONS: frozenset[EffectiveAction] = frozenset(
    {
        EffectiveAction.AGENT_WORKSPACE_REPAIR,
        EffectiveAction.BOUNDARY_PROJECTION_REPAIR,
        EffectiveAction.CAPABILITY_BRIDGE_REPAIR,
        EffectiveAction.CONTROL_PLANE_HEALTH_REPAIR,
        EffectiveAction.CONTROL_PLANE_PROJECTION_REPAIR,
        EffectiveAction.CONTROL_PLANE_REPAIR,
        EffectiveAction.RUNTIME_USER_GATE_PROJECTION_REPAIR,
        EffectiveAction.STATE_PROJECTION_GAP_REPAIR,
        EffectiveAction.TODO_DECISION_SCOPE_PROJECTION_REPAIR,
    }
)
# Registered actions that run on a Host. The root should-run/Envelope slot is a
# disjoint union of the decision vocabulary and the frontier vocabulary, and
# every value either owner can carry is classified: replan above, repair above,
# capability intent in its own branch, and the rest named here. Host execution
# is therefore a stated classification rather than the absence of a branch, so
# a newly registered action cannot inherit it silently -- the accompanying test
# fails until the new value is classified deliberately.
HOST_EXECUTION_ACTIONS: frozenset[str] = frozenset(
    {
        # Decision vocabulary.
        EffectiveAction.AGENT_MONITOR_ONLY.value,
        EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED.value,
        EffectiveAction.BLOCKED_HEALTH.value,
        EffectiveAction.BLOCKED_WAIT.value,
        EffectiveAction.COORDINATE_TASK_BUNDLE.value,
        EffectiveAction.EXTERNAL_EVIDENCE_OBSERVE.value,
        EffectiveAction.HEARTBEAT_RECEIPT_WRITE_FAILED.value,
        EffectiveAction.HEARTBEAT_SETTLED_SKIP.value,
        EffectiveAction.LARK_INBOX_REPLY_DUE.value,
        EffectiveAction.MONITOR_DUE.value,
        EffectiveAction.MONITOR_QUIET_SKIP.value,
        EffectiveAction.NORMAL_RUN.value,
        EffectiveAction.OPERATOR_GATE_NOTIFY.value,
        EffectiveAction.OPERATOR_INBOX_MATERIAL_REVIEW_DUE.value,
        EffectiveAction.OUTCOME_FLOOR_RECOVERY.value,
        EffectiveAction.PEER_COORDINATION_BLOCKED.value,
        EffectiveAction.QUOTA_SKIP.value,
        EffectiveAction.SCOPED_USER_GATE_FALLBACK.value,
        EffectiveAction.TERMINAL_NO_FOLLOWUP.value,
        EffectiveAction.THROTTLED_SKIP.value,
        EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value,
        # Frontier vocabulary. These are not EffectiveAction members, so they
        # are named by value rather than through the enum.
        "agent_scope_exhausted",
        "agent_scope_wait",
        "reassignment_required",
    }
)


def unregistered_route(effective_action: str) -> LoopXTurnRoute:
    """Classify an action no registered owner declares.

    Legacy payloads and forward-compatible writers can carry a value this
    driver does not know. Such a value still runs on a Host: narrowing what the
    driver accepts is an admission-domain change that needs its own approval and
    version boundary, so this keeps the previous behaviour. Naming the path
    separates "this classification was chosen" from "this value was not
    recognised", which the earlier bare fallthrough could not express.
    """

    return LoopXTurnRoute.READY_FOR_HOST


class FailedTurnSessionRecoveryError(ValueError):
    """Typed public-safe refusal to resume a failed Turn's Host Session."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _typed_route(envelope: Mapping[str, Any]) -> LoopXTurnRoute:
    if envelope.get("schema_version") != TURN_ENVELOPE_SCHEMA_VERSION:
        return LoopXTurnRoute.CONTRACT_ERROR
    signature = _mapping(envelope.get("action_signature"))
    source_hash = str(signature.get("source_hash") or "")
    envelope_hash = str(signature.get("envelope_hash") or "")
    if (
        signature.get("matches") is not True
        or not source_hash
        or source_hash != envelope_hash
    ):
        return LoopXTurnRoute.CONTRACT_ERROR
    # Packet size is a performance warning, not execution authority. Keep the
    # diagnostics in the envelope; schema/signature/lineage remain hard gates.

    action = _mapping(envelope.get("action"))
    user = _mapping(envelope.get("user"))
    should_run = envelope.get("should_run") is True
    effective_action = str(envelope.get("effective_action") or "")
    delivery_allowed = action.get("delivery_allowed") is True
    must_attempt = action.get("must_attempt") is True

    if should_run:
        if not delivery_allowed or not must_attempt:
            return LoopXTurnRoute.BLOCKED
        try:
            registered_action = EffectiveAction(effective_action)
        except ValueError:
            registered_action = None
        if registered_action is EffectiveAction.GOVERNED_CAPABILITY_INTENT:
            intent = _mapping(action.get("capability_intent"))
            if (intent.get("schema_version") != "pending_capability_intent_projection_v0"
                or intent.get("goal_id") != envelope.get("goal_id")
                or intent.get("agent_id") != envelope.get("agent_id")
                or not intent.get("command")):
                return LoopXTurnRoute.CONTRACT_ERROR
            return LoopXTurnRoute.CAPABILITY_ACTION_REQUIRED
        if effective_action in REPLAN_ACTIONS:
            return LoopXTurnRoute.REPLAN_REQUIRED
        if registered_action in REPAIR_ACTIONS:
            return LoopXTurnRoute.REPAIR_REQUIRED
        if effective_action in HOST_EXECUTION_ACTIONS:
            return LoopXTurnRoute.READY_FOR_HOST
        return unregistered_route(effective_action)
    if user.get("action_required") is True:
        return LoopXTurnRoute.USER_ACTION_REQUIRED
    if action.get("quiet_noop_allowed") is True:
        return LoopXTurnRoute.WAIT
    return LoopXTurnRoute.BLOCKED


def selected_turn_todo(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the todo that owns one Turn across adaptive bundle execution."""

    orchestration = _mapping(envelope.get("task_orchestration_contract"))
    primary_todo_id = str(orchestration.get("primary_todo_id") or "").strip()
    if (
        orchestration.get("schema_version") == "task_orchestration_contract_v2"
        and orchestration.get("mode") == "adaptive"
        and primary_todo_id
    ):
        return {
            "todo_id": primary_todo_id,
            "source": "task_orchestration_contract.primary_todo_id",
        }
    action = _mapping(envelope.get("action"))
    return _mapping(action.get("selected_todo"))


def _turn_lineage(
    envelope: Mapping[str, Any],
    *,
    selected_todo: Mapping[str, Any],
) -> dict[str, str]:
    signature_document = turn_envelope_action_signature_document(envelope)
    action = _mapping(signature_document.get("action"))
    signature_document["action"] = {
        **action,
        "selected_todo": dict(selected_todo),
    }
    action_hash = "sha256:" + sha256(
        json.dumps(
            signature_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "goal_id": str(envelope.get("goal_id") or ""),
        "agent_id": str(envelope.get("agent_id") or ""),
        "todo_id": str(selected_todo.get("todo_id") or ""),
        "action_hash": action_hash,
    }


def _session_plan(
    *,
    route: LoopXTurnRoute,
    lineage: Mapping[str, str],
    session_binding: Mapping[str, Any] | None,
    iteration_context_policy: str,
) -> tuple[dict[str, Any], str | None]:
    host_route = route in {
        LoopXTurnRoute.READY_FOR_HOST,
        LoopXTurnRoute.REPAIR_REQUIRED,
        LoopXTurnRoute.REPLAN_REQUIRED,
    }
    if not host_route:
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "none",
            "binding_status": "not_applicable",
            "context_policy": {
                "schema_version": LOOPX_ITERATION_CONTEXT_POLICY_SCHEMA_VERSION,
                "mode": iteration_context_policy,
                "scope": "iteration",
            },
        }, None
    if not all(lineage.values()):
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "reject",
            "binding_status": "missing_turn_lineage",
        }, "host-bound routes require goal, agent, todo, and action-hash lineage"

    if iteration_context_policy == "fresh":
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "start_new",
            "binding_status": (
                "existing_binding_ignored" if session_binding else "not_found"
            ),
            "context_policy": {
                "schema_version": LOOPX_ITERATION_CONTEXT_POLICY_SCHEMA_VERSION,
                "mode": "fresh",
                "scope": "iteration",
            },
        }, None

    binding = dict(session_binding or {})
    if not binding:
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "start_new",
            "context_policy": {
                "schema_version": LOOPX_ITERATION_CONTEXT_POLICY_SCHEMA_VERSION,
                "mode": "resume_if_available",
                "scope": "iteration",
            },
        }, None
    if binding.get("schema_version") != LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION:
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "reject",
            "binding_status": "unsupported_schema",
        }, "unsupported LoopX Turn session binding schema"

    identity_fields = ("goal_id", "agent_id", "todo_id")
    actual = {field: str(binding.get(field) or "") for field in identity_fields}
    expected = {field: lineage[field] for field in identity_fields}
    if actual != expected:
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "reject",
            "binding_status": "identity_mismatch",
        }, "session binding does not match the current goal, agent, and todo"
    return {
        "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
        "action": "resume",
        "binding_status": "compatible",
        "context_policy": {
            "schema_version": LOOPX_ITERATION_CONTEXT_POLICY_SCHEMA_VERSION,
            "mode": "resume_if_available",
            "scope": "iteration",
        },
    }, None


def reconcile_failed_turn_session_request(
    request: Mapping[str, Any],
    *,
    session_binding: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Re-authorize a journaled host request against one current binding."""

    envelope = _mapping(request.get("turn_envelope"))
    selected_todo = selected_turn_todo(envelope)
    lineage = _turn_lineage(envelope, selected_todo=selected_todo)
    session, session_error = _session_plan(
        route=LoopXTurnRoute.READY_FOR_HOST,
        lineage=lineage,
        session_binding=session_binding,
        iteration_context_policy="resume_if_available",
    )
    if session_error:
        status = str(session.get("binding_status") or "")
        reason = {
            "missing_turn_lineage": "turn_lineage_missing",
            "unsupported_schema": "session_binding_schema_unsupported",
            "identity_mismatch": "session_binding_identity_mismatch",
        }.get(status, "session_binding_rejected")
        raise FailedTurnSessionRecoveryError(
            reason,
            f"failed-Turn recovery {session_error}",
        )
    if session.get("action") != "resume":
        raise FailedTurnSessionRecoveryError(
            "session_binding_missing",
            "failed-Turn recovery requires a compatible persisted session binding",
        )

    planned_session = _mapping(request.get("session"))
    planned_action = str(planned_session.get("action") or "")
    if planned_action not in {"start_new", "resume"}:
        raise FailedTurnSessionRecoveryError(
            "planned_session_action_unsupported",
            "failed-Turn recovery requires a start_new or resume session action",
        )
    if planned_action == "resume":
        return dict(request)
    return {
        **request,
        "session": {
            **session,
            "binding_status": "failed_turn_recovery",
        },
    }


def _child_host_operations(
    envelope: Mapping[str, Any],
    *,
    host: str,
) -> list[dict[str, Any]]:
    orchestration = _mapping(envelope.get("task_orchestration_contract"))
    if (
        orchestration.get("schema_version") != "task_orchestration_contract_v2"
        or orchestration.get("mode") != "adaptive"
    ):
        return []
    lanes = orchestration.get("eligible_child_lanes")
    if not isinstance(lanes, list):
        return []
    brief_defaults = _mapping(orchestration.get("child_brief_defaults"))
    if brief_defaults.get("schema_version") != "subagent_control_plane_handoff_v0":
        return []
    supported_contexts = supported_child_context_modes(host)
    if not supported_contexts:
        return []
    child_operations: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, Mapping):
            continue
        brief = {**brief_defaults, **_mapping(lane.get("child_brief"))}
        brief["evidence_boundary"] = {
            "task_domain": brief.get("task_domain"),
            "task_repository": brief.get("task_repository"),
            "required_write_scopes": list(brief.get("required_write_scopes") or []),
        }
        context_policy = _mapping(brief.get("context_policy"))
        recommended = str(context_policy.get("default") or "fresh")
        allowed_contexts = context_policy.get("allowed")
        if not isinstance(allowed_contexts, list):
            allowed_contexts = [recommended]
        available_contexts = [
            context for context in allowed_contexts if context in supported_contexts
        ]
        child_operations.append(
            {
                "schema_version": LOOPX_CHILD_HOST_OPERATION_SCHEMA_VERSION,
                "todo_id": str(lane.get("todo_id") or "") or None,
                "host": host,
                "selection_owner": "task_coordinator",
                "recommended_context": recommended,
                "available_contexts": available_contexts,
                "brief": brief,
                "result_channel": "public_safe_typed_evidence",
                "writeback_owner": str(
                    orchestration.get("writeback_owner") or "task_coordinator"
                ),
            }
        )
    return child_operations


def _bind_host_child_operations(
    child_operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    bound: list[dict[str, Any]] = []
    for operation in child_operations:
        context_mode = str(operation.get("recommended_context") or "")
        adapter = project_child_context_adapter(
            host=str(operation.get("host") or ""),
            context_mode=context_mode,
        )
        if adapter is None:
            continue
        bound.append(
            {
                **operation,
                "host_adapter": adapter,
            }
        )
    return bound


def build_loopx_turn_plan(
    turn_envelope: Mapping[str, Any],
    *,
    host: str,
    execution_mode: str,
    scheduler_owner: str | None = None,
    session_binding: Mapping[str, Any] | None = None,
    turn_instance_id: str | None = None,
    iteration_context_policy: str = "resume_if_available",
) -> dict[str, Any]:
    """Project a TurnEnvelope into a typed, side-effect-free host decision."""

    if host not in SUPPORTED_HOSTS:
        raise ValueError(f"unsupported LoopX Turn host: {host}")
    if execution_mode not in SUPPORTED_EXECUTION_MODES:
        raise ValueError(f"unsupported LoopX Turn execution mode: {execution_mode}")
    if iteration_context_policy not in SUPPORTED_ITERATION_CONTEXT_POLICIES:
        raise ValueError(
            "iteration context policy must be fresh or resume_if_available"
        )

    execution_context = scheduler_execution_context_for_turn(
        host=host,
        execution_mode=execution_mode,
        scheduler_owner=scheduler_owner,
    )
    envelope = dict(turn_envelope)
    route = (
        _typed_route(envelope)
        if execution_context.ok
        else LoopXTurnRoute.CONTRACT_ERROR
    )
    selected_todo = selected_turn_todo(envelope)
    lineage = _turn_lineage(envelope, selected_todo=selected_todo)
    session, session_error = _session_plan(
        route=route,
        lineage=lineage,
        session_binding=session_binding,
        iteration_context_policy=iteration_context_policy,
    )
    if session_error:
        route = LoopXTurnRoute.CONTRACT_ERROR
    would_invoke_host = route in {
        LoopXTurnRoute.READY_FOR_HOST,
        LoopXTurnRoute.REPAIR_REQUIRED,
        LoopXTurnRoute.REPLAN_REQUIRED,
    }
    raw_child_operations = (
        _child_host_operations(envelope, host=host) if would_invoke_host else []
    )
    context_projection = execution_context.projection()
    transaction = build_loopx_turn_transaction_plan(
        planned=would_invoke_host,
        lineage=lineage,
        host=host,
        execution_mode=execution_mode,
        scheduler_owner=str(context_projection.get("scheduler_owner") or ""),
        session_action=str(session.get("action") or "none"),
        turn_instance_id=turn_instance_id,
    )
    execution_topology = build_subagent_execution_topology(
        turn_envelope=envelope,
        child_operations=raw_child_operations,
        turn_key=str(transaction.get("turn_key") or ""),
        source_state_ref=str(
            _mapping(envelope.get("action_signature")).get("source_hash") or ""
        ),
    )
    child_operations = bind_child_operations_to_topology(
        raw_child_operations,
        execution_topology,
    )
    child_operations = _bind_host_child_operations(child_operations)
    payload = {
        "ok": route is not LoopXTurnRoute.CONTRACT_ERROR,
        "schema_version": LOOPX_TURN_PLAN_SCHEMA_VERSION,
        "mode": "plan",
        "host": {
            "kind": host,
            "execution_mode": execution_mode,
            "scheduler_owner": context_projection.get("scheduler_owner"),
            "explicit_isolation": execution_mode == "isolated-headless",
        },
        "scheduler_execution_context": context_projection,
        "session": session,
        "route": {
            "schema_version": "loopx_turn_route_v0",
            "kind": route.value,
            "effective_action": envelope.get("effective_action"),
            "would_invoke_host": would_invoke_host,
            "host_invocation_allowed": False,
            "selected_todo": selected_todo or None,
        },
        "turn_envelope": envelope,
        "transaction": transaction,
        "effects": {
            "host_invoked": False,
            "state_written": False,
            "scheduler_acknowledged": False,
            "quota_spent": False,
        },
        "boundary": {
            "read_only": True,
            "requires_explicit_execute_surface": True,
            "preserves_turn_envelope": True,
            "opaque_session_handle_omitted": True,
        },
        **(
            {"error": session_error or "; ".join(execution_context.errors)}
            if session_error or not execution_context.ok
            else {}
        ),
    }
    if child_operations:
        payload["child_operations"] = child_operations
        context = envelope_agent_context(
            envelope,
            phase="before_delegate",
            observations={"child_count": len(child_operations)},
        )
        if context is not None:
            payload["delegation_context"] = context
    if execution_topology:
        payload["subagent_execution_topology"] = execution_topology
    if route is LoopXTurnRoute.CAPABILITY_ACTION_REQUIRED:
        payload["capability_action"] = {
            "status": "required",
            "intent": _mapping(_mapping(envelope.get("action")).get("capability_intent")),
            "executed": False,
            "execution_owner": "capability_adapter",
            "reason": "Use the capability-owned command and its receipt before planning a host turn.",
        }
    return payload
