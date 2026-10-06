"""Contract tests for the reliability-diagnostics L1 shadow-observer slice.

Expected values come from the design contract (RFC §3.1 and §7.4), not from
observed implementation output: no control path, no raw material, bounded and
counted loss, visible clocks, and a total status enum.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.reliability_diagnostics import (
    CAPABILITY_ID,
    CONTROL_FIELD_FAMILIES,
    DSH_PROVIDER_ID,
    ENVELOPE_FIELDS,
    FIXTURE_GOAL_ID,
    OBSERVER_ENVELOPE_SCHEMA_VERSION,
    OBSERVER_STATS_SCHEMA_VERSION,
    RAW_MATERIAL_FIELD_FAMILIES,
    SOURCE_REF_FIELDS,
    SUMMARY_FIELDS,
    ClockSource,
    DiagnosticSignal,
    DiagnosticStage,
    EnvelopeRejection,
    ObserverEnvelopeError,
    ObserverEventKind,
    ObserverRunIdentity,
    ReceiptReason,
    ReceiptStatus,
    ShadowObserverIntake,
    append_ledger_records,
    build_diagnostic_projection,
    build_integrity_receipt,
    dsh_fixture_records,
    ledger_path,
    ledger_ref,
    normalize_observer_envelope,
    normalize_observer_stats,
    read_ledger,
    read_ledger_records,
    run_dsh_fixture,
)
from loopx.capabilities.reliability_diagnostics.envelope import classify_rejected_field
from loopx.cli_commands.reliability_diagnostics import _ingest

GOAL = "goal-observer"
SESSION = "session-observer"
T0 = "2026-09-01T10:00:00+00:00"
OBSERVER = "observer-1"
RUN_IDENTITY = ObserverRunIdentity(
    worker_id="worker-1",
    model_id="model-1",
    task_id="task-1",
    environment_id="environment-1",
    tools_id="tools-1",
    budget_id="budget-1",
    adapter_revision="adapter-1",
    observer_revision="observer-revision-1",
)
EVENT_SOURCES = ["dsh-agent-hooks", "dsh-session-events"]
SOURCE_FIELDS = [
    "agent.id",
    "event.data",
    "event.seq",
    "event.time",
    "event.type",
    "session.id",
]


def envelope(
    sequence: int,
    kind: ObserverEventKind = ObserverEventKind.STEP_ENDED,
    *,
    observed_at: str = T0,
    summary: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": OBSERVER_ENVELOPE_SCHEMA_VERSION,
        "capability_id": CAPABILITY_ID,
        "provider_id": DSH_PROVIDER_ID,
        "observer_id": OBSERVER,
        "goal_id": GOAL,
        "session_id": SESSION,
        "sequence": sequence,
        "observed_at": observed_at,
        "clock": {"source": ClockSource.HARNESS_EVENT_TIME.value, "uncertainty_ms": 0},
        "event_kind": kind.value,
        "summary": summary or {},
        "source_refs": {"event_seq": str(sequence)},
    }
    record.update(overrides)
    return record


def stats(**overrides: Any) -> dict[str, Any]:
    accepted = overrides.get("accepted_event_count", 1)
    rejected = overrides.get("rejected_event_count", 0)
    dropped = overrides.get("backpressure_drop_count", 0)
    record: dict[str, Any] = {
        "schema_version": OBSERVER_STATS_SCHEMA_VERSION,
        "capability_id": CAPABILITY_ID,
        "provider_id": DSH_PROVIDER_ID,
        "observer_id": OBSERVER,
        "goal_id": GOAL,
        "run_identity": RUN_IDENTITY.as_dict(),
        "event_sources": EVENT_SOURCES,
        "source_fields_consumed": SOURCE_FIELDS,
        "emitted_at": T0,
        "observed_event_count": accepted + rejected + dropped,
        "accepted_event_count": accepted,
        "rejected_event_count": rejected,
        "rejected_by_reason": {},
        "buffer_bound": 8,
        "backpressure_drop_count": 0,
        "observer_failure_count": 0,
        "peak_buffered_event_count": min(accepted, 8),
        "flush_attempt_count": 1,
        "outbound_endpoints": [],
        "observation_entered_worker_context": False,
        "observation_entered_scheduler_inputs": False,
        "clock_source": ClockSource.HARNESS_EVENT_TIME.value,
    }
    record.update(overrides)
    return record


def at(seconds: int) -> str:
    return f"2026-09-01T10:{seconds // 60:02d}:{seconds % 60:02d}+00:00"


def receipt_for(*records: dict[str, Any], malformed: int = 0) -> dict[str, Any]:
    return build_integrity_receipt(
        read_ledger(records, goal_id=GOAL, malformed_line_count=malformed)
    )


# --- envelope schema -------------------------------------------------------


def test_envelope_schema_has_no_control_or_raw_material_fields() -> None:
    flattened = {
        name.replace("_", "")
        for name in ENVELOPE_FIELDS | SUMMARY_FIELDS | SOURCE_REF_FIELDS
    }
    assert not flattened & CONTROL_FIELD_FAMILIES
    assert not flattened & RAW_MATERIAL_FIELD_FAMILIES


def test_valid_envelope_round_trips() -> None:
    record = envelope(
        3,
        ObserverEventKind.TOOL_CALLED,
        summary={"turn": 1, "step": 2, "tool_name": "bash"},
    )
    record["source_refs"]["tool_call_id"] = "call-9"
    normalized = normalize_observer_envelope(record)
    assert normalized.event_kind is ObserverEventKind.TOOL_CALLED
    assert normalized.as_dict() == record


@pytest.mark.parametrize(
    "field",
    [
        "command",
        "send",
        "schedule",
        "retry",
        "stop",
        "resume",
        "gate",
        "toolCall",
        "worker_state",
        "callback",
    ],
)
def test_control_shaped_fields_are_rejected_as_control(field: str) -> None:
    record = envelope(1, **{field: {"kind": "continue"}})
    record["sequence"] = -1  # also malformed: control classification must still win
    with pytest.raises(ObserverEnvelopeError) as excinfo:
        normalize_observer_envelope(record)
    assert excinfo.value.reason is EnvelopeRejection.CONTROL_FIELD_REJECTED


@pytest.mark.parametrize(
    "field", ["transcript", "tool_output", "stdout", "cwd", "messages", "arguments"]
)
def test_raw_material_fields_are_rejected_and_classified(field: str) -> None:
    record = envelope(1, **{field: "protected"})
    with pytest.raises(ObserverEnvelopeError) as excinfo:
        normalize_observer_envelope(record)
    assert excinfo.value.reason is EnvelopeRejection.RAW_MATERIAL_FIELD_REJECTED


def test_raw_material_inside_summary_is_rejected() -> None:
    record = envelope(1, summary={"text": "hello"})
    with pytest.raises(ObserverEnvelopeError) as excinfo:
        normalize_observer_envelope(record)
    assert excinfo.value.reason is EnvelopeRejection.RAW_MATERIAL_FIELD_REJECTED


def test_unknown_field_is_rejected_as_unsupported() -> None:
    record = envelope(1, colour="blue")
    with pytest.raises(ObserverEnvelopeError) as excinfo:
        normalize_observer_envelope(record)
    assert excinfo.value.reason is EnvelopeRejection.UNSUPPORTED_FIELD_REJECTED
    assert (
        classify_rejected_field("agentSend")
        is EnvelopeRejection.UNSUPPORTED_FIELD_REJECTED
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"schema_version": "other_v9"}, EnvelopeRejection.SCHEMA_MISMATCH),
        ({"capability_id": "session-runtime"}, EnvelopeRejection.SCHEMA_MISMATCH),
        ({"goal_id": "goal with spaces"}, EnvelopeRejection.IDENTITY_INVALID),
        ({"sequence": True}, EnvelopeRejection.SEQUENCE_INVALID),
        ({"sequence": -4}, EnvelopeRejection.SEQUENCE_INVALID),
        ({"observed_at": "2026-09-01T10:00:00"}, EnvelopeRejection.CLOCK_INVALID),
        (
            {"clock": {"source": "gps", "uncertainty_ms": 0}},
            EnvelopeRejection.CLOCK_INVALID,
        ),
        (
            {"clock": {"source": "fixture", "uncertainty_ms": -1}},
            EnvelopeRejection.CLOCK_INVALID,
        ),
        ({"event_kind": "prompt_injected"}, EnvelopeRejection.EVENT_KIND_INVALID),
        (
            {"summary": {"tool_name": "bash -c 'rm -rf'"}},
            EnvelopeRejection.SUMMARY_INVALID,
        ),
        ({"summary": {"turn": "one"}}, EnvelopeRejection.SUMMARY_INVALID),
        (
            {"source_refs": {"tool_call_id": "/Users/someone/call"}},
            EnvelopeRejection.SOURCE_REF_INVALID,
        ),
        (
            {"source_refs": {"tool_call_id": "sk-abcdefghijklmnop0123"}},
            EnvelopeRejection.PUBLIC_SAFETY_VIOLATION,
        ),
    ],
)
def test_malformed_envelopes_carry_typed_reasons(
    mutation: dict[str, Any], reason: EnvelopeRejection
) -> None:
    record = envelope(1)
    record.update(mutation)
    with pytest.raises(ObserverEnvelopeError) as excinfo:
        normalize_observer_envelope(record)
    assert excinfo.value.reason is reason


# --- intake ----------------------------------------------------------------


def intake(bound: int = 8) -> ShadowObserverIntake:
    return ShadowObserverIntake(
        provider_id=DSH_PROVIDER_ID,
        observer_id="observer-1",
        goal_id=GOAL,
        clock_source=ClockSource.FIXTURE,
        run_identity=RUN_IDENTITY,
        event_sources=tuple(EVENT_SOURCES),
        source_fields_consumed=tuple(SOURCE_FIELDS),
        buffer_bound=bound,
    )


def test_intake_exposes_no_control_surface() -> None:
    names = {name.lower() for name in dir(ShadowObserverIntake)}
    assert not names & {
        "send",
        "command",
        "schedule",
        "retry",
        "stop",
        "resume",
        "pause",
        "inject",
    }


def test_intake_bounds_buffer_and_counts_drops_without_raising() -> None:
    sink = intake(bound=2)
    accepted = [sink.observe(envelope(index)) for index in range(5)]
    assert accepted == [True, True, False, False, False]
    record = sink.stats(emitted_at=T0).as_dict()
    assert record["buffer_bound"] == 2
    assert record["accepted_event_count"] == 2
    assert record["backpressure_drop_count"] == 3
    assert record["outbound_endpoints"] == []
    assert record["observation_entered_worker_context"] is False
    assert record["observation_entered_scheduler_inputs"] is False
    assert record["observed_event_count"] == (
        record["accepted_event_count"]
        + record["rejected_event_count"]
        + record["backpressure_drop_count"]
    )
    assert normalize_observer_stats(record).backpressure_drop_count == 3


def test_intake_isolates_observer_crashes() -> None:
    class Exploding(dict):
        def __iter__(self):  # noqa: ANN204
            raise RuntimeError("observer bug")

    sink = intake()
    assert sink.observe(Exploding()) is False
    assert sink.observe(envelope(0)) is True
    assert sink.stats(emitted_at=T0).observer_failure_count == 1


def test_intake_flush_failure_is_counted_not_raised() -> None:
    sink = intake()
    sink.observe(envelope(0))
    sink.observe(envelope(1))

    def broken(_: list[dict[str, Any]]) -> None:
        raise OSError("disk full")

    assert sink.flush(broken, emitted_at=T0) == []
    record = sink.stats(emitted_at=T0)
    assert record.observer_failure_count == 1
    assert record.backpressure_drop_count == 2
    assert sink.buffered_count == 0


def test_intake_rejects_and_counts_by_reason() -> None:
    sink = intake()
    sink.observe(envelope(0, transcript="x"))
    sink.observe(envelope(1, command="stop"))
    sink.observe(envelope(2, goal_id="other-goal"))
    assert sink.stats(emitted_at=T0).rejected_by_reason == {
        EnvelopeRejection.RAW_MATERIAL_FIELD_REJECTED.value: 1,
        EnvelopeRejection.CONTROL_FIELD_REJECTED.value: 1,
        EnvelopeRejection.IDENTITY_INVALID.value: 1,
    }


def test_stats_record_rejects_unknown_fields() -> None:
    record = stats(send_path="agent.send")
    with pytest.raises(ValueError, match="unsupported fields"):
        normalize_observer_stats(record)


@pytest.mark.parametrize(
    "mutation",
    [
        {"emitted_at": "not-a-time"},
        {"emitted_at": "2026-09-01T10:00:00"},
        {"accepted_event_count": 999, "observed_event_count": 1},
        {"run_identity": {"worker_id": "worker-1"}},
        {"event_sources": []},
        {"source_fields_consumed": ["event.type", "event.type"]},
        {"peak_buffered_event_count": 9},
    ],
)
def test_stats_record_rejects_inconsistent_or_incomplete_evidence(
    mutation: dict[str, Any],
) -> None:
    record = stats(**mutation)
    with pytest.raises(ValueError):
        normalize_observer_stats(record)


# --- receipt ---------------------------------------------------------------


def test_receipt_without_observations_is_invalid() -> None:
    receipt = receipt_for()
    assert receipt["status"] == ReceiptStatus.INVALID.value
    assert receipt["reason_codes"] == [ReceiptReason.NO_OBSERVATIONS.value]


def test_receipt_is_valid_for_contiguous_stream_with_stats() -> None:
    receipt = receipt_for(
        envelope(0),
        envelope(1, observed_at=at(1)),
        envelope(2, observed_at=at(2)),
        stats(accepted_event_count=3),
    )
    assert receipt["status"] == ReceiptStatus.VALID.value
    assert receipt["reason_codes"] == []
    assert receipt["outbound_endpoints"] == []
    assert receipt["lost_event_count"] == 0
    assert receipt["observed_event_count"] == 3
    assert receipt["provider_ids"] == [DSH_PROVIDER_ID]


def test_receipt_counts_sequence_gaps_as_lost_events() -> None:
    receipt = receipt_for(
        envelope(0),
        envelope(4, observed_at=at(1)),
        envelope(5, observed_at=at(2)),
        stats(accepted_event_count=3),
    )
    assert receipt["lost_event_count"] == 3
    assert receipt["status"] == ReceiptStatus.DEGRADED.value
    assert receipt["reason_codes"] == [ReceiptReason.SEQUENCE_GAP.value]


def test_receipt_counts_duplicate_sequences_separately() -> None:
    receipt = receipt_for(
        envelope(0),
        envelope(0, observed_at=at(1)),
        stats(accepted_event_count=2),
    )
    assert receipt["duplicate_sequence_count"] == 1
    assert receipt["lost_event_count"] == 0
    assert ReceiptReason.SEQUENCE_DUPLICATE.value in receipt["reason_codes"]


@pytest.mark.parametrize(
    ("stats_override", "reason"),
    [
        ({"observer_failure_count": 1}, ReceiptReason.OBSERVER_FAILURE),
        (
            {
                "rejected_event_count": 1,
                "rejected_by_reason": {"control_field_rejected": 1},
            },
            ReceiptReason.CONTROL_FIELD_REJECTED,
        ),
    ],
)
def test_receipt_quarantines_observer_failure_and_control_fields(
    stats_override: dict[str, Any], reason: ReceiptReason
) -> None:
    receipt = receipt_for(envelope(0), stats(**stats_override))
    assert receipt["status"] == ReceiptStatus.QUARANTINED.value
    assert receipt["reason_codes"] == [reason.value]


def test_receipt_invalidates_malformed_ledger_records() -> None:
    receipt = receipt_for(
        envelope(0), stats(), {"schema_version": "unknown"}, malformed=1
    )
    assert receipt["ledger_invalid_record_count"] == 2
    assert receipt["status"] == ReceiptStatus.INVALID.value


@pytest.mark.parametrize(
    ("stats_override", "reason"),
    [
        (
            {"outbound_endpoints": ["loopx-continuation"]},
            ReceiptReason.OUTBOUND_ENDPOINT_CONFIGURED,
        ),
        (
            {"observation_entered_worker_context": True},
            ReceiptReason.OBSERVATION_ENTERED_WORKER_CONTEXT,
        ),
    ],
)
def test_receipt_invalidates_any_outbound_or_worker_context_path(
    stats_override: dict[str, Any], reason: ReceiptReason
) -> None:
    receipt = receipt_for(
        envelope(0), stats(observer_failure_count=1, **stats_override)
    )
    assert (
        receipt["status"] == ReceiptStatus.INVALID.value
    )  # invalid outranks quarantined
    assert reason.value in receipt["reason_codes"]
    assert ReceiptReason.OBSERVER_FAILURE.value in receipt["reason_codes"]


def test_receipt_quarantines_a_trailing_public_safety_rejection() -> None:
    receipt = receipt_for(
        envelope(0),
        stats(
            rejected_event_count=1,
            rejected_by_reason={EnvelopeRejection.PUBLIC_SAFETY_VIOLATION.value: 1},
        ),
    )
    assert receipt["status"] == ReceiptStatus.QUARANTINED.value
    assert receipt["reason_codes"] == [ReceiptReason.PUBLIC_SAFETY_VIOLATION.value]


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        (
            [
                envelope(
                    0, clock={"source": "observer_wall_clock", "uncertainty_ms": 1001}
                ),
                stats(),
            ],
            ReceiptReason.CLOCK_UNCERTAINTY_EXCEEDED,
        ),
        (
            [envelope(0), stats(backpressure_drop_count=2)],
            ReceiptReason.BACKPRESSURE_DROP,
        ),
        (
            [
                envelope(0),
                stats(
                    rejected_event_count=1,
                    rejected_by_reason={"raw_material_field_rejected": 1},
                ),
            ],
            ReceiptReason.RAW_MATERIAL_REJECTED,
        ),
    ],
)
def test_receipt_degrades_but_keeps_evidence(
    records: list[dict[str, Any]], reason: ReceiptReason
) -> None:
    receipt = receipt_for(*records)
    assert receipt["status"] == ReceiptStatus.DEGRADED.value
    assert receipt["reason_codes"] == [reason.value]


def test_receipt_without_stats_is_invalid() -> None:
    receipt = receipt_for(envelope(0))
    assert receipt["status"] == ReceiptStatus.INVALID.value
    assert receipt["reason_codes"] == [ReceiptReason.OBSERVER_STATS_MISSING.value]


def test_receipt_invalidates_unlinked_stats_and_identity_rejection() -> None:
    unlinked = receipt_for(envelope(0), stats(provider_id="other-provider"))
    assert unlinked["status"] == ReceiptStatus.INVALID.value
    assert ReceiptReason.OBSERVER_STATS_MISMATCH.value in unlinked["reason_codes"]

    rejected = receipt_for(
        envelope(0),
        stats(
            rejected_event_count=1,
            rejected_by_reason={EnvelopeRejection.IDENTITY_INVALID.value: 1},
        ),
    )
    assert rejected["status"] == ReceiptStatus.INVALID.value
    assert ReceiptReason.IDENTITY_REJECTED.value in rejected["reason_codes"]


def test_receipt_clock_uncertainty_at_threshold_is_visible_not_degraded() -> None:
    receipt = receipt_for(
        envelope(0, clock={"source": "observer_wall_clock", "uncertainty_ms": 1000}),
        stats(),
    )
    assert receipt["clock"] == {
        "sources": ["harness_event_time", "observer_wall_clock"],
        "max_uncertainty_ms": 1000,
    }
    assert receipt["status"] == ReceiptStatus.VALID.value


def test_receipt_sums_latest_stats_per_observer_instance() -> None:
    receipt = receipt_for(
        envelope(0, observer_id="a"),
        envelope(0, observer_id="b", session_id="session-b"),
        stats(observer_id="a", backpressure_drop_count=1),
        stats(observer_id="a", backpressure_drop_count=4),
        stats(observer_id="b", backpressure_drop_count=2),
    )
    assert receipt["backpressure_drop_count"] == 6
    assert receipt["observer_ids"] == ["a", "b"]


# --- projection ------------------------------------------------------------


def projection_for(*records: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return build_diagnostic_projection(read_ledger(records, goal_id=GOAL), **kwargs)


@pytest.mark.parametrize("first,last,gap_ms", [
    ("2026-09-01T12:00:00+02:00", "2026-09-01T10:01:00Z", 60_000),
    ("2026-09-02T00:00:00+14:00", "2026-09-01T10:01:00Z", 60_000),
    ("2026-09-01T10:00:00Z", "2026-09-01T10:00:00.500Z", 500),
    ("2026-09-01T10:00:00Z", "2026-09-01T10:00:00.000+00:00", 0),
    ("2026-09-01T10:00:00+00:00", "2026-09-01T10:01:00+00:00", 60_000),
])
def test_diagnostics_order_instants_without_rewriting_timestamps(
    first: str, last: str, gap_ms: int,
) -> None:
    # UTC instants, then session/sequence, determine order, not text or append order.
    records = [
        envelope(1, ObserverEventKind.TURN_ENDED, observed_at=last),
        envelope(0, ObserverEventKind.AGENT_ERROR, observed_at=first),
        stats(accepted_event_count=2),
    ]
    reading = read_ledger(records, goal_id=GOAL)
    assert [item.sequence for item in reading.ordered_envelopes] == [0, 1]
    receipt = build_integrity_receipt(reading)
    assert receipt["status"] == "valid"
    assert receipt["observed_from"] == first
    assert receipt["observed_until"] == last
    projection = build_diagnostic_projection(reading, as_of="2026-09-01T10:06:00Z")
    assert projection["stage"] == "idle"
    assert projection["recovery"] == {
        "error_count": 1, "recovered_error_count": 1, "unrecovered_error_count": 0,
    }
    assert projection["stall"]["max_inter_event_gap_ms"] == gap_ms
    assert projection["stall"]["last_event_age_ms"] == 360_000 - gap_ms
    assert projection["signals"] == []


def test_diagnostics_equal_instants_keep_session_then_sequence_order() -> None:
    reading = read_ledger([
        envelope(0, observed_at="2026-09-01T10:00:00+00:00", session_id="session-b"),
        envelope(1, observed_at="2026-09-01T10:00:00.000Z", session_id="session-a"),
        envelope(0, observed_at="2026-09-01T12:00:00+02:00", session_id="session-a"),
    ], goal_id=GOAL)
    assert [(item.session_id, item.sequence) for item in reading.ordered_envelopes] == [
        ("session-a", 0), ("session-a", 1), ("session-b", 0),
    ]


@pytest.mark.parametrize("earlier,later", [
    ("2026-09-01T10:00:00.0000001Z", "2026-09-01T10:00:00.0000009Z"),
    ("20260901T100000,0000001Z", "2026-09-01T12:00:00.0000009+02:00"),
    ("2026-09-01T10:00:00+00:00:01.0000009", "2026-09-01T10:00:00+00:00:01.0000001"),
    ("2026-09-01T10:00:00-00:00:01.0000001", "2026-09-01T10:00:00-00:00:01.0000009"),
    ("2026-09-01T10:00:00+00:00:00.5", "2026-09-01T09:59:59.6Z"),
    ("2026-09-01T09:59:59.4Z", "2026-09-01T10:00:00+00:00:00.5"),
])
def test_diagnostics_preserves_accepted_fractional_precision(earlier: str, later: str) -> None:
    reading = read_ledger([
        envelope(0, ObserverEventKind.AGENT_ERROR, observed_at=later),
        envelope(1, ObserverEventKind.TURN_ENDED, observed_at=earlier),
        stats(accepted_event_count=2),
    ], goal_id=GOAL)
    assert reading.invalid_record_count == 0
    assert [item.sequence for item in reading.ordered_envelopes] == [1, 0]
    receipt = build_integrity_receipt(reading)
    assert (receipt["observed_from"], receipt["observed_until"]) == (earlier, later)
    projection = build_diagnostic_projection(reading)
    assert projection["stage"] == "errored"
    assert projection["recovery"]["unrecovered_error_count"] == 1
    assert projection["recovery"]["recovered_error_count"] == 0


@pytest.mark.parametrize("first,last,elapsed_ms", [
    ("2026-09-01T10:00:00.0000009Z", "2026-09-01T10:00:00.0010001Z", 0),
    ("2026-09-01T10:00:00+00:00:00.5", "2026-09-01T10:00:00Z", 500),
    ("2026-09-01T10:00:00Z", "2026-09-01T10:00:00-00:00:00.5", 500),
    ("0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z", 315_537_897_599_999),
])
def test_diagnostic_age_and_gap_use_the_same_instant_precision(
    first: str, last: str, elapsed_ms: int,
) -> None:
    projection = projection_for(
        envelope(0, ObserverEventKind.STEP_STARTED, observed_at=first),
        stats(), as_of=last, stall_threshold_ms=1,
    )
    assert projection["stall"]["last_event_age_ms"] == elapsed_ms
    assert projection["stall"]["detected"] is (elapsed_ms >= 1)
    interval = projection_for(
        envelope(0, observed_at=first), envelope(1, observed_at=last),
        stats(accepted_event_count=2),
    )
    assert interval["stall"]["max_inter_event_gap_ms"] == elapsed_ms


def test_cli_diagnostics_replays_mixed_offsets_without_mutating_ledger(tmp_path: Path) -> None:
    first, recovered, last = (
        "2026-09-01T12:00:00+02:00", "2026-09-01T10:01:00Z", "2026-09-01T10:02:00Z",
    )
    records = [
        envelope(0, ObserverEventKind.AGENT_ERROR, observed_at=first),
        envelope(1, ObserverEventKind.STEP_ENDED, observed_at=recovered),
        envelope(2, ObserverEventKind.TURN_ENDED, observed_at=last),
        stats(accepted_event_count=3),
    ]
    command = [
        sys.executable, "-m", "loopx.cli", "--registry", str(tmp_path / "registry.json"),
        "--runtime-root", str(tmp_path), "--format", "json", "reliability-diagnostics",
    ]

    def run(*args: str, source: str | None = None) -> dict[str, Any]:
        result = subprocess.run(
            [*command, *args, "--goal-id", GOAL], input=source,
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
        )
        return json.loads(result.stdout)

    ingest = run("ingest", "--input", "-", source="\n".join(map(json.dumps, records)))
    assert ingest["accepted_envelope_count"] == 3
    assert ingest["rejected_event_count"] == 0
    path = tmp_path / ingest["ledger_ref"]
    before = path.read_bytes()
    combined = run("status", "--with-receipt", "--as-of", "2026-09-01T10:10:00Z")
    receipt = run("receipt")["receipt"]
    assert receipt == combined["receipt"]
    assert receipt["status"] == "valid"
    assert (receipt["observed_from"], receipt["observed_until"]) == (first, last)
    projection = combined["projection"]
    assert projection["stage"] == "idle"
    assert projection["recovery"]["recovered_error_count"] == 1
    assert projection["recovery"]["unrecovered_error_count"] == 0
    assert projection["stall"]["last_event_age_ms"] == 480_000
    assert projection["signals"] == []
    assert projection["authority"] == "none"
    assert path.read_bytes() == before


def test_projection_declares_read_only_boundary() -> None:
    projection = projection_for(envelope(0), stats())
    assert projection["mode"] == "read_only"
    assert projection["authority"] == "none"
    assert projection["write_scope"] == "diagnostic_ledger_only"
    assert projection["worker_influence"] == "none"
    assert (
        set(projection) & {"command", "next_action", "recommended_action", "gate"}
        == set()
    )


def test_projection_stall_requires_active_stage_and_silence() -> None:
    running = projection_for(
        envelope(0, ObserverEventKind.TURN_STARTED),
        envelope(1, ObserverEventKind.STEP_STARTED, observed_at=at(1)),
        stats(accepted_event_count=2),
        as_of=at(6 * 60),
        stall_threshold_ms=300_000,
    )
    assert running["stage"] == DiagnosticStage.RUNNING.value
    assert running["stall"]["detected"] is True
    assert DiagnosticSignal.STALL_SUSPECTED.value in running["signals"]

    idle = projection_for(
        envelope(0, ObserverEventKind.TURN_STARTED),
        envelope(
            1,
            ObserverEventKind.TURN_ENDED,
            observed_at=at(1),
            summary={"reason": "completed"},
        ),
        stats(accepted_event_count=2),
        as_of=at(6 * 60),
    )
    assert idle["stage"] == DiagnosticStage.IDLE.value
    assert idle["stall"]["detected"] is False


def test_projection_repetition_counts_consecutive_identical_tool_runs() -> None:
    tools = ["read", "read", "bash", "read", "read", "read"]
    records = [
        envelope(
            index,
            ObserverEventKind.TOOL_CALLED,
            observed_at=at(index),
            summary={"tool_name": tool},
        )
        for index, tool in enumerate(tools)
    ]
    projection = projection_for(*records, stats(accepted_event_count=len(records)))
    assert projection["repetition"] == {
        "detected": True,
        "threshold": 3,
        "longest_tool_run": 3,
        "tool_name": "read",
    }
    below = projection_for(*records[:2], stats(accepted_event_count=2))
    assert below["repetition"]["detected"] is False


def test_projection_recovery_and_stage_transitions() -> None:
    unrecovered = projection_for(
        envelope(0, ObserverEventKind.STEP_STARTED),
        envelope(
            1,
            ObserverEventKind.AGENT_ERROR,
            observed_at=at(1),
            summary={"error_class": "Timeout"},
        ),
        stats(accepted_event_count=2),
    )
    assert unrecovered["stage"] == DiagnosticStage.ERRORED.value
    assert unrecovered["recovery"] == {
        "error_count": 1,
        "recovered_error_count": 0,
        "unrecovered_error_count": 1,
    }
    assert DiagnosticSignal.UNRECOVERED_ERROR.value in unrecovered["signals"]

    recovered = projection_for(
        envelope(0, ObserverEventKind.STEP_STARTED),
        envelope(1, ObserverEventKind.AGENT_ERROR, observed_at=at(1)),
        envelope(2, ObserverEventKind.STEP_ENDED, observed_at=at(2)),
        envelope(3, ObserverEventKind.SESSION_DISPOSED, observed_at=at(3)),
        stats(accepted_event_count=4),
    )
    assert recovered["stage"] == DiagnosticStage.DISPOSED.value
    assert recovered["recovery"]["unrecovered_error_count"] == 0
    assert recovered["recovery"]["recovered_error_count"] == 1
    assert recovered["signals"] == []

    terminal_error = projection_for(
        envelope(
            0,
            ObserverEventKind.TURN_ENDED,
            summary={"turn": 1, "reason": "error"},
        ),
        stats(),
    )
    assert terminal_error["stage"] == DiagnosticStage.ERRORED.value
    assert terminal_error["recovery"] == {
        "error_count": 1,
        "recovered_error_count": 0,
        "unrecovered_error_count": 1,
    }
    assert DiagnosticSignal.UNRECOVERED_ERROR.value in terminal_error["signals"]


def test_projection_surfaces_event_loss_and_integrity() -> None:
    projection = projection_for(
        envelope(0),
        envelope(3, observed_at=at(1)),
        stats(accepted_event_count=2),
    )
    assert DiagnosticSignal.EVENT_LOSS.value in projection["signals"]
    assert projection["integrity"]["status"] == ReceiptStatus.DEGRADED.value
    assert projection["evidence"]["lost_event_count"] == 2


# --- ledger ----------------------------------------------------------------


def test_ledger_ref_is_relative_and_goal_scoped(tmp_path: Path) -> None:
    ref = ledger_ref("goal:alpha")
    assert re.fullmatch(r"reliability_diagnostics/by-goal/[0-9a-f]{64}\.ndjson", ref)
    assert ledger_path(tmp_path, "goal:alpha") == tmp_path / ref
    with pytest.raises(ValueError):
        ledger_ref("../escape")


def test_ledger_append_is_line_oriented_and_tolerates_malformed_lines(
    tmp_path: Path,
) -> None:
    path = ledger_path(tmp_path, GOAL)
    assert append_ledger_records(path, [envelope(0)]) == 1
    assert append_ledger_records(path, []) == 0
    path.open("a", encoding="utf-8").write("not json\n")
    assert append_ledger_records(path, [stats()]) == 1
    records, malformed = read_ledger_records(path)
    assert [json.dumps(item, sort_keys=True) for item in records] == [
        json.dumps(envelope(0), sort_keys=True),
        json.dumps(stats(), sort_keys=True),
    ]
    assert malformed == 1


def test_ingest_persists_a_durable_gate_for_refused_input(tmp_path: Path) -> None:
    source = tmp_path / "observer.ndjson"
    source.write_text(
        "\n".join(
            [
                json.dumps(envelope(0)),
                "not json",
                json.dumps(envelope(1, goal_id="other-goal")),
                json.dumps(stats()),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    path = ledger_path(tmp_path, GOAL)
    result = _ingest(path, GOAL, str(source))

    assert result["accepted_envelope_count"] == 1
    assert result["passthrough_stats_count"] == 1
    assert result["malformed_line_count"] == 1
    assert result["rejected_by_reason"] == {
        EnvelopeRejection.IDENTITY_INVALID.value: 1,
        EnvelopeRejection.SCHEMA_MISMATCH.value: 1,
    }
    assert result["ingest_gate_recorded"] is True
    records, malformed = read_ledger_records(path)
    assert malformed == 0
    assert records[-1]["schema_version"] == "reliability_ingest_violation_v0"
    receipt = build_integrity_receipt(read_ledger(records, goal_id=GOAL))
    assert receipt["status"] == ReceiptStatus.INVALID.value
    assert ReceiptReason.LEDGER_RECORD_INVALID.value in receipt["reason_codes"]


# --- fixture ---------------------------------------------------------------


def test_dsh_fixture_exercises_every_contract_hazard_and_stays_degraded() -> None:
    result = run_dsh_fixture()
    receipt = result["receipt"]
    assert receipt["goal_id"] == FIXTURE_GOAL_ID
    assert receipt["status"] == ReceiptStatus.DEGRADED.value
    assert set(receipt["reason_codes"]) == {
        ReceiptReason.SEQUENCE_GAP.value,
        ReceiptReason.BACKPRESSURE_DROP.value,
        ReceiptReason.RAW_MATERIAL_REJECTED.value,
        ReceiptReason.CLOCK_UNCERTAINTY_EXCEEDED.value,
    }
    assert receipt["outbound_endpoints"] == []
    assert receipt["observer_failure_count"] == 0
    assert "transcript" not in json.dumps(result["ledger_records"])
    assert len(dsh_fixture_records()) == receipt["observed_event_count"]
    assert receipt["observed_event_count"] == (
        receipt["accepted_event_count"]
        + receipt["rejected_event_count"]
        + receipt["backpressure_drop_count"]
    )
