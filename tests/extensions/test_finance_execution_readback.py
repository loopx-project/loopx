from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest


PACKAGE = Path(__file__).resolve().parents[2] / "packages/loopx-finance-execution/src"
sys.path.insert(0, str(PACKAGE))
from loopx_finance_execution.order_readback import (  # noqa: E402
    check_order_summary,
    reconcile_execution,
)
from loopx_finance_execution.simulator import _digest  # noqa: E402


def terms():
    return {
        "schema_version": "finance_execution_terms_v0",
        "account_ref": "account:synthetic",
        "venue": "venue:synthetic",
        "product": "perpetual",
        "instrument": "SYNTH",
        "side": "buy",
        "quantity": "2",
        "quantity_unit": "SYNTH",
        "order_type": "limit",
        "limit_price": "10",
        "price_unit": "TEST",
        "time_in_force": "IOC",
        "reduce_only": False,
        "margin_mode": "isolated",
        "leverage": "1",
        "maximum_fee": "0.2",
        "fee_unit": "TEST",
        "expires_at": "2030-01-01T10:00:00Z",
        "protective_orders": [
            {
                "kind": "stop_loss",
                "trigger_price": "9",
                "price_unit": "TEST",
                "quantity": "2",
                "quantity_unit": "SYNTH",
                "reduce_only": True,
            }
        ],
    }


def summary(t):
    return {**deepcopy(t), "schema_version": "finance_order_summary_v0"}


def observation(t, **changes):
    return {
        "schema_version": "finance_execution_observation_v0",
        "operation_id": "op:synthetic",
        "attempt_id": "attempt:one",
        "terms_digest": _digest(t),
        "account_ref": t["account_ref"],
        "venue": t["venue"],
        "instrument": t["instrument"],
        "observed_at": "2030-01-01T11:00:00Z",
        "coverage_complete": True,
        "submission_attempted": True,
        "order_id": "order:one",
        "order_state": "open",
        "fills": [],
        "protection_state": "unknown",
        **changes,
    }


def fill(quantity="2", **changes):
    return {
        "fill_id": "fill:one",
        "order_id": "order:one",
        "side": "buy",
        "quantity": quantity,
        "quantity_unit": "SYNTH",
        "price": "9.9",
        "price_unit": "TEST",
        "fee": "0.1",
        "fee_unit": "TEST",
        **changes,
    }


def check(t, s, **changes):
    return check_order_summary(
        t,
        s,
        observed_at="2030-01-01T09:59:00Z",
        now="2030-01-01T09:59:01Z",
        maximum_age_seconds=5,
        **changes,
    )


def reduce(t, o):
    return reconcile_execution(
        t, o, operation_id="op:synthetic", attempt_id="attempt:one"
    )


def test_exact_summary_preserves_digest_and_never_issues_permission():
    t = terms()
    s = summary(t)
    s["quantity"] = "2.00"
    r = check(t, s)
    assert r["ok"] and r["terms_digest"] == _digest(t)
    assert r["execution_allowed"] is False and r["external_write_performed"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_ref", "account:other"),
        ("venue", "venue:other"),
        ("instrument", "OTHER"),
        ("product", "spot"),
        ("side", "sell"),
        ("quantity", "2.01"),
        ("quantity_unit", "USD"),
        ("limit_price", "10.01"),
        ("time_in_force", "GTC"),
        ("reduce_only", True),
        ("margin_mode", "cross"),
        ("leverage", "10"),
        ("maximum_fee", "0.3"),
        ("fee_unit", "USD"),
        ("protective_orders", []),
        ("expires_at", "2030-01-01T10:01:00Z"),
    ],
)
def test_each_effect_bearing_change_stops(field, value):
    t = terms()
    s = summary(t)
    s[field] = value
    assert not check(t, s)["ok"]


@pytest.mark.parametrize(
    "now,observed,reason",
    [
        ("2030-01-01T10:00:00Z", "2030-01-01T09:59:59Z", "expired"),
        ("2030-01-01T10:00:01Z", "2030-01-01T10:00:00Z", "expired"),
        ("2030-01-01T09:59:06Z", "2030-01-01T09:59:00Z", "stale_or_future_summary"),
        ("2030-01-01T09:59:00Z", "2030-01-01T09:59:01Z", "stale_or_future_summary"),
    ],
)
def test_clock_boundaries(now, observed, reason):
    t = terms()
    r = check_order_summary(
        t, summary(t), now=now, observed_at=observed, maximum_age_seconds=5
    )
    assert reason in r["reasons"] and r["next_step"] == "stop"


def test_missing_field_and_unknown_quantity_fail_closed():
    t = terms()
    s = summary(t)
    del s["leverage"]
    assert not check(t, s)["ok"]
    s = summary(t)
    s["quantity"] = "NaN"
    assert not check(t, s)["ok"]


@pytest.mark.parametrize(
    "change,state",
    [
        ({}, "submitted"),
        ({"order_id": None, "order_state": "unknown"}, "submission_unknown"),
        ({"coverage_complete": False}, "submission_unknown"),
        ({"order_state": "filled"}, "submission_unknown"),
        ({"order_state": "rejected"}, "rejected"),
        ({"order_state": "cancelled"}, "cancelled"),
        ({"fills": [fill("0.5")]}, "partially_filled"),
        (
            {"fills": [fill()], "order_state": "filled", "protection_state": "active"},
            "filled",
        ),
        (
            {"submission_attempted": False, "order_id": None, "order_state": "unknown"},
            "not_submitted",
        ),
    ],
)
def test_retained_attempt_states_survive_expiry_without_resubmit(change, state):
    t = terms()
    r = reduce(t, observation(t, **change))
    assert r["state"] == state
    assert r["resubmit_allowed"] is False and r["execution_allowed"] is False
    assert r["external_write_performed"] is False
    assert r == reduce(
        t, observation(t, **change)
    )  # Restart uses the same retained facts.


@pytest.mark.parametrize(
    "protection,fee,complete",
    [
        ("active", "0.1", True),
        ("failed", "0.1", False),
        ("unknown", "0.1", False),
        ("not_required", "0.1", False),
        ("active", None, False),
        ("active", "0.3", False),
    ],
)
def test_entry_fill_does_not_hide_protection_or_cost_gap(protection, fee, complete):
    t = terms()
    r = reduce(
        t,
        observation(
            t, fills=[fill(fee=fee)], order_state="filled", protection_state=protection
        ),
    )
    assert r["state"] == "filled" and r["financial_readback_complete"] is complete
    assert r["fee"] == fee


@pytest.mark.parametrize(
    "field,value",
    [
        ("terms_digest", "0" * 64),
        ("operation_id", "op:other"),
        ("attempt_id", "attempt:other"),
        ("account_ref", "account:other"),
        ("venue", "venue:other"),
        ("instrument", "OTHER"),
    ],
)
def test_wrong_operation_attempt_and_identity_rejected(field, value):
    t = terms()
    o = observation(t)
    o[field] = value
    with pytest.raises(ValueError):
        reduce(t, o)


@pytest.mark.parametrize(
    "fills",
    [
        [fill(), fill()],
        [fill("2.1")],
        [fill(order_id="order:other")],
        [fill(price="10.01")],
        [fill(fee_unit="USD")],
        [fill(side="sell")],
    ],
)
def test_duplicate_excess_or_wrong_fill_rejected(fills):
    t = terms()
    with pytest.raises(ValueError):
        reduce(t, observation(t, fills=fills))


def test_actual_cli_readback_and_legacy_simulation_modes():
    t = terms()
    env = {**os.environ, "PYTHONPATH": str(PACKAGE)}
    argv = [sys.executable, "-m", "loopx_finance_execution.cli"]
    request = {
        "terms": t,
        "summary": summary(t),
        "now": "2030-01-01T09:59:01Z",
        "observed_at": "2030-01-01T09:59:00Z",
        "maximum_age_seconds": 5,
    }
    process = subprocess.run(
        argv + ["--check-order-summary"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert json.loads(process.stdout) == check(t, summary(t))
    request = {
        "terms": t,
        "observation": observation(t, coverage_complete=False),
        "operation_id": "op:synthetic",
        "attempt_id": "attempt:one",
    }
    process = subprocess.run(
        argv + ["--reconcile-execution"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert json.loads(process.stdout)["state"] == "submission_unknown"
    # Without an opt-in, the old simulator refuses the new readback request.
    process = subprocess.run(
        argv, input=json.dumps(request), text=True, capture_output=True, env=env
    )
    assert process.returncode == 1 and json.loads(process.stdout)["simulation"] is True
