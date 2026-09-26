"""Turn-bound reports of host-native child-tool decisions.

The native tool belongs to the host. LoopX can durably reconcile the
coordinator's typed report of its result, but cannot attest that a host call
occurred unless the host itself supplies an integration. This distinction is
part of the projection, not an implicit promise of configured capacity.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...control_plane.quota.heartbeat_receipt import find_heartbeat_receipt
from ...control_plane.runtime.public_safety import validate_public_safe_value
from ...rollout_event_log import (
    append_rollout_event_once,
    build_rollout_event,
    iter_rollout_events,
    load_rollout_events,
    rollout_event_log_path,
)


SCHEMA_VERSION = "native_subagent_activity_v0"
EVENT_KINDS = {
    "decision": "native_child_decision",
    "result": "native_child_result",
    "review": "native_child_review",
}
OPERATIONS = frozenset({"spawn", "followup", "skip"})
DECISION_OUTCOMES = frozenset({"started", "capacity_rejected", "host_failed", "skipped"})
RESULT_OUTCOMES = frozenset({"completed", "failed", "cancelled"})
REVIEW_OUTCOMES = frozenset({"accepted", "deferred", "rejected"})
SKIP_REASONS = frozenset({
    "no_independent_work", "duplicate_evidence", "brief_incomplete",
    "parent_work_priority", "scope_not_admitted", "capacity_deferred",
})
FAILURE_REASONS = frozenset({"host_unavailable", "host_rejected", "host_failed"})
REVIEW_REASONS = frozenset({"evidence_incomplete", "source_unverified", "contradicted", "not_needed"})
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_MAX_VISIBLE = 8


def _id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _OPAQUE_ID.fullmatch(value):
        raise ValueError(f"{field} must be a compact opaque id")
    validate_public_safe_value(value, path=field)
    return value


def _choice(value: Any, *, field: str, choices: frozenset[str]) -> str:
    if value not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(choices))}")
    return str(value)


def _details(event: Mapping[str, Any]) -> dict[str, Any]:
    value = event.get("details")
    return dict(value) if isinstance(value, Mapping) else {}


def _events_for_turn(
    events: Sequence[Mapping[str, Any]], *, agent_id: str, turn_instance_id: str,
) -> list[dict[str, Any]]:
    return [dict(event) for event in events
            if event.get("event_kind") in EVENT_KINDS.values()
            and event.get("agent_id") == agent_id
            and event.get("run_id") == turn_instance_id]


def native_child_activity(
    events: Sequence[Mapping[str, Any]], *, goal_id: str, agent_id: str,
    turn_instance_id: str, configured_limit: int,
) -> dict[str, Any]:
    """One read model for CLI, agent-context and product projections."""
    rows = _events_for_turn(events, agent_id=agent_id, turn_instance_id=turn_instance_id)
    operations: dict[str, dict[str, Any]] = {}
    for event in rows:
        details = _details(event)
        operation_id = str(event.get("case_id") or "")
        if not operation_id:
            continue
        row = operations.setdefault(operation_id, {"operation_id": operation_id})
        kind = event.get("event_kind")
        if kind == EVENT_KINDS["decision"]:
            row.update({key: details[key] for key in
                        ("entrypoint_id", "observation_source", "operation", "outcome", "reason_code")
                        if key in details})
            row["recorded_at"] = event.get("recorded_at")
        elif kind == EVENT_KINDS["result"]:
            row["result"] = details.get("outcome")
        elif kind == EVENT_KINDS["review"]:
            row["parent_review"] = details.get("outcome")
            if details.get("outcome") == "accepted":
                row["evidence_ref"] = details.get("evidence_ref")
                row["validation_ref"] = details.get("validation_ref")
    ordered = [row for row in operations.values() if "operation" in row]
    ordered.sort(key=lambda row: (str(row.get("recorded_at") or ""), row["operation_id"]))
    launched = sum(row.get("outcome") == "started" and row.get("operation") == "spawn"
                   for row in ordered)
    attempted = sum(row.get("operation") in {"spawn", "followup"} for row in ordered)
    rejected = sum(row.get("outcome") == "capacity_rejected" for row in ordered)
    host_failed = sum(row.get("outcome") == "host_failed" for row in ordered)
    return {
        "schema_version": SCHEMA_VERSION,
        "goal_id": goal_id, "agent_id": agent_id,
        "turn_instance_id": turn_instance_id,
        "entrypoint_scope": "host_native_child_tools",
        "observation": "coordinator_reported" if ordered else "unknown",
        "host_attested": False,
        "configured_limit_kind": "upper_bound",
        "configured_limit": configured_limit,
        "observed_capacity": "capacity_rejection_reported" if rejected else
                             "host_failure_reported" if host_failed else
                             "attempt_reported" if attempted else "not_observed",
        "attempted_count": attempted,
        "launched_count": launched,
        "skipped_count": sum(row.get("outcome") == "skipped" for row in ordered),
        "capacity_rejected_count": rejected,
        "host_failed_count": host_failed,
        "parent_accepted_count": sum(row.get("parent_review") == "accepted" for row in ordered),
        "retry_same_turn": False if rejected or host_failed else None,
        "operation_count": len(ordered),
        "visible_operation_count": min(len(ordered), _MAX_VISIBLE),
        "operations": ordered[-_MAX_VISIBLE:],
        "quota_spend_slots": 0,
    }


def load_native_child_activity(
    runtime_root: Path, *, goal_id: str, agent_id: str,
    turn_instance_id: str, configured_limit: int,
) -> dict[str, Any]:
    source = iter_rollout_events(rollout_event_log_path(runtime_root, goal_id))
    events = [event for event in source
              if event.get("event_kind") in EVENT_KINDS.values()
              and event.get("agent_id") == agent_id
              and event.get("run_id") == turn_instance_id]
    return native_child_activity(
        events, goal_id=goal_id, agent_id=agent_id,
        turn_instance_id=turn_instance_id, configured_limit=configured_limit,
    )


def latest_native_child_activity(
    events: Sequence[Mapping[str, Any]], *, goal_id: str, configured_limit: int,
) -> dict[str, Any] | None:
    """Expose only the latest reported Turn in existing Goal status surfaces."""
    observations = [event for event in events
                    if event.get("goal_id") == goal_id
                    and event.get("event_kind") in EVENT_KINDS.values()
                    and event.get("agent_id") and event.get("run_id")]
    if not observations:
        return None
    latest = max(observations, key=lambda event: str(event.get("recorded_at") or ""))
    return native_child_activity(
        events, goal_id=goal_id, agent_id=str(latest["agent_id"]),
        turn_instance_id=str(latest["run_id"]), configured_limit=configured_limit,
    )


def _normalized_fields(
    *, stage: str, operation: str | None, outcome: str,
    entrypoint_id: str | None, reason_code: str | None,
    evidence_ref: str | None, validation_ref: str | None,
) -> dict[str, str]:
    if stage == "decision":
        op = _choice(operation, field="operation", choices=OPERATIONS)
        result = _choice(outcome, field="outcome", choices=DECISION_OUTCOMES)
        host = _id(entrypoint_id, field="entrypoint_id")
        if (op == "skip") != (result == "skipped"):
            raise ValueError("skip operation and skipped outcome must occur together")
        if result == "skipped":
            reason = _choice(reason_code, field="reason_code", choices=SKIP_REASONS)
        elif result == "capacity_rejected":
            if reason_code not in {None, "host_capacity_exhausted"}:
                raise ValueError("capacity rejection reason must be host_capacity_exhausted")
            reason = "host_capacity_exhausted"
        elif result == "host_failed":
            reason = _choice(reason_code, field="reason_code", choices=FAILURE_REASONS)
        else:
            if reason_code is not None:
                raise ValueError("started outcome must not include a reason_code")
            reason = None
        if evidence_ref or validation_ref:
            raise ValueError("evidence refs belong to parent review")
        return {"entrypoint_id": host, "observation_source": "coordinator_reported",
                "operation": op, "outcome": result,
                **({"reason_code": reason} if reason else {})}
    if operation or entrypoint_id:
        raise ValueError("result/review reuse the decision's operation and entrypoint_id")
    if stage == "result":
        result = _choice(outcome, field="outcome", choices=RESULT_OUTCOMES)
        if reason_code or evidence_ref or validation_ref:
            raise ValueError("result accepts only a typed outcome")
        return {"outcome": result}
    if stage == "review":
        result = _choice(outcome, field="outcome", choices=REVIEW_OUTCOMES)
        if result == "accepted":
            if reason_code:
                raise ValueError("accepted review must not include a reason_code")
            return {"outcome": result, "evidence_ref": _id(evidence_ref, field="evidence_ref"),
                    "validation_ref": _id(validation_ref, field="validation_ref")}
        if evidence_ref or validation_ref:
            raise ValueError("deferred/rejected review must not adopt evidence")
        return {"outcome": result, "reason_code":
                _choice(reason_code, field="reason_code", choices=REVIEW_REASONS)}
    raise ValueError("stage must be decision, result or review")


def record_native_child(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    turn_instance_id: str, operation_id: str, configured_limit: int,
    stage: str, outcome: str, operation: str | None = None,
    entrypoint_id: str | None = None, reason_code: str | None = None,
    evidence_ref: str | None = None, validation_ref: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Preview or append a typed report; never launch a child or spend quota."""
    goal_id = _id(goal_id, field="goal_id")
    agent_id = _id(agent_id, field="agent_id")
    turn_instance_id = _id(turn_instance_id, field="turn_instance_id")
    operation_id = _id(operation_id, field="operation_id")
    if isinstance(configured_limit, bool) or not isinstance(configured_limit, int) or configured_limit < 1:
        raise ValueError("enabled multi_subagent configured_limit must be positive")
    fields = _normalized_fields(
        stage=stage, operation=operation, outcome=outcome, entrypoint_id=entrypoint_id,
        reason_code=reason_code, evidence_ref=evidence_ref, validation_ref=validation_ref,
    )
    guard = find_heartbeat_receipt(
        runtime_root, goal_id=goal_id, agent_id=agent_id,
        turn_instance_id=turn_instance_id,
    )
    if not guard or not _details(guard).get("settlement_effect_id"):
        raise ValueError("native child report requires an admitted, settlement-bound Turn guard")
    log_path = rollout_event_log_path(runtime_root, goal_id)
    events = load_rollout_events(log_path)
    prior = _events_for_turn(events, agent_id=agent_id, turn_instance_id=turn_instance_id)
    existing = next((event for event in prior
                     if event.get("case_id") == operation_id
                     and event.get("event_kind") == EVENT_KINDS[stage]), None)
    if existing is not None and _details(existing) != fields:
        raise ValueError("operation identity already has a conflicting native child report")

    def validate_transition(observed: Sequence[Mapping[str, Any]]) -> None:
        current = _events_for_turn(observed, agent_id=agent_id,
                                   turn_instance_id=turn_instance_id)
        decisions = {str(item.get("case_id")): item for item in current
                     if item.get("event_kind") == EVENT_KINDS["decision"]}
        if stage == "decision":
            if str(guard.get("status") or "") not in {"normal_run", "turn_run_once"}:
                raise ValueError("native child decision requires a runnable Turn guard")
            if fields["operation"] in {"spawn", "followup"} and any(
                _details(item).get("outcome") in {"capacity_rejected", "host_failed"}
                for item in decisions.values()
            ):
                raise ValueError("host failure forbids same-Turn spawn/followup retry")
            return
        decision = decisions.get(operation_id)
        if decision is None or _details(decision).get("outcome") != "started":
            raise ValueError("result/review requires a started native child decision")
        if stage == "review":
            result = next((item for item in current if item.get("case_id") == operation_id
                           and item.get("event_kind") == EVENT_KINDS["result"]), None)
            if result is None or _details(result).get("outcome") != "completed":
                raise ValueError("parent review requires a completed native child result")

    if existing is None:
        validate_transition(prior)
    event = build_rollout_event(
        goal_id=goal_id, event_kind=EVENT_KINDS[stage], agent_id=agent_id,
        run_id=turn_instance_id, case_id=operation_id, status=fields["outcome"],
        details=fields, recorded_at=(existing or {}).get("recorded_at"),
    )
    appended = False
    if execute:
        stored, appended = append_rollout_event_once(
            log_path, event,
            identity_fields=("goal_id", "event_kind", "agent_id", "run_id", "case_id"),
            precondition=lambda: validate_transition(load_rollout_events(log_path)),
        )
        if _details(stored) != fields:
            raise ValueError("operation identity already has a conflicting native child report")
        events = load_rollout_events(log_path)
    else:
        stored = existing or event
    return {
        "ok": True, "dry_run": not execute, "appended": appended,
        "receipt": {key: stored[key] for key in
                    ("event_id", "event_kind", "recorded_at", "run_id", "case_id", "status")},
        "native_child_activity": native_child_activity(
            events if execute or existing else [*events, event], goal_id=goal_id,
            agent_id=agent_id, turn_instance_id=turn_instance_id,
            configured_limit=configured_limit,
        ),
    }
