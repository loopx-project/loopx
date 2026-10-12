"""Temporal eligibility must come from semantics, not numeric coincidences."""

import copy
import json
from pathlib import Path

import pytest

from loopx_finance_value_discovery import assess_flow_difference
from test_period_dispatch import invoke

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/flow-difference-v1.json"


def request():
    return json.loads(EXAMPLE.read_text())


def test_shared_origin_flow_produces_exact_exclusive_tail_and_keeps_lineage():
    payload = request()
    frozen = copy.deepcopy(payload)
    result = assess_flow_difference(payload)
    assert payload == frozen
    assert result == assess_flow_difference(payload)
    assert result["candidate_lexical_difference"] == result["derived_value"] == "35"
    assert result["derivation_evidence_eligible"] is True
    assert result["reason_codes"] == []
    assert result["derived_period"] == {
        "role": "cash_flow",
        "start": "2001-07-01T00:00:00Z",
        "end": "2001-10-01T00:00:00Z",
        "boundary_convention": "start_inclusive_end_exclusive",
    }
    assert result["operands"] == {
        "total": payload["total"],
        "prefix": payload["prefix"],
    }
    for key in (
        "source_evidence_authenticated",
        "source_lifecycle_assessed",
        "financial_admission",
        "trading_allowed",
    ):
        assert result[key] is False


@pytest.mark.parametrize("kind", ["stock", "average", "ratio"])
def test_nonflow_rejected_even_when_naive_difference_coincides_with_expected(kind):
    payload = request()
    # A ratio/average can coincidentally give the expected 35; that proves no rule.
    payload["total"]["aggregation_kind"] = kind
    payload["prefix"]["aggregation_kind"] = kind
    result = assess_flow_difference(payload)
    assert result["candidate_lexical_difference"] is None
    assert result["derived_value"] is None and result["derived_period"] is None
    assert "total_aggregation_is_not_flow" in result["reason_codes"]


@pytest.mark.parametrize(
    ("case", "reason", "candidate"),
    [
        ("missing_context", "finite_duration_context_missing", "35"),
        ("missing_declaration", "economic_period_missing", "35"),
        ("missing_value", "prefix_value_missing", None),
        ("finite_accuracy", "prefix_exact_accuracy_unproven", "35"),
        ("unknown_accuracy", "prefix_exact_accuracy_unproven", "35"),
        ("unit", "flow_unit_mismatch", None),
        ("subject", "flow_subject_ref_mismatch", None),
        ("metric", "flow_metric_ref_mismatch", None),
        ("origin", "cumulative_start_mismatch", "35"),
        ("reversed", "prefix_end_not_before_total_end", "35"),
        ("equal_end", "prefix_end_not_before_total_end", "35"),
        ("digest", "context_source_binding_mismatch", "35"),
        ("version", "source_version_compatibility_missing", "35"),
        ("timezone", "source_timezone_missing", "35"),
        ("role", "economic_role_mismatch", "35"),
    ],
)
def test_missing_and_incompatible_inputs_never_become_qualified_values(
    case, reason, candidate
):
    payload = request()
    prefix = payload["prefix"]
    if case == "missing_context":
        prefix["context"] = None
    elif case == "missing_declaration":
        prefix["economic_period"] = None
    elif case == "missing_value":
        prefix["statement_basis"]["value"] = prefix["accuracy"] = None
    elif case == "finite_accuracy":
        prefix["accuracy"] = {"decimals": "0"}
    elif case == "unknown_accuracy":
        prefix["accuracy"] = None
    elif case in {"subject", "metric"}:
        prefix["statement_basis"][case + "_ref"] = "other"
    elif case == "unit":
        prefix["statement_basis"]["unit"]["scale"] = 3
    elif case == "origin":
        prefix["context"]["startDate"] = "2001-02-01Z"
        prefix["economic_period"]["start"] = "2001-02-01T00:00:00Z"
    elif case in {"reversed", "equal_end"}:
        prefix["context"]["endDate"] = (
            "2001-12-31Z" if case == "reversed" else "2001-09-30Z"
        )
        prefix["economic_period"]["end"] = (
            "2002-01-01T00:00:00Z" if case == "reversed" else "2001-10-01T00:00:00Z"
        )
    elif case == "digest":
        prefix["source_digest"] = "sha256:" + "b" * 64
    elif case == "version":
        prefix["statement_basis"]["version_ref"] = "later"
    elif case == "timezone":
        prefix["context"] = {
            k: v.removesuffix("Z") for k, v in prefix["context"].items()
        }
    elif case == "role":
        prefix["economic_period"]["role"] = "other"
    result = assess_flow_difference(payload)
    assert result["candidate_lexical_difference"] == candidate
    assert result["derived_value"] is None and result["derived_period"] is None
    assert result["derivation_evidence_eligible"] is False
    assert reason in result["reason_codes"]


def compatible_cross_source():
    payload = request()
    prefix = payload["prefix"]
    prefix["source_digest"] = prefix["economic_period"]["context_source_digest"] = (
        "sha256:" + "b" * 64
    )
    prefix["statement_basis"]["filing_ref"] = "synthetic:earlier-filing"
    payload["compatibility"] = {
        "evidence_ref": "synthetic:version-compatibility",
        **{
            side: {
                "source_digest": payload[side]["source_digest"],
                **{
                    key: payload[side]["statement_basis"][key]
                    for key in ("filing_ref", "version_ref", "scope_ref")
                },
            }
            for side in ("total", "prefix")
        },
    }
    return payload


def test_cross_source_compatibility_is_explicit_and_bound_to_both_current_pins():
    payload = compatible_cross_source()
    assert assess_flow_difference(payload)["derived_value"] == "35"
    payload["compatibility"] = None
    assert (
        "source_version_compatibility_missing"
        in assess_flow_difference(payload)["reason_codes"]
    )


@pytest.mark.parametrize(
    "field", ["source_digest", "filing_ref", "version_ref", "scope_ref"]
)
def test_stale_compatibility_does_not_follow_changed_pin(field):
    payload = compatible_cross_source()
    payload["compatibility"]["prefix"][field] = "stale"
    result = assess_flow_difference(payload)
    assert result["derived_value"] is None
    assert f"prefix_compatibility_{field}_mismatch" in result["reason_codes"]


def test_offset_equivalence_and_signed_decimal_arithmetic():
    payload = request()
    payload["prefix"]["context"]["startDate"] = "2001-01-01T01:00:00+01:00"
    payload["prefix"]["context"]["endDate"] = "2001-07-01T01:00:00+01:00"
    payload["total"]["statement_basis"]["value"] = "-0.05"
    payload["prefix"]["statement_basis"]["value"] = "0.02"
    assert assess_flow_difference(payload)["derived_value"] == "-0.07"


@pytest.mark.parametrize("case", ["flow", "missing_context", "ratio", "malformed"])
def test_direct_and_schema_selected_stdin_share_semantic_refusal_and_recovery(case):
    payload = request()
    if case == "missing_context":
        payload["prefix"]["context"] = None
    elif case == "ratio":
        payload["prefix"]["aggregation_kind"] = "ratio"
    elif case == "malformed":
        payload["financial_admission"] = True
    direct = invoke(["assess-period-difference", "--input-json", "-"], payload)
    assert invoke([], payload) == direct
    if case == "malformed":
        assert direct[0] == 1 and direct[1]["ok"] is False
        assert direct[1]["external_writes_performed"] is False
        assert invoke([], request())[1]["derived_value"] == "35"
    else:
        assert direct[0] == 0
        assert direct[1]["derivation_evidence_eligible"] is (case == "flow")
