"""Public synthetic cases for producer-declared numeric accuracy."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "loopx-finance-value-discovery" / "src"))

from loopx_finance_value_discovery.numeric_accuracy import assess_numeric_accuracy  # noqa: E402


@pytest.mark.parametrize(
    ("value", "declaration", "state", "decimals"),
    [
        ("14.5000", {"decimals": "INF"}, "producer_declared_exact", "INF"),
        ("14.5000", {"decimals": "2"}, "decimal_places_declared", 2),
        ("1400", {"decimals": "-2"}, "decimal_places_declared", -2),
        ("1.23", {"precision": "INF"}, "producer_declared_exact", "INF"),
        ("123.45", {"precision": "3"}, "significant_digits_declared", 0),
        ("-0.0123", {"precision": "3"}, "significant_digits_declared", 4),
        ("9.999e20", {"precision": "4"}, "significant_digits_declared", -17),
        ("1e-100", {"precision": "1"}, "significant_digits_declared", 100),
        ("0.00", {"precision": "3"}, "significant_digits_declared", "INF"),
        ("0.00", {"decimals": "2"}, "decimal_places_declared", 2),
        ("1.23", {"precision": "0"}, "unknown", None),
        ("1.230000", {}, "unknown", None),
        ("1.230000", None, "unknown", None),
        ("1.23", {"decimals": None}, "unknown", None),
    ],
)
def test_declared_accuracy_has_no_display_digit_or_truth_inference(value, declaration, state, decimals):
    result = assess_numeric_accuracy(value, declaration)
    assert result["state"] == state
    assert result["effective_decimals"] == decimals
    assert result["accuracy_known"] is (state != "unknown")
    assert result["independent_truth_verified"] is False
    assert result["arithmetic_identity_verified"] is False
    assert result["trading_allowed"] is False


def test_preserves_lexical_fact_and_attributes_without_mutating_input():
    declaration = {"decimals": " +02 ", "precision": None}
    before = copy.deepcopy(declaration)
    result = assess_numeric_accuracy(" +0014.5000 ", declaration)
    assert result["value"] == " +0014.5000 "
    assert result["declaration"] == before
    assert declaration == before
    assert result["effective_decimals"] == 2
    result["declaration"]["decimals"] = "9"
    assert declaration == before


@pytest.mark.parametrize(
    "declaration",
    [
        {"decimals": "INF", "precision": "INF"},
        {"decimals": "2", "precision": "0"},
        {"precision": "-1"},
        {"decimals": "2.0"},
        {"decimals": "inf"},
        {"decimals": "+INF"},
        {"decimals": ""},
        {"decimals": True},
        {"precision": 3},
        {"scale": "2"},
        {"precision": "9" * 129},
        [],
    ],
)
def test_invalid_or_conflicting_declarations_fail_closed(declaration):
    with pytest.raises(ValueError):
        assess_numeric_accuracy("1.23", declaration)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Inf", "1_000", "1,000", "", True, 1.23, "1e" + "9" * 128])
def test_non_numeric_or_non_finite_values_are_rejected(value):
    with pytest.raises(ValueError):
        assess_numeric_accuracy(value, {"decimals": "INF"})
