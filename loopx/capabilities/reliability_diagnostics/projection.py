"""Compact read-only diagnostic projection derived from event kinds and timing.

This projection is a sibling of the session-runtime projection, never merged
into it. It carries explicit boundary fields (``mode``, ``authority``) so a
consumer can prove it holds no runtime authority, and every signal is derived
from typed event kinds and observed timestamps only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from typing import Any

from .envelope import (
    CAPABILITY_ID,
    IDENTITY_TOKEN_PATTERN,
    ObserverEnvelope,
    ObserverEventKind,
    observed_at_microseconds,
    parse_observed_at,
)
from .intake import normalize_observer_run_identity
from .receipt import LedgerReading, build_integrity_receipt

DIAGNOSTIC_PROJECTION_SCHEMA_VERSION = "reliability_diagnostic_projection_v0"
DEFAULT_STALL_THRESHOLD_MS = 300_000
DEFAULT_REPETITION_THRESHOLD = 3
_TERMINAL_ERROR_REASONS = frozenset(
    {"error", "failed", "failure", "aborted", "cancelled", "canceled", "timeout"}
)


class DiagnosticStage(StrEnum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    RUNNING = "running"
    TOOL_RUNNING = "tool_running"
    ERRORED = "errored"
    DISPOSED = "disposed"


class DiagnosticSignal(StrEnum):
    STALL_SUSPECTED = "stall_suspected"
    REPETITION_SUSPECTED = "repetition_suspected"
    UNRECOVERED_ERROR = "unrecovered_error"
    EVENT_LOSS = "event_loss"
    INTEGRITY_NOT_VALID = "integrity_not_valid"


class BindingIneligibility(StrEnum):
    """Owner-local reasons for refusing a bound diagnostic read, not runtime state."""
    NO_OBSERVATIONS = "no_observations"
    SESSION_IDENTITY_MISMATCH = "session_identity_mismatch"
    RUN_IDENTITY_MISMATCH = "run_identity_mismatch"
    INTEGRITY_NOT_VALID = "integrity_not_valid"
    OBSERVATION_IN_FUTURE = "observation_in_future"
    OBSERVATION_STALE = "observation_stale"


_ACTIVE_STAGES = frozenset({DiagnosticStage.RUNNING, DiagnosticStage.TOOL_RUNNING})


def _stage_after(envelope: ObserverEnvelope) -> DiagnosticStage:
    kind = envelope.event_kind
    if kind is ObserverEventKind.SESSION_DISPOSED:
        return DiagnosticStage.DISPOSED
    if kind is ObserverEventKind.AGENT_ERROR:
        return DiagnosticStage.ERRORED
    if kind is ObserverEventKind.TOOL_CALLED:
        return DiagnosticStage.TOOL_RUNNING
    if kind is ObserverEventKind.TURN_ENDED:
        if str(envelope.summary.get("reason", "")) in _TERMINAL_ERROR_REASONS:
            return DiagnosticStage.ERRORED
        return DiagnosticStage.IDLE
    if kind is ObserverEventKind.SESSION_STARTED:
        return DiagnosticStage.IDLE
    if kind is ObserverEventKind.AGENT_STATUS:
        return (
            DiagnosticStage.RUNNING
            if envelope.summary.get("status") == "running"
            else DiagnosticStage.IDLE
        )
    if kind is ObserverEventKind.UNSUPPORTED:
        return DiagnosticStage.UNKNOWN
    return DiagnosticStage.RUNNING


def _ms_between(earlier: str, later: str) -> int:
    return int(
        Fraction(observed_at_microseconds(later) - observed_at_microseconds(earlier), 1000)
    )


@dataclass
class _ProjectionAccumulator:
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "turns_started": 0,
            "turns_ended": 0,
            "steps": 0,
            "tool_calls": 0,
            "errors": 0,
        }
    )
    stage: DiagnosticStage = DiagnosticStage.UNKNOWN
    max_gap_ms: int = 0
    longest_run: int = 0
    longest_run_tool: str | None = None
    current_run: int = 0
    current_tool: str | None = None
    unrecovered_errors: int = 0
    recovered_errors: int = 0
    previous: ObserverEnvelope | None = None

    def consume(self, envelope: ObserverEnvelope) -> None:
        if self.previous is not None:
            self.max_gap_ms = max(
                self.max_gap_ms,
                _ms_between(self.previous.observed_at, envelope.observed_at),
            )
        self._track_progress(envelope)
        self._track_repetition(envelope)
        self.stage = _stage_after(envelope)
        self.previous = envelope

    def _track_progress(self, envelope: ObserverEnvelope) -> None:
        kind = envelope.event_kind
        if kind is ObserverEventKind.TURN_STARTED:
            self.counts["turns_started"] += 1
            return
        if kind is ObserverEventKind.TURN_ENDED:
            self.counts["turns_ended"] += 1
            self._track_turn_end(envelope)
            return
        if kind is ObserverEventKind.STEP_ENDED:
            self.counts["steps"] += 1
            self._recover_errors()
            return
        if kind is ObserverEventKind.AGENT_ERROR:
            self.counts["errors"] += 1
            self.unrecovered_errors += 1

    def _track_turn_end(self, envelope: ObserverEnvelope) -> None:
        terminal_error = (
            str(envelope.summary.get("reason", "")) in _TERMINAL_ERROR_REASONS
        )
        if terminal_error:
            if not self.unrecovered_errors:
                self.counts["errors"] += 1
                self.unrecovered_errors = 1
            return
        self._recover_errors()

    def _recover_errors(self) -> None:
        if not self.unrecovered_errors:
            return
        self.recovered_errors += self.unrecovered_errors
        self.unrecovered_errors = 0

    def _track_repetition(self, envelope: ObserverEnvelope) -> None:
        kind = envelope.event_kind
        if kind is ObserverEventKind.TOOL_CALLED:
            self.counts["tool_calls"] += 1
            tool = str(envelope.summary.get("tool_name", ""))
            self.current_run = self.current_run + 1 if tool == self.current_tool else 1
            self.current_tool = tool
            if self.current_run > self.longest_run:
                self.longest_run = self.current_run
                self.longest_run_tool = tool or None
            return
        if kind not in {
            ObserverEventKind.TOOL_COMPLETED,
            ObserverEventKind.AGENT_PRE_STEP,
        }:
            self.current_run = 0
            self.current_tool = None


def _diagnostic_signals(
    *,
    stall_detected: bool,
    repetition_detected: bool,
    unrecovered_errors: int,
    receipt: dict[str, Any],
) -> list[str]:
    candidates = (
        (stall_detected, DiagnosticSignal.STALL_SUSPECTED),
        (repetition_detected, DiagnosticSignal.REPETITION_SUSPECTED),
        (bool(unrecovered_errors), DiagnosticSignal.UNRECOVERED_ERROR),
        (
            bool(receipt["lost_event_count"] or receipt["backpressure_drop_count"]),
            DiagnosticSignal.EVENT_LOSS,
        ),
        (receipt["status"] != "valid", DiagnosticSignal.INTEGRITY_NOT_VALID),
    )
    return [signal.value for applies, signal in candidates if applies]


def build_diagnostic_projection(
    reading: LedgerReading,
    *,
    as_of: str | None = None,
    stall_threshold_ms: int = DEFAULT_STALL_THRESHOLD_MS,
    repetition_threshold: int = DEFAULT_REPETITION_THRESHOLD,
    expected_binding: Mapping[str, Any] | None = None,
    max_observation_age_ms: int | None = None,
) -> dict[str, Any]:
    binding = None
    if expected_binding is not None:
        fields = {"provider_id", "observer_id", "session_id", "run_identity"}
        if not isinstance(expected_binding, Mapping) or set(expected_binding) != fields:
            raise ValueError(
                "expected_binding requires provider_id, observer_id, session_id and run_identity"
            )
        for key in fields - {"run_identity"}:
            value = expected_binding[key]
            if not isinstance(value, str) or not IDENTITY_TOKEN_PATTERN.fullmatch(
                value
            ):
                raise ValueError(f"expected_binding.{key} must be an identity token")
        identity = normalize_observer_run_identity(expected_binding["run_identity"])
        binding = {**expected_binding, "run_identity": identity.as_dict()}
        if as_of is None:
            raise ValueError(
                "expected_binding requires an explicit as_of evaluation time"
            )
        if (
            isinstance(max_observation_age_ms, bool)
            or not isinstance(max_observation_age_ms, int)
            or max_observation_age_ms < 0
        ):
            raise ValueError(
                "expected_binding requires a non-negative max_observation_age_ms"
            )
    elif max_observation_age_ms is not None:
        raise ValueError("max_observation_age_ms requires expected_binding")
    if as_of is not None:
        message = "as_of must be a timezone-aware ISO-8601 timestamp"
        try:
            parsed_as_of = parse_observed_at(as_of)
        except ValueError as exc:
            raise ValueError(message) from exc
        if parsed_as_of.utcoffset() is None:
            raise ValueError(message)

    receipt = build_integrity_receipt(reading)
    envelopes = reading.ordered_envelopes

    state = _ProjectionAccumulator()
    for envelope in envelopes:
        state.consume(envelope)

    last_observed_at = state.previous.observed_at if state.previous else None
    effective_as_of = as_of or last_observed_at
    last_event_age_ms = (
        _ms_between(last_observed_at, effective_as_of)
        if last_observed_at and effective_as_of
        else 0
    )
    stall_detected = (
        state.stage in _ACTIVE_STAGES and last_event_age_ms >= stall_threshold_ms
    )
    repetition_detected = state.longest_run >= repetition_threshold
    signals = _diagnostic_signals(
        stall_detected=stall_detected,
        repetition_detected=repetition_detected,
        unrecovered_errors=state.unrecovered_errors,
        receipt=receipt,
    )

    result = {
        "schema_version": DIAGNOSTIC_PROJECTION_SCHEMA_VERSION,
        "capability_id": CAPABILITY_ID,
        "goal_id": reading.goal_id,
        "mode": "read_only",
        "authority": "none",
        "write_scope": "diagnostic_ledger_only",
        "worker_influence": "none",
        "provider_ids": receipt["provider_ids"],
        "stage": state.stage.value,
        "counts": state.counts,
        "stall": {
            "detected": stall_detected,
            "threshold_ms": stall_threshold_ms,
            "last_event_age_ms": last_event_age_ms,
            "max_inter_event_gap_ms": state.max_gap_ms,
        },
        "repetition": {
            "detected": repetition_detected,
            "threshold": repetition_threshold,
            "longest_tool_run": state.longest_run,
            "tool_name": state.longest_run_tool,
        },
        "recovery": {
            "error_count": state.counts["errors"],
            "recovered_error_count": state.recovered_errors,
            "unrecovered_error_count": state.unrecovered_errors,
        },
        "signals": signals,
        "integrity": {
            "status": receipt["status"],
            "reason_codes": receipt["reason_codes"],
        },
        "evidence": {
            "observed_event_count": receipt["observed_event_count"],
            "lost_event_count": receipt["lost_event_count"],
            "session_count": receipt["session_count"],
            "observed_from": receipt["observed_from"],
            "observed_until": receipt["observed_until"],
            "as_of": effective_as_of,
        },
    }
    if binding is not None:
        reasons: list[str] = []
        if not envelopes:
            reasons.append(BindingIneligibility.NO_OBSERVATIONS.value)
        if any(
            item.provider_id != binding["provider_id"]
            or item.observer_id != binding["observer_id"]
            or item.session_id != binding["session_id"]
            for item in envelopes
        ):
            reasons.append(BindingIneligibility.SESSION_IDENTITY_MISMATCH.value)
        stats = list(reading.stats.values())
        if len(stats) != 1 or any(
            item.provider_id != binding["provider_id"]
            or item.observer_id != binding["observer_id"]
            or item.run_identity.as_dict() != binding["run_identity"]
            for item in stats
        ):
            reasons.append(BindingIneligibility.RUN_IDENTITY_MISMATCH.value)
        if receipt["status"] != "valid":
            reasons.append(BindingIneligibility.INTEGRITY_NOT_VALID.value)
        # Compare instants; textual timestamp ordering can mix UTC offsets.
        observation_ages_us = [
            observed_at_microseconds(effective_as_of)
            - observed_at_microseconds(item.observed_at)
            for item in envelopes
        ]
        if any(age < 0 for age in observation_ages_us):
            reasons.append(BindingIneligibility.OBSERVATION_IN_FUTURE.value)
        elif (
            observation_ages_us
            and min(observation_ages_us) > max_observation_age_ms * 1000
        ):
            reasons.append(BindingIneligibility.OBSERVATION_STALE.value)
        result["binding"] = {
            "expected": binding,
            "eligible": not reasons,
            "reason_codes": reasons,
            "max_observation_age_ms": max_observation_age_ms,
        }
        if reasons:
            result["stage"] = DiagnosticStage.UNKNOWN.value
    return result
