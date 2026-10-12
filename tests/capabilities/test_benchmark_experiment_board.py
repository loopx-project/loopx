from __future__ import annotations

import copy
import itertools
import json
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
    build_benchmark_experiment_board,
    build_benchmark_four_arm_contract,
    compact_benchmark_four_arm_contract,
    default_benchmark_experiment_board_path,
    normalize_benchmark_experiment_board_row,
    preview_benchmark_experiment_board_reconcile,
    read_benchmark_experiment_board_rows,
    render_benchmark_experiment_board_markdown,
    upsert_benchmark_experiment_board_row,
)
from loopx.capabilities.catalog import (
    build_capability_detail_packet,
    render_capability_detail_markdown,
)
from loopx.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def _row(
    *,
    run_id: str,
    arm_id: str,
    arm_role: str,
    protocol_id: str,
    observed_at: str,
    f2p: int,
    comparison_anchor_run_id: str | None = None,
    claim_scope: str = "matched_study",
    status: str = "completed",
    score_countable: bool = True,
    fidelity: str = "qualified",
    insight_status: str = "complete",
    orchestrator_runtime: dict[str, str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
        "benchmark_id": "fixture-swe@1",
        "study_id": "native-goal-study",
        "case_id": "case-12",
        "run_id": run_id,
        "arm_id": arm_id,
        "arm_role": arm_role,
        "attempt": 1,
        "status": status,
        "observed_at": observed_at,
        "model_id": "solver-model-v1",
        "protocol_id": protocol_id,
        "comparison_protocol_id": "native-goal-corrected-v1",
        "claim_scope": claim_scope,
        "primary_metric": "feature_pass",
        "guardrail_metrics": ["preservation_pass", "reward"],
        "metrics": {
            "feature_pass": {
                "value": f2p,
                "total": 66,
                "higher_is_better": True,
            },
            "preservation_pass": {
                "value": 293,
                "total": 293,
                "higher_is_better": True,
            },
            "reward": {"value": 0, "higher_is_better": True},
        },
        "countability": {
            "integrity_qualified": score_countable,
            "official_result_present": status == "completed",
            "score_countable": score_countable,
        },
        "treatment_fidelity": fidelity,
        "effort": {
            "duration_ms": 1000,
            "agent_steps": 10,
            "estimated_cost_usd": 0.25,
        },
        "insight": {
            "status": insight_status,
            "classification": "boundary-mismatch",
            "artifact_ref": "case-12-insight.json",
        },
    }
    if comparison_anchor_run_id is not None:
        payload["comparison_anchor_run_id"] = comparison_anchor_run_id
    if orchestrator_runtime is not None:
        payload["orchestrator_runtime"] = orchestrator_runtime
    return payload


def _baseline(**overrides: object) -> dict[str, object]:
    payload = _row(
        run_id="baseline-12",
        arm_id="native-goal",
        arm_role="baseline",
        protocol_id="runner-v21",
        observed_at="2026-08-18T00:00:00+00:00",
        f2p=0,
        fidelity="not_applicable",
    )
    payload.update(overrides)
    return payload


def _running_baseline(**overrides: object) -> dict[str, object]:
    payload = _baseline(
        status="running",
        metrics={},
        countability={
            "integrity_qualified": False,
            "official_result_present": False,
            "score_countable": False,
        },
        insight={"status": "pending"},
    )
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "before,after,higher,expected",
    [
        (50, 60, True, "regressed"),
        (50, 100, True, "flat"),
        (50, 120, True, "improved"),
        (50, 60, False, "improved"),
        (50, 100, False, "flat"),
        (50, 120, False, "regressed"),
    ],
)
def test_metric_direction_uses_ratio_scale(before, after, higher, expected):
    baseline = _baseline()
    candidate = _baseline(
        run_id="candidate",
        arm_id="treatment",
        arm_role="treatment",
        treatment_fidelity="qualified",
        comparison_anchor_run_id=baseline["run_id"],
    )
    baseline["metrics"]["feature_pass"] = {
        "value": before,
        "total": 100,
        "higher_is_better": higher,
    }
    candidate["metrics"]["feature_pass"] = {
        "value": after,
        "total": 200,
        "higher_is_better": higher,
    }
    comparison = build_benchmark_experiment_board([baseline, candidate])["comparisons"][
        0
    ]
    metric = comparison["metric_deltas"]["feature_pass"]
    assert comparison["matched_pair_countable"] is True
    assert metric["delta"] == after - before
    assert metric["delta_rate"] == pytest.approx(after / 200 - before / 100)
    assert metric["direction"] == expected
    rendered = render_benchmark_experiment_board_markdown(
        build_benchmark_experiment_board([baseline, candidate])
    )
    assert f"{(after / 200 - before / 100) * 100:g} pp" in rendered


@pytest.mark.parametrize(
    "before,after,reason",
    [
        ({"value": 1000, "unit": "ms"}, {"value": 2, "unit": "s"}, "unit_mismatch"),
        ({"value": 1, "unit": "ms"}, {"value": 2}, "unit_mismatch"),
        (
            {"value": 1, "higher_is_better": True},
            {"value": 2, "higher_is_better": False},
            "direction_mismatch",
        ),
        ({"value": 1, "higher_is_better": True}, {"value": 2}, "direction_mismatch"),
        ({"value": 1, "total": 10}, {"value": 2}, "denominator_presence_mismatch"),
        (
            {"value": 1, "total": 10},
            {"value": 0, "total": 0},
            "non_positive_denominator",
        ),
    ],
)
def test_primary_metric_incompatibility_excludes_pair(before, after, reason):
    baseline = _baseline()
    candidate = _baseline(
        run_id="candidate",
        arm_id="treatment",
        arm_role="treatment",
        treatment_fidelity="qualified",
        comparison_anchor_run_id=baseline["run_id"],
    )
    baseline["metrics"]["feature_pass"] = before
    candidate["metrics"]["feature_pass"] = after
    comparison = build_benchmark_experiment_board([baseline, candidate])["comparisons"][
        0
    ]
    assert comparison["matched_pair_countable"] is False
    assert "primary_metric_definition_mismatch" in comparison["reason_codes"]
    metric = comparison["metric_deltas"]["feature_pass"]
    assert metric["comparison_unavailable_reason"] == reason
    assert "delta" not in metric and "direction" not in metric


def test_incompatible_auxiliary_metric_preserves_primary_pair():
    baseline = _baseline()
    candidate = _baseline(
        run_id="candidate",
        arm_id="treatment",
        arm_role="treatment",
        treatment_fidelity="qualified",
        comparison_anchor_run_id=baseline["run_id"],
    )
    candidate["metrics"]["preservation_pass"]["unit"] = "seconds"
    comparison = build_benchmark_experiment_board([baseline, candidate])["comparisons"][
        0
    ]
    assert comparison["matched_pair_countable"] is True
    assert comparison["metric_deltas"]["feature_pass"]["direction"] == "flat"
    assert (
        comparison["metric_deltas"]["preservation_pass"][
            "comparison_unavailable_reason"
        ]
        == "unit_mismatch"
    )
    # Historical scalar rows need not acquire optional metadata to remain readable.
    baseline["metrics"]["feature_pass"] = {"value": 1}
    candidate["metrics"]["feature_pass"] = {"value": 2}
    comparison = build_benchmark_experiment_board([baseline, candidate])["comparisons"][
        0
    ]
    assert comparison["matched_pair_countable"] is True
    assert comparison["metric_deltas"]["feature_pass"] == {
        "baseline_value": 1,
        "candidate_value": 2,
        "delta": 1.0,
    }


def test_factorial_ratio_interaction_uses_one_scale():
    rows = _four_arm_rows()
    for row, value, total in zip(rows, [50, 60, 50, 55], [100, 200, 100, 100]):
        row["metrics"]["preservation_pass"] = {
            "value": value,
            "total": total,
            "higher_is_better": True,
        }
    contrast = build_benchmark_experiment_board(
        rows,
        four_arm_contract=_four_arm_contract(),
    )["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is True
    interaction = contrast["interaction_contrast"]["metric_contrasts"][
        "preservation_pass"
    ]
    assert interaction["difference_in_differences"] == -5
    assert interaction["difference_in_differences_rate"] == pytest.approx(0.25)
    assert interaction["direction"] == "improved"
    # Two individually compatible effects still cannot be subtracted across units.
    for row in rows[2:]:
        row["metrics"]["preservation_pass"]["unit"] = "different-unit"
    contrast = build_benchmark_experiment_board(
        rows,
        four_arm_contract=_four_arm_contract(),
    )["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is True
    assert contrast["interaction_contrast"]["metric_contrasts"][
        "preservation_pass"
    ] == {
        "comparison_unavailable_reason": "unit_mismatch",
    }


def test_factorial_zero_primary_denominator_is_not_countable():
    rows = _four_arm_rows()
    for row in rows:
        row["metrics"]["feature_pass"].update(value=0, total=0)
    contrast = build_benchmark_experiment_board(
        rows,
        four_arm_contract=_four_arm_contract(),
    )["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is False
    assert "primary_metric_definition_mismatch" in contrast["reason_codes"]


@pytest.mark.parametrize("total", [None, 1e-308])
def test_non_finite_comparison_does_not_publish_a_direction(total):
    baseline = _baseline()
    candidate = _baseline(
        run_id="candidate",
        arm_id="treatment",
        arm_role="treatment",
        treatment_fidelity="qualified",
        comparison_anchor_run_id=baseline["run_id"],
    )
    for row, value in [(baseline, -1e308), (candidate, 1e308)]:
        row["metrics"]["feature_pass"] = {"value": value, "higher_is_better": True}
        if total is not None:
            row["metrics"]["feature_pass"].update(
                value=1 if row is baseline else 2, total=total
            )
    comparison = build_benchmark_experiment_board([baseline, candidate])["comparisons"][
        0
    ]
    assert comparison["matched_pair_countable"] is False
    assert comparison["metric_deltas"]["feature_pass"] == {
        "baseline_value": baseline["metrics"]["feature_pass"]["value"],
        "candidate_value": candidate["metrics"]["feature_pass"]["value"],
        "comparison_unavailable_reason": "non_finite_difference",
    }


@pytest.mark.parametrize("metric_name", ["feature_pass", "preservation_pass"])
def test_factorial_non_finite_raw_interaction_is_unavailable(metric_name):
    rows = _four_arm_rows()
    for row, value in zip(rows, [1e308, 0, 0, 1e308]):
        row["metrics"][metric_name] = {
            "value": value,
            "total": 1e308,
            "higher_is_better": True,
        }
    contrast = build_benchmark_experiment_board(
        rows,
        four_arm_contract=_four_arm_contract(),
    )["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is (metric_name != "feature_pass")
    assert contrast["interaction_contrast"]["metric_contrasts"][metric_name] == {
        "comparison_unavailable_reason": "non_finite_difference",
    }


def _four_arm_contract() -> dict[str, object]:
    return compact_benchmark_four_arm_contract(
        build_benchmark_four_arm_contract(
            base_goal_text="Complete the requested task.",
            domain_hint="Independently validate every public requirement.",
            hint_id="swe_hint",
            domain_hint_independent_of_loopx=True,
        )
    )


def _four_arm_rows() -> list[dict[str, object]]:
    runner_revision = "0123456789abcdef"
    loopx_runtime = {
        "provider_id": "loopx",
        "version": "0.6.0",
        "revision": "abcdef0123456789",
    }
    goal_plain = _baseline(
        run_id="goal-plain-12",
        arm_id="goal_plain",
        runner_revision=runner_revision,
    )
    goal_plain["metrics"] = {
        **goal_plain["metrics"],
        "feature_pass": {
            "value": 10,
            "total": 66,
            "higher_is_better": True,
        },
    }
    loopx_plain = _row(
        run_id="loopx-plain-12",
        arm_id="loopx_plain",
        arm_role="treatment",
        protocol_id="loopx-plain-v1",
        observed_at="2026-08-18T00:10:00+00:00",
        f2p=14,
        comparison_anchor_run_id="goal-plain-12",
        orchestrator_runtime=loopx_runtime,
    )
    goal_hint = _row(
        run_id="goal-hint-12",
        arm_id="goal_swe_hint",
        arm_role="control",
        protocol_id="goal-hint-v1",
        observed_at="2026-08-18T00:20:00+00:00",
        f2p=13,
        comparison_anchor_run_id="goal-plain-12",
    )
    loopx_hint = _row(
        run_id="loopx-hint-12",
        arm_id="loopx_swe_hint",
        arm_role="treatment",
        protocol_id="loopx-hint-v1",
        observed_at="2026-08-18T00:30:00+00:00",
        f2p=20,
        comparison_anchor_run_id="goal-hint-12",
        orchestrator_runtime=loopx_runtime,
    )
    for row in (loopx_plain, goal_hint, loopx_hint):
        row["runner_revision"] = runner_revision
    return [goal_plain, loopx_plain, goal_hint, loopx_hint]


def _connected_goal_registries(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "canonical-project"
    source_registry = project / ".loopx" / "registry.json"
    source_registry.parent.mkdir(parents=True)
    goal = {
        "id": "fixture-goal",
        "objective": "Run a public benchmark study.",
        "repo": str(project),
    }
    source_registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "source-runtime"),
                "goals": [goal],
            }
        ),
        encoding="utf-8",
    )
    global_registry = tmp_path / "shared-runtime" / "registry.global.json"
    global_registry.parent.mkdir(parents=True)
    global_registry.write_text(
        json.dumps(
            {
                "registry_role": "global-local",
                "common_runtime_root": str(global_registry.parent),
                "goals": [{**goal, "source_registry": str(source_registry)}],
            }
        ),
        encoding="utf-8",
    )
    return project, global_registry


@pytest.mark.parametrize("scope", ["benchmark_id", "study_id", "case_id"])
def test_board_resolves_reused_run_ids_within_the_persisted_scope(scope: str) -> None:
    baseline = _baseline()
    treatment = _row(
        run_id="treatment",
        arm_id="loopx",
        arm_role="treatment",
        protocol_id="runner-v21",
        observed_at="2026-08-18T00:01:00+00:00",
        f2p=20,
        comparison_anchor_run_id=baseline["run_id"],
    )
    other_baseline, other_treatment = copy.deepcopy(baseline), copy.deepcopy(treatment)
    other_baseline[scope] = other_treatment[scope] = "z-other"
    other_baseline["metrics"]["feature_pass"]["value"] = 60
    other_treatment["metrics"]["feature_pass"]["value"] = 65
    rows = [baseline, treatment, other_baseline, other_treatment]
    for ordering in itertools.permutations(rows):
        board = build_benchmark_experiment_board(ordering)
        assert board["summary"]["matched_pair_countable_count"] == 2
        assert [
            c["metric_deltas"]["feature_pass"]["delta"] for c in board["comparisons"]
        ] == [20, 5]
    if scope != "case_id":
        filtered = build_benchmark_experiment_board(rows, **{scope: baseline[scope]})
        assert filtered["comparisons"] == board["comparisons"][:1]


def test_board_never_falls_back_to_an_anchor_in_another_case() -> None:
    baseline = _baseline(case_id="other-case")
    treatment = _row(
        run_id="treatment",
        arm_id="loopx",
        arm_role="treatment",
        protocol_id="runner-v21",
        observed_at="2026-08-18T00:01:00+00:00",
        f2p=20,
        comparison_anchor_run_id=baseline["run_id"],
    )
    comparison = build_benchmark_experiment_board([baseline, treatment])["comparisons"][
        0
    ]
    assert comparison["reason_codes"] == ["comparison_anchor_run_missing"]
    assert comparison["metric_deltas"] == {}


def test_board_rejects_duplicate_full_run_identities() -> None:
    baseline = _baseline()
    conflicting = copy.deepcopy(baseline)
    conflicting["metrics"]["feature_pass"]["value"] = 60
    with pytest.raises(ValueError, match="duplicate experiment-board run identity"):
        build_benchmark_experiment_board([baseline, conflicting])


def test_row_rejects_raw_or_path_like_fields() -> None:
    raw_field = _baseline(raw_trajectory="private content")
    with pytest.raises(ValueError, match="unsupported fields"):
        normalize_benchmark_experiment_board_row(raw_field)

    path_ref = _baseline()
    path_ref["insight"] = {
        "status": "complete",
        "artifact_ref": "/private/case-12-insight.json",
    }
    with pytest.raises(ValueError, match="public-safe token"):
        normalize_benchmark_experiment_board_row(path_ref)


def test_row_rejects_illegal_role_and_countability_states() -> None:
    baseline_with_anchor = _baseline(comparison_anchor_run_id="other-run")
    with pytest.raises(ValueError, match="baseline rows cannot name"):
        normalize_benchmark_experiment_board_row(baseline_with_anchor)

    standard_without_anchor = _row(
        run_id="control-12",
        arm_id="loop-control",
        arm_role="control",
        protocol_id="runner-v23",
        observed_at="2026-08-18T00:20:00+00:00",
        f2p=64,
    )
    with pytest.raises(ValueError, match="non-baseline rows must name"):
        normalize_benchmark_experiment_board_row(standard_without_anchor)

    explore_with_study_claim = _row(
        run_id="explore-12",
        arm_id="combined-critic",
        arm_role="explore",
        protocol_id="runner-v23",
        observed_at="2026-08-18T00:40:00+00:00",
        f2p=66,
        comparison_anchor_run_id="control-12",
    )
    with pytest.raises(ValueError, match="explore rows must use diagnostic_only"):
        normalize_benchmark_experiment_board_row(explore_with_study_claim)

    running_countable = _baseline(status="running")
    with pytest.raises(ValueError, match="score_countable requires completed"):
        normalize_benchmark_experiment_board_row(running_countable)

    unqualified_countable = _baseline(
        countability={
            "integrity_qualified": False,
            "official_result_present": True,
            "score_countable": True,
        }
    )
    with pytest.raises(ValueError, match="requires integrity_qualified"):
        normalize_benchmark_experiment_board_row(unqualified_countable)


def test_board_keeps_standard_and_explore_lanes_and_compares_explicit_anchor() -> None:
    baseline = _baseline()
    treatment = _row(
        run_id="control-12",
        arm_id="loop-control",
        arm_role="control",
        protocol_id="runner-v23",
        observed_at="2026-08-18T00:20:00+00:00",
        f2p=64,
        comparison_anchor_run_id="baseline-12",
        orchestrator_runtime={
            "provider_id": "loopx",
            "version": "0.5.2",
            "revision": "0123456789abcdef",
        },
    )
    explore = _row(
        run_id="explore-12",
        arm_id="combined-critic",
        arm_role="explore",
        protocol_id="runner-v23",
        observed_at="2026-08-18T00:40:00+00:00",
        f2p=66,
        comparison_anchor_run_id="control-12",
        claim_scope="diagnostic_only",
    )

    board = build_benchmark_experiment_board([baseline, treatment, explore])

    assert board["summary"]["arm_role_counts"] == {
        "baseline": 1,
        "control": 1,
        "explore": 1,
        "treatment": 0,
    }
    assert board["summary"]["matched_pair_countable_count"] == 2
    assert board["summary"]["orchestrator_runtime_counts"] == {
        "loopx@0.5.2@0123456789abcdef": 1,
        "none": 2,
    }
    assert board["summary"]["comparison_arm_role_counts"] == {
        "control": {"all": 1, "matched_pair_countable": 1},
        "explore": {"all": 1, "matched_pair_countable": 1},
        "treatment": {"all": 0, "matched_pair_countable": 0},
    }
    assert board["summary"]["comparison_claim_scope_counts"] == {
        "diagnostic_only": {"all": 1, "matched_pair_countable": 1},
        "inventory_only": {"all": 0, "matched_pair_countable": 0},
        "matched_study": {"all": 1, "matched_pair_countable": 1},
    }
    rendered = render_benchmark_experiment_board_markdown(board)
    assert "- Countable matched-study comparisons: `1`" in rendered
    assert "- Countable diagnostic-only comparisons: `1`" in rendered
    comparisons = {item["candidate_arm_role"]: item for item in board["comparisons"]}
    assert comparisons["control"]["metric_deltas"]["feature_pass"]["delta"] == 64
    assert comparisons["control"]["metric_deltas"]["feature_pass"]["direction"] == (
        "improved"
    )
    assert comparisons["control"]["warning_codes"] == [
        "exact_protocol_revision_differs"
    ]
    assert board["runs"][1]["orchestrator_runtime"] == {
        "provider_id": "loopx",
        "revision": "0123456789abcdef",
        "version": "0.5.2",
    }
    assert "loopx@0.5.2:0123456789ab" in rendered
    assert comparisons["explore"]["claim_scope"] == "diagnostic_only"
    assert comparisons["explore"]["comparison_anchor_arm_role"] == "control"
    assert comparisons["explore"]["metric_deltas"]["feature_pass"]["delta"] == 2
    assert board["agent_guidance"]["required_sequence"][0] == (
        "read_board_before_launch_or_case_selection"
    )


def test_board_projects_explicit_four_arm_conditional_effects_and_interaction() -> None:
    board = build_benchmark_experiment_board(
        _four_arm_rows(),
        four_arm_contract=_four_arm_contract(),
    )

    assert board["summary"]["factorial_contrast_count"] == 1
    assert board["summary"]["factorial_contrast_countable_count"] == 1
    contrast = board["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is True
    assert contrast["reason_codes"] == []
    effects = {
        item["effect"]: item["metric_deltas"]["feature_pass"]["delta"]
        for item in contrast["conditional_effects"]
    }
    assert effects == {
        "loopx_without_domain_hint": 4,
        "domain_hint_without_loopx": 3,
        "loopx_with_domain_hint": 7,
    }
    interaction = contrast["interaction_contrast"]["metric_contrasts"]["feature_pass"]
    assert interaction["difference_in_differences"] == 3
    assert interaction["direction"] == "improved"
    assert interaction["difference_in_differences_rate"] == pytest.approx(3 / 66)

    standard = {item["candidate_arm_id"]: item for item in board["comparisons"]}
    assert standard["loopx_swe_hint"]["matched_pair_countable"] is False
    assert standard["loopx_swe_hint"]["reason_codes"] == [
        "standard_arm_anchor_is_not_baseline"
    ]
    assert "Countable factorial contrasts: `1`" in (
        render_benchmark_experiment_board_markdown(board)
    )


def test_factorial_projection_fails_closed_on_ambiguous_or_misaligned_cells() -> None:
    rows = _four_arm_rows()
    duplicate = dict(rows[1])
    duplicate["run_id"] = "loopx-plain-12-r2"
    duplicate["observed_at"] = "2026-08-18T00:11:00+00:00"
    rows.append(duplicate)
    board = build_benchmark_experiment_board(
        rows,
        four_arm_contract=_four_arm_contract(),
    )
    contrast = board["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is False
    assert contrast["reason_codes"] == ["cell_has_ambiguous_score_countable_runs"]
    cells = {item["arm_id"]: item for item in contrast["cells"]}
    assert cells["loopx_plain"]["reason_codes"] == ["ambiguous_score_countable_runs"]

    rows = _four_arm_rows()
    rows[3]["comparison_anchor_run_id"] = "goal-plain-12"
    rows[3]["runner_revision"] = "different-revision"
    board = build_benchmark_experiment_board(
        rows,
        four_arm_contract=_four_arm_contract(),
    )
    contrast = board["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is False
    assert contrast["reason_codes"] == [
        "cell_comparison_anchor_run_mismatch",
        "runner_revision_mismatch",
    ]
    cells = {item["arm_id"]: item for item in contrast["cells"]}
    assert cells["loopx_swe_hint"]["reason_codes"] == ["comparison_anchor_run_mismatch"]

    rows = _four_arm_rows()
    rows[3]["orchestrator_runtime"] = {
        "provider_id": "loopx",
        "version": "0.6.0",
        "revision": "different-revision",
    }
    rows[3]["metrics"]["feature_pass"]["total"] = 67
    board = build_benchmark_experiment_board(
        rows,
        four_arm_contract=_four_arm_contract(),
    )
    contrast = board["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is False
    assert contrast["reason_codes"] == [
        "loopx_orchestrator_runtime_mismatch",
        "primary_metric_definition_mismatch",
    ]

    rows = _four_arm_rows()
    shared_runtime = rows[1]["orchestrator_runtime"]
    rows[0]["orchestrator_runtime"] = shared_runtime
    rows[2]["orchestrator_runtime"] = shared_runtime
    board = build_benchmark_experiment_board(
        rows,
        four_arm_contract=_four_arm_contract(),
    )
    contrast = board["factorial_contrasts"][0]
    assert contrast["factorial_contrast_countable"] is False
    assert contrast["reason_codes"] == ["factor_runtime_cohorts_not_distinct"]

    contract = _four_arm_contract()
    contract["primary_comparisons"][0]["effect"] = "mislabelled_effect"
    with pytest.raises(
        ValueError, match="primary comparisons do not match factor design"
    ):
        build_benchmark_experiment_board(
            _four_arm_rows(),
            four_arm_contract=contract,
        )


def test_locked_jsonl_upsert_updates_one_stable_run_row(tmp_path: Path) -> None:
    ledger = tmp_path / "experiment-board.jsonl"
    running = _baseline(
        status="running",
        metrics={},
        countability={
            "integrity_qualified": False,
            "official_result_present": False,
            "score_countable": False,
        },
        insight={"status": "pending"},
    )
    first = upsert_benchmark_experiment_board_row(ledger, running)
    second = upsert_benchmark_experiment_board_row(
        ledger, _baseline(arm_id="corrected-native-goal")
    )
    rows = read_benchmark_experiment_board_rows(ledger)

    assert first["write"]["status"] == "inserted"
    assert second["write"]["status"] == "updated"
    assert second["write"]["path_recorded"] is False
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["arm_id"] == "corrected-native-goal"
    assert "domain_state_key" not in rows[0]


def test_row_rejects_incomplete_or_path_like_orchestrator_runtime() -> None:
    missing_revision = _baseline(orchestrator_runtime={"provider_id": "loopx"})
    with pytest.raises(ValueError, match="orchestrator_runtime.revision"):
        normalize_benchmark_experiment_board_row(missing_revision)

    path_revision = _baseline(
        orchestrator_runtime={
            "provider_id": "loopx",
            "revision": "/private/runtime",
        }
    )
    with pytest.raises(ValueError, match="compact public-safe token"):
        normalize_benchmark_experiment_board_row(path_revision)


def test_upsert_rejects_stale_transition_after_terminal_state(tmp_path: Path) -> None:
    ledger = tmp_path / "experiment-board.jsonl"
    upsert_benchmark_experiment_board_row(ledger, _baseline())

    stale_running = _baseline(
        status="running",
        metrics={},
        countability={
            "integrity_qualified": False,
            "official_result_present": False,
            "score_countable": False,
        },
        insight={"status": "pending"},
    )
    with pytest.raises(ValueError, match="cannot move from completed to running"):
        upsert_benchmark_experiment_board_row(ledger, stale_running)

    rows = read_benchmark_experiment_board_rows(ledger)
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


def test_reconcile_promotes_terminal_and_skips_late_nonterminal_source() -> None:
    canonical = _running_baseline(observed_at="2026-08-18T00:10:00+00:00")
    source_running = _running_baseline(
        observed_at="2026-08-18T00:20:00+00:00",
        arm_id="provider-running",
    )
    source_terminal = _baseline(
        observed_at="2026-08-18T00:30:00+00:00",
        arm_id="provider-terminal",
    )
    stale_late_running = _running_baseline(
        observed_at="2026-08-18T00:40:00+00:00",
        arm_id="stale-provider",
    )

    rows, receipt = preview_benchmark_experiment_board_reconcile(
        [canonical],
        [stale_late_running, source_terminal, source_running],
    )

    assert rows == [normalize_benchmark_experiment_board_row(source_terminal)]
    assert receipt == {
        "source_row_count": 3,
        "candidate_run_count": 1,
        "inserted": 0,
        "updated": 1,
        "unchanged": 0,
        "stale_skipped": 1,
    }

    repeated, repeated_receipt = preview_benchmark_experiment_board_reconcile(
        rows,
        [stale_late_running, source_terminal, source_running],
    )
    assert repeated == rows
    assert repeated_receipt["unchanged"] == 1
    assert repeated_receipt["stale_skipped"] == 2


def test_reconcile_rejects_conflicting_terminal_sources() -> None:
    completed = _baseline(observed_at="2026-08-18T00:30:00+00:00")
    runner_invalid = _running_baseline(
        status="runner_invalid",
        observed_at="2026-08-18T00:40:00+00:00",
    )

    with pytest.raises(ValueError, match="conflicting terminal states"):
        preview_benchmark_experiment_board_reconcile(
            [],
            [completed, runner_invalid],
        )


def test_reconcile_rejects_older_terminal_conflict_with_canonical_row() -> None:
    canonical = _baseline(observed_at="2026-08-18T00:40:00+00:00")
    older_runner_invalid = _running_baseline(
        status="runner_invalid",
        observed_at="2026-08-18T00:30:00+00:00",
    )

    with pytest.raises(ValueError, match="conflicting terminal states"):
        preview_benchmark_experiment_board_reconcile(
            [canonical],
            [older_runner_invalid],
        )


def test_cli_previews_then_writes_and_reads_board(tmp_path: Path) -> None:
    row_path = tmp_path / "row.json"
    row_path.write_text(json.dumps(_baseline()), encoding="utf-8")
    command = [
        str(REPO_ROOT / "scripts/loopx"),
        "benchmark",
        "experiment-board-upsert",
        "--goal-id",
        "fixture-goal",
        "--project",
        str(tmp_path),
        "--row-json",
        str(row_path),
        "--format",
        "json",
    ]

    preview = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    preview_payload = json.loads(preview.stdout)
    ledger = default_benchmark_experiment_board_path(
        project=tmp_path, goal_id="fixture-goal"
    )
    assert preview_payload["write"]["status"] == "preview_inserted"
    assert preview_payload["write"]["write_performed"] is False
    assert not ledger.exists()

    executed = subprocess.run(
        [*command, "--execute"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    executed_payload = json.loads(executed.stdout)
    assert executed_payload["write"]["status"] == "inserted"
    assert executed_payload["path_recorded"] is False
    assert str(tmp_path) not in executed.stdout
    assert ledger.exists()

    shown = subprocess.run(
        [
            str(REPO_ROOT / "scripts/loopx"),
            "benchmark",
            "experiment-board-show",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    shown_payload = json.loads(shown.stdout)
    assert shown_payload["summary"]["run_count"] == 1
    assert shown_payload["agent_guidance"]["next_action"] == (
        "close_running_rows_or_review_matched_comparisons"
    )


def test_cli_show_projects_factorial_contrast_from_explicit_contract(
    tmp_path: Path,
) -> None:
    ledger = default_benchmark_experiment_board_path(
        project=tmp_path, goal_id="fixture-goal"
    )
    for row in _four_arm_rows():
        upsert_benchmark_experiment_board_row(ledger, row)
    contract_path = tmp_path / "four-arm-contract.json"
    contract_path.write_text(json.dumps(_four_arm_contract()), encoding="utf-8")

    shown = subprocess.run(
        [
            str(REPO_ROOT / "scripts/loopx"),
            "benchmark",
            "experiment-board-show",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--four-arm-contract-json",
            str(contract_path),
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(shown.stdout)
    assert payload["summary"]["factorial_contrast_countable_count"] == 1
    assert (
        payload["factorial_contrasts"][0]["interaction_contrast"]["metric_contrasts"][
            "feature_pass"
        ]["difference_in_differences"]
        == 3
    )


def test_cli_reconciles_provider_ledgers_without_recording_paths(
    tmp_path: Path,
) -> None:
    source_running = tmp_path / "provider-a.jsonl"
    source_terminal = tmp_path / "provider-b.jsonl"
    upsert_benchmark_experiment_board_row(
        source_running,
        _running_baseline(observed_at="2026-08-18T00:10:00+00:00"),
    )
    upsert_benchmark_experiment_board_row(
        source_terminal,
        _baseline(observed_at="2026-08-18T00:20:00+00:00"),
    )
    command = [
        str(REPO_ROOT / "scripts/loopx"),
        "benchmark",
        "experiment-board-reconcile",
        "--goal-id",
        "fixture-goal",
        "--project",
        str(tmp_path),
        "--source-ledger",
        str(source_running),
        "--source-ledger",
        str(source_terminal),
        "--format",
        "json",
    ]
    ledger = default_benchmark_experiment_board_path(
        project=tmp_path, goal_id="fixture-goal"
    )

    preview = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    preview_payload = json.loads(preview.stdout)
    assert preview_payload["summary"]["run_count"] == 1
    assert preview_payload["write"]["status"] == "preview_reconciled"
    assert preview_payload["write"]["inserted"] == 1
    assert not ledger.exists()

    executed = subprocess.run(
        [*command, "--execute"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    executed_payload = json.loads(executed.stdout)
    assert executed_payload["write"]["status"] == "reconciled"
    assert executed_payload["write"]["write_performed"] is True
    assert executed_payload["runs"][0]["status"] == "completed"
    assert str(tmp_path) not in executed.stdout
    assert read_benchmark_experiment_board_rows(ledger)[0]["status"] == "completed"


def test_cli_omitted_project_routes_board_to_registered_goal_repo(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, global_registry = _connected_goal_registries(tmp_path)
    unrelated = tmp_path / "linked-worktree"
    unrelated.mkdir()
    row_path = tmp_path / "row.json"
    row_path.write_text(json.dumps(_baseline()), encoding="utf-8")
    monkeypatch.chdir(unrelated)

    assert (
        main(
            [
                "--registry",
                str(global_registry),
                "benchmark",
                "experiment-board-upsert",
                "--goal-id",
                "fixture-goal",
                "--row-json",
                str(row_path),
                "--execute",
                "--format",
                "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    ledger = default_benchmark_experiment_board_path(
        project=project,
        goal_id="fixture-goal",
    )

    assert payload["summary"]["run_count"] == 1
    assert ledger.is_file()
    assert not (unrelated / ".loopx").exists()


def test_cli_explicit_project_cannot_override_registered_goal_repo(
    tmp_path: Path,
    capsys,
) -> None:
    project, global_registry = _connected_goal_registries(tmp_path)
    stale_project = tmp_path / "stale-linked-worktree"
    stale_project.mkdir()
    row_path = tmp_path / "row.json"
    row_path.write_text(json.dumps(_baseline()), encoding="utf-8")

    with pytest.raises(SystemExit):
        main(
            [
                "--registry",
                str(global_registry),
                "benchmark",
                "experiment-board-upsert",
                "--goal-id",
                "fixture-goal",
                "--project",
                str(stale_project),
                "--row-json",
                str(row_path),
                "--execute",
                "--format",
                "json",
            ]
        )

    assert "--project must match the canonical connected goal repository" in (
        capsys.readouterr().err
    )
    assert not default_benchmark_experiment_board_path(
        project=project,
        goal_id="fixture-goal",
    ).exists()
    assert not (stale_project / ".loopx").exists()


def test_capability_show_teaches_agents_to_use_the_board() -> None:
    packet = build_capability_detail_packet("benchmark-toolkit")
    capability = packet["capability"]
    usage = capability["agent_usage"]
    rendered = render_capability_detail_markdown(packet)

    assert usage["required_sequence"][0] == (
        "read_experiment_board_before_launch_or_case_selection"
    )
    assert "experiment-board-show" in usage["board_commands"]["read"]
    assert "experiment-board-upsert" in usage["board_commands"]["write"]
    assert "experiment-board-reconcile" in usage["board_commands"]["reconcile"]
    assert "## Agent Usage" in rendered
    assert "read_experiment_board_before_launch_or_case_selection" in rendered
