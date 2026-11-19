"""Shared orchestration around the typed Turn recovery decision."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .session_recovery import (
    SessionBindingResolver,
    assess_failed_turn_retry_request,
)
from .turn_journal_runtime import interpret_turn_journal_projection


LOOPX_TURN_RECOVERY_AUDIT_SCHEMA_VERSION = "loopx_turn_recovery_audit_v0"


class TurnRecoveryBlockedError(ValueError):
    """A typed journal recovery refusal with its public-safe decision."""

    def __init__(self, decision: Mapping[str, Any], message: str) -> None:
        super().__init__(message)
        self.decision = dict(decision)


@dataclass(frozen=True)
class TurnRecoveryAssessment:
    """One typed decision plus the request and provider check it consumed."""

    inspection: dict[str, object]
    decision: dict[str, Any]
    request: dict[str, Any]
    error: ValueError | None


def assess_existing_turn_recovery(
    journal: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    retry_failed: bool,
    session_binding_resolver: SessionBindingResolver | None,
    assess_session: bool = True,
    session_recovery_check: Mapping[str, Any] | None = None,
    recovery_error: ValueError | None = None,
) -> TurnRecoveryAssessment:
    """Ask the TS-owned decision once, after any relevant Host Session check."""

    resolved_request = dict(request)
    resolved_check = (
        dict(session_recovery_check)
        if isinstance(session_recovery_check, Mapping)
        else None
    )
    resolved_error = recovery_error
    if assess_session and journal.get("status") == "failed" and retry_failed:
        resolved_request, resolved_check, resolved_error = (
            assess_failed_turn_retry_request(
                request,
                journal,
                session_binding_resolver=session_binding_resolver,
            )
        )
    inspection = interpret_turn_journal_projection(
        journal,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
        retry_failed=retry_failed,
        session_recovery_check=resolved_check,
    )
    decision_value = inspection.get("recovery_decision")
    if not isinstance(decision_value, Mapping):
        raise RuntimeError("Turn journal recovery decision shape mismatch")
    return TurnRecoveryAssessment(
        inspection=inspection,
        decision=dict(decision_value),
        request=resolved_request,
        error=resolved_error,
    )


def require_turn_recovery_continuation(
    assessment: TurnRecoveryAssessment,
) -> dict[str, Any]:
    """Return the reconciled request or raise the decision-bearing refusal."""

    if assessment.decision.get("action") == "continue":
        return dict(assessment.request)
    message = (
        str(assessment.error)
        if assessment.error is not None
        else "LoopX Turn journal recovery is blocked"
    )
    if assessment.decision.get("reason") == "host_outcome_unknown":
        message = (
            "Original Host attempt outcome is unknown; reconcile its execution and "
            "downstream effects before retrying. Preserve the original Turn journal."
        )
    raise TurnRecoveryBlockedError(assessment.decision, message)


def build_turn_recovery_audit(
    decision: Mapping[str, Any],
    journal: Mapping[str, Any],
    *,
    status: str,
    host_invoked: bool | None,
) -> dict[str, Any]:
    """Build the allowlisted planned-versus-actual recovery record."""

    return {
        "schema_version": LOOPX_TURN_RECOVERY_AUDIT_SCHEMA_VERSION,
        "planned": dict(decision),
        "actual": {
            "status": status,
            "journal_status": str(journal.get("status") or ""),
            "completed_phases": [
                str(phase) for phase in list(journal.get("completed_phases") or [])
            ],
            "host_invoked": host_invoked,
        },
    }
