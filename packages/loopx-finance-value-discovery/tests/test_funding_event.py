"""Same-event money addition is distinct from period-normalized rate comparison."""

import copy
import json
from decimal import localcontext
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from loopx_finance_value_discovery import assess_funding_event_pair
from test_period_dispatch import invoke

PACKAGE = Path(__file__).resolve().parents[1]
SCHEMAS = PACKAGE / "src/loopx_finance_value_discovery/schemas"


def request():
    return json.loads((PACKAGE / "examples/funding-event-v1.json").read_text())


def forecast():
    payload = request()
    payload["decision_at"] = "2001-01-02T07:59:00Z"
    payload["costs"].update(kind="conditional", observed_at="2001-01-02T07:58:02Z")
    for leg in payload["legs"]:
        leg["rate"]["kind"] = "forecast"
        leg["settlement"] = None
        leg["source"].update(
            producer_as_of="2001-01-02T07:58:00Z",
            capture_start="2001-01-02T07:58:01Z",
            capture_end="2001-01-02T07:58:02Z",
        )
    payload["legs"][1]["rate"]["value"] = "-0.02"
    payload["legs"][1]["rate"]["original_literal"] = "-0.02"
    return payload


def validate_schemas(payload, result):
    source = json.loads((SCHEMAS / "funding-event-input.schema.json").read_text())
    target = json.loads((SCHEMAS / "funding-event-response.schema.json").read_text())
    registry = Registry().with_resource(source["$id"], Resource.from_contents(source))
    Draft202012Validator(source).validate(payload)
    Draft202012Validator(target, registry=registry).validate(result)


def test_different_native_periods_do_not_prohibit_declared_same_event_cash():
    payload = request()
    frozen = copy.deepcopy(payload)
    result = assess_funding_event_pair(payload)
    # Independently specified: +100*.002 -100*.005 - .1 = -.4.
    assert result["net_cash"] == "-0.4"
    assert result["declared_realized_gross_cash"] == "-0.3"
    assert result["native_period_relation"] == "different"
    assert result["settlement_event_relation"] == "same"
    assert result["raw_rate_comparison_performed"] is False
    assert result["pair_operands_eligible"] is True
    assert result["conditional_net_estimate"] is None
    assert result["reason_codes"] == []
    assert payload == frozen == result["input"]
    assert result == assess_funding_event_pair(payload)
    validate_schemas(payload, result)
    for field in (
        "source_evidence_authenticated",
        "settlement_authenticated",
        "source_lifecycle_assessed",
        "pit_admitted",
        "financial_admission",
        "trading_allowed",
        "external_reads_performed",
        "external_writes_performed",
    ):
        assert result[field] is False


def test_forecast_is_a_separate_conditional_estimate_and_never_realized_cash():
    payload = forecast()
    result = assess_funding_event_pair(payload)
    assert result["conditional_gross_estimate"] == "2.200"
    assert result["conditional_net_estimate"] == "2.100"
    assert result["net_cash"] is None
    assert result["declared_realized_gross_cash"] is None
    assert (
        result["conditional_net_estimate"]
        != result["leg_assessments"][0]["candidate_signed_cash"]
    )
    validate_schemas(payload, result)


@pytest.mark.parametrize(
    "unit,value", [("fraction", "0.002"), ("percent", "0.2"), ("basis_points", "20")]
)
def test_explicit_units_preserve_original_lexemes_without_changing_cash(unit, value):
    payload = request()
    payload["legs"][0]["rate"].update(unit=unit, value=value)
    payload["legs"][0]["rate"]["original_literal"] = value
    result = assess_funding_event_pair(payload)
    assert result["input"]["legs"][0]["rate"]["value"] == value
    assert result["leg_assessments"][0]["rate_fraction"] == "0.002"
    assert result["net_cash"] == "-0.4"


def test_reversed_declared_payment_convention_preserves_direction():
    payload = forecast()
    payload["legs"][0]["rate"]["payment_convention"] = "positive_short_pays"
    result = assess_funding_event_pair(payload)
    assert result["leg_assessments"][0]["conditional_signed_estimate"] == "-0.200"
    assert result["conditional_net_estimate"] == "1.700"
    assert result["net_cash"] is None


@pytest.mark.parametrize(
    "case,reason",
    [
        ("second_leg", "leg2_leg_missing"),
        ("period", "leg1_rate_period_missing"),
        ("period_relation", "leg1_period_event_relation_missing"),
        ("event", "leg1_settlement_event_missing"),
        ("event_mismatch", "pair_event_different"),
        ("event_ref", "pair_event_different"),
        ("venue", "pair_event_different"),
        ("sign", "leg1_payment_convention_missing"),
        ("kind", "leg1_rate_kind_missing"),
        ("unit", "leg1_rate_unit_missing"),
        ("quantity", "leg1_quantity_basis_missing"),
        ("quantity_unit", "leg1_quantity_unit_mapping_mismatch"),
        ("currency", "pair_currency_missing_or_mismatched"),
        ("inverse", "leg1_linear_quote_notional_mapping_unproven"),
        ("quanto", "leg1_linear_quote_notional_mapping_unproven"),
        ("source", "leg1_source_digest_missing"),
        ("producer_clock", "leg1_producer_as_of_missing"),
        ("timezone", "leg1_period_start_timezone_missing"),
        ("period_order", "leg1_rate_period_nonpositive"),
        ("period_end", "leg1_accrual_ends_after_payment_event"),
        ("payment", "leg1_realized_payment_missing"),
        ("payment_conflict", "leg1_declared_payment_calculation_conflict"),
        ("duplicate_payment", "duplicate_payment_identity"),
        ("costs", "costs_missing"),
        ("cost_currency", "cost_currency_mismatch"),
        ("conditional_cost", "realized_costs_unproven"),
    ],
)
def test_missing_or_conflicting_fields_hold_pair_without_filling_zero(case, reason):
    payload = request()
    leg = payload["legs"][0]
    if case == "second_leg":
        payload["legs"][1] = None
    elif case == "period":
        leg["rate"]["period"] = None
    elif case == "period_relation":
        leg["rate"]["period"]["event_relation"] = None
    elif case == "event":
        leg["rate"]["event"] = None
    elif case == "event_mismatch":
        leg["rate"]["event"]["settlement_at"] = "2001-01-02T08:00:01Z"
    elif case == "event_ref":
        leg["rate"]["event"]["ref"] = "synthetic:another-event"
    elif case == "venue":
        leg["instrument"]["venue_ref"] = "synthetic:other-venue"
    elif case == "sign":
        leg["rate"]["payment_convention"] = None
    elif case in {"kind", "unit"}:
        leg["rate"][case] = None
    elif case == "quantity":
        leg["calculation_basis"] = None
    elif case == "quantity_unit":
        leg["calculation_basis"]["quantity_unit"] = "unproven"
    elif case == "currency":
        leg["instrument"]["quote_currency"] = leg["calculation_basis"]["currency"] = (
            "OTHER"
        )
        leg["settlement"]["currency"] = "OTHER"
    elif case in {"inverse", "quanto"}:
        leg["instrument"]["mapping_kind"] = case
    elif case == "source":
        leg["source"]["digest"] = None
    elif case == "producer_clock":
        leg["source"]["producer_as_of"] = None
    elif case == "timezone":
        leg["rate"]["period"]["start"] = "2001-01-02T07:00:00"
    elif case == "period_order":
        leg["rate"]["period"]["start"] = leg["rate"]["period"]["end"]
    elif case == "period_end":
        leg["rate"]["period"]["end"] = "2001-01-02T09:00:00Z"
    elif case == "payment":
        leg["settlement"] = None
    elif case == "payment_conflict":
        leg["settlement"]["amount"] = "0.3"
    elif case == "duplicate_payment":
        payload["legs"][1]["settlement"]["payment_ref"] = leg["settlement"][
            "payment_ref"
        ]
    elif case == "costs":
        payload["costs"] = None
    elif case == "cost_currency":
        payload["costs"]["currency"] = "OTHER"
    elif case == "conditional_cost":
        payload["costs"]["kind"] = "conditional"
    result = assess_funding_event_pair(payload)
    assert result["net_cash"] is None and result["conditional_net_estimate"] is None
    assert reason in result["reason_codes"]
    assert result["input"] == payload
    # A pair failure retains independent qualified leg output.
    if case in {"event_ref", "venue", "costs"}:
        assert result["leg_assessments"][1]["declared_realized_cash"] == "-0.5"
    if case in {"quantity", "quantity_unit", "inverse", "quanto"}:
        assert result["leg_assessments"][0]["candidate_signed_cash"] is None
    validate_schemas(payload, result)


@pytest.mark.parametrize("offset", ["-00:00:00.000001", "equal", "+00:00:00.000001"])
def test_forecast_event_must_be_strictly_after_absolute_decision(offset):
    payload = forecast()
    payload["decision_at"] = {
        "-00:00:00.000001": "2001-01-02T07:59:59.999999Z",
        "equal": "2001-01-02T08:00:00Z",
        "+00:00:00.000001": "2001-01-02T08:00:00.000001Z",
    }[offset]
    result = assess_funding_event_pair(payload)
    assert (result["conditional_net_estimate"] is not None) is (offset.startswith("-"))
    assert result["net_cash"] is None
    if offset != "-00:00:00.000001":
        assert "leg1_forecast_event_not_after_decision" in result["reason_codes"]


def test_equivalent_zones_and_capture_fraction_use_absolute_clock_order():
    payload = request()
    payload["legs"][1]["rate"]["event"]["settlement_at"] = "2001-01-02T09:00:00+01:00"
    assert assess_funding_event_pair(payload)["net_cash"] == "-0.4"
    payload["legs"][0]["source"]["capture_end"] = "2001-01-02T08:00:04.000001Z"
    result = assess_funding_event_pair(payload)
    assert result["net_cash"] is None
    assert "leg1_capture_after_decision" in result["reason_codes"]


def test_ambient_decimal_precision_cannot_change_declared_results():
    with localcontext() as context:
        context.prec = 2
        small = assess_funding_event_pair(request())
    with localcontext() as context:
        context.prec = 64
        large = assess_funding_event_pair(request())
    assert small == large


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1E1000"])
def test_nonfinite_or_out_of_bound_amounts_fail_without_echoing_source(bad):
    payload = request()
    payload["legs"][0]["rate"]["value"] = bad
    with pytest.raises(ValueError):
        assess_funding_event_pair(payload)


@pytest.mark.parametrize("payload_factory", [request, forecast])
def test_real_direct_and_schema_selected_stdin_share_exact_results(payload_factory):
    payload = payload_factory()
    direct = invoke(["assess-funding-event", "--input-json", "-"], payload)
    managed = invoke([], payload)
    assert direct == managed == (0, assess_funding_event_pair(payload))


def test_malformed_input_uses_existing_error_packet_then_same_process_route_recovers():
    payload = request()
    bad = dict(payload, account="must-never-be-echoed")
    direct = invoke(["assess-funding-event", "--input-json", "-"], bad)
    assert invoke([], bad) == direct
    assert direct[0] == 1 and direct[1]["ok"] is False
    assert "must-never-be-echoed" not in json.dumps(direct)
    assert invoke([], payload)[1] == assess_funding_event_pair(payload)


def test_realized_event_equality_and_explicit_zero_cost_are_not_missing():
    payload = request()
    at = "2001-01-02T08:00:00Z"
    payload["decision_at"] = at
    payload["costs"].update(amount="0", observed_at=at)
    for leg in payload["legs"]:
        leg["source"].update(producer_as_of=at, capture_start=at, capture_end=at)
        leg["settlement"]["settled_at"] = at
    result = assess_funding_event_pair(payload)
    assert result["net_cash"] == "-0.3"
    assert result["reason_codes"] == []


@pytest.mark.parametrize(
    "field,reason",
    [
        ("settled_at", "leg1_payment_after_decision"),
        ("capture_start", "leg1_capture_start_after_capture_end"),
        ("producer_as_of", "leg1_producer_as_of_after_capture_end"),
        ("cost", "cost_after_decision"),
    ],
)
def test_future_or_reversed_capture_payment_cost_clocks_hold_cash(field, reason):
    payload = request()
    after = "2001-01-02T08:00:04.000001Z"
    if field == "settled_at":
        payload["legs"][0]["settlement"][field] = after
    elif field == "cost":
        payload["costs"]["observed_at"] = after
    else:
        payload["legs"][0]["source"][field] = after
    result = assess_funding_event_pair(payload)
    assert result["net_cash"] is None
    assert reason in result["reason_codes"]


def test_original_percent_literal_is_retained_and_missing_literal_is_unknown():
    payload = request()
    payload["legs"][0]["rate"].update(
        original_literal="0.20%",
        value="0.20",
        unit="percent",
    )
    result = assess_funding_event_pair(payload)
    assert result["net_cash"] == "-0.4"
    assert result["input"]["legs"][0]["rate"]["original_literal"] == "0.20%"
    payload["legs"][0]["rate"]["original_literal"] = None
    result = assess_funding_event_pair(payload)
    assert result["net_cash"] is None
    assert "leg1_rate_original_literal_missing" in result["reason_codes"]


def test_missing_forecast_costs_preserve_gross_but_not_net():
    payload = forecast()
    payload["costs"] = None
    result = assess_funding_event_pair(payload)
    assert result["conditional_gross_estimate"] == "2.200"
    assert result["conditional_net_estimate"] is None and result["net_cash"] is None


def test_forecast_cannot_borrow_a_payment_and_event_count_is_not_inferred():
    payload = forecast()
    payload["legs"][0]["settlement"] = request()["legs"][0]["settlement"]
    result = assess_funding_event_pair(payload)
    assert result["net_cash"] is None and result["conditional_net_estimate"] is None
    assert "leg1_payment_requires_realized_rate" in result["reason_codes"]
    payload = request()
    payload["legs"][0]["rate"]["event_count"] = 8
    with pytest.raises(ValueError):
        assess_funding_event_pair(payload)
