"""Owner-local failure reporting for the quota CLI command.

Keeping the honest failure and validation payloads beside the command handler
pushed that module over its size budget. They form one cohesive unit: what gets
logged, what the operator sees, and how a rejected request is reported without
inventing success.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

from ..control_plane.coordination.legacy_writer_fence import (
    LegacyCoordinationWriterFenced,
)
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
)
from ..control_plane.effect_runtime import (
    EffectRuntimeHostPermissionError,
    EffectRuntimeStartupError,
)
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.error_codes import (
    CloseoutQueryUnavailableError,
    HeartbeatReceiptIdentityConflictError,
    QuotaActionSelectionConflictError,
    QuotaCommandValidationError,
    QuotaIdentityPreconditionError,
    quota_error_code,
)
from ..file_lock import lock_timeout_error_fields

QUOTA_EVENT_KINDS = {
    "should-run": "quota_should_run",
    "monitor-poll": "quota_monitor_poll",
    "scheduler-ack": "quota_scheduler_ack",
    "scheduler-ack-current": "quota_scheduler_ack",
    "scheduler-fail-current": "quota_scheduler_failure",
    "spend-slot": "quota_spend",
    "void-slot": "quota_void",
}


def should_log_quota(command: str, payload: Mapping[str, object]) -> bool:
    return command in QUOTA_EVENT_KINDS and (
        command == "should-run"
        or (
            bool(payload.get("ok"))
            and (
                bool(payload.get("appended"))
                or bool(payload.get("receipt_repair_required"))
            )
        )
    )


def verbose_debug_fields(error: Exception, *, verbose: bool) -> dict[str, object]:
    if not verbose:
        return {}
    return {
        "verbose_debug": {
            "error_type": type(error).__name__,
            "error": str(error),
        }
    }


def quota_failure_payload(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    error: Exception,
) -> dict[str, object]:
    command = args.quota_command
    lock_timeout_fields = lock_timeout_error_fields(error)
    verbose_debug = verbose_debug_fields(
        error, verbose=bool(getattr(args, "verbose", False))
    )
    recommended_action = (
        error.recommended_action
        if isinstance(error, EffectRuntimeHostPermissionError)
        else "fix quota/status collection before spending automatic compute"
    )
    if command not in QUOTA_EVENT_KINDS:
        return {
            "ok": False,
            "mode": command,
            "registry": str(registry_path),
            "runtime_root": runtime_root_arg,
            "error_code": quota_error_code(error),
            "error": "quota collection failed",
            "summary": {
                "registered_goals": 0,
                "health_blockers": 1,
                "next_automatic_turn": None,
                "states": {},
            },
            "groups": {},
            "health_items": [
                {
                    "goal_id": "loopx-quota",
                    "status": "quota_collection_failed",
                    "waiting_on": "codex",
                    "severity": "high",
                    "recommended_action": recommended_action,
                    "source": "quota",
                }
            ],
            **verbose_debug,
            **lock_timeout_fields,
        }

    # A managed-runtime failure already carries a bounded, public-safe message
    # (which method could not be reached and how long it was given), so publish
    # it instead of a generic line the caller cannot act on.
    public_reason = (
        str(error)
        if isinstance(
            error,
            (CloseoutQueryUnavailableError, HeartbeatReceiptIdentityConflictError, EffectRuntimeStartupError),
        )
        else "quota collection failed"
    )
    payload: dict[str, object] = {
        "ok": False,
        "mode": command,
        "goal_id": args.goal_id,
        "decision": "skip",
        "should_run": False,
        "error_code": quota_error_code(error),
        "reason": public_reason,
        "state": "blocked_health",
        "waiting_on": "codex",
        "status": "quota_collection_failed",
        "source": "quota",
        "recommended_action": recommended_action,
        **verbose_debug,
        **lock_timeout_fields,
    }
    if isinstance(error, HeartbeatReceiptIdentityConflictError):
        payload.update({
            "status": "heartbeat_receipt_identity_conflict",
            "effective_action": EffectiveAction.QUOTA_SKIP.value,
            "recommended_action": (
                "Rerun quota should-run with the same --turn-instance-id and without "
                "--todo-id or --replan-obligation-id to read the bound Turn's recovery "
                "contract. Settle verified work or record its verified blocked closeout; "
                "do not replace the committed binding, repair receipt storage, or invent "
                "a new Turn id. Independent work belongs to the next real host Turn."
            ),
        })
    if isinstance(error, CloseoutQueryUnavailableError):
        payload.update({
            "status": error.diagnostic_code,
            "effective_action": EffectiveAction.CONTROL_PLANE_HEALTH_REPAIR.value,
            "recommended_action": (
                "check runtime health, then retry quota should-run with the same "
                "Turn identity to read the closeout state; do not infer settlement "
                "or replay work from a missing query response"
            ),
        })
    if isinstance(error, QuotaActionSelectionConflictError):
        # The requested Todo could not be reconciled with the projection. Report
        # the real conflict and the next read to make, rather than the generic
        # "quota collection failed" and a pointer at receipt writeback.
        selection_conflict: dict[str, object] = {
            "kind": error.kind.value,
            "requested_todo_id": error.requested_todo_id,
            "selected_todo_id": error.selected_todo_id,
            "qualification_state": error.qualification_state,
        }
        if error.unsettled_prior_turn_instance_id:
            selection_conflict["unsettled_prior_turn_instance_id"] = (
                error.unsettled_prior_turn_instance_id
            )
        if error.unsettled_repair:
            selection_conflict["unsettled_repair"] = error.unsettled_repair
        if (
            error.admission_must_attempt is not None
            or error.admission_delivery_allowed is not None
        ):
            selection_conflict["admission"] = {
                "agent_must_attempt": error.admission_must_attempt,
                "delivery_allowed": error.admission_delivery_allowed,
            }
        if error.retained_selection:
            selection_conflict["retained_selection"] = True
            selection_conflict["retained_selection_todo_id"] = error.selected_todo_id
        if error.receipt_replan_obligation_id:
            selection_conflict["receipt_replan_obligation_id"] = (
                error.receipt_replan_obligation_id
            )
        payload.update(
            {
                "reason": str(error),
                "status": "quota_action_selection_conflict",
                "recommended_action": error.recommended_action,
                "action_selection_conflict": selection_conflict,
            }
        )
    if isinstance(error, QuotaIdentityPreconditionError):
        payload.update(
            {
                "reason": str(error),
                "status": "quota_identity_precondition_failed",
                "identity_precondition": error.precondition.value,
                "recommended_action": error.recommended_action,
            }
        )
        if error.agent_id is not None:
            payload["agent_id"] = error.agent_id
    elif isinstance(error, (LegacyCoordinationWriterFenced, LocalCoordinationAuthorityUnavailable)):
        payload.update(
            {
                "error_code": error.code,
                "reason": str(error),
                **error.payload,
            }
        )
    if lock_timeout_fields:
        payload["recommended_action"] = "inspect the lock holder before retrying"
    if command == "monitor-poll":
        payload.update(
            {
                "source": args.source,
                "agent_id": args.agent_id,
                "todo_id": args.todo_id,
                "target_key": args.target_key,
                "result_hash": args.result_hash,
                "material_change": bool(args.material_change),
            }
        )
    elif command in {"scheduler-ack", "scheduler-ack-current"}:
        payload.update(
            {
                "agent_id": args.agent_id,
                "surface": args.surface,
                "state_key": args.state_key,
                "applied_rrule": args.applied_rrule,
            }
        )
    elif command == "scheduler-fail-current":
        payload.update(
            {
                "agent_id": args.agent_id,
                "surface": args.surface,
                "state_key": args.state_key,
                "failed_rrule": args.failed_rrule,
                "failure_kind": args.failure_kind,
            }
        )
    return payload


def quota_validation_failure_payload(
    args: argparse.Namespace,
    exc: QuotaCommandValidationError,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> dict[str, object]:
    command = args.quota_command
    if command not in QUOTA_EVENT_KINDS:
        return {
            "ok": False,
            "mode": command,
            "registry": str(registry_path),
            "runtime_root": runtime_root_arg,
            "error_code": "QUOTA_VALIDATION_FAILED",
            "error": str(exc),
            "summary": {
                "registered_goals": 0,
                "health_blockers": 0,
                "next_automatic_turn": None,
                "states": {},
            },
            "groups": {},
            "health_items": [],
        }
    return {
        "ok": False,
        "mode": command,
        "goal_id": args.goal_id,
        "decision": "skip",
        "should_run": False,
        "error_code": "QUOTA_VALIDATION_FAILED",
        "reason": str(exc),
        "state": "blocked_validation",
        "waiting_on": "codex",
        "status": "quota_validation_failed",
        "source": "quota",
        "recommended_action": "fix the command arguments before retrying",
    }




__all__ = [
    "QUOTA_EVENT_KINDS",
    "quota_failure_payload",
    "quota_validation_failure_payload",
    "should_log_quota",
    "verbose_debug_fields",
]
