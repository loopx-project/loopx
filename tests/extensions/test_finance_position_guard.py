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


def partial_example():
    return bind_partial({
        "schema_version": guard.PARTIAL_REQUEST_SCHEMA, "extension_id": guard.EXTENSION_ID,
        "operation": guard.OPERATION, "invocation_id": "partial-synthetic",
        "input": {
            "decision_at": NOW, "max_age_seconds": 120,
            "episode": {"trade_id": "episode-1", "target_key": "episode-1", "asset": "DEMO",
                        "max_hold_until": NOW, "source_ref": "original-plan", "source_digest": SCOPE},
            "position": {"quantity": "10.000", "asset": "DEMO", "quantity_unit": "DEMO", "observed_at": NOW,
                         "source_ref": "visible-position", "source_digest": "sha256:" + "b" * 64},
            "decision": {"disposition": "exit_under_original_time_limit", "source_ref": None, "source_digest": None},
            "execution": {"disposition": "awaiting_owner_execution", "source_ref": None, "source_digest": None},
            "evidence_gaps": ["account_identity", "order_ids", "opening_fills", "costs"],
        },
    })


def bind_partial(request):
    request["context_refs"] = [
        {"kind": kind, "ref": value["source_ref"], "digest": guard.canonical_digest(value)}
        for kind in ["episode", "position", "decision", "execution"]
        if (value := request["input"][kind]) is not None and value["source_ref"] is not None
    ]
    return request


@pytest.mark.parametrize("deadline,expected", [
    ("2026-01-03T12:00:01Z", "attention_required"),
    (NOW, "exit_review_required"),
    ("2026-01-03T11:59:59Z", "exit_review_required"),
])
def test_explicit_partial_path_preserves_deadline_without_full_receipts(deadline, expected):
    r = partial_example()
    r["input"]["episode"]["max_hold_until"] = deadline
    original = deepcopy(bind_partial(r))
    out = guard.evaluate_finance_position_guard(r)
    assert out["state"] == expected
    assert out["obligation"]["pending"] and not out["obligation"]["closeout_verified"]
    assert not out["obligation"]["deadline_authority_verified"]
    assert out["exit_draft"] is None and out["risk_estimate"] is None
    assert not out["position_verified"] and not any(out["effects"].values())
    assert out["monitor_projection"]["keep_monitor_open"]
    assert r == original


@pytest.mark.parametrize("quantity,execution", [
    (None, "awaiting_owner_execution"), ("4", "partial_exit_observed"),
    ("0", "flat_observed_unverified"),
])
def test_partial_unknown_partial_exit_and_visible_flat_cannot_close(quantity, execution):
    r = partial_example()
    r["input"]["position"]["quantity"] = quantity
    r["input"]["execution"]["disposition"] = execution
    out = guard.evaluate_finance_position_guard(bind_partial(r))
    assert out["state"] == "exit_review_required"
    assert out["obligation"]["execution"] == execution
    assert out["obligation"]["pending"] and out["exit_draft"] is None


def test_partial_semantic_material_changes_at_deadline_not_each_poll(monkeypatch):
    r = partial_example()
    r["input"]["decision_at"] = "2026-01-03T11:59:59Z"
    r["input"]["position"]["observed_at"] = r["input"]["decision_at"]
    before = guard.evaluate_finance_position_guard(bind_partial(r))
    r["input"]["decision_at"] = NOW
    equal = guard.evaluate_finance_position_guard(bind_partial(r))
    assert before["material_digest"] != equal["material_digest"]
    r["input"]["decision_at"] = "2026-01-03T12:00:01Z"
    r["input"]["position"].update(quantity="10.0", observed_at=r["input"]["decision_at"], source_digest="sha256:" + "c" * 64)
    r["invocation_id"] = "later-poll"
    monkeypatch.setattr(guard, "_utc_now", lambda: datetime(2026, 1, 3, 12, 0, 1, tzinfo=UTC))
    after = guard.evaluate_finance_position_guard(bind_partial(r))
    assert after["material_digest"] == equal["material_digest"]
    assert after["request_digest"] != equal["request_digest"]
    r["input"]["execution"]["disposition"] = "partial_exit_observed"
    r["input"]["position"]["quantity"] = "4"
    assert guard.evaluate_finance_position_guard(bind_partial(r))["material_digest"] != after["material_digest"]


@pytest.mark.parametrize("observed", ["2026-01-03T11:50:00Z", "2026-01-03T12:00:01Z", None])
def test_partial_stale_future_missing_source_keeps_original_pending(observed):
    r = partial_example()
    if observed is None:
        r["input"]["position"] = None
    else:
        r["input"]["position"]["observed_at"] = observed
    out = guard.evaluate_finance_position_guard(bind_partial(r))
    assert out["state"] == "exit_review_required" and out["urgent"]
    assert out["material_projection"]["position_source_state"] != "available_unverified"
    assert out["obligation"]["pending"] and out["exit_draft"] is None


def test_partial_binding_fences_and_exact_decimal_precision():
    r = partial_example()
    r["input"]["episode"]["max_hold_until"] = "2027-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="digest binding"):
        guard.evaluate_finance_position_guard(r)
    # Even consistently supplied future clocks never authenticate an extension
    # or close a native obligation; its applier must retain the original source.
    out = guard.evaluate_finance_position_guard(bind_partial(r))
    assert out["obligation"]["pending"] and not out["obligation"]["deadline_authority_verified"]
    r["input"]["position"]["quantity"] = "123456789012345678901234.123456789012345678"
    assert guard.evaluate_finance_position_guard(bind_partial(r))["observed_quantity"] == r["input"]["position"]["quantity"]


def test_partial_wrong_asset_keeps_identity_conflict_visible_without_draft():
    r = partial_example()
    r["input"]["position"]["asset"] = "OTHER"
    out = guard.evaluate_finance_position_guard(bind_partial(r))
    assert out["material_projection"]["position_asset_matches"] is False
    assert "source_conflict" in out["evidence_gaps"]
    assert out["state"] == "exit_review_required" and out["exit_draft"] is None
    out["source_projections"]["episode"]["max_hold_until"] = "2027-01-01T00:00:00Z"
    assert r["input"]["episode"]["max_hold_until"] == NOW


@pytest.mark.parametrize("field,value", [
    ("decision", {"disposition": "extend_deadline", "source_ref": None, "source_digest": None}),
    ("execution", {"disposition": "closed_verified", "source_ref": None, "source_digest": None}),
    ("evidence_gaps", []),
])
def test_partial_cannot_claim_verified_closure_or_extension(field, value):
    r = partial_example()
    r["input"][field] = value
    with pytest.raises(ValueError, match="schema admission"):
        guard.evaluate_finance_position_guard(bind_partial(r))


def test_partial_actual_stdin_and_direct_cli_and_generic_private_error(monkeypatch, capsys, tmp_path):
    r = partial_example()
    r["input"]["source_clocks"] = {"positions": NOW, "orders": NOW, "history": None}
    r["input"]["source_capture_digest"] = SCOPE
    expected = guard.evaluate_finance_position_guard(r)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(r)))
    assert run([]) == 0 and json.loads(capsys.readouterr().out) == expected
    p = tmp_path / "partial.json"
    p.write_text(json.dumps(r))
    assert run(["evaluate-position", "--input-json", str(p)]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    r["input"]["credential"] = "private-sentinel-do-not-reflect"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(r)))
    assert run([]) == 1 and "private-sentinel" not in capsys.readouterr().out


@pytest.mark.parametrize("deadline", ["2026-01-03T12:00:01Z", NOW, "2026-01-03T11:59:59Z"])
@pytest.mark.parametrize("clocks,states", [
    ({"positions": NOW, "orders": NOW, "history": NOW},
     {"positions": "available_unverified", "orders": "available_unverified", "history": "available_unverified"}),
    ({"positions": NOW, "orders": NOW, "history": "2026-01-03T12:00:01Z"},
     {"positions": "available_unverified", "orders": "available_unverified", "history": "stale_or_future"}),
    ({"positions": "2026-01-03T11:50:00Z", "orders": "2026-01-03T11:50:01Z", "history": "2026-01-03T11:50:02Z"},
     {"positions": "stale_or_future", "orders": "stale_or_future", "history": "stale_or_future"}),
    ({"positions": NOW, "orders": None, "history": NOW},
     {"positions": "available_unverified", "orders": "missing", "history": "available_unverified"}),
    ({"positions": "2026-01-03T11:58:00Z", "orders": NOW, "history": NOW},
     {"positions": "available_unverified", "orders": "available_unverified", "history": "available_unverified"}),
])
def test_capture_clocks_survive_null_position_without_displacing_deadline(deadline, clocks, states):
    r = partial_example()
    r["input"].update(position=None, source_clocks=clocks, source_capture_digest=SCOPE)
    r["input"]["episode"]["max_hold_until"] = deadline
    original = deepcopy(bind_partial(r))
    out = guard.evaluate_finance_position_guard(r)
    assert out["source_clock_projection"] == {"observed_at": clocks, "states": states, "capture_digest": SCOPE}
    assert out["material_projection"]["source_clock_states"] == states
    assert out["state"] == ("attention_required" if deadline > NOW else "exit_review_required")
    assert out["urgent"] == (deadline <= NOW)
    assert out["source_projections"]["position"] is None
    assert out["obligation"]["pending"] and not out["obligation"]["closeout_verified"]
    assert not out["position_verified"] and out["exit_draft"] is None
    assert r == original
    out["source_clock_projection"]["observed_at"]["positions"] = None
    assert r == original


def test_capture_clock_opt_in_preserves_legacy_shape_and_unknown_digest():
    r = partial_example()
    legacy = guard.evaluate_finance_position_guard(r)
    assert "source_clock_projection" not in legacy
    assert "source_clock_states" not in legacy["material_projection"]
    r["input"]["source_clocks"] = {"positions": None, "orders": None, "history": None}
    out = guard.evaluate_finance_position_guard(r)
    assert out["source_clock_projection"]["capture_digest"] is None
    assert out["source_clock_projection"]["states"] == dict.fromkeys(["positions", "orders", "history"], "missing")
    assert out["state"] == legacy["state"] and out["evidence_gaps"] == legacy["evidence_gaps"]


def test_capture_freshness_is_material_but_recapture_digest_is_not():
    r = partial_example()
    r["input"]["source_clocks"] = {"positions": NOW, "orders": NOW, "history": NOW}
    r["input"]["source_capture_digest"] = SCOPE
    before = guard.evaluate_finance_position_guard(r)
    r["input"]["source_capture_digest"] = "sha256:" + "c" * 64
    r["input"]["source_clocks"]["history"] = "2026-01-03T11:59:59Z"
    recaptured = guard.evaluate_finance_position_guard(r)
    assert before["material_digest"] == recaptured["material_digest"]
    assert before["request_digest"] != recaptured["request_digest"]
    r["input"]["source_clocks"]["history"] = "2026-01-03T11:50:00Z"
    assert guard.evaluate_finance_position_guard(r)["material_digest"] != before["material_digest"]


@pytest.mark.parametrize("fields", [
    {"source_capture_digest": SCOPE},
    {"source_clocks": {"positions": NOW, "orders": NOW}},
    {"source_clocks": {"positions": NOW, "orders": NOW, "history": "private-invalid-time"}},
    {"source_clocks": {"positions": NOW, "orders": NOW, "history": "2026-01-03T12:00:00"}},
    {"source_clocks": {"positions": NOW, "orders": NOW, "history": NOW, "first_public": NOW}},
    {"source_clocks": {"positions": NOW, "orders": NOW, "history": NOW}, "source_capture_digest": "private-invalid-digest"},
])
def test_capture_clock_shape_does_not_admit_guessed_or_private_fields(fields):
    r = partial_example()
    r["input"].update(fields)
    with pytest.raises(ValueError, match="schema admission") as error:
        guard.evaluate_finance_position_guard(r)
    assert "private-invalid" not in str(error.value)


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


@pytest.mark.parametrize("deadline,expected", [
    ("2026-01-03T12:00:01Z", "attention_required"),
    (NOW, "exit_review_required"),
    ("2026-01-03T11:59:59Z", "exit_review_required"),
])
def test_hold_deadline_is_independent_of_later_poll(deadline, expected):
    r = example()
    r["input"]["trade"]["max_hold_until"] = deadline
    receipt(r, "monitor")["data"]["next_due_at"] = "2026-01-03T12:30:00Z"
    out = evaluate(r)
    assert out["state"] == expected
    assert "monitor_misses_hold_deadline" in out["reasons"]
    assert out["monitor_projection"]["keep_monitor_open"]
    assert not any(out["effects"].values())
    if expected == "exit_review_required":
        assert "maximum_hold_reached" in out["reasons"]
        assert out["next_action"] == "prepare_human_exit_draft"
        assert out["exit_draft"]["quantity"] == "10"


def test_expiry_is_material_even_with_unchanged_price_and_position(monkeypatch):
    r = example()
    r["input"]["trade"]["max_hold_until"] = NOW
    r["input"]["decision_at"] = "2026-01-03T11:59:59Z"
    for row in r["input"]["receipts"]:
        row["observed_at"] = r["input"]["decision_at"]
    # Only the decision/read clocks advance; price, quantity and deadline stay.
    receipt(r, "monitor")["data"]["next_due_at"] = NOW
    monkeypatch.setattr(guard, "_utc_now", lambda: datetime(2026, 1, 3, 11, 59, 59, tzinfo=UTC))
    before = evaluate(r)
    r["input"]["decision_at"] = NOW
    for row in r["input"]["receipts"]:
        row["observed_at"] = NOW
    monkeypatch.setattr(guard, "_utc_now", lambda: datetime(2026, 1, 3, 12, tzinfo=UTC))
    after = evaluate(r)
    assert before["state"] == "protected_open"
    assert after["state"] == "exit_review_required"
    assert before["open_quantity"] == after["open_quantity"]
    assert before["monitor_projection"]["keep_monitor_open"]
    assert after["monitor_projection"]["keep_monitor_open"]


@pytest.mark.parametrize("missing", ["costs", "orders", "expired_monitor"])
def test_missing_verification_does_not_erase_expiry_review(missing):
    r = example()
    r["input"]["trade"]["max_hold_until"] = NOW
    if missing == "costs":
        receipt(r, "fills")["data"]["costs_complete"] = False
    elif missing == "orders":
        receipt(r, "orders")["data"]["orders"] = []
        receipt(r, "orders")["complete"] = False
    else:
        receipt(r, "monitor")["data"]["expires_at"] = NOW
    out = evaluate(r)
    assert out["state"] == "exit_review_required"
    assert out["risk_estimate"]["estimated_stop_loss_cash"] is None
    assert out["exit_draft"]["status"] == "manual_final_check_required"
    assert out["exit_draft"]["financial_authority"] == "human_final_submit"
    assert out["monitor_projection"]["keep_monitor_open"]
    assert not any(out["effects"].values())


@pytest.mark.parametrize("invalid", ["identity", "stale", "incomplete", "side", "increased"])
def test_expiry_survives_untrusted_position_but_draft_is_withheld(invalid):
    r = example()
    r["input"]["trade"]["max_hold_until"] = NOW
    p = receipt(r, "position")
    if invalid == "identity":
        p["account_scope_digest"] = "sha256:" + "b" * 64
    elif invalid == "stale":
        p["observed_at"] = "2026-01-03T11:50:00Z"
    elif invalid == "incomplete":
        p["complete"] = False
    elif invalid == "side":
        p["data"]["side"] = "short"
    else:
        p["data"]["quantity"] = "11"
    out = evaluate(r)
    assert out["state"] == "exit_review_required" and out["urgent"]
    assert "maximum_hold_reached" in out["reasons"]
    assert out["exit_draft"] is None
    assert out["monitor_projection"]["keep_monitor_open"]
    assert out["risk_estimate"]["estimated_stop_loss_cash"] is None
    assert not any(out["effects"].values())
    p.update(deepcopy(receipt(example(), "position")))
    assert evaluate(r)["exit_draft"]["quantity"] == "10"


def test_expiry_tracks_partial_exit_until_verified_flat():
    r = example()
    r["input"]["trade"]["max_hold_until"] = NOW
    # Returning a decision/draft does not change the input or discharge it.
    original = deepcopy(r)
    assert evaluate(r)["state"] == "exit_review_required"
    assert r == original
    assert evaluate(r)["state"] == "exit_review_required"
    receipt(r, "position")["data"]["quantity"] = "4"
    receipt(r, "fills")["data"]["fills"].append(
        {"fill_id": "exit-1", "role": "exit", "quantity": "6", "price": "102",
         "fee_cash": "1", "filled_at": NOW}
    )
    out = evaluate(r)
    assert out["state"] == "exit_review_required"
    assert out["exit_draft"]["quantity"] == "4"
    assert out["monitor_projection"]["keep_monitor_open"]
    receipt(r, "position")["data"]["quantity"] = "0"
    assert evaluate(r)["state"] == "closure_unverified"
    receipt(r, "orders")["data"]["orders"] = []
    receipt(r, "fills")["data"]["fills"].append(
        {"fill_id": "exit-2", "role": "exit", "quantity": "4", "price": "102",
         "fee_cash": "1", "filled_at": NOW}
    )
    assert evaluate(r)["state"] == "closed_verified"


def test_caller_admitted_new_deadline_does_not_close_open_position():
    r = example()
    r["input"]["trade"]["max_hold_until"] = NOW
    assert evaluate(r)["state"] == "exit_review_required"
    # Admission/provenance of a human extension belongs to the caller. Polling
    # alone cannot modify this frozen trade field or manufacture an extension.
    r["input"]["trade"]["max_hold_until"] = "2026-01-04T12:00:00Z"
    out = evaluate(r)
    assert out["state"] == "protected_open"
    assert out["monitor_projection"]["keep_monitor_open"]
    assert not any(out["effects"].values())


def test_expiry_cannot_accept_an_execution_authority_override():
    r = example()
    r["input"]["trade"]["max_hold_until"] = NOW
    r["input"]["trade"]["financial_authority"] = "automatic_submit"
    with pytest.raises(ValueError, match="schema admission"):
        evaluate(r)


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
