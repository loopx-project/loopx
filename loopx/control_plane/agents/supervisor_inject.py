from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...event_sourced_state import SUPERVISOR_PROPOSED, AppendOnlyStateEventStore
from ..todos.contract import normalize_todo_claimed_by
from .supervisor_events import (
    SupervisorReceiptOutcome,
    SupervisorRollbackMode,
    load_supervisor_event_projection,
    record_supervisor_receipt,
)


SUPERVISOR_INJECT_CAPABILITY = "session_message_injection"
SUPERVISOR_INJECT_ADAPTER_SCHEMA_VERSION = "supervisor_inject_adapter_v0"


@dataclass(frozen=True)
class SupervisorInjectRequest:
    goal_id: str
    decision_id: str
    execution_id: str
    target_agent_id: str
    message: str
    authority_ref: str


@dataclass(frozen=True)
class SupervisorInjectResult:
    outcome: SupervisorReceiptOutcome
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]


class SupervisorInjectHostAdapter(Protocol):
    """One opt-in host seam; LoopX does not provide a default implementation."""

    adapter_id: str
    capabilities: tuple[str, ...]
    rollback_mode: SupervisorRollbackMode
    rollback_ref: str

    def inject(self, request: SupervisorInjectRequest) -> SupervisorInjectResult:
        ...


def _required_token(raw: object, *, field: str) -> str:
    token = normalize_todo_claimed_by(raw)
    if not token:
        raise ValueError(f"{field} must be a compact token")
    return token


def _capabilities(adapter: SupervisorInjectHostAdapter) -> list[str]:
    capabilities = []
    for raw in adapter.capabilities:
        token = _required_token(raw, field="adapter capabilities")
        if token not in capabilities:
            capabilities.append(token)
    if SUPERVISOR_INJECT_CAPABILITY not in capabilities:
        raise ValueError(
            "inject adapter is missing declared capability: "
            + SUPERVISOR_INJECT_CAPABILITY
        )
    return capabilities


def _proposal(log_path: Path, *, goal_id: str, decision_id: str) -> dict:
    for event in AppendOnlyStateEventStore(log_path).load():
        if (
            event.get("event_type") == SUPERVISOR_PROPOSED
            and event.get("goal_id") == goal_id
            and (event.get("refs") or {}).get("decision_id") == decision_id
        ):
            return event
    raise ValueError(f"no recorded supervisor proposal for decision_id={decision_id}")


def execute_supervisor_inject(
    *,
    log_path: Path,
    goal_id: str,
    decision_id: str,
    execution_id: str,
    authority_ref: str,
    adapter: SupervisorInjectHostAdapter,
    execute: bool,
) -> dict:
    """Preview or execute one proposal through an explicitly supplied host adapter."""

    normalized_decision_id = _required_token(decision_id, field="decision_id")
    normalized_execution_id = _required_token(execution_id, field="execution_id")
    normalized_authority_ref = _required_token(authority_ref, field="authority_ref")
    adapter_id = _required_token(adapter.adapter_id, field="adapter_id")
    capabilities = _capabilities(adapter)
    try:
        rollback_mode = SupervisorRollbackMode(adapter.rollback_mode)
    except ValueError as exc:
        choices = ", ".join(item.value for item in SupervisorRollbackMode)
        raise ValueError(f"adapter rollback_mode must be one of: {choices}") from exc
    rollback_ref = _required_token(adapter.rollback_ref, field="adapter rollback_ref")

    proposal_event = _proposal(
        log_path,
        goal_id=goal_id,
        decision_id=normalized_decision_id,
    )
    decision = ((proposal_event.get("payload") or {}).get("decision") or {})
    if decision.get("kind") != "inject":
        raise ValueError("supervisor inject adapter only accepts inject proposals")
    if decision.get("required_host_capabilities") != [SUPERVISOR_INJECT_CAPABILITY]:
        raise ValueError("inject proposal capability contract is not canonical")

    projection = load_supervisor_event_projection(log_path, goal_id=goal_id)
    prior = next(
        (
            item
            for item in projection.get("items") or []
            if item.get("decision_id") == normalized_decision_id
            and item.get("execution_status") == SupervisorReceiptOutcome.EXECUTED.value
        ),
        None,
    )
    request = SupervisorInjectRequest(
        goal_id=goal_id,
        decision_id=normalized_decision_id,
        execution_id=normalized_execution_id,
        target_agent_id=str(decision.get("target_agent_id") or ""),
        message=str(decision.get("message") or ""),
        authority_ref=normalized_authority_ref,
    )
    adapter_contract = {
        "adapter_id": adapter_id,
        "capabilities": capabilities,
        "rollback_boundary": {
            "mode": rollback_mode.value,
            "ref": rollback_ref,
            "automatic": False,
            "requires_explicit_authority": True,
        },
    }
    if prior is not None:
        return {
            "ok": True,
            "schema_version": SUPERVISOR_INJECT_ADAPTER_SCHEMA_VERSION,
            "mode": "supervisor_inject",
            "dry_run": not execute,
            "host_called": False,
            "already_executed": True,
            "adapter": adapter_contract,
            "request": request.__dict__,
            "receipt": prior.get("latest_receipt"),
            "projection": projection,
        }
    if not execute:
        return {
            "ok": True,
            "schema_version": SUPERVISOR_INJECT_ADAPTER_SCHEMA_VERSION,
            "mode": "supervisor_inject",
            "dry_run": True,
            "host_called": False,
            "already_executed": False,
            "would_execute": True,
            "adapter": adapter_contract,
            "request": request.__dict__,
            "projection": projection,
        }

    result = adapter.inject(request)
    if not isinstance(result, SupervisorInjectResult):
        raise ValueError("inject adapter must return SupervisorInjectResult")
    outcome = SupervisorReceiptOutcome(result.outcome)
    receipt_payload = {
        "receipt_id": normalized_execution_id,
        "decision_id": normalized_decision_id,
        "adapter_id": adapter_id,
        "outcome": outcome.value,
        "authority_ref": normalized_authority_ref,
        "evidence_refs": list(result.evidence_refs),
        "reason_codes": list(result.reason_codes),
        "rollback_boundary": adapter_contract["rollback_boundary"],
    }
    recorded = record_supervisor_receipt(
        log_path=log_path,
        goal_id=goal_id,
        receipt=receipt_payload,
        host_capabilities=capabilities,
        execute=True,
    )
    return {
        "ok": True,
        "schema_version": SUPERVISOR_INJECT_ADAPTER_SCHEMA_VERSION,
        "mode": "supervisor_inject",
        "dry_run": False,
        "host_called": True,
        "already_executed": False,
        "adapter": adapter_contract,
        "request": request.__dict__,
        "receipt": ((recorded.get("event") or {}).get("payload") or {}).get("receipt"),
        "projection": recorded.get("projection"),
    }
