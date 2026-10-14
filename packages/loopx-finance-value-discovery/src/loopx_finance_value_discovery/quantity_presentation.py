"""Quantity review through the existing Finance view, never source admission."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from .presentation_validation import public_reference_tree, record
from .quantity_reconciliation import assess_quantity_reconciliation
from .replay import canonical_sha256

FINANCE_QUANTITY_RESEARCH_INPUT_SCHEMA_VERSION = "finance_quantity_research_input_v1"
# Ten operand layers plus arithmetic and assurance fit the existing 12-layer view.
MAX_DISPLAY_FLOWS = 8


def _declared(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _layer(order: int, label: str, points: list[str]) -> dict[str, Any]:
    return {
        "id": f"quantity-{order}",
        "order": order,
        "label": label,
        "status": "partial",
        "summary": "Caller declarations; source authenticity and financial admission remain unproven.",
        "supporting_evidence": [
            {"classification": "agent_inference", "statement": point}
            for point in points
        ],
        "counterevidence": [
            {
                "classification": "agent_inference",
                "statement": "Arithmetic agreement does not authenticate identity, coverage, event time or historical availability.",
            }
        ],
        "thesis_breakers": [
            "Incorrect class, pool, unit, version, precision or coverage invalidates reconciliation."
        ],
    }


def build_quantity_research_packet(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Assess once and retain all operands and holds without display truncation.

    This provider-local wrapper reuses decision_research_dashboard_v0, Core
    publication and Lark readback. Large packets fail the existing display
    bounds; the arithmetic entry remains available for up to 256 flows.
    """
    from .dashboard import (
        build_finance_research_dashboard_packet,
        _reject_forbidden_material,
    )

    _reject_forbidden_material(payload)
    wrapper = record(
        payload,
        context="quantity research",
        allowed={
            "schema_version",
            "goal_id",
            "source_id",
            "source_version",
            "supersedes",
            "point_in_time",
            "evidence_cutoff",
            "review_due_at",
            "quantity_input",
        },
    )
    if wrapper.get("schema_version") != FINANCE_QUANTITY_RESEARCH_INPUT_SCHEMA_VERSION:
        raise ValueError("unsupported quantity research input version")
    assessment = assess_quantity_reconciliation(wrapper.get("quantity_input"))
    original = assessment["operands"]
    if len(original["flows"]) > MAX_DISPLAY_FLOWS:
        raise ValueError(
            "Complete quantity research display supports at most 8 flows; nothing was truncated or published. Use assess-quantity for the full arithmetic assessment."
        )
    public_reference_tree(original, "quantity input")
    calculation_keys = [
        "candidate_net_quantity",
        "candidate_expected_closing_quantity",
        "candidate_closing_difference",
        "reconciled_closing_quantity",
    ]
    # Exact strings stay in the layer even if they exceed metric-cell capacity.
    metrics = [
        {
            "id": key.replace("_", "-"),
            "label": label,
            "value": "unknown (not zero)"
            if assessment[key] is None
            else (
                assessment[key]
                if len(assessment[key]) <= 120
                else "See exact arithmetic below"
            ),
            "detail": "Quantity at the declared scale; candidate arithmetic is not qualified closing or cash.",
            "tone": "warning" if assessment[key] is None else "neutral",
        }
        for key, label in zip(
            calculation_keys,
            [
                "Candidate net quantity / 候选净数量",
                "Candidate closing / 候选期末",
                "Closing difference / 期末差额",
                "Reconciled closing / 核对期末",
            ],
            strict=True,
        )
    ]
    metrics += [
        {
            "id": "validated-alpha",
            "label": "Validated alpha",
            "value": "0",
            "detail": "Financial utility remains unverified.",
            "tone": "warning",
        },
        {
            "id": "method-state",
            "label": "Method state",
            "value": "unchanged",
            "detail": "Presentation does not adopt a method.",
            "tone": "neutral",
        },
    ]
    layers = [
        _layer(
            1,
            "Arithmetic / 数量核对",
            [
                "Reconciliation state: " + assessment["state"],
                *[key + ": " + _declared(assessment[key]) for key in calculation_keys],
                "quantity_reconciled: " + _declared(assessment["quantity_reconciled"]),
            ],
        )
    ]
    rows = [
        ("opening", original["opening_stock"]),
        ("closing", original["closing_stock"]),
    ]
    rows += [(f"flow_{i}", flow) for i, flow in enumerate(original["flows"])]
    for name, row in rows:
        layers.append(
            _layer(
                len(layers) + 1,
                "Operand / 原始声明: " + name,
                [
                    *[
                        key + ": " + _declared(row[key])
                        for key in [
                            "evidence_ref",
                            "source_digest",
                            "captured_at",
                            "identity",
                            "unit",
                            "value",
                            "accuracy",
                        ]
                    ],
                    "Role and order: "
                    + _declared(
                        {
                            key: row[key]
                            for key in ["aggregation_kind", "position", "direction"]
                            if key in row
                        }
                    ),
                    "Accuracy assessment: "
                    + _declared(assessment["numeric_accuracy_assessments"][name]),
                ],
            )
        )
    # Group reasons within the existing text bound. No omitted or shortened codes.
    reason_groups: list[str] = []
    group = ""
    for reason in assessment["reason_codes"]:
        addition = (", " if group else "Holds: ") + reason
        if len(group + addition) > 600:
            reason_groups.append(group)
            group = "Holds: " + reason
        else:
            group += addition
    if group:
        reason_groups.append(group)
    layers.append(
        _layer(
            len(layers) + 1,
            "Coverage, holds and assurance / 覆盖与缺证",
            [
                "Coverage: " + _declared(original["coverage"]),
                *(
                    reason_groups
                    or ["Holds: none; source authentication remains unproven."]
                ),
                "Arithmetic assurance: " + assessment["arithmetic_assurance"],
                "Coverage assurance: " + assessment["coverage_assurance"],
                "Ordering assurance: " + assessment["ordering_assurance"],
                "Source authentication, financial admission, cash derivation and trading: all false; exact cash: unknown (not zero).",
                "Original capture clocks are retained. Review time does not establish event time, first availability or PIT eligibility.",
            ],
        )
    )
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
                    "point_in_time",
                    "evidence_cutoff",
                    "review_due_at",
                ]
            },
            "title": "Quantity reconciliation review / 数量核对复核",
            "subtitle": "Declared quantity stocks and signed flows; source and financial qualification remain separate.",
            "adjudication": {
                "status": "insufficient_evidence",
                "label": "Insufficient Evidence",
                "confidence": "low",
                "summary": "Arithmetic state: "
                + assessment["state"]
                + ". Review original operands, holds and coverage; agreement grants no source, investment or trading admission.",
            },
            "metrics": metrics,
            "dashboard_summaries": [],
            "research_layers": layers,
            "entities": [],
            "research_ledger": [],
            "event_gates": [],
            "artifacts": [
                {
                    "artifact_id": "quantity-assessment",
                    "kind": "research_packet",
                    "label": "Complete quantity assessment",
                    "summary": "The returned quantity_assessment preserves all operands, nulls and holds. This hash identifies the calculation; it does not authenticate sources.",
                    "artifact_ref": "sha256:" + canonical_sha256(assessment),
                    "evidence_refs": ["sha256:" + assessment["input_sha256"]],
                }
            ],
            "method_state": {
                "revision": "quantity-reconciliation-v1",
                "lifecycle_state": "active_method_unchanged",
                "active_method_changed": False,
                "summary": "Quantity display only; method adoption and financial utility remain separate.",
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
    packet["quantity_assessment"] = assessment
    return packet
