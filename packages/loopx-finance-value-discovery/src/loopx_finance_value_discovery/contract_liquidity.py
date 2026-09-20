"""Evaluate provider-neutral derivatives exit-liquidity evidence.

The Finance extension owns the economic admission rule.  Venue adapters own
contract discovery, precision handling and order-book measurement; this module
accepts only their public-safe, frozen measurements and never calls a venue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .boundary import reject_forbidden_material


FINANCE_CONTRACT_LIQUIDITY_INPUT_SCHEMA_VERSION = (
    "finance_contract_liquidity_input_v0"
)
FINANCE_CONTRACT_LIQUIDITY_EVALUATION_SCHEMA_VERSION = (
    "finance_contract_liquidity_evaluation_v0"
)

_POSITION_DIRECTIONS = {"long", "short"}
_EXIT_SIDE_BY_POSITION = {"long": "sell", "short": "buy"}
_CONTRACT_KINDS = {"perpetual", "future"}
_DECIMAL_TEXT = re.compile(r"^(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,18})?$")


def _text(value: object, *, field: str, limit: int = 160) -> str:
    result = " ".join(str(value or "").split())
    if not result:
        raise ValueError(f"{field} is required")
    if len(result) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    reject_forbidden_material(result, path=field)
    return result


def _timestamp(value: object, *, field: str) -> datetime:
    text = _text(value, field=field, limit=80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _decimal(
    value: object,
    *,
    field: str,
    minimum: Decimal = Decimal("0"),
    strictly_positive: bool = False,
) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_TEXT.fullmatch(value):
        raise ValueError(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < minimum:
        raise ValueError(f"{field} must be a finite decimal >= {minimum}")
    if strictly_positive and parsed == 0:
        raise ValueError(f"{field} must be greater than zero")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    normalized = format(value.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _evidence_refs(value: object, *, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field} must be a list")
    if not 1 <= len(value) <= 5:
        raise ValueError(f"{field} must contain 1-5 items")
    refs = [
        _text(item, field=f"{field}[{index}]", limit=160)
        for index, item in enumerate(value)
    ]
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must use unique evidence refs")
    return refs


def _scenario(value: object, *, index: int) -> dict[str, Any]:
    field = f"scenarios[{index}]"
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    allowed = {
        "scenario_id",
        "position_direction",
        "exit_side",
        "requested_notional",
        "executable_notional",
        "spread_bps",
        "price_impact_bps",
        "fee_bps",
        "evidence_refs",
    }
    if set(value) - allowed:
        raise ValueError(f"{field} has unsupported fields")

    direction = _text(
        value.get("position_direction"),
        field=f"{field}.position_direction",
        limit=16,
    ).lower()
    if direction not in _POSITION_DIRECTIONS:
        raise ValueError(
            f"{field}.position_direction must be one of {sorted(_POSITION_DIRECTIONS)}"
        )
    exit_side = _text(
        value.get("exit_side"), field=f"{field}.exit_side", limit=16
    ).lower()
    expected_side = _EXIT_SIDE_BY_POSITION[direction]
    if exit_side != expected_side:
        raise ValueError(
            f"{field}.exit_side must be {expected_side} for a {direction} position"
        )

    return {
        "scenario_id": _text(
            value.get("scenario_id"), field=f"{field}.scenario_id", limit=96
        ),
        "position_direction": direction,
        "exit_side": exit_side,
        "requested_notional": _decimal(
            value.get("requested_notional"),
            field=f"{field}.requested_notional",
            strictly_positive=True,
        ),
        "executable_notional": _decimal(
            value.get("executable_notional"),
            field=f"{field}.executable_notional",
        ),
        "spread_bps": _decimal(
            value.get("spread_bps"), field=f"{field}.spread_bps"
        ),
        "price_impact_bps": _decimal(
            value.get("price_impact_bps"), field=f"{field}.price_impact_bps"
        ),
        "fee_bps": _decimal(value.get("fee_bps"), field=f"{field}.fee_bps"),
        "evidence_refs": _evidence_refs(
            value.get("evidence_refs"), field=f"{field}.evidence_refs"
        ),
    }


def evaluate_finance_contract_liquidity(value: object) -> dict[str, Any]:
    """Return a deterministic exit-cost admission over frozen measurements."""

    if not isinstance(value, Mapping):
        raise ValueError("contract liquidity input must be an object")
    reject_forbidden_material(value)
    allowed = {
        "schema_version",
        "evaluation_id",
        "instrument_ref",
        "contract_kind",
        "quote_unit",
        "observed_at",
        "evaluation_as_of",
        "maximum_age_seconds",
        "maximum_exit_cost_bps",
        "minimum_book_coverage_ratio",
        "scenarios",
    }
    if set(value) - allowed:
        raise ValueError("contract liquidity input has unsupported fields")
    if value.get("schema_version") != FINANCE_CONTRACT_LIQUIDITY_INPUT_SCHEMA_VERSION:
        raise ValueError(
            "schema_version must be "
            f"{FINANCE_CONTRACT_LIQUIDITY_INPUT_SCHEMA_VERSION}"
        )

    contract_kind = _text(
        value.get("contract_kind"), field="contract_kind", limit=24
    ).lower()
    if contract_kind not in _CONTRACT_KINDS:
        raise ValueError(f"contract_kind must be one of {sorted(_CONTRACT_KINDS)}")
    observed_at = _timestamp(value.get("observed_at"), field="observed_at")
    evaluation_as_of = _timestamp(
        value.get("evaluation_as_of"), field="evaluation_as_of"
    )
    if observed_at > evaluation_as_of:
        raise ValueError("observed_at must not be after evaluation_as_of")
    maximum_age_seconds = value.get("maximum_age_seconds")
    if (
        not isinstance(maximum_age_seconds, int)
        or isinstance(maximum_age_seconds, bool)
        or not 1 <= maximum_age_seconds <= 86_400
    ):
        raise ValueError("maximum_age_seconds must be an integer from 1 to 86400")
    maximum_exit_cost_bps = _decimal(
        value.get("maximum_exit_cost_bps"),
        field="maximum_exit_cost_bps",
        strictly_positive=True,
    )
    minimum_coverage = _decimal(
        value.get("minimum_book_coverage_ratio"),
        field="minimum_book_coverage_ratio",
        strictly_positive=True,
    )
    if minimum_coverage > Decimal("1"):
        raise ValueError("minimum_book_coverage_ratio must be <= 1")

    raw_scenarios = value.get("scenarios")
    if not isinstance(raw_scenarios, Sequence) or isinstance(
        raw_scenarios, (str, bytes, bytearray)
    ):
        raise ValueError("scenarios must be a list")
    if not 1 <= len(raw_scenarios) <= 16:
        raise ValueError("scenarios must contain 1-16 items")
    scenarios = [
        _scenario(item, index=index) for index, item in enumerate(raw_scenarios)
    ]
    scenario_ids = [item["scenario_id"] for item in scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("scenarios must use unique scenario ids")
    identities = [
        (item["position_direction"], item["requested_notional"])
        for item in scenarios
    ]
    if len(identities) != len(set(identities)):
        raise ValueError(
            "scenarios must use unique position_direction/requested_notional pairs"
        )

    stale_after = observed_at + timedelta(seconds=maximum_age_seconds)
    freshness_state = "fresh" if evaluation_as_of <= stale_after else "stale"
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        requested = scenario["requested_notional"]
        executable = scenario["executable_notional"]
        coverage_ratio = executable / requested
        total_cost_bps = (
            scenario["spread_bps"]
            + scenario["price_impact_bps"]
            + scenario["fee_bps"]
        )
        reasons: list[str] = []
        if freshness_state == "stale":
            reasons.append("measurement_stale")
        if coverage_ratio < minimum_coverage:
            reasons.append("insufficient_executable_notional")
        if total_cost_bps > maximum_exit_cost_bps:
            reasons.append("exit_cost_above_limit")
        if freshness_state == "stale":
            disposition = "insufficient_evidence"
        elif reasons:
            disposition = "insufficient_liquidity"
        else:
            disposition = "eligible_for_research_successor"
        results.append(
            {
                "scenario_id": scenario["scenario_id"],
                "position_direction": scenario["position_direction"],
                "exit_side": scenario["exit_side"],
                "requested_notional": _decimal_text(requested),
                "executable_notional": _decimal_text(executable),
                "book_coverage_ratio": _decimal_text(coverage_ratio),
                "spread_bps": _decimal_text(scenario["spread_bps"]),
                "price_impact_bps": _decimal_text(scenario["price_impact_bps"]),
                "fee_bps": _decimal_text(scenario["fee_bps"]),
                "total_exit_cost_bps": _decimal_text(total_cost_bps),
                "evidence_refs": scenario["evidence_refs"],
                "disposition": disposition,
                "reasons": reasons,
            }
        )

    if freshness_state == "stale":
        disposition = "insufficient_evidence"
    elif all(
        item["disposition"] == "eligible_for_research_successor"
        for item in results
    ):
        disposition = "eligible_for_research_successor"
    else:
        disposition = "insufficient_liquidity"

    return {
        "ok": True,
        "schema_version": FINANCE_CONTRACT_LIQUIDITY_EVALUATION_SCHEMA_VERSION,
        "evaluation_id": _text(
            value.get("evaluation_id"), field="evaluation_id", limit=96
        ),
        "instrument_ref": _text(
            value.get("instrument_ref"), field="instrument_ref", limit=120
        ),
        "contract_kind": contract_kind,
        "quote_unit": _text(value.get("quote_unit"), field="quote_unit", limit=32),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "evaluation_as_of": evaluation_as_of.isoformat().replace("+00:00", "Z"),
        "stale_after": stale_after.isoformat().replace("+00:00", "Z"),
        "freshness_state": freshness_state,
        "limits": {
            "maximum_age_seconds": maximum_age_seconds,
            "maximum_exit_cost_bps": _decimal_text(maximum_exit_cost_bps),
            "minimum_book_coverage_ratio": _decimal_text(minimum_coverage),
        },
        "scenario_results": results,
        "disposition": disposition,
        "boundary": {
            "venue_semantics_state": "adapter_asserted",
            "investment_value_evaluated": False,
            "funding_evaluated": False,
            "trading_allowed": False,
            "automatic_ready_allowed": False,
        },
    }
