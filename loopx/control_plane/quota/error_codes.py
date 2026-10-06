from __future__ import annotations

import json
from enum import StrEnum

from ..effect_runtime import EffectRuntimeHostPermissionError


class CloseoutQueryUnavailableError(RuntimeError):
    """A read-only closeout query returned no verified result; no verdict exists."""

    error_code = "quota_closeout_query_unavailable"
    diagnostic_code = "closeout_query_unavailable"


class QuotaCommandValidationError(ValueError):
    """Public-safe diagnostic for an invalid ``loopx quota`` invocation."""


class HeartbeatReceiptIdentityConflictError(ValueError):
    """Public-safe diagnostic for a same-turn settlement identity conflict."""


class QuotaIdentityPrecondition(StrEnum):
    """Typed identity admission preconditions for scoped quota decisions."""

    PUBLIC_SAFE_AGENT_ID = "public_safe_agent_id"
    REGISTERED_AGENT_ROSTER_PRESENT = "registered_agent_roster_present"
    REQUESTED_AGENT_REGISTERED = "requested_agent_registered"


class QuotaIdentityPreconditionError(ValueError):
    """Public-safe failure raised before a scoped quota decision can be built."""

    def __init__(
        self,
        precondition: QuotaIdentityPrecondition,
        *,
        agent_id: str | None = None,
    ) -> None:
        self.precondition = precondition
        self.agent_id = agent_id
        if precondition is QuotaIdentityPrecondition.PUBLIC_SAFE_AGENT_ID:
            self.error_code = "quota_agent_id_invalid"
            self.recommended_action = (
                "use a public-safe agent id registered in the selected registry, "
                "then retry"
            )
            reason = "agent_id must be a public-safe registered agent id"
        elif precondition is QuotaIdentityPrecondition.REGISTERED_AGENT_ROSTER_PRESENT:
            self.error_code = "quota_agent_registry_roster_missing"
            self.recommended_action = (
                "register this agent in coordination.registered_agents of the "
                "selected registry, then rerun quota should-run with the same "
                "--registry and --agent-id"
            )
            reason = "selected registry goal is missing coordination.registered_agents"
        else:
            self.error_code = "quota_agent_not_registered"
            self.recommended_action = (
                "register this agent in coordination.registered_agents of the "
                "selected registry, then rerun quota should-run with the same "
                "--registry and --agent-id"
            )
            reason = (
                f"agent_id={agent_id!r} is not registered in the selected registry goal"
            )
        super().__init__(reason)


class QuotaActionSelectionConflictKind(StrEnum):
    """Why a requested ``--todo-id`` could not be reconciled with the projection."""

    UNQUALIFIED = "unqualified"
    CONFLICT = "conflict"
    NOT_ADMITTED = "not_admitted"


class QuotaActionSelectionConflictError(RuntimeError):
    """Public-safe diagnostic for an unreconcilable requested action selection.

    A guard bound to a ``--todo-id`` has to agree with the current projection.
    When it cannot, this error names what was requested, what the projection
    selects or the Turn retains, and what the caller should do next, so the
    failure is not reported as an opaque quota collection failure.
    """

    error_code = "quota_action_selection_conflict"

    def __init__(
        self,
        kind: QuotaActionSelectionConflictKind,
        *,
        requested_todo_id: str | None,
        selected_todo_id: str | None = None,
        qualification_state: str | None = None,
        unsettled_prior_turn_instance_id: str | None = None,
        unsettled_repair: str | None = None,
        admission_must_attempt: bool | None = None,
        admission_delivery_allowed: bool | None = None,
        receipt_replan_obligation_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.requested_todo_id = requested_todo_id
        self.selected_todo_id = selected_todo_id
        self.qualification_state = qualification_state
        self.unsettled_prior_turn_instance_id = unsettled_prior_turn_instance_id
        self.unsettled_repair = unsettled_repair
        self.admission_must_attempt = admission_must_attempt
        self.admission_delivery_allowed = admission_delivery_allowed
        self.receipt_replan_obligation_id = receipt_replan_obligation_id
        self.retained_selection = bool(
            kind is QuotaActionSelectionConflictKind.CONFLICT
            and receipt_replan_obligation_id
            and selected_todo_id
        )
        if kind is QuotaActionSelectionConflictKind.UNQUALIFIED:
            reason = (
                "the current projection carries no typed action-selection "
                "qualification, so the requested Todo "
                f"{requested_todo_id or '(none)'} cannot be reconciled with the "
                "delivery frontier"
            )
        elif kind is QuotaActionSelectionConflictKind.NOT_ADMITTED:
            # The projection already selects the requested Todo, so calling this
            # a selection conflict would name the same id on both sides of the
            # sentence and leave the caller with no next read.  Name the refusal
            # that actually happened, and the prior Turn that owes a closeout
            # when the payload carries one.
            reason = (
                f"the requested Todo {requested_todo_id or '(none)'} is the "
                "projection's current selection, so this Turn was not refused by "
                "a selection conflict; the Turn was not admitted to settle that "
                f"Todo (qualification state: {qualification_state or 'absent'})"
            )
            if admission_must_attempt is not None or (
                admission_delivery_allowed is not None
            ):
                reason += (
                    "; admission facts: agent must_attempt="
                    f"{admission_must_attempt}, delivery_allowed="
                    f"{admission_delivery_allowed}"
                )
            if unsettled_prior_turn_instance_id:
                reason += (
                    f"; the prior Turn {unsettled_prior_turn_instance_id} is still "
                    "unsettled and must be settled first"
                    + (
                        f" (repair: {unsettled_repair})"
                        if unsettled_repair
                        else ""
                    )
                )
        elif self.retained_selection:
            # The Turn's receipt is bound to an autonomous replan obligation, so
            # the requested Todo cannot replace the selection that Turn already
            # retains.  The default conflict sentence calls that id "the
            # projection's current selection", which is not what the caller is
            # up against: name the retained selection and the obligation that
            # owns the Turn.
            reason = (
                f"requested Todo {requested_todo_id or '(none)'} cannot replace "
                "the retained pending selection "
                f"{selected_todo_id or 'none'} on this Turn: the Turn's receipt is "
                "bound to the autonomous replan obligation "
                f"{receipt_replan_obligation_id or '(unnamed)'}, which owns its "
                "settlement"
            )
        elif receipt_replan_obligation_id:
            reason = (
                f"requested Todo {requested_todo_id or '(none)'} cannot replace "
                "this Turn's settlement identity: it is bound to the autonomous "
                f"replan obligation {receipt_replan_obligation_id}, and no pending "
                "Todo selection is retained"
            )
        else:
            reason = (
                f"requested Todo {requested_todo_id or '(none)'} is neither the "
                "projection's current selection "
                f"({selected_todo_id or 'none'}) nor deferred or rejected by it "
                f"(qualification state: {qualification_state or 'absent'})"
            )
        if kind is QuotaActionSelectionConflictKind.NOT_ADMITTED:
            self.recommended_action = (
                "read the payload's admission facts and delivery boundary; when "
                "the prior Turn named in the reason is the blocker, settle that "
                "Turn first and then rerun this Turn"
            )
        elif self.retained_selection:
            self.recommended_action = (
                "settle the autonomous replan obligation that owns this Turn, or "
                "rerun `loopx quota should-run` without --todo-id to read the "
                "selection the Turn retains; do not rebind the retained selection"
            )
        elif receipt_replan_obligation_id:
            self.recommended_action = (
                "settle the autonomous replan obligation that owns this Turn, "
                "then start a fresh Turn and rerun `loopx quota should-run` to "
                "select a Todo; do not rebind this Turn"
            )
        else:
            self.recommended_action = (
                "rerun `loopx quota should-run` without --todo-id to read the current "
                "selection, then bind that Todo, a deferred Todo, or the Todo the "
                "recovery obligation must settle"
            )
        super().__init__(reason)


def quota_error_code(exc: BaseException) -> str:
    if isinstance(exc, EffectRuntimeHostPermissionError):
        return "quota_runtime_permission_denied"
    if isinstance(exc, CloseoutQueryUnavailableError):
        return exc.error_code
    if isinstance(exc, json.JSONDecodeError):
        return "quota_state_invalid_json"
    if isinstance(exc, QuotaCommandValidationError):
        return "quota_invalid_arguments"
    if isinstance(exc, QuotaIdentityPreconditionError):
        return exc.error_code
    if isinstance(exc, QuotaActionSelectionConflictError):
        return exc.error_code
    if isinstance(exc, HeartbeatReceiptIdentityConflictError):
        return "heartbeat_receipt_identity_conflict"
    if isinstance(exc, PermissionError):
        return "quota_state_permission_denied"
    if isinstance(exc, OSError):
        return "quota_state_io_failed"
    if isinstance(exc, KeyError):
        return "quota_state_missing_field"
    if isinstance(exc, TypeError):
        return "quota_state_shape_error"
    return "quota_unexpected_collection_error"
