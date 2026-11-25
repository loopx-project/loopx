"""Modularization and Zero-Any contract tests for model behavior portfolio."""

from __future__ import annotations

import ast
from pathlib import Path

from loopx.control_plane.testing import (
    actual_default_model_behavior_catalog as _catalog,
    actual_default_model_behavior_portfolio as _portfolio,
    actual_default_model_behavior_sources as _sources,
    actual_default_model_behavior_validators as _validators,
)


def test_portfolio_modules_remain_under_700_loc_and_zero_any() -> None:
    testing_dir = (
        Path(__file__).resolve().parents[2]
        / "loopx"
        / "control_plane"
        / "testing"
    )
    for filename in (
        "actual_default_model_behavior_catalog.py",
        "actual_default_model_behavior_sources.py",
        "actual_default_model_behavior_validators.py",
        "actual_default_model_behavior_portfolio.py",
    ):
        source = (testing_dir / filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        any_refs = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "Any"
        ]
        assert any_refs == [], f"{filename} must remain Zero-Any"
        assert "type: ignore" not in source
        assert "O_NOFOLLOW" not in source
        assert len(source.splitlines()) < 700


def test_portfolio_facade_preserves_exports_and_preflight_parity(
    tmp_path: Path,
) -> None:
    assert _portfolio._SCENARIOS is _catalog._SCENARIOS
    assert _portfolio._CONTRASTS is _catalog._CONTRASTS
    assert _portfolio._receipt_alignment is _validators._receipt_alignment
    assert _portfolio._scenario_contract is _validators._scenario_contract
    assert (
        _portfolio.build_actual_default_model_behavior_scenario_inputs
        is _sources.build_actual_default_model_behavior_scenario_inputs
    )

    catalog = _portfolio.actual_default_model_behavior_scenario_catalog()
    scenarios = catalog["scenarios"]
    contrasts = catalog["contrasts"]
    assert isinstance(scenarios, list)
    assert isinstance(contrasts, list)
    assert len(scenarios) == _portfolio.ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT
    assert len(contrasts) == _portfolio.ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT

    sources, packets = _portfolio.build_actual_default_model_behavior_scenario_inputs(
        tmp_path
    )
    assert set(sources) == set(packets) == {spec.scenario_id for spec in _portfolio._SCENARIOS}
    contracts = {
        spec.scenario_id: _portfolio._scenario_contract(
            spec,
            sources[spec.scenario_id],
            packets[spec.scenario_id],
        )
        for spec in _portfolio._SCENARIOS
    }
    _portfolio._validate_contrast_source_contracts(contracts)
