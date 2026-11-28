"""Contract, boundary, and zero-Any tests for benchmark factorial contrasts."""

from __future__ import annotations

import ast
import copy
from collections.abc import Mapping
import io
import math
from pathlib import Path
import tokenize
from types import MappingProxyType
from typing import cast

import pytest

from loopx.canary.maintainability_ratchet import (
    DECISION_POINT_LIMIT,
    DECISION_STATEMENT_LIMIT,
    _FunctionMetrics,
    _iter_functions,
)
from loopx.capabilities import benchmark_toolkit
from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_FACTORIAL_CONTRAST_SCHEMA_VERSION,
    build_benchmark_factorial_contrasts,
    build_benchmark_four_arm_contract,
    compact_benchmark_four_arm_contract,
)
from loopx.capabilities.benchmark_toolkit.factorial_contrast import (
    BENCHMARK_FACTORIAL_CONTRAST_SCHEMA_VERSION as DIRECT_SCHEMA_VERSION,
    benchmark_metric_comparison_value,
    build_benchmark_factorial_contrasts as direct_build_factorial_contrasts,
    build_benchmark_metric_delta,
)


def _freeze_mapping(payload: Mapping[str, object]) -> MappingProxyType[str, object]:
    frozen: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, Mapping):
            frozen[key] = _freeze_mapping(value)
        elif isinstance(value, list):
            frozen[key] = [
                _freeze_mapping(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            frozen[key] = value
    return MappingProxyType(frozen)


def _make_contract() -> dict[str, object]:
    return compact_benchmark_four_arm_contract(
        build_benchmark_four_arm_contract(
            base_goal_text="Solve the benchmark case.",
            domain_hint="Verify all boundary constraints independently.",
            hint_id="swe_hint",
            domain_hint_independent_of_loopx=True,
        )
    )


def _make_row(
    *,
    run_id: str,
    arm_id: str,
    arm_role: str,
    value: int,
    anchor_run_id: str | None = None,
    runtime: dict[str, str] | None = None,
    score_countable: object = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "benchmark_id": "swe@1",
        "study_id": "study-1",
        "case_id": "case-1",
        "run_id": run_id,
        "arm_id": arm_id,
        "arm_role": arm_role,
        "treatment_fidelity": "not_applicable" if arm_role == "baseline" else "qualified",
        "claim_scope": "matched_study",
        "model_id": "model-a",
        "comparison_protocol_id": "proto-a",
        "runner_revision": "rev-001",
        "primary_metric": "feature_pass",
        "orchestrator_runtime": runtime,
        "countability": {"score_countable": score_countable}
        if score_countable is not None
        else None,
        "metrics": {
            "feature_pass": {"value": value, "total": 20, "higher_is_better": True},
        },
    }
    if anchor_run_id is not None:
        payload["comparison_anchor_run_id"] = anchor_run_id
    return payload


def _make_four_rows() -> list[dict[str, object]]:
    loopx_rt = {"provider_id": "loopx", "version": "2.4.1", "revision": "abc123"}
    return [
        _make_row(run_id="r-gp", arm_id="goal_plain", arm_role="baseline", value=4),
        _make_row(
            run_id="r-lp",
            arm_id="loopx_plain",
            arm_role="treatment",
            value=8,
            anchor_run_id="r-gp",
            runtime=loopx_rt,
        ),
        _make_row(
            run_id="r-gh",
            arm_id="goal_swe_hint",
            arm_role="control",
            value=6,
            anchor_run_id="r-gp",
        ),
        _make_row(
            run_id="r-lh",
            arm_id="loopx_swe_hint",
            arm_role="treatment",
            value=13,
            anchor_run_id="r-gh",
            runtime=loopx_rt,
        ),
    ]


def _ast_any_count(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
        or (isinstance(node, ast.ImportFrom) and any(alias.name == "Any" for alias in node.names))
    )


def _ignore_comment_count(source: str) -> int:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return sum(
        1
        for tok in tokens
        if tok.type == tokenize.COMMENT and ("type: ignore" in tok.string or "noqa" in tok.string)
    )


def test_factorial_contrast_zero_any_size_complexity_and_exports() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "loopx" / "capabilities" / "benchmark_toolkit" / "factorial_contrast.py"
    for path in (target, Path(__file__)):
        src = path.read_text(encoding="utf-8")
        assert _ast_any_count(src) == 0, path
        assert _ignore_comment_count(src) == 0, path

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 550
    for qname, fn in _iter_functions(ast.parse(target.read_text(encoding="utf-8")).body):
        metrics = _FunctionMetrics(fn)
        metrics.visit(fn)
        assert metrics.statement_count <= DECISION_STATEMENT_LIMIT, qname
        assert metrics.decision_points <= DECISION_POINT_LIMIT, qname

    assert BENCHMARK_FACTORIAL_CONTRAST_SCHEMA_VERSION == DIRECT_SCHEMA_VERSION
    assert benchmark_toolkit.build_benchmark_factorial_contrasts is direct_build_factorial_contrasts


def test_metric_delta_and_comparison_value_boundaries() -> None:
    assert build_benchmark_metric_delta(
        {"value": 1, "unit": "count"}, {"value": 2, "unit": "sec"}
    )["comparison_unavailable_reason"] == "unit_mismatch"
    assert build_benchmark_metric_delta(
        {"value": 1, "higher_is_better": True}, {"value": 2, "higher_is_better": False}
    )["comparison_unavailable_reason"] == "direction_mismatch"
    assert build_benchmark_metric_delta(
        {"value": 1, "total": 10}, {"value": 2}
    )["comparison_unavailable_reason"] == "denominator_presence_mismatch"
    assert build_benchmark_metric_delta(
        {"value": 1, "total": 0}, {"value": 2, "total": 10}
    )["comparison_unavailable_reason"] == "non_positive_denominator"
    assert build_benchmark_metric_delta(
        {"value": 1, "total": False}, {"value": 2, "total": 10}
    )["comparison_unavailable_reason"] == "non_positive_denominator"

    bool_total_delta = build_benchmark_metric_delta(
        {"value": 1, "total": True, "higher_is_better": True},
        {"value": 2, "total": True, "higher_is_better": True},
    )
    assert "delta_rate" not in bool_total_delta
    assert bool_total_delta["direction"] == "improved"
    assert benchmark_metric_comparison_value(bool_total_delta) == 1.0

    regressed = build_benchmark_metric_delta(
        {"value": 5, "total": 10, "higher_is_better": False},
        {"value": 6, "total": 10, "higher_is_better": False},
    )
    assert regressed["direction"] == "regressed"
    assert benchmark_metric_comparison_value(regressed) == pytest.approx(0.1)

    flat = build_benchmark_metric_delta(
        {"value": 5, "higher_is_better": True}, {"value": 5, "higher_is_better": True}
    )
    assert flat["direction"] == "flat"
    assert benchmark_metric_comparison_value({"comparison_unavailable_reason": "unit_mismatch", "delta": 1.0}) is None
    assert benchmark_metric_comparison_value({"delta": math.nan}) is None


def test_build_factorial_contrasts_mappingproxy_parity_and_malformed_inputs() -> None:
    contract = _make_contract()
    rows = _make_four_rows()
    expected = build_benchmark_factorial_contrasts(rows, contract)
    frozen_actual = build_benchmark_factorial_contrasts(
        [_freeze_mapping(row) for row in rows], _freeze_mapping(contract)
    )
    assert frozen_actual == expected
    assert len(expected) == 1
    assert expected[0]["factorial_contrast_countable"] is True
    interaction = expected[0]["interaction_contrast"]
    assert isinstance(interaction, Mapping)
    contrasts_map = interaction["metric_contrasts"]
    assert isinstance(contrasts_map, Mapping)
    feature_metric = contrasts_map["feature_pass"]
    assert isinstance(feature_metric, Mapping)
    assert feature_metric["difference_in_differences"] == 3.0

    # Non-matching arm rows are ignored; malformed non-mapping countability fails closed.
    bad_rows = copy.deepcopy(rows)
    bad_rows[0]["countability"] = None
    unmatched = {"benchmark_id": "swe@1", "study_id": "study-1", "case_id": "case-1", "arm_id": "explore_arm"}
    degraded = build_benchmark_factorial_contrasts([unmatched, *bad_rows], contract)
    assert len(degraded) == 1
    assert degraded[0]["factorial_contrast_countable"] is False
    reasons = degraded[0]["reason_codes"]
    assert isinstance(reasons, list) and "cell_has_no_score_countable_run" in reasons

    assert build_benchmark_factorial_contrasts([unmatched], contract) == []

    bad_contract = copy.deepcopy(contract)
    arms = bad_contract["arms"]
    assert isinstance(arms, list) and isinstance(arms[0], dict)
    arms[0]["task_goal_sha256"] = "not-a-sha256"
    with pytest.raises(ValueError, match="sha256"):
        build_benchmark_factorial_contrasts(rows, bad_contract)

    with pytest.raises(TypeError, match="object"):
        build_benchmark_factorial_contrasts(rows, cast(Mapping[str, object], "not-a-mapping"))
