"""Reconcile declared signed cash rows; source interpretation stays upstream.

This Finance-owned arithmetic assessment never extracts a source, authenticates
its labels, admits financial evidence, or decides whether cash is distributable.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from decimal import Decimal, ROUND_HALF_UP, localcontext
from enum import StrEnum
from typing import Any

from .numeric_accuracy import assess_numeric_accuracy
from .period_semantics import (
    FINANCE_PERIOD_COMPARISON_INPUT_SCHEMA_VERSION,
    assess_period_comparison,
)
from .replay import canonical_sha256

FINANCE_CASH_RECONCILIATION_INPUT_SCHEMA_VERSION = (
    "finance_cash_reconciliation_input_v1"
)
FINANCE_CASH_RECONCILIATION_SCHEMA_VERSION = "finance_cash_reconciliation_assessment_v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class CashMetric(StrEnum):
    OPERATING = "operating_cash_flow"
    GROSS_CAPEX = "gross_capex"
    ASSET_SALES = "asset_sale_proceeds"
    INCENTIVES = "government_incentive_proceeds"
    NET_CAPEX = "net_capex"
    ADJUSTED_FCF = "adjusted_free_cash_flow"


class CashMeasurementKind(StrEnum):
    REPORTED_FLOW = "reported_cash_flow"
    STOCK = "stock"
    COMMITMENT = "commitment"
    FORECAST = "forecast"
    DISPLAY = "rounded_display"


class CashReconciliationState(StrEnum):
    CONSISTENT = "consistent"
    CONFLICT = "conflict"
    INCOMPLETE = "incomplete"
    INELIGIBLE = "ineligible"


class CashFactState(StrEnum):
    OBSERVED = "observed"
    MISSING = "missing"
    CONFLICT = "conflict"


def _fields(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{name} has unsupported or missing fields")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} requires a bounded nonempty string")
    return value


def _unit(value: Any) -> None:
    if value is None:
        return
    unit = _fields(value, {"currency", "scale"}, "cash unit")
    _text(unit["currency"], "currency")
    if type(unit["scale"]) is not int or unit["scale"] not in {0, 3, 6, 9}:
        raise ValueError("cash unit scale must be 0, 3, 6 or 9")


def _number(value: Any, accuracy: Any) -> tuple[Decimal, dict[str, Any]]:
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("cash amount requires a bounded finite numeric string")
    assessment = assess_numeric_accuracy(value, accuracy)
    number = Decimal(value)
    if abs(number.adjusted()) > 64 or abs(number.as_tuple().exponent) > 64:
        raise ValueError("cash amount is outside the bounded decimal range")
    return number, assessment


def _encoded(value: Decimal) -> str:
    return format(value, "f")


def assess_cash_reconciliation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compare reported lexical amounts, retaining precision/period unknowns.

    All six roles and their source classification are producer declarations.
    The upstream caller must verify extraction and the current retained pin.
    A consistent sum or a valid source digest cannot authenticate those facts.
    """
    _fields(
        payload,
        {"schema_version", "subject_ref", "column_ref", "unit", "period", "facts"},
        "cash reconciliation",
    )
    if payload["schema_version"] != FINANCE_CASH_RECONCILIATION_INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported Finance cash reconciliation input version")
    _text(payload["subject_ref"], "subject_ref")
    _text(payload["column_ref"], "column_ref")
    _unit(payload["unit"])
    period = assess_period_comparison(
        {
            "schema_version": FINANCE_PERIOD_COMPARISON_INPUT_SCHEMA_VERSION,
            "left": payload["period"],
            "right": payload["period"],
            "period_intent": "same_period",
        }
    )
    source_digest = payload["period"]["source_digest"]
    facts = payload["facts"]
    if not isinstance(facts, list) or len(facts) != len(CashMetric):
        raise ValueError("cash reconciliation requires exactly six metric rows")
    rows: dict[CashMetric, dict[str, Any]] = {}
    amounts: dict[CashMetric, Decimal] = {}
    reasons: set[str] = set()
    incomplete = False
    if payload["unit"] is None:
        reasons.add("unit_missing")
    for fact in facts:
        _fields(
            fact,
            {
                "metric",
                "state",
                "measurement_kind",
                "value",
                "unit",
                "source_refs",
                "accuracy",
            },
            "cash fact",
        )
        try:
            metric = CashMetric(fact["metric"])
            kind = CashMeasurementKind(fact["measurement_kind"])
            fact_state = CashFactState(fact["state"])
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "unsupported cash metric, state or measurement kind"
            ) from exc
        if metric in rows:
            raise ValueError("cash metric must occur exactly once")
        refs = fact["source_refs"]
        if not isinstance(refs, list) or len(refs) > 2:
            raise ValueError("cash fact requires at most two source references")
        if fact_state == CashFactState.OBSERVED and len(refs) != 1:
            raise ValueError("observed cash fact requires one source reference")
        if fact_state == CashFactState.CONFLICT and len(refs) != 2:
            raise ValueError("conflicting cash fact requires two source references")
        for ref in refs:
            _fields(
                ref,
                {"digest", "locator", "column_ref", "label"},
                "cash source reference",
            )
            for field in ("locator", "column_ref", "label"):
                _text(ref[field], "source." + field)
            if not isinstance(ref["digest"], str) or not _DIGEST.fullmatch(
                ref["digest"]
            ):
                raise ValueError("cash source reference requires exact SHA256")
            if ref["digest"] != source_digest:
                reasons.add("source_digest_mismatch:" + metric)
            if ref["column_ref"] != payload["column_ref"]:
                reasons.add("source_column_mismatch:" + metric)
        _unit(fact["unit"])
        if fact["unit"] is None:
            reasons.add("unit_missing:" + metric)
        elif fact["unit"] != payload["unit"]:
            reasons.add("unit_mismatch:" + metric)
        row = copy.deepcopy(dict(fact))
        if kind != CashMeasurementKind.REPORTED_FLOW:
            reasons.add("not_reported_cash_flow:" + metric)
        if fact_state != CashFactState.OBSERVED:
            if fact["value"] is not None or fact["accuracy"] is not None:
                raise ValueError(
                    "missing/conflicting cash fact must retain null value and accuracy"
                )
            incomplete = True
            reasons.add(fact["state"] + ":" + metric)
            row["numeric_accuracy"] = None
        else:
            number, accuracy = _number(fact["value"], fact["accuracy"])
            amounts[metric] = number
            row["numeric_accuracy"] = accuracy
            if metric == CashMetric.GROSS_CAPEX and number > 0:
                reasons.add("gross_cash_outflow_sign_mismatch")
            if metric in {CashMetric.ASSET_SALES, CashMetric.INCENTIVES} and number < 0:
                reasons.add("cash_proceeds_sign_mismatch:" + metric)
        rows[metric] = row
    # Arithmetic is exact for these bounded lexical decimals; the measurement
    # accuracy remains the producer's distinct, possibly unknown declaration.
    calculation = None
    state = (
        CashReconciliationState.INCOMPLETE
        if incomplete
        else CashReconciliationState.INELIGIBLE
    )
    if not reasons:
        with localcontext() as context:
            context.prec = 512
            offsets = amounts[CashMetric.ASSET_SALES] + amounts[CashMetric.INCENTIVES]
            net = amounts[CashMetric.GROSS_CAPEX] + offsets
            adjusted = amounts[CashMetric.OPERATING] + net
            net_residual = amounts[CashMetric.NET_CAPEX] - net
            fcf_residual = amounts[CashMetric.ADJUSTED_FCF] - adjusted
            calculation = {
                "capex_cash_offsets": _encoded(offsets),
                "net_capex_calculated": _encoded(net),
                "adjusted_free_cash_flow_calculated": _encoded(adjusted),
                "operating_less_gross_capex": _encoded(
                    amounts[CashMetric.OPERATING] + amounts[CashMetric.GROSS_CAPEX]
                ),
                "net_capex_residual": _encoded(net_residual),
                "adjusted_free_cash_flow_residual": _encoded(fcf_residual),
                "billion_display": {
                    metric.value: {
                        str(places): _encoded(
                            amounts[metric]
                            .scaleb(payload["unit"]["scale"] - 9)
                            .quantize(
                                Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
                            )
                        )
                        for places in (1, 2)
                    }
                    for metric in (
                        CashMetric.OPERATING,
                        CashMetric.NET_CAPEX,
                        CashMetric.ADJUSTED_FCF,
                    )
                },
                "display_is_new_observation": False,
            }
        if net_residual:
            reasons.add("net_capex_reconciliation_mismatch")
        if fcf_residual:
            reasons.add("adjusted_fcf_reconciliation_mismatch")
        state = (
            CashReconciliationState.CONFLICT
            if reasons
            else CashReconciliationState.CONSISTENT
        )
    result = {
        "ok": True,
        "schema_version": FINANCE_CASH_RECONCILIATION_SCHEMA_VERSION,
        "subject_ref": payload["subject_ref"],
        "column_ref": payload["column_ref"],
        "unit": copy.deepcopy(payload["unit"]),
        "reconciliation_state": state.value,
        "reason_codes": sorted(reasons),
        "arithmetic_basis": "reported_lexical_values_at_declared_scale",
        "facts": [rows[metric] for metric in CashMetric],
        "calculation": calculation,
        "period_assessment": period,
        "numeric_accuracy_known": all(
            row["numeric_accuracy"] and row["numeric_accuracy"]["accuracy_known"]
            for row in rows.values()
        ),
        "source_interpretation_assurance": "caller_asserted_not_authenticated",
        "source_evidence_authenticated": False,
        "source_lifecycle_assessed": False,
        "distributable_cash_assessed": False,
        "financial_admission": False,
        "trading_allowed": False,
        "input_sha256": canonical_sha256(payload),
    }
    return result
