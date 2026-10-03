"""Persist the typed resume decision inside the existing refresh serialization lock."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path
from .settlement import QuotaSettlementReadback, attach_settlement_progress, settlement_result_payload


def finish_external_delivery_refresh(
    payload: dict[str, Any], readback: QuotaSettlementReadback | None,
    runtime_root: Path, *, dry_run: bool,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if readback is None:
        return payload
    plan = readback.external_delivery
    if not isinstance(plan, dict) or plan.get("schema_version") != "refresh_external_delivery_v0":
        raise RuntimeError("TypeScript refresh external delivery result missing or invalid")
    payload["external_delivery"] = {k: v for k, v in plan.items() if k != "transition"}
    payload["external_sink_delivery_authorized"] = plan["authorized"] is True
    transition = plan.get("transition")
    if payload.get("ok") and transition and not dry_run:
        identity = readback.identity.value
        if identity is None:
            raise RuntimeError("external delivery transition has no settlement identity")
        append_rollout_event(
            rollout_event_log_path(runtime_root, identity.goal_id),
            build_rollout_event(
                goal_id=identity.goal_id, agent_id=identity.agent_id,
                todo_id=identity.todo_id, run_id=identity.turn_instance_id,
                event_kind="refresh_external_delivery", status=transition["state"],
                summary="Refresh external delivery preference recorded.", details=transition,
                goal_ref=goal_ref,
            ),
        )
        plan["transition"] = None  # The planned journal effect was committed once.
    return payload


def refresh_recovery_payload(
    readback: QuotaSettlementReadback, *, registry_path: Path,
    runtime_root: Path, goal_id: str, dry_run: bool,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    recovery = readback.refresh_recovery
    identity = readback.identity.value
    plan = readback.external_delivery
    if not recovery or identity is None or not isinstance(plan, dict):
        raise RuntimeError("TypeScript refresh admission result missing")
    decision = recovery["decision"]
    delivery_error = plan.get("error_code") if decision != "reject" else None
    if decision not in {"replay", "repair_receipt", "reject"} and not delivery_error:
        # Record a requested pause before a new writeback can commit. If later
        # validation fails, retaining the pause is conservative and retryable.
        if (plan.get("transition") or {}).get("state") == "paused":
            finish_external_delivery_refresh(
                {"ok": True},
                readback,
                runtime_root,
                dry_run=dry_run,
                goal_ref=goal_ref,
            )
        return None
    payload = {
        **(readback.writeback_run or {}), "ok": decision != "reject" and not delivery_error,
        "dry_run": dry_run, "appended": False,
        "idempotent_replay": decision == "replay" and not delivery_error,
        "receipt_repair_required": decision == "repair_receipt" and not dry_run,
        "registry": str(registry_path), "runtime_root": str(runtime_root), "goal_id": goal_id,
        "refresh_recovery": recovery, "settlement_identity": identity.as_dict(),
        "settlement_result": settlement_result_payload(readback.delivery),
    }
    if not dry_run:
        attach_settlement_progress(
            payload,
            readback,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_ref=goal_ref,
        )
    if decision == "reject":
        payload["error"] = (
            f"{recovery['reason']}: committed writeback is unchanged; "
            "do not begin a new Turn or repeat spend to repair it. "
            "Retry the original delivery fields with only the missing vision decision; "
            "if a newer vision already exists, inspect current quota instead."
        )
    elif delivery_error:
        payload["error_code"] = delivery_error
        payload["refresh_recovery"] = {**recovery, "decision": "reject", "reason": delivery_error}
        key = plan.get("resume_key")
        payload["error"] = (
            f"{delivery_error}: external delivery was not resumed. "
            "Keep --suppress-external-sinks for local recovery. "
            + (f"To deliberately resume this operation, retry the same recovery command with "
               f"--resume-external-sinks {key} instead of --suppress-external-sinks. " if key else "")
            + "Existing provider permissions still apply; do not repeat business mutations or spend."
        )
    return finish_external_delivery_refresh(
        payload,
        readback,
        runtime_root,
        dry_run=dry_run,
        goal_ref=goal_ref,
    )
