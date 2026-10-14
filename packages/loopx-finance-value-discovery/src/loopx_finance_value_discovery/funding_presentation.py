"""Map declared funding arithmetic to the existing Finance research surface.

Finance owns the arithmetic and view. Core continues to own publication,
revision/hash readback and optional-extension lifecycle; this module sends
nothing and never interprets source/payment authentication.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from .funding_event import FundingRateKind, assess_funding_event_pair
from .replay import canonical_sha256
from .presentation_validation import public_reference_tree, record


FINANCE_FUNDING_RESEARCH_INPUT_SCHEMA_VERSION = (
    "finance_funding_event_research_input_v1"
)


def _declared(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _layer(order: int, label: str, points: list[str]) -> dict[str, Any]:
    return {
        "id": f"funding-{order}",
        "order": order,
        "label": label,
        "status": "partial",
        "summary": "Caller declarations; source and payment authentication remain unproven.",
        "supporting_evidence": [
            {"classification": "agent_inference", "statement": item} for item in points
        ],
        "counterevidence": [
            {
                "classification": "agent_inference",
                "statement": "A complete arithmetic input does not establish source authenticity, PIT admission, or investment utility.",
            }
        ],
        "thesis_breakers": [
            "An incorrect source, payment, quantity basis or currency invalidates this calculation."
        ],
    }


def build_funding_event_research_packet(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Assess once, preserve full operands, and publish only a bounded safe view.

    The new input literal is local to this Finance provider. Output reuses
    ``finance_research_dashboard_packet_v0`` and its existing view validator.
    No bull/base/bear probabilities or source clocks are manufactured.
    """
    from .dashboard import (
        build_finance_research_dashboard_packet,
        _reject_forbidden_material,
    )

    _reject_forbidden_material(payload)
    wrapper = record(
        payload,
        context="funding research",
        allowed={
            "schema_version",
            "goal_id",
            "source_id",
            "source_version",
            "supersedes",
            "review_due_at",
            "funding_event_input",
        },
    )
    if wrapper.get("schema_version") != FINANCE_FUNDING_RESEARCH_INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported funding research input version")
    assessment = assess_funding_event_pair(wrapper.get("funding_event_input"))
    original = assessment["input"]
    public_reference_tree(original, "funding input")
    decision_at = original["decision_at"]
    layers = [
        _layer(
            1,
            "Event and period pairing / 事件与期间",
            [
                "Settlement event relation: " + assessment["settlement_event_relation"],
                "Native period relation: " + assessment["native_period_relation"],
                "Pair operands eligible: "
                + _declared(assessment["pair_operands_eligible"]),
                "All holds: " + _declared(assessment["reason_codes"]),
                "Raw rates were not compared or normalized; discrete payment counts were not inferred.",
            ],
        )
    ]
    for index, (leg, result) in enumerate(
        zip(original["legs"], assessment["leg_assessments"], strict=True)
    ):
        points = (
            ["Operand: unknown (not zero)"]
            if leg is None
            else [f"{key}: {_declared(value)}" for key, value in leg.items()]
        )
        points += [
            "Leg holds: " + _declared(result["reason_codes"]),
            "Declared calculation (candidate is not admission): "
            + _declared(
                {
                    key: result[key]
                    for key in [
                        "eligible",
                        "rate_kind",
                        "rate_fraction",
                        "currency",
                        "candidate_signed_cash",
                        "conditional_signed_estimate",
                        "declared_realized_cash",
                        "payment_ref",
                    ]
                }
            ),
            "Normalized event, period and clocks: "
            + _declared(
                {
                    key: result[key]
                    for key in ["event_identity", "period", "normalized_clocks"]
                }
            ),
        ]
        layers.append(
            _layer(index + 2, f"Leg {index + 1} declarations / 原始声明", points)
        )
    layers.append(
        _layer(
            4,
            "Costs and assurance / 费用与资格",
            [
                "Costs: " + _declared(original["costs"]),
                "Unknown costs are not zero; known zero costs remain zero.",
                "Arithmetic assurance: " + assessment["arithmetic_assurance"],
                "Source interpretation: "
                + assessment["source_interpretation_assurance"],
                "Source/payment authentication, source lifecycle, PIT, financial admission and trading authority: all false.",
            ],
        )
    )
    currency = assessment["cash_currency"]
    metrics = []
    cash_measures = [
        ("conditional_gross_estimate", "Conditional gross estimate / 条件毛额"),
        ("conditional_net_estimate", "Conditional net estimate / 条件净额"),
        ("declared_realized_gross_cash", "Declared realized gross / 声明已实现毛额"),
        ("net_cash", "Declared realized net / 声明已实现净额"),
    ]
    # The ordinary summary shows the declared kind first. A realized review
    # must not lead with two empty forecast cells; no amount or authority changes.
    if assessment["leg_assessments"][0]["rate_kind"] == FundingRateKind.REALIZED:
        cash_measures = cash_measures[2:] + cash_measures[:2]
    for key, label in cash_measures:
        amount = assessment[key]
        metrics.append(
            {
                "id": key.replace("_", "-"),
                "label": label,
                "value": "unknown (not zero)"
                if amount is None
                else f"{amount} {currency}",
                "detail": "Forecast estimates are conditional; declared realized cash is not authenticated settlement.",
                "tone": "warning" if amount is None else "neutral",
            }
        )
    metrics += [
        {
            "id": "validated-alpha",
            "label": "Validated alpha",
            "value": "0",
            "detail": "No financial utility was validated.",
            "tone": "warning",
        },
        {
            "id": "method-state",
            "label": "Method state",
            "value": "unchanged",
            "detail": "Display preparation does not adopt a research method.",
            "tone": "neutral",
        },
    ]
    input_ref = "sha256:" + assessment["input_sha256"]
    assessment_ref = "sha256:" + canonical_sha256(assessment)
    packet = build_finance_research_dashboard_packet(
        {
            "schema_version": "finance_research_dashboard_input_v0",
            "surface_id": "investment-research",
            **{
                key: wrapper.get(key)
                for key in [
                    "goal_id",
                    "source_id",
                    "source_version",
                    "supersedes",
                    "review_due_at",
                ]
            },
            "point_in_time": decision_at,
            "evidence_cutoff": decision_at,
            "title": "Funding event review / 资金费事件复核",
            "subtitle": "Declared arithmetic and missing evidence; no investment or trading admission.",
            "adjudication": {
                "status": "insufficient_evidence",
                "label": "Insufficient Evidence",
                "summary": "Review the declared event pair, each original operand and all holds. Source and payment authentication remain unproven.",
                "confidence": "low",
            },
            "metrics": metrics,
            "dashboard_summaries": [],
            "research_layers": layers,
            "entities": [],
            "research_ledger": [],
            "event_gates": [],
            "artifacts": [
                {
                    "artifact_id": "funding-assessment",
                    "kind": "research_packet",
                    "label": "Complete funding assessment",
                    "summary": "The returned funding_assessment retains the original operands, nulls, holds and assurance flags. This content hash identifies it; the display does not authenticate the sources.",
                    "artifact_ref": assessment_ref,
                    "evidence_refs": [input_ref],
                }
            ],
            "method_state": {
                "revision": "funding-event-v1",
                "lifecycle_state": "active_method_unchanged",
                "active_method_changed": False,
                "summary": "Finance arithmetic displayed; method adoption and utility remain separate.",
            },
            "boundary": {
                "research_aid_only": True,
                "investment_advice": False,
                "trading_allowed": False,
                "raw_provider_payload_recorded": False,
                "private_source_content_read": False,
            },
        }
    )
    packet["funding_assessment"] = assessment
    return packet
