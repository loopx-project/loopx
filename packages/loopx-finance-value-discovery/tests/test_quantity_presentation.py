from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path

import pytest

from loopx_finance_value_discovery.cli import run
from loopx_finance_value_discovery.dashboard import (
    build_finance_research_dashboard_packet,
)
from loopx_finance_value_discovery.lark_projection import (
    build_decision_research_lark_card,
    render_decision_research_markdown,
)
from loopx_finance_value_discovery.quantity_presentation import (
    build_quantity_research_packet,
)
from loopx_finance_value_discovery.quantity_reconciliation import (
    assess_quantity_reconciliation,
)
from loopx_finance_value_discovery.replay import canonical_sha256


def request():
    return json.loads(
        (Path(__file__).parents[1] / "examples/quantity-research-v1.json").read_text()
    )


def display(payload):
    packet = build_finance_research_dashboard_packet(payload)
    return packet, render_decision_research_markdown(
        packet["presentation_projection"]["view"]
    )


def test_complete_original_operands_and_clocks_and_same_owner():
    payload = request()
    before = deepcopy(payload)
    packet, text = display(payload)
    result = packet["quantity_assessment"]
    assert payload == before
    assert result == assess_quantity_reconciliation(payload["quantity_input"])
    assert result["reconciled_closing_quantity"] == "117"
    view = packet["presentation_projection"]["view"]
    assert view["identity"]["as_of"] == payload["point_in_time"]
    assert view["identity"]["evidence_cutoff"] == payload["evidence_cutoff"]
    assert view["adjudication"]["status"] == "insufficient_evidence"
    assert view["boundary"]["trading_allowed"] is False
    assert "sha256:" + canonical_sha256(result) in text
    for row in [
        payload["quantity_input"]["opening_stock"],
        payload["quantity_input"]["closing_stock"],
        *payload["quantity_input"]["flows"],
    ]:
        assert row["captured_at"] in text
        assert row["evidence_ref"] in text
        assert row["source_digest"] in text
        for reference in row["identity"].values():
            assert reference in text
    assert '"measure\\_ref":"share"' in text
    assert "cash derivation and trading: all false" in text


@pytest.mark.parametrize(
    "change,state,reason",
    [
        ("missing-opening", "incomplete", "opening_value_missing"),
        ("rounded", "ineligible", "opening_exact_accuracy_unproven"),
        ("class", "ineligible", "flow_0_identity_mismatch_or_unknown"),
        ("coverage", "incomplete", "flow_coverage_incomplete"),
        ("conflict", "conflict", "reported_closing_quantity_conflict"),
    ],
)
def test_holds_never_become_zero_qualified_or_admitted(change, state, reason):
    payload = request()
    original = payload["quantity_input"]
    if change == "missing-opening":
        original["opening_stock"].update(value=None, accuracy=None)
    elif change == "rounded":
        original["opening_stock"]["accuracy"] = {"decimals": "0"}
    elif change == "class":
        original["flows"][0]["identity"]["instrument_ref"] = "synthetic:class-b"
    elif change == "coverage":
        original["coverage"]["complete"] = False
    else:
        original["closing_stock"]["value"] = "118"
    packet, text = display(payload)
    result = packet["quantity_assessment"]
    assert result["state"] == state
    assert result["reconciled_closing_quantity"] is None
    assert "Reconciled closing / 核对期末: unknown (not zero)" in text
    assert reason.replace("_", "\\_") in text
    for flag in [
        "source_evidence_authenticated",
        "financial_admission",
        "cash_flow_derived",
        "trading_allowed",
    ]:
        assert result[flag] is False


def test_large_exact_quantity_is_preserved_below_metric_not_rounded():
    payload = request()
    original = payload["quantity_input"]
    original["opening_stock"]["value"] = "1e64"
    original["flows"][0]["value"] = "1e-64"
    original["flows"][1]["value"] = "0"
    original["closing_stock"]["value"] = "1e64"
    packet, text = display(payload)
    exact = "1" + "0" * 64 + "." + "0" * 63 + "1"
    assert packet["quantity_assessment"]["candidate_expected_closing_quantity"] == exact
    assert exact in text
    assert "See exact arithmetic below" in text
    assert packet["quantity_assessment"]["state"] == "conflict"


@pytest.mark.parametrize(
    "unsafe",
    [
        "/Users/example/private.json",
        "https://private.example.org/x",
        "https://example.org/x?token=synthetic-only",
        "<script>bad</script>",
    ],
)
def test_publication_rejects_unsafe_nested_reference(unsafe):
    payload = request()
    payload["quantity_input"]["opening_stock"]["identity"]["instrument_ref"] = unsafe
    for entry in [
        build_quantity_research_packet,
        build_finance_research_dashboard_packet,
    ]:
        with pytest.raises(ValueError):
            entry(payload)


def test_capacity_is_explicit_refusal_and_arithmetic_still_works():
    payload = request()
    original = payload["quantity_input"]
    original["flows"] = []
    for i in range(8):
        row = deepcopy(request()["quantity_input"]["flows"][0])
        row.update(evidence_ref=f"synthetic:flow-{i}", position=i + 1, value="0")
        original["flows"].append(row)
    original["closing_stock"].update(position=9, value="100")
    packet, text = display(payload)
    assert len(packet["presentation_projection"]["view"]["layers"]) == 12
    for i in range(8):
        assert f"synthetic:flow-{i}" in text
    extra = deepcopy(original["flows"][-1])
    extra.update(evidence_ref="synthetic:flow-8", position=9)
    original["flows"].append(extra)
    with pytest.raises(ValueError, match="at most 8 flows.*nothing was truncated"):
        display(payload)
    assert (
        assess_quantity_reconciliation(original)["reconciled_closing_quantity"] == "100"
    )


def test_stdin_direct_research_card_and_legacy_quantity_dispatch(
    monkeypatch, capsys, tmp_path
):
    payload = request()
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert run([]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet == build_finance_research_dashboard_packet(payload)
    assert run(["render-lark-card", "--input-json", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == build_decision_research_lark_card(
        packet["presentation_projection"]["view"]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload["quantity_input"])))
    assert run([]) == 0
    assert json.loads(capsys.readouterr().out) == assess_quantity_reconciliation(
        payload["quantity_input"]
    )
