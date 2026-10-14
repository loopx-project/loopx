"""Reconcile declared quantity stocks and signed flows, never money or trades.

Source adapters own instrument, pool, version, extraction and coverage claims.
Source row order is a caller declaration, not an authenticated event clock.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any

from .cash_reconciliation import _fields, _text
from .cumulative_flow import TemporalAggregationKind
from .numeric_accuracy import NumericAccuracyState, bounded_decimal
from .period_semantics import _boundary, _digest
from .replay import canonical_sha256

FINANCE_QUANTITY_INPUT_SCHEMA_VERSION = "finance_quantity_reconciliation_input_v1"
FINANCE_QUANTITY_SCHEMA_VERSION = "finance_quantity_reconciliation_assessment_v1"
_IDENTITY_FIELDS = {"subject_ref", "instrument_ref", "ownership_scope_ref", "source_version_ref"}
_ROW_FIELDS = {"evidence_ref", "source_digest", "captured_at", "identity", "unit",
               "value", "accuracy", "position", "aggregation_kind"}


class QuantityFlowDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"


class QuantityMeasure(StrEnum):
    SHARE = "share"
    NATIVE_CONTRACT = "native_contract"
    ITEM = "item"


class QuantityReconciliationState(StrEnum):
    CONSISTENT = "consistent"
    CONFLICT = "conflict"
    INCOMPLETE = "incomplete"
    INELIGIBLE = "ineligible"


def _row(value: Any, name: str, reasons: set[str], *, flow: bool = False):
    value = _fields(value, _ROW_FIELDS | ({"direction"} if flow else set()), name)
    try:
        kind = TemporalAggregationKind(value["aggregation_kind"])
    except (TypeError, ValueError) as exc:
        raise ValueError("explicit quantity stock/flow classification required") from exc
    if kind != (TemporalAggregationKind.FLOW if flow else TemporalAggregationKind.STOCK):
        reasons.add(f"{name}_stock_flow_classification_mismatch")
    for field in ("evidence_ref",):
        if value[field] is None:
            reasons.add(f"{name}_{field}_missing")
        else:
            _text(value[field], field)
    if not _digest(value["source_digest"]):
        reasons.add(f"{name}_source_digest_missing")
    if value["captured_at"] is None:
        reasons.add(f"{name}_capture_clock_missing")
    elif not _boundary(value["captured_at"], "captured_at", explicit_time=True)["utc_boundary"]:
        reasons.add(f"{name}_capture_timezone_missing")
    identity = value["identity"]
    if identity is None:
        reasons.add(f"{name}_identity_missing")
    else:
        identity = _fields(identity, _IDENTITY_FIELDS, "quantity identity")
        for field, reference in identity.items():
            if reference is None:
                reasons.add(f"{name}_{field}_missing")
            else:
                _text(reference, field)
    unit = value["unit"]
    if unit is None:
        reasons.add(f"{name}_unit_missing")
    else:
        unit = _fields(unit, {"measure_ref", "scale"}, "quantity unit")
        try:
            QuantityMeasure(unit["measure_ref"])
        except (TypeError, ValueError) as exc:
            raise ValueError("explicit share, native_contract or item quantity measure required") from exc
        if type(unit["scale"]) is not int or unit["scale"] not in {0, 3, 6, 9}:
            raise ValueError("quantity scale must be 0, 3, 6 or 9")
    position = value["position"]
    if type(position) is not int or not 0 <= position <= 1_000_000:
        raise ValueError("quantity position requires a bounded source ordinal")
    number, accuracy = None, None
    if value["value"] is None:
        reasons.add(f"{name}_value_missing")
        if value["accuracy"] is not None:
            raise ValueError("missing quantity requires null accuracy")
    else:
        number, accuracy = bounded_decimal(value["value"], value["accuracy"], label="quantity")
        if number < 0:
            raise ValueError("quantity magnitude must be nonnegative; direction owns the sign")
        if accuracy["state"] != NumericAccuracyState.PRODUCER_DECLARED_EXACT:
            reasons.add(f"{name}_exact_accuracy_unproven")
    direction = None
    if flow:
        if value["direction"] is None:
            reasons.add(f"{name}_direction_missing")
        else:
            try:
                direction = QuantityFlowDirection(value["direction"])
            except (TypeError, ValueError) as exc:
                raise ValueError("quantity flow requires increase or decrease direction") from exc
    return value, number, accuracy, direction


def assess_quantity_reconciliation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Check opening + explicit signed flows = closing within one declared pool.

    No backwards inference of opening stock, conversion between units/classes,
    multiplicity by reporting person, cash multiplication or permission occurs.
    """
    _fields(payload, {"schema_version", "opening_stock", "closing_stock", "flows", "coverage"},
            "quantity reconciliation")
    if payload["schema_version"] != FINANCE_QUANTITY_INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported Finance quantity reconciliation input version")
    flows = payload["flows"]
    if not isinstance(flows, list) or len(flows) > 256:
        raise ValueError("quantity reconciliation requires at most 256 flows")
    coverage = _fields(payload["coverage"], {"complete", "evidence_ref", "ordering_basis"},
                       "quantity coverage")
    if type(coverage["complete"]) is not bool:
        raise ValueError("quantity coverage completeness requires a boolean declaration")
    if coverage["ordering_basis"] not in {"source_row_order", "declared_event_order"}:
        raise ValueError("explicit quantity ordering basis required")
    reasons: set[str] = set()
    if not coverage["complete"]:
        reasons.add("flow_coverage_incomplete")
    if coverage["evidence_ref"] is None:
        reasons.add("flow_coverage_evidence_missing")
    else:
        _text(coverage["evidence_ref"], "coverage evidence_ref")
    named = [("opening", payload["opening_stock"]), ("closing", payload["closing_stock"])]
    named += [(f"flow_{i}", flow) for i, flow in enumerate(flows)]
    parsed = {name: _row(row, name, reasons, flow=name.startswith("flow_")) for name, row in named}
    opening, closing = parsed["opening"][0], parsed["closing"][0]
    comparable = True
    for name, (row, *_rest) in parsed.items():
        for field in ("identity", "unit"):
            if row[field] is None or opening[field] is None or row[field] != opening[field]:
                comparable = False
                reasons.add(f"{name}_{field}_mismatch_or_unknown")
        if row["identity"] and any(v is None for v in row["identity"].values()):
            comparable = False
        if row["source_digest"] != opening["source_digest"]:
            comparable = False
            reasons.add(f"{name}_source_commitment_mismatch")
    refs = [flow["evidence_ref"] for flow in flows]
    if len(refs) != len(set(refs)):
        reasons.add("duplicate_flow_reference")
        comparable = False
    positions = [flow["position"] for flow in flows]
    if (opening["position"] >= closing["position"]
            or any(not opening["position"] < p <= closing["position"] for p in positions)
            or any(a >= b for a, b in zip(positions, positions[1:]))):
        reasons.add("flow_order_or_stock_window_invalid")
        comparable = False
    complete = coverage["complete"] and coverage["evidence_ref"] is not None
    known = all(number is not None for _row_value, number, _accuracy, _direction in parsed.values())
    directions_known = all(parsed[f"flow_{i}"][3] is not None for i in range(len(flows)))
    expected = difference = net = None
    if comparable and complete and known and directions_known:
        with localcontext() as context:
            context.prec = 256
            net_number = sum((parsed[f"flow_{i}"][1] * (
                1 if parsed[f"flow_{i}"][3] == QuantityFlowDirection.INCREASE else -1
            ) for i in range(len(flows))), start=Decimal(0))
            expected_number = parsed["opening"][1] + net_number
            difference_number = parsed["closing"][1] - expected_number
            net, expected, difference = (format(n, "f") for n in (
                net_number, expected_number, difference_number,
            ))
    if reasons:
        state = (QuantityReconciliationState.INCOMPLETE if (
            not known or not complete or not directions_known
        ) else QuantityReconciliationState.INELIGIBLE)
    else:
        state = (QuantityReconciliationState.CONSISTENT if difference_number == 0
                 else QuantityReconciliationState.CONFLICT)
        if state == QuantityReconciliationState.CONFLICT:
            reasons.add("reported_closing_quantity_conflict")
    return {
        "schema_version": FINANCE_QUANTITY_SCHEMA_VERSION,
        "input_sha256": canonical_sha256(payload), "operands": copy.deepcopy(dict(payload)),
        "state": state, "reason_codes": sorted(reasons),
        "numeric_accuracy_assessments": {name: accuracy for name, (_row_value, _number_value, accuracy, _direction) in parsed.items()},
        "candidate_net_quantity": net, "candidate_expected_closing_quantity": expected,
        "candidate_closing_difference": difference,
        "reconciled_closing_quantity": expected if state == QuantityReconciliationState.CONSISTENT else None,
        "quantity_reconciled": state == QuantityReconciliationState.CONSISTENT,
        "arithmetic_assurance": "exact_lexical_quantity_at_same_declared_scale",
        "coverage_assurance": "caller_asserted_not_authenticated",
        "ordering_assurance": "caller_asserted_not_authenticated_event_time",
        "source_evidence_authenticated": False, "financial_admission": False,
        "cash_flow_derived": False, "exact_cash": None, "trading_allowed": False,
    }
