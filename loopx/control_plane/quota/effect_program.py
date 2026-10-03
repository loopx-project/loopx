from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from ..effect_runtime import effect_runtime_result
from ..effect_program import (
    SETTLEMENT_IDENTITY_SCHEMA_VERSION,
    SETTLEMENT_PLAN_SCHEMA_VERSION,
    SETTLEMENT_RECEIPT_SCHEMA_VERSION,
    ReceiptBoundMonitorPhase,
    ReceiptBoundReplayPhase,
    ReceiptBoundTerminalPhase,
    SettlementBindingKind,
    SettlementFailure,
    SettlementFailureKind,
    SettlementIdentity,
    SettlementPlan,
    SettlementReceipt,
    SettlementResult,
    SettlementStep,
    SettlementStepKind,
    receipt_bound_monitor_phase,
    receipt_bound_replay_phase,
    receipt_bound_terminal_phase,
    settlement_result_payload,
)
from ..goals.goal_ref_validation import exact_goal_ref

__all__ = [
    "SETTLEMENT_IDENTITY_SCHEMA_VERSION",
    "SETTLEMENT_PLAN_SCHEMA_VERSION",
    "SETTLEMENT_RECEIPT_SCHEMA_VERSION",
    "ReceiptBoundMonitorPhase",
    "ReceiptBoundReplayPhase",
    "ReceiptBoundTerminalPhase",
    "SettlementBindingKind",
    "SettlementFailure",
    "SettlementFailureKind",
    "SettlementIdentity",
    "SettlementPlan",
    "SettlementReceipt",
    "SettlementResult",
    "SettlementStep",
    "SettlementStepKind",
    "build_codex_app_settlement_plan",
    "build_turn_scoped_cli_settlement_plan",
    "receipt_bound_monitor_phase",
    "receipt_bound_replay_phase",
    "receipt_bound_terminal_phase",
    "settlement_binding_args",
    "settlement_result_payload",
    "settlement_step_command",
]


def _quoted_turn_ref(turn_instance_id_ref: str) -> str:
    if turn_instance_id_ref == "${LOOPX_TURN:?}":
        return '"${LOOPX_TURN:?}"'
    return shlex.quote(turn_instance_id_ref)


def _settlement_actor_args(arguments: str, agent_id: str) -> str:
    """Identity owns the actor; optional host arguments cannot omit or retarget it."""
    tokens = shlex.split(arguments)
    actors = []
    for index, token in enumerate(tokens):
        if token == "--agent-id":
            actors.append(tokens[index + 1] if index + 1 < len(tokens) else None)
        elif token.startswith("--agent-id="):
            actors.append(token.partition("=")[2])
    if actors and actors != [agent_id]:
        raise ValueError("settlement command actor must match its exact identity")
    if actors:
        return arguments if arguments[:1].isspace() else f" {arguments}"
    return f"{arguments} --agent-id {shlex.quote(agent_id)}"


def _settlement_goal_ref(
    goal_ref: Mapping[str, object] | None,
    *,
    goal_id: str,
) -> dict[str, str] | None:
    if goal_ref is None:
        return None

    normalized = exact_goal_ref(
        str(goal_ref.get("goal_id") or ""),
        str(goal_ref.get("goal_instance_id") or ""),
    )
    if normalized["goal_id"] != goal_id:
        raise ValueError("settlement GoalRef does not match goal_id")
    return normalized


def build_codex_app_settlement_plan(
    *,
    goal_id: str,
    agent_id: str,
    command_prefix: str = "loopx",
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    scoped_cli_args: str,
    lifecycle_actor_args: str,
    turn_instance_id_ref: str | None = None,
    writeback_path_args: str = "",
    delivery_boundary: str | None = None,
    quota_spend_source: str = "heartbeat",
    goal_ref: Mapping[str, object] | None = None,
) -> SettlementPlan:
    return build_turn_scoped_cli_settlement_plan(
        goal_id=goal_id,
        agent_id=agent_id,
        command_prefix=command_prefix,
        todo_id=todo_id,
        replan_obligation_id=replan_obligation_id,
        scoped_cli_args=scoped_cli_args,
        lifecycle_actor_args=lifecycle_actor_args,
        turn_instance_id=turn_instance_id_ref or "${LOOPX_TURN:?}",
        writeback_path_args=writeback_path_args,
        delivery_boundary=delivery_boundary,
        quota_spend_source=quota_spend_source,
        goal_ref=goal_ref,
    )


def build_turn_scoped_cli_settlement_plan(
    *,
    goal_id: str,
    agent_id: str,
    command_prefix: str = "loopx",
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    scoped_cli_args: str,
    lifecycle_actor_args: str,
    turn_instance_id: str,
    writeback_path_args: str = "",
    delivery_boundary: str | None = None,
    quota_spend_source: str = "heartbeat",
    goal_ref: Mapping[str, object] | None = None,
) -> SettlementPlan:
    if bool(todo_id) == bool(replan_obligation_id):
        raise ValueError(
            "turn-scoped CLI settlement requires exactly one Todo or autonomous "
            "replan obligation binding"
        )
    if quota_spend_source not in {"heartbeat", "visible-goal"}:
        raise ValueError(
            "turn-scoped CLI settlement requires quota_spend_source heartbeat "
            "or visible-goal"
        )
    identity = SettlementIdentity(
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=replan_obligation_id,
    )
    scoped_cli_args = _settlement_actor_args(scoped_cli_args, identity.agent_id)
    lifecycle_actor_args = _settlement_actor_args(lifecycle_actor_args, identity.agent_id)
    normalized_goal_ref = _settlement_goal_ref(goal_ref, goal_id=identity.goal_id)
    quoted_turn = _quoted_turn_ref(turn_instance_id)
    binding_arg = (
        f" --todo-id {shlex.quote(todo_id)}"
        if todo_id
        else f" --replan-obligation-id {shlex.quote(str(replan_obligation_id))}"
    )
    turn_arg = f" --turn-instance-id {quoted_turn}"
    goal_ref_arg = (
        f" --goal-instance-id {shlex.quote(normalized_goal_ref['goal_instance_id'])}"
        if normalized_goal_ref is not None
        else ""
    )
    boundary_arg = (
        " --delivery-boundary in_flight_continuation"
        if delivery_boundary == "in_flight_continuation"
        else ""
    )
    cli_prefix = command_prefix.strip() or "loopx"
    ordinary_completion = (
        f"{cli_prefix} todo complete --goal-id {shlex.quote(goal_id)}{binding_arg}"
        f"{lifecycle_actor_args}{turn_arg}{goal_ref_arg} --evidence '<validated evidence>'"
    )
    terminal_closeout = ordinary_completion + " --no-follow-up"
    writeback = (
        f"{cli_prefix} refresh-state --goal-id {shlex.quote(goal_id)} "
        "--classification <validated_progress> --delivery-batch-scale <scale> "
        f"--delivery-outcome <outcome>{boundary_arg}{binding_arg}{turn_arg}{goal_ref_arg}"
        f"{scoped_cli_args}{writeback_path_args}"
    )
    spend = (
        f"{cli_prefix} quota spend-slot --goal-id {shlex.quote(goal_id)} --slots 1 "
        f"--source {quota_spend_source} --execute{binding_arg}{turn_arg}{goal_ref_arg}"
        f"{scoped_cli_args}"
    )
    payload = effect_runtime_result("settlement.turn_scoped_cli_plan", {
        "identity": identity.as_dict(), "delivery_boundary": delivery_boundary,
        **({"goal_ref": normalized_goal_ref} if normalized_goal_ref is not None else {}),
        "command_templates": {
            "todo_completion": ordinary_completion, "durable_writeback": writeback,
            "quota_spend": spend, "terminal_closeout": terminal_closeout,
        },
    })
    if not isinstance(payload, Mapping) or not isinstance(payload.get("ordered_steps"), list):
        raise RuntimeError("TypeScript CLI settlement plan shape mismatch")
    return SettlementPlan(identity=identity, steps=tuple(
        SettlementStep(
            kind=SettlementStepKind(row["kind"]), owner=row["owner"],
            precondition=row["precondition"], idempotency_key_ref=row["idempotency_key_ref"],
            expected_receipt=row["expected_receipt"], command_template=row.get("command_template"),
            conditional=row.get("conditional", False), command_condition=row.get("command_condition"),
        ) for row in payload["ordered_steps"]
    ), _runtime_payload=payload)


def settlement_step_command(
    plan: Mapping[str, Any] | None,
    kind: SettlementStepKind,
) -> str | None:
    if not isinstance(plan, Mapping):
        return None
    steps = plan.get("ordered_steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, Mapping) or step.get("kind") != kind.value:
            continue
        command = str(step.get("command_template") or "").strip()
        return command or None
    return None


def settlement_binding_args(plan: Mapping[str, Any] | None) -> str:
    if not isinstance(plan, Mapping):
        return ""
    identity = plan.get("identity")
    if not isinstance(identity, Mapping):
        return ""
    todo_id = str(identity.get("todo_id") or "").strip()
    replan_obligation_id = str(identity.get("replan_obligation_id") or "").strip()
    turn_instance_id = str(identity.get("turn_instance_id") or "").strip()
    if bool(todo_id) == bool(replan_obligation_id) or not turn_instance_id:
        return ""
    binding_arg = (
        f" --todo-id {shlex.quote(todo_id)}"
        if todo_id
        else f" --replan-obligation-id {shlex.quote(replan_obligation_id)}"
    )
    goal_ref = plan.get("goal_ref")
    goal_ref_arg = ""
    if isinstance(goal_ref, Mapping):
        normalized_goal_ref = _settlement_goal_ref(
            goal_ref,
            goal_id=str(identity.get("goal_id") or ""),
        )
        assert normalized_goal_ref is not None
        goal_ref_arg = (
            f" --goal-instance-id "
            f"{shlex.quote(normalized_goal_ref['goal_instance_id'])}"
        )
    return (
        binding_arg
        + f" --turn-instance-id {_quoted_turn_ref(turn_instance_id)}"
        + goal_ref_arg
    )
