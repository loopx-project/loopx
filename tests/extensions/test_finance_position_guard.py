from __future__ import annotations

import io
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/loopx-finance-value-discovery/src"))
from loopx_finance_value_discovery import position_guard as guard  # noqa: E402
from loopx_finance_value_discovery.cli import run  # noqa: E402

NOW = "2026-01-03T12:00:00Z"
SCOPE = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(guard, "_utc_now", lambda: datetime(2026, 1, 3, 12, tzinfo=UTC))


def bind(request):
    request["context_refs"] = [
        {"kind": r["kind"], "ref": r["receipt_id"], "digest": guard.canonical_digest(r)}
        for r in request["input"]["receipts"]
    ]
    return request


def receipt(request, kind):
    return next(r for r in request["input"]["receipts"] if r["kind"] == kind)


def example():
    trade = {"trade_id": "episode-1", "target_key": "episode-1", "asset": "DEMO",
             "account_scope_digest": SCOPE, "side": "long", "entry_at": "2026-01-02T12:00:00Z",
             "entry_quantity": "10", "entry_price": "100", "opening_fill_ids": ["fill-1"],
             "max_hold_until": "2026-01-04T12:00:00Z", "review_interval_seconds": 3600,
             "stop_price": "95", "take_profit_price": "110", "cash_currency": "USD",
             "max_loss_cash": "100", "exit_cost_reserve_cash": "5",
             "financial_authority": "human_final_submit", "payoff_model": "linear_quote_cash",
             "quantity_unit": "base_asset", "unit_multiplier": "1", "price_currency": "USD"}
    orders = [{"order_id": kind, "kind": kind, "side": "sell", "remaining_quantity": "10",
               "reduce_only": True, "trigger_price": price, "trigger_basis": "mark"}
              for kind, price in [("stop_loss", "95"), ("take_profit", "110")]]
    data = {"position": {"quantity": "10", "side": "long", "trigger_reference_price": "102", "trigger_basis": "mark"},
            "orders": {"orders": orders},
            "fills": {"fills": [{"fill_id": "fill-1", "role": "entry", "quantity": "10", "price": "100", "fee_cash": "1", "filled_at": trade["entry_at"]}],
                      "history_complete": True, "costs_complete": True, "funding_paid_cash": "2"},
            "monitor": {"todo_id": "todo-synthetic", "task_class": "continuous_monitor", "status": "open", "target_key": "episode-1",
                        "claimed_by": "synthetic-owner", "next_due_at": "2026-01-03T13:00:00Z", "expires_at": "2026-01-05T12:00:00Z"}}
    receipts = [{"kind": kind, "receipt_id": kind + "-read", "trade_id": "episode-1", "asset": "DEMO",
                 "account_scope_digest": SCOPE, "observed_at": NOW, "complete": True, "data": value}
                for kind, value in data.items()]
    return bind({"schema_version": guard.REQUEST_SCHEMA, "extension_id": guard.EXTENSION_ID,
                 "operation": guard.OPERATION, "invocation_id": "synthetic-guard",
                 "input": {"decision_at": NOW, "max_age_seconds": 120, "max_skew_seconds": 30, "trade": trade, "receipts": receipts}})


def evaluate(request):
    return guard.evaluate_finance_position_guard(bind(request))


def close(request):
    receipt(request, "position")["data"]["quantity"] = "0"
    receipt(request, "orders")["data"]["orders"] = []
    receipt(request, "fills")["data"]["fills"].append(
        {"fill_id": "exit-1", "role": "exit", "quantity": "10", "price": "102", "fee_cash": "1", "filled_at": NOW}
    )


def test_full_alternative_legs_cover_once_without_authority():
    out = evaluate(example())
    assert out["state"] == "protected_open"
    assert out["protection_coverage"] == {"stop_loss": "10", "take_profit": "10"}
    assert out["risk_estimate"]["estimated_stop_loss_cash"] == "58"
    assert not out["risk_estimate"]["is_loss_guarantee"]
    assert out["exit_draft"] is None
    assert not any(out["effects"].values())
    assert out["privacy"] == "private_account_material"


def test_existing_earlier_core_review_is_not_postponed():
    r = example()
    receipt(r, "monitor")["data"]["next_due_at"] = "2026-01-03T12:10:00Z"
    out = evaluate(r)
    assert out["monitor_projection"]["suggested_next_due_at"] == "2026-01-03T12:10:00+00:00"
    assert not out["effects"]["monitor_mutations"]


@pytest.mark.parametrize("field,value,reason", [
    ("reduce_only", False, "unsafe_open_order"),
    ("side", "buy", "unsafe_open_order"),
    ("remaining_quantity", "5", "stop_loss_undercovered"),
    ("trigger_basis", "index", "protection_plan_or_basis_mismatch"),
    ("trigger_price", "96", "protection_plan_or_basis_mismatch"),
])
def test_loss_stop_cannot_be_replaced_by_tp(field, value, reason):
    r = example()
    receipt(r, "orders")["data"]["orders"][0][field] = value
    out = evaluate(r)
    assert out["state"] == "attention_required" and out["urgent"]
    assert reason in out["reasons"]


def test_split_alternatives_are_not_added_without_tranche_proof():
    r = example()
    orders = receipt(r, "orders")["data"]["orders"]
    orders[0]["remaining_quantity"] = "5"
    other = deepcopy(orders[0])
    other["order_id"] = "another-alternative"
    orders.append(other)
    assert "stop_loss_undercovered" in evaluate(r)["reasons"]


def test_partial_exit_rechecks_remaining_quantity_and_costs():
    r = example()
    receipt(r, "position")["data"]["quantity"] = "4"
    receipt(r, "fills")["data"]["fills"].append(
        {"fill_id": "exit-1", "role": "exit", "quantity": "6", "price": "102", "fee_cash": "1", "filled_at": NOW}
    )
    out = evaluate(r)
    assert out["state"] == "protected_open" and out["open_quantity"] == "4"
    assert out["monitor_projection"]["keep_monitor_open"]
    assert out["risk_estimate"]["estimated_stop_loss_cash"] == "17"


@pytest.mark.parametrize("kind", guard.KINDS)
def test_stale_or_future_reads_cannot_claim_protected(kind):
    for timestamp in ["2026-01-03T11:50:00Z", "2026-01-03T12:00:01Z"]:
        r = example()
        receipt(r, kind)["observed_at"] = timestamp
        out = evaluate(r)
        assert out["state"] == "attention_required"
        assert "receipt_stale_or_future" in out["reasons"]
        assert out["risk_estimate"]["estimated_stop_loss_cash"] is None


def test_digest_identity_and_episode_fences():
    r = example()
    receipt(r, "position")["data"]["quantity"] = "8"
    with pytest.raises(ValueError, match="digest binding"):
        guard.evaluate_finance_position_guard(r)
    for field, value in [("asset", "OTHER"), ("trade_id", "episode-reopened"), ("account_scope_digest", "sha256:" + "b"*64)]:
        r = example()
        receipt(r, "orders")[field] = value
        assert "receipt_identity_mismatch" in evaluate(r)["reasons"]
    r = example()
    r["input"]["trade"]["opening_fill_ids"] = ["old-episode-fill"]
    assert "opening_fill_identity_mismatch" in evaluate(r)["reasons"]


def test_expiry_never_hides_open_exposure_or_waives_time_exit():
    r = example()
    r["input"]["trade"]["max_hold_until"] = NOW
    receipt(r, "monitor")["data"]["next_due_at"] = NOW
    out = evaluate(r)
    assert out["state"] == "exit_review_required" and out["urgent"]
    assert out["exit_draft"]["quantity"] == "10"
    assert out["exit_draft"]["financial_authority"] == "human_final_submit"
    assert out["monitor_projection"]["keep_monitor_open"]
    receipt(r, "monitor")["data"]["expires_at"] = NOW
    out = evaluate(r)
    assert "monitor_expired_with_open_exposure" in out["reasons"]
    assert out["monitor_projection"]["keep_monitor_open"]


def test_monitor_after_deadline_and_wrong_owner_target_are_not_ready():
    r = example()
    receipt(r, "monitor")["data"]["next_due_at"] = "2026-01-05T11:00:00Z"
    assert "monitor_misses_hold_deadline" in evaluate(r)["reasons"]
    r = example()
    receipt(r, "monitor")["data"]["target_key"] = "another-episode"
    assert "monitor_binding_unverified" in evaluate(r)["reasons"]


def test_flat_requires_complete_fills_and_no_residual_orders():
    r = example()
    receipt(r, "position")["data"]["quantity"] = "0"
    out = evaluate(r)
    assert out["state"] == "closure_unverified"
    assert out["monitor_projection"]["keep_monitor_open"]
    r = example()
    close(r)
    out = evaluate(r)
    assert out["state"] == "closed_verified"
    assert not out["monitor_projection"]["keep_monitor_open"]
    assert out["next_action"] == "propose_close_monitor"
    assert not any(out["effects"].values())
    receipt(r, "orders")["data"]["orders"] = receipt(example(), "orders")["data"]["orders"]
    assert "residual_orders_after_flat" in evaluate(r)["reasons"]


def test_close_requires_post_position_fills_and_orders():
    r = example()
    close(r)
    receipt(r, "orders")["observed_at"] = "2026-01-03T11:59:59Z"
    assert "closure_readback_order_unverified" in evaluate(r)["reasons"]


def test_duplicate_fill_is_one_fee_but_conflict_is_unverified():
    r = example()
    fills = receipt(r, "fills")["data"]["fills"]
    fills.append(deepcopy(fills[0]))
    assert evaluate(r)["risk_estimate"]["estimated_stop_loss_cash"] == "58"
    fills[-1]["fee_cash"] = "2"
    assert "conflicting_fill_identity" in evaluate(r)["reasons"]


def test_cost_budget_and_unknown_costs():
    r = example()
    r["input"]["trade"]["max_loss_cash"] = "50"
    assert evaluate(r)["state"] == "exit_review_required"
    receipt(r, "fills")["data"]["costs_complete"] = False
    out = evaluate(r)
    assert "costs_unverified" in out["reasons"] and out["risk_estimate"]["estimated_stop_loss_cash"] is None


def test_short_uses_buy_protection():
    r = example()
    t = r["input"]["trade"]
    t.update(side="short", stop_price="105", take_profit_price="90")
    receipt(r, "position")["data"]["side"] = "short"
    for o in receipt(r, "orders")["data"]["orders"]:
        o.update(side="buy", trigger_price="105" if o["kind"] == "stop_loss" else "90")
    assert evaluate(r)["state"] == "protected_open"


def test_runtime_clock_and_decimal_admission_do_not_echo_private_values():
    r = example()
    r["input"]["decision_at"] = "2026-01-03T11:00:00Z"
    with pytest.raises(ValueError, match="clock"):
        evaluate(r)
    r = example()
    r["input"]["trade"]["entry_price"] = "NaN-private"
    with pytest.raises(ValueError, match="schema admission") as error:
        evaluate(r)
    assert "NaN-private" not in str(error.value)


def test_cli_and_managed_operation_use_same_private_result(monkeypatch, capsys):
    r = example()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(r)))
    assert run(["evaluate-position", "--input-json", "-"]) == 0
    direct = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(r)))
    assert run([]) == 0
    assert json.loads(capsys.readouterr().out) == direct


def test_incompatible_payoff_and_currency_cannot_receive_risk_estimate():
    for field, value in [("payoff_model", "inverse"), ("unit_multiplier", "100"), ("price_currency", "USDC")]:
        r = example()
        r["input"]["trade"][field] = value
        with pytest.raises(ValueError):
            evaluate(r)


def test_profitable_partial_exit_never_returns_empty_zero_cash():
    r = example()
    receipt(r, "position")["data"]["quantity"] = "1"
    receipt(r, "fills")["data"]["fills"].append(
        {"fill_id": "profitable-exit", "role": "exit", "quantity": "9", "price": "120", "fee_cash": "1", "filled_at": NOW}
    )
    assert evaluate(r)["risk_estimate"]["estimated_stop_loss_cash"] == "0"
