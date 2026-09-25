from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from ...event_sourced_state import (
    LOCAL_PRIVATE_PRIVACY,
    SUPERVISOR_PROPOSED,
    SUPERVISOR_RECEIPT_RECORDED,
    AppendOnlyStateEventStore,
    StateEventConflictError,
    make_state_event,
)
from ..todos.contract import normalize_todo_claimed_by
from .supervisor import normalize_supervisor_decision


SUPERVISOR_EVENT_LOG_NAME = "supervisor-events.jsonl"
SUPERVISOR_EVENT_PROJECTION_SCHEMA_VERSION = "supervisor_event_projection_v0"
SUPERVISOR_RECEIPT_SCHEMA_VERSION = "supervisor_host_receipt_v1"

_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:ak|sk|access[_-]?key|secret[_-]?key)\b\s*[:=]\s*\S+"
)


class SupervisorReceiptOutcome(str, Enum):
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


class SupervisorRollbackMode(str, Enum):
    COMPENSATING_ACTION = "compensating_action"
    NOT_REVERSIBLE = "not_reversible"


def supervisor_event_log_path(runtime_root: Path, goal_id: str) -> Path:
    # Supervisor events share the same goal-scoped storage boundary as rollout
    # events; validate before constructing a path so CLI reads and writes cannot
    # escape the runtime goals directory.
    from ...history import validate_goal_id_path_segment

    safe_goal_id = validate_goal_id_path_segment(goal_id)
    return runtime_root.expanduser() / "goals" / safe_goal_id / SUPERVISOR_EVENT_LOG_NAME


def _tokens(values: Any, *, field: str, required: bool) -> list[str]:
    if values is None and not required:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    normalized: list[str] = []
    for raw in values:
        token = normalize_todo_claimed_by(raw)
        if not token:
            raise ValueError(f"{field} must contain compact opaque references")
        if token not in normalized:
            normalized.append(token)
    if required and not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _required_token(payload: Mapping[str, Any], field: str) -> str:
    token = normalize_todo_claimed_by(payload.get(field))
    if not token:
        raise ValueError(f"{field} must be a compact token")
    return token


def _reject_inline_secrets(value: Any, *, field: str) -> None:
    if _INLINE_SECRET_PATTERN.search(str(value or "")):
        raise ValueError(f"{field} contains an inline credential")


def build_supervisor_proposal_event(
    *,
    goal_id: str,
    supervisor: Mapping[str, Any],
    decision: Mapping[str, Any],
    recorded_at: str | None = None,
) -> dict[str, Any]:
    from ...history import validate_goal_id_path_segment

    safe_goal_id = validate_goal_id_path_segment(goal_id)
    normalized = normalize_supervisor_decision(decision, supervisor=supervisor)
    _reject_inline_secrets(normalized, field="decision")
    decision_id = str(normalized["decision_id"])
    supervisor_agent_id = str(supervisor.get("agent_id") or "")
    return make_state_event(
        event_id=f"supervisor-proposal-{decision_id}",
        goal_id=safe_goal_id,
        event_type=SUPERVISOR_PROPOSED,
        refs={
            "decision_id": decision_id,
            "supervisor_agent_id": supervisor_agent_id,
        },
        payload={"decision": normalized},
        recorded_at=recorded_at,
        producer="loopx.supervisor",
        privacy=LOCAL_PRIVATE_PRIVACY,
    )


def normalize_supervisor_receipt(
    raw: Mapping[str, Any],
    *,
    proposal: Mapping[str, Any],
    host_capabilities: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("supervisor receipt must be an object")
    decision = proposal.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("proposal is missing its normalized decision")
    if decision.get("kind") == "observe":
        raise ValueError("observe decisions do not accept host execution receipts")
    receipt_id = _required_token(raw, "receipt_id")
    decision_id = _required_token(raw, "decision_id")
    if decision_id != decision.get("decision_id"):
        raise ValueError("receipt decision_id does not match the proposal")
    adapter_id = _required_token(raw, "adapter_id")
    try:
        outcome = SupervisorReceiptOutcome(str(raw.get("outcome") or ""))
    except ValueError as exc:
        choices = ", ".join(item.value for item in SupervisorReceiptOutcome)
        raise ValueError(f"outcome must be one of: {choices}") from exc

    capabilities = _tokens(
        list(host_capabilities or []),
        field="host_capabilities",
        required=False,
    )
    evidence_refs = _tokens(raw.get("evidence_refs"), field="evidence_refs", required=True)
    reason_codes = _tokens(raw.get("reason_codes"), field="reason_codes", required=True)
    required_capabilities = list(decision.get("required_host_capabilities") or [])
    missing_capabilities = sorted(set(required_capabilities) - set(capabilities))
    authority_ref = normalize_todo_claimed_by(raw.get("authority_ref"))
    raw_rollback = raw.get("rollback_boundary")
    rollback_boundary = None
    if raw_rollback is not None:
        if not isinstance(raw_rollback, Mapping):
            raise ValueError("rollback_boundary must be an object")
        try:
            rollback_mode = SupervisorRollbackMode(str(raw_rollback.get("mode") or ""))
        except ValueError as exc:
            choices = ", ".join(item.value for item in SupervisorRollbackMode)
            raise ValueError(f"rollback_boundary.mode must be one of: {choices}") from exc
        rollback_ref = _required_token(raw_rollback, "ref")
        if raw_rollback.get("automatic") is not False:
            raise ValueError("rollback_boundary.automatic must be false")
        rollback_boundary = {
            "mode": rollback_mode.value,
            "ref": rollback_ref,
            "automatic": False,
            "requires_explicit_authority": True,
        }
    if outcome is SupervisorReceiptOutcome.EXECUTED:
        if missing_capabilities:
            raise ValueError(
                "executed receipt is missing required host capabilities: "
                + ", ".join(missing_capabilities)
            )
        if not authority_ref:
            raise ValueError("executed receipt requires authority_ref")
        if rollback_boundary is None:
            raise ValueError("executed receipt requires rollback_boundary")

    receipt = {
        "schema_version": SUPERVISOR_RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "decision_id": decision_id,
        "adapter_id": adapter_id,
        "outcome": outcome.value,
        "capabilities": capabilities,
        "capability_source": "host_adapter_context",
        "required_host_capabilities": required_capabilities,
        "capability_match": not missing_capabilities,
        "missing_capabilities": missing_capabilities,
        "authority_ref": authority_ref,
        "rollback_boundary": rollback_boundary,
        "evidence_refs": evidence_refs,
        "reason_codes": reason_codes,
    }
    _reject_inline_secrets(receipt, field="receipt")
    return receipt


def build_supervisor_receipt_event(
    *,
    goal_id: str,
    proposal_event: Mapping[str, Any],
    receipt: Mapping[str, Any],
    host_capabilities: list[str] | tuple[str, ...] | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    from ...history import validate_goal_id_path_segment

    safe_goal_id = validate_goal_id_path_segment(goal_id)
    proposal = proposal_event.get("payload")
    if not isinstance(proposal, Mapping):
        raise ValueError("proposal event is missing payload")
    normalized = normalize_supervisor_receipt(
        receipt,
        proposal=proposal,
        host_capabilities=host_capabilities,
    )
    return make_state_event(
        event_id=f"supervisor-receipt-{normalized['receipt_id']}",
        goal_id=safe_goal_id,
        event_type=SUPERVISOR_RECEIPT_RECORDED,
        refs={
            "decision_id": normalized["decision_id"],
            "receipt_id": normalized["receipt_id"],
        },
        payload={"receipt": normalized},
        recorded_at=recorded_at,
        producer="loopx.supervisor",
        privacy=LOCAL_PRIVATE_PRIVACY,
    )


def _matching_event(
    events: list[dict[str, Any]],
    *,
    event_type: str,
    ref_name: str,
    ref_value: str,
) -> dict[str, Any] | None:
    return next(
        (
            event
            for event in events
            if event.get("event_type") == event_type
            and (event.get("refs") or {}).get(ref_name) == ref_value
        ),
        None,
    )


def _append_idempotent(
    store: AppendOnlyStateEventStore,
    event: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    prior = next(
        (item for item in store.load() if item.get("event_id") == event.get("event_id")),
        None,
    )
    if prior is not None:
        if prior.get("refs") != event.get("refs") or prior.get("payload") != event.get("payload"):
            raise StateEventConflictError(f"conflicting event_id: {event.get('event_id')}")
        return prior, False
    try:
        return store.append(event), True
    except StateEventConflictError:
        concurrent = next(
            (
                item
                for item in store.load()
                if item.get("event_id") == event.get("event_id")
            ),
            None,
        )
        if (
            concurrent is not None
            and concurrent.get("refs") == event.get("refs")
            and concurrent.get("payload") == event.get("payload")
        ):
            return concurrent, False
        raise


def record_supervisor_proposal(
    *,
    log_path: Path,
    goal_id: str,
    supervisor: Mapping[str, Any],
    decision: Mapping[str, Any],
    execute: bool,
) -> dict[str, Any]:
    store = AppendOnlyStateEventStore(log_path)
    events = store.load()
    event = build_supervisor_proposal_event(
        goal_id=goal_id,
        supervisor=supervisor,
        decision=decision,
    )
    prior = next(
        (item for item in events if item.get("event_id") == event.get("event_id")),
        None,
    )
    if execute:
        appended, created = _append_idempotent(store, event)
        projection_events = store.load()
    elif prior is not None:
        appended, created = _append_idempotent(store, event)
        projection_events = events
    else:
        appended, created = event, False
        projection_events = [*events, event]
    return {
        "ok": True,
        "mode": "supervisor_proposal",
        "dry_run": not execute,
        "appended": created,
        "would_append": prior is None,
        "event": appended,
        "projection": build_supervisor_event_projection(projection_events, goal_id=goal_id),
    }


def record_supervisor_receipt(
    *,
    log_path: Path,
    goal_id: str,
    receipt: Mapping[str, Any],
    execute: bool,
    host_capabilities: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    store = AppendOnlyStateEventStore(log_path)
    events = store.load()
    decision_id = _required_token(receipt, "decision_id")
    proposal = _matching_event(
        events,
        event_type=SUPERVISOR_PROPOSED,
        ref_name="decision_id",
        ref_value=decision_id,
    )
    if proposal is None:
        raise ValueError(f"no recorded supervisor proposal for decision_id={decision_id}")
    prior_executed = next(
        (
            event
            for event in events
            if event.get("event_type") == SUPERVISOR_RECEIPT_RECORDED
            and (event.get("refs") or {}).get("decision_id") == decision_id
            and ((event.get("payload") or {}).get("receipt") or {}).get("outcome")
            == SupervisorReceiptOutcome.EXECUTED.value
        ),
        None,
    )
    event = build_supervisor_receipt_event(
        goal_id=goal_id,
        proposal_event=proposal,
        receipt=receipt,
        host_capabilities=host_capabilities,
    )
    if prior_executed is not None and prior_executed.get("event_id") != event.get("event_id"):
        raise ValueError(f"decision_id={decision_id} already has an executed receipt")
    prior = next(
        (item for item in events if item.get("event_id") == event.get("event_id")),
        None,
    )
    if execute:
        appended, created = _append_idempotent(store, event)
        projection_events = store.load()
    elif prior is not None:
        appended, created = _append_idempotent(store, event)
        projection_events = events
    else:
        appended, created = event, False
        projection_events = [*events, event]
    return {
        "ok": True,
        "mode": "supervisor_receipt",
        "dry_run": not execute,
        "appended": created,
        "would_append": prior is None,
        "event": appended,
        "projection": build_supervisor_event_projection(projection_events, goal_id=goal_id),
    }


def build_supervisor_event_projection(
    events: list[dict[str, Any]],
    *,
    goal_id: str,
) -> dict[str, Any]:
    proposals: dict[str, dict[str, Any]] = {}
    receipts: dict[str, list[dict[str, Any]]] = {}
    for event in sorted(events, key=lambda item: int(item.get("append_sequence") or 0)):
        if event.get("goal_id") != goal_id:
            continue
        decision_id = str((event.get("refs") or {}).get("decision_id") or "")
        if not decision_id:
            continue
        if event.get("event_type") == SUPERVISOR_PROPOSED:
            proposals[decision_id] = event
        elif event.get("event_type") == SUPERVISOR_RECEIPT_RECORDED:
            receipts.setdefault(decision_id, []).append(event)

    rows = []
    for decision_id, proposal_event in proposals.items():
        decision = ((proposal_event.get("payload") or {}).get("decision") or {})
        decision_receipts = receipts.get(decision_id, [])
        latest_receipt_event = decision_receipts[-1] if decision_receipts else None
        latest_receipt = (
            ((latest_receipt_event.get("payload") or {}).get("receipt") or {})
            if latest_receipt_event
            else None
        )
        rows.append(
            {
                "decision_id": decision_id,
                "kind": decision.get("kind"),
                "target_agent_id": decision.get("target_agent_id"),
                "proposal_event_id": proposal_event.get("event_id"),
                "proposed_at": proposal_event.get("recorded_at"),
                "required_host_capabilities": list(
                    decision.get("required_host_capabilities") or []
                ),
                "execution_status": (
                    "not_required"
                    if decision.get("kind") == "observe"
                    else latest_receipt.get("outcome")
                    if latest_receipt
                    else "proposal_only"
                ),
                "receipt_count": len(decision_receipts),
                "latest_receipt": latest_receipt,
            }
        )
    return {
        "ok": True,
        "schema_version": SUPERVISOR_EVENT_PROJECTION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "proposal_count": len(rows),
        "receipt_count": sum(len(items) for items in receipts.values()),
        "items": rows,
        "orphan_receipt_count": len(set(receipts) - set(proposals)),
        "boundary": {
            "proposal_is_execution_evidence": False,
            "executed_requires_capability_matched_receipt": True,
            "ledger_privacy": LOCAL_PRIVATE_PRIVACY,
        },
    }


def load_supervisor_event_projection(log_path: Path, *, goal_id: str) -> dict[str, Any]:
    return build_supervisor_event_projection(
        AppendOnlyStateEventStore(log_path).load(),
        goal_id=goal_id,
    )


def render_supervisor_event_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "\n".join(
            [
                "# LoopX Supervisor Event",
                "",
                "- ok: `False`",
                f"- error: {payload.get('error') or 'unknown error'}",
            ]
        )
    projection = payload.get("projection") or payload
    lines = [
        "# LoopX Supervisor Events",
        "",
        f"- goal_id: `{projection.get('goal_id')}`",
        f"- proposals: `{projection.get('proposal_count') or 0}`",
        f"- receipts: `{projection.get('receipt_count') or 0}`",
        "",
    ]
    for item in projection.get("items") or []:
        lines.append(
            f"- `{item.get('decision_id')}` kind=`{item.get('kind')}` "
            f"status=`{item.get('execution_status')}`"
        )
    return "\n".join(lines)
