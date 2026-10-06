"""Independent synthetic cash identities and shipped CLI failure semantics."""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "src"))

from loopx_finance_value_discovery import assess_cash_reconciliation  # noqa: E402
from loopx_finance_value_discovery.cli import main  # noqa: E402


def request():
    return json.loads((PACKAGE / "examples/cash-reconciliation-v1.json").read_text())


def test_signed_cash_identity_and_display_do_not_authorize_evidence():
    payload = request()
    before = copy.deepcopy(payload)
    result = assess_cash_reconciliation(payload)
    assert result["reconciliation_state"] == "consistent"
    calculation = result["calculation"]
    # Independent hand calculation: -2600 + 50 + 125 = -2425;
    # 10125 - 2425 = 7700, whereas 10125 - 2600 = 7525.
    assert calculation["capex_cash_offsets"] == "175"
    assert calculation["net_capex_calculated"] == "-2425"
    assert calculation["adjusted_free_cash_flow_calculated"] == "7700"
    assert calculation["operating_less_gross_capex"] == "7525"
    assert calculation["net_capex_residual"] == "0"
    assert calculation["adjusted_free_cash_flow_residual"] == "0"
    assert calculation["billion_display"]["net_capex"] == {"1": "-2.4", "2": "-2.43"}
    assert calculation["billion_display"]["adjusted_free_cash_flow"] == {
        "1": "7.7",
        "2": "7.70",
    }
    assert calculation["display_is_new_observation"] is False
    assert result["period_assessment"]["period_evidence_eligible"] is True
    for field in (
        "financial_admission",
        "trading_allowed",
        "source_evidence_authenticated",
        "source_lifecycle_assessed",
        "distributable_cash_assessed",
    ):
        assert result[field] is False
    assert payload == before
    assert assess_cash_reconciliation(payload) == result
    result["facts"][0]["source_refs"][0]["label"] = "changed"
    assert payload == before


def test_missing_period_and_accuracy_stay_unknown_even_when_sum_reconciles():
    payload = request()
    payload["period"]["context"] = None
    payload["period"]["economic_period"] = None
    for fact in payload["facts"]:
        fact["accuracy"] = None
    result = assess_cash_reconciliation(payload)
    assert result["reconciliation_state"] == "consistent"
    assert result["numeric_accuracy_known"] is False
    assert result["period_assessment"]["period_evidence_eligible"] is False
    assert all(
        fact["numeric_accuracy"]["state"] == "unknown" for fact in result["facts"]
    )


@pytest.mark.parametrize(
    ("row", "field", "value", "reason"),
    [
        (1, "value", "2600", "gross_cash_outflow_sign_mismatch"),
        (2, "value", "-50", "cash_proceeds_sign_mismatch:asset_sale_proceeds"),
        (
            3,
            "value",
            "-125",
            "cash_proceeds_sign_mismatch:government_incentive_proceeds",
        ),
        (
            0,
            "unit",
            {"currency": "USD", "scale": 3},
            "unit_mismatch:operating_cash_flow",
        ),
        (
            0,
            "unit",
            {"currency": "EUR", "scale": 6},
            "unit_mismatch:operating_cash_flow",
        ),
        (0, "unit", None, "unit_missing:operating_cash_flow"),
    ],
)
def test_sign_and_unit_failures_prevent_calculation(row, field, value, reason):
    payload = request()
    payload["facts"][row][field] = value
    result = assess_cash_reconciliation(payload)
    assert result["reconciliation_state"] == "ineligible"
    assert result["calculation"] is None
    assert reason in result["reason_codes"]


@pytest.mark.parametrize("kind", ["stock", "commitment", "forecast", "rounded_display"])
def test_non_cash_flow_classifications_cannot_become_observed_cash(kind):
    payload = request()
    payload["facts"][0]["measurement_kind"] = kind
    result = assess_cash_reconciliation(payload)
    assert result["reconciliation_state"] == "ineligible"
    assert result["calculation"] is None


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        (
            "column_ref",
            "urn:synthetic:column:2001-FY",
            "source_column_mismatch:net_capex",
        ),
        ("digest", "sha256:" + "b" * 64, "source_digest_mismatch:net_capex"),
    ],
)
def test_source_column_and_digest_must_bind_to_same_declared_operand(
    field, value, reason
):
    payload = request()
    payload["facts"][4]["source_refs"][0][field] = value
    result = assess_cash_reconciliation(payload)
    assert result["calculation"] is None
    assert reason in result["reason_codes"]


@pytest.mark.parametrize(
    ("index", "value", "reason", "residual"),
    [
        (1, "-2425", "net_capex_reconciliation_mismatch", "-175"),
        (4, "-2600", "net_capex_reconciliation_mismatch", "-175"),
        (5, "7525", "adjusted_fcf_reconciliation_mismatch", "-175"),
        (5, "7701", "adjusted_fcf_reconciliation_mismatch", "1"),
    ],
)
def test_gross_net_alias_or_display_as_exact_produces_residual(
    index, value, reason, residual
):
    payload = request()
    payload["facts"][index]["value"] = value
    result = assess_cash_reconciliation(payload)
    assert result["reconciliation_state"] == "conflict"
    assert reason in result["reason_codes"]
    residual_field = (
        "net_capex_residual"
        if reason.startswith("net_capex")
        else "adjusted_free_cash_flow_residual"
    )
    assert result["calculation"][residual_field] == residual


@pytest.mark.parametrize("state", ["missing", "conflict"])
def test_missing_conflicting_fact_is_not_zero_and_recovers_without_input_mutation(
    state,
):
    payload = request()
    fact = payload["facts"][2]
    fact.update(state=state, value=None, accuracy=None)
    fact["source_refs"] = (
        []
        if state == "missing"
        else [
            fact["source_refs"][0],
            {**fact["source_refs"][0], "locator": "urn:synthetic:contradiction"},
        ]
    )
    result = assess_cash_reconciliation(payload)
    assert result["reconciliation_state"] == "incomplete"
    assert result["calculation"] is None
    assert result["facts"][2]["value"] is None
    assert assess_cash_reconciliation(request())["reconciliation_state"] == "consistent"


def test_zero_and_fractional_lexical_values_use_decimal_not_float_or_missing_defaults():
    payload = request()
    for fact, value in zip(
        payload["facts"], ["0.3", "-0.2", "0", "0", "-0.2", "0.1"], strict=True
    ):
        fact["value"] = value
        fact["accuracy"] = {"decimals": "1"}
    result = assess_cash_reconciliation(payload)
    assert result["reconciliation_state"] == "consistent"
    assert result["calculation"]["adjusted_free_cash_flow_calculated"] == "0.1"


@pytest.mark.parametrize(
    "value", [True, 10125, "NaN", "1e100", "1e-100", "1,025", "9" * 129]
)
def test_invalid_or_unbounded_numbers_raise_value_error(value):
    payload = request()
    payload["facts"][0]["value"] = value
    with pytest.raises(ValueError):
        assess_cash_reconciliation(payload)


def test_gate_boolean_and_final_decision_are_not_cash_operands():
    payload = request()
    payload["facts"][0]["passed"] = True
    with pytest.raises(ValueError, match="unsupported or missing fields"):
        assess_cash_reconciliation(payload)
    payload = request()
    payload["financial_admission"] = True
    with pytest.raises(ValueError):
        assess_cash_reconciliation(payload)


def test_actual_cli_direct_and_stdin_share_assessment_and_recover(monkeypatch, capsys):
    payload = request()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main(["assess-cash", "--input-json", "-"]) == 0
    direct = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out) == direct
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO('{"schema_version":"finance_cash_reconciliation_input_v1"}'),
    )
    assert main([]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out) == direct


def test_period_dispatch_compatibility_uses_existing_period_owner(monkeypatch, capsys):
    from loopx_finance_value_discovery import assess_period_comparison

    period = request()["period"]
    payload = {
        "schema_version": "finance_period_comparison_input_v1",
        "left": period,
        "right": period,
        "period_intent": "same_period",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out) == assess_period_comparison(payload)
