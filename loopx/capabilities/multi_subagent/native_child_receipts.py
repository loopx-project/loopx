"""Turn-bound native child decisions, with explicit report/host provenance."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...control_plane.quota.accounting_admission import quota_accounting_admission
from ...control_plane.quota.settlement import read_heartbeat_settlement
from ...control_plane.runtime.public_safety import validate_public_safe_value
from ...rollout_event_log import (
    append_rollout_event_once,
    build_rollout_event,
    iter_rollout_events,
    load_rollout_events,
    rollout_event_log_path,
)


NATIVE_SUBAGENT_ACTIVITY_SCHEMA_VERSION = "native_subagent_activity_v0"
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
    events: Sequence[Mapping[str, Any]], *, goal_id: str, agent_id: str, turn_instance_id: str,
    goal_ref: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [dict(event) for event in events
            if event.get("event_kind") in EVENT_KINDS.values()
            and event.get("goal_id") == goal_id
            and event.get("agent_id") == agent_id
            and event.get("run_id") == turn_instance_id
            and (
                event.get("goal_ref") == dict(goal_ref)
                if goal_ref is not None
                else "goal_ref" not in event
            )]


def native_child_activity(
    events: Sequence[Mapping[str, Any]], *, goal_id: str, agent_id: str,
    turn_instance_id: str, configured_limit: int,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One read model for CLI, agent-context and product projections."""
    rows = _events_for_turn(
        events,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_instance_id,
        goal_ref=goal_ref,
    )
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
            if "reason_code" in details:
                row["result_reason_code"] = details["reason_code"]
        elif kind == EVENT_KINDS["review"]:
            row["parent_review"] = details.get("outcome")
            if "reason_code" in details:
                row["review_reason_code"] = details["reason_code"]
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
    sources = {row.get("observation_source") for row in ordered}
    observation = ("unknown" if not sources else "host_observed"
                   if sources == {"host_observed"} else "mixed"
                   if "host_observed" in sources else "coordinator_reported")
    return {
        "schema_version": NATIVE_SUBAGENT_ACTIVITY_SCHEMA_VERSION,
        "goal_id": goal_id, "agent_id": agent_id,
        "turn_instance_id": turn_instance_id,
        "entrypoint_scope": "host_native_child_tools",
        "observation": observation,
        "host_attested": observation == "host_observed",
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


def _load_native_child_events(
    runtime_root: Path, *, goal_id: str, agent_id: str,
    turn_instance_id: str,
    goal_ref: Mapping[str, Any] | None = None,
    registry_path: Path | None = None,
) -> list[dict[str, Any]]:
    with quota_accounting_admission(
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=goal_id,
        goal_ref=goal_ref,
        operation="native-child-read",
        lock_legacy_index=False,
    ) as source_admission:
        if source_admission is not None:
            readback = read_heartbeat_settlement(
                runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                todo_id=None,
                turn_instance_id=turn_instance_id,
                resolve_original_binding=True,
                registry_path=registry_path,
                goal_ref=goal_ref,
                source_admission=source_admission,
                borrow_source_admission=True,
            )
            if readback is None or readback.identity.value is None:
                raise ValueError(
                    "native child read requires an admitted, settlement-bound Turn guard"
                )
        source = iter_rollout_events(
            rollout_event_log_path(runtime_root, goal_id)
        )
        events = [
            event
            for event in source
            if event.get("event_kind") in EVENT_KINDS.values()
            and event.get("goal_id") == goal_id
            and event.get("agent_id") == agent_id
            and event.get("run_id") == turn_instance_id
            and (
                event.get("goal_ref") == dict(goal_ref)
                if goal_ref is not None
                else "goal_ref" not in event
            )
        ]
        return events


def load_native_child_activity(
    runtime_root: Path, *, goal_id: str, agent_id: str,
    turn_instance_id: str, configured_limit: int,
    goal_ref: Mapping[str, Any] | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    events = _load_native_child_events(
        runtime_root, goal_id=goal_id, agent_id=agent_id,
        turn_instance_id=turn_instance_id, goal_ref=goal_ref,
        registry_path=registry_path,
    )
    return native_child_activity(
        events, goal_id=goal_id, agent_id=agent_id,
        turn_instance_id=turn_instance_id, configured_limit=configured_limit,
        goal_ref=goal_ref,
    )


def latest_native_child_activity(
    events: Sequence[Mapping[str, Any]], *, goal_id: str, configured_limit: int,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Expose only the latest reported Turn in existing Goal status surfaces."""
    observations = [event for event in events
                    if event.get("goal_id") == goal_id
                    and event.get("event_kind") in EVENT_KINDS.values()
                    and event.get("agent_id") and event.get("run_id")
                    and (
                        event.get("goal_ref") == dict(goal_ref)
                        if goal_ref is not None
                        else "goal_ref" not in event
                    )]
    if not observations:
        return None
    latest = max(observations, key=lambda event: str(event.get("recorded_at") or ""))
    return native_child_activity(
        events, goal_id=goal_id, agent_id=str(latest["agent_id"]),
        turn_instance_id=str(latest["run_id"]), configured_limit=configured_limit,
        goal_ref=goal_ref,
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
    # Optional identity echoes are checked against the immutable decision under
    # the append lock; they do not become duplicate result/review authority.
    if operation is not None:
        _choice(operation, field="operation", choices=OPERATIONS)
    if entrypoint_id is not None:
        _id(entrypoint_id, field="entrypoint_id")
    if stage == "result":
        result = _choice(outcome, field="outcome", choices=RESULT_OUTCOMES)
        if evidence_ref is not None or validation_ref is not None:
            raise ValueError("evidence refs belong to an accepted parent review")
        return {"outcome": result, **({"reason_code": _id(reason_code, field="reason_code")}
                                     if reason_code is not None else {})}
    if stage == "review":
        result = _choice(outcome, field="outcome", choices=REVIEW_OUTCOMES)
        if result == "accepted":
            if reason_code is not None:
                raise ValueError("accepted review must not include a reason_code")
            return {"outcome": result, "evidence_ref": _id(evidence_ref, field="evidence_ref"),
                    "validation_ref": _id(validation_ref, field="validation_ref")}
        if evidence_ref is not None or validation_ref is not None:
            raise ValueError("deferred/rejected review must not adopt evidence")
        # Diagnostic codes explain nonadoption, never authorize it. Keep them
        # optional and public-safe rather than imposing a provider vocabulary.
        return {"outcome": result, **({"reason_code": _id(reason_code, field="reason_code")}
                                     if reason_code is not None else {})}
    raise ValueError("stage must be decision, result or review")


def _record_native_child(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    turn_instance_id: str, operation_id: str, configured_limit: int,
    stage: str, outcome: str, operation: str | None = None,
    entrypoint_id: str | None = None, reason_code: str | None = None,
    evidence_ref: str | None = None, validation_ref: str | None = None,
    execute: bool = False,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
    source_admission: Mapping[str, Any] | None = None,
    _host_observed: bool = False,
    _host_child_refs: Sequence[str] | None = None,
    _host_wait_ref: str | None = None,
) -> dict[str, Any]:
    """Preview or append a typed report; never launch a child or spend quota."""
    goal_id = _id(goal_id, field="goal_id")
    agent_id = _id(agent_id, field="agent_id")
    turn_instance_id = _id(turn_instance_id, field="turn_instance_id")
    operation_id = _id(operation_id, field="operation_id")
    if isinstance(configured_limit, bool) or not isinstance(configured_limit, int) or configured_limit < 1:
        raise ValueError("enabled multi_subagent configured_limit must be positive")
    fields: dict[str, Any] = _normalized_fields(
        stage=stage, operation=operation, outcome=outcome, entrypoint_id=entrypoint_id,
        reason_code=reason_code, evidence_ref=evidence_ref, validation_ref=validation_ref,
    )
    if _host_observed:
        if stage == "review" or operation == "skip":
            raise ValueError("host observation cannot attest a parent review or skip")
        fields["observation_source"] = "host_observed"
    if _host_child_refs is not None:
        if (not _host_observed or stage != "decision" or outcome != "started"
                or isinstance(_host_child_refs, (str, bytes)) or not _host_child_refs):
            raise ValueError("child correlation requires a started host-observed decision")
        # Rollout details are scalar; each opaque binding remains exact and
        # cannot collide with the decision fields or be text-truncated.
        fields.update({"host_child_ref_" + _id(ref, field="host_child_ref"): True
                       for ref in sorted(set(_host_child_refs))})
    if _host_wait_ref is not None:
        if not _host_observed or stage != "result":
            raise ValueError("wait correlation requires a host-observed result")
        fields["host_wait_ref"] = _id(_host_wait_ref, field="host_wait_ref")
    log_path = rollout_event_log_path(runtime_root, goal_id)
    events = load_rollout_events(log_path)
    prior = _events_for_turn(
        events,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_instance_id,
        goal_ref=goal_ref,
    )

    def started_decision(current: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        decision = next((item for item in current if item.get("case_id") == operation_id
                         and item.get("event_kind") == EVENT_KINDS["decision"]), None)
        if decision is None or _details(decision).get("outcome") != "started":
            raise ValueError("result/review requires a started native child decision")
        details = _details(decision)
        for key, echo in (("operation", operation), ("entrypoint_id", entrypoint_id)):
            if echo is not None and echo != details.get(key):
                raise ValueError(f"{key} conflicts with the native child decision identity")
        return decision

    if stage != "decision":
        # Exact replays skip append preconditions, but an echoed identity must
        # still match; normalization must not silently discard a contradiction.
        started_decision(prior)
    existing = next((event for event in prior
                     if event.get("case_id") == operation_id
                     and event.get("event_kind") == EVENT_KINDS[stage]
                     and (stage != "result" or _host_wait_ref is None
                          or _details(event).get("host_wait_ref") == _host_wait_ref)), None)
    if stage == "result" and _host_wait_ref is None and existing is not None:
        # A later decision snapshot can confirm the same typed result without
        # replacing the causal wait binding already stored on that result.
        wait_ref = _details(existing).get("host_wait_ref")
        if wait_ref is not None:
            fields["host_wait_ref"] = wait_ref
    if existing is not None and _details(existing) != fields:
        raise ValueError("operation identity already has a conflicting native child report")

    def validate_result_identity(current: Sequence[Mapping[str, Any]]) -> None:
        if stage != "result":
            return
        for result in current:
            if result.get("event_kind") != EVENT_KINDS["result"]:
                continue
            details = _details(result)
            if (_host_wait_ref is not None and details.get("host_wait_ref") == _host_wait_ref
                    and result.get("case_id") != operation_id):
                raise ValueError("wait identity already has a conflicting native child binding")
            if (result.get("case_id") == operation_id
                    and any(details.get(key) != fields.get(key)
                            for key in ("outcome", "observation_source"))):
                raise ValueError("operation identity already has a conflicting native child report")

    validate_result_identity(prior)

    def report_admission() -> Mapping[str, Any]:
        readback = read_heartbeat_settlement(
            runtime_root, goal_id=goal_id, agent_id=agent_id, todo_id=None,
            turn_instance_id=turn_instance_id, resolve_original_binding=True,
            registry_path=registry_path, goal_ref=goal_ref,
            source_admission=source_admission,
            borrow_source_admission=source_admission is not None,
        )
        if readback is None or readback.identity.value is None or readback.identity.failure is not None:
            reason = (readback.identity.failure.reason
                      if readback is not None and readback.identity.failure is not None else "guard missing")
            raise ValueError("native child report requires an admitted, settlement-bound Turn guard: " + reason)
        admission = readback.native_child_admission
        if (not isinstance(admission, Mapping)
                or admission.get("schema_version") != "native_child_report_admission_v0"
                or admission.get("report_permission") not in {
                    "new_operation", "existing_operation_only", "not_admitted",
                }):
            raise ValueError("TypeScript native child report admission missing or invalid")
        return admission

    def validate_transition(observed: Sequence[Mapping[str, Any]]) -> None:
        admission = report_admission()
        current = _events_for_turn(observed, goal_id=goal_id, agent_id=agent_id,
                                   turn_instance_id=turn_instance_id,
                                   goal_ref=goal_ref)
        validate_result_identity(current)
        decisions = {str(item.get("case_id")): item for item in current
                     if item.get("event_kind") == EVENT_KINDS["decision"]}
        if stage == "decision":
            if admission["report_permission"] != "new_operation":
                raise ValueError("native child decision requires an open, work-admitted Turn guard")
            # Host observations record calls that already happened, including
            # violations; recording one cannot authorize another host call.
            if not _host_observed and fields["operation"] in {"spawn", "followup"} and any(
                _details(item).get("outcome") in {"capacity_rejected", "host_failed"}
                for item in decisions.values()
            ):
                raise ValueError("host failure forbids same-Turn spawn/followup retry")
            return
        if admission["report_permission"] == "not_admitted":
            raise ValueError("native child report requires a work-admitted Turn guard")
        started_decision(current)
        if stage == "review":
            result = next((item for item in current if item.get("case_id") == operation_id
                           and item.get("event_kind") == EVENT_KINDS["result"]), None)
            if result is None or _details(result).get("outcome") not in RESULT_OUTCOMES:
                raise ValueError("parent review requires a terminal native child result")
            if fields["outcome"] == "accepted" and _details(result).get("outcome") != "completed":
                raise ValueError("accepted parent review requires a completed native child result")

    if not execute:
        if existing is None:
            validate_transition(prior)
        else:
            report_admission()
    event = build_rollout_event(
        goal_id=goal_id, event_kind=EVENT_KINDS[stage], agent_id=agent_id,
        run_id=turn_instance_id, case_id=operation_id, status=fields["outcome"],
        details=fields, recorded_at=(existing or {}).get("recorded_at"),
        goal_ref=goal_ref,
    )
    appended = False
    if execute:
        stored, appended = append_rollout_event_once(
            log_path, event,
            identity_fields=(
                "goal_id",
                "event_kind",
                "agent_id",
                "run_id",
                "case_id",
                *(("goal_ref",) if goal_ref is not None else ()),
                *(("details",) if _host_wait_ref is not None else ()),
            ),
            precondition=lambda: validate_transition(load_rollout_events(log_path)),
        )
        if _details(stored) != fields:
            raise ValueError("operation identity already has a conflicting native child report")
        if not appended:
            # Append-once skips its precondition for an exact duplicate. Verify
            # the original identity without re-authorizing work or appending.
            report_admission()
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
            goal_ref=goal_ref,
        ),
    }


def record_native_child(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    turn_instance_id: str, operation_id: str, configured_limit: int,
    stage: str, outcome: str, operation: str | None = None,
    entrypoint_id: str | None = None, reason_code: str | None = None,
    evidence_ref: str | None = None, validation_ref: str | None = None,
    execute: bool = False, registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
    _host_observed: bool = False,
    _host_child_refs: Sequence[str] | None = None,
    _host_wait_ref: str | None = None,
) -> dict[str, Any]:
    """Preview or append one report under its exact quota owner."""

    with quota_accounting_admission(
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=goal_id,
        goal_ref=goal_ref,
        operation="native-child-report",
        lock_legacy_index=False,
    ) as source_admission:
        return _record_native_child(
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            operation_id=operation_id,
            configured_limit=configured_limit,
            stage=stage,
            outcome=outcome,
            operation=operation,
            entrypoint_id=entrypoint_id,
            reason_code=reason_code,
            evidence_ref=evidence_ref,
            validation_ref=validation_ref,
            execute=execute,
            registry_path=registry_path,
            goal_ref=goal_ref,
            source_admission=source_admission,
            _host_observed=_host_observed,
            _host_child_refs=_host_child_refs,
            _host_wait_ref=_host_wait_ref,
        )
