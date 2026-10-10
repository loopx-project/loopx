"""Receipt ergonomics must not weaken operation identity or evidence adoption."""
from pathlib import Path

import pytest

from loopx.capabilities.multi_subagent.native_child_receipts import load_native_child_activity
from test_native_child_receipts import AGENT, GOAL, TURN, _admit, _record


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("reason", [None, "bounded_child_result_not_available"])
def test_terminal_reports_accept_matching_echoes_and_optional_diagnostics(
    tmp_path: Path, outcome: str, reason: str | None,
) -> None:
    _admit(tmp_path)
    _record(tmp_path, "op-1", stage="decision", operation="followup",
            outcome="started", entrypoint_id="generic_host")
    terminal = dict(stage="result", outcome=outcome, reason_code=reason)
    first = _record(tmp_path, "op-1", **terminal,
                    operation="followup", entrypoint_id="generic_host")
    assert first["appended"] is True
    replay = _record(tmp_path, "op-1", **terminal)
    assert replay["appended"] is False
    assert replay["receipt"] == first["receipt"]
    with pytest.raises(ValueError, match="conflicting"):
        _record(tmp_path, "op-1", stage="result", outcome=outcome,
                reason_code="changed_diagnostic")
    for echo in ({"operation": "spawn"}, {"entrypoint_id": "other_host"}):
        with pytest.raises(ValueError, match="decision identity"):
            _record(tmp_path, "op-1", **terminal, **echo)
    if outcome != "completed":
        with pytest.raises(ValueError, match="completed"):
            _record(tmp_path, "op-1", stage="review", outcome="accepted",
                    evidence_ref="evidence-1", validation_ref="validation-1")
    review = dict(stage="review", outcome="rejected", reason_code=reason)
    written = _record(tmp_path, "op-1", **review,
                      operation="followup", entrypoint_id="generic_host")
    assert written["appended"] is True
    assert _record(tmp_path, "op-1", **review)["appended"] is False
    with pytest.raises(ValueError, match="conflicting"):
        _record(tmp_path, "op-1", stage="review", outcome="rejected",
                reason_code="changed_diagnostic")
    cold = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
                                      turn_instance_id=TURN, configured_limit=6)
    assert cold == written["native_child_activity"]
    assert cold["parent_accepted_count"] == 0
    assert cold["host_attested"] is False
    assert cold["quota_spend_slots"] == 0
    [row] = cold["operations"]
    assert row["result"] == outcome
    assert row["parent_review"] == "rejected"
    assert row.get("result_reason_code") == reason
    assert row.get("review_reason_code") == reason


@pytest.mark.parametrize("stage", ["result", "review"])
def test_diagnostics_stay_opaque_and_nonadoption_cannot_carry_evidence(
    tmp_path: Path, stage: str,
) -> None:
    _admit(tmp_path)
    _record(tmp_path, "op-1", stage="decision", operation="spawn",
            outcome="started", entrypoint_id="generic_host")
    if stage == "review":
        _record(tmp_path, "op-1", stage="result", outcome="failed")
    outcome = "failed" if stage == "result" else "deferred"
    for reason in ("", "/private/raw-error", "raw error text"):
        with pytest.raises(ValueError, match="compact opaque id"):
            _record(tmp_path, "op-1", stage=stage, outcome=outcome, reason_code=reason)
    with pytest.raises(ValueError, match="evidence|review"):
        _record(tmp_path, "op-1", stage=stage, outcome=outcome, evidence_ref="evidence-1")
    assert _record(tmp_path, "op-1", stage=stage, outcome=outcome)["appended"] is True


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
@pytest.mark.parametrize("review", ["deferred", "rejected"])
def test_unsuccessful_terminal_results_allow_explicit_nonadoption(
    tmp_path: Path, terminal: str, review: str,
) -> None:
    _admit(tmp_path)
    _record(tmp_path, "op-1", stage="decision", operation="spawn",
            outcome="started", entrypoint_id="generic_host")
    _record(tmp_path, "op-1", stage="result", outcome=terminal)
    result = _record(tmp_path, "op-1", stage="review", outcome=review)
    assert result["native_child_activity"]["operations"][0]["parent_review"] == review
    assert result["native_child_activity"]["parent_accepted_count"] == 0
