from __future__ import annotations
from .turn_contract_generated import LoopXTurnRoute  # compatibility re-export

import json
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from typing import Any

from ..agent_context import envelope_agent_context
from ..effect_program import SettlementIdentity
from .subagent_host_adapter import (
    project_child_context_adapter,
    supported_child_context_modes,
)
from .subagent_execution_topology import (
    bind_child_operations_to_topology,
    build_subagent_execution_topology,
)
from ..quota.turn_envelope import (
    assert_turn_envelope_consumer_version,
    _turn_envelope_result,
    turn_envelope_scoped_settlement_identity,
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
SUPPORTED_HOSTS = {"codex-cli", "codex-app-server", "claude-code", "dsh", "generic-cli"}
SUPPORTED_EXECUTION_MODES = {"interactive-visible", "isolated-headless"}
SUPPORTED_ITERATION_CONTEXT_POLICIES = {"fresh", "resume"}


class SessionBindingScope(StrEnum):
    TODO = "todo"
    AGENT = "agent"


def session_identity_fields(scope: str) -> tuple[str, ...]:
    """Conversation identity is independent of a Turn's settlement identity."""
    selected = SessionBindingScope(scope)
    if selected is SessionBindingScope.TODO:
        return ("goal_id", "agent_id", "todo_id")
    return ("goal_id", "agent_id")


class FailedTurnSessionRecoveryError(ValueError):
    """Typed public-safe refusal to resume a failed Turn's Host Session."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _historical_route_text(envelope: Mapping[str, Any]) -> dict[str, str]:
    """Retain Python str/or encoding for historical, non-string JSON scalars.

    This codec selects no route; the TypeScript owner applies every gate.
    Complete contracts do not accept these historical decoding hints.
    """
    signature = _mapping(envelope.get("action_signature"))
    return {"source_hash": str(signature.get("source_hash") or ""),
            "envelope_hash": str(signature.get("envelope_hash") or ""),
            "effective_action": str(envelope.get("effective_action") or "")}


def _typed_route(envelope: Mapping[str, Any]) -> LoopXTurnRoute:
    """Compatibility transport to the shared TypeScript route owner."""
    return LoopXTurnRoute(_turn_envelope_result(
        "driver_route", envelope=dict(envelope), historical_route_text=_historical_route_text(envelope),
    )["route"])


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


def _receipt_bound_scoped_identity(
    envelope: Mapping[str, Any], *, turn_instance_id: str | None,
) -> SettlementIdentity | None:
    """Transport the quota owner's signed binding; never invent an obligation.

    Older Todo envelopes retain their established construction. Empty-frontier
    planning needs the existing scoped identity from the complete settlement
    plan, as well as its matching semantic obligation and original Turn.
    """
    raw = turn_envelope_scoped_settlement_identity(envelope, turn_instance_id=turn_instance_id)
    if raw is None:
        return None
    return SettlementIdentity.from_runtime_payload(raw)


def _turn_lineage(
    envelope: Mapping[str, Any],
    *,
    selected_todo: Mapping[str, Any],
    settlement_identity: SettlementIdentity | None = None,
    projection: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    resolved = projection if projection is not None else _turn_envelope_result(
        "driver_lineage", envelope=dict(envelope), historical_route_text=_historical_route_text(envelope),
    )
    signature_document = resolved["action_signature"]
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
    lineage = {
        "goal_id": str(envelope.get("goal_id") or ""),
        "agent_id": str(envelope.get("agent_id") or ""),
        "todo_id": str(selected_todo.get("todo_id") or ""),
        "action_hash": action_hash,
    }
    if settlement_identity is not None:
        if settlement_identity.request_subject is not None:
            lineage["request_binding_id"] = settlement_identity.binding_id
        elif settlement_identity.replan_obligation_id is not None:
            lineage["replan_obligation_id"] = settlement_identity.replan_obligation_id
    return lineage


def _session_plan(
    *,
    route: LoopXTurnRoute,
    lineage: Mapping[str, str],
    session_binding: Mapping[str, Any] | None,
    iteration_context_policy: str,
    session_scope: str = "agent",
) -> tuple[dict[str, Any], str | None]:
    identity_fields = session_identity_fields(session_scope)
    if lineage.get("request_binding_id") and iteration_context_policy == "fresh":
        identity_fields = ("goal_id", "agent_id")
    context_policy = {
        "schema_version": LOOPX_ITERATION_CONTEXT_POLICY_SCHEMA_VERSION,
        "mode": iteration_context_policy,
        "scope": "iteration",
        **({"binding_scope": session_scope} if session_scope != "todo" else {}),
    }
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
            "context_policy": context_policy,
        }, None
    if (not all(lineage.get(field) for field in (*identity_fields, "action_hash"))
            or not (lineage.get("todo_id") or lineage.get("replan_obligation_id") or lineage.get("request_binding_id"))):
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
            "context_policy": context_policy,
        }, None

    binding = dict(session_binding or {})
    if not binding:
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "start_new",
            "context_policy": context_policy,
        }, None
    if binding.get("schema_version") != LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION:
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "reject",
            "binding_status": "unsupported_schema",
        }, "unsupported LoopX Turn session binding schema"

    actual = {field: str(binding.get(field) or "") for field in identity_fields}
    expected = {field: lineage[field] for field in identity_fields}
    if actual != expected:
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "action": "reject",
            "binding_status": "identity_mismatch",
        }, "session binding does not match the current " + ", ".join(identity_fields)
    return {
        "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
        "action": "resume",
        "binding_status": "compatible",
        "context_policy": context_policy,
    }, None


def reconcile_failed_turn_session_request(
    request: Mapping[str, Any],
    *,
    session_binding: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Re-authorize a journaled host request against one current binding."""

    envelope = _mapping(request.get("turn_envelope"))
    selected_todo = selected_turn_todo(envelope)
    try:
        settlement_identity = (None if selected_todo else _receipt_bound_scoped_identity(
            envelope, turn_instance_id=None,
        ))
    except (ValueError, RuntimeError) as exc:
        raise FailedTurnSessionRecoveryError("turn_lineage_missing", str(exc)) from exc
    lineage = _turn_lineage(envelope, selected_todo=selected_todo,
                            settlement_identity=settlement_identity)
    planned_session = _mapping(request.get("session"))
    session, session_error = _session_plan(
        route=LoopXTurnRoute.READY_FOR_HOST,
        lineage=lineage,
        session_binding=session_binding,
        iteration_context_policy="resume",
        session_scope=str(_mapping(planned_session.get("context_policy")).get("binding_scope") or "todo"),
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
    iteration_context_policy: str = "resume",
    session_scope: str | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Project a TurnEnvelope into a typed, side-effect-free host decision."""

    if host not in SUPPORTED_HOSTS:
        raise ValueError(f"unsupported LoopX Turn host: {host}")
    if execution_mode not in SUPPORTED_EXECUTION_MODES:
        raise ValueError(f"unsupported LoopX Turn execution mode: {execution_mode}")
    if iteration_context_policy not in SUPPORTED_ITERATION_CONTEXT_POLICIES:
        raise ValueError(
            "iteration context policy must be fresh or resume"
        )
    if session_scope is None:
        session_scope = "agent" if host in {"codex-cli", "codex-app-server"} else "todo"
    session_identity_fields(session_scope)
    if session_scope != "todo" and host not in {"codex-cli", "codex-app-server"}:
        raise ValueError("agent session scope requires a Codex conversation host")

    execution_context = scheduler_execution_context_for_turn(
        host=host,
        execution_mode=execution_mode,
        scheduler_owner=scheduler_owner,
    )
    envelope = dict(turn_envelope)
    contract_error = None
    try:
        assert_turn_envelope_consumer_version(envelope)
    except (ValueError, RuntimeError) as exc:
        contract_error = str(exc)
    projection = _turn_envelope_result("driver_lineage", envelope=envelope,
        historical_route_text=_historical_route_text(envelope))
    route = (
        LoopXTurnRoute(projection["route"])
        if execution_context.ok and contract_error is None
        else LoopXTurnRoute.CONTRACT_ERROR
    )
    selected_todo = selected_turn_todo(envelope)
    settlement_identity = None
    identity_error = None
    if route is LoopXTurnRoute.REPLAN_REQUIRED or (route is LoopXTurnRoute.READY_FOR_HOST and not selected_todo):
        try:
            settlement_identity = _receipt_bound_scoped_identity(
                envelope, turn_instance_id=turn_instance_id,
            )
        except (ValueError, RuntimeError) as exc:
            identity_error = str(exc)
            route = LoopXTurnRoute.CONTRACT_ERROR
        if (not selected_todo and identity_error is None
                and (settlement_identity is None or (session_scope != "agent"
                  and not (settlement_identity.request_subject is not None and iteration_context_policy == "fresh")))):
            route = LoopXTurnRoute.BLOCKED
    lineage = _turn_lineage(envelope, selected_todo=selected_todo,
                            settlement_identity=settlement_identity, projection=projection)
    session, session_error = _session_plan(
        route=route,
        lineage=lineage,
        session_binding=session_binding,
        iteration_context_policy=iteration_context_policy,
        session_scope=session_scope,
    )
    if session_error:
        route = LoopXTurnRoute.CONTRACT_ERROR
    session_error = identity_error or session_error
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
        turn_instance_id=(turn_instance_id if turn_instance_id is not None else
                          (settlement_identity.turn_instance_id if settlement_identity else None)),
        goal_ref=goal_ref,
        settlement_identity=settlement_identity,
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
            {"error": contract_error or session_error or "; ".join(execution_context.errors)}
            if contract_error or session_error or not execution_context.ok
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
    if goal_ref is not None:
        payload["goal_ref"] = dict(goal_ref)
    if route is LoopXTurnRoute.CAPABILITY_ACTION_REQUIRED:
        payload["capability_action"] = {
            "status": "required",
            "intent": _mapping(_mapping(envelope.get("action")).get("capability_intent")),
            "executed": False,
            "execution_owner": "capability_adapter",
            "reason": "Use the capability-owned command and its receipt before planning a host turn.",
        }
    return payload
