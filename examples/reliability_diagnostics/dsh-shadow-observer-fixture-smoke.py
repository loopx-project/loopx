#!/usr/bin/env python3
"""Prove the L1 shadow-observer contract on the deterministic DSH-shaped fixture.

Assertions come from the reliability-diagnostics design contract (RFC §3.1
non-interference, §7.4 integrity receipt): no outbound control path, bounded
and counted loss, visible clock uncertainty, raw material never persisted, and
a read-only projection with no authority. The smoke also proves the CLI
readback path round-trips the same ledger.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.reliability_diagnostics import (  # noqa: E402
    CONTROL_FIELD_FAMILIES,
    ENVELOPE_FIELDS,
    FIXTURE_GOAL_ID,
    RAW_MATERIAL_FIELD_FAMILIES,
    dsh_fixture_records,
    run_dsh_fixture,
)
from loopx.capabilities.reliability_diagnostics.fixture import (  # noqa: E402
    FIXTURE_BUFFER_BOUND,
    FIXTURE_UNCERTAIN_CLOCK_MS,
)


def run_cli(*args: str, runtime_root: Path, stdin: str | None = None) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "reliability-diagnostics",
            *args,
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def assert_no_outbound_control(receipt: dict, projection: dict) -> None:
    assert receipt["outbound_endpoints"] == [], receipt
    assert receipt["observation_entered_worker_context"] is False, receipt
    assert receipt["observation_entered_scheduler_inputs"] is False, receipt
    assert projection["mode"] == "read_only", projection
    assert projection["authority"] == "none", projection
    assert projection["worker_influence"] == "none", projection
    flattened = {name.replace("_", "") for name in ENVELOPE_FIELDS}
    assert not flattened & CONTROL_FIELD_FAMILIES
    assert not flattened & RAW_MATERIAL_FIELD_FAMILIES


def assert_bounded_failure(result: dict) -> None:
    receipt = result["receipt"]
    stats = result["stats"]
    fixture_records = dsh_fixture_records()
    # Every fixture record is observed once, then accepted, rejected, or dropped.
    assert len(fixture_records) == receipt["observed_event_count"], (
        len(fixture_records),
        receipt,
    )
    assert receipt["observed_event_count"] == (
        receipt["accepted_event_count"]
        + receipt["rejected_event_count"]
        + receipt["backpressure_drop_count"]
    ), receipt
    assert stats["buffer_bound"] == FIXTURE_BUFFER_BOUND
    assert receipt["backpressure_drop_count"] == 3, receipt
    # Sequence 10 and the rejected sequence 19 are visible as gaps; trailing
    # drops are visible only through the stats record.
    assert receipt["lost_event_count"] == 2, receipt
    assert receipt["clock"]["max_uncertainty_ms"] == FIXTURE_UNCERTAIN_CLOCK_MS
    assert receipt["rejected_by_reason"] == {"raw_material_field_rejected": 1}, receipt
    assert receipt["observer_failure_count"] == 0
    assert receipt["persisted_event_count"] == receipt["accepted_event_count"]
    assert receipt["event_sources"] == [
        "session/created",
        "session/disposed",
        "session/event",
    ]
    assert receipt["status"] == "degraded", receipt
    assert set(receipt["reason_codes"]) == {
        "sequence_gap",
        "backpressure_drop",
        "raw_material_rejected",
        "clock_uncertainty_exceeded",
    }, receipt
    assert "transcript" not in json.dumps(result["ledger_records"])
    assert "protected task content" not in json.dumps(result)


def assert_projection_signals(projection: dict) -> None:
    assert projection["repetition"] == {
        "detected": True,
        "threshold": 3,
        "longest_tool_run": 3,
        "tool_name": "read",
    }, projection
    assert projection["recovery"] == {
        "error_count": 1,
        "recovered_error_count": 1,
        "unrecovered_error_count": 0,
    }, projection
    assert projection["stall"]["detected"] is False, projection
    assert set(projection["signals"]) == {
        "repetition_suspected",
        "event_loss",
        "integrity_not_valid",
    }, projection
    assert projection["integrity"]["status"] == "degraded"


def assert_cli_readback(result: dict) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_root = Path(tmp)
        ndjson = "\n".join(json.dumps(record) for record in result["ledger_records"]) + "\n"
        ingest = run_cli("ingest", "--goal-id", FIXTURE_GOAL_ID, "--input", "-", runtime_root=runtime_root, stdin=ndjson)
        assert ingest["ok"] is True, ingest
        assert ingest["appended_record_count"] == len(result["ledger_records"]), ingest
        # Independent SHA-256 vector for the exact "goal-dsh-fixture" identity.
        assert ingest["ledger_ref"] == (
            "reliability_diagnostics/by-goal/"
            "a6cd5f6d145e6b683350da45838dfb904dcf881c8ebd3fcd0de86b12b8605d38.ndjson"
        )
        assert str(runtime_root) not in json.dumps(ingest)

        receipt = run_cli("receipt", "--goal-id", FIXTURE_GOAL_ID, runtime_root=runtime_root)
        assert receipt["receipt"] == result["receipt"], receipt
        status = run_cli("status", "--goal-id", FIXTURE_GOAL_ID, runtime_root=runtime_root)
        assert status["projection"] == result["projection"], status

        # A refused input leaves a durable invalid gate record in the ledger.
        poisoned = dict(result["ledger_records"][0])
        poisoned["command"] = {"kind": "stop"}
        rejected = run_cli("ingest", "--goal-id", FIXTURE_GOAL_ID, "--input", "-", runtime_root=runtime_root, stdin=json.dumps(poisoned) + "\n")
        assert rejected["rejected_by_reason"] == {"control_field_rejected": 1}, rejected
        assert run_cli("receipt", "--goal-id", FIXTURE_GOAL_ID, runtime_root=runtime_root)["receipt"]["status"] == "invalid"


def main() -> int:
    result = run_dsh_fixture()
    assert_no_outbound_control(result["receipt"], result["projection"])
    assert_bounded_failure(result)
    assert_projection_signals(result["projection"])
    assert_cli_readback(result)
    print("reliability-diagnostics dsh-shadow-observer-fixture-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
