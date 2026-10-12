from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import urlencode, urlunparse

import pytest

from loopx_finance_value_discovery.cli import run
from loopx_finance_value_discovery.dashboard import (
    build_finance_research_dashboard_packet,
)
from loopx_finance_value_discovery.funding_event import assess_funding_event_pair
from loopx_finance_value_discovery.funding_presentation import (
    build_funding_event_research_packet,
)
from loopx_finance_value_discovery.lark_projection import (
    render_decision_research_markdown,
)
from loopx_finance_value_discovery.replay import canonical_sha256


def request():
    return json.loads(
        (Path(__file__).parents[1] / "examples/funding-research-v1.json").read_text()
    )


def display(payload):
    packet = build_finance_research_dashboard_packet(payload)
    return packet, render_decision_research_markdown(
        packet["presentation_projection"]["view"]
    )


def test_complete_same_event_different_period_cash_and_original_lineage():
    payload = request()
    before = json.dumps(payload, sort_keys=True)
    packet, markdown = display(payload)
    assessment = packet["funding_assessment"]
    assert assessment == assess_funding_event_pair(payload["funding_event_input"])
    assert json.dumps(payload, sort_keys=True) == before
    assert "-0.4 SYN" in markdown
    assert "Native period relation: different" in markdown
    assert "Conditional net estimate / 条件净额: unknown (not zero)" in markdown
    for leg in payload["funding_event_input"]["legs"]:
        for key in [
            "ref",
            "digest",
            "field_ref",
            "version_ref",
            "producer_as_of",
            "capture_start",
            "capture_end",
        ]:
            assert leg["source"][key].replace("_", "\\_") in markdown
        assert leg["rate"]["original_literal"] in markdown
        assert leg["settlement"]["payment_ref"] in markdown
    view = packet["presentation_projection"]["view"]
    assert view["adjudication"]["status"] == "insufficient_evidence"
    assert not view["boundary"]["trading_allowed"]
    assert "sha256:" + canonical_sha256(assessment) in markdown
    assert view["metrics"][0]["id"] == "declared-realized-gross-cash"
    assert view["entities"] == []  # No invented scenario probabilities.


def test_forecast_never_becomes_realized_cash():
    payload = request()
    original = payload["funding_event_input"]
    original["decision_at"] = "2001-01-02T07:59:59Z"
    original["costs"] = None
    for leg in original["legs"]:
        leg["rate"]["kind"] = "forecast"
        leg["settlement"] = None
        leg["source"].update(
            producer_as_of="2001-01-02T07:59:50Z",
            capture_start="2001-01-02T07:59:51Z",
            capture_end="2001-01-02T07:59:52Z",
        )
    packet, markdown = display(payload)
    # 2 * 50 * .002 - 1 * 50 * .01 = -.3; original scale is preserved.
    assert packet["funding_assessment"]["conditional_gross_estimate"] == "-0.300"
    assert packet["funding_assessment"]["net_cash"] is None
    assert "Conditional gross estimate / 条件毛额: -0.300 SYN" in markdown
    assert "Declared realized net / 声明已实现净额: unknown (not zero)" in markdown
    assert "costs\\_missing" in markdown


def test_missing_leg_all_holds_and_zero_cost_remain_distinct():
    payload = request()
    payload["funding_event_input"]["legs"][1] = None
    payload["funding_event_input"]["costs"]["amount"] = "0"
    packet, markdown = display(payload)
    assert packet["funding_assessment"]["net_cash"] is None
    assert '"amount":"0"' in markdown
    assert "Operand: unknown (not zero)" in markdown
    for reason in packet["funding_assessment"]["reason_codes"]:
        assert reason.replace("_", "\\_") in markdown


@pytest.mark.parametrize(
    "value",
    [
        "/Users/example/private.json",
        "password: forbidden",
        "<script>wrong</script>",
        urlunparse(
            (
                "https",
                "example.org",
                "/source",
                "",
                urlencode({"token": "synthetic-only"}),
                "",
            )
        ),
        "https://private.example.org/source",
        urlunparse(
            ("https", "synthetic-user:invalid@example.org", "/source", "", "", "")
        ),
    ],
)
def test_safe_projection_rejects_private_and_markup_at_both_apis(value):
    payload = request()
    payload["funding_event_input"]["legs"][0]["source"]["ref"] = value
    for entry in [
        build_finance_research_dashboard_packet,
        build_funding_event_research_packet,
    ]:
        with pytest.raises(ValueError):
            entry(payload)


def test_new_stdin_and_existing_card_entry_and_old_arithmetic_dispatch(
    monkeypatch, capsys, tmp_path
):
    payload = request()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert run([]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet == build_finance_research_dashboard_packet(payload)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload))
    assert run(["render-lark-card", "--input-json", str(path)]) == 0
    card = json.loads(capsys.readouterr().out)
    assert card["elements"][0]["text"]["content"] == render_decision_research_markdown(
        packet["presentation_projection"]["view"]
    )
    original = payload["funding_event_input"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(original)))
    assert run([]) == 0
    assert json.loads(capsys.readouterr().out) == assess_funding_event_pair(original)
