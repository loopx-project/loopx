from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_SRC = ROOT / "packages" / "loopx-finance-value-discovery" / "src"
sys.path.insert(0, str(EXTENSION_SRC))

from loopx_finance_value_discovery.presentation_view import (  # noqa: E402
    DECISION_RESEARCH_VIEW_SCHEMA_VERSION,
    validate_decision_research_view,
)
from loopx_finance_value_discovery.lark_projection import (  # noqa: E402
    build_decision_research_lark_card,
    build_source_period_metrics_lark_card,
)


def _valid_view() -> dict[str, object]:
    return {
        "identity": {
            "title": "Synthetic Technology Research",
            "subtitle": "Evidence-gated decision review",
            "as_of": "2026-01-15T12:00:00+00:00",
            "evidence_cutoff": "2026-01-15",
        },
        "adjudication": {
            "status": "insufficient_evidence",
            "label": "Insufficient Evidence",
            "summary": "The frozen gates do not yet support a selected conclusion.",
            "confidence": "medium",
        },
        "metrics": [
            {
                "id": "validated-alpha",
                "label": "Validated alpha",
                "value": "0",
                "detail": "No company-specific residual passed every frozen gate.",
                "tone": "warning",
            },
            {
                "id": "method-state",
                "label": "Method state",
                "value": "unchanged",
                "detail": "The active method was not promoted or replaced.",
                "tone": "neutral",
            },
        ],
        "dashboard_summaries": [
            {
                "id": "adjudication-summary",
                "label": "Current adjudication",
                "title": "Evidence remains insufficient",
                "summary": "Continue monitoring frozen event gates.",
                "tone": "warning",
                "destination_anchor": "executive-adjudication",
            }
        ],
        "layers": [
            {
                "id": "beta",
                "order": 1,
                "label": "Beta",
                "status": "supported",
                "summary": "Discount-rate exposure explains part of the move.",
                "evidence_points": [
                    "Broad peer dispersion remains limited.",
                    "Rate sensitivity is visible across the synthetic group.",
                ],
            },
            {
                "id": "residual-alpha",
                "order": 4,
                "label": "Residual alpha",
                "status": "rejected",
                "summary": "Validated company alpha = 0.",
                "evidence_points": [
                    "The candidate did not pass persistence controls.",
                    "Counterevidence remains unresolved.",
                ],
            },
        ],
        "entities": [
            {
                "entity_id": "synthetic-cloud",
                "symbol": "SYN",
                "display_name": "Synthetic Cloud",
                "classification": "Watchlist",
                "status": "insufficient_evidence",
                "confidence": "medium",
                "inference": "A quality business is not yet a validated mispricing.",
                "observations": [
                    {
                        "id": "observation-range",
                        "label": "Observation range",
                        "kind": "observation_range",
                        "value": "90-100 synthetic units",
                        "as_of": "2026-01-15T12:00:00+00:00",
                        "source_ref": "filing:syn-q4",
                        "source_type": "company_filing",
                        "confidence": "high",
                        "invalidation": "A verified close below 90 with worsening fundamentals.",
                    }
                ],
                "scenario_estimates": [
                    {
                        "scenario": "bull",
                        "label": "Bull scenario estimate",
                        "value": "140 synthetic units",
                        "horizon": "24 months",
                        "probability": 0.25,
                        "assumptions": [
                            "Growth reaccelerates.",
                            "Free cash flow conversion improves.",
                        ],
                    },
                    {
                        "scenario": "base",
                        "label": "Base scenario estimate",
                        "value": "112 synthetic units",
                        "horizon": "24 months",
                        "probability": 0.5,
                        "assumptions": [
                            "Growth remains durable.",
                            "The valuation multiple is stable.",
                        ],
                    },
                    {
                        "scenario": "bear",
                        "label": "Bear scenario estimate",
                        "value": "72 synthetic units",
                        "horizon": "24 months",
                        "probability": 0.25,
                        "assumptions": [
                            "Growth slows.",
                            "The valuation multiple compresses.",
                        ],
                    },
                ],
                "counterevidence": [
                    "Capital intensity may remain above the frozen assumption."
                ],
                "thesis_breakers": [
                    "Two consecutive periods of slowing growth and weaker cash conversion."
                ],
                "next_events": [
                    "Next official earnings release.",
                    "Updated capital expenditure guidance.",
                ],
            }
        ],
        "research_ledger": [
            {
                "case_id": "case-synthetic-cloud",
                "label": "Synthetic Cloud residual-alpha case",
                "gate_states": [
                    {
                        "gate_id": "de-beta",
                        "label": "De-beta control",
                        "status": "passed",
                        "summary": "The peer control was available.",
                    },
                    {
                        "gate_id": "persistence",
                        "label": "Persistence",
                        "status": "failed",
                        "summary": "The residual did not persist.",
                    },
                ],
                "decision": "rejected",
                "summary": "The company-specific alpha claim was rejected.",
                "evidence_refs": ["filing:syn-q4", "market:synthetic-peer-control"],
            }
        ],
        "artifacts": [
            {
                "artifact_id": "synthetic-research-packet",
                "kind": "research_packet",
                "label": "Synthetic research packet",
                "summary": "Frozen adjudication, scenarios, and evidence lineage.",
                "artifact_ref": "artifact:synthetic-research-packet",
                "evidence_refs": [
                    "filing:syn-q4",
                    "market:synthetic-peer-control",
                ],
            }
        ],
        "event_gates": [
            {
                "event_id": "E1",
                "label": "Synthetic cloud earnings",
                "status": "pending",
                "observation_window": "Next official reporting window",
                "frozen_hypothesis": "Returns depend on monetization, not capex alone.",
                "observables": [
                    "Cloud growth versus frozen guidance.",
                    "Capital expenditure and free cash flow direction.",
                ],
                "current_evidence": [
                    "No official result was available at the evidence cutoff."
                ],
                "supports": [
                    "Growth above the frozen range with stable cash conversion."
                ],
                "refutes": [
                    "Higher capital expenditure without measurable cloud return."
                ],
                "thesis_breakers": [
                    "Official guidance shows deteriorating returns on investment."
                ],
                "next_review": "After the official filing is available.",
            }
        ],
        "method_state": {
            "revision": "candidate-v1",
            "lifecycle_state": "active_method_unchanged",
            "active_method_changed": False,
            "summary": "Active method unchanged.",
        },
        "boundary": {
            "research_aid_only": True,
            "investment_advice": False,
            "trading_allowed": False,
            "raw_provider_payload_recorded": False,
            "private_source_content_read": False,
        },
    }


def test_finance_view_schema_version_is_stable() -> None:
    assert DECISION_RESEARCH_VIEW_SCHEMA_VERSION == "decision_research_dashboard_v0"


def _source_period_metric() -> dict[str, object]:
    return {
        "metric_id": "synthetic-parent-fees",
        "label": "Synthetic parent fees",
        "event_namespace": "synthetic.period.metric",
        "event_id": "parent-fees-20260115",
        "event_at": "2026-01-15T12:00:00Z",
        "instrument_id": "SYNTH-USD",
        "scope_id": "synthetic-scope",
        "period_start": "2026-01-15",
        "period_end": "2026-01-15",
        "source_state": "ok",
        "value": 15.0,
        "unit": "USD",
        "metric_basis": "period_estimate",
        "metric_semantics": "generic",
        "value_origin": "source_reported",
        "value_precision": "rounded",
        "observation_authority": "source_reported_rounded",
        "sign_basis": "not_signed",
        "fee_inclusion": "not_applicable",
        "account_scope": "not_applicable",
        "account_value_role": "not_applicable",
        "includes_isolated_margin": False,
        "expected_components": ["primary", "secondary"],
        "observed_components": ["primary"],
        "double_counted_components": [],
        "numerator_scope": ["primary"],
        "denominator_scope": [],
        "lineage_id": "synthetic-upstream-day",
        "source_ref": "source:synthetic-parent-day",
        "methodology_state": "declared_only",
        "anomaly_state": "unverified",
    }


def _spot_market_identity() -> dict[str, object]:
    return {
        "pairs": [
            {
                "name": "SYNTH-SPOT",
                "asset_indexes": [3, 0],
                "is_canonical": False,
                "source_ref": "source:synthetic-spot-pair",
            }
        ],
        "tokens": [
            {"index": 0, "symbol": "USDC", "source_ref": "source:synthetic-usdc"},
            {"index": 3, "symbol": "SYN", "source_ref": "source:synthetic-syn"},
        ],
        "contexts": [
            {
                "coin": "SYNTH-SPOT",
                "observed_at": "2026-01-15T12:00:00Z",
                "mark_price": None,
                "source_ref": "source:synthetic-spot-context",
            }
        ],
    }


def test_decision_research_view_projects_period_metrics_idempotently() -> None:
    view = _valid_view()
    view["source_period_metrics"] = [_source_period_metric()]

    validated = validate_decision_research_view(view)
    metric = validated["source_period_metrics"][0]
    assert metric["coverage_state"] == "partial"
    assert metric["missing_components"] == ["secondary"]
    assert metric["value"] == 15.0
    assert metric["ready_eligible"] is False
    assert validate_decision_research_view(validated) == validated


def test_lark_card_consumes_the_same_period_metric_projection() -> None:
    view = _valid_view()
    view["source_period_metrics"] = [_source_period_metric()]

    card = build_source_period_metrics_lark_card(view)
    markdown = card["elements"][0]["text"]["content"]

    assert card["header"]["title"]["content"] == ("Finance source-period evidence")
    assert "missing (not zero)" not in markdown
    assert "Coverage: `partial`" in markdown
    assert "Missing components: secondary" in markdown
    assert "never grant ready" in markdown


def test_lark_card_calls_missing_period_evidence_missing_not_zero() -> None:
    view = _valid_view()
    metric = _source_period_metric()
    metric.update(value=None, observed_components=[], numerator_scope=[])
    view["source_period_metrics"] = [metric]

    card = build_source_period_metrics_lark_card(view)
    markdown = card["elements"][0]["text"]["content"]

    assert "missing (not zero)" in markdown


def test_research_card_without_period_metrics_keeps_review_basis() -> None:
    view = _valid_view()
    view["metrics"].append({
        "id": "supply", "label": "Reported supply", "value": "123456789.1234567891 SYN",
        "detail": "No source as-of height was provided; not a flow measurement.", "tone": "warning",
    })
    frozen = deepcopy(view)
    card = build_decision_research_lark_card(view)
    markdown = card["elements"][0]["text"]["content"]
    assert "Insufficient Evidence" in markdown
    assert "2026-01-15T12:00:00+00:00" in markdown
    assert "123456789.1234567891 SYN" in markdown
    assert "not a flow measurement" in markdown
    assert "Counterevidence" in markdown and "Capital intensity" in markdown
    assert "Refutes" in markdown and "Higher capital expenditure" in markdown
    assert "Next review: After the official filing" in markdown
    assert "artifact:synthetic-research-packet" in markdown
    assert "active method changed: false" in markdown
    assert "trading allowed: false" in markdown
    assert "No source-period metrics" in markdown
    assert "truncated" not in markdown
    assert view == frozen


def test_research_card_does_not_silently_cut_counterevidence_for_capacity() -> None:
    view = _valid_view()
    original = view["entities"][0]
    view["entities"] = []
    for index in range(3):
        entity = deepcopy(original)
        entity["entity_id"] = f"synthetic-{index}"
        entity["counterevidence"] = ["Unknown source boundary " * 24] * 12
        view["entities"].append(entity)
    validate_decision_research_view(view)
    with pytest.raises(ValueError, match="exceeds card capacity"):
        build_decision_research_lark_card(view)


def test_period_card_keeps_source_numeric_precision() -> None:
    view = _valid_view()
    metric = _source_period_metric()
    metric["value"] = 123456789.123456
    view["source_period_metrics"] = [metric]
    view["spot_market_identity"] = _spot_market_identity()
    view["spot_market_identity"]["contexts"][0]["mark_price"] = 12.123456789
    card = build_decision_research_lark_card(view)
    assert "123456789.123456 USD" in card["elements"][0]["text"]["content"]
    assert "12.123456789" in card["elements"][0]["text"]["content"]


def test_lark_card_escapes_dynamic_markdown_without_changing_the_view() -> None:
    view = _valid_view()
    metric = _source_period_metric()
    metric["label"] = "Synthetic\n**READY** [approve](https://example.com)"
    metric["unit"] = "USD_`spoof`"
    view["source_period_metrics"] = [metric]

    validated = validate_decision_research_view(view)
    card = build_source_period_metrics_lark_card(view)
    markdown = card["elements"][0]["text"]["content"]

    assert validated["source_period_metrics"][0]["label"] == metric["label"]
    assert "\n**READY**" not in markdown
    assert "\\*\\*READY\\*\\*" in markdown
    assert "[approve](https://example.com)" not in markdown
    assert "USD\\_\\`spoof\\`" in markdown


def test_dashboard_view_and_lark_share_spot_identity_join() -> None:
    view = _valid_view()
    view["spot_market_identity"] = _spot_market_identity()

    validated = validate_decision_research_view(view)
    market = validated["spot_market_identity"]["markets"][0]
    assert market["base_asset"] == {"index": 3, "symbol": "SYN"}
    assert market["quote_asset"] == {"index": 0, "symbol": "USDC"}
    assert market["canonicality"] == "noncanonical_name"
    assert market["backing_inference"] == "not_inferred"
    assert validate_decision_research_view(validated) == validated

    card = build_source_period_metrics_lark_card(view)
    markdown = card["elements"][0]["text"]["content"]
    assert "Spot identity joins" in markdown
    assert "SYN / USDC" in markdown
    assert "missing (not zero)" in markdown
    assert "noncanonical_name" in markdown


def test_decision_research_view_preserves_strict_research_truth() -> None:
    validated = validate_decision_research_view(_valid_view())

    assert validated["adjudication"]["status"] == "insufficient_evidence"
    assert validated["metrics"][0]["value"] == "0"
    assert validated["metrics"][1]["value"] == "unchanged"
    assert validated["layers"][1]["status"] == "rejected"
    assert validated["research_ledger"][0]["decision"] == "rejected"
    assert validated["artifacts"][0]["kind"] == "research_packet"
    assert validated["artifacts"][0]["artifact_ref"] == (
        "artifact:synthetic-research-packet"
    )
    assert [
        item["probability"] for item in validated["entities"][0]["scenario_estimates"]
    ] == [
        0.25,
        0.5,
        0.25,
    ]
    assert validated["boundary"]["trading_allowed"] is False


def test_decision_research_view_defaults_missing_artifacts_to_empty() -> None:
    view = _valid_view()
    del view["artifacts"]

    validated = validate_decision_research_view(view)

    assert validated["artifacts"] == []


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda view: view.update({"unsupported": "field"}),
            "unsupported keys",
        ),
        (
            lambda view: view["identity"].update({"as_of": "not-a-timestamp"}),
            "as_of",
        ),
        (
            lambda view: view["entities"][0]["observations"][0].update(
                {"invalidation": ""}
            ),
            "invalidation",
        ),
        (
            lambda view: view["entities"][0]["scenario_estimates"][0].update(
                {"assumptions": []}
            ),
            "assumptions",
        ),
        (
            lambda view: view["entities"][0]["scenario_estimates"][0].update(
                {"probability": 0.4}
            ),
            "sum to 1",
        ),
        (
            lambda view: view["entities"][0].update({"counterevidence": []}),
            "counterevidence",
        ),
        (
            lambda view: view["entities"][0].update({"thesis_breakers": []}),
            "thesis_breakers",
        ),
        (
            lambda view: view["identity"].update(
                {"subtitle": "<script>alert(1)</script>"}
            ),
            "plain text",
        ),
        (
            lambda view: view["identity"].update(
                {"subtitle": "Owner account ID 998877 remains linked."}
            ),
            "sensitive material",
        ),
        (
            lambda view: view["identity"].update(
                {"subtitle": "Owner account_id 998877 remains linked."}
            ),
            "sensitive material",
        ),
        (
            lambda view: view["identity"].update(
                {"subtitle": "Research source: .codex/goals/private-research.md"}
            ),
            "local path",
        ),
        (
            lambda view: view["identity"].update(
                {"subtitle": "Research source: ../.codex/goals/private-research.md"}
            ),
            "local path",
        ),
        (
            lambda view: view["identity"].update(
                {"subtitle": "Research source: project/.local/private-research.md"}
            ),
            "local path",
        ),
        (
            lambda view: view["layers"][0]["evidence_points"].append(
                "/tmp/private-research.json"
            ),
            "local path",
        ),
        (
            lambda view: view["layers"][0].update({"raw_provider_response": "secret"}),
            "forbidden key",
        ),
        (
            lambda view: view["artifacts"][0].update(
                {"artifact_ref": "/tmp/private-research-packet.json"}
            ),
            "local path",
        ),
        (
            lambda view: view["boundary"].update({"investment_advice": True}),
            "research boundary",
        ),
        (
            lambda view: view.update(
                {
                    "dashboard_summaries": [
                        *view["dashboard_summaries"],
                        *deepcopy(view["dashboard_summaries"]),
                        *deepcopy(view["dashboard_summaries"]),
                        *deepcopy(view["dashboard_summaries"]),
                    ]
                }
            ),
            "at most 3",
        ),
    ],
)
def test_decision_research_view_rejects_invalid_or_private_content(
    mutator,
    message: str,
) -> None:
    view = deepcopy(_valid_view())
    mutator(view)

    with pytest.raises(ValueError, match=message):
        validate_decision_research_view(view)


def test_decision_research_view_rejects_non_finite_probability() -> None:
    view = deepcopy(_valid_view())
    view["entities"][0]["scenario_estimates"][0]["probability"] = float("nan")

    with pytest.raises(ValueError, match="finite"):
        validate_decision_research_view(view)


def test_decision_research_view_allows_explicit_public_evidence_url() -> None:
    view = _valid_view()
    view["research_ledger"][0]["evidence_refs"].append(
        "https://www.sec.gov/Archives/edgar/data/000000/example.htm"
    )

    validated = validate_decision_research_view(view)

    assert validated["research_ledger"][0]["evidence_refs"][-1].startswith(
        "https://www.sec.gov/"
    )


@pytest.mark.parametrize(
    "reference",
    [
        "http://example.com/evidence",
        "https://localhost/evidence",
        "https://127.0.0.1/evidence",
        "https://private.example.internal/evidence",
        "https://example.com/private/report?"
        + "".join(("to", "ken"))
        + "=synthetic-value",
        "https://example.com/report?"
        + "".join(("refresh_", "to", "ken"))
        + "=synthetic-value",
    ],
)
def test_decision_research_view_rejects_unsafe_evidence_urls(
    reference: str,
) -> None:
    view = _valid_view()
    view["research_ledger"][0]["evidence_refs"].append(reference)

    with pytest.raises(ValueError, match="evidence reference"):
        validate_decision_research_view(view)
