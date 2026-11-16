"""Shared orchestration around the typed Turn recovery decision."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..goals.checkpoint_context_io import TurnResultWork

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


def preview_turn_recovery(plan: Mapping[str, Any], host_projection: Mapping[str, Any], *,
                          journal: Mapping[str, Any] | None, request: Mapping[str, Any],
                          goal_id: str, turn_key: str, retry_failed: bool,
                          session_binding_resolver: SessionBindingResolver | None,
                          empty_effects: Mapping[str, bool]) -> dict[str, Any]:
    """Read-only preview of the same retained-Turn recovery used by execution."""
    from .execution_readback import execution_payload
    from .journal_store import LOOPX_TURN_JOURNAL_SCHEMA_VERSION
    preview = {
        "schema_version": LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
        "status": "preview",
        "host": host_projection,
        "result_kind": None,
        "receipt": None,
        "scheduler": {"disposition": "not_evaluated"},
    }
    # A retained Turn may only need settlement. Project the same typed
    # recovery owner that execution consumes, without changing its journal
    # or calling any execution callback. A new Turn keeps the original plan.
    preview_plan = plan
    if journal is not None:
        envelope = plan.get("turn_envelope")
        assessment = assess_existing_turn_recovery(
            journal, request, goal_id=goal_id,
            agent_id=str(envelope.get("agent_id") or "")
            if isinstance(envelope, Mapping) else "",
            turn_key=turn_key,
            retry_failed=retry_failed,
            session_binding_resolver=session_binding_resolver,
        )
        preview_plan = {**plan, "route": {
            **plan["route"],
            "would_invoke_host": assessment.decision["reinvoke_host"],
        }}
        preview["recovery_audit"] = build_turn_recovery_audit(
            assessment.decision, journal, status="finished", host_invoked=False
        )
    return execution_payload(
        preview_plan,
        preview,
        execute=False,
        replayed=False,
        effects=empty_effects,
    )


def prepare_terminal_host_recovery(journal: Mapping[str, Any] | None, request: dict[str, Any], *,
                                   plan: Mapping[str, Any], goal_id: str, turn_key: str,
                                   result_work: TurnResultWork | None,
                                   session_binding_resolver: SessionBindingResolver | None) -> tuple[dict[str, Any], bool]:
    """Restore only unfinished terminal validation and its original request."""
    validation_reinvokes_host = _validation_reinvokes_host(journal)
    terminal_retry = (journal or {}).get("result_revalidation") or {}
    terminal_view = terminal_retry.get("validation", {})
    if terminal_retry.get("phase") == "terminal_closeout" and terminal_view.get("status") == "failed":
        assessment = assess_existing_turn_recovery({"schema_version": journal["schema_version"],
            "goal_id": goal_id, "turn_key": turn_key, "plan": journal["plan"], **terminal_view}, request,
            goal_id=goal_id, agent_id=str(plan["turn_envelope"]["agent_id"]), turn_key=turn_key,
            retry_failed=True, session_binding_resolver=session_binding_resolver)
        request = require_turn_recovery_continuation(assessment)
    terminal_work_changed = (result_work is not None and terminal_retry.get("phase") == "terminal_closeout"
        and terminal_retry.get("validation", {}).get("work_context") != result_work.read())
    needs_host = (
        validation_reinvokes_host
        or journal is None
        or "typed_result" not in list(journal.get("completed_phases") or [])
        or terminal_work_changed
        or isinstance(journal.get("result_revalidation"), dict) and
            journal["result_revalidation"].get("phase") == "terminal_closeout" and
            ("typed_result" not in journal["result_revalidation"].get("validation", {}).get("completed_phases", []) or
             journal["result_revalidation"].get("validation", {}).get("status") == "failed")
    )
    return request, needs_host


def _validation_reinvokes_host(journal: Mapping[str, Any] | None) -> bool:
    receipt = journal.get("receipt") if isinstance(journal, Mapping) else None
    return (
        isinstance(journal, Mapping)
        and journal.get("status") == "failed"
        and isinstance(receipt, Mapping)
        and receipt.get("failed_phase") == "validation"
        and journal.get("validation_stage") != "task_postcondition"
    )


def reset_failed_turn_for_retry(journal: dict[str, Any] | None,
                                persist_journal: Callable[[Mapping[str, Any]], None]) -> None:
    """Reset failed transport phases while retaining committed result facts."""
    from .transaction import TRANSACTION_PHASES
    if journal and journal.get("status") == "failed":
        receipt = (
            journal.get("receipt")
            if isinstance(journal.get("receipt"), dict)
            else {}
        )
        if receipt.get("failed_phase") == "validation":
            if _validation_reinvokes_host(journal):
                journal.pop("host_result", None)
            journal.pop("result_kind", None)
            journal["completed_phases"] = (
                list(TRANSACTION_PHASES[:2])
                if isinstance(journal.get("host_result"), dict)
                else []
            )
            journal.pop("task_validation", None)
            journal.pop("validation_stage", None)
        journal.pop("reason", None)
        journal.pop("receipt", None)
        journal.pop("host_recovery", None)
        journal.pop("host_failure", None)
        journal["status"] = "in_progress"
        persist_journal(journal)


def recover_existing_turn(plan: Mapping[str, Any], journal: Mapping[str, Any],
                          recovery_decision: Mapping[str, Any], *, empty_effects: Mapping[str, bool],
                          post_settlement: Callable[..., dict[str, Any]] | None) -> dict[str, Any]:
    """Read and recover only the already settled Turn's post-settlement work."""
    from .execution_readback import execution_payload
    from .post_settlement import recover_post_settlement
    payload = execution_payload(
        plan,
        journal,
        execute=True,
        replayed=True,
        effects=empty_effects,
    )
    payload.update(recover_post_settlement(
        plan=plan, post_settlement=post_settlement, journal=journal,
    ))
    payload["recovery"] = build_turn_recovery_audit(
        recovery_decision,
        journal,
        status="finished",
        host_invoked=False,
    )
    return payload


def admit_unfinished_host(plan: Mapping[str, Any], *, needs_host: bool,
                          admit_start: Callable[[Mapping[str, Any]], dict[str, Any]] | None,
                          journal: Mapping[str, Any] | None, turn_key: str,
                          host_projection: Mapping[str, Any], empty_effects: Mapping[str, bool]
                          ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Reserve cadence only for a real unfinished Host attempt, before writes."""
    from .execution_readback import execution_payload
    admission = None
    if needs_host and admit_start is not None:
        admission = admit_start(
            {
                "turn_key": turn_key,
                "attempt": int(journal.get("host_attempt_count") or 0) + 1
                if journal is not None
                else 1,
            }
        )
        if admission.get("admitted") is not True:
            waiting = {
                "status": "interval_wait",
                "host": host_projection,
                "completed_phases": [],
                "reason": str(
                    admission.get("reason") or "automatic start not admitted"
                ),
                "admission": admission,
            }
            return admission, execution_payload(
                plan, waiting, execute=True, replayed=False, effects=empty_effects
            )
    return admission, None
