"""Lark card projection for the finance-owned research view.

The renderer consumes the same validated ``decision_research_dashboard_v0``
view as the Dashboard. It does not read provider payloads or define a second
finance schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

from loopx.extensions.lark.presentation.message_card import (
    build_lark_markdown_reply_card,
)

from .presentation_view import validate_decision_research_view


_LARK_MARKDOWN_CONTROL_RE = re.compile(r"([\\`*_{}\[\]!|>~])")


def _lark_plain_text(value: object) -> str:
    """Keep validated display text from changing the surrounding card markup."""

    compact = " ".join(str(value).split())
    return _LARK_MARKDOWN_CONTROL_RE.sub(r"\\\1", compact)


def _display_value(metric: Mapping[str, Any]) -> str:
    value = metric.get("value")
    if value is None:
        return "missing (not zero)"
    return f"{value} {metric['unit']}"


def render_source_period_metrics_markdown(view: Mapping[str, Any]) -> str:
    """Render a bounded source-period summary from the canonical finance view."""

    validated = validate_decision_research_view(view)
    metrics = validated.get("source_period_metrics", [])
    spot_markets = validated.get("spot_market_identity", {}).get("markets", [])
    if not metrics and not spot_markets:
        return (
            "**Source-period evidence**\n\n"
            "No source-period metrics were projected. Missing evidence is not zero."
        )
    lines = [
        "**Source-period evidence**",
        "",
        "These rows are evidence-only and never grant ready or trading authority.",
    ]
    for metric in metrics:
        period = (
            metric["period_start"]
            if metric["period_start"] == metric["period_end"]
            else f"{metric['period_start']} → {metric['period_end']}"
        )
        lines.extend(
            [
                "",
                f"**{_lark_plain_text(metric['label'])}** · `{period}`",
                f"- Value: {_lark_plain_text(_display_value(metric))} · basis `{metric['metric_basis']}` / `{metric['metric_semantics']}` · {metric['value_origin']} / {metric['value_precision']}",
                f"- Coverage: `{metric['coverage_state']}` · observed {len(metric['observed_components'])}/{len(metric['expected_components'])} · methodology `{metric['methodology_state']}` · anomaly `{metric['anomaly_state']}`",
                f"- Event: `{metric['event_identity']['namespace']}` / `{metric['event_identity']['source_event_id']}` · `{metric['event_identity']['instrument_id']}` · `{metric['event_identity']['event_at']}`",
                f"- Lineage: `{metric['lineage_state']}` · authority `{metric['observation_authority']}` · independent evidence `{str(metric['independent_evidence']).lower()}` · source {_lark_plain_text(metric['source_ref'])}",
                f"- Accounting: sign `{metric['sign_basis']}` · fee `{metric['fee_inclusion']}` · NAV treatment `{metric['account_nav_treatment']}`",
            ]
        )
        if metric["missing_components"]:
            lines.append(
                "- Missing components: "
                + ", ".join(
                    _lark_plain_text(component)
                    for component in metric["missing_components"]
                )
            )
        if metric["double_counted_components"]:
            lines.append(
                "- Excluded double-counted components: "
                + ", ".join(
                    _lark_plain_text(component)
                    for component in metric["double_counted_components"]
                )
            )
        if metric["gap_reasons"]:
            lines.append(
                "- Holds: "
                + ", ".join(
                    _lark_plain_text(reason) for reason in metric["gap_reasons"]
                )
            )
    if spot_markets:
        lines.extend(
            [
                "",
                "**Spot identity joins**",
                "Contexts join by `context.coin == pair.name`; assets resolve by explicit index, never array position.",
            ]
        )
        for market in spot_markets:
            price = (
                "missing (not zero)"
                if market["mark_price"] is None
                else str(market["mark_price"])
            )
            lines.extend(
                [
                    "",
                    f"**{_lark_plain_text(market['pair_name'])}** · {_lark_plain_text(market['base_asset']['symbol'])} / {_lark_plain_text(market['quote_asset']['symbol'])}",
                    f"- Context: {_lark_plain_text(market['context_coin'])} · observed `{market['observed_at']}` · mark `{price}`",
                    f"- Canonicality: `{market['canonicality']}` · backing `{market['backing_inference']}`",
                ]
            )
    return "\n".join(lines)


def build_source_period_metrics_lark_card(
    view: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a send-ready card without performing an external message write."""

    return build_lark_markdown_reply_card(
        render_source_period_metrics_markdown(view),
        title="Finance source-period evidence",
        template="yellow",
        footer="LoopX finance projection · evidence only · never auto-ready",
    )


def render_decision_research_markdown(view: Mapping[str, Any]) -> str:
    """Preserve the canonical research, including uncertainty and counterevidence."""

    validated = validate_decision_research_view(view)
    identity = validated["identity"]
    decision = validated["adjudication"]
    plain = _lark_plain_text
    lines = [
        f"**{plain(identity['title'])}**",
        plain(identity["subtitle"]),
        f"As of: {plain(identity['as_of'])} · evidence cutoff: {plain(identity['evidence_cutoff'])}",
        "Research aid only · investment advice: false · trading allowed: false.",
        "",
        f"**{plain(decision['label'])}** · confidence: {plain(decision['confidence'])}",
        plain(decision["summary"]),
    ]

    def section(title: str, items: list[str]) -> None:
        if items:
            lines.extend(["", f"**{title}**", *[f"- {item}" for item in items]])

    section("Measures", [
        f"{plain(item['label'])}: {plain(item['value'])} · {plain(item['detail'])}"
        for item in validated["metrics"]
    ])
    for layer in validated["layers"]:
        section(plain(layer["label"]), [
            f"{plain(layer['status'])} · {plain(layer['summary'])}",
            *map(plain, layer["evidence_points"]),
        ])
    for entity in validated["entities"]:
        section(plain(entity["display_name"]), [
            f"{plain(entity['symbol'])} · {plain(entity['status'])} · confidence: {plain(entity['confidence'])}",
            plain(entity["inference"]),
        ])
        section("Observations", [
            f"{plain(item['label'])}: {plain(item['value'])} · {plain(item['kind'])} · {plain(item['as_of'])} · {plain(item['source_ref'])} · invalidation: {plain(item['invalidation'])}"
            for item in entity["observations"]
        ])
        section("Scenario estimates", [
            f"{plain(item['label'])}: {plain(item['value'])} · {plain(item['horizon'])} · probability: {plain(item['probability'])} · assumptions: {', '.join(map(plain, item['assumptions']))}"
            for item in entity["scenario_estimates"]
        ])
        section("Counterevidence", list(map(plain, entity["counterevidence"])))
        section("Thesis breakers", list(map(plain, entity["thesis_breakers"])))
        section("Next events", list(map(plain, entity["next_events"])))
    for case in validated["research_ledger"]:
        section(plain(case["label"]), [
            f"{plain(case['decision'])} · {plain(case['summary'])}",
            *[f"{plain(gate['label'])}: {plain(gate['status'])} · {plain(gate['summary'])}" for gate in case["gate_states"]],
            "Evidence: " + ", ".join(map(plain, case["evidence_refs"])),
        ])
    for event in validated["event_gates"]:
        section(plain(event["label"]), [
            f"{plain(event['status'])} · {plain(event['observation_window'])}",
            plain(event["frozen_hypothesis"]),
            "Next review: " + plain(event["next_review"]),
        ])
        for label, key in [("Observables", "observables"), ("Current evidence", "current_evidence"),
                           ("Supports", "supports"), ("Refutes", "refutes"), ("Thesis breakers", "thesis_breakers")]:
            section(label, list(map(plain, event[key])))
    section("Artifacts", [
        f"{plain(item['label'])}: {plain(item['summary'])} · {plain(item['artifact_ref'])} · evidence: {', '.join(map(plain, item['evidence_refs']))}"
        for item in validated["artifacts"]
    ])
    method = validated["method_state"]
    section("Method", [f"{plain(method['revision'])} · {plain(method['lifecycle_state'])} · active method changed: {str(method['active_method_changed']).lower()} · {plain(method['summary'])}"])
    lines.extend(["", render_source_period_metrics_markdown(validated)])
    return "\n".join(lines)


def build_decision_research_lark_card(view: Mapping[str, Any]) -> dict[str, Any]:
    """Prepare a complete bounded card; sending requires the existing sink authority."""

    return _research_card(render_decision_research_markdown(view))


def build_published_decision_research_lark_card(
    *,
    state_file: str | Path,
    goal_id: str,
    extension_revision: str,
    payload_sha256: str,
    surface_id: str = "investment-research",
) -> dict[str, Any]:
    """Read the exact active publication through Core; prepare, never send.

    Core owns lifecycle, envelope validation and content hashing. A stale or
    disabled publication must fail there instead of falling back to a new view.
    """

    from loopx.extensions.presentation import read_extension_projection

    envelope = read_extension_projection(
        state_file=state_file,
        extension_id="loopx-finance-value-discovery",
        surface_id=surface_id,
        extension_revision=extension_revision,
        payload_sha256=payload_sha256,
    )
    if envelope["goal_id"] != goal_id:
        raise ValueError("published research does not belong to the requested Goal")
    lineage = envelope["lineage"]
    plain = _lark_plain_text
    reference = [
        "**Published review reference**",
        f"Goal: {plain(envelope['goal_id'])} · surface: {plain(envelope['surface_id'])}",
        f"Source: {plain(lineage['source_id'])} · version: {lineage['version']}",
        f"Published: {plain(envelope['generated_at'])}",
        "Review due: " + (
            plain(envelope["review_due_at"])
            if envelope["review_due_at"] is not None else "unknown"
        ),
        "Check this original review deadline before acting; card preparation does not renew it.",
        f"Extension revision: {plain(envelope['extension_revision'])}",
        f"Payload SHA-256: {envelope['payload_sha256']}",
    ]
    if lineage["supersedes"]:
        reference.append("Supersedes: " + ", ".join(map(plain, lineage["supersedes"])))
    markdown = render_decision_research_markdown(envelope["view"])
    return _research_card(markdown + "\n\n" + "\n".join(reference))


def _research_card(markdown: str) -> dict[str, Any]:
    """Apply the same no-truncation limit to prepared and published research."""

    if len(markdown.encode("utf-8")) > 18_000:
        raise ValueError("Research exceeds card capacity; review the complete published result in App. No research was truncated or sent.")
    return build_lark_markdown_reply_card(
        markdown,
        title="Finance research review",
        template="yellow",
        footer="Research aid only · no trading authority · method adoption is separate",
        max_markdown_chars=len(markdown),
    )
