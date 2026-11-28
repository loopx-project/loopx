"""Factorial contrast read model for benchmark experiment-board rows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import math
from typing import NamedTuple, SupportsFloat

from .experiment_identity import experiment_token_text as _token
from .four_arm_contract import (
    BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION,
    BENCHMARK_FOUR_ARM_QUALIFICATION_SCOPE,
)
from ...control_plane.content_digest import BARE_SHA256_PATTERN

BENCHMARK_FACTORIAL_CONTRAST_SCHEMA_VERSION = "benchmark_factorial_contrast_v0"

Payload = dict[str, object]
PayloadView = Mapping[str, object]

_FACTOR_CELLS = frozenset({(False, False), (True, False), (False, True), (True, True)})
_REQUIRED_FACTORS = {"loopx": [False, True], "domain_hint": [False, True]}


class _ArmSpec(NamedTuple):
    arm_id: str
    arm_role: str
    loopx_enabled: bool
    domain_hint_enabled: bool
    task_goal_sha256: str
    comparison_anchor_arm_id: str | None


class _EffectSpec(NamedTuple):
    effect: str
    candidate_arm_id: str
    anchor_arm_id: str


class _NormalizedDesign(NamedTuple):
    arms_by_id: dict[str, _ArmSpec]
    effects: tuple[_EffectSpec, ...]
    candidate_effect_pair: tuple[str, str]
    anchor_effect_pair: tuple[str, str]


def _optional_token(value: object, *, field: str) -> str | None:
    return None if value in (None, "") else _token(value, field=field)


def _as_mapping(value: object, *, error: str) -> PayloadView:
    if not isinstance(value, (dict, Mapping)):
        raise TypeError(error)
    return value


def _as_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (SupportsFloat, str, bytes, bytearray)):
        return float(value)
    raise TypeError(f"float() argument must be a string or a real number, not {type(value).__name__!r}")


def _metrics_dict(row: PayloadView) -> PayloadView:
    return _as_mapping(row["metrics"], error="benchmark metrics must be a mapping")


def _metric_entry(metrics: PayloadView, metric_name: object) -> PayloadView:
    if not isinstance(metric_name, str):
        raise KeyError(metric_name)
    return _as_mapping(metrics[metric_name], error="benchmark metric entry must be a mapping")


def _metric_map(row: PayloadView, metric_name: object) -> PayloadView:
    return _metric_entry(_metrics_dict(row), metric_name)


def _metric_comparison_issue(baseline: PayloadView, candidate: PayloadView) -> str | None:
    """Check metric meaning, without imposing a study's fixed-total policy."""
    if baseline.get("unit") != candidate.get("unit"):
        return "unit_mismatch"
    if baseline.get("higher_is_better") != candidate.get("higher_is_better"):
        return "direction_mismatch"
    if ("total" in baseline) != ("total" in candidate):
        return "denominator_presence_mismatch"
    if "total" in baseline:
        for metric in (baseline, candidate):
            total = metric["total"]
            if not isinstance(total, (int, float)):
                raise TypeError(f"'<=' not supported between instances of {type(total).__name__!r} and 'int'")
            if total <= 0:
                return "non_positive_denominator"
    return None


def _direction_for(delta_value: float, higher_is_better: bool) -> str:
    if delta_value == 0:
        return "flat"
    return "improved" if (delta_value > 0) == higher_is_better else "regressed"


def build_benchmark_metric_delta(baseline: PayloadView, candidate: PayloadView) -> Payload:
    """Compare two normalized experiment-board metric values."""
    baseline_value, candidate_value = baseline["value"], candidate["value"]
    result: Payload = {"baseline_value": baseline_value, "candidate_value": candidate_value}
    issue = _metric_comparison_issue(baseline, candidate)
    if issue is not None:
        return {**result, "comparison_unavailable_reason": issue}

    baseline_num = float(baseline_value) if isinstance(baseline_value, (int, float)) else _as_float(baseline_value)
    candidate_num = float(candidate_value) if isinstance(candidate_value, (int, float)) else _as_float(candidate_value)
    delta = candidate_num - baseline_num
    if not math.isfinite(delta):
        return {**result, "comparison_unavailable_reason": "non_finite_difference"}
    result["delta"] = delta

    delta_rate: float | None = None
    baseline_total, candidate_total = baseline.get("total"), candidate.get("total")
    if (
        isinstance(baseline_total, (int, float)) and not isinstance(baseline_total, bool) and baseline_total > 0
        and isinstance(candidate_total, (int, float)) and not isinstance(candidate_total, bool) and candidate_total > 0
    ):
        baseline_rate = baseline_num / float(baseline_total)
        candidate_rate = candidate_num / float(candidate_total)
        delta_rate = candidate_rate - baseline_rate
        if not (math.isfinite(baseline_rate) and math.isfinite(candidate_rate) and math.isfinite(delta_rate)):
            return {
                "baseline_value": baseline_value,
                "candidate_value": candidate_value,
                "comparison_unavailable_reason": "non_finite_difference",
            }
        result.update(
            {
                "baseline_total": baseline_total, "candidate_total": candidate_total,
                "baseline_rate": baseline_rate, "candidate_rate": candidate_rate, "delta_rate": delta_rate,
            }
        )

    higher_is_better = candidate.get("higher_is_better")
    if isinstance(higher_is_better, bool) and baseline.get("higher_is_better") == higher_is_better:
        result["direction"] = _direction_for(delta if delta_rate is None else delta_rate, higher_is_better)
    return result


def benchmark_metric_comparison_value(metric: PayloadView) -> float | None:
    """Read the comparison scale while retaining the raw delta in the packet."""
    if metric.get("comparison_unavailable_reason"):
        return None
    value = metric.get("delta_rate", metric.get("delta"))
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _normalize_arms(raw_arms: object) -> tuple[dict[tuple[bool, bool], _ArmSpec], dict[str, _ArmSpec]]:
    if not isinstance(raw_arms, list) or len(raw_arms) != 4:
        raise ValueError("four-arm contract must declare exactly four arms")
    cells: dict[tuple[bool, bool], _ArmSpec] = {}
    arms_by_id: dict[str, _ArmSpec] = {}
    for entry in raw_arms:
        raw_arm = _as_mapping(entry, error="four-arm contract arms must be objects")
        arm_id = _token(raw_arm.get("arm_id"), field="four-arm arm_id")
        loopx_enabled, hint_enabled = raw_arm.get("loopx_enabled"), raw_arm.get("domain_hint_enabled")
        if not isinstance(loopx_enabled, bool) or not isinstance(hint_enabled, bool):
            raise TypeError("four-arm factors must be boolean")
        cell_key = (loopx_enabled, hint_enabled)
        if cell_key in cells or arm_id in arms_by_id:
            raise ValueError("four-arm contract cells and arm ids must be unique")
        expected_role = (
            "baseline" if cell_key == (False, False) else "control" if cell_key == (False, True) else "treatment"
        )
        arm_role = _token(raw_arm.get("arm_role"), field="four-arm arm_role")
        if arm_role != expected_role:
            raise ValueError("four-arm contract role does not match its factor cell")
        task_goal_sha256 = str(raw_arm.get("task_goal_sha256") or "").strip()
        if not BARE_SHA256_PATTERN.fullmatch(task_goal_sha256):
            raise ValueError("four-arm task-goal hash must be sha256")
        anchor_id = _optional_token(raw_arm.get("comparison_anchor_arm_id"), field="four-arm comparison_anchor_arm_id")
        arm = _ArmSpec(arm_id, arm_role, loopx_enabled, hint_enabled, task_goal_sha256, anchor_id)
        cells[cell_key] = arm
        arms_by_id[arm_id] = arm
    if set(cells) != _FACTOR_CELLS:
        raise ValueError("four-arm contract is missing a factor cell")
    if cells[(False, False)].comparison_anchor_arm_id is not None:
        raise ValueError("plain Goal cell cannot declare a comparison anchor")
    expected_anchors = {
        (True, False): cells[(False, False)].arm_id,
        (False, True): cells[(False, False)].arm_id,
        (True, True): cells[(False, True)].arm_id,
    }
    for cell_key, expected_anchor in expected_anchors.items():
        if cells[cell_key].comparison_anchor_arm_id != expected_anchor:
            raise ValueError("four-arm comparison anchor does not match factor design")
    if cells[(False, False)].task_goal_sha256 != cells[(True, False)].task_goal_sha256:
        raise ValueError("plain Goal and LoopX cells must have prompt parity")
    if cells[(False, True)].task_goal_sha256 != cells[(True, True)].task_goal_sha256:
        raise ValueError("hinted Goal and LoopX cells must have prompt parity")
    if cells[(False, False)].task_goal_sha256 == cells[(False, True)].task_goal_sha256:
        raise ValueError("plain and hinted task goals must be distinct")
    return cells, arms_by_id


def _normalize_primary_comparisons(
    raw_effects: object,
    expected_effects: Mapping[str, tuple[str, str]],
    arms_by_id: Mapping[str, _ArmSpec],
) -> tuple[tuple[_EffectSpec, ...], set[tuple[str, str]]]:
    if not isinstance(raw_effects, list) or len(raw_effects) != 3:
        raise ValueError("four-arm contract must declare three primary comparisons")
    effects: list[_EffectSpec] = []
    observed_effects: dict[str, tuple[str, str]] = {}
    observed_pairs: set[tuple[str, str]] = set()
    for entry in raw_effects:
        raw_effect = _as_mapping(entry, error="four-arm primary comparisons must be objects")
        effect = _token(raw_effect.get("effect"), field="four-arm effect")
        pair = (
            _token(raw_effect.get("candidate_arm_id"), field="four-arm candidate_arm_id"),
            _token(raw_effect.get("anchor_arm_id"), field="four-arm anchor_arm_id"),
        )
        if effect in observed_effects or pair in observed_pairs:
            raise ValueError("four-arm primary comparisons must be unique")
        if pair[0] not in arms_by_id or pair[1] not in arms_by_id:
            raise ValueError("four-arm primary comparison names an unknown arm")
        observed_effects[effect] = pair
        observed_pairs.add(pair)
        effects.append(_EffectSpec(effect, pair[0], pair[1]))
    if observed_effects != expected_effects:
        raise ValueError("four-arm primary comparisons do not match factor design")
    return tuple(effects), observed_pairs


def _normalize_interaction_pairs(
    raw_interaction_obj: object,
    expected_effects: Mapping[str, tuple[str, str]],
    observed_pairs: set[tuple[str, str]],
) -> tuple[tuple[str, str], tuple[str, str]]:
    raw_interaction = _as_mapping(raw_interaction_obj, error="four-arm interaction contrast must be an object")

    def read_pair(field: str) -> tuple[str, str]:
        raw_pair = _as_mapping(raw_interaction.get(field), error=f"four-arm interaction {field} must be an object")
        pair = (
            _token(raw_pair.get("candidate_arm_id"), field=f"four-arm interaction {field} candidate_arm_id"),
            _token(raw_pair.get("anchor_arm_id"), field=f"four-arm interaction {field} anchor_arm_id"),
        )
        if pair not in observed_pairs:
            raise ValueError("four-arm interaction names an unknown primary effect")
        return pair

    candidate_pair, anchor_pair = read_pair("candidate_effect"), read_pair("anchor_effect")
    if (
        candidate_pair != expected_effects["loopx_with_domain_hint"]
        or anchor_pair != expected_effects["loopx_without_domain_hint"]
    ):
        raise ValueError("four-arm interaction contrast does not match factor design")
    return candidate_pair, anchor_pair


def _normalize_four_arm_design(contract: PayloadView) -> _NormalizedDesign:
    if not isinstance(contract, Mapping):
        raise TypeError("four-arm contract must be an object")
    if contract.get("schema_version") != BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION:
        raise ValueError("four-arm contract schema mismatch")
    if contract.get("qualified") is not True:
        raise ValueError("four-arm contract must be design-qualified")
    if contract.get("qualification_scope") != BENCHMARK_FOUR_ARM_QUALIFICATION_SCOPE:
        raise ValueError("four-arm contract qualification scope mismatch")
    attestations = contract.get("attestations")
    if not isinstance(attestations, Mapping) or attestations.get("domain_hint_independent_of_loopx") is not True:
        raise ValueError("four-arm domain hint independence is not attested")
    if contract.get("factors") != _REQUIRED_FACTORS:
        raise ValueError("four-arm contract factors are unsupported")

    cells, arms_by_id = _normalize_arms(contract.get("arms"))
    expected_effects = {
        "loopx_without_domain_hint": (cells[(True, False)].arm_id, cells[(False, False)].arm_id),
        "domain_hint_without_loopx": (cells[(False, True)].arm_id, cells[(False, False)].arm_id),
        "loopx_with_domain_hint": (cells[(True, True)].arm_id, cells[(False, True)].arm_id),
    }
    effects, observed_pairs = _normalize_primary_comparisons(
        contract.get("primary_comparisons"), expected_effects, arms_by_id
    )
    candidate_pair, anchor_pair = _normalize_interaction_pairs(
        contract.get("interaction_contrast"), expected_effects, observed_pairs
    )
    return _NormalizedDesign(arms_by_id, effects, candidate_pair, anchor_pair)


def _interaction_metric(
    *,
    candidate_effect: PayloadView,
    anchor_effect: PayloadView,
    higher_is_better: bool | None,
) -> Payload:
    for effect in (candidate_effect, anchor_effect):
        if effect.get("comparison_unavailable_reason"):
            return {"comparison_unavailable_reason": effect["comparison_unavailable_reason"]}

    candidate_delta, anchor_delta = candidate_effect.get("delta"), anchor_effect.get("delta")
    result: Payload = {"candidate_effect_delta": candidate_delta, "anchor_effect_delta": anchor_delta}
    diff_abs: float | None = None
    if isinstance(candidate_delta, (int, float)) and isinstance(anchor_delta, (int, float)):
        diff_abs = candidate_delta - anchor_delta
        result["difference_in_differences"] = diff_abs

    candidate_rate, anchor_rate = candidate_effect.get("delta_rate"), anchor_effect.get("delta_rate")
    diff_rate: float | None = None
    if isinstance(candidate_rate, (int, float)) and isinstance(anchor_rate, (int, float)):
        diff_rate = candidate_rate - anchor_rate
        result["candidate_effect_delta_rate"] = candidate_rate
        result["anchor_effect_delta_rate"] = anchor_rate
        result["difference_in_differences_rate"] = diff_rate

    for value in result.values():
        if not math.isfinite(value if isinstance(value, (int, float)) else _as_float(value)):
            return {"comparison_unavailable_reason": "non_finite_difference"}

    difference = diff_abs if diff_rate is None else diff_rate
    if difference == 0:
        result["direction"] = "flat"
    elif difference is not None and isinstance(higher_is_better, bool):
        result["direction"] = _direction_for(difference, higher_is_better)
    return result


def build_benchmark_factorial_contrasts(
    rows: Iterable[PayloadView],
    contract: PayloadView,
) -> list[Payload]:
    """Project conditional effects from explicitly declared four-arm cells."""
    design = _normalize_four_arm_design(contract)
    arms_by_id = design.arms_by_id
    grouped: dict[tuple[str, str, str], dict[str, list[PayloadView]]] = {}
    for row in rows:
        arm_id = row.get("arm_id")
        if not isinstance(arm_id, str) or arm_id not in arms_by_id:
            continue
        key = (str(row.get("benchmark_id")), str(row.get("study_id")), str(row.get("case_id")))
        grouped.setdefault(key, {}).setdefault(arm_id, []).append(row)

    contrasts: list[Payload] = []
    for (benchmark_id, study_id, case_id), case_by_arm in sorted(grouped.items()):
        reasons: list[str] = []
        selected: dict[str, PayloadView] = {}
        cell_status: list[Payload] = []
        cell_reasons_by_arm: dict[str, list[str]] = {}
        for arm_id, arm_contract in arms_by_id.items():
            candidates = case_by_arm.get(arm_id, ())
            countable = [
                row
                for row in candidates
                if isinstance(countability := row.get("countability"), (dict, Mapping))
                and countability.get("score_countable") is True
            ]
            cell_reasons: list[str] = []
            if not countable:
                cell_reasons.append("no_score_countable_run")
                reasons.append("cell_has_no_score_countable_run")
            elif len(countable) > 1:
                cell_reasons.append("ambiguous_score_countable_runs")
                reasons.append("cell_has_ambiguous_score_countable_runs")
            else:
                selected[arm_id] = countable[0]
            cell_reasons_by_arm[arm_id] = cell_reasons
            cell_status.append(
                {
                    "arm_id": arm_id,
                    "arm_role": arm_contract.arm_role,
                    "loopx_enabled": arm_contract.loopx_enabled,
                    "domain_hint_enabled": arm_contract.domain_hint_enabled,
                    "run_count": len(candidates),
                    "score_countable_run_count": len(countable),
                    "selected_run_id": countable[0].get("run_id") if len(countable) == 1 else None,
                    "reason_codes": cell_reasons,
                }
            )

        if len(selected) == len(arms_by_id):
            _qualify_selected_cells(
                selected=selected, arms_by_id=arms_by_id, cell_reasons_by_arm=cell_reasons_by_arm, reasons=reasons
            )

        effects, effect_deltas_by_pair = _conditional_effects(selected=selected, design=design)
        interaction_metrics = _interaction_metrics(
            selected=selected, design=design, effect_deltas_by_pair=effect_deltas_by_pair
        )
        primary_metric = next(iter(selected.values())).get("primary_metric") if selected else None
        if (
            isinstance(primary_metric, str)
            and primary_metric in interaction_metrics
            and interaction_metrics[primary_metric].get("comparison_unavailable_reason")
        ):
            reasons.append("primary_metric_definition_mismatch")
        contrasts.append(
            {
                "schema_version": BENCHMARK_FACTORIAL_CONTRAST_SCHEMA_VERSION,
                "benchmark_id": benchmark_id,
                "study_id": study_id,
                "case_id": case_id,
                "primary_metric": primary_metric,
                "factorial_contrast_countable": not reasons,
                "qualification_scope": "declared_factor_design_and_board_results",
                "reason_codes": sorted(set(reasons)),
                "cells": cell_status,
                "conditional_effects": effects,
                "interaction_contrast": {
                    "candidate_effect_pair": list(design.candidate_effect_pair),
                    "anchor_effect_pair": list(design.anchor_effect_pair),
                    "metric_contrasts": interaction_metrics,
                },
            }
        )
    return contrasts


def _qualify_cell_links(
    *,
    selected: Mapping[str, PayloadView],
    arms_by_id: Mapping[str, _ArmSpec],
    cell_reasons_by_arm: Mapping[str, list[str]],
    reasons: list[str],
) -> None:
    def reject_cell(arm_id: str, reason: str) -> None:
        cell_reasons_by_arm[arm_id].append(reason)
        reasons.append(f"cell_{reason}")

    for arm_id, row in selected.items():
        arm_contract = arms_by_id[arm_id]
        if row.get("arm_role") != arm_contract.arm_role:
            reject_cell(arm_id, "role_mismatch")
        expected_fidelity = "not_applicable" if arm_contract.arm_role == "baseline" else "qualified"
        if row.get("treatment_fidelity") != expected_fidelity:
            reject_cell(arm_id, "treatment_fidelity_not_qualified")
        if row.get("claim_scope") != "matched_study":
            reject_cell(arm_id, "claim_scope_not_matched_study")
        anchor_arm_id = arm_contract.comparison_anchor_arm_id
        if anchor_arm_id is not None and row.get("comparison_anchor_run_id") != selected[anchor_arm_id].get("run_id"):
            reject_cell(arm_id, "comparison_anchor_run_mismatch")


def _qualify_selected_cells(
    *,
    selected: Mapping[str, PayloadView],
    arms_by_id: Mapping[str, _ArmSpec],
    cell_reasons_by_arm: Mapping[str, list[str]],
    reasons: list[str],
) -> None:
    _qualify_cell_links(
        selected=selected, arms_by_id=arms_by_id, cell_reasons_by_arm=cell_reasons_by_arm, reasons=reasons
    )
    selected_rows = tuple(selected.values())
    for field in ("model_id", "comparison_protocol_id", "primary_metric"):
        first_value = selected_rows[0].get(field)
        if any(row.get(field) != first_value for row in selected_rows[1:]):
            reasons.append(f"{field}_mismatch")
    runner_revisions = [row.get("runner_revision") for row in selected_rows]
    if any(value in (None, "") for value in runner_revisions):
        reasons.append("runner_revision_missing")
    elif any(value != runner_revisions[0] for value in runner_revisions[1:]):
        reasons.append("runner_revision_mismatch")

    loopx_runtimes = [selected[arm.arm_id].get("orchestrator_runtime") for arm in arms_by_id.values() if arm.loopx_enabled]
    if any(runtime is None for runtime in loopx_runtimes):
        reasons.append("loopx_orchestrator_runtime_missing")
    elif loopx_runtimes[0] != loopx_runtimes[1]:
        reasons.append("loopx_orchestrator_runtime_mismatch")
    goal_runtimes = [selected[arm.arm_id].get("orchestrator_runtime") for arm in arms_by_id.values() if not arm.loopx_enabled]
    if goal_runtimes[0] != goal_runtimes[1]:
        reasons.append("goal_orchestrator_runtime_mismatch")
    elif loopx_runtimes and all(runtime is not None for runtime in loopx_runtimes) and goal_runtimes[0] == loopx_runtimes[0]:
        reasons.append("factor_runtime_cohorts_not_distinct")

    metric_entries = [_metric_map(row, selected_rows[0]["primary_metric"]) for row in selected_rows]
    signatures = [{key: value for key, value in entry.items() if key != "value"} for entry in metric_entries]
    if (
        any(signature != signatures[0] for signature in signatures[1:])
        or build_benchmark_metric_delta(metric_entries[0], metric_entries[0]).get("comparison_unavailable_reason")
    ):
        reasons.append("primary_metric_definition_mismatch")


def _conditional_effects(
    *,
    selected: Mapping[str, PayloadView],
    design: _NormalizedDesign,
) -> tuple[list[Payload], dict[tuple[str, str], dict[str, Payload]]]:
    if len(selected) != len(design.arms_by_id):
        return [], {}
    effects: list[Payload] = []
    effect_deltas_by_pair: dict[tuple[str, str], dict[str, Payload]] = {}
    for spec in design.effects:
        candidate, anchor = selected[spec.candidate_arm_id], selected[spec.anchor_arm_id]
        anchor_metrics, candidate_metrics = _metrics_dict(anchor), _metrics_dict(candidate)
        metric_deltas: dict[str, Payload] = {
            name: build_benchmark_metric_delta(
                _metric_entry(anchor_metrics, name), _metric_entry(candidate_metrics, name)
            )
            for name in sorted(set(anchor_metrics) & set(candidate_metrics))
            if isinstance(name, str)
        }
        effects.append(
            {
                "effect": spec.effect,
                "candidate_arm_id": spec.candidate_arm_id,
                "anchor_arm_id": spec.anchor_arm_id,
                "candidate_run_id": candidate.get("run_id"),
                "anchor_run_id": anchor.get("run_id"),
                "metric_deltas": metric_deltas,
            }
        )
        effect_deltas_by_pair[(spec.candidate_arm_id, spec.anchor_arm_id)] = metric_deltas
    return effects, effect_deltas_by_pair


def _interaction_metrics(
    *,
    selected: Mapping[str, PayloadView],
    design: _NormalizedDesign,
    effect_deltas_by_pair: Mapping[tuple[str, str], Mapping[str, Payload]],
) -> dict[str, Payload]:
    if not effect_deltas_by_pair:
        return {}
    candidate_metrics = effect_deltas_by_pair[design.candidate_effect_pair]
    anchor_metrics = effect_deltas_by_pair[design.anchor_effect_pair]
    selected_metric_maps = tuple(_metrics_dict(row) for row in selected.values())
    candidate_metric_map = _metrics_dict(selected[design.candidate_effect_pair[0]])
    interaction_metrics: dict[str, Payload] = {}
    for name in sorted(set(candidate_metrics) & set(anchor_metrics)):
        metric_source = _metric_entry(candidate_metric_map, name)
        # Conditional effects can each be valid while using different units or
        # scales. Check all four cells before subtracting those effects.
        issue: str | None = None
        for metrics in selected_metric_maps:
            issue = _metric_comparison_issue(metric_source, _metric_entry(metrics, name))
            if issue is not None:
                break
        if issue is not None:
            interaction_metrics[name] = {"comparison_unavailable_reason": issue}
            continue
        higher_is_better = metric_source.get("higher_is_better")
        interaction_metrics[name] = _interaction_metric(
            candidate_effect=candidate_metrics[name],
            anchor_effect=anchor_metrics[name],
            higher_is_better=higher_is_better if isinstance(higher_is_better, bool) else None,
        )
    return interaction_metrics
