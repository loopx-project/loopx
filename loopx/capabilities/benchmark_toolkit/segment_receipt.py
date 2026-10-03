"""Fail-closed identity and freshness checks for benchmark agent segments."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any

BENCHMARK_SEGMENT_RECEIPT_SCHEMA_VERSION = "benchmark_segment_receipt_v0"
_NONCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{15,127}\Z")


class BenchmarkSegmentReceiptClassification(str, Enum):
    """Why one agent-segment receipt is or is not attributable."""

    INPUT_INVALID = "segment_receipt_input_invalid"
    QUALIFIED = "qualified"
    IDENTITY_MISMATCH = "segment_identity_mismatch"
    OUTSIDE_SEGMENT_WINDOW = "receipt_outside_segment_window"
    REPLAYED = "segment_receipt_replayed"


class BenchmarkSegmentReceiptTransition(str, Enum):
    """Runner-owned transition selected by the receipt gate."""

    REPAIR_RECEIPT_EVIDENCE = "repair_segment_receipt_evidence"
    ACCEPT_SEGMENT = "accept_segment_receipt"
    DISCARD_AND_RERUN = "discard_and_rerun_segment"


def _nonce(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _NONCE.fullmatch(text):
        raise ValueError(f"{field} must be a compact opaque nonce")
    return text


def _timestamp(value: Any, *, field: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def build_benchmark_segment_receipt(
    *,
    expected_segment_nonce: str,
    receipt_segment_nonce: str,
    segment_started_at: str,
    receipt_written_at: str,
    segment_ended_at: str,
    prior_receipt_nonces: Sequence[str] = (),
) -> dict[str, Any]:
    """Qualify one receipt without retaining its identity or timestamps.

    The runner owns nonce generation, file cleanup, process execution, timestamp
    observation, and any retry. This reducer only rejects a receipt copied from a
    different segment, written outside the observed segment window, or replayed
    from an earlier segment.
    """

    expected = _nonce(expected_segment_nonce, field="expected_segment_nonce")
    observed = _nonce(receipt_segment_nonce, field="receipt_segment_nonce")
    started = _timestamp(segment_started_at, field="segment_started_at")
    written = _timestamp(receipt_written_at, field="receipt_written_at")
    ended = _timestamp(segment_ended_at, field="segment_ended_at")
    prior = [
        _nonce(value, field="prior_receipt_nonce")
        for value in prior_receipt_nonces
    ]
    if ended < started:
        raise ValueError("segment_ended_at cannot precede segment_started_at")

    identity_matches = expected == observed
    within_window = started <= written <= ended
    receipt_unique = observed not in prior

    if not identity_matches:
        classification = BenchmarkSegmentReceiptClassification.IDENTITY_MISMATCH
    elif not receipt_unique:
        classification = BenchmarkSegmentReceiptClassification.REPLAYED
    elif not within_window:
        classification = (
            BenchmarkSegmentReceiptClassification.OUTSIDE_SEGMENT_WINDOW
        )
    else:
        classification = BenchmarkSegmentReceiptClassification.QUALIFIED

    qualified = classification is BenchmarkSegmentReceiptClassification.QUALIFIED
    transition = (
        BenchmarkSegmentReceiptTransition.ACCEPT_SEGMENT
        if qualified
        else BenchmarkSegmentReceiptTransition.DISCARD_AND_RERUN
    )
    return {
        "schema_version": BENCHMARK_SEGMENT_RECEIPT_SCHEMA_VERSION,
        "classification": classification.value,
        "qualified": qualified,
        "segment_result_usable": qualified,
        "segment_identity_matches": identity_matches,
        "receipt_within_segment_window": within_window,
        "receipt_unique": receipt_unique,
        "prior_receipt_count": len(prior),
        "recommended_transition": transition.value,
        "public_boundary": {
            "nonce_recorded": False,
            "timestamps_recorded": False,
            "receipt_content_recorded": False,
            "run_identity_recorded": False,
            "path_recorded": False,
        },
        "write_performed": False,
    }
