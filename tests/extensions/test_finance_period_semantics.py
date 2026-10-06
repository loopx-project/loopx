"""Source-independent finite period baselines and parent admission failures."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "loopx-finance-value-discovery" / "src"))

from loopx_finance_value_discovery import (  # noqa: E402
    assess_period_comparison,
    assess_period_encoding,
)
from loopx_finance_value_discovery.cli import main  # noqa: E402

SOURCE = "sha256:" + "a" * 64


def operand(start="2001-01-01", end="2001-12-31", role="annual_return"):
    return dict(
        source_digest=SOURCE,
        context={"startDate": start + "Z", "endDate": end + "Z"},
        economic_period=dict(
            context_source_digest=SOURCE,
            evidence_ref="urn:synthetic:parent:period",
            role=role,
            start=start + "T00:00:00Z",
            end="2002-01-01T00:00:00Z",
            start_basis="calendar",
        ),
    )


def request():
    return dict(
        schema_version="finance_period_comparison_input_v1",
        left=operand(),
        right=operand(),
        period_intent="same_period",
    )


@pytest.mark.parametrize(
    ("context", "field", "expected"),
    [
        (
            {"startDate": "2001-05-22", "endDate": "2001-12-31"},
            "startDate",
            "2001-05-22T00:00:00",
        ),
        (
            {"startDate": "2001-05-22", "endDate": "2001-12-31"},
            "endDate",
            "2002-01-01T00:00:00",
        ),
        ({"instant": "2001-05-21"}, "instant", "2001-05-22T00:00:00"),
        ({"instant": "2001-12-31T00:00:00"}, "instant", "2001-12-31T00:00:00"),
        ({"instant": "2000-02-28"}, "instant", "2000-02-29T00:00:00"),
        (
            {"instant": "2001-12-31T23:59:59.123456Z"},
            "instant",
            "2001-12-31T23:59:59.123456",
        ),
    ],
)
def test_fixed_format_boundaries_preserve_literals_and_missing_timezone(
    context, field, expected
):
    before = copy.deepcopy(context)
    result = assess_period_encoding(context)
    assert result["context"] == context == before
    boundary = result["boundaries"][field]
    assert boundary["local_boundary"] == expected
    assert boundary["literal"] == context[field]
    if "Z" not in context[field]:
        assert boundary["timezone"] is None and boundary["utc_boundary"] is None
    assert not result["economic_periods_assessed"] and not result["trading_allowed"]


def test_date_only_one_day_zero_duration_and_absolute_offset_ordering():
    assert assess_period_encoding({"startDate": "2001-05-22", "endDate": "2001-05-22"})[
        "duration_valid"
    ]
    assert not assess_period_encoding(
        {"startDate": "2001-05-22", "endDate": "2001-05-21"}
    )["duration_valid"]
    crossed = assess_period_encoding(
        {"startDate": "2001-05-22T10:00:00+02:00", "endDate": "2001-05-22T09:00:00Z"}
    )
    assert crossed["duration_valid"] and crossed["ordering_basis"] == "absolute"
    mixed = assess_period_encoding(
        {"startDate": "2001-05-22", "endDate": "2001-05-22Z"}
    )
    assert mixed["duration_valid"] is None and not mixed["absolute_boundaries_known"]


def test_actual_valid_declaration_and_cli_retain_authenticity_boundary(
    tmp_path, capsys
):
    payload = request()
    before = copy.deepcopy(payload)
    result = assess_period_comparison(payload)
    assert payload == before and result["period_evidence_eligible"]
    assert result["economic_period_relation"] == "same"
    assert (
        not result["source_evidence_authenticated"]
        and not result["source_lifecycle_assessed"]
    )
    assert not result["financial_admission"] and not result["trading_allowed"]
    file = tmp_path / "request.json"
    file.write_text(json.dumps(payload))
    assert main(["assess-period", "--input-json", str(file)]) == 0
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda p: p["right"].update(context=None), "finite_duration_context_missing"),
        (lambda p: p["right"].update(economic_period=None), "economic_period_missing"),
        (
            lambda p: p["right"]["context"].update(
                startDate="2001-01-01", endDate="2001-12-31"
            ),
            "source_timezone_missing",
        ),
        (
            lambda p: p["right"]["economic_period"].update(role="seeding_return"),
            "economic_role_mismatch",
        ),
        (
            lambda p: p["right"]["economic_period"].update(
                start="2001-07-22T00:00:00Z"
            ),
            "economic_encoded_boundary_mismatch",
        ),
        (
            lambda p: p["right"]["economic_period"].update(
                context_source_digest="sha256:" + "b" * 64
            ),
            "context_source_binding_mismatch",
        ),
        (
            lambda p: p["right"].update(source_digest=None),
            "context_source_binding_missing",
        ),
        (
            lambda p: p["right"]["economic_period"].update(evidence_ref=None),
            "economic_period_evidence_missing",
        ),
        (
            lambda p: p["right"]["economic_period"].update(start_basis="event"),
            "event_to_duration_mapping_missing",
        ),
        (
            lambda p: p.update(period_intent="cross_period"),
            "economic_period_intent_mismatch",
        ),
    ],
)
def test_same_carrier_dates_never_fill_missing_economic_proof(change, reason):
    payload = request()
    change(payload)
    result = assess_period_comparison(payload)
    assert not result["period_evidence_eligible"] and reason in result["reason_codes"]
    assert assess_period_comparison(request())["period_evidence_eligible"]


def test_explicit_event_mapping_requires_evidence_timezone_and_matching_boundary():
    payload = request()
    for side in ["left", "right"]:
        payload[side]["economic_period"].update(
            start_basis="event",
            event_instant="2001-01-01T00:00:00Z",
            event_mapping_evidence_ref="urn:synthetic:parent:event-duration",
        )
    assert assess_period_comparison(payload)["period_evidence_eligible"]
    payload["right"]["economic_period"]["event_instant"] = "2000-12-31T00:00:00Z"
    result = assess_period_comparison(payload)
    assert (
        not result["period_evidence_eligible"]
        and "event_to_duration_boundary_mismatch" in result["reason_codes"]
    )
    payload["right"]["economic_period"]["event_instant"] = "2001-01-01T00:00:00"
    assert "event_timezone_missing" in assess_period_comparison(payload)["reason_codes"]


def test_cross_period_uses_exact_parent_role_and_absolute_boundaries():
    payload = request()
    payload["period_intent"] = "cross_period"
    payload["right"] = operand("2000-01-01", "2000-12-31")
    payload["right"]["economic_period"]["end"] = "2001-01-01T00:00:00Z"
    result = assess_period_comparison(payload)
    assert (
        result["period_evidence_eligible"]
        and result["economic_period_relation"] == "distinct"
    )
    assert not result["financial_admission"]


@pytest.mark.parametrize("field", ["period_intent", "start_basis"])
def test_structural_type_errors_are_actionable_value_errors(field):
    payload = request()
    target = payload if field == "period_intent" else payload["left"]["economic_period"]
    target[field] = []
    with pytest.raises(ValueError):
        assess_period_comparison(payload)


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"endDate": "2001-12-31"},
        {"instant": "2001-01-01", "startDate": "2001-01-01"},
        {"instant": "2001-02-29"},
        {"instant": "2001-01-01T24:00:00"},
        {"instant": "2001-01-01T00:00:00.1234567"},
        {"instant": "2001-01-01+14:01"},
        {"instant": "2001-01-01+00:60"},
        {"instant": True},
        {"instant": "9999-12-31"},
    ],
)
def test_unsupported_or_ambiguous_encodings_fail_closed(context):
    with pytest.raises(ValueError):
        assess_period_encoding(context)


def test_unknown_and_new_path_failures_do_not_change_existing_accuracy_or_input(
    tmp_path, capsys
):
    from loopx_finance_value_discovery import assess_numeric_accuracy

    assert assess_period_encoding(None)["kind"] == "unknown"
    legacy = assess_numeric_accuracy("1.0000", {"decimals": "INF"})
    bad = request()
    bad["left"]["economic_period"]["unexpected"] = True
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(bad))
    assert main(["assess-period", "--input-json", str(file)]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert assess_numeric_accuracy("1.0000", {"decimals": "INF"}) == legacy
