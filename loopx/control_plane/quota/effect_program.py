from __future__ import annotations

import os
import shlex
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from ..effect_runtime import _encode_rpc_json, effect_runtime_result
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
from ..work_items.delivery_batch_scale import DELIVERY_BATCH_SCALE_INPUT_CHOICES
from ..turn_driver.native_context_profile import NATIVE_CONTEXT_PAGE_OUTPUT_BYTES

_PLATFORM_IS_POSIX = os.name == "posix"

_CLI_PLAN_PROJECTIONS: ContextVar[dict[bytes, Any] | None] = ContextVar(
    "quota_cli_plan_projections",
    default=None,
)


@contextmanager
def reuse_cli_settlement_plans() -> Iterator[None]:
    """Reuse pure rendering within one quota decision's projection passes."""
    token = _CLI_PLAN_PROJECTIONS.set({})
    try:
        yield
    finally:
        _CLI_PLAN_PROJECTIONS.reset(token)


def _render_cli_settlement_plan(request: dict[str, Any]) -> Any:
    projections = _CLI_PLAN_PROJECTIONS.get()
    if projections is None:
        return effect_runtime_result("settlement.turn_scoped_cli_plan", request)
    # Key by the complete typed request, including scope and command
    # arguments. Each projection gets an independent copy to edit.
    key = _encode_rpc_json(request)
    if key not in projections:
        projections[key] = effect_runtime_result("settlement.turn_scoped_cli_plan", request)
    return deepcopy(projections[key])


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
    "is_turn_reference",
    "receipt_bound_monitor_phase",
    "receipt_bound_replay_phase",
    "receipt_bound_terminal_phase",
    "required_environment_arg",
    "settlement_binding_args",
    "settlement_result_payload",
    "settlement_step_command",
    "turn_instance_arg",
    "turn_reference",
]


_POSIX_TURN_REF = "${LOOPX_TURN:?}"
_POWERSHELL_TURN_REF = "$LOOPX_TURN"
# Any POSIX shell reference names the same variable; only the guard differs.
_POSIX_TURN_REFS = frozenset({_POSIX_TURN_REF, "${LOOPX_TURN}"})


def _is_windows_host() -> bool:
    return os.name == "nt"


def required_environment_arg(name: str) -> str:
    """Expand a required environment variable without invoking the CLI if empty."""
    if _is_windows_host():
        return (
            f'"$(if (-not $env:{name}) {{ throw \'{name} must be set and non-empty\' }}; '
            f'$env:{name})"'
        )
    return f'"${{{name}:?}}"'


def _default_turn_ref() -> str:
    return _POWERSHELL_TURN_REF if _is_windows_host() else _POSIX_TURN_REF


def _quoted_turn_ref(turn_instance_id_ref: str) -> str:
    if turn_instance_id_ref == _POSIX_TURN_REF:
        return _default_turn_ref() if _is_windows_host() else f'"{_POSIX_TURN_REF}"'
    if turn_instance_id_ref == _POWERSHELL_TURN_REF and _is_windows_host():
        return turn_instance_id_ref
    return shlex.quote(turn_instance_id_ref)


def turn_reference(turn_instance_id: str | None = None) -> str:
    """The concrete Turn when one is known, else the reference this host expands."""

    return turn_instance_id or _default_turn_ref()


def is_turn_reference(value: object) -> bool:
    """Report whether a token is a shell-level Turn reference, not a concrete Turn."""

    if not isinstance(value, str):
        return False
    return value in _POSIX_TURN_REFS or value == _POWERSHELL_TURN_REF


def turn_instance_arg(turn_instance_id: str | None = None) -> str:
    """Render the ``--turn-instance-id`` argument for the shell that runs it.

    A caller holding a concrete Turn passes it through unchanged. Without one,
    the argument names the reference the current host shell can expand, so a
    native Windows host does not receive an empty Turn from POSIX syntax.
    """

    return f"--turn-instance-id {_quoted_turn_ref(turn_reference(turn_instance_id))}"


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
    autonomous_replan: bool = False,
    handoff_mode: str | None = None,
) -> SettlementPlan:
    return build_turn_scoped_cli_settlement_plan(
        goal_id=goal_id,
        agent_id=agent_id,
        command_prefix=command_prefix,
        todo_id=todo_id,
        replan_obligation_id=replan_obligation_id,
        scoped_cli_args=scoped_cli_args,
        lifecycle_actor_args=lifecycle_actor_args,
        turn_instance_id=turn_instance_id_ref or _default_turn_ref(),
        writeback_path_args=writeback_path_args,
        delivery_boundary=delivery_boundary,
        quota_spend_source=quota_spend_source,
        goal_ref=goal_ref,
        autonomous_replan=autonomous_replan,
        handoff_mode=handoff_mode,
    )


def build_turn_scoped_cli_settlement_plan(
    *,
    goal_id: str,
    agent_id: str,
    command_prefix: str = "loopx",
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    request_subject: Mapping[str, Any] | None = None,
    scoped_cli_args: str,
    lifecycle_actor_args: str,
    turn_instance_id: str,
    writeback_path_args: str = "",
    delivery_boundary: str | None = None,
    quota_spend_source: str = "heartbeat",
    goal_ref: Mapping[str, object] | None = None,
    autonomous_replan: bool = False,
    handoff_mode: str | None = None,
) -> SettlementPlan:
    if sum(bool(value) for value in (todo_id, replan_obligation_id, request_subject)) != 1:
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
        request_subject=request_subject,
    )
    scoped_cli_args = _settlement_actor_args(scoped_cli_args, identity.agent_id)
    lifecycle_actor_args = _settlement_actor_args(lifecycle_actor_args, identity.agent_id)
    normalized_goal_ref = _settlement_goal_ref(goal_ref, goal_id=identity.goal_id)
    quoted_turn = _quoted_turn_ref(turn_instance_id)
    binding_arg = (
        f" --todo-id {shlex.quote(todo_id)}"
        if todo_id
        else f" --replan-obligation-id {shlex.quote(str(replan_obligation_id))}" if replan_obligation_id else ""
    )
    turn_arg = f" --turn-instance-id {quoted_turn}"
    goal_ref_arg = (
        f" --goal-instance-id {shlex.quote(normalized_goal_ref['goal_instance_id'])}"
        if normalized_goal_ref is not None
        else ""
    )
    boundary_arg = (
        " --delivery-boundary <boundary>"
        if delivery_boundary == "in_flight_continuation"
        else ""
    )
    # Returned settlement commands are machine-readable without caller surgery.
    # Keep this global option before every subcommand, including quota spend-slot.
    cli_prefix = f"{command_prefix.strip() or 'loopx'} --format json"
    task_claim = (
        f"{cli_prefix} todo claim --goal-id {shlex.quote(goal_id)}{binding_arg}"
        f" --agent-id {shlex.quote(agent_id)} --claimed-by {shlex.quote(agent_id)}"
        f"{goal_ref_arg}"
    )
    # Preserve a caller's accepted execution reference or explicit lease proof.
    # This only renders CLI syntax; the canonical lifecycle still checks it.
    proof_options = {"--claim-operation-id", "--task-lease-idempotency-key", "--task-lease-expected-version"}
    completion_proof_supplied = any(
        token.partition("=")[0] in proof_options for token in shlex.split(lifecycle_actor_args)
    )
    ordinary_completion = (
        f"{cli_prefix} todo complete --goal-id {shlex.quote(goal_id)}{binding_arg}"
        f"{lifecycle_actor_args}{turn_arg}{goal_ref_arg} --evidence '<validated evidence>'"
    )
    terminal_closeout = ordinary_completion + " --no-follow-up"

    def writeback_command(boundary: str) -> str:
        return (
            f"{cli_prefix} refresh-state --goal-id {shlex.quote(goal_id)} "
            "--classification <validated_progress> --delivery-batch-scale <scale> "
            f"--delivery-outcome <outcome>{boundary}{binding_arg}{turn_arg}{goal_ref_arg}"
            f"{scoped_cli_args}{writeback_path_args}"
        )

    writeback = writeback_command(boundary_arg)
    semantic_writeback = (
        writeback_command(" --delivery-boundary semantic_closeout")
        + " --agent-vision-json <file>"
    )
    # Imported here so the settlement-plan kernel stays loadable on its own: the
    # capture-binding owner is orchestration a command template never needs.
    from ..turn_driver.decision_capture import private_capture_supported

    max_output_arg = (
        f" --max-page-output-bytes {NATIVE_CONTEXT_PAGE_OUTPUT_BYTES}"
        if private_capture_supported()
        else ""
    )
    spend = (
        f"{cli_prefix} quota spend-slot --goal-id {shlex.quote(goal_id)} --slots 1 "
        f"--source {quota_spend_source} --execute{binding_arg}{turn_arg}{goal_ref_arg}"
        f"{scoped_cli_args}{max_output_arg}"
    )
    payload = _render_cli_settlement_plan({
        "identity": identity.as_dict(), "delivery_boundary": delivery_boundary,
        "autonomous_replan": autonomous_replan,
        "handoff_mode": handoff_mode,
        "completion_proof_supplied": completion_proof_supplied,
        "delivery_batch_scale_choices": list(DELIVERY_BATCH_SCALE_INPUT_CHOICES),
        **({"goal_ref": normalized_goal_ref} if normalized_goal_ref is not None else {}),
        "command_templates": {
            **({"todo_claim": task_claim} if todo_id else {}),
            "todo_completion": ordinary_completion, "durable_writeback": writeback,
            "semantic_closeout": semantic_writeback,
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
            vision_authoring=row.get("vision_authoring"),
            semantic_closeout_command_template=row.get("semantic_closeout_command_template"),
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
    return binding_arg + " " + turn_instance_arg(turn_instance_id) + goal_ref_arg
