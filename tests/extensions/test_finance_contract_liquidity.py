from __future__ import annotations

import json
import sys
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXTENSION_ROOT = ROOT / "packages" / "loopx-finance-value-discovery"
EXTENSION_SRC = EXTENSION_ROOT / "src"
EXAMPLE = EXTENSION_ROOT / "examples" / "contract-liquidity-v0.json"
sys.path.insert(0, str(EXTENSION_SRC))

from loopx_finance_value_discovery.cli import run  # noqa: E402
from loopx_finance_value_discovery.contract_liquidity import (  # noqa: E402
    evaluate_finance_contract_liquidity,
)


def _example() -> dict[str, object]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_amount_and_direction_specific_exit_cost_is_derived() -> None:
    result = evaluate_finance_contract_liquidity(_example())

    assert result["schema_version"] == "finance_contract_liquidity_evaluation_v0"
    assert result["freshness_state"] == "fresh"
    assert result["disposition"] == "eligible_for_research_successor"
    assert result["scenario_results"] == [
        {
            "scenario_id": "close-long-1000",
            "position_direction": "long",
            "exit_side": "sell",
            "requested_notional": "1000",
            "executable_notional": "1200",
            "book_coverage_ratio": "1.2",
            "spread_bps": "2",
            "price_impact_bps": "4.5",
            "fee_bps": "5",
            "total_exit_cost_bps": "11.5",
            "evidence_refs": ["public-book:snapshot-001"],
            "disposition": "eligible_for_research_successor",
            "reasons": [],
        },
        {
            "scenario_id": "close-short-1000",
            "position_direction": "short",
            "exit_side": "buy",
            "requested_notional": "1000",
            "executable_notional": "1100",
            "book_coverage_ratio": "1.1",
            "spread_bps": "2.2",
            "price_impact_bps": "5.1",
            "fee_bps": "5",
            "total_exit_cost_bps": "12.3",
            "evidence_refs": ["public-book:snapshot-001"],
            "disposition": "eligible_for_research_successor",
            "reasons": [],
        },
    ]
    assert result["boundary"] == {
        "venue_semantics_state": "adapter_asserted",
        "investment_value_evaluated": False,
        "funding_evaluated": False,
        "trading_allowed": False,
        "automatic_ready_allowed": False,
    }


def test_capacity_and_cost_fail_independently() -> None:
    payload = _example()
    payload["scenarios"][0]["executable_notional"] = "750"
    payload["scenarios"][1]["price_impact_bps"] = "30"

    result = evaluate_finance_contract_liquidity(payload)

    assert result["disposition"] == "insufficient_liquidity"
    assert result["scenario_results"][0]["reasons"] == [
        "insufficient_executable_notional"
    ]
    assert result["scenario_results"][1]["reasons"] == [
        "exit_cost_above_limit"
    ]


def test_stale_measurement_is_insufficient_evidence_not_a_liquidity_rejection() -> None:
    payload = _example()
    payload["evaluation_as_of"] = "2026-01-15T12:02:01Z"

    result = evaluate_finance_contract_liquidity(payload)

    assert result["freshness_state"] == "stale"
    assert result["disposition"] == "insufficient_evidence"
    assert all(
        item["disposition"] == "insufficient_evidence"
        and item["reasons"] == ["measurement_stale"]
        for item in result["scenario_results"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("funding_rate", "0.0001"),
        ("expected_return_bps", "100"),
        ("investment_score", "high"),
        ("venue", "synthetic-exchange"),
    ],
)
def test_funding_value_and_venue_semantics_are_not_admission_inputs(
    field: str, value: str
) -> None:
    payload = _example()
    payload[field] = value

    with pytest.raises(ValueError, match="unsupported fields"):
        evaluate_finance_contract_liquidity(payload)


def test_exit_side_must_close_the_declared_position_direction() -> None:
    payload = _example()
    payload["scenarios"][0]["exit_side"] = "buy"

    with pytest.raises(ValueError, match="must be sell for a long position"):
        evaluate_finance_contract_liquidity(payload)


def test_decimal_measurements_are_strings_and_nonnegative() -> None:
    payload = _example()
    payload["scenarios"][0]["spread_bps"] = 2
    with pytest.raises(ValueError, match="must be a decimal string"):
        evaluate_finance_contract_liquidity(payload)

    payload = _example()
    payload["scenarios"][0]["fee_bps"] = "-1"
    with pytest.raises(ValueError, match="must be a decimal string"):
        evaluate_finance_contract_liquidity(payload)

    payload = _example()
    payload["scenarios"][0]["requested_notional"] = "1e1000000"
    with pytest.raises(ValueError, match="must be a decimal string"):
        evaluate_finance_contract_liquidity(payload)


def test_cli_and_managed_runtime_route_the_same_schema(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        run(["evaluate-contract-liquidity", "--input-json", str(EXAMPLE)]) == 0
    )
    direct = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(sys, "stdin", EXAMPLE.open(encoding="utf-8"))
    assert run([]) == 0
    managed = json.loads(capsys.readouterr().out)

    assert direct == managed


def test_extension_and_package_versions_match() -> None:
    manifest = tomllib.loads(
        (EXTENSION_ROOT / "extension.toml").read_text(encoding="utf-8")
    )
    package = tomllib.loads(
        (EXTENSION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert manifest["version"] == package["project"]["version"] == "0.8.0"


def test_duplicate_amount_direction_identity_is_rejected() -> None:
    payload = _example()
    duplicate = deepcopy(payload["scenarios"][0])
    duplicate["scenario_id"] = "another-long-1000"
    payload["scenarios"].append(duplicate)

    with pytest.raises(ValueError, match="unique position_direction"):
        evaluate_finance_contract_liquidity(payload)
