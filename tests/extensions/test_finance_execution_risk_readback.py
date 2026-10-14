from __future__ import annotations

from copy import deepcopy
from decimal import Inexact, Rounded, localcontext
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PACKAGE = Path(__file__).resolve().parents[2] / "packages/loopx-finance-execution/src"
sys.path.insert(0, str(PACKAGE))
from loopx_finance_execution.risk_readback import check_risk_bound  # noqa: E402
from loopx_finance_execution.simulator import _digest  # noqa: E402


def request():
    terms = {
        "schema_version": "finance_execution_terms_v0",
        "account_ref": "account:synthetic",
        "venue": "venue:synthetic",
        "product": "linear_perpetual",
        "instrument": "SYNTH",
        "side": "buy",
        "quantity": "2",
        "quantity_unit": "SYNTH",
        "order_type": "limit",
        "limit_price": "30",
        "price_unit": "TEST",
        "time_in_force": "IOC",
        "reduce_only": False,
        "margin_mode": "isolated",
        "leverage": "10",
        "maximum_fee": "0.1",
        "fee_unit": "TEST",
        "protective_orders": [],
        "expires_at": "2030-01-01T10:00:00Z",
    }
    return {
        "terms": terms,
        "budget": {
            "schema_version": "finance_risk_budget_v0",
            "cash_unit": "TEST",
            "maximum_input": "9",
            "maximum_loss": "9",
        },
        "exposure_bound": {
            "schema_version": "finance_exposure_bound_v0",
            "terms_digest": _digest(terms),
            "source_ref": "evidence:synthetic",
            "cash_unit": "TEST",
            "maximum_cash_committed": "6",
            "maximum_additional_liability": "1",
            "maximum_total_cost": "2",
            "liability_scope": "capped",
            "automatic_margin_addition": False,
        },
        "observed_at": "2030-01-01T09:59:00Z",
        "now": "2030-01-01T09:59:01Z",
        "maximum_age_seconds": 5,
    }


def check(r):
    result = check_risk_bound(**r)
    assert result["read_only"] is True
    assert result["execution_allowed"] is False
    assert result["venue_bound_authenticated"] is False
    assert result["external_write_performed"] is False
    return result


def test_exact_boundary_includes_extra_liability_and_all_costs():
    r = request()
    frozen = deepcopy(r)
    result = check(r)
    assert r == frozen
    assert result["ok"] is True
    assert result["status"] == "within_declared_bound"
    assert result["upper_bounds"] == {"maximum_input": "9", "maximum_loss": "9"}
    # Notional is 60, but that does not stand in for isolated collateral or loss.
    for field, reason in [
        ("maximum_input", "input_budget_exceeded"),
        ("maximum_loss", "loss_budget_exceeded"),
    ]:
        r = request()
        r["budget"][field] = "8.99999999999999999999999999999999"
        assert reason in check(r)["reasons"]


@pytest.mark.parametrize(
    "field",
    ["maximum_cash_committed", "maximum_additional_liability", "maximum_total_cost"],
)
def test_unknown_never_defaults_to_zero(field):
    r = request()
    r["exposure_bound"][field] = None
    result = check(r)
    assert result["ok"] is False
    assert f"unknown:{field}" in result["reasons"]
    assert result["upper_bounds"] is None


def test_missing_source_bound_and_cross_margin_cannot_be_admitted():
    r = request()
    r["exposure_bound"] = None
    assert "exposure_bound_missing" in check(r)["reasons"]
    r = request()
    r["terms"]["margin_mode"] = "cross"
    r["exposure_bound"]["terms_digest"] = _digest(r["terms"])
    result = check(r)
    assert "cross_margin_unsupported" in result["reasons"]
    assert result["upper_bounds"] is None


@pytest.mark.parametrize("scope", ["uncapped", "unknown"])
def test_scope_without_a_cap_is_not_a_loss_bound(scope):
    r = request()
    r["exposure_bound"]["liability_scope"] = scope
    result = check(r)
    assert "liability_not_capped" in result["reasons"]
    assert result["upper_bounds"] is None


@pytest.mark.parametrize("addition", [True, None])
def test_automatic_or_unknown_collateral_addition_is_held(addition):
    r = request()
    r["exposure_bound"]["automatic_margin_addition"] = addition
    result = check(r)
    assert "automatic_margin_addition_or_unknown" in result["reasons"]
    assert result["upper_bounds"] is None


@pytest.mark.parametrize("object_name", ["terms", "budget", "exposure_bound"])
def test_unit_drift_has_no_comparable_bound(object_name):
    r = request()
    r[object_name]["price_unit" if object_name == "terms" else "cash_unit"] = "OTHER"
    if object_name == "terms":
        r["exposure_bound"]["terms_digest"] = _digest(r["terms"])
    result = check(r)
    assert "cash_unit_mismatch" in result["reasons"]
    assert result["upper_bounds"] is None


@pytest.mark.parametrize(
    "change", ["quantity", "account_ref", "limit_price", "maximum_fee"]
)
def test_bound_is_tied_to_original_exact_terms(change):
    r = request()
    r["terms"][change] = "other:account" if change == "account_ref" else "3"
    result = check(r)
    assert "terms_digest_mismatch" in result["reasons"]
    assert result["upper_bounds"] is None


@pytest.mark.parametrize(
    "now,reason",
    [
        ("2030-01-01T10:00:00Z", "expired"),
        ("2030-01-01T09:59:06Z", "stale_or_future_bound"),
        ("2030-01-01T09:58:59Z", "stale_or_future_bound"),
    ],
)
def test_fixed_expiry_and_observation_clock(now, reason):
    r = request()
    r["now"] = now
    assert reason in check(r)["reasons"]


def test_cash_risks_the_full_purchase_and_margin_cannot_be_understated():
    r = request()
    r["terms"]["margin_mode"] = "cash"
    r["terms"]["product"] = "spot"
    r["terms"]["leverage"] = "1"
    r["exposure_bound"]["terms_digest"] = _digest(r["terms"])
    assert "cash_bound_below_order_notional" in check(r)["reasons"]
    r["exposure_bound"]["maximum_cash_committed"] = "60"
    assert "loss_budget_exceeded" in check(r)["reasons"]
    r = request()
    r["exposure_bound"]["maximum_cash_committed"] = "5.9999999999999999999999999999"
    assert "cash_bound_below_initial_margin" in check(r)["reasons"]


@pytest.mark.parametrize(
    "field,value", [("product", "inverse_perpetual"), ("quantity_unit", "contracts")]
)
def test_unqualified_contract_multiplier_is_not_inferred(field, value):
    r = request()
    r["terms"][field] = value
    r["exposure_bound"]["terms_digest"] = _digest(r["terms"])
    result = check(r)
    assert "product_or_quantity_basis_unsupported" in result["reasons"]
    assert result["upper_bounds"] is None


def test_exact_sum_and_initial_margin_ignore_ambient_context():
    r = request()
    r["exposure_bound"]["maximum_total_cost"] = "2.00000000000000000000000000000001"
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[Inexact] = ctx.traps[Rounded] = True
        result = check(r)
    assert (
        result["upper_bounds"]["maximum_loss"] == "9.00000000000000000000000000000001"
    )
    assert "loss_budget_exceeded" in result["reasons"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1e300", True])
def test_invalid_amounts_are_rejected(value):
    r = request()
    r["exposure_bound"]["maximum_total_cost"] = value
    with pytest.raises(ValueError):
        check(r)


def test_cli_pass_held_and_redacted_error():
    env = {**os.environ, "PYTHONPATH": str(PACKAGE)}
    for r, expected in [
        (request(), True),
        ({**request(), "exposure_bound": None}, False),
    ]:
        run = subprocess.run(
            [sys.executable, "-m", "loopx_finance_execution.cli", "--check-risk-bound"],
            input=json.dumps(r),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        assert run.returncode == 0
        assert json.loads(run.stdout)["ok"] is expected
    r = request()
    r["budget"]["private_secret"] = "DO_NOT_ECHO"
    run = subprocess.run(
        [sys.executable, "-m", "loopx_finance_execution.cli", "--check-risk-bound"],
        input=json.dumps(r),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert run.returncode == 1
    assert json.loads(run.stdout)["read_only"] is True
    assert "DO_NOT_ECHO" not in run.stdout + run.stderr
