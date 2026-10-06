"""Assess a producer's numeric accuracy declaration, never financial truth.

The source adapter owns extraction, QName/context/unit/scale and source clocks.
This module only interprets ordinary numeric precision/decimals declarations.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

FINANCE_NUMERIC_ACCURACY_SCHEMA_VERSION = "finance_numeric_accuracy_assessment_v1"
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_INTEGER = re.compile(r"[+-]?[0-9]+\Z")


class NumericAccuracyState(StrEnum):
    UNKNOWN = "unknown"
    PRODUCER_DECLARED_EXACT = "producer_declared_exact"
    DECIMAL_PLACES_DECLARED = "decimal_places_declared"
    SIGNIFICANT_DIGITS_DECLARED = "significant_digits_declared"


def assess_numeric_accuracy(
    value: str, declaration: Mapping[str, str | None] | None
) -> dict[str, Any]:
    """Preserve raw attributes and infer decimals per XBRL 2.1 §4.6.4–6.

    Missing accuracy and precision=0 are valid but unknown. Conflicting or
    malformed declarations raise ValueError. INF is the producer's declaration
    about this lexical fact; it does not certify a ratio, rounding policy,
    first availability, economic equivalence, or an independently true value.
    """
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or not _NUMBER.fullmatch(value.strip())
    ):
        raise ValueError("numeric accuracy requires a finite numeric string")
    try:
        number = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError("numeric accuracy requires a finite numeric string") from exc
    if not number.is_finite():
        raise ValueError("numeric accuracy requires a finite numeric string")
    if declaration is None:
        raw: dict[str, str | None] = {}
    elif isinstance(declaration, Mapping) and set(declaration) <= {"decimals", "precision"}:
        raw = dict(declaration)
    else:
        raise ValueError("numeric accuracy declaration accepts only decimals and precision")
    parsed: dict[str, int | str] = {}
    for key, literal in raw.items():
        if literal is None:
            continue
        if not isinstance(literal, str) or len(literal) > 128:
            raise ValueError(f"{key} must be a bounded attribute string")
        token = literal.strip()
        if token == "INF":
            parsed[key] = token
        elif _INTEGER.fullmatch(token):
            integer = int(token)
            if key == "precision" and integer < 0:
                raise ValueError("precision must be non-negative or INF")
            parsed[key] = integer
        else:
            raise ValueError(f"invalid numeric accuracy {key}")
    if len(parsed) > 1:
        raise ValueError("decimals and precision must not both be declared")

    effective_decimals: int | str | None = None
    state = NumericAccuracyState.UNKNOWN
    reason = "source_accuracy_missing"
    if parsed:
        key, count = next(iter(parsed.items()))
        if count == "INF":
            state = NumericAccuracyState.PRODUCER_DECLARED_EXACT
            effective_decimals = "INF"
            reason = "producer_declared_infinite_accuracy"
        elif key == "decimals":
            state = NumericAccuracyState.DECIMAL_PLACES_DECLARED
            effective_decimals = count
            reason = "producer_declared_decimal_places"
        elif count == 0:
            reason = "precision_zero_unknown"
        else:
            state = NumericAccuracyState.SIGNIFICANT_DIGITS_DECLARED
            # adjusted() is floor(log10(abs(value))) without floating error.
            effective_decimals = "INF" if number.is_zero() else int(count) - number.adjusted() - 1
            reason = "inferred_from_declared_significant_digits"
    return {
        "schema_version": FINANCE_NUMERIC_ACCURACY_SCHEMA_VERSION,
        "value": value,
        "declaration": raw,
        "state": state.value,
        "reason_code": reason,
        "effective_decimals": effective_decimals,
        "accuracy_known": state != NumericAccuracyState.UNKNOWN,
        "independent_truth_verified": False,
        "arithmetic_identity_verified": False,
        "trading_allowed": False,
    }
