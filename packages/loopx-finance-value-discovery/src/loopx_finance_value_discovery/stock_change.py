"""Compare declared stock instants without manufacturing a period flow.

Extraction, completeness, source lifecycle and financial use remain with the
source consumer. A signed stock difference says nothing about acquisitions,
funding, cash flows or the causes of a balance change.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime
from decimal import localcontext
from typing import Any

from .cash_reconciliation import _encoded, _fields, _number
from .cumulative_flow import TemporalAggregationKind
from .numeric_accuracy import NumericAccuracyState
from .period_semantics import (
    PeriodKind,
    _boundary,
    _digest,
    _optional_ref,
    _ROLE,
    assess_period_encoding,
)
from .replay import canonical_sha256
from .statement_basis import _basis, _version_compatibility

FINANCE_STOCK_CHANGE_INPUT_SCHEMA_VERSION = "finance_stock_change_input_v1"
FINANCE_STOCK_CHANGE_SCHEMA_VERSION = "finance_stock_change_assessment_v1"
_OPERAND_FIELDS = {
    "source_digest", "context", "point", "statement_basis",
    "aggregation_kind", "accuracy",
}


def _point(operand: Mapping[str, Any]) -> dict[str, Any]:
    source_known = _digest(operand["source_digest"])
    encoding = assess_period_encoding(operand["context"])
    reasons: set[str] = set()
    if encoding["kind"] != PeriodKind.INSTANT:
        reasons.add("finite_instant_context_missing")
    if not encoding["absolute_boundaries_known"]:
        reasons.add("source_timezone_missing")
    declaration = operand["point"]
    instant = None
    if declaration is None:
        reasons.add("economic_point_missing")
    else:
        declaration = _fields(
            declaration,
            {"context_source_digest", "evidence_ref", "role", "instant"},
            "economic point",
        )
        if not isinstance(declaration["role"], str) or not _ROLE.fullmatch(
            declaration["role"]
        ):
            raise ValueError("economic point role requires a bounded exact token")
        declaration_source_known = _digest(declaration["context_source_digest"])
        if (
            not source_known or not declaration_source_known
            or declaration["context_source_digest"] != operand["source_digest"]
        ):
            reasons.add("context_source_binding_mismatch")
        if not _optional_ref(declaration["evidence_ref"]):
            reasons.add("economic_point_evidence_missing")
        if declaration["instant"] is None:
            reasons.add("economic_instant_missing")
        else:
            # Explicit dateTime: a date-only column label must not invent a
            # timezone or replace the original context. Date end semantics are
            # owned solely by assess_period_encoding, as for duration replay.
            instant = _boundary(declaration["instant"], "instant", explicit_time=True)
            if not instant["utc_boundary"]:
                reasons.add("economic_timezone_missing")
            original = encoding["boundaries"].get("instant", {}).get("utc_boundary")
            if original and instant["utc_boundary"] != original:
                reasons.add("economic_encoded_boundary_mismatch")
    return {
        "encoding": encoding,
        "point": copy.deepcopy(declaration),
        "derived_instant": instant,
        "declaration_eligible": not reasons,
        "reason_codes": sorted(reasons),
        "source_binding_assurance": "caller_asserted_not_authenticated",
    }


def assess_stock_change(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stock change only for ordered, comparable, exact declarations."""
    _fields(payload, {"schema_version", "later", "earlier", "compatibility"}, "stock change")
    if payload["schema_version"] != FINANCE_STOCK_CHANGE_INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported Finance stock change input version")
    operands = {
        side: _fields(payload[side], _OPERAND_FIELDS, "stock operand")
        for side in ("later", "earlier")
    }
    points = {side: _point(operand) for side, operand in operands.items()}
    reasons: set[str] = set()
    for side, point in points.items():
        reasons.update(f"{side}_{reason}" for reason in point["reason_codes"])
    bases = {
        side: _basis(operand["statement_basis"], side, reasons)
        for side, operand in operands.items()
    }
    amounts, accuracies, kinds = {}, {}, {}
    for side, operand in operands.items():
        try:
            kinds[side] = TemporalAggregationKind(operand["aggregation_kind"])
        except (ValueError, TypeError) as exc:
            raise ValueError("unsupported temporal aggregation kind") from exc
        if kinds[side] != TemporalAggregationKind.STOCK:
            reasons.add(f"{side}_aggregation_is_not_stock")
        basis = bases[side]
        if basis is None or basis["value"] is None:
            if operand["accuracy"] is not None:
                raise ValueError("missing stock value requires null accuracy")
            amounts[side], accuracies[side] = None, None
        else:
            amounts[side], accuracies[side] = _number(basis["value"], operand["accuracy"])
        if (
            accuracies[side] is None
            or accuracies[side]["state"] != NumericAccuracyState.PRODUCER_DECLARED_EXACT
        ):
            reasons.add(f"{side}_exact_accuracy_unproven")
    identity_matches = all(bases.values())
    if identity_matches:
        for field in ("subject_ref", "metric_ref", "unit"):
            if bases["later"][field] is None or bases["later"][field] != bases["earlier"][field]:
                reasons.add(f"stock_{field}_mismatch")
                identity_matches = False
    _version_compatibility(
        payload["compatibility"], operands, bases, reasons, sides=("later", "earlier")
    )
    if all(point["point"] is not None for point in points.values()):
        if points["later"]["point"]["role"] != points["earlier"]["point"]["role"]:
            reasons.add("economic_role_mismatch")
    window = None
    if all(point["declaration_eligible"] for point in points.values()):
        later = points["later"]["derived_instant"]["utc_boundary"]
        earlier = points["earlier"]["derived_instant"]["utc_boundary"]
        if datetime.fromisoformat(earlier) >= datetime.fromisoformat(later):
            reasons.add("earlier_instant_not_before_later_instant")
        else:
            window = {"earlier": earlier, "later": later}
    candidate = None
    if (
        identity_matches
        and all(kind == TemporalAggregationKind.STOCK for kind in kinds.values())
        and all(value is not None for value in amounts.values())
    ):
        with localcontext() as context:
            context.prec = 256
            candidate = _encoded(amounts["later"] - amounts["earlier"])
    eligible = not reasons
    return {
        "schema_version": FINANCE_STOCK_CHANGE_SCHEMA_VERSION,
        "input_sha256": canonical_sha256(payload),
        "operands": copy.deepcopy(dict(operands)),
        "point_assessments": points,
        "numeric_accuracy_assessments": accuracies,
        "compatibility": copy.deepcopy(payload["compatibility"]),
        "compatibility_assurance": "caller_asserted_not_authenticated",
        "candidate_lexical_difference": candidate,
        "change_evidence_eligible": eligible,
        "stock_change": candidate if eligible else None,
        "change_window": window if eligible else None,
        "reason_codes": sorted(reasons),
        "arithmetic_assurance": "exact_lexical_subtraction_only",
        "cash_flow_derived": False,
        "causes_of_change_assessed": False,
        "source_evidence_authenticated": False,
        "source_lifecycle_assessed": False,
        "financial_admission": False,
        "trading_allowed": False,
    }
