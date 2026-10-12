"""Disclosure identity cannot be inferred from a shared label or period."""

import copy
import json
from pathlib import Path

import pytest

from loopx_finance_value_discovery.period_semantics import assess_period_comparison

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture():
    return json.loads((EXAMPLES / "period-comparison-v2.json").read_text())


def test_signed_partition_maps_original_aggregate_to_reviewed_narrow_scope():
    result = assess_period_comparison(fixture())
    assert result["period_evidence_eligible"]
    assert result["comparison_evidence_eligible"]
    basis = result["statement_basis_assessment"]
    assert basis["relation"] == "bridged"
    # Independent baseline: -200 + 30 - 170 = -340; selected other = -170.
    assert basis["bridge"]["left"]["component_sum"] == "-340"
    assert basis["bridge"]["left"]["residual"] == "0"
    assert basis["bridge"]["right"]["residual"] == "0"
    assert basis["bridge"]["left"]["target_value"] == "-170"
    assert basis["bridge"]["right"]["target_value"] == "-170"
    assert not result["source_evidence_authenticated"]
    assert not result["source_lifecycle_assessed"]
    assert not result["financial_admission"]
    assert not result["trading_allowed"]
    assert basis["bridge"]["left"]["precision_assurance"] == "unknown"


def test_same_period_does_not_join_distinct_disclosure_or_presentation_bases():
    value = fixture()
    value["basis_bridge"] = None
    result = assess_period_comparison(value)
    assert result["period_evidence_eligible"]
    assert not result["comparison_evidence_eligible"]
    assert result["statement_basis_assessment"]["relation"] == "unproven"
    assert set(result["statement_basis_assessment"]["reason_codes"]) == {
        "statement_source_digest_mismatch", "statement_filing_ref_mismatch",
        "statement_version_ref_mismatch", "statement_scope_ref_mismatch",
    }


def test_exact_declared_basis_needs_no_bridge():
    value = fixture()
    value["right"] = copy.deepcopy(value["left"])
    value["basis_bridge"] = None
    result = assess_period_comparison(value)
    assert result["comparison_evidence_eligible"]
    assert result["statement_basis_assessment"]["relation"] == "match"


@pytest.mark.parametrize("field", ["subject_ref", "metric_ref", "unit"])
def test_bridge_never_repairs_other_subject_metric_or_unit(field):
    value = fixture()
    value["right"]["statement_basis"][field] = (
        {"currency": "EUR", "scale": 3} if field == "unit" else "different"
    )
    result = assess_period_comparison(value)
    assert not result["comparison_evidence_eligible"]
    assert f"statement_{field}_mismatch" in result["statement_basis_assessment"]["reason_codes"]


@pytest.mark.parametrize("field", ["source_digest", "filing_ref", "version_ref", "scope_ref"])
def test_decomposition_must_pin_each_operand(field):
    value = fixture()
    value["basis_bridge"]["left"][field] = "wrong-pin"
    result = assess_period_comparison(value)
    assert not result["comparison_evidence_eligible"]
    assert f"left_bridge_{field}_mismatch" in result["statement_basis_assessment"]["reason_codes"]


@pytest.mark.parametrize("field", ["subject_ref", "metric_ref", "filing_ref", "version_ref",
                                  "scope_ref", "evidence_ref", "unit", "value"])
def test_unknown_basis_stays_unknown_even_when_partition_closes(field):
    value = fixture()
    value["left"]["statement_basis"][field] = None
    result = assess_period_comparison(value)
    assert not result["comparison_evidence_eligible"]
    assert f"left_{field}_missing" in result["statement_basis_assessment"]["reason_codes"]


def test_missing_target_and_nonzero_residual_are_not_filled():
    value = fixture()
    value["basis_bridge"]["left"]["components"].pop()
    result = assess_period_comparison(value)
    basis = result["statement_basis_assessment"]
    assert not result["comparison_evidence_eligible"]
    assert basis["bridge"]["left"]["residual"] == "-170"
    assert basis["bridge"]["left"]["target_value"] is None
    assert "left_bridge_partition_conflict" in basis["reason_codes"]
    assert "left_bridge_target_component_missing" in basis["reason_codes"]
    assert assess_period_comparison(fixture())["comparison_evidence_eligible"]


def test_partition_cannot_manufacture_economic_context_or_coverage():
    value = fixture()
    value["left"]["context"] = None
    result = assess_period_comparison(value)
    assert not result["period_evidence_eligible"]
    assert result["statement_basis_assessment"]["statement_basis_eligible"]
    assert not result["comparison_evidence_eligible"]
    value = fixture()
    value["basis_bridge"]["left"]["coverage_evidence_ref"] = None
    assert not assess_period_comparison(value)["comparison_evidence_eligible"]


def test_decimal_sum_is_exact_for_signed_fractional_components():
    value = fixture()
    for side in ("left", "right"):
        value[side]["statement_basis"]["value"] = "0.1"
        value["basis_bridge"][side]["components"] = [
            {"component_ref": "other", "value": "0.3"},
            {"component_ref": "offset", "value": "-0.2"},
        ]
    result = assess_period_comparison(value)
    assert result["comparison_evidence_eligible"]
    assert result["statement_basis_assessment"]["bridge"]["left"]["residual"] == "0.0"


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1e999", 3, "not-a-number"])
def test_unbounded_or_nonfinite_components_are_rejected(bad):
    value = fixture()
    value["basis_bridge"]["left"]["components"][0]["value"] = bad
    with pytest.raises(ValueError):
        assess_period_comparison(value)


def test_closed_shape_and_unique_components_are_enforced():
    value = fixture()
    value["basis_bridge"]["left"]["components"].append(
        copy.deepcopy(value["basis_bridge"]["left"]["components"][0])
    )
    with pytest.raises(ValueError, match="unique"):
        assess_period_comparison(value)
    value = fixture()
    value["left"]["statement_basis"]["first_public_at"] = "invented"
    with pytest.raises(ValueError, match="unsupported"):
        assess_period_comparison(value)


def test_v1_replay_remains_period_only_and_rejects_v2_fields():
    value = json.loads((EXAMPLES / "period-comparison-v1.json").read_text())
    result = assess_period_comparison(value)
    assert result["schema_version"] == "finance_period_comparison_assessment_v1"
    assert result["period_evidence_eligible"]
    assert "statement_basis_assessment" not in result
    assert "comparison_evidence_eligible" not in result
    value["left"]["statement_basis"] = fixture()["left"]["statement_basis"]
    with pytest.raises(ValueError):
        assess_period_comparison(value)
