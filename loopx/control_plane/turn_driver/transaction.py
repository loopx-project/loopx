"""Typed LoopX Turn transaction planning and receipt validation."""

from __future__ import annotations
from .turn_contract_generated import LoopXTurnResultKind  # compatibility re-export

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from ...turn_identity import normalize_turn_instance_id
from ..effect_program import (
    SettlementIdentity,
    SettlementPlan,
    SettlementStep,
    SettlementStepKind,
    TURN_TRANSACTION_PHASES,
)

LOOPX_TURN_TRANSACTION_PLAN_SCHEMA_VERSION = "loopx_turn_transaction_plan_v0"
LOOPX_TURN_RESULT_SCHEMA_VERSION = "loopx_turn_result_v0"
LOOPX_TURN_RECEIPT_SCHEMA_VERSION = "loopx_turn_receipt_v0"
LOOPX_TURN_RECEIPT_VALIDATION_SCHEMA_VERSION = "loopx_turn_receipt_validation_v0"
LOOPX_TURN_EXECUTION_SCHEMA_VERSION = "loopx_turn_execution_v0"
TRANSACTION_PHASES = TURN_TRANSACTION_PHASES



MATERIAL_RESULT_KINDS = {
    LoopXTurnResultKind.VALIDATED_PROGRESS,
    LoopXTurnResultKind.VALIDATED_COMPLETION,
    LoopXTurnResultKind.REPAIR_REQUIRED,
    LoopXTurnResultKind.REPLAN_REQUIRED,
}
NO_SPEND_RESULT_KINDS = {
    LoopXTurnResultKind.USER_ACTION_REQUIRED,
    LoopXTurnResultKind.WAIT,
    LoopXTurnResultKind.ITERATION_FAILED,
    LoopXTurnResultKind.HOST_FAILURE,
    LoopXTurnResultKind.VALIDATION_FAILED,
    LoopXTurnResultKind.WRITEBACK_FAILED,
    LoopXTurnResultKind.QUOTA_SPEND_FAILED,
}
STOP_RESULT_KINDS = {
    LoopXTurnResultKind.USER_ACTION_REQUIRED,
    LoopXTurnResultKind.WAIT,
    LoopXTurnResultKind.ITERATION_FAILED,
}
FAILURE_PHASES = {
    LoopXTurnResultKind.HOST_FAILURE: "host_execute",
    LoopXTurnResultKind.VALIDATION_FAILED: "validation",
    LoopXTurnResultKind.WRITEBACK_FAILED: "durable_writeback",
    LoopXTurnResultKind.QUOTA_SPEND_FAILED: "quota_spend",
    LoopXTurnResultKind.TERMINAL_CLOSEOUT_FAILED: "terminal_closeout",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def loopx_turn_execution_committed(execution: Mapping[str, Any]) -> bool:
    """Return whether one public execution completed its durable transaction."""

    validation = _mapping(execution.get("validation"))
    receipt = _mapping(execution.get("receipt"))
    effects = _mapping(execution.get("effects"))
    return bool(
        execution.get("status") == "committed"
        and receipt.get("status") == "committed"
        and validation.get("status") in {"passed", "progress"}
        and effects.get("state_written") is True
        and effects.get("quota_spent") is True
    )


def loopx_turn_execution_recovery_required(
    execution: Mapping[str, Any],
) -> bool:
    """Return whether a failed public execution requests repair or replan."""

    validation = _mapping(execution.get("validation"))
    receipt = _mapping(execution.get("receipt"))
    recovery_kind = (
        validation.get("recovery_kind")
        or receipt.get("result_kind")
        or execution.get("result_kind")
    )
    return bool(
        recovery_kind in {"repair_required", "replan_required"}
        and (
            validation.get("status") == "failed"
            or receipt.get("failed_phase")
            or execution.get("status") in {"failed", "validation_failed"}
        )
    )


def loopx_turn_execution_has_durable_effects(
    execution: Mapping[str, Any],
) -> bool:
    """Return whether an execution wrote state or spent quota."""

    effects = _mapping(execution.get("effects"))
    return bool(
        effects.get("state_written") is True or effects.get("quota_spent") is True
    )


def build_loopx_turn_transaction_plan(
    *,
    planned: bool,
    lineage: Mapping[str, str],
    host: str,
    execution_mode: str,
    session_action: str,
    scheduler_owner: str = "none",
    turn_instance_id: str | None = None,
    goal_ref: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    normalized_instance_id = normalize_turn_instance_id(turn_instance_id)
    identity = {
        "lineage": dict(lineage),
        "host": host,
        "execution_mode": execution_mode,
        "scheduler_owner": scheduler_owner,
        "session_action": session_action,
    }
    if normalized_instance_id is not None:
        identity["turn_instance_id"] = normalized_instance_id
    if goal_ref is not None:
        identity["goal_ref"] = dict(goal_ref)
    turn_key = _canonical_hash(identity)
    settlement_identity = SettlementIdentity(
        goal_id=str(lineage.get("goal_id") or ""),
        agent_id=str(lineage.get("agent_id") or ""),
        todo_id=str(lineage.get("todo_id") or ""),
        turn_instance_id=normalized_instance_id or turn_key,
    )
    effect_ref = "$.identity.effect_id"
    settlement_plan = SettlementPlan(
        identity=settlement_identity,
        steps=(
            SettlementStep(
                kind=SettlementStepKind.VALIDATION,
                owner="turn_driver",
                precondition="typed host result passed independent task validation",
                idempotency_key_ref=effect_ref,
                expected_receipt="validation_receipt",
            ),
            SettlementStep(
                kind=SettlementStepKind.DURABLE_WRITEBACK,
                owner="turn_driver_callback_adapter",
                precondition="validation receipt exists",
                idempotency_key_ref=effect_ref,
                expected_receipt="durable_writeback_receipt",
            ),
            SettlementStep(
                kind=SettlementStepKind.QUOTA_SPEND,
                owner="turn_driver_callback_adapter",
                precondition="matching durable writeback receipt exists",
                idempotency_key_ref=effect_ref,
                expected_receipt="quota_spend_receipt",
            ),
            SettlementStep(
                kind=SettlementStepKind.TERMINAL_CLOSEOUT,
                owner="turn_driver_callback_adapter",
                precondition=(
                    "validated completion durably declares no_followup and "
                    "matching quota spend receipt exists"
                ),
                idempotency_key_ref=effect_ref,
                expected_receipt="terminal_closeout_receipt",
                conditional=True,
            ),
        ),
    )
    plan = {
        "schema_version": LOOPX_TURN_TRANSACTION_PLAN_SCHEMA_VERSION,
        "status": "planned" if planned else "not_applicable",
        "turn_key": turn_key,
        "phases": list(TRANSACTION_PHASES) if planned else [],
        "commit_policy": "result<validate<writeback<spend;apply<ack;cadence:no-spend",
        "receipt_seed": {
            "schema_version": LOOPX_TURN_RECEIPT_SCHEMA_VERSION,
            "status": "not_executed",
            "next_phase": TRANSACTION_PHASES[0] if planned else None,
        },
    }
    if planned:
        plan["settlement_plan"] = settlement_plan.as_dict()
    if normalized_instance_id is not None:
        plan["turn_instance_id"] = normalized_instance_id
    if goal_ref is not None:
        plan["goal_ref"] = dict(goal_ref)
    return plan


def _result_kind(value: Any, errors: list[str]) -> LoopXTurnResultKind | None:
    try:
        return LoopXTurnResultKind(str(value or ""))
    except ValueError:
        errors.append("unsupported execution result kind")
        return None


def _completed_phases(value: Any, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append("completed_phases must be a list")
        return []
    phases = [str(item) for item in value]
    expected = list(TRANSACTION_PHASES[: len(phases)])
    if phases != expected:
        errors.append("completed_phases must be an ordered transaction prefix")
    return phases


def _settlement_identity(
    transaction_plan: Mapping[str, Any],
    errors: list[str],
) -> dict[str, str]:
    settlement_plan = _mapping(transaction_plan.get("settlement_plan"))
    identity = _mapping(settlement_plan.get("identity"))
    normalized = {
        "goal_id": str(identity.get("goal_id") or ""),
        "agent_id": str(identity.get("agent_id") or ""),
        "todo_id": str(identity.get("todo_id") or ""),
        "effect_id": str(identity.get("effect_id") or ""),
    }
    if not all(normalized.values()):
        errors.append("transaction plan is missing its typed settlement identity")
    return normalized


def require_loopx_turn_completion_outcome(
    completion: Any,
    *,
    expected_todo_id: str,
) -> dict[str, Any]:
    """Validate the durable Todo lifecycle outcome of a completed Turn."""

    if not isinstance(completion, Mapping):
        raise TypeError("validated completion is missing its durable Todo outcome")
    todo_id = str(completion.get("todo_id") or "")
    if not expected_todo_id or todo_id != expected_todo_id:
        raise ValueError("validated completion is missing its durable Todo outcome")
    continuation = str(completion.get("continuation") or "")
    if continuation not in {"successor", "active_goal", "no_followup"}:
        raise ValueError("validated completion has an invalid continuation outcome")
    outcome: dict[str, Any] = {
        "todo_id": todo_id,
        "continuation": continuation,
    }
    if continuation == "successor":
        successor_ids = completion.get("successor_todo_ids")
        if (
            not isinstance(successor_ids, list)
            or not successor_ids
            or not all(isinstance(item, str) and item for item in successor_ids)
        ):
            raise ValueError("successor completion requires successor Todo ids")
        outcome["successor_todo_ids"] = list(successor_ids)
    return outcome


def validate_loopx_turn_receipt(
    transaction_plan: Mapping[str, Any],
    execution_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one public-safe host result against its transaction plan."""

    plan = _mapping(transaction_plan)
    result = _mapping(execution_result)
    errors: list[str] = []
    if plan.get("schema_version") != LOOPX_TURN_TRANSACTION_PLAN_SCHEMA_VERSION:
        errors.append("unsupported transaction plan schema")
    if plan.get("status") != "planned":
        errors.append("transaction plan is not executable")
    if result.get("schema_version") != LOOPX_TURN_RESULT_SCHEMA_VERSION:
        errors.append("unsupported execution result schema")

    turn_key = str(plan.get("turn_key") or "")
    if not turn_key or str(result.get("turn_key") or "") != turn_key:
        errors.append("execution result turn_key does not match the transaction plan")
    settlement_identity = _settlement_identity(plan, errors)

    kind = _result_kind(result.get("result_kind"), errors)
    completed = _completed_phases(result.get("completed_phases"), errors)
    failed_phase = str(result.get("failed_phase") or "") or None
    next_index = min(len(completed), len(TRANSACTION_PHASES))
    expected_next = (
        TRANSACTION_PHASES[next_index] if next_index < len(TRANSACTION_PHASES) else None
    )

    terminal_closeout_failure = bool(
        failed_phase == "terminal_closeout"
        and kind is LoopXTurnResultKind.TERMINAL_CLOSEOUT_FAILED
        and completed == list(TRANSACTION_PHASES[:5])
    )
    if failed_phase and failed_phase != expected_next and not terminal_closeout_failure:
        errors.append("failed_phase must be the next uncompleted transaction phase")
    if failed_phase and kind not in FAILURE_PHASES:
        errors.append("failed_phase is only valid for a typed failure result")
    if kind in FAILURE_PHASES and failed_phase != FAILURE_PHASES[kind]:
        errors.append(f"{kind.value} must declare failed_phase={FAILURE_PHASES[kind]}")
    if kind in MATERIAL_RESULT_KINDS and "validation" not in completed:
        errors.append("material result requires completed validation")
    if kind in NO_SPEND_RESULT_KINDS and "quota_spend" in completed:
        errors.append(f"{kind.value} cannot spend quota")

    ok = not errors
    fully_committed = completed == list(TRANSACTION_PHASES)
    failed = kind in FAILURE_PHASES if kind is not None else False
    stopped = kind in STOP_RESULT_KINDS if kind is not None else False
    status = (
        "invalid"
        if not ok
        else "failed"
        if failed
        else "stopped"
        if stopped
        else "committed"
        if fully_committed
        else "validated"
        if "validation" in completed
        else "in_progress"
    )
    return {
        "ok": ok,
        "schema_version": LOOPX_TURN_RECEIPT_VALIDATION_SCHEMA_VERSION,
        "turn_key": turn_key or None,
        "lineage": {
            "goal_id": settlement_identity["goal_id"],
            "agent_id": settlement_identity["agent_id"],
            "todo_id": settlement_identity["todo_id"],
        },
        "settlement_effect_id": settlement_identity["effect_id"] or None,
        "result_kind": kind.value if kind is not None else None,
        "completed_phases": completed,
        "failed_phase": failed_phase,
        "status": status,
        "next_phase": (
            None
            if fully_committed or (ok and stopped)
            else failed_phase or expected_next
        ),
        "commit_eligibility": {
            "writeback": ok
            and kind in MATERIAL_RESULT_KINDS
            and "validation" in completed,
            "quota_spend": (
                ok
                and kind in MATERIAL_RESULT_KINDS
                and "durable_writeback" in completed
            ),
            "scheduler_ack": ok and "scheduler_apply" in completed,
        },
        "errors": errors,
    }
