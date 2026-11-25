"""Modularization and Zero-Any contract tests for model behavior portfolio."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from pathlib import Path
import pytest

from loopx.control_plane.testing import (
    actual_default_model_behavior_catalog as _catalog,
    actual_default_model_behavior_portfolio as _portfolio,
    actual_default_model_behavior_sources as _sources,
    actual_default_model_behavior_validators as _validators,
)
from loopx.control_plane.testing.model_behavior_qualification import (
    ModelBehaviorActor,
)
from loopx.control_plane.testing.onboarding_model_behavior_qualification import (
    OnboardingModelBehaviorActor,
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
        assert ("type" + ": ignore") not in source
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
    assert set(sources) == set(packets) == {
        spec.scenario_id for spec in _portfolio._SCENARIOS
    }
    contracts = {
        spec.scenario_id: _portfolio._scenario_contract(
            spec,
            sources[spec.scenario_id],
            packets[spec.scenario_id],
        )
        for spec in _portfolio._SCENARIOS
    }
    _portfolio._validate_contrast_source_contracts(contracts)


@pytest.mark.parametrize(
    ("raw_violations", "expected_ok", "expected_errors"),
    [
        (None, True, []),
        ([], True, []),
        (["blocked_policy"], False, ["blocked_policy"]),
        ("blocked_policy", False, ["invalid_safety_violations_shape"]),
        ({"reason": "blocked_policy"}, False, ["invalid_safety_violations_shape"]),
        (42, False, ["invalid_safety_violations_shape"]),
    ],
)
def test_receipt_alignment_fails_closed_on_malformed_safety_violations(
    tmp_path: Path,
    raw_violations: object,
    expected_ok: bool,
    expected_errors: list[str],
) -> None:
    sources, packets = _portfolio.build_actual_default_model_behavior_scenario_inputs(
        tmp_path
    )
    turn_spec = next(
        spec for spec in _portfolio._SCENARIOS if spec.scenario_id == "turn_human_gate"
    )
    turn_expected = _portfolio._scenario_contract(
        turn_spec, sources[turn_spec.scenario_id], packets[turn_spec.scenario_id]
    )
    turn_receipt: dict[str, object] = {
        **turn_expected,
        "safety_violations": raw_violations,
    }
    ok, errors = _portfolio._receipt_alignment(turn_spec, turn_receipt, turn_expected)
    assert ok is expected_ok
    assert errors == expected_errors

    onb_spec = next(
        spec
        for spec in _portfolio._SCENARIOS
        if spec.scenario_id == "onboarding_connect_default"
    )
    onb_expected = _portfolio._scenario_contract(
        onb_spec, sources[onb_spec.scenario_id], packets[onb_spec.scenario_id]
    )
    onb_receipt: dict[str, object] = {
        "next_action": onb_spec.expected_route,
        "source_aligned": True,
        "safety_violations": raw_violations,
    }
    onb_ok, onb_errors = _portfolio._receipt_alignment(
        onb_spec, onb_receipt, onb_expected
    )
    assert onb_ok is expected_ok
    assert onb_errors == expected_errors


@pytest.mark.parametrize(
    ("target_scenario", "malformed_value"),
    [
        ("turn_human_gate", "blocked_policy"),
        ("turn_human_gate", {"reason": "blocked_policy"}),
        ("turn_human_gate", 42),
        ("onboarding_connect_default", "onboarding_safety_block"),
        ("onboarding_connect_default", {"reason": "onboarding_safety_block"}),
        ("onboarding_connect_default", 42),
    ],
)
def test_portfolio_runner_rejects_malformed_safety_violations_in_both_arms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_scenario: str,
    malformed_value: object,
) -> None:
    sources, packets = _portfolio.build_actual_default_model_behavior_scenario_inputs(
        tmp_path
    )
    contracts = {
        spec.scenario_id: _portfolio._scenario_contract(
            spec, sources[spec.scenario_id], packets[spec.scenario_id]
        )
        for spec in _portfolio._SCENARIOS
    }
    injected_scenario: str | None = None

    def _run_turn(
        packet: Mapping[str, object],
        *,
        qualification_id: str,
        arm: str,
        actor: ModelBehaviorActor,
        semantic_contract_required: bool = False,
        semantic_contract_fields: Sequence[str] = (),
    ) -> dict[str, object]:
        del packet, arm, actor, semantic_contract_required
        scenario_id = qualification_id.split(":")[1]
        contract = dict(contracts[scenario_id])
        raw_actions = contract.get("intended_action_kinds")
        if isinstance(raw_actions, list):
            actions: list[object] = list(raw_actions)
        elif scenario_id == "turn_planning_horizon_strategic_context":
            actions = ["inspect", "test", "writeback", "spend"]
        else:
            actions = ["complete_slice"]
        receipt: dict[str, object] = {
            **contract,
            "intended_action_kinds": actions,
            "semantic_contract_complete": bool(semantic_contract_fields),
            "safety_violations": [],
        }
        if injected_scenario == scenario_id:
            receipt["safety_violations"] = malformed_value
        return receipt

    def _run_onboarding(
        packet: Mapping[str, object],
        *,
        qualification_id: str,
        phase: str,
        actor: OnboardingModelBehaviorActor,
    ) -> dict[str, object]:
        del packet, phase, actor
        scenario_id = qualification_id.split(":")[1]
        spec = next(
            item for item in _portfolio._SCENARIOS if item.scenario_id == scenario_id
        )
        receipt: dict[str, object] = {
            "next_action": spec.expected_route,
            "source_aligned": True,
            "safety_violations": [],
        }
        if injected_scenario == scenario_id:
            receipt["safety_violations"] = malformed_value
        return receipt

    def _tool_actor(scenario_id: str) -> dict[str, object]:
        base: dict[str, object] = {
            **contracts[scenario_id],
            "qualification_passed": True,
            "safety_violations": [],
        }
        if scenario_id == "turn_required_vision_replan":
            base["semantic_action_accepted"] = True
            base["selected_semantic_outcomes"] = ["fresh_vision_path_outcome"]
        return base

    monkeypatch.setattr(_portfolio, "run_model_behavior_qualification_arm", _run_turn)
    monkeypatch.setattr(
        _portfolio, "run_onboarding_model_behavior_phase", _run_onboarding
    )

    def _run_portfolio(qual_id: str) -> dict[str, object]:
        return _portfolio.run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id=qual_id,
            turn_actor=lambda _: {},
            onboarding_actor=lambda _: {},
            selected_todo_actor=lambda _: _tool_actor("turn_selected_todo"),
            replan_semantic_action_actor=lambda _: _tool_actor(
                "turn_required_vision_replan"
            ),
            scoped_gate_successor_actor=lambda _: _tool_actor(
                "turn_scoped_gate_successor_replan"
            ),
            capability_monitor_repair_actor=lambda _: _tool_actor(
                "turn_capability_monitor_repair"
            ),
            terminal_settlement_actor=lambda _: _tool_actor(
                "turn_terminal_settlement"
            ),
        )

    baseline = _run_portfolio("clean-baseline")
    assert baseline["qualification_passed"] is True
    assert baseline["failure_count"] == 0

    injected_scenario = target_scenario
    malformed = _run_portfolio("malformed-safety")
    assert malformed["qualification_passed"] is False
    assert malformed["failure_count"] == 1
    scenarios_list = malformed["scenarios"]
    assert isinstance(scenarios_list, list)
    failing = next(
        row
        for row in scenarios_list
        if isinstance(row, dict) and row.get("scenario_id") == target_scenario
    )
    assert failing["status"] == "failed"
    assert failing["failure_codes"] == ["invalid_safety_violations_shape"]
