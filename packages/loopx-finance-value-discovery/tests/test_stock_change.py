"""Stock arithmetic never implies flow, funding or an authenticated source."""

import copy
import json
from pathlib import Path

import pytest

from loopx_finance_value_discovery import assess_stock_change
from test_period_dispatch import invoke

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/stock-change-v1.json"


def request():
    return json.loads(EXAMPLE.read_text())


def test_exact_stock_change_retains_original_clocks_and_never_derives_flow():
    payload = request()
    frozen = copy.deepcopy(payload)
    result = assess_stock_change(payload)
    assert payload == frozen
    assert result == assess_stock_change(payload)
    assert result["candidate_lexical_difference"] == result["stock_change"] == "23.25"
    assert result["change_window"] == {
        "earlier": "2002-01-01T00:00:00Z", "later": "2003-01-01T00:00:00Z",
    }
    assert result["operands"] == {side: payload[side] for side in ("later", "earlier")}
    assert result["change_evidence_eligible"] is True
    assert not result["reason_codes"]
    for field in ("cash_flow_derived", "causes_of_change_assessed",
                  "source_evidence_authenticated", "source_lifecycle_assessed",
                  "financial_admission", "trading_allowed"):
        assert result[field] is False


@pytest.mark.parametrize(
    ("case", "reason", "candidate"),
    [
        ("context", "earlier_finite_instant_context_missing", "23.25"),
        ("duration", "earlier_finite_instant_context_missing", "23.25"),
        ("point", "earlier_economic_point_missing", "23.25"),
        ("timezone", "earlier_source_timezone_missing", "23.25"),
        ("binding", "earlier_context_source_binding_mismatch", "23.25"),
        ("boundary", "earlier_economic_encoded_boundary_mismatch", "23.25"),
        ("role", "economic_role_mismatch", "23.25"),
        ("equal", "earlier_instant_not_before_later_instant", "23.25"),
        ("reversed", "earlier_instant_not_before_later_instant", "23.25"),
        ("accuracy", "earlier_exact_accuracy_unproven", "23.25"),
        ("rounded", "earlier_exact_accuracy_unproven", "23.25"),
        ("version", "source_version_compatibility_missing", "23.25"),
        ("value", "earlier_value_missing", None),
        ("subject", "stock_subject_ref_mismatch", None),
        ("metric", "stock_metric_ref_mismatch", None),
        ("unit", "stock_unit_mismatch", None),
    ],
)
def test_missing_or_changed_declarations_hold_qualified_change(case, reason, candidate):
    payload = request()
    earlier = payload["earlier"]
    if case == "context":
        earlier["context"] = None
    elif case == "duration":
        earlier["context"] = {"startDate": "2001-01-01Z", "endDate": "2001-12-31Z"}
    elif case == "point":
        earlier["point"] = None
    elif case == "timezone":
        earlier["context"]["instant"] = "2001-12-31"
    elif case == "binding":
        earlier["point"]["context_source_digest"] = "sha256:" + "c" * 64
    elif case == "boundary":
        earlier["point"]["instant"] = "2001-12-31T00:00:00Z"
    elif case == "role":
        earlier["point"]["role"] = "other"
    elif case in {"equal", "reversed"}:
        earlier["context"]["instant"] = "2002-12-31Z" if case == "equal" else "2003-12-31Z"
        earlier["point"]["instant"] = "2003-01-01T00:00:00Z" if case == "equal" else "2004-01-01T00:00:00Z"
    elif case in {"accuracy", "rounded"}:
        earlier["accuracy"] = None if case == "accuracy" else {"decimals": "0"}
    elif case == "version":
        earlier["statement_basis"]["version_ref"] = "synthetic:revised"
    elif case == "value":
        earlier["statement_basis"]["value"] = earlier["accuracy"] = None
    elif case in {"subject", "metric"}:
        earlier["statement_basis"][case + "_ref"] = "other"
    elif case == "unit":
        earlier["statement_basis"]["unit"]["scale"] = 3
    result = assess_stock_change(payload)
    assert result["candidate_lexical_difference"] == candidate
    assert result["stock_change"] is None and result["change_window"] is None
    assert not result["change_evidence_eligible"]
    assert reason in result["reason_codes"]


@pytest.mark.parametrize("kind", ["flow", "average", "ratio"])
def test_nonstock_cannot_borrow_the_same_arithmetic(kind):
    payload = request()
    payload["earlier"]["aggregation_kind"] = kind
    result = assess_stock_change(payload)
    assert result["candidate_lexical_difference"] is None
    assert result["stock_change"] is None
    assert "earlier_aggregation_is_not_stock" in result["reason_codes"]


def compatible_cross_version():
    payload = request()
    earlier = payload["earlier"]
    earlier["source_digest"] = earlier["point"]["context_source_digest"] = "sha256:" + "c" * 64
    earlier["statement_basis"]["filing_ref"] = "synthetic:original-filing"
    payload["compatibility"] = {
        "evidence_ref": "synthetic:comparable-bases",
        **{side: {
            "source_digest": payload[side]["source_digest"],
            **{key: payload[side]["statement_basis"][key]
               for key in ("filing_ref", "version_ref", "scope_ref")},
        } for side in ("later", "earlier")},
    }
    return payload


def test_original_filing_and_comparative_vintage_require_explicit_current_pins():
    payload = compatible_cross_version()
    assert assess_stock_change(payload)["stock_change"] == "23.25"
    payload["compatibility"] = None
    assert "source_version_compatibility_missing" in assess_stock_change(payload)["reason_codes"]


@pytest.mark.parametrize("field", ["source_digest", "filing_ref", "version_ref", "scope_ref"])
def test_compatibility_cannot_follow_changed_sources(field):
    payload = compatible_cross_version()
    payload["compatibility"]["earlier"][field] = "stale"
    result = assess_stock_change(payload)
    assert result["stock_change"] is None
    assert f"earlier_compatibility_{field}_mismatch" in result["reason_codes"]


def test_offsets_fractional_seconds_and_negative_change_use_absolute_ordering():
    payload = request()
    payload["later"]["context"]["instant"] = "2002-01-01T01:00:00.000001+01:00"
    payload["later"]["point"]["instant"] = "2002-01-01T00:00:00.000001Z"
    payload["later"]["statement_basis"]["value"] = "-0.05"
    payload["earlier"]["statement_basis"]["value"] = "0.02"
    assert assess_stock_change(payload)["stock_change"] == "-0.07"


@pytest.mark.parametrize("case", ["exact", "missing_context", "flow", "malformed"])
def test_direct_and_stdin_share_semantic_holds_and_recover(case):
    payload = request()
    if case == "missing_context":
        payload["later"]["context"] = None
    elif case == "flow":
        payload["later"]["aggregation_kind"] = "flow"
    elif case == "malformed":
        payload["cash_flow_derived"] = True
    direct = invoke(["assess-stock-change", "--input-json", "-"], payload)
    assert invoke([], payload) == direct
    if case == "malformed":
        assert direct[0] == 1 and direct[1]["ok"] is False
        assert invoke([], request())[1]["stock_change"] == "23.25"
    else:
        assert direct[0] == 0
        assert direct[1]["change_evidence_eligible"] is (case == "exact")


def test_decimal_difference_preserves_low_order_digits_across_supported_range():
    payload = request()
    payload["later"]["statement_basis"]["value"] = "1e64"
    payload["earlier"]["statement_basis"]["value"] = "1e-64"
    assert assess_stock_change(payload)["stock_change"] == "9" * 64 + "." + "9" * 64
