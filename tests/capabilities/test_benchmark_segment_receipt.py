from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_SEGMENT_RECEIPT_SCHEMA_VERSION,
    build_benchmark_segment_receipt,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NONCE = "segment-0001-abcdef"


def _receipt(**overrides: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "expected_segment_nonce": NONCE,
        "receipt_segment_nonce": NONCE,
        "segment_started_at": "2026-09-12T00:00:00Z",
        "receipt_written_at": "2026-09-12T00:01:00Z",
        "segment_ended_at": "2026-09-12T00:01:01Z",
        "prior_receipt_nonces": [],
    }
    facts.update(overrides)
    return build_benchmark_segment_receipt(**facts)  # type: ignore[arg-type]


def test_segment_receipt_qualifies_current_unique_window() -> None:
    receipt = _receipt()

    assert receipt["schema_version"] == BENCHMARK_SEGMENT_RECEIPT_SCHEMA_VERSION
    assert receipt["classification"] == "qualified"
    assert receipt["qualified"] is True
    assert receipt["segment_result_usable"] is True
    assert receipt["recommended_transition"] == "accept_segment_receipt"


@pytest.mark.parametrize(
    ("overrides", "classification"),
    [
        ({"receipt_segment_nonce": "segment-0002-abcdef"}, "segment_identity_mismatch"),
        ({"receipt_written_at": "2026-09-11T23:59:59Z"}, "receipt_outside_segment_window"),
        ({"receipt_written_at": "2026-09-12T00:01:02Z"}, "receipt_outside_segment_window"),
        ({"prior_receipt_nonces": [NONCE]}, "segment_receipt_replayed"),
    ],
)
def test_segment_receipt_mutations_fail_closed(
    overrides: dict[str, object], classification: str
) -> None:
    receipt = _receipt(**overrides)

    assert receipt["classification"] == classification
    assert receipt["qualified"] is False
    assert receipt["segment_result_usable"] is False
    assert receipt["recommended_transition"] == "discard_and_rerun_segment"


def test_segment_receipt_is_public_safe() -> None:
    receipt = _receipt()
    rendered = json.dumps(receipt, sort_keys=True)

    assert NONCE not in rendered
    assert "2026-09-12" not in rendered
    assert receipt["public_boundary"] == {
        "nonce_recorded": False,
        "timestamps_recorded": False,
        "receipt_content_recorded": False,
        "run_identity_recorded": False,
        "path_recorded": False,
    }


def test_segment_receipt_cli_rejects_stale_identity_without_echo() -> None:
    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts/loopx"),
            "benchmark",
            "segment-receipt",
            "--expected-segment-nonce",
            NONCE,
            "--receipt-segment-nonce",
            "stale-segment-abcdef",
            "--segment-started-at",
            "2026-09-12T00:00:00Z",
            "--receipt-written-at",
            "2026-09-12T00:01:00Z",
            "--segment-ended-at",
            "2026-09-12T00:01:01Z",
            "--require-qualified",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["classification"] == "segment_identity_mismatch"
    assert NONCE not in completed.stdout
    assert "stale-segment-abcdef" not in completed.stdout


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_segment_nonce", "short"),
        ("receipt_segment_nonce", "contains space invalid"),
        ("segment_started_at", "not-a-time"),
        ("segment_ended_at", "2026-09-11T23:59:59Z"),
    ],
)
def test_segment_receipt_rejects_invalid_input(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        _receipt(**{field: value})
