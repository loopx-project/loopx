"""Assess declared disclosure bases and signed presentation decompositions.

This is the v2 layer of the existing Finance period operation. Evidence refs,
partition coverage and component meanings remain upstream parent declarations.
Exact arithmetic proves a lexical sum, never source truth or financial utility.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any

from .cash_reconciliation import _encoded, _fields, _number, _text, _unit
from .period_semantics import (
    FINANCE_PERIOD_COMPARISON_INPUT_SCHEMA_VERSION,
    FINANCE_PERIOD_COMPARISON_INPUT_V2_SCHEMA_VERSION,
    assess_period_comparison,
)
from .replay import canonical_sha256

FINANCE_STATEMENT_COMPARISON_SCHEMA_VERSION = "finance_period_comparison_assessment_v2"
_PERIOD_FIELDS = {"source_digest", "context", "economic_period"}
_IDENTITY_FIELDS = {"subject_ref", "metric_ref", "filing_ref", "version_ref", "scope_ref"}
_BASIS_FIELDS = _IDENTITY_FIELDS | {"evidence_ref", "unit", "value"}
_PIN_FIELDS = {"source_digest", "filing_ref", "version_ref", "scope_ref"}


class StatementBasisRelation(StrEnum):
    MATCH = "match"
    BRIDGED = "bridged"
    UNPROVEN = "unproven"


def _basis(value: Any, side: str, reasons: set[str]) -> Mapping[str, Any] | None:
    if value is None:
        reasons.add(f"{side}_statement_basis_missing")
        return None
    basis = _fields(value, _BASIS_FIELDS, "statement basis")
    for field in _IDENTITY_FIELDS | {"evidence_ref"}:
        if basis[field] is None:
            reasons.add(f"{side}_{field}_missing")
        else:
            _text(basis[field], field)
    _unit(basis["unit"])
    if basis["unit"] is None:
        reasons.add(f"{side}_unit_missing")
    if basis["value"] is None:
        reasons.add(f"{side}_value_missing")
    else:
        _number(basis["value"], None)
    return basis


def _component_refs(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError(f"{name} requires 1..32 unique component references")
    refs = [_text(ref, name) for ref in value]
    if len(set(refs)) != len(refs):
        raise ValueError(f"{name} requires unique component references")
    return refs


def _decomposition(
    value: Any,
    operand: Mapping[str, Any],
    basis: Mapping[str, Any] | None,
    targets: list[str],
    side: str,
    reasons: set[str],
) -> dict[str, Any]:
    decomposition = _fields(
        value, _PIN_FIELDS | {"coverage_evidence_ref", "components"}, "decomposition"
    )
    for field in _PIN_FIELDS | {"coverage_evidence_ref"}:
        if decomposition[field] is None:
            reasons.add(f"{side}_bridge_{field}_missing")
        else:
            _text(decomposition[field], field)
    for field in _PIN_FIELDS:
        expected = operand["source_digest"] if field == "source_digest" else (
            basis[field] if basis is not None else None
        )
        if expected is None or decomposition[field] != expected:
            reasons.add(f"{side}_bridge_{field}_mismatch")
    components = decomposition["components"]
    if not isinstance(components, list) or not 1 <= len(components) <= 32:
        raise ValueError("decomposition requires 1..32 signed components")
    amounts: dict[str, Decimal | None] = {}
    for component in components:
        row = _fields(component, {"component_ref", "value"}, "signed component")
        ref = _text(row["component_ref"], "component_ref")
        if ref in amounts:
            raise ValueError("decomposition requires unique component references")
        if row["value"] is None:
            reasons.add(f"{side}_bridge_component_value_missing")
            amounts[ref] = None
        else:
            amounts[ref] = _number(row["value"], None)[0]
    if any(ref not in amounts for ref in targets):
        reasons.add(f"{side}_bridge_target_component_missing")
    # 256 digits cover the existing bounded Decimal range and at most 32 rows.
    with localcontext() as context:
        context.prec = 256
        total = None if any(v is None for v in amounts.values()) else sum(
            amounts.values(), Decimal(0)
        )
        original = None if basis is None or basis["value"] is None else _number(
            basis["value"], None
        )[0]
        residual = None if total is None or original is None else original - total
        if residual is not None and residual != 0:
            reasons.add(f"{side}_bridge_partition_conflict")
        selected = None if any(amounts.get(ref) is None for ref in targets) else sum(
            (amounts[ref] for ref in targets), Decimal(0)
        )
    return {
        "declaration": copy.deepcopy(dict(decomposition)),
        "reported_value": None if original is None else _encoded(original),
        "component_sum": None if total is None else _encoded(total),
        "residual": None if residual is None else _encoded(residual),
        "target_value": None if selected is None else _encoded(selected),
        "precision_assurance": "unknown",
    }


def assess_statement_comparison(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Require explicit identity or a source-bound, closed signed partition."""
    _fields(payload, {"schema_version", "left", "right", "period_intent", "basis_bridge"},
            "period comparison v2")
    if payload["schema_version"] != FINANCE_PERIOD_COMPARISON_INPUT_V2_SCHEMA_VERSION:
        raise ValueError("unsupported Finance statement comparison input version")
    operands = {
        side: _fields(payload[side], _PERIOD_FIELDS | {"statement_basis"}, "period operand v2")
        for side in ("left", "right")
    }
    # Reuse the existing owner without changing persisted v1 replay semantics.
    period = assess_period_comparison({
        "schema_version": FINANCE_PERIOD_COMPARISON_INPUT_SCHEMA_VERSION,
        "period_intent": payload["period_intent"],
        **{side: {key: operand[key] for key in _PERIOD_FIELDS}
           for side, operand in operands.items()},
    })
    reasons: set[str] = set()
    bases = {side: _basis(operand["statement_basis"], side, reasons)
             for side, operand in operands.items()}
    left, right = bases["left"], bases["right"]
    if left is not None and right is not None:
        for field in ("subject_ref", "metric_ref", "unit"):
            if left[field] != right[field]:
                reasons.add(f"statement_{field}_mismatch")
    bridge = payload["basis_bridge"]
    bridge_result = None
    relation = StatementBasisRelation.UNPROVEN
    if bridge is None:
        if left is not None and right is not None:
            for field in ("filing_ref", "version_ref", "scope_ref"):
                if left[field] != right[field]:
                    reasons.add(f"statement_{field}_mismatch")
            if operands["left"]["source_digest"] != operands["right"]["source_digest"]:
                reasons.add("statement_source_digest_mismatch")
        if not reasons:
            relation = StatementBasisRelation.MATCH
    else:
        bridge = _fields(bridge, {"evidence_ref", "target_scope_ref", "target_component_refs",
                                  "left", "right"}, "basis bridge")
        for field in ("evidence_ref", "target_scope_ref"):
            if bridge[field] is None:
                reasons.add(f"bridge_{field}_missing")
            else:
                _text(bridge[field], field)
        targets = _component_refs(bridge["target_component_refs"], "target_component_refs")
        bridge_result = {
            "evidence_ref": bridge["evidence_ref"],
            "target_scope_ref": bridge["target_scope_ref"],
            "target_component_refs": targets,
            **{side: _decomposition(bridge[side], operands[side], bases[side], targets,
                                    side, reasons) for side in ("left", "right")},
            "coverage_assurance": "parent_declared_not_authenticated",
            "arithmetic_assurance": "exact_lexical_sum_only",
        }
        if not reasons:
            relation = StatementBasisRelation.BRIDGED
    period["schema_version"] = FINANCE_STATEMENT_COMPARISON_SCHEMA_VERSION
    period["input_sha256"] = canonical_sha256(payload)
    period["statement_basis_assessment"] = {
        "relation": relation.value,
        "operands": copy.deepcopy(bases),
        "bridge": bridge_result,
        "statement_basis_eligible": not reasons,
        "reason_codes": sorted(reasons),
        "source_binding_assurance": "caller_asserted_not_authenticated",
    }
    period["comparison_evidence_eligible"] = period["period_evidence_eligible"] and not reasons
    return period
