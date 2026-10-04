"""Owner-local CLI readback for the reliability-diagnostics ledger."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..capabilities.reliability_diagnostics import (
    CAPABILITY_ID,
    OBSERVER_ENVELOPE_SCHEMA_VERSION,
    OBSERVER_STATS_SCHEMA_VERSION,
    EnvelopeRejection,
    ObserverEnvelopeError,
    append_ledger_records,
    build_diagnostic_projection,
    build_integrity_receipt,
    ledger_ref,
    normalize_observer_envelope,
    normalize_observer_stats,
    parse_ndjson_lines,
    read_ledger,
    read_ledger_records,
)
from ..capabilities.reliability_diagnostics.ledger import resolve_ledger_path
from ..history import load_registry
from ..paths import resolve_runtime_root

PrintPayload = Callable[
    [dict[str, Any], str, Callable[[dict[str, Any]], str]], str | None
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]

INGEST_VIOLATION_SCHEMA_VERSION = "reliability_ingest_violation_v0"


@dataclass(frozen=True)
class _AcceptedIngestRecord:
    value: dict[str, Any]
    kind: str


class _IngestRecordRejected(ValueError):
    def __init__(self, reason: EnvelopeRejection) -> None:
        super().__init__(reason.value)
        self.reason = reason


def register_reliability_diagnostics_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "reliability-diagnostics",
        help="Read back the L1 shadow-observer ledger: ingest, receipt, status.",
    )
    commands = parser.add_subparsers(
        dest="reliability_diagnostics_command", required=True
    )
    ingest = commands.add_parser(
        "ingest",
        help="Validate observer envelopes and append accepted ones to the goal ledger.",
    )
    add_subcommand_format(ingest)
    ingest.add_argument("--goal-id", required=True)
    ingest.add_argument(
        "--input", required=True, help="NDJSON file path, or - for stdin."
    )
    receipt = commands.add_parser(
        "receipt", help="Render the treatment-integrity receipt."
    )
    add_subcommand_format(receipt)
    receipt.add_argument("--goal-id", required=True)
    status = commands.add_parser(
        "status", help="Render the read-only diagnostic projection."
    )
    add_subcommand_format(status)
    status.add_argument("--goal-id", required=True)
    status.add_argument(
        "--as-of", help="Timezone-aware ISO-8601 time used for stall age."
    )
    status.add_argument(
        "--with-receipt",
        action="store_true",
        help="Include integrity evidence from the same ledger read; grants no control authority.",
    )


def _render(payload: dict[str, Any]) -> str:
    lines = [f"# Reliability Diagnostics ({payload.get('command')})", ""]
    for key in ("goal_id", "ledger_ref", "appended_record_count", "rejected_by_reason"):
        if key in payload:
            lines.append(f"- {key}: `{payload[key]}`")
    for section in ("receipt", "projection"):
        body = payload.get(section)
        if isinstance(body, dict):
            lines.append(
                f"- {section}.status: `{body.get('status') or body.get('integrity', {}).get('status')}`"
            )
            for key in (
                "stage",
                "signals",
                "reason_codes",
                "lost_event_count",
                "backpressure_drop_count",
            ):
                if key in body:
                    lines.append(f"- {section}.{key}: `{body[key]}`")
    return "\n".join(lines) + "\n"


def _normalize_stats_for_ingest(
    record: dict[str, Any],
    goal_id: str,
) -> _AcceptedIngestRecord:
    try:
        normalized = normalize_observer_stats(record)
    except ValueError as exc:
        raise _IngestRecordRejected(EnvelopeRejection.SCHEMA_MISMATCH) from exc
    if normalized.goal_id != goal_id:
        raise _IngestRecordRejected(EnvelopeRejection.IDENTITY_INVALID)
    return _AcceptedIngestRecord(normalized.as_dict(), "stats")


def _normalize_envelope_for_ingest(
    record: dict[str, Any],
    goal_id: str,
) -> _AcceptedIngestRecord:
    try:
        normalized = normalize_observer_envelope(record)
    except ObserverEnvelopeError as exc:
        raise _IngestRecordRejected(exc.reason) from exc
    if normalized.goal_id != goal_id:
        raise _IngestRecordRejected(EnvelopeRejection.IDENTITY_INVALID)
    return _AcceptedIngestRecord(normalized.as_dict(), "envelope")


def _normalize_ingest_record(record: Any, goal_id: str) -> _AcceptedIngestRecord:
    if not isinstance(record, dict):
        raise _IngestRecordRejected(EnvelopeRejection.SCHEMA_MISMATCH)
    schema = record.get("schema_version")
    if schema == OBSERVER_STATS_SCHEMA_VERSION:
        return _normalize_stats_for_ingest(record, goal_id)
    if schema == OBSERVER_ENVELOPE_SCHEMA_VERSION:
        return _normalize_envelope_for_ingest(record, goal_id)
    raise _IngestRecordRejected(EnvelopeRejection.SCHEMA_MISMATCH)


def _ingest(path: Path, goal_id: str, source: str) -> dict[str, Any]:
    if source == "-":
        lines = sys.stdin.read().splitlines()
    else:
        lines = Path(source).expanduser().read_text(encoding="utf-8").splitlines()
    parsed, malformed = parse_ndjson_lines(lines)
    accepted_records: list[dict[str, Any]] = []
    accepted_by_kind = {"envelope": 0, "stats": 0}
    rejected_by_reason: dict[str, int] = {}

    def reject(reason: EnvelopeRejection) -> None:
        rejected_by_reason[reason.value] = rejected_by_reason.get(reason.value, 0) + 1

    for record in parsed:
        try:
            accepted = _normalize_ingest_record(record, goal_id)
        except _IngestRecordRejected as exc:
            reject(exc.reason)
            continue
        accepted_records.append(accepted.value)
        accepted_by_kind[accepted.kind] += 1

    if malformed:
        rejected_by_reason[EnvelopeRejection.SCHEMA_MISMATCH.value] = (
            rejected_by_reason.get(EnvelopeRejection.SCHEMA_MISMATCH.value, 0)
            + malformed
        )
    appended = append_ledger_records(path, accepted_records)
    rejected_event_count = sum(rejected_by_reason.values())
    gate_recorded = rejected_event_count > 0
    if gate_recorded:
        # The gate record is intentionally not an observer schema. The ledger
        # reader retains it as a durable invalid-record reason instead of
        # silently forgetting input that the ingest boundary refused.
        appended += append_ledger_records(
            path,
            [
                {
                    "schema_version": INGEST_VIOLATION_SCHEMA_VERSION,
                    "capability_id": CAPABILITY_ID,
                    "goal_id": goal_id,
                    "emitted_at": _now(),
                    "malformed_line_count": malformed,
                    "rejected_event_count": rejected_event_count,
                    "rejected_by_reason": dict(sorted(rejected_by_reason.items())),
                }
            ],
        )
    return {
        "ok": True,
        "command": "ingest",
        "goal_id": goal_id,
        "ledger_ref": ledger_ref(goal_id),
        "appended_record_count": appended,
        "accepted_envelope_count": accepted_by_kind["envelope"],
        "passthrough_stats_count": accepted_by_kind["stats"],
        "rejected_event_count": rejected_event_count,
        "rejected_by_reason": dict(sorted(rejected_by_reason.items())),
        "malformed_line_count": malformed,
        "observer_failure_count": 0,
        "ingest_gate_recorded": gate_recorded,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def handle_reliability_diagnostics_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "reliability-diagnostics":
        return None
    goal_id = str(args.goal_id)
    runtime_root = resolve_runtime_root(
        load_registry(registry_path) if registry_path.is_file() else {},
        runtime_root_arg,
        registry_path=registry_path,
    )
    try:
        command = args.reliability_diagnostics_command
        path = resolve_ledger_path(runtime_root, goal_id, for_write=command == "ingest")
        if command == "ingest":
            payload = _ingest(path, goal_id, str(args.input))
        else:
            records, malformed = read_ledger_records(path)
            reading = read_ledger(
                records, goal_id=goal_id, malformed_line_count=malformed
            )
            payload = {
                "ok": True,
                "command": command,
                "goal_id": goal_id,
                "ledger_ref": path.relative_to(runtime_root.expanduser()).as_posix(),
            }
            if command == "receipt":
                payload["receipt"] = build_integrity_receipt(reading)
            else:
                payload["projection"] = build_diagnostic_projection(
                    reading, as_of=args.as_of
                )
                if args.with_receipt:
                    payload["receipt"] = build_integrity_receipt(reading)
    except (OSError, ValueError) as exc:
        print(f"error: {CAPABILITY_ID}: {exc}", file=sys.stderr)
        return 2
    print_payload(payload, output_format(args), _render)
    return 0
