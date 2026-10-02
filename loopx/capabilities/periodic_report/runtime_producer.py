from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from ...rollout_event_log import ROLLOUT_EVENT_SCHEMA_VERSION
from ...control_plane.digest_envelope import sha256_envelope
from .core import (
    _integer,
    _object,
    _reject_raw_keys,
    _text,
    _timestamp,
    _token,
)
from .triggers import (
    build_periodic_report_trigger_decision,
    normalize_periodic_report_trigger_policy,
)


RUNTIME_TRIGGER_REQUEST_SCHEMA = "periodic_report_runtime_trigger_request_v0"
RUNTIME_PRODUCER_RECEIPT_SCHEMA = "periodic_report_runtime_producer_v0"
STAGE_COMPLETION_SCHEMA = "periodic_report_stage_completion_receipt_v0"
_MAX_RELEVANT_ROLLOUT_EVENTS = 4096

_SAFE_BOUNDARY_FIELDS = (
    "raw_task_text_recorded",
    "raw_logs_recorded",
    "raw_trajectory_recorded",
    "raw_session_transcript_recorded",
    "credential_values_recorded",
    "absolute_paths_recorded",
)


def _parsed_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event_digest(event_ids: Sequence[str]) -> str:
    encoded = json.dumps(
        sorted(event_ids),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_envelope(encoded)


def _safe_durable_event(
    raw: Mapping[str, Any],
    *,
    goal_id: str,
    window_start: datetime,
    window_end: datetime,
    index: int,
) -> dict[str, Any] | None:
    label = f"rollout_events[{index}]"
    event = _object(raw, label)
    if event.get("schema_version") != ROLLOUT_EVENT_SCHEMA_VERSION:
        raise ValueError(f"{label} must use {ROLLOUT_EVENT_SCHEMA_VERSION}")
    if event.get("goal_id") != goal_id:
        return None
    kind = str(event.get("event_kind") or "").strip()
    if kind not in {"todo_complete", "refresh_state", "quota_should_run"}:
        return None
    recorded_at = _timestamp(event.get("recorded_at"), f"{label}.recorded_at")
    if not window_start <= _parsed_timestamp(recorded_at) <= window_end:
        return None
    event_id = _text(event.get("event_id"), f"{label}.event_id", maximum=128)
    boundary = _object(event.get("boundary"), f"{label}.boundary")
    if any(boundary.get(field) is not False for field in _SAFE_BOUNDARY_FIELDS):
        raise ValueError(f"{label}.boundary does not prove a public-safe event")
    details = (
        dict(event["details"])
        if isinstance(event.get("details"), Mapping)
        else {}
    )
    stage_completion: dict[str, Any] | None = None
    raw_stage_identity = details.get("stage_identity")
    if raw_stage_identity is not None:
        if details.get("stage_completion_schema") != STAGE_COMPLETION_SCHEMA:
            raise ValueError(
                f"{label}.details.stage_completion_schema must use "
                f"{STAGE_COMPLETION_SCHEMA}"
            )
        stage_identity = _token(
            raw_stage_identity, f"{label}.details.stage_identity"
        )
        closed_vision_revision = _text(
            details.get("closed_vision_revision"),
            f"{label}.details.closed_vision_revision",
            maximum=128,
        )
        frontier_identity = _text(
            details.get("frontier_identity"),
            f"{label}.details.frontier_identity",
            maximum=256,
        )
        transition = _token(
            details.get("stage_transition"),
            f"{label}.details.stage_transition",
        )
        acceptance = _token(
            details.get("stage_acceptance"),
            f"{label}.details.stage_acceptance",
        )
        completed_at = _timestamp(
            details.get("stage_completed_at", recorded_at),
            f"{label}.details.stage_completed_at",
        )
        event_status = str(event.get("status") or "").strip()
        if (
            (
                kind == "refresh_state"
                and event_status in {"appended", "receipt_repaired"}
                or kind == "quota_should_run"
                and event_status in {"normal_run", "should-run"}
            )
            and transition in {"successor_frontier_settled", "goal_terminal"}
            and acceptance == "validated"
            and details.get("stage_outcome_checkpoint_satisfied") is True
            and details.get("stage_durable_writeback_required") is True
        ):
            stage_completion = {
                "stage_identity": stage_identity,
                "closed_vision_revision": closed_vision_revision,
                "frontier_identity": frontier_identity,
                "transition": transition,
                "completed_at": completed_at,
                "completion_receipt_ref": f"stage:{stage_identity}",
            }
    return {
        "event_id": event_id,
        "event_kind": kind,
        "recorded_at": recorded_at,
        "todo_id": str(event.get("todo_id") or "").strip() or None,
        "replan_recorded": details.get("autonomous_replan_recorded") is True,
        "stage_completion": stage_completion,
    }


def build_periodic_report_runtime_trigger_decision(
    request: Mapping[str, Any],
    *,
    rollout_events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Promote durable control-plane events into one normal trigger decision.

    This is the provider-neutral post-writeback producer seam. It reads no files
    and performs no writes; the CLI adapter supplies events read from the durable
    rollout log, then the existing trigger decision remains authoritative.
    """

    payload = _object(request, "request")
    _reject_raw_keys(payload, "request")
    unknown = sorted(
        set(payload)
        - {
            "schema_version",
            "evaluated_at",
            "goal_id",
            "profile",
            "trigger_policy",
            "segment",
            "last_report",
        }
    )
    if unknown:
        raise ValueError("request contains unsupported fields: " + ", ".join(unknown))
    if payload.get("schema_version") != RUNTIME_TRIGGER_REQUEST_SCHEMA:
        raise ValueError(f"schema_version must be {RUNTIME_TRIGGER_REQUEST_SCHEMA!r}")
    evaluated_at = _timestamp(payload.get("evaluated_at"), "evaluated_at")
    goal_id = _token(payload.get("goal_id"), "goal_id")
    policy = normalize_periodic_report_trigger_policy(
        payload.get("trigger_policy", {})
    )
    aggregation = policy.get("aggregation")
    if not isinstance(aggregation, Mapping):
        raise ValueError("trigger_policy.aggregation is required for runtime promotion")
    if aggregation.get("stage_completion_required") is not True:
        raise ValueError(
            "runtime promotion requires trigger_policy.aggregation."
            "stage_completion_required=true"
        )

    segment = _object(payload.get("segment"), "segment")
    segment_ref = _token(segment.get("segment_ref"), "segment.segment_ref")
    start_at = _timestamp(segment.get("start_at"), "segment.start_at")
    end_at = _timestamp(segment.get("end_at"), "segment.end_at")
    window_start = _parsed_timestamp(start_at)
    window_end = _parsed_timestamp(end_at)
    if window_start >= window_end:
        raise ValueError("segment.start_at must be earlier than segment.end_at")
    if end_at != evaluated_at:
        raise ValueError("segment.end_at must match evaluated_at")
    duration = int((window_end - window_start).total_seconds())
    if duration > int(aggregation["window_seconds"]):
        raise ValueError("segment window exceeds trigger_policy.aggregation.window_seconds")
    remaining_todo_count = _integer(
        segment.get("remaining_todo_count"),
        "segment.remaining_todo_count",
        maximum=1_000_000,
    )
    if isinstance(rollout_events, (str, bytes, Mapping)):
        raise ValueError("rollout_events must be an iterable of objects")

    relevant: list[dict[str, Any]] = []
    relevant_count = 0
    seen_event_ids: set[str] = set()
    for index, raw_event in enumerate(rollout_events):
        event = _safe_durable_event(
            raw_event,
            goal_id=goal_id,
            window_start=window_start,
            window_end=window_end,
            index=index,
        )
        if event is None:
            continue
        relevant_count += 1
        if relevant_count > _MAX_RELEVANT_ROLLOUT_EVENTS:
            raise ValueError(
                "rollout_events must contain at most "
                f"{_MAX_RELEVANT_ROLLOUT_EVENTS} relevant window items"
            )
        event_id = str(event["event_id"])
        if event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        relevant.append(event)
    relevant.sort(key=lambda item: (item["recorded_at"], item["event_id"]))

    completions: list[dict[str, Any]] = []
    seen_completion_keys: set[str] = set()
    replans: list[dict[str, Any]] = []
    for event in relevant:
        if event["event_kind"] == "todo_complete":
            completion_key = str(event.get("todo_id") or event["event_id"])
            if completion_key not in seen_completion_keys:
                seen_completion_keys.add(completion_key)
                completions.append(event)
        elif event["replan_recorded"]:
            replans.append(event)

    transition: str | None = None
    contributing: list[dict[str, Any]] = []
    delivered_count = 0
    selected_stage_completion: dict[str, Any] | None = None
    stage_events: list[dict[str, Any]] = []
    seen_stage_identities: set[str] = set()
    for event in relevant:
        stage_completion = event.get("stage_completion")
        if not isinstance(stage_completion, Mapping):
            continue
        stage_identity = str(stage_completion.get("stage_identity") or "")
        if not stage_identity or stage_identity in seen_stage_identities:
            continue
        seen_stage_identities.add(stage_identity)
        stage_events.append(event)
    boundary_event = stage_events[0] if stage_events else None
    if boundary_event is not None:
        completions_at_boundary = [
            event
            for event in completions
            if event["recorded_at"] <= boundary_event["recorded_at"]
        ]
        transition = "segment_completed"
        contributing = [boundary_event]
        delivered_count = len(completions_at_boundary)

    source_ref = f"rollout-window:{goal_id}:{segment_ref}"
    if transition:
        evidence_ids = [str(event["event_id"]) for event in contributing]
        stage_completion = dict(contributing[-1]["stage_completion"])
        selected_stage_completion = stage_completion
        source_ref = f"{source_ref}:{stage_completion['stage_identity']}"
        candidate = {
            "trigger_kind": "bounded_segment_milestone",
            "observed_at": str(contributing[-1]["recorded_at"]),
            "source_ref": source_ref,
            "evidence_digest": _event_digest(
                [str(stage_completion["stage_identity"])]
            ),
            "facts": {
                "segment_ref": segment_ref,
                "transition": transition,
                "delivered_count": delivered_count,
                "remaining_todo_count": remaining_todo_count,
                "durable_writeback": True,
                "acceptance": "validated",
                "completed_at": stage_completion["completed_at"],
                "completion_receipt_ref": stage_completion[
                    "completion_receipt_ref"
                ],
                "stage_identity": stage_completion["stage_identity"],
                "closed_vision_revision": stage_completion[
                    "closed_vision_revision"
                ],
                "frontier_identity": stage_completion["frontier_identity"],
                "stage_transition": stage_completion["transition"],
                "outcome_checkpoint_satisfied": True,
                "status": "completed",
            },
        }
        status = "promoted"
        reason = "authoritative_stage_completion_observed"
    else:
        evidence_ids = [str(event["event_id"]) for event in relevant]
        candidate = {
            "trigger_kind": "state_refreshed",
            "observed_at": end_at,
            "source_ref": source_ref,
            "evidence_digest": _event_digest(evidence_ids or [segment_ref]),
            "facts": {},
        }
        status = "not_promoted"
        reason = "authoritative_stage_completion_missing"

    trigger_request: dict[str, Any] = {
        "schema_version": "periodic_report_trigger_request_v0",
        "evaluated_at": evaluated_at,
        "profile": payload.get("profile"),
        "trigger_policy": policy,
        "candidates": [candidate],
    }
    if payload.get("last_report") is not None:
        trigger_request["last_report"] = payload["last_report"]
    decision = build_periodic_report_trigger_decision(trigger_request)
    decision["producer_receipt"] = {
        "schema_version": RUNTIME_PRODUCER_RECEIPT_SCHEMA,
        "status": status,
        "reason": reason,
        "goal_id": goal_id,
        "segment_ref": segment_ref,
        "window": {"start_at": start_at, "end_at": end_at},
        "todo_completed_count": len(completions),
        "replan_event_count": len(replans),
        "stage_completion_count": len(stage_events),
        "selected_stage_completion": selected_stage_completion,
        "contributing_event_ids": evidence_ids,
        "transition": transition,
        "boundary": {
            "durable_rollout_events_required": True,
            "provider_neutral": True,
            "external_writes_performed": False,
            "raw_content_persisted": False,
        },
    }
    return decision
