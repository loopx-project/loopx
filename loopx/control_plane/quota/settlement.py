from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..settlement_driver import decode_settlement_result
from .effect_program import (
    SETTLEMENT_IDENTITY_SCHEMA_VERSION,
    SETTLEMENT_PLAN_SCHEMA_VERSION,
    SETTLEMENT_RECEIPT_SCHEMA_VERSION,
    SettlementFailure,
    SettlementFailureKind,
    SettlementIdentity,
    SettlementPlan,
    SettlementReceipt,
    SettlementResult,
    SettlementStep,
    SettlementStepKind,
    ReceiptBoundMonitorPhase,
    ReceiptBoundReplayPhase,
    build_codex_app_settlement_plan,
    build_turn_scoped_cli_settlement_plan,
    settlement_binding_args,
    settlement_result_payload,
    settlement_step_command,
)

QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA = (
    "loopx_quota_settlement_readback_request_v0"
)
QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA = (
    "loopx_quota_settlement_readback_result_v0"
)
SEMANTIC_REPLAN_GUARD_SCHEMA = "semantic_replan_guard_v0"


def _checkpoint_instructions(checkpoint: Mapping[str, Any]) -> str:
    lines = [
        "Submit a checkpoint-only refresh with the same Goal, Agent, Todo/obligation, "
        "Turn, and delivery fields, from the original working directory.",
        "- Read first: Run `loopx checkpoint-context` with the same `--goal-id`, "
        "`--agent-id`, `--todo-id` or `--replan-obligation-id`, `--turn-instance-id`, "
        "and original registry/runtime/project/state-file options. Include "
        "`--dependency-todo-id` for any additional upstream Todo result used in the "
        "judgment. Read its returned basis and judge again; echo `read_context_id` "
        "as `--checkpoint-read-context` in the supplement. Confirmations for the "
        "same Turn are serial: a reread replaces the old receipt. If stale or "
        "replaced, reread and rejudge; never substitute a new token onto an old judgment.",
        "- Preserve: Keep original values and presence for target, scope, and isolation "
        "options: `--registry`, `--runtime-root`, `--project`, `--state-file`, "
        "`--progress-scope`, `--agent-lane`, `--available-capability`, "
        "`--no-global-sync`, `--suppress-external-sinks`, `--resume-external-sinks`; "
        "identity and delivery options: "
        "`--goal-id`, `--agent-id`, `--todo-id`, `--replan-obligation-id`, "
        "`--turn-instance-id`, `--completion-todo-id`, `--completion-turn-key`, "
        "`--classification`, `--recommended-action`, `--delivery-batch-scale`, "
        "`--delivery-outcome`, `--delivery-boundary`, `--delivery-workspace-path`, "
        "`--progress-result-class`, `--progress-surface-id`, `--progress-hypothesis-id`, "
        "`--progress-probe-kind`, `--progress-blocker-id`, `--progress-coverage-scope-id`, "
        "`--progress-evidence-id`, `--progress-coverage-complete`. Do not add options absent "
        "from the original command or change its delivery target.",
        "- Remove: Remove previously executed state-mutation options, even when their "
        "values are unchanged: `--next-action`, `--autonomous-replan-recorded`, "
        "`--repair-delta-kind`, `--usage-json`, `--usage-codex-session`. "
        "Remove dependent options that become invalid without them.",
        "- Add: Echo `--checkpoint-read-context` from the read, and add only one valid vision decision: a valid `--agent-vision-json` packet "
        "or inline `--vision-*` patch containing your authored vision content.",
    ]
    if checkpoint.get("missing_baseline") is True:
        lines.append(
            "No persisted vision baseline is available; an unchanged reason cannot "
            "satisfy this checkpoint."
        )
    else:
        lines[-1] += (
            " Alternatively use `--vision-unchanged-reason` only if a persisted vision "
            "exists and its scope and acceptance still apply."
        )
    lines.append(
        "Do not repeat implementation, manufacture a successor, or begin another "
        "Turn solely to repair this checkpoint. Keep the ordinary one-spend "
        "settlement order. Recovery remains subject to existing validation and "
        "does not certify acceptance or bypass replan, identity, or terminal gates."
    )
    return "\n".join(lines)


def render_first_refresh_checkpoint_hint(payload: dict[str, Any]) -> list[str]:
    """Explain the first committed missing checkpoint without changing admission."""
    recovery = payload.get("refresh_recovery")
    identity = payload.get("settlement_identity")
    checkpoint = payload.get("vision_checkpoint")
    if (
        payload.get("ok") is True
        and payload.get("appended") is True
        and payload.get("dry_run") is False
        and isinstance(recovery, dict)
        and recovery.get("decision") == "append"
        and recovery.get("reason") == "first_writeback"
        and isinstance(identity, dict)
        and identity
        and isinstance(checkpoint, dict)
        and checkpoint.get("required") is True
        and checkpoint.get("satisfied") is False
        and checkpoint.get("decision") == "missing_required"
    ):
        return [
            "The writeback succeeded, but the required vision checkpoint is still unsatisfied.",
            _checkpoint_instructions(checkpoint),
        ]
    return []


def render_refresh_recovery_markdown(payload: dict[str, Any]) -> str | None:
    """Present an admitted recovery result without deriving settlement policy."""
    recovery = payload.get("refresh_recovery") or {}
    if recovery.get("decision") not in {"replay", "repair_receipt", "reject"}:
        return None
    lines = [
        "# LoopX State Refresh",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- recovery: `{recovery['decision']}`",
        f"- reason: `{recovery.get('reason')}`",
        "- appended: `False` — original writeback preserved; no new delivery or spend.",
    ]
    lines.extend(render_settlement_progress_markdown(payload))
    checkpoint = payload.get("vision_checkpoint") or {}
    if checkpoint:
        lines.append(
            f"- vision_checkpoint: `{checkpoint.get('decision')}`; satisfied={checkpoint.get('satisfied')}"
        )
    if payload.get("error"):
        lines.append(str(payload["error"]))
    elif checkpoint.get("satisfied") is False:
        lines.append(_checkpoint_instructions(checkpoint))
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class QuotaSettlementReadback:
    identity: SettlementResult[SettlementIdentity]
    writeback: SettlementResult[dict[str, Any]]
    spend: SettlementResult[dict[str, Any]]
    delivery: SettlementResult[dict[str, Any]]
    settlement: SettlementResult[dict[str, Any]]
    terminal_closeout: SettlementResult[dict[str, Any]]
    terminal_settlement: SettlementResult[dict[str, Any]]
    workspace_causality: dict[str, str] | None
    semantic_replan_guard: dict[str, str | None] | None
    writeback_run: dict[str, Any] | None
    spend_run: dict[str, Any] | None
    heartbeat_receipt: dict[str, Any] | None
    writeback_event: dict[str, Any] | None
    spend_event: dict[str, Any] | None
    completion_event: dict[str, Any] | None
    monitor_phase: ReceiptBoundMonitorPhase | None
    replay_phase: ReceiptBoundReplayPhase | None
    refresh_recovery: dict[str, Any] | None = None
    external_delivery: dict[str, Any] | None = None
    progress: dict[str, Any] | None = None


def attach_settlement_progress(
    payload: dict[str, Any],
    readback: QuotaSettlementReadback,
    *,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
) -> None:
    """Render the TS-owned receipt progress without deriving a second settlement rule."""
    progress = readback.progress
    if not isinstance(progress, dict) or progress.get("schema_version") != "quota_settlement_progress_v0":
        raise RuntimeError("TypeScript quota settlement progress missing or invalid")
    payload["settlement_progress"] = dict(progress)
    payload.pop("settlement_owed", None)
    identity = readback.identity.value
    if payload.get("ok") is not True or progress.get("next_step") != "quota_spend" or identity is None:
        return
    prefix = "loopx"
    if registry_path is not None:
        prefix += f" --registry {shlex.quote(str(registry_path))}"
    if runtime_root is not None:
        prefix += f" --runtime-root {shlex.quote(str(runtime_root))}"
    plan = build_turn_scoped_cli_settlement_plan(
        goal_id=identity.goal_id,
        agent_id=identity.agent_id,
        todo_id=identity.todo_id,
        replan_obligation_id=identity.replan_obligation_id,
        turn_instance_id=identity.turn_instance_id,
        command_prefix=prefix,
        scoped_cli_args="",
        lifecycle_actor_args="",
        quota_spend_source=progress["quota_spend_source"],
    )
    payload["settlement_owed"] = {
        **identity.as_dict(), "schema_version": "turn_settlement_owed_v0",
        "kind": "quota_spend", "recovery_does_not_spend": True,
        "reason": (
            "quota spend is committed but its receipt is missing; retry the same identity to repair it without another debit"
            if progress["state"] == "spend_receipt_required" else
            "writeback is verified; execute quota spend once for the same settlement identity"
        ),
        "command": settlement_step_command(plan.as_dict(), SettlementStepKind.QUOTA_SPEND),
    }


def render_settlement_progress_markdown(payload: dict[str, Any]) -> list[str]:
    progress = payload.get("settlement_progress")
    if not isinstance(progress, dict):
        return []
    lines = [f"- settlement: `{progress.get('state')}`"]
    if progress.get("closeout_kind") == "typed_blocked_writeback_no_spend":
        lines.append("- closeout: typed blocked writeback; no quota slot spent")
    owed = payload.get("settlement_owed")
    if isinstance(owed, dict):
        lines.extend([f"- settlement_owed: {owed['reason']}", "", "```sh", owed["command"], "```"])
    return lines


__all__ = [
    "SETTLEMENT_IDENTITY_SCHEMA_VERSION",
    "SETTLEMENT_PLAN_SCHEMA_VERSION",
    "SETTLEMENT_RECEIPT_SCHEMA_VERSION",
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
    "read_heartbeat_settlement",
    "settlement_binding_args",
    "settlement_result_payload",
    "settlement_step_command",
]


def _readback_result(
    payload: Any,
    *,
    identity: bool = False,
) -> SettlementResult[Any]:
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript quota settlement readback result shape mismatch")
    result = payload.get("result")
    projection = payload.get("payload")
    if not isinstance(result, Mapping) or not isinstance(projection, Mapping):
        raise RuntimeError("TypeScript quota settlement readback result shape mismatch")
    return decode_settlement_result(
        result,
        value_decoder=(
            SettlementIdentity.from_runtime_payload if identity else None
        ),
        projection_payload=projection,
    )


def _optional_readback_record(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RuntimeError("TypeScript quota settlement readback result shape mismatch")
    return dict(value)


def _semantic_replan_guard(value: Any) -> dict[str, str | None] | None:
    guard = _optional_readback_record(value)
    if guard is None:
        return None
    scope = guard.get("scope")
    selected_obligation_id = guard.get("selected_obligation_id")
    selected_obligation_is_malformed = (
        selected_obligation_id is not None
        and not isinstance(selected_obligation_id, str)
    )
    legacy_guard_claims_selection = (
        scope == "legacy_unscoped" and selected_obligation_id is not None
    )
    if (
        guard.get("schema_version") != SEMANTIC_REPLAN_GUARD_SCHEMA
        or scope not in {"legacy_unscoped", "turn_guard"}
        or selected_obligation_is_malformed
        or legacy_guard_claims_selection
    ):
        raise RuntimeError("TypeScript semantic replan guard shape mismatch")
    return {
        "schema_version": SEMANTIC_REPLAN_GUARD_SCHEMA,
        "scope": str(scope),
        "selected_obligation_id": selected_obligation_id,
    }


def read_heartbeat_settlement(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str | None,
    todo_id: str | None,
    turn_instance_id: str | None,
    replan_obligation_id: str | None = None,
    infer_turn_instance_id: bool = False,
    allow_unbound_binding: bool = False,
    refresh_retry: dict[str, Any] | None = None,
) -> QuotaSettlementReadback | None:
    """Read one complete heartbeat settlement through the TS domain owner."""

    try:
        payload = effect_runtime_result(
            "quota.settlement.read",
            {
                "schema_version": QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
                "runtime_root": str(runtime_root.expanduser()),
                "goal_id": goal_id,
                "agent_id": agent_id,
                "todo_id": todo_id,
                "turn_instance_id": turn_instance_id,
                "replan_obligation_id": replan_obligation_id,
                "infer_turn_instance_id": infer_turn_instance_id,
                "allow_unbound_binding": allow_unbound_binding,
                **(
                    {"refresh_retry": refresh_retry}
                    if refresh_retry is not None
                    else {}
                ),
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(payload, Mapping) or (
        payload.get("schema_version")
        != QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA
    ):
        raise RuntimeError("TypeScript quota settlement readback result shape mismatch")
    if payload.get("found") is False:
        if set(payload) != {"schema_version", "found"}:
            raise RuntimeError(
                "TypeScript quota settlement readback result shape mismatch"
            )
        return None
    if payload.get("found") is not True:
        raise RuntimeError("TypeScript quota settlement readback result shape mismatch")
    workspace_causality = _optional_readback_record(
        payload.get("workspace_causality")
    )
    monitor_phase = payload.get("monitor_phase")
    replay_phase = payload.get("replay_phase")
    if monitor_phase not in {None, "poll_due", "settlement_pending", "settled"} or (
        replay_phase not in {None, "open", "settlement_pending", "settled"}
    ):
        raise RuntimeError("TypeScript quota settlement readback result shape mismatch")
    return QuotaSettlementReadback(
        identity=_readback_result(payload.get("identity"), identity=True),
        writeback=_readback_result(payload.get("writeback")),
        spend=_readback_result(payload.get("spend")),
        delivery=_readback_result(payload.get("delivery")),
        settlement=_readback_result(payload.get("settlement")),
        terminal_closeout=_readback_result(payload.get("terminal_closeout")),
        terminal_settlement=_readback_result(payload.get("terminal_settlement")),
        workspace_causality=(
            {str(key): str(value) for key, value in workspace_causality.items()}
            if workspace_causality is not None
            else None
        ),
        semantic_replan_guard=_semantic_replan_guard(
            payload.get("semantic_replan_guard")
        ),
        writeback_run=_optional_readback_record(payload.get("writeback_run")),
        refresh_recovery=_optional_readback_record(payload.get("refresh_recovery")),
        external_delivery=_optional_readback_record(payload.get("external_delivery")),
        progress=_optional_readback_record(payload.get("progress")),
        spend_run=_optional_readback_record(payload.get("spend_run")),
        heartbeat_receipt=_optional_readback_record(payload.get("heartbeat_receipt")),
        writeback_event=_optional_readback_record(payload.get("writeback_event")),
        spend_event=_optional_readback_record(payload.get("spend_event")),
        completion_event=_optional_readback_record(payload.get("completion_event")),
        monitor_phase=(
            ReceiptBoundMonitorPhase(str(monitor_phase))
            if monitor_phase is not None
            else None
        ),
        replay_phase=(
            ReceiptBoundReplayPhase(str(replay_phase))
            if replay_phase is not None
            else None
        ),
    )
