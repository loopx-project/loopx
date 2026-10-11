"""Derive a tail flow from source-bound cumulative and prefix declarations.

The producer owns extraction, aggregation classification, comparability and
source clocks. Exact lexical subtraction alone never qualifies a period value.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime
from decimal import localcontext
from enum import StrEnum
from typing import Any

from .cash_reconciliation import _encoded, _fields, _number
from .numeric_accuracy import NumericAccuracyState
from .period_semantics import (
    FINANCE_PERIOD_COMPARISON_INPUT_SCHEMA_VERSION,
    assess_period_comparison,
)
from .replay import canonical_sha256
from .statement_basis import _basis, _version_compatibility

FINANCE_FLOW_DIFFERENCE_INPUT_SCHEMA_VERSION = "finance_flow_difference_input_v1"
FINANCE_FLOW_DIFFERENCE_SCHEMA_VERSION = "finance_flow_difference_assessment_v1"
_PERIOD_FIELDS = {"source_digest", "context", "economic_period"}


class TemporalAggregationKind(StrEnum):
    """Local Finance declaration; classification cannot be inferred from values."""

    FLOW = "flow"
    STOCK = "stock"
    AVERAGE = "average"
    RATIO = "ratio"



def assess_flow_difference(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Subtract a prefix only after declared flow/identity/period/accuracy gates.

    This v1 deliberately supports producer-declared exact flows only. Finite
    accuracy needs a separate error-propagation policy; averages need duration
    weighting and ratios need their own numerator/denominator transformation.
    """
    _fields(
        payload,
        {"schema_version", "total", "prefix", "compatibility"},
        "flow difference",
    )
    if payload["schema_version"] != FINANCE_FLOW_DIFFERENCE_INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported Finance flow difference input version")
    operands = {
        side: _fields(
            payload[side],
            _PERIOD_FIELDS | {"statement_basis", "aggregation_kind", "accuracy"},
            "flow operand",
        )
        for side in ("total", "prefix")
    }
    period = assess_period_comparison(
        {
            "schema_version": FINANCE_PERIOD_COMPARISON_INPUT_SCHEMA_VERSION,
            "left": {key: operands["total"][key] for key in _PERIOD_FIELDS},
            "right": {key: operands["prefix"][key] for key in _PERIOD_FIELDS},
            "period_intent": "cross_period",
        }
    )
    reasons: set[str] = set(period["reason_codes"])
    basis_reasons: set[str] = set()
    bases = {
        side: _basis(operands[side]["statement_basis"], side, basis_reasons)
        for side in operands
    }
    reasons.update(basis_reasons)
    amounts, accuracies, kinds = {}, {}, {}
    for side, operand in operands.items():
        try:
            kinds[side] = TemporalAggregationKind(operand["aggregation_kind"])
        except (ValueError, TypeError) as exc:
            raise ValueError("unsupported temporal aggregation kind") from exc
        if kinds[side] != TemporalAggregationKind.FLOW:
            reasons.add(f"{side}_aggregation_is_not_flow")
        basis = bases[side]
        if basis is None or basis["value"] is None:
            if operand["accuracy"] is not None:
                raise ValueError("missing flow value requires null accuracy")
            amounts[side], accuracies[side] = None, None
        else:
            amounts[side], accuracies[side] = _number(
                basis["value"], operand["accuracy"]
            )
        if (
            accuracies[side] is None
            or accuracies[side]["state"] != NumericAccuracyState.PRODUCER_DECLARED_EXACT
        ):
            reasons.add(f"{side}_exact_accuracy_unproven")
    identity_matches = all(bases.values())
    if identity_matches:
        for field in ("subject_ref", "metric_ref", "unit"):
            if (
                bases["total"][field] is None
                or bases["total"][field] != bases["prefix"][field]
            ):
                reasons.add(f"flow_{field}_mismatch")
                identity_matches = False
    _version_compatibility(payload["compatibility"], operands, bases, reasons)

    tail = None
    left, right = period["operands"]["left"], period["operands"]["right"]
    if left["declaration_eligible"] and right["declaration_eligible"]:
        total = left["derived_economic_boundaries"]
        prefix = right["derived_economic_boundaries"]
        if total["start"]["utc_boundary"] != prefix["start"]["utc_boundary"]:
            reasons.add("cumulative_start_mismatch")
        # Canonical UTC boundaries have the same lexical shape except fractional
        # seconds, so use datetime ordering, not string ordering.
        if datetime.fromisoformat(
            prefix["end"]["utc_boundary"]
        ) >= datetime.fromisoformat(total["end"]["utc_boundary"]):
            reasons.add("prefix_end_not_before_total_end")
        if not reasons:
            tail = {
                "role": left["economic_period"]["role"],
                "start": prefix["end"]["utc_boundary"],
                "end": total["end"]["utc_boundary"],
                "boundary_convention": "start_inclusive_end_exclusive",
            }
    candidate = None
    if (
        identity_matches
        and all(kind == TemporalAggregationKind.FLOW for kind in kinds.values())
        and all(value is not None for value in amounts.values())
    ):
        # Existing bounded numbers fit in 256 significant digits for subtraction.
        with localcontext() as context:
            context.prec = 256
            candidate = _encoded(amounts["total"] - amounts["prefix"])
    eligible = not reasons
    return {
        "schema_version": FINANCE_FLOW_DIFFERENCE_SCHEMA_VERSION,
        "input_sha256": canonical_sha256(payload),
        "operands": copy.deepcopy(dict(operands)),
        "period_assessment": period,
        "numeric_accuracy_assessments": accuracies,
        "compatibility": copy.deepcopy(payload["compatibility"]),
        "compatibility_assurance": "caller_asserted_not_authenticated",
        "candidate_lexical_difference": candidate,
        "derivation_evidence_eligible": eligible,
        "derived_value": candidate if eligible else None,
        "derived_period": tail if eligible else None,
        "reason_codes": sorted(reasons),
        "arithmetic_assurance": "exact_lexical_subtraction_only",
        "source_evidence_authenticated": False,
        "source_lifecycle_assessed": False,
        "financial_admission": False,
        "trading_allowed": False,
    }
