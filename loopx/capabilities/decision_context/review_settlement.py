"""Owner-gated or evidence-only settlement for one Decision Context assembly."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...rollout_event_log import append_rollout_event, build_rollout_event
from .assembler import DecisionContextAssembly
from .cursor_commit import (
    _validate_review_packet_chain,
    commit_profile_decision_cursors,
    resolve_decision_review_source_event,
)
from .packets import build_decision_review_receipt


DECISION_REVIEW_SETTLEMENT_SCHEMA_VERSION = "decision_review_settlement_v0"


def settle_profile_decision_review(
    *,
    goal_id: str,
    agent_id: str,
    profile_path: Path,
    cursor_path: Path,
    assembly: DecisionContextAssembly,
    lifecycle_event_log_path: Path,
    actor_ref: str,
    reason_code: str,
    summary: str,
    proposal: Mapping[str, Any] | None = None,
    source_event_id: str | None = None,
    disposition: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Settle a proposal decision or an explicitly reviewed evidence update.

    Gated dispositions come only from the exact user-gate event. Without a
    gate, callers may explicitly observe new facts or retain the legacy
    semantic no-change default. Neither path confers action authority.
    """

    packet = assembly.public_packet()
    evidence = packet.get("evidence_packet")
    if not isinstance(evidence, Mapping):
        raise ValueError("decision-context assembly has no evidence packet")
    evidence_ref = str(evidence.get("packet_ref") or "")
    decision_id = str(packet.get("decision_id") or "")

    gate_event: Mapping[str, Any] | None = None
    proposal_ref: str | None = None
    gate_todo_id: str | None = None
    if source_event_id is not None:
        if disposition is not None:
            raise ValueError("evidence-only disposition cannot accompany a user-gate event")
        if proposal is None:
            raise ValueError("gated decision review requires a proposal")
        proposal_ref = str(proposal.get("packet_ref") or "")
        gate_event, disposition = resolve_decision_review_source_event(
            event_log_path=lifecycle_event_log_path,
            source_event_id=source_event_id,
            proposal_packet_ref=proposal_ref,
        )
        gate_todo_id = str(gate_event.get("todo_id") or "")
        recorded_at = str(gate_event.get("recorded_at") or "")
    else:
        if proposal is not None:
            raise ValueError("proposal settlement requires a user-gate event")
        disposition = disposition if disposition is not None else "no_change"
        if disposition not in {"no_change", "observed"}:
            raise ValueError("proposal disposition requires a user-gate event")
        recorded_at = str(packet.get("observed_at") or "")

    review_receipt = build_decision_review_receipt(
        goal_id=goal_id,
        decision_id=decision_id,
        evidence_packet_ref=evidence_ref,
        recorded_at=recorded_at,
        disposition=disposition,
        actor_ref=actor_ref,
        reason_code=reason_code,
        summary=summary,
        proposal_packet_ref=proposal_ref,
        gate_todo_id=gate_todo_id,
        source_event_id=source_event_id,
    )
    # Validate semantic proof before preview success or any event append.
    # Cursor commit revalidates this chain together with profile/cursor CAS.
    _validate_review_packet_chain(
        assembly=assembly,
        proposal=proposal,
        review_receipt=review_receipt,
        event_log_path=lifecycle_event_log_path,
    )
    artifact_refs = [evidence_ref, str(review_receipt["packet_ref"])]
    if proposal_ref is not None:
        artifact_refs.append(proposal_ref)
    lifecycle_event = build_rollout_event(
        goal_id=goal_id,
        event_kind="validation",
        agent_id=agent_id,
        todo_id=gate_todo_id,
        decision_id=decision_id,
        caused_by=source_event_id,
        status="committed",
        summary="Decision review settlement validated.",
        artifact_refs=artifact_refs,
        details={
            "capability": "decision_context",
            "disposition": disposition,
            "quiet_noop": disposition == "no_change",
        },
        recorded_at=recorded_at,
    )

    result: dict[str, Any] = {
        "ok": True,
        "schema_version": DECISION_REVIEW_SETTLEMENT_SCHEMA_VERSION,
        "goal_id": goal_id,
        "decision_id": decision_id,
        "disposition": disposition,
        "review_receipt": review_receipt,
        "lifecycle_event": {
            "schema_version": lifecycle_event["schema_version"],
            "event_id": lifecycle_event["event_id"],
            "event_kind": lifecycle_event["event_kind"],
            "status": lifecycle_event["status"],
            "recorded_at": lifecycle_event["recorded_at"],
        },
        "executed": execute,
        "quiet_noop": disposition == "no_change",
        "external_writes_performed": False,
        "raw_context_captured": False,
        "private_paths_captured": False,
    }
    if not execute:
        result["cursor_commit"] = None
        return result

    appended = append_rollout_event(lifecycle_event_log_path, lifecycle_event)
    result["lifecycle_event"]["event_id"] = appended["event_id"]
    result["cursor_commit"] = commit_profile_decision_cursors(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
        cursor_path=cursor_path,
        assembly=assembly,
        proposal=proposal,
        review_receipt=review_receipt,
        lifecycle_event_log_path=lifecycle_event_log_path,
        lifecycle_event_id=str(appended["event_id"]),
    )
    return result
