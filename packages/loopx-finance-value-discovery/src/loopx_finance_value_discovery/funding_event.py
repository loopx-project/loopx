"""Assess two declared funding payments without comparing naked rates.

Source extraction, instrument semantics and payment authentication belong to
the caller. This local Finance vocabulary adds no account or Core authority.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any

from .cash_reconciliation import _encoded, _fields, _number
from .period_semantics import _boundary, _digest, _optional_ref
from .replay import canonical_sha256

FINANCE_FUNDING_EVENT_INPUT_SCHEMA_VERSION = "finance_funding_event_pair_input_v1"
FINANCE_FUNDING_EVENT_SCHEMA_VERSION = "finance_funding_event_pair_assessment_v1"


class FundingRateKind(StrEnum):
    FORECAST = "forecast"
    REALIZED = "realized"


class FundingRateUnit(StrEnum):
    FRACTION = "fraction"
    PERCENT = "percent"
    BASIS_POINTS = "basis_points"


class FundingPaymentConvention(StrEnum):
    POSITIVE_LONG_PAYS = "positive_long_pays"
    POSITIVE_SHORT_PAYS = "positive_short_pays"


class FundingPositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class FundingMappingKind(StrEnum):
    LINEAR_QUOTE_NOTIONAL = "linear_quote_notional"
    INVERSE = "inverse"
    QUANTO = "quanto"


class FundingCostKind(StrEnum):
    CONDITIONAL = "conditional"
    REALIZED = "realized"


class FundingEventRelation(StrEnum):
    ACCRUAL_ENDS_NO_LATER_THAN_PAYMENT = "accrual_ends_no_later_than_payment"


def _enum(value: Any, owner: type[StrEnum], name: str) -> Any:
    if value is None:
        return None
    try:
        return owner(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"unsupported funding {name}") from exc


def _ref(value: Any, name: str, reasons: set[str]) -> None:
    if not _optional_ref(value):
        reasons.add(name + "_missing")


def _clock(value: Any, name: str, reasons: set[str]) -> str | None:
    if value is None:
        reasons.add(name + "_missing")
        return None
    result = _boundary(value, name, explicit_time=True)["utc_boundary"]
    if result is None:
        reasons.add(name + "_timezone_missing")
    return result


def _amount(value: Any, name: str, reasons: set[str]) -> Decimal | None:
    if value is None:
        reasons.add(name + "_missing")
        return None
    # Reuse Finance's bounded finite lexical-number owner. Exact arithmetic
    # does not infer measurement precision from the original display.
    return _number(value, None)[0]


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _leg(value: Any, decision: str) -> dict[str, Any]:
    reasons: set[str] = set()
    clocks: dict[str, Any] = {}
    if value is None:
        return {
            "eligible": False,
            "reason_codes": ["leg_missing"],
            "rate_kind": None,
            "rate_fraction": None,
            "event_identity": None,
            "period": None,
            "currency": None,
            "candidate_signed_cash": None,
            "conditional_signed_estimate": None,
            "declared_realized_cash": None,
            "normalized_clocks": {},
            "payment_ref": None,
        }
    leg = _fields(
        value,
        {"source", "instrument", "side", "rate", "calculation_basis", "settlement"},
        "funding leg",
    )
    source = _fields(
        leg["source"],
        {
            "ref",
            "digest",
            "field_ref",
            "version_ref",
            "producer_as_of",
            "capture_start",
            "capture_end",
        },
        "funding source",
    )
    for field in ("ref", "field_ref", "version_ref"):
        _ref(source[field], "source_" + field, reasons)
    if not _digest(source["digest"]):
        reasons.add("source_digest_missing")
    for field in ("producer_as_of", "capture_start", "capture_end"):
        clocks[field] = _clock(source[field], field, reasons)
    if clocks["capture_end"]:
        if _time(clocks["capture_end"]) > _time(decision):
            reasons.add("capture_after_decision")
        for field in ("producer_as_of", "capture_start"):
            if clocks[field] and _time(clocks[field]) > _time(clocks["capture_end"]):
                reasons.add(field + "_after_capture_end")

    instrument = _fields(
        leg["instrument"],
        {
            "venue_ref",
            "instrument_ref",
            "version_ref",
            "base_unit",
            "quote_currency",
            "mapping_kind",
            "evidence_ref",
        },
        "funding instrument",
    )
    for field in (
        "venue_ref",
        "instrument_ref",
        "version_ref",
        "base_unit",
        "quote_currency",
        "evidence_ref",
    ):
        _ref(instrument[field], "instrument_" + field, reasons)
    mapping = _enum(instrument["mapping_kind"], FundingMappingKind, "mapping kind")
    if mapping != FundingMappingKind.LINEAR_QUOTE_NOTIONAL:
        reasons.add("linear_quote_notional_mapping_unproven")
    side = _enum(leg["side"], FundingPositionSide, "position side")
    if side is None:
        reasons.add("position_side_missing")

    rate = _fields(
        leg["rate"],
        {
            "original_literal",
            "value",
            "unit",
            "kind",
            "period",
            "event",
            "payment_convention",
            "evidence_ref",
        },
        "funding rate",
    )
    kind = _enum(rate["kind"], FundingRateKind, "rate kind")
    unit = _enum(rate["unit"], FundingRateUnit, "rate unit")
    convention = _enum(
        rate["payment_convention"], FundingPaymentConvention, "payment convention"
    )
    for field, item in (
        ("rate_kind", kind),
        ("rate_unit", unit),
        ("payment_convention", convention),
    ):
        if item is None:
            reasons.add(field + "_missing")
    _ref(rate["evidence_ref"], "rate_evidence", reasons)
    _ref(rate["original_literal"], "rate_original_literal", reasons)
    number = _amount(rate["value"], "rate_value", reasons)
    fraction = None
    if number is not None and unit is not None:
        with localcontext() as context:
            context.prec = 512
            fraction = (
                number
                / {
                    FundingRateUnit.FRACTION: Decimal(1),
                    FundingRateUnit.PERCENT: Decimal(100),
                    FundingRateUnit.BASIS_POINTS: Decimal(10000),
                }[unit]
            )

    period = None
    if rate["period"] is None:
        reasons.add("rate_period_missing")
    else:
        raw_period = _fields(
            rate["period"],
            {"start", "end", "event_relation", "evidence_ref"},
            "funding period",
        )
        _ref(raw_period["evidence_ref"], "period_evidence", reasons)
        relation = _enum(
            raw_period["event_relation"], FundingEventRelation, "period/event relation"
        )
        if relation is None:
            reasons.add("period_event_relation_missing")
        start = _clock(raw_period["start"], "period_start", reasons)
        end = _clock(raw_period["end"], "period_end", reasons)
        if start and end:
            if _time(start) >= _time(end):
                reasons.add("rate_period_nonpositive")
            period = {"start": start, "end": end}

    event = None
    if rate["event"] is None:
        reasons.add("settlement_event_missing")
    else:
        raw_event = _fields(
            rate["event"], {"ref", "settlement_at", "evidence_ref"}, "funding event"
        )
        _ref(raw_event["ref"], "event_ref", reasons)
        _ref(raw_event["evidence_ref"], "event_evidence", reasons)
        clocks["settlement_event"] = _clock(
            raw_event["settlement_at"], "event_at", reasons
        )
        at = clocks["settlement_event"]
        if at and raw_event["ref"] and instrument["venue_ref"]:
            event = {
                "venue_ref": instrument["venue_ref"],
                "ref": raw_event["ref"],
                "settlement_at": at,
            }
        if at:
            if period and _time(period["end"]) > _time(at):
                reasons.add("accrual_ends_after_payment_event")
            if kind == FundingRateKind.FORECAST and _time(at) <= _time(decision):
                reasons.add("forecast_event_not_after_decision")
            if kind == FundingRateKind.REALIZED and _time(at) > _time(decision):
                reasons.add("realized_event_after_decision")

    quantity = price = None
    currency = None
    dimension_known = False
    if leg["calculation_basis"] is None:
        reasons.add("quantity_basis_missing")
    else:
        basis = _fields(
            leg["calculation_basis"],
            {
                "quantity",
                "quantity_unit",
                "reference_price",
                "price_base_unit",
                "currency",
                "evidence_ref",
            },
            "funding calculation basis",
        )
        _ref(basis["evidence_ref"], "quantity_basis_evidence", reasons)
        for field in ("quantity_unit", "price_base_unit", "currency"):
            _ref(basis[field], field, reasons)
        quantity = _amount(basis["quantity"], "quantity", reasons)
        price = _amount(basis["reference_price"], "reference_price", reasons)
        if quantity is not None and quantity < 0:
            raise ValueError("funding quantity must be nonnegative")
        if price is not None and price <= 0:
            raise ValueError("funding reference price must be positive")
        if (
            basis["quantity_unit"] != instrument["base_unit"]
            or basis["price_base_unit"] != instrument["base_unit"]
        ):
            reasons.add("quantity_unit_mapping_mismatch")
        if basis["currency"] != instrument["quote_currency"]:
            reasons.add("quote_currency_mapping_mismatch")
        currency = basis["currency"]
        dimension_known = bool(
            instrument["base_unit"]
            and instrument["quote_currency"]
            and basis["quantity_unit"] == instrument["base_unit"]
            and basis["price_base_unit"] == instrument["base_unit"]
            and basis["currency"] == instrument["quote_currency"]
        )

    candidate = None
    if (
        fraction is not None
        and quantity is not None
        and price is not None
        and side is not None
        and convention is not None
        and dimension_known
        and mapping == FundingMappingKind.LINEAR_QUOTE_NOTIONAL
    ):
        with localcontext() as context:
            context.prec = 512
            sign = Decimal(1) if side == FundingPositionSide.SHORT else Decimal(-1)
            if convention == FundingPaymentConvention.POSITIVE_SHORT_PAYS:
                sign = -sign
            candidate = quantity * price * fraction * sign

    realized = None
    settlement = leg["settlement"]
    if settlement is not None:
        payment = _fields(
            settlement,
            {"payment_ref", "settled_at", "amount", "currency", "evidence_ref"},
            "funding payment",
        )
        for field in ("payment_ref", "currency", "evidence_ref"):
            _ref(payment[field], "payment_" + field, reasons)
        clocks["settled_at"] = _clock(
            payment["settled_at"], "payment_settled_at", reasons
        )
        amount = _amount(payment["amount"], "payment_amount", reasons)
        if clocks["settled_at"]:
            if _time(clocks["settled_at"]) > _time(decision):
                reasons.add("payment_after_decision")
            if event and _time(clocks["settled_at"]) < _time(event["settlement_at"]):
                reasons.add("payment_before_event")
        if payment["currency"] != currency:
            reasons.add("payment_currency_mismatch")
        if candidate is not None and amount is not None and candidate != amount:
            reasons.add("declared_payment_calculation_conflict")
        if kind != FundingRateKind.REALIZED:
            reasons.add("payment_requires_realized_rate")
        realized = amount
    elif kind == FundingRateKind.REALIZED:
        reasons.add("realized_payment_missing")
    eligible = not reasons
    return {
        "eligible": eligible,
        "reason_codes": sorted(reasons),
        "rate_kind": kind,
        "rate_fraction": _encoded(fraction) if fraction is not None else None,
        "event_identity": event,
        "period": period,
        "currency": currency,
        "candidate_signed_cash": _encoded(candidate) if candidate is not None else None,
        "conditional_signed_estimate": (
            _encoded(candidate)
            if eligible and kind == FundingRateKind.FORECAST
            else None
        ),
        "declared_realized_cash": (
            _encoded(realized)
            if eligible and kind == FundingRateKind.REALIZED
            else None
        ),
        "normalized_clocks": clocks,
        "payment_ref": settlement["payment_ref"] if settlement is not None else None,
    }


def assess_funding_event_pair(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Add declared same-event cash, never infer event counts from bare rates."""
    _fields(payload, {"schema_version", "decision_at", "legs", "costs"}, "funding pair")
    if payload["schema_version"] != FINANCE_FUNDING_EVENT_INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported Finance funding event input version")
    reasons: set[str] = set()
    decision = _clock(payload["decision_at"], "decision_at", reasons)
    if decision is None:
        raise ValueError("funding decision requires an explicit zoned dateTime")
    legs = payload["legs"]
    if not isinstance(legs, list) or len(legs) != 2:
        raise ValueError("funding pair requires exactly two nullable legs")
    assessments = [_leg(leg, decision) for leg in legs]
    for index, result in enumerate(assessments):
        reasons.update(f"leg{index + 1}_{reason}" for reason in result["reason_codes"])
    events = [item["event_identity"] for item in assessments]
    periods = [item["period"] for item in assessments]
    event_relation = (
        "unknown"
        if not all(events)
        else ("same" if events[0] == events[1] else "different")
    )
    if event_relation != "same":
        reasons.add("pair_event_" + event_relation)
    if assessments[0]["currency"] is None or (
        assessments[0]["currency"] != assessments[1]["currency"]
    ):
        reasons.add("pair_currency_missing_or_mismatched")
    if assessments[0]["rate_kind"] != assessments[1]["rate_kind"]:
        reasons.add("pair_rate_kind_mismatch")
    if (
        assessments[0]["payment_ref"] is not None
        and assessments[0]["payment_ref"] == assessments[1]["payment_ref"]
        and events[0] is not None
        and events[1] is not None
        and events[0]["venue_ref"] == events[1]["venue_ref"]
    ):
        reasons.add("duplicate_payment_identity")
    pair_eligible = not reasons
    currency = assessments[0]["currency"] if pair_eligible else None
    conditional = realized = net = estimate = None
    if pair_eligible:
        with localcontext() as context:
            context.prec = 512
            if assessments[0]["rate_kind"] == FundingRateKind.FORECAST:
                conditional = sum(
                    Decimal(item["conditional_signed_estimate"]) for item in assessments
                )
            else:
                realized = sum(
                    Decimal(item["declared_realized_cash"]) for item in assessments
                )

    cost_amount = cost_kind = None
    if payload["costs"] is None:
        reasons.add("costs_missing")
    else:
        cost = _fields(
            payload["costs"],
            {"amount", "currency", "kind", "observed_at", "evidence_ref"},
            "funding costs",
        )
        _ref(cost["evidence_ref"], "cost_evidence", reasons)
        _ref(cost["currency"], "cost_currency", reasons)
        observed = _clock(cost["observed_at"], "cost_observed_at", reasons)
        if observed and _time(observed) > _time(decision):
            reasons.add("cost_after_decision")
        cost_amount = _amount(cost["amount"], "cost_amount", reasons)
        if cost_amount is not None and cost_amount < 0:
            raise ValueError("funding costs must be nonnegative")
        cost_kind = _enum(cost["kind"], FundingCostKind, "cost kind")
        if cost_kind is None:
            reasons.add("cost_kind_missing")
        if currency is not None and cost["currency"] != currency:
            reasons.add("cost_currency_mismatch")
        if realized is not None and cost_kind != FundingCostKind.REALIZED:
            reasons.add("realized_costs_unproven")
    if not reasons:
        with localcontext() as context:
            context.prec = 512
            if conditional is not None:
                estimate = conditional - cost_amount
            else:
                net = realized - cost_amount
    return {
        "ok": True,
        "schema_version": FINANCE_FUNDING_EVENT_SCHEMA_VERSION,
        "input_sha256": canonical_sha256(payload),
        "input": copy.deepcopy(dict(payload)),
        "leg_assessments": assessments,
        "settlement_event_relation": event_relation,
        "native_period_relation": "unknown"
        if not all(periods)
        else ("same" if periods[0] == periods[1] else "different"),
        "pair_operands_eligible": pair_eligible,
        "cash_currency": currency,
        "conditional_gross_estimate": _encoded(conditional)
        if conditional is not None
        else None,
        "conditional_net_estimate": _encoded(estimate)
        if estimate is not None
        else None,
        "declared_realized_gross_cash": _encoded(realized)
        if realized is not None
        else None,
        "net_cash": _encoded(net) if net is not None else None,
        "reason_codes": sorted(reasons),
        "raw_rate_comparison_performed": False,
        "arithmetic_assurance": "bounded_lexical_declared_linear_quote_notional_only",
        "source_interpretation_assurance": "caller_declared_not_authenticated",
        "source_evidence_authenticated": False,
        "settlement_authenticated": False,
        "source_lifecycle_assessed": False,
        "pit_admitted": False,
        "financial_admission": False,
        "trading_allowed": False,
        "external_reads_performed": False,
        "external_writes_performed": False,
    }
