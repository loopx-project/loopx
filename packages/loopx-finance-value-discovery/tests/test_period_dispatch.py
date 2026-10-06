"""Real stdin/direct processes must preserve the period admission boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src"
EXAMPLES = PACKAGE / "examples"


def invoke(arguments, payload):
    env = os.environ.copy()
    env["LOOPX_USAGE_PING"] = "0"
    # Test the provider module in a child process, using this checkout's source.
    env["PYTHONPATH"] = os.pathsep.join([str(SOURCE), env.get("PYTHONPATH", "")])
    process = subprocess.run(
        [sys.executable, "-m", "loopx_finance_value_discovery.cli", *arguments],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
        env=env,
    )
    assert not process.stderr
    return process.returncode, json.loads(process.stdout)


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("same_duration", None),
        ("missing_context", "finite_duration_context_missing"),
        ("missing_declaration", "economic_period_missing"),
        ("instant_is_not_duration", "finite_duration_context_missing"),
        ("binding_conflict", "context_source_binding_mismatch"),
    ],
)
def test_stdin_matches_direct_without_promoting_missing_or_conflicting_proof(case, reason):
    payload = json.loads((EXAMPLES / "period-comparison-v1.json").read_text())
    right = payload["right"]
    if case == "missing_context":
        right["context"] = None
    elif case == "missing_declaration":
        right["economic_period"] = None
    elif case == "instant_is_not_duration":
        right["context"] = {"instant": "2001-12-31Z"}
    elif case == "binding_conflict":
        right["economic_period"]["context_source_digest"] = "sha256:" + "b" * 64

    direct = invoke(["assess-period", "--input-json", "-"], payload)
    managed = invoke([], payload)
    assert managed == direct
    code, result = managed
    assert code == 0
    assert result["schema_version"] == "finance_period_comparison_assessment_v1"
    assert result["period_evidence_eligible"] is (reason is None)
    if reason is not None:
        assert reason in result["reason_codes"]
    else:
        assert result["economic_period_relation"] == "same"
        assert result["reason_codes"] == []
    for field in (
        "source_evidence_authenticated", "source_lifecycle_assessed",
        "financial_admission", "trading_allowed",
    ):
        assert result[field] is False


def test_malformed_period_stdin_fails_then_valid_request_recovers():
    payload = json.loads((EXAMPLES / "period-comparison-v1.json").read_text())
    bad = dict(payload, left=[])
    direct = invoke(["assess-period", "--input-json", "-"], bad)
    assert invoke([], bad) == direct
    code, error = direct
    assert code == 1 and error["ok"] is False
    assert error["external_reads_performed"] is False
    assert error["external_writes_performed"] is False
    assert error["trading_allowed"] is False
    assert invoke([], payload)[1]["period_evidence_eligible"] is True


def test_legacy_discovery_stdin_retains_direct_reducer_result():
    payload = json.loads((EXAMPLES / "paypal-debeta-discovery.json").read_text())
    assert invoke([], payload) == invoke(
        ["reduce", "--input-json", "-", "--format", "json"], payload
    )
