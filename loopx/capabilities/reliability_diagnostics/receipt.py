"""Treatment-integrity receipt for one goal's shadow-observer ledger.

The receipt answers whether the ledger is admissible passive evidence. It is
computed only from ledger records: accepted envelopes and observer stats. Its
status enum is total and ordered; every non-``valid`` status names typed reason
codes so an operator never has to infer why evidence was downgraded.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .envelope import (
    CAPABILITY_ID,
    OBSERVER_ENVELOPE_SCHEMA_VERSION,
    OBSERVER_STATS_SCHEMA_VERSION,
    EnvelopeRejection,
    ObserverEnvelope,
    ObserverEnvelopeError,
    normalize_observer_envelope,
    observed_at_microseconds,
)
from .intake import ObserverStats, normalize_observer_stats

INTEGRITY_RECEIPT_SCHEMA_VERSION = "reliability_integrity_receipt_v0"
DEFAULT_CLOCK_UNCERTAINTY_DEGRADED_MS = 1000


class ReceiptStatus(StrEnum):
    VALID = "valid"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    INVALID = "invalid"


class ReceiptReason(StrEnum):
    NO_OBSERVATIONS = "no_observations"
    OUTBOUND_ENDPOINT_CONFIGURED = "outbound_endpoint_configured"
    OBSERVATION_ENTERED_WORKER_CONTEXT = "observation_entered_worker_context"
    OBSERVER_FAILURE = "observer_failure"
    CONTROL_FIELD_REJECTED = "control_field_rejected"
    PUBLIC_SAFETY_VIOLATION = "public_safety_violation"
    LEDGER_RECORD_INVALID = "ledger_record_invalid"
    OBSERVER_STATS_MISSING = "observer_stats_missing"
    OBSERVER_STATS_MISMATCH = "observer_stats_mismatch"
    IDENTITY_REJECTED = "identity_rejected"
    OBSERVATION_ENTERED_SCHEDULER_INPUTS = "observation_entered_scheduler_inputs"
    SEQUENCE_GAP = "sequence_gap"
    SEQUENCE_DUPLICATE = "sequence_duplicate"
    BACKPRESSURE_DROP = "backpressure_drop"
    RAW_MATERIAL_REJECTED = "raw_material_rejected"
    UNSUPPORTED_FIELD_REJECTED = "unsupported_field_rejected"
    CLOCK_UNCERTAINTY_EXCEEDED = "clock_uncertainty_exceeded"


_INVALID_REASONS = frozenset(
    {
        ReceiptReason.NO_OBSERVATIONS,
        ReceiptReason.OUTBOUND_ENDPOINT_CONFIGURED,
        ReceiptReason.OBSERVATION_ENTERED_WORKER_CONTEXT,
        ReceiptReason.OBSERVATION_ENTERED_SCHEDULER_INPUTS,
        ReceiptReason.LEDGER_RECORD_INVALID,
        ReceiptReason.OBSERVER_STATS_MISSING,
        ReceiptReason.OBSERVER_STATS_MISMATCH,
        ReceiptReason.IDENTITY_REJECTED,
    }
)
_QUARANTINE_REASONS = frozenset(
    {
        ReceiptReason.OBSERVER_FAILURE,
        ReceiptReason.CONTROL_FIELD_REJECTED,
        ReceiptReason.PUBLIC_SAFETY_VIOLATION,
    }
)


@dataclass
class LedgerReading:
    """Typed, ordered view over one goal ledger's records."""

    goal_id: str
    envelopes: list[ObserverEnvelope] = field(default_factory=list)
    stats: dict[str, ObserverStats] = field(default_factory=dict)
    invalid_record_count: int = 0

    @property
    def ordered_envelopes(self) -> list[ObserverEnvelope]:
        return sorted(
            self.envelopes,
            key=lambda item: (observed_at_microseconds(item.observed_at), item.session_id, item.sequence),
        )


def _append_envelope_record(
    reading: LedgerReading,
    record: Mapping[str, Any],
) -> None:
    envelope = normalize_observer_envelope(record)
    if envelope.goal_id != reading.goal_id:
        raise ObserverEnvelopeError(
            EnvelopeRejection.IDENTITY_INVALID, "goal_id does not match ledger"
        )
    reading.envelopes.append(envelope)


def _store_stats_record(
    reading: LedgerReading,
    record: Mapping[str, Any],
) -> None:
    stats = normalize_observer_stats(record)
    if stats.goal_id != reading.goal_id:
        raise ValueError("goal_id does not match ledger")
    previous = reading.stats.get(stats.observer_id)
    if previous is not None and (
        previous.provider_id != stats.provider_id
        or previous.run_identity != stats.run_identity
    ):
        raise ValueError("observer stats identity changed within one ledger")
    reading.stats[stats.observer_id] = stats


def _read_ledger_record(reading: LedgerReading, record: Mapping[str, Any]) -> None:
    schema = record.get("schema_version")
    if schema == OBSERVER_ENVELOPE_SCHEMA_VERSION:
        _append_envelope_record(reading, record)
        return
    if schema == OBSERVER_STATS_SCHEMA_VERSION:
        _store_stats_record(reading, record)
        return
    raise ValueError("unknown ledger record schema")


def read_ledger(
    records: Iterable[Any], *, goal_id: str, malformed_line_count: int = 0
) -> LedgerReading:
    reading = LedgerReading(goal_id=goal_id, invalid_record_count=malformed_line_count)
    for record in records:
        if not isinstance(record, Mapping):
            reading.invalid_record_count += 1
            continue
        try:
            _read_ledger_record(reading, record)
        except ValueError:
            reading.invalid_record_count += 1
    return reading


def _sequence_accounting(envelopes: Iterable[ObserverEnvelope]) -> tuple[int, int]:
    by_session: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for envelope in envelopes:
        by_session[
            (envelope.provider_id, envelope.observer_id, envelope.session_id)
        ].append(envelope.sequence)
    lost = 0
    duplicates = 0
    for sequences in by_session.values():
        ordered = sorted(sequences)
        for previous, current in zip(ordered, ordered[1:]):
            if current == previous:
                duplicates += 1
            else:
                lost += current - previous - 1
    return lost, duplicates


@dataclass(frozen=True)
class _ReceiptFacts:
    lost_event_count: int
    duplicate_sequence_count: int
    rejected_by_reason: dict[str, int]
    outbound_endpoints: list[str]
    entered_worker_context: bool
    entered_scheduler_inputs: bool
    observer_failure_count: int
    backpressure_drop_count: int
    max_clock_uncertainty_ms: int
    clock_sources: list[str]
    envelopes_by_observer: dict[str, list[ObserverEnvelope]]
    stats_mismatch: bool


def _group_envelopes_by_observer(
    envelopes: Iterable[ObserverEnvelope],
) -> dict[str, list[ObserverEnvelope]]:
    grouped: dict[str, list[ObserverEnvelope]] = defaultdict(list)
    for envelope in envelopes:
        grouped[envelope.observer_id].append(envelope)
    return grouped


def _observer_stats_mismatch(
    reading: LedgerReading,
    envelopes_by_observer: Mapping[str, list[ObserverEnvelope]],
) -> bool:
    for observer_id, observer_envelopes in envelopes_by_observer.items():
        observer_stats = reading.stats.get(observer_id)
        if observer_stats is None:
            continue
        provider_ids = {item.provider_id for item in observer_envelopes}
        if provider_ids != {
            observer_stats.provider_id
        } or observer_stats.accepted_event_count != len(observer_envelopes):
            return True
    return any(
        item.accepted_event_count and observer_id not in envelopes_by_observer
        for observer_id, item in reading.stats.items()
    )


def _collect_receipt_facts(
    reading: LedgerReading,
    envelopes: list[ObserverEnvelope],
    stats: list[ObserverStats],
) -> _ReceiptFacts:
    lost, duplicates = _sequence_accounting(envelopes)
    rejected_by_reason: dict[str, int] = defaultdict(int)
    for item in stats:
        for reason, count in item.rejected_by_reason.items():
            rejected_by_reason[reason] += count
    envelopes_by_observer = _group_envelopes_by_observer(envelopes)
    return _ReceiptFacts(
        lost_event_count=lost,
        duplicate_sequence_count=duplicates,
        rejected_by_reason=dict(rejected_by_reason),
        outbound_endpoints=sorted(
            {endpoint for item in stats for endpoint in item.outbound_endpoints}
        ),
        entered_worker_context=any(
            item.observation_entered_worker_context for item in stats
        ),
        entered_scheduler_inputs=any(
            item.observation_entered_scheduler_inputs for item in stats
        ),
        observer_failure_count=sum(item.observer_failure_count for item in stats),
        backpressure_drop_count=sum(item.backpressure_drop_count for item in stats),
        max_clock_uncertainty_ms=max(
            (item.clock.uncertainty_ms for item in envelopes), default=0
        ),
        clock_sources=sorted(
            {item.clock.source.value for item in envelopes}
            | {item.clock_source.value for item in stats}
        ),
        envelopes_by_observer=envelopes_by_observer,
        stats_mismatch=_observer_stats_mismatch(reading, envelopes_by_observer),
    )


def _receipt_reasons(
    reading: LedgerReading,
    envelopes: list[ObserverEnvelope],
    facts: _ReceiptFacts,
    *,
    clock_uncertainty_degraded_ms: int,
) -> set[ReceiptReason]:
    missing_stats = bool(envelopes) and any(
        observer_id not in reading.stats for observer_id in facts.envelopes_by_observer
    )
    rejected = facts.rejected_by_reason
    candidates = (
        (not envelopes, ReceiptReason.NO_OBSERVATIONS),
        (bool(facts.outbound_endpoints), ReceiptReason.OUTBOUND_ENDPOINT_CONFIGURED),
        (
            facts.entered_worker_context,
            ReceiptReason.OBSERVATION_ENTERED_WORKER_CONTEXT,
        ),
        (
            facts.entered_scheduler_inputs,
            ReceiptReason.OBSERVATION_ENTERED_SCHEDULER_INPUTS,
        ),
        (bool(facts.observer_failure_count), ReceiptReason.OBSERVER_FAILURE),
        (
            bool(rejected.get(EnvelopeRejection.CONTROL_FIELD_REJECTED.value)),
            ReceiptReason.CONTROL_FIELD_REJECTED,
        ),
        (
            bool(rejected.get(EnvelopeRejection.PUBLIC_SAFETY_VIOLATION.value)),
            ReceiptReason.PUBLIC_SAFETY_VIOLATION,
        ),
        (bool(reading.invalid_record_count), ReceiptReason.LEDGER_RECORD_INVALID),
        (missing_stats, ReceiptReason.OBSERVER_STATS_MISSING),
        (facts.stats_mismatch, ReceiptReason.OBSERVER_STATS_MISMATCH),
        (bool(facts.lost_event_count), ReceiptReason.SEQUENCE_GAP),
        (bool(facts.duplicate_sequence_count), ReceiptReason.SEQUENCE_DUPLICATE),
        (bool(facts.backpressure_drop_count), ReceiptReason.BACKPRESSURE_DROP),
        (
            bool(rejected.get(EnvelopeRejection.RAW_MATERIAL_FIELD_REJECTED.value)),
            ReceiptReason.RAW_MATERIAL_REJECTED,
        ),
        (
            bool(rejected.get(EnvelopeRejection.UNSUPPORTED_FIELD_REJECTED.value)),
            ReceiptReason.UNSUPPORTED_FIELD_REJECTED,
        ),
        (
            bool(rejected.get(EnvelopeRejection.IDENTITY_INVALID.value)),
            ReceiptReason.IDENTITY_REJECTED,
        ),
        (
            facts.max_clock_uncertainty_ms > clock_uncertainty_degraded_ms,
            ReceiptReason.CLOCK_UNCERTAINTY_EXCEEDED,
        ),
    )
    return {reason for applies, reason in candidates if applies}


def _receipt_status(reasons: set[ReceiptReason]) -> ReceiptStatus:
    if reasons & _INVALID_REASONS:
        return ReceiptStatus.INVALID
    if reasons & _QUARANTINE_REASONS:
        return ReceiptStatus.QUARANTINED
    if reasons:
        return ReceiptStatus.DEGRADED
    return ReceiptStatus.VALID


def build_integrity_receipt(
    reading: LedgerReading,
    *,
    clock_uncertainty_degraded_ms: int = DEFAULT_CLOCK_UNCERTAINTY_DEGRADED_MS,
) -> dict[str, Any]:
    envelopes = reading.ordered_envelopes
    stats = list(reading.stats.values())
    facts = _collect_receipt_facts(reading, envelopes, stats)
    reasons = _receipt_reasons(
        reading,
        envelopes,
        facts,
        clock_uncertainty_degraded_ms=clock_uncertainty_degraded_ms,
    )
    status = _receipt_status(reasons)

    return {
        "schema_version": INTEGRITY_RECEIPT_SCHEMA_VERSION,
        "capability_id": CAPABILITY_ID,
        "goal_id": reading.goal_id,
        "status": status.value,
        "reason_codes": sorted(reason.value for reason in reasons),
        "provider_ids": sorted(
            {item.provider_id for item in envelopes}
            | {item.provider_id for item in stats}
        ),
        "observer_ids": sorted(set(reading.stats) | set(facts.envelopes_by_observer)),
        "session_count": len({item.session_id for item in envelopes}),
        "observed_event_count": sum(item.observed_event_count for item in stats),
        "accepted_event_count": sum(item.accepted_event_count for item in stats),
        "persisted_event_count": len(envelopes),
        "lost_event_count": facts.lost_event_count,
        "duplicate_sequence_count": facts.duplicate_sequence_count,
        "ledger_invalid_record_count": reading.invalid_record_count,
        "rejected_event_count": sum(facts.rejected_by_reason.values()),
        "rejected_by_reason": dict(sorted(facts.rejected_by_reason.items())),
        "buffer_bound": max((item.buffer_bound for item in stats), default=None),
        "backpressure_drop_count": facts.backpressure_drop_count,
        "observer_failure_count": facts.observer_failure_count,
        "peak_buffered_event_count": max(
            (item.peak_buffered_event_count for item in stats), default=0
        ),
        "flush_attempt_count": sum(item.flush_attempt_count for item in stats),
        "clock": {
            "sources": facts.clock_sources,
            "max_uncertainty_ms": facts.max_clock_uncertainty_ms,
        },
        "outbound_endpoints": facts.outbound_endpoints,
        "observation_entered_worker_context": facts.entered_worker_context,
        "observation_entered_scheduler_inputs": facts.entered_scheduler_inputs,
        "run_identities": [
            item.run_identity.as_dict()
            for item in sorted(stats, key=lambda candidate: candidate.observer_id)
        ],
        "event_sources": sorted(
            {source for item in stats for source in item.event_sources}
        ),
        "source_fields_consumed": sorted(
            {field for item in stats for field in item.source_fields_consumed}
        ),
        "event_kinds_consumed": sorted({item.event_kind.value for item in envelopes}),
        "summary_fields_consumed": sorted(
            {key for item in envelopes for key in item.summary}
        ),
        "observed_from": envelopes[0].observed_at if envelopes else None,
        "observed_until": envelopes[-1].observed_at if envelopes else None,
    }
